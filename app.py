"""
================================================================
🌤️ Taiwan Weather LINE Bot - 完整版（Render 雲端部署版）
================================================================
功能列表：
  主動詢問:
    1. 空品 (AQI)
    2. 雷達圖
    3. 雨量圖
    4. 氣象 (即時天氣 / 12hr / 3day / 7day)
    5. 戶外活動建議
    6. 紫外線指數
    7. 颱風資訊
    8. 推播設定

  自動推播:
    1. AQI 警示 (每 30 分鐘檢查)
    2. 地震速報 (每 5 分鐘檢查)
    3. 空品預報 (每天早上 7:00)

部署需求:
  pip install flask requests urllib3 line-bot-sdk tabulate apscheduler gunicorn

環境變數（雲端必設）:
  CHANNEL_ACCESS_TOKEN  -- LINE Channel access token
  CHANNEL_SECRET        -- LINE Channel secret
  CWA_API_KEY           -- 中央氣象署 API key
  MOENV_API_KEY         -- 環境部 API key
  SUBSCRIPTIONS_FILE    -- (可選) 訂閱資料路徑，雲端建議設成 /var/data/subscriptions.json
  PORT                  -- (可選) 本地測試用，雲端 Render 會自動設定

健康檢查端點（給 UptimeRobot 等服務 ping 用，避免 Render 免費方案 sleep）:
  GET /         -- 首頁，回傳簡單字串
  GET /health   -- 健康檢查，回傳 OK
================================================================
"""

from flask import Flask, request
import requests
import time
import json
import os
from datetime import datetime
import urllib3
from tabulate import tabulate
from apscheduler.schedulers.background import BackgroundScheduler
import atexit

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

from linebot.v3.webhook import WebhookHandler
from linebot.v3.webhooks import MessageEvent, TextMessageContent
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import (
    Configuration,
    ApiClient,
    MessagingApi,
    ReplyMessageRequest,
    PushMessageRequest,
    TextMessage,
    ImageMessage,
    QuickReply,
    QuickReplyItem,
    MessageAction,
)

app = Flask(__name__)

# ======================
# 金鑰設定（全部從環境變數讀取，雲端與本地都用同一份程式碼）
# ======================
CHANNEL_ACCESS_TOKEN = os.environ.get("CHANNEL_ACCESS_TOKEN", "")
CHANNEL_SECRET = os.environ.get("CHANNEL_SECRET", "")
CWA_API_KEY = os.environ.get("CWA_API_KEY", "")
MOENV_API_KEY = os.environ.get("MOENV_API_KEY", "")

# 啟動時檢查必要的環境變數，沒設好就早點報錯（log 看得到）
for _key in ("CHANNEL_ACCESS_TOKEN", "CHANNEL_SECRET", "CWA_API_KEY", "MOENV_API_KEY"):
    if not os.environ.get(_key):
        print(f"⚠️  警告：環境變數 {_key} 未設定，相關功能會失效")

# LINE 訊息長度上限（保留 200 字緩衝）
LINE_MSG_LIMIT = 4800

# 訂閱資料儲存路徑
# 本地預設 subscriptions.json
# 雲端 Render 若有掛 Persistent Disk，建議在環境變數設成 /var/data/subscriptions.json
SUBSCRIPTIONS_FILE = os.environ.get("SUBSCRIPTIONS_FILE", "subscriptions.json")

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# ======================
# 城市代碼
# ======================
CITY_MAP = {
    "宜蘭縣": "001", "桃園市": "005", "新竹縣": "009", "苗栗縣": "013",
    "彰化縣": "017", "南投縣": "021", "雲林縣": "025", "嘉義縣": "029",
    "屏東縣": "033", "臺東縣": "037", "花蓮縣": "041", "澎湖縣": "045",
    "基隆市": "049", "新竹市": "053", "嘉義市": "057", "臺北市": "061",
    "高雄市": "065", "新北市": "069", "臺中市": "073", "臺南市": "077",
    "連江縣": "081", "金門縣": "085"
}

# ======================
# 紫外線測站對照表 (StationID → 縣市, 測站名稱)
# 來源：CWA 氣象署觀測站清單
# ======================
UV_STATIONS = {
    # 北部
    "466880": ("新北市", "板橋"),
    "466900": ("新北市", "淡水"),
    "466910": ("臺北市", "鞍部"),
    "466920": ("臺北市", "臺北"),
    "466930": ("臺北市", "竹子湖"),
    "466940": ("基隆市", "基隆"),
    "467050": ("新北市", "新屋"),
    "467060": ("桃園市", "新屋"),
    "467080": ("宜蘭縣", "宜蘭"),
    "467110": ("金門縣", "金門"),
    "467270": ("新竹縣", "新竹"),
    "467300": ("澎湖縣", "東吉島"),
    "467350": ("澎湖縣", "澎湖"),
    "467410": ("臺南市", "臺南"),
    "467420": ("嘉義縣", "嘉義"),
    "467440": ("高雄市", "高雄"),
    "467480": ("嘉義縣", "嘉義"),
    "467490": ("臺中市", "臺中"),
    "467530": ("嘉義縣", "阿里山"),
    "467540": ("臺東縣", "大武"),
    "467550": ("南投縣", "玉山"),
    "467570": ("新竹縣", "竹北"),
    "467571": ("新竹縣", "新竹"),
    "467590": ("屏東縣", "恆春"),
    "467610": ("臺東縣", "成功"),
    "467620": ("臺東縣", "蘭嶼"),
    "467650": ("南投縣", "日月潭"),
    "467660": ("臺東縣", "臺東"),
    "467770": ("南投縣", "梧棲"),
    "467780": ("臺中市", "梧棲"),
    "467790": ("花蓮縣", "花蓮"),
    "467990": ("連江縣", "馬祖"),
    "C0A520": ("新北市", "屈尺"),
    "C0A530": ("新北市", "福山"),
    "C0A540": ("新北市", "信賢"),
    "C0A550": ("新北市", "雙溪"),
    "C0A560": ("新北市", "桶後"),
    "C0A570": ("新北市", "拉拉山"),
    "C0A580": ("臺北市", "天母"),
    "C0A590": ("臺北市", "社子"),
    "C0A640": ("基隆市", "五堵"),
    "C0A650": ("臺北市", "陽明山"),
    "C0A660": ("臺北市", "石牌"),
    "C0A860": ("新北市", "汐止"),
    "C0A870": ("新北市", "五分山"),
    "C0A880": ("基隆市", "和平島"),
    "C0A890": ("基隆市", "彭佳嶼"),
    "C0A930": ("新北市", "瑞芳"),
    "C0A940": ("新北市", "金山"),
    "C0A950": ("新北市", "三貂角"),
    "C0A960": ("新北市", "雙溪"),
    "C0A970": ("新北市", "石碇"),
    "C0A980": ("新北市", "平溪"),
    "C0A990": ("新北市", "深坑"),
    "C0AC40": ("新北市", "汐止"),
    "C0AC60": ("新北市", "四分尾山"),
    "C0AC70": ("新北市", "三峽"),
    "C0AC80": ("新北市", "新店"),
    "C0AH40": ("新北市", "貢寮"),
    "C0AH70": ("新北市", "鼻頭角"),
    "C0AI40": ("新北市", "林口"),
    "C0AI50": ("新北市", "三芝"),
    "C0AI60": ("新北市", "石門"),
}

def _get_station_info(station_id):
    """根據 StationID 取得 (縣市, 測站名稱)，找不到回傳 ('未知', station_id)"""
    return UV_STATIONS.get(station_id, ("未知", station_id))


def load_subscriptions():
    """載入訂閱資料"""
    if not os.path.exists(SUBSCRIPTIONS_FILE):
        return {
            "users": {},
            "last_earthquake_id": "",
            "last_aqi_alert_time": ""
        }
    try:
        with open(SUBSCRIPTIONS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"❌ 讀取訂閱檔失敗: {e}")
        return {"users": {}, "last_earthquake_id": "", "last_aqi_alert_time": ""}

def save_subscriptions(data):
    """儲存訂閱資料"""
    try:
        # 確保父目錄存在（雲端用 /var/data/... 時很重要）
        parent = os.path.dirname(SUBSCRIPTIONS_FILE)
        if parent and not os.path.exists(parent):
            os.makedirs(parent, exist_ok=True)
        with open(SUBSCRIPTIONS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"❌ 儲存訂閱檔失敗: {e}")

def get_user_sub(user_id):
    """取得單一使用者的訂閱設定"""
    subs = load_subscriptions()
    return subs["users"].get(user_id, {
        "aqi_alert": {"enabled": False, "threshold": 150},
        "earthquake": {"enabled": False, "min_magnitude": 4.0},
        "aqi_forecast": {"enabled": False, "city": "臺北市"}
    })

def update_user_sub(user_id, sub_type, key, value):
    """更新使用者的訂閱設定"""
    subs = load_subscriptions()
    if user_id not in subs["users"]:
        subs["users"][user_id] = {
            "aqi_alert": {"enabled": False, "threshold": 150},
            "earthquake": {"enabled": False, "min_magnitude": 4.0},
            "aqi_forecast": {"enabled": False, "city": "臺北市"}
        }
    if sub_type not in subs["users"][user_id]:
        subs["users"][user_id][sub_type] = {}
    subs["users"][user_id][sub_type][key] = value
    save_subscriptions(subs)

# ====================== 
# 工具函式
# ======================
def norm(t):
    return (t or "").strip().replace("台", "臺")

def wx_icon(wx):
    wx = str(wx)
    if "雷" in wx: return "⛈️"
    if "雨" in wx: return "🌧️"
    if "陰" in wx: return "☁️"
    if "多雲" in wx: return "🌤️"
    if "晴" in wx: return "☀️"
    return "🌡️"

def aqi_icon(aqi):
    try:
        aqi = int(aqi)
    except:
        return "⚪"
    if aqi <= 50: return "🟢"
    if aqi <= 100: return "🟡"
    if aqi <= 150: return "🟠"
    if aqi <= 200: return "🔴"
    return "🟣"

def aqi_warning(aqi):
    try:
        aqi = int(aqi)
    except:
        return "資料異常", "", ""
    
    if aqi <= 50:
        return "優", "空氣品質優", "✅ 適合戶外活動"
    elif aqi <= 100:
        return "良", "空氣品質良好", "⚠️ 敏感族群應避免長時間戶外活動"
    elif aqi <= 150:
        return "普通", "空氣品質普通", "⚠️ 敏感族群應減少戶外活動"
    elif aqi <= 200:
        return "不良", "空氣品質不良", "🚫 敏感族群應停止戶外活動"
    else:
        return "危害", "空氣品質危害", "🚫 所有人應停止戶外活動"

def uv_level(uv):
    """紫外線等級判斷"""
    try:
        uv = float(uv)
    except:
        return "資料異常", "", ""
    
    if uv <= 2:
        return "低", "🟢", "✅ 一般可不必防護"
    elif uv <= 5:
        return "中", "🟡", "⚠️ 戴帽子或撐傘"
    elif uv <= 7:
        return "高", "🟠", "⚠️ 擦防曬，避開正午陽光"
    elif uv <= 10:
        return "過量", "🔴", "🚫 避免長時間戶外活動"
    else:
        return "危險", "🟣", "🚫 應盡量待在室內"

def truncate_msg(msg):
    if len(msg) > LINE_MSG_LIMIT:
        return msg[:LINE_MSG_LIMIT - 50] + "\n\n...(訊息過長已截斷)"
    return msg

# ======================
# 時間與數值解析
# ======================
def parse_dt(s):
    if not s:
        return None
    s = s.replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(s[:16], fmt[:len(fmt)])
        except ValueError:
            continue
    return None

def get_smart_value(elements, target_names, target_time_str):
    """版本一：時間字串比對版本（給 12hr 和 7day 用）"""
    target_dt = parse_dt(target_time_str)
    if target_dt is None:
        return "--"

    for name in target_names:
        if name not in elements:
            continue
        for item in elements[name]:
            start_str = item.get("DataTime") or item.get("StartTime")
            end_str = item.get("EndTime") or start_str
            start_dt = parse_dt(start_str)
            end_dt = parse_dt(end_str)
            if start_dt is None:
                continue
            if end_dt is None:
                end_dt = start_dt

            if start_dt <= target_dt <= end_dt:
                vals = item.get("ElementValue", [])
                if not vals:
                    continue
                v_dict = vals[0]
                for key in ("value", "Weather", "ProbabilityOfPrecipitation"):
                    val = v_dict.get(key)
                    if val is not None and str(val).strip() not in ("", "NA", "-"):
                        return str(val)
                for k, v in v_dict.items():
                    if k.lower() != "measures" and str(v).strip() not in ("", "NA", "-"):
                        return str(v)
    return "--"

def get_smart_value_idx(elements, element_names, time_idx, base_len, current_time):
    """版本二：索引對齊版本（給 3day 用）"""
    for name in element_names:
        if name not in elements:
            continue
        target_list = elements[name]
        if not target_list:
            continue
        try:
            if len(target_list) == base_len:
                val_obj = target_list[time_idx]["ElementValue"][0]
                val = val_obj.get("value") or val_obj.get("Weather") or val_obj.get("ProbabilityOfPrecipitation")
                if val is None:
                    val = list(val_obj.values())[0] if val_obj else None
                if val is not None and str(val).strip() not in ("", "NA", "-", "None"):
                    return str(val)
            for item in target_list:
                start = item.get("StartTime") or item.get("DataTime")
                end = item.get("EndTime") or start
                if start and start <= current_time <= end:
                    val_obj = item["ElementValue"][0]
                    val = val_obj.get("value") or val_obj.get("Weather") or val_obj.get("ProbabilityOfPrecipitation")
                    if val is None:
                        for k, v in val_obj.items():
                            if k.lower() != "measures" and str(v).strip() not in ("", "NA", "-"):
                                val = v
                                break
                    if val is not None and str(val).strip() not in ("", "NA", "-", "None"):
                        return str(val)
            if len(target_list) > 0:
                ratio = base_len // len(target_list) if len(target_list) > 0 else 1
                idx = time_idx // ratio if ratio > 0 else 0
                val_obj = target_list[min(idx, len(target_list)-1)]["ElementValue"][0]
                val = val_obj.get("value") or val_obj.get("Weather") or val_obj.get("ProbabilityOfPrecipitation")
                if val is None:
                    val = list(val_obj.values())[0] if val_obj else None
                if val is not None and str(val).strip() not in ("", "NA", "-", "None"):
                    return str(val)
        except:
            continue
    return "N/A"

# ======================
# 天氣預報查詢函式
# ======================
def fetch_12h(city, district):
    try:
        code = CITY_MAP.get(city)
        url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-{code}"
        params = {"Authorization": CWA_API_KEY, "format": "JSON", "locationName": district}
        data = requests.get(url, params=params, timeout=20, verify=False).json()
        target = data["records"]["Locations"][0]["Location"][0]
        elements = {e["ElementName"]: e["Time"] for e in target["WeatherElement"]}
        time_base = elements.get("溫度") or elements.get("T")
        msg = f"📍 {city}{district}｜⏱️ 12 小時逐時預報\n\n"
        for i in range(min(12, len(time_base))):
            ct = time_base[i].get("DataTime") or time_base[i].get("StartTime")
            td = ct[5:16].replace("T", " ")
            wx = get_smart_value(elements, ["天氣現象", "Wx"], ct)
            temp = get_smart_value(elements, ["溫度", "T"], ct)
            at = get_smart_value(elements, ["體感溫度", "AT"], ct)
            pop = get_smart_value(elements, ["3小時降雨機率", "PoP3h", "6小時降雨機率", "PoP6h", "12小時降雨機率", "PoP12h"], ct)
            rh = get_smart_value(elements, ["相對濕度", "RH"], ct)
            temp_str = f"{temp}°C" if temp != "--" else "--"
            at_str = f"{at}°C" if at != "--" else "--"
            pop_str = f"{pop}%" if pop != "--" else "0%"
            rh_str = f"{rh}%" if rh != "--" else "--"
            msg += f"{td} {wx_icon(wx)} {wx}\n"
            msg += f"  🌡{temp_str} 體{at_str} 🌧{pop_str} 濕{rh_str}\n"
        return truncate_msg(msg)
    except Exception as e:
        return f"❌ 12hr 預報錯誤：{str(e)[:150]}"

def fetch_3day(city, district):
    try:
        code = CITY_MAP.get(city)
        url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-{code}"
        params = {"Authorization": CWA_API_KEY, "format": "JSON", "locationName": district}
        data = requests.get(url, params=params, timeout=20, verify=False).json()
        target = data["records"]["Locations"][0]["Location"][0]
        elements = {e["ElementName"]: e["Time"] for e in target["WeatherElement"]}
        time_base = elements.get("溫度") or elements.get("T")
        if not time_base:
            return f"❌ 3day 錯誤：無溫度資料"
        base_len = len(time_base)
        table_rows = []
        for i in range(base_len):
            current_time = time_base[i]["DataTime"]
            time_display = current_time[5:16].replace("T", " ")
            wx = get_smart_value_idx(elements, ["天氣現象", "Wx", "天氣預報綜合描述"], i, base_len, current_time)
            temp = get_smart_value_idx(elements, ["溫度", "T"], i, base_len, current_time)
            at = get_smart_value_idx(elements, ["體感溫度", "AT"], i, base_len, current_time)
            pop = get_smart_value_idx(elements, ["3小時降雨機率", "PoP3h", "6小時降雨機率", "PoP6h", "12小時降雨機率", "PoP12h"], i, base_len, current_time)
            table_rows.append([
                time_display,
                f"{wx_icon(wx)} {str(wx)[:8]}",
                f"{temp}°C" if temp != "N/A" else "--",
                f"{at}°C" if at != "N/A" else "--",
                f"{pop}%" if pop != "N/A" else "0%"
            ])
        headers = ["時間", "天氣", "氣溫", "體感", "降雨"]
        table_str = tabulate(table_rows, headers=headers, tablefmt="grid")
        msg = f"📍 {city}{district}｜📅 3 日短期預報\n\n{table_str}"
        if len(msg) > LINE_MSG_LIMIT:
            msg = f"📍 {city}{district}｜📅 3 日短期預報\n\n"
            for row in table_rows:
                msg += f"{row[0]} {row[1]}\n  🌡{row[2]} 體{row[3]} 🌧{row[4]}\n"
        return truncate_msg(msg)
    except Exception as e:
        return f"❌ 3day 預報錯誤：{str(e)[:150]}"

def fetch_7day(city, district):
    try:
        for api_id in ["F-D0047-091", "F-D0047-093"]:
            url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/{api_id}"
            params = {"Authorization": CWA_API_KEY, "format": "JSON"}
            data = requests.get(url, params=params, timeout=20, verify=False).json()
            if data.get("success") != "true":
                continue
            target = None
            for group in data["records"]["Locations"]:
                for loc in group.get("Location", []):
                    name = norm(loc.get("LocationName", ""))
                    if name == norm(district) or name == norm(city):
                        target = loc
                        break
                if target:
                    break
            if not target:
                continue
            elements = {e["ElementName"]: e["Time"] for e in target["WeatherElement"]}
            time_base = (elements.get("最高溫度") or elements.get("溫度") or elements.get("T") or elements.get("平均溫度"))
            if not time_base:
                continue
            msg = f"📍 {city}{district}｜🗓️ 一週天氣預報\n\n"
            for i in range(min(14, len(time_base))):
                ct = time_base[i].get("DataTime") or time_base[i].get("StartTime")
                td = ct[5:16].replace("T", " ")
                hour = td[6:8]
                period = "白天" if hour in ("06", "07", "08") else "晚上"
                wx = get_smart_value(elements, ["天氣現象", "Wx", "天氣預報綜合描述"], ct)
                temp = get_smart_value(elements, ["最高溫度", "溫度", "T", "平均溫度"], ct)
                pop = get_smart_value(elements, ["3小時降雨機率", "PoP3h", "12小時降雨機率", "PoP12h", "6小時降雨機率", "PoP6h"], ct)
                rh = get_smart_value(elements, ["相對濕度", "RH", "平均相對濕度"], ct)
                temp_str = f"{temp}°C" if temp != "--" else "--"
                pop_str = f"{pop}%" if pop != "--" else "0%"
                rh_str = f"{rh}%" if rh != "--" else "--"
                msg += f"{td[:5]} {period} {wx_icon(wx)} {wx}\n"
                msg += f"  🌡{temp_str} 🌧{pop_str} 濕{rh_str}\n"
            return truncate_msg(msg)
        return f"❌ 找不到 {city}{district}"
    except Exception as e:
        return f"❌ 7day 預報錯誤：{str(e)[:150]}"

def get_weather_now(city, district):
    try:
        code = CITY_MAP.get(city)
        url1 = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-{code}"
        params1 = {"Authorization": CWA_API_KEY, "format": "JSON"}
        data1 = requests.get(url1, params=params1, timeout=20, verify=False).json()
        if "Locations" not in data1.get("records", {}):
            return f"❌ 找不到 {city} 的資料"
        target = None
        dist_norm = norm(district)
        locations = data1["records"]["Locations"][0].get("Location", [])
        for loc in locations:
            if norm(loc.get("LocationName", "")) == dist_norm:
                target = loc
                break
        if not target:
            return f"❌ 找不到 {city}{district}"
        elements = {e["ElementName"]: e["Time"] for e in target["WeatherElement"]}
        time_base = elements.get("溫度") or elements.get("T")
        if not time_base:
            return f"❌ {city}{district} 無溫度資料"
        ct = time_base[0].get("DataTime") or time_base[0].get("StartTime")
        wx = get_smart_value(elements, ["天氣現象", "Wx"], ct)
        temp = get_smart_value(elements, ["溫度", "T"], ct)
        rh = get_smart_value(elements, ["相對濕度", "RH"], ct)
        url2 = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-C0032-001"
        params2 = {"Authorization": CWA_API_KEY, "format": "JSON"}
        data2 = requests.get(url2, params=params2, timeout=20, verify=False).json()
        pop = "--"
        if "location" in data2.get("records", {}):
            for loc in data2["records"]["location"]:
                if city in loc.get("locationName", ""):
                    w = loc.get("weatherElement", [])
                    if len(w) > 1:
                        pop = w[1]["time"][0]["parameter"]["parameterName"]
                    break
        return (
            f"📍 {city}{district}\n"
            f"{wx_icon(wx)} {wx}\n"
            f"🌡 溫度：{temp}°C\n"
            f"🌧 降雨機率：{pop}%\n"
            f"💨 濕度：{rh}%"
        )
    except Exception as e:
        return f"❌ 天氣資料錯誤：{str(e)[:150]}"

# ======================
# AQI 查詢
# ======================
def get_all_aqi_stations():
    """取得所有測站的 AQI 資料"""
    try:
        r = requests.get(
            "https://data.moenv.gov.tw/api/v2/AQX_P_432",
            params={"format": "json", "limit": 1000, "api_key": MOENV_API_KEY},
            timeout=15, verify=False
        )
        r.raise_for_status()
        data = r.json()
        if "records" in data:
            return data.get("records", [])
        elif "data" in data:
            return data.get("data", [])
        return data if isinstance(data, list) else []
    except Exception as e:
        return []

def get_aqi_by_city(city):
    records = get_all_aqi_stations()
    if not records:
        return []
    city = city.replace("台", "臺")
    return [rec for rec in records if isinstance(rec, dict) and city in rec.get("county", "")]

def get_aqi_detail(station):
    try:
        aqi = int(station.get("aqi", 0) or 0)
        pm = station.get("pm2.5", "-")
        level, status, advice = aqi_warning(aqi)
        return (
            f"📍 {station.get('county', '')} {station.get('sitename', '')}\n"
            f"{aqi_icon(aqi)} AQI {aqi} ｜ PM2.5 {pm}\n"
            f"【{level}】{status}\n"
            f"{advice}"
        )
    except Exception as e:
        return "❌ 測站資料錯誤"

# ======================
# 圖片
# ======================
def get_radar():
    return f"https://www.cwa.gov.tw/Data/radar/CV1_TW_3600.png?t={int(time.time())}"

def get_rainfall():
    return f"https://www.cwa.gov.tw/Data/rainfall/QZJ_forPreview.jpg?t={int(time.time())}"

# ============================================================
# 戶外活動建議
# ============================================================
def get_outdoor_advice(city, district):
    """整合 AQI + 天氣 + 紫外線（今日最大+預報），給戶外活動綜合評分"""
    try:
        # 1. 取得天氣
        weather = get_weather_now(city, district)
        
        # 2. 取得該縣市最差的 AQI 測站作為代表
        stations = get_aqi_by_city(city)
        max_aqi = 0
        if stations:
            for s in stations:
                try:
                    aqi = int(s.get("aqi", 0) or 0)
                    if aqi > max_aqi:
                        max_aqi = aqi
                except:
                    pass
        
        # 3. 取得紫外線指數
        uv_max_today = get_uv_for_city(city)
        uv_estimate, uv_period = get_uv_estimate_now(uv_max_today)
        
        try:
            uv_for_score = float(uv_estimate) if uv_estimate != "N/A" else None
        except:
            uv_for_score = None
        
        # 4. 綜合評分
        score = 100
        warnings = []
        
        if max_aqi > 200:
            score -= 50
            warnings.append(f"🔴 空氣品質危害 (AQI {max_aqi})")
        elif max_aqi > 150:
            score -= 35
            warnings.append(f"🟠 空氣品質不良 (AQI {max_aqi})")
        elif max_aqi > 100:
            score -= 20
            warnings.append(f"🟡 空氣品質普通 (AQI {max_aqi})")
        elif max_aqi > 50:
            score -= 5
        
        if uv_for_score is not None:
            if uv_for_score >= 11:
                score -= 30
                warnings.append(f"🟣 紫外線危險 (UV {uv_for_score})")
            elif uv_for_score >= 8:
                score -= 20
                warnings.append(f"🔴 紫外線過量 (UV {uv_for_score})")
            elif uv_for_score >= 6:
                score -= 10
                warnings.append(f"🟠 紫外線高 (UV {uv_for_score})")
            elif uv_for_score >= 3:
                score -= 3
                warnings.append(f"🟡 紫外線中等 (UV {uv_for_score})")
        
        if "🌧" in weather and "雨" in weather:
            score -= 20
            warnings.append("🌧 有降雨")
        
        if score >= 90:
            grade = "A"
            emoji = "🌟"
            advice = "非常適合戶外活動！"
        elif score >= 75:
            grade = "B"
            emoji = "✅"
            advice = "適合戶外活動，注意防護"
        elif score >= 60:
            grade = "C"
            emoji = "⚠️"
            advice = "可以出門，但敏感族群要小心"
        elif score >= 40:
            grade = "D"
            emoji = "🚧"
            advice = "建議減少戶外活動時間"
        else:
            grade = "F"
            emoji = "🚫"
            advice = "建議待在室內"
        
        msg = f"🏃 戶外活動建議｜{city}{district}\n\n"
        msg += f"{emoji} 評等：{grade} ({score}/100)\n"
        msg += f"💡 {advice}\n\n"
        
        if warnings:
            msg += "⚠️ 注意事項：\n"
            for w in warnings:
                msg += f"  {w}\n"
            msg += "\n"
        
        msg += "📊 詳細資訊：\n"
        msg += f"  🌫️ AQI: {max_aqi}\n"
        msg += f"  ☀️ UV 今日最大: {uv_max_today}\n"
        msg += f"  🕐 UV 目前估算: {uv_estimate}（{uv_period}）\n"
        
        return msg
    except Exception as e:
        return f"❌ 戶外活動建議錯誤：{str(e)[:150]}"

# ============================================================
# 紫外線指數
# ============================================================
def _parse_uv_locations(data):
    """從 UV API 回傳資料中提取 location 列表，相容多種格式"""
    records = data.get("records", {})
    
    if "location" in records and isinstance(records["location"], list):
        return records["location"]
    
    we = records.get("weatherElement")
    if isinstance(we, dict) and "location" in we:
        return we["location"]
    if isinstance(we, list):
        for elem in we:
            if isinstance(elem, dict) and "location" in elem:
                return elem["location"]
    
    if "Locations" in records:
        locs = records["Locations"]
        if isinstance(locs, list) and len(locs) > 0:
            inner = locs[0]
            if "Location" in inner:
                return inner["Location"]
            return locs
    
    return []

def _get_uv_value_from_loc(loc):
    """從單一 location 物件中取出 UV 值，相容多種格式"""
    for key in ("UVIndex", "uvindex", "UVI", "uvi", "H_UVI", "h_uvi", "value", "Value"):
        val = loc.get(key)
        if val is not None and str(val).strip() not in ("", "NA", "-", "None"):
            return str(val)
    
    we = loc.get("weatherElement", []) or loc.get("WeatherElement", [])
    if isinstance(we, list):
        for elem in we:
            name = elem.get("elementName") or elem.get("ElementName", "")
            if name in ("UVIndex", "UVI", "H_UVI", "uvindex"):
                val = elem.get("elementValue") or elem.get("ElementValue")
                if val is not None and str(val).strip() not in ("", "NA", "-", "None"):
                    return str(val)
    
    return None

def _get_loc_county(loc):
    """從 location 物件中取出縣市名稱"""
    for key in ("county", "County", "CountyName"):
        val = loc.get(key)
        if val:
            return str(val)
    
    params = loc.get("parameter", []) or loc.get("Parameter", [])
    if isinstance(params, list):
        for p in params:
            pname = p.get("parameterName") or p.get("ParameterName", "")
            if pname in ("CITY", "COUNTY", "CountyName", "City", "County"):
                val = p.get("parameterValue") or p.get("ParameterValue")
                if val:
                    return str(val)
    
    return ""

def _get_loc_name(loc):
    """從 location 物件中取出測站名稱"""
    for key in ("locationName", "LocationName", "StationName", "stationName"):
        val = loc.get(key)
        if val:
            return str(val)
    return "?"

def get_uv_for_city(city):
    """取得該縣市的紫外線指數（今日最大值，從 O-A0005-001）"""
    try:
        url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0005-001"
        params = {"Authorization": CWA_API_KEY, "format": "JSON"}
        data = requests.get(url, params=params, timeout=20, verify=False).json()
        
        locations = _parse_uv_locations(data)
        if not locations:
            return "N/A"
        
        city_norm = norm(city)
        max_uv = None
        
        for loc in locations:
            station_id = loc.get("StationID", "")
            station_county, _ = _get_station_info(station_id)
            
            if norm(station_county) == city_norm:
                uv_val = loc.get("UVIndex")
                if uv_val is not None:
                    try:
                        uv_num = float(uv_val)
                        if max_uv is None or uv_num > max_uv:
                            max_uv = uv_num
                    except:
                        pass
        
        return str(max_uv) if max_uv is not None else "N/A"
    except Exception as e:
        return "N/A"

def get_uv_estimate_now(uv_max_today):
    """根據當前時間和今日最大 UV 值，估算目前的 UV 強度"""
    try:
        if uv_max_today == "N/A":
            return "N/A", "無資料"
        
        max_uv = float(uv_max_today)
        now = datetime.now()
        hour = now.hour
        
        if hour < 6 or hour >= 19:
            return "0", "夜間"
        elif hour < 8:
            ratio = 0.15
            period = "清晨"
        elif hour < 10:
            ratio = 0.55
            period = "上午"
        elif hour < 14:
            ratio = 0.95
            period = "中午峰值"
        elif hour < 16:
            ratio = 0.6
            period = "下午"
        elif hour < 18:
            ratio = 0.2
            period = "傍晚"
        else:
            ratio = 0.05
            period = "黃昏"
        
        estimate = round(max_uv * ratio, 1)
        return str(estimate), period
    except:
        return "N/A", "無資料"

def get_uv_info(city):
    """取得該縣市的紫外線詳細資訊（含今日最大值）"""
    try:
        url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/O-A0005-001"
        params = {"Authorization": CWA_API_KEY, "format": "JSON"}
        data = requests.get(url, params=params, timeout=20, verify=False).json()
        
        locations = _parse_uv_locations(data)
        if not locations:
            return f"❌ 無紫外線資料"
        
        city_norm = norm(city)
        results = []
        
        for loc in locations:
            station_id = loc.get("StationID", "")
            station_county, station_name = _get_station_info(station_id)
            uv_val = loc.get("UVIndex")
            
            if uv_val is None:
                continue
            
            if norm(station_county) == city_norm:
                results.append((station_name, uv_val, station_id))
        
        if not results:
            return (
                f"❌ {city} 無紫外線測站資料\n"
                f"（可能是夜間時段，紫外線資料只在日間更新；\n"
                f"或該縣市沒有 UV 觀測站）"
            )
        
        msg = f"☀️ {city} 紫外線指數\n"
        msg += f"📌 顯示為「今日最大值」\n\n"
        max_uv = 0
        for name, uv, sid in results:
            try:
                uv_num = float(uv)
                level, icon, _ = uv_level(uv_num)
                msg += f"📍 {name} ({sid})\n   {icon} UV {uv}（{level}）\n"
                if uv_num > max_uv:
                    max_uv = uv_num
            except:
                msg += f"📍 {name}\n   ⚪ UV {uv}\n"
        
        if max_uv > 0:
            _, _, advice = uv_level(max_uv)
            msg += f"\n💡 整體建議：{advice}"
        
        estimate, period = get_uv_estimate_now(str(max_uv))
        if estimate != "N/A":
            msg += f"\n🕐 目前估算：UV {estimate}（{period}）"
        
        return truncate_msg(msg)
    except Exception as e:
        return f"❌ 紫外線資料錯誤：{str(e)[:150]}"

# ============================================================
# 颱風資訊
# ============================================================
def get_typhoon_info():
    """取得目前颱風資訊"""
    try:
        url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/W-C0034-005"
        params = {"Authorization": CWA_API_KEY, "format": "JSON"}
        data = requests.get(url, params=params, timeout=20, verify=False).json()
        
        if data.get("success") != "true":
            return "✅ 目前無颱風警報"
        
        records = data.get("records", {})
        typhoons = records.get("tropicalCyclones", {}).get("tropicalCyclone", [])
        
        if not typhoons:
            return "✅ 目前無颱風警報\n\n臺灣周圍海域目前平靜，無颱風生成或接近。"
        
        msg = "🌀 颱風資訊\n\n"
        
        for ty in typhoons[:3]:
            ty_info = ty.get("typhoonName", "未命名")
            ty_cwa_name = ty.get("cwaTyphoonName", "")
            year = ty.get("year", "")
            
            msg += f"🌪️ {ty_cwa_name or ty_info}\n"
            if year:
                msg += f"   年份：{year}\n"
            
            analysis = ty.get("analysisData", {}).get("fix", [])
            if analysis:
                latest = analysis[-1]
                fix_time = latest.get("fixTime", "")[:16].replace("T", " ")
                coord = latest.get("coordinate", "")
                pressure = latest.get("pressure", "")
                wind_speed = latest.get("maxWindSpeed", "")
                moving_speed = latest.get("movingSpeed", "")
                moving_dir = latest.get("movingDirection", "")
                
                msg += f"   ⏰ 觀測時間：{fix_time}\n"
                if coord:
                    msg += f"   📍 位置：{coord}\n"
                if pressure:
                    msg += f"   🔻 中心氣壓：{pressure} hPa\n"
                if wind_speed:
                    msg += f"   💨 最大風速：{wind_speed} m/s\n"
                if moving_dir and moving_speed:
                    msg += f"   ➡️ 移動：向{moving_dir} {moving_speed} km/h\n"
            
            msg += "\n"
        
        return truncate_msg(msg)
    except Exception as e:
        return f"❌ 颱風資訊錯誤：{str(e)[:150]}"

# ============================================================
# 地震資訊
# ============================================================
def get_latest_earthquake():
    """取得最新地震資訊（顯著有感地震）"""
    try:
        url = "https://opendata.cwa.gov.tw/api/v1/rest/datastore/E-A0015-001"
        params = {"Authorization": CWA_API_KEY, "format": "JSON", "limit": 5}
        data = requests.get(url, params=params, timeout=20, verify=False).json()
        
        if data.get("success") != "true":
            return None, "❌ 地震 API 失敗"
        
        eqs = data.get("records", {}).get("Earthquake", [])
        if not eqs:
            return None, "✅ 近期無顯著有感地震"
        
        latest = eqs[0]
        eq_no = latest.get("EarthquakeNo", "")
        info = latest.get("EarthquakeInfo", {})
        
        origin_time = info.get("OriginTime", "")
        epicenter = info.get("Epicenter", {})
        location = epicenter.get("Location", "")
        depth = info.get("Depth", {}).get("Value", "")
        magnitude = info.get("EarthquakeMagnitude", {}).get("MagnitudeValue", "")
        
        msg = "🌏 最新地震資訊\n\n"
        msg += f"📅 時間：{origin_time}\n"
        msg += f"📍 震央：{location}\n"
        msg += f"📏 深度：{depth} 公里\n"
        msg += f"⚡ 規模：M {magnitude}\n"
        
        intensity = latest.get("Intensity", {})
        max_int = intensity.get("MaxIntensity") or info.get("MaxIntensity", "")
        if max_int:
            msg += f"📊 最大震度：{max_int}\n"
        
        report_url = latest.get("Web", "")
        if report_url:
            msg += f"\n🔗 詳細報告：{report_url}"
        
        return eq_no, msg
    except Exception as e:
        return None, f"❌ 地震資料錯誤：{str(e)[:150]}"

# ============================================================
# 空品預報
# ============================================================
def get_aqi_forecast(city):
    """取得空品預報"""
    try:
        url = "https://data.moenv.gov.tw/api/v2/aqf_p_01"
        params = {"format": "json", "limit": 100, "api_key": MOENV_API_KEY}
        r = requests.get(url, params=params, timeout=15, verify=False)
        data = r.json()
        
        records = data.get("records", []) or data.get("data", [])
        if not records:
            return f"❌ 無空品預報資料"
        
        city_norm = norm(city).replace("縣", "").replace("市", "")
        msg = f"🔮 {city} 空品預報\n\n"
        
        found = False
        for rec in records[:20]:
            area = rec.get("area", "") or rec.get("Area", "")
            if city_norm in area or area in city_norm:
                forecast_date = rec.get("forecastdate", "") or rec.get("ForecastDate", "")
                aqi_str = rec.get("aqi", "") or rec.get("AQI", "")
                major_pollutant = rec.get("majorpollutant", "") or rec.get("MajorPollutant", "")
                
                try:
                    aqi_num = int(aqi_str)
                    level, status, advice = aqi_warning(aqi_num)
                    msg += f"📅 {forecast_date}\n"
                    msg += f"   {aqi_icon(aqi_num)} AQI {aqi_num}（{level}）\n"
                    if major_pollutant:
                        msg += f"   主要污染物：{major_pollutant}\n"
                    msg += f"   {advice}\n\n"
                    found = True
                except:
                    pass
        
        if not found:
            return f"❌ {city} 暫無空品預報資料"
        
        return truncate_msg(msg)
    except Exception as e:
        return f"❌ 空品預報錯誤：{str(e)[:150]}"

# ============================================================
# 🔔 自動推播：核心函式
# ============================================================
def push_message(user_id, msg):
    """推送文字訊息給特定使用者"""
    try:
        with ApiClient(configuration) as api:
            MessagingApi(api).push_message(
                PushMessageRequest(
                    to=user_id,
                    messages=[TextMessage(text=truncate_msg(msg))]
                )
            )
        return True
    except Exception as e:
        print(f"❌ 推送失敗 {user_id}: {e}")
        return False

# ============================================================
# 🔔 自動推播：AQI 警示檢查（每 30 分鐘）
# ============================================================
def check_aqi_alert():
    """檢查全台 AQI，超過用戶設定門檻則推送"""
    print(f"⏰ [{datetime.now().strftime('%H:%M:%S')}] 檢查 AQI 警示...")
    try:
        records = get_all_aqi_stations()
        if not records:
            return
        
        high_aqi_stations = []
        for rec in records:
            try:
                aqi = int(rec.get("aqi", 0) or 0)
                if aqi > 100:
                    high_aqi_stations.append({
                        "county": rec.get("county", ""),
                        "sitename": rec.get("sitename", ""),
                        "aqi": aqi,
                        "pm25": rec.get("pm2.5", "-")
                    })
            except:
                pass
        
        high_aqi_stations.sort(key=lambda x: x["aqi"], reverse=True)
        
        subs = load_subscriptions()
        for user_id, user_sub in subs.get("users", {}).items():
            aqi_alert = user_sub.get("aqi_alert", {})
            if not aqi_alert.get("enabled"):
                continue
            threshold = aqi_alert.get("threshold", 150)
            
            triggered = [s for s in high_aqi_stations if s["aqi"] >= threshold]
            if not triggered:
                continue
            
            msg = f"🚨 AQI 警示通知\n（門檻：{threshold}）\n\n"
            for s in triggered[:10]:
                level, _, _ = aqi_warning(s["aqi"])
                msg += f"{aqi_icon(s['aqi'])} {s['county']} {s['sitename']}\n"
                msg += f"   AQI {s['aqi']}（{level}）｜PM2.5 {s['pm25']}\n"
            
            push_message(user_id, msg)
            print(f"  ✅ 推送 AQI 警示給 {user_id} (觸發 {len(triggered)} 站)")
    except Exception as e:
        print(f"❌ AQI 警示檢查錯誤: {e}")

# ============================================================
# 🔔 自動推播：地震速報檢查（每 5 分鐘）
# ============================================================
def check_earthquake():
    """檢查最新地震，與上次 ID 比對，有新地震則推送"""
    print(f"⏰ [{datetime.now().strftime('%H:%M:%S')}] 檢查地震速報...")
    try:
        eq_no, msg = get_latest_earthquake()
        if not eq_no:
            return
        
        subs = load_subscriptions()
        last_id = subs.get("last_earthquake_id", "")
        
        if str(eq_no) == str(last_id):
            return
        
        try:
            mag_line = [l for l in msg.split("\n") if "規模" in l]
            if mag_line:
                mag = float(mag_line[0].split("M ")[1].strip())
            else:
                mag = 0
        except:
            mag = 0
        
        for user_id, user_sub in subs.get("users", {}).items():
            eq_sub = user_sub.get("earthquake", {})
            if not eq_sub.get("enabled"):
                continue
            min_mag = eq_sub.get("min_magnitude", 4.0)
            
            if mag >= min_mag:
                push_message(user_id, f"🚨 地震速報\n\n{msg}")
                print(f"  ✅ 推送地震速報給 {user_id} (M{mag})")
        
        subs["last_earthquake_id"] = str(eq_no)
        save_subscriptions(subs)
    except Exception as e:
        print(f"❌ 地震速報檢查錯誤: {e}")

# ============================================================
# 🔔 自動推播：空品預報（每天早上 7:00）
# ============================================================
def push_aqi_forecast():
    """推送當日空品預報給訂閱用戶"""
    print(f"⏰ [{datetime.now().strftime('%H:%M:%S')}] 推送空品預報...")
    try:
        subs = load_subscriptions()
        for user_id, user_sub in subs.get("users", {}).items():
            forecast_sub = user_sub.get("aqi_forecast", {})
            if not forecast_sub.get("enabled"):
                continue
            city = forecast_sub.get("city", "臺北市")
            msg = f"🌅 早安！今日空品預報\n\n{get_aqi_forecast(city)}"
            push_message(user_id, msg)
            print(f"  ✅ 推送空品預報給 {user_id} ({city})")
    except Exception as e:
        print(f"❌ 空品預報推送錯誤: {e}")

# ============================================================
# 排程器設定
# ============================================================
scheduler = BackgroundScheduler(timezone="Asia/Taipei")
scheduler.add_job(check_aqi_alert, "interval", minutes=30, id="aqi_alert")
scheduler.add_job(check_earthquake, "interval", minutes=5, id="earthquake")
scheduler.add_job(push_aqi_forecast, "cron", hour=7, minute=0, id="aqi_forecast")
scheduler.start()
atexit.register(lambda: scheduler.shutdown())

# ======================
# LINE 回覆函式
# ======================

# 縣市分組（為了避免超過 13 個 Quick Reply 上限）
CITY_GROUPS = {
    "北部": ["臺北市", "新北市", "基隆市", "桃園市", "新竹市", "新竹縣", "宜蘭縣"],
    "中部": ["臺中市", "苗栗縣", "彰化縣", "南投縣", "雲林縣"],
    "南部": ["高雄市", "臺南市", "嘉義市", "嘉義縣", "屏東縣"],
    "東部": ["花蓮縣", "臺東縣"],
    "離島": ["澎湖縣", "金門縣", "連江縣"],
}

def build_quick_reply(items):
    """items 是 list of (label, text) tuples"""
    if not items:
        return None
    items = items[:13]
    qr_items = [
        QuickReplyItem(action=MessageAction(label=label[:20], text=text))
        for label, text in items
    ]
    return QuickReply(items=qr_items)

def build_city_group_quick_reply():
    items = [(name, name) for name in CITY_GROUPS.keys()]
    return build_quick_reply(items)

def build_city_quick_reply(group_name):
    cities = CITY_GROUPS.get(group_name, [])
    items = [(city, city) for city in cities]
    return build_quick_reply(items)

def build_district_quick_reply(districts, page=0):
    """建立『選行政區』的快速按鈕，支援分頁"""
    PAGE_SIZE = 12
    total = len(districts)
    total_pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    
    page = max(0, min(page, total_pages - 1)) if total_pages > 0 else 0
    
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, total)
    current_districts = districts[start:end]
    
    items = [(d, d) for d in current_districts]
    
    has_prev = page > 0
    has_next = end < total
    
    if has_prev and has_next:
        items = [(d, d) for d in districts[start:start + 11]]
        items.append(("⬅️ 上一頁", "__prev_page__"))
        items.append(("➡️ 下一頁", "__next_page__"))
    elif has_prev:
        items.append(("⬅️ 上一頁", "__prev_page__"))
    elif has_next:
        items.append(("➡️ 下一頁", "__next_page__"))
    
    return build_quick_reply(items)

def build_main_menu_quick_reply():
    items = [
        ("🌫️ 空品", "1"),
        ("📡 雷達", "2"),
        ("🌧️ 雨量", "3"),
        ("🌤️ 氣象", "4"),
        ("🏃 戶外", "5"),
        ("☀️ 紫外線", "6"),
        ("🌀 颱風", "7"),
        ("🔔 推播", "8"),
    ]
    return build_quick_reply(items)

def reply_text(token, msg, quick_reply=None):
    """回覆文字（可附帶 Quick Reply 按鈕）"""
    msg = truncate_msg(msg)
    text_msg = TextMessage(text=msg, quick_reply=quick_reply) if quick_reply else TextMessage(text=msg)
    with ApiClient(configuration) as api:
        MessagingApi(api).reply_message(
            ReplyMessageRequest(reply_token=token, messages=[text_msg])
        )

def reply_image(token, url, quick_reply=None):
    """回覆圖片（可附帶 Quick Reply 按鈕）"""
    img_msg = ImageMessage(
        original_content_url=url,
        preview_image_url=url,
        quick_reply=quick_reply
    ) if quick_reply else ImageMessage(original_content_url=url, preview_image_url=url)
    with ApiClient(configuration) as api:
        MessagingApi(api).reply_message(
            ReplyMessageRequest(reply_token=token, messages=[img_msg])
        )

def handle_district_pagination(event, user_id, state, text):
    """處理行政區的「上一頁/下一頁」按鈕"""
    if text not in ("__prev_page__", "__next_page__"):
        return False
    
    districts = state.get("districts", [])
    current_page = state.get("district_page", 0)
    city = state.get("city", "")
    
    if text == "__next_page__":
        new_page = current_page + 1
    else:
        new_page = max(0, current_page - 1)
    
    new_state = dict(state)
    new_state["district_page"] = new_page
    set_state(user_id, new_state)
    
    total = len(districts)
    total_pages = (total + 11) // 12 if total > 13 else 1
    page_info = f"（第 {new_page + 1}/{total_pages} 頁）" if total_pages > 1 else ""
    
    reply_text(
        event.reply_token,
        f"🏘️ 請選擇 {city} 的行政區{page_info}：",
        quick_reply=build_district_quick_reply(districts, page=new_page)
    )
    return True

# ======================
# 使用者狀態管理
# ======================
# 個人聊天的 state 永不過期(user 可以中斷後繼續)
# 群組/多人聊天室的 state 5 分鐘自動過期(避免久未操作後誤觸發其他 user)
user_state = {}
GROUP_STATE_TTL = 300  # 群組 state 存活時間(秒)

def _is_group_key(key):
    """state key 是不是群組/多人聊天室來源"""
    return isinstance(key, str) and (key.startswith("group_") or key.startswith("room_"))

def set_state(user_id, state):
    # 群組 state 加上時間戳,用來判斷過期
    if _is_group_key(user_id):
        state = dict(state)  # 不影響呼叫端傳進來的 dict
        state["_ts"] = time.time()
    user_state[user_id] = state

def get_state(user_id):
    state = user_state.get(user_id, {})
    # 群組 state 超過 TTL 視為過期,清掉
    if _is_group_key(user_id) and state.get("_ts"):
        if time.time() - state["_ts"] > GROUP_STATE_TTL:
            del user_state[user_id]
            return {}
    return state

def clear_state(user_id):
    if user_id in user_state:
        del user_state[user_id]

# ======================
# 健康檢查端點（給 UptimeRobot 等保持喚醒服務 ping 用）
# 這兩個端點輕量、不會觸發任何業務邏輯，適合定期 ping
# ======================
@app.route("/", methods=["GET"])
def index():
    return "Taiwan Weather LINE Bot is running 🌤️", 200

@app.route("/health", methods=["GET"])
def health():
    return "OK", 200

# ======================
# webhook
# ======================
@app.route("/callback", methods=["POST"])
def callback():
    body = request.get_data(as_text=True)
    sig = request.headers.get("X-Line-Signature", "")
    try:
        handler.handle(body, sig)
    except InvalidSignatureError:
        return "Bad Request", 400
    return "OK", 200

# 雲端備援：可讓外部 cron 服務定時觸發
@app.route("/cron-trigger", methods=["GET", "POST"])
def cron_trigger():
    """外部 cron 觸發端點"""
    job = request.args.get("job", "")
    if job == "aqi_alert":
        check_aqi_alert()
    elif job == "earthquake":
        check_earthquake()
    elif job == "aqi_forecast":
        push_aqi_forecast()
    elif job == "ping":
        pass
    else:
        check_aqi_alert()
        check_earthquake()
    return "OK", 200

# ======================
# 主要 handler
# ======================
@handler.add(MessageEvent, message=TextMessageContent)
def handle(event):
    text = event.message.text.strip()
    source_type = event.source.type  # "user" / "group" / "room"
    is_group = source_type in ("group", "room")
    
    # 取得 sender 的真實 LINE user_id(個人聊天一定有;群組/多人聊天室可能沒有)
    sender_id = getattr(event.source, "user_id", None) or "anonymous"
    
    # state key:
    # - 個人聊天用 user_id
    # - 群組用 "group_<group_id>_<sender_id>",讓每個 user 在群組裡有獨立 state
    # - 多人聊天室用 "room_<room_id>_<sender_id>"
    if source_type == "user":
        user_id = sender_id
    elif source_type == "group":
        user_id = f"group_{event.source.group_id}_{sender_id}"
    elif source_type == "room":
        user_id = f"room_{event.source.room_id}_{sender_id}"
    else:
        return  # 未知來源,忽略
    
    state = get_state(user_id)
    
    # 群組/多人聊天室的呼叫機制:
    # 沒有 active state 時(剛開始或閒置過期),要求前綴觸發,不然 Bot 會在群組裡洗版
    # 已經在 active flow 中的訊息(Quick Reply 按鈕點選等),直接放行
    if is_group and not state:
        TRIGGER_PREFIXES = ("/天氣", "@天氣", "天氣機器人")
        
        matched_prefix = None
        for p in TRIGGER_PREFIXES:
            if text.startswith(p):
                matched_prefix = p
                break
        
        if matched_prefix is None:
            return  # 非觸發訊息,完全安靜
        
        # 去掉前綴,讓後面的主選單邏輯能正常處理
        text = text[len(matched_prefix):].strip()
        
        # 標記進入 main_menu state,後續訊息(包含 Quick Reply 按鈕)就能直接處理
        set_state(user_id, {"step": "main_menu"})
        state = get_state(user_id)
    
    current_step = state.get("step", "main_menu")
    
    # ======================== 取消 / 結束關鍵字 ========================
    # 任何時候打這些字,清掉 state 讓 Bot 安靜下來
    # 群組裡如果連 active state 都沒有,前面已經 return 不會走到這裡
    CANCEL_KEYWORDS = ("取消", "結束", "離開", "停", "退出", "cancel", "exit", "quit", "stop")
    if text.lower() in CANCEL_KEYWORDS:
        clear_state(user_id)
        if is_group:
            # 群組裡用簡短訊息確認(避免洗版)
            reply_text(event.reply_token, "👌 已結束,需要時打「/天氣」叫我。")
        else:
            reply_text(event.reply_token, "👌 已結束對話,需要時隨時叫我。")
        return
    
    # ======================== 主選單 ========================
    if current_step == "main_menu":
        if text in ("1", "空品", "空氣"):
            reply_text(
                event.reply_token,
                "🌫️ 空品查詢\n請選擇縣市分組：",
                quick_reply=build_city_group_quick_reply()
            )
            set_state(user_id, {"step": "aqi_city_group"})
        elif text in ("2", "雷達"):
            reply_image(event.reply_token, get_radar(), quick_reply=build_main_menu_quick_reply())
        elif text in ("3", "雨量"):
            reply_image(event.reply_token, get_rainfall(), quick_reply=build_main_menu_quick_reply())
        elif text in ("4", "氣象", "天氣"):
            items = [("⏰ 即時天氣", "1"), ("📅 天氣預報", "2")]
            reply_text(
                event.reply_token,
                "🌤️ 氣象查詢\n請選擇查詢類型：",
                quick_reply=build_quick_reply(items)
            )
            set_state(user_id, {"step": "weather_type"})
        elif text in ("5", "戶外", "活動"):
            reply_text(
                event.reply_token,
                "🏃 戶外活動建議\n請選擇縣市分組：",
                quick_reply=build_city_group_quick_reply()
            )
            set_state(user_id, {"step": "outdoor_city_group"})
        elif text in ("6", "紫外線", "UV"):
            reply_text(
                event.reply_token,
                "☀️ 紫外線指數\n請選擇縣市分組：",
                quick_reply=build_city_group_quick_reply()
            )
            set_state(user_id, {"step": "uv_city_group"})
        elif text in ("7", "颱風"):
            reply_text(event.reply_token, get_typhoon_info(), quick_reply=build_main_menu_quick_reply())
        elif text in ("8", "推播", "推播設定", "訂閱"):
            if is_group:
                # 群組裡不開放推播設定:訂閱資料以 user_id 為 key,推播只能對個人發
                reply_text(
                    event.reply_token,
                    "🔔 推播設定請在個人聊天中使用喔!\n\n請點我的頭像加好友,然後私訊我「推播」來設定。",
                    quick_reply=build_main_menu_quick_reply()
                )
                clear_state(user_id)
                return
            sub = get_user_sub(user_id)
            aqi_status = "✅ 開啟" if sub["aqi_alert"]["enabled"] else "❌ 關閉"
            eq_status = "✅ 開啟" if sub["earthquake"]["enabled"] else "❌ 關閉"
            forecast_status = "✅ 開啟" if sub["aqi_forecast"]["enabled"] else "❌ 關閉"
            items = [
                ("🚨 AQI 警示", "1"),
                ("🌏 地震速報", "2"),
                ("🔮 空品預報", "3"),
                ("🔙 返回", "0"),
            ]
            reply_text(event.reply_token, (
                "🔔 推播設定\n\n"
                f"1. AQI 警示：{aqi_status}\n"
                f"   門檻：{sub['aqi_alert']['threshold']}\n"
                f"2. 地震速報：{eq_status}\n"
                f"   最小規模：M{sub['earthquake']['min_magnitude']}\n"
                f"3. 空品預報：{forecast_status}\n"
                f"   縣市：{sub['aqi_forecast']['city']}\n\n"
                "請選擇要設定的項目："
            ), quick_reply=build_quick_reply(items))
            set_state(user_id, {"step": "push_menu"})
        else:
            reply_text(event.reply_token, (
                "🌤️ 聊天機器人\n\n"
                "請選擇要查詢的功能："
            ), quick_reply=build_main_menu_quick_reply())
    
    # ======================== 空品流程 ========================
    elif current_step == "aqi_city_group":
        if text in CITY_GROUPS:
            reply_text(
                event.reply_token,
                f"📍 {text}縣市：",
                quick_reply=build_city_quick_reply(text)
            )
            set_state(user_id, {"step": "aqi_city"})
        else:
            reply_text(event.reply_token, "⚠️ 請從按鈕選擇分組", quick_reply=build_city_group_quick_reply())
    
    elif current_step == "aqi_city":
        if text in CITY_MAP:
            selected_city = text
            stations = get_aqi_by_city(selected_city)
            if not stations:
                reply_text(event.reply_token, f"❌ {selected_city} 無測站資料", quick_reply=build_main_menu_quick_reply())
                set_state(user_id, {"step": "main_menu"})
            else:
                station_items = [(s.get('sitename', '?'), s.get('sitename', '?')) for s in stations[:13]]
                reply_text(
                    event.reply_token,
                    f"📍 {selected_city} 的測站（請選擇）：",
                    quick_reply=build_quick_reply(station_items)
                )
                set_state(user_id, {"step": "aqi_station", "city": selected_city, "stations": stations})
        else:
            reply_text(event.reply_token, "⚠️ 請從按鈕選擇縣市")
    
    elif current_step == "aqi_station":
        stations = state.get("stations", [])
        target = None
        for s in stations:
            if s.get("sitename") == text:
                target = s
                break
        if target:
            reply_text(event.reply_token, get_aqi_detail(target), quick_reply=build_main_menu_quick_reply())
            clear_state(user_id)
        else:
            reply_text(event.reply_token, "⚠️ 請從按鈕選擇測站")
    
    # ======================== 氣象流程 ========================
    elif current_step == "weather_type":
        if text in ("1", "即時"):
            reply_text(
                event.reply_token,
                "⏰ 即時天氣\n請選擇縣市分組：",
                quick_reply=build_city_group_quick_reply()
            )
            set_state(user_id, {"step": "weather_now_city_group"})
        elif text in ("2", "預報"):
            items = [("⏱️ 12 小時", "1"), ("📅 3 天", "2"), ("🗓️ 一週", "3")]
            reply_text(
                event.reply_token,
                "📅 預報類型：",
                quick_reply=build_quick_reply(items)
            )
            set_state(user_id, {"step": "weather_forecast_type"})
        else:
            reply_text(event.reply_token, "⚠️ 請選擇按鈕")
    
    # ======================== 縣市分組共用處理 ========================
    elif current_step in ("weather_now_city_group", "weather_forecast_city_group", "outdoor_city_group", "uv_city_group"):
        if text in CITY_GROUPS:
            next_step_map = {
                "weather_now_city_group": "weather_now_city",
                "weather_forecast_city_group": "weather_forecast_city",
                "outdoor_city_group": "outdoor_city",
                "uv_city_group": "uv_city",
            }
            new_state = {"step": next_step_map[current_step]}
            if state.get("mode"):
                new_state["mode"] = state.get("mode")
            set_state(user_id, new_state)
            reply_text(
                event.reply_token,
                f"📍 {text}縣市：",
                quick_reply=build_city_quick_reply(text)
            )
        else:
            reply_text(event.reply_token, "⚠️ 請從按鈕選擇分組", quick_reply=build_city_group_quick_reply())
    
    # ======================== 縣市選擇後，去抓行政區 ========================
    elif current_step in ("weather_now_city", "weather_forecast_city", "outdoor_city"):
        if text in CITY_MAP:
            selected_city = text
            try:
                code = CITY_MAP.get(selected_city)
                url = f"https://opendata.cwa.gov.tw/api/v1/rest/datastore/F-D0047-{code}"
                params = {"Authorization": CWA_API_KEY, "format": "JSON"}
                data = requests.get(url, params=params, timeout=20, verify=False).json()
                if "Locations" in data.get("records", {}):
                    locations = data["records"]["Locations"][0].get("Location", [])
                    districts = [loc.get("LocationName", "") for loc in locations]
                    if districts:
                        next_step = {
                            "weather_now_city": "weather_now_district",
                            "weather_forecast_city": "weather_forecast_district",
                            "outdoor_city": "outdoor_district"
                        }[current_step]
                        new_state = {"step": next_step, "city": selected_city, "districts": districts, "district_page": 0}
                        if current_step == "weather_forecast_city":
                            new_state["mode"] = state.get("mode")
                        set_state(user_id, new_state)
                        total_pages = (len(districts) + 11) // 12 if len(districts) > 13 else 1
                        page_info = f"（第 1/{total_pages} 頁）" if total_pages > 1 else ""
                        reply_text(
                            event.reply_token,
                            f"🏘️ 請選擇 {selected_city} 的行政區{page_info}：",
                            quick_reply=build_district_quick_reply(districts, page=0)
                        )
                    else:
                        reply_text(event.reply_token, f"❌ {selected_city} 無行政區資料", quick_reply=build_main_menu_quick_reply())
                        set_state(user_id, {"step": "main_menu"})
                else:
                    reply_text(event.reply_token, f"❌ {selected_city} 無行政區資料", quick_reply=build_main_menu_quick_reply())
                    set_state(user_id, {"step": "main_menu"})
            except Exception as e:
                reply_text(event.reply_token, f"❌ 無法取得 {selected_city} 的行政區清單\n原因：{str(e)[:150]}", quick_reply=build_main_menu_quick_reply())
                set_state(user_id, {"step": "main_menu"})
        else:
            reply_text(event.reply_token, "⚠️ 請從按鈕選擇縣市")
    
    elif current_step == "weather_now_district":
        if handle_district_pagination(event, user_id, state, text):
            return
        districts = state.get("districts", [])
        city = state.get("city", "")
        if text in districts:
            reply_text(event.reply_token, get_weather_now(city, text), quick_reply=build_main_menu_quick_reply())
            clear_state(user_id)
        else:
            reply_text(event.reply_token, "⚠️ 請從按鈕選擇行政區")
    
    elif current_step == "weather_forecast_type":
        mode = None
        if text in ("1", "12"):
            mode = "12h"
        elif text == "2":
            mode = "3day"
        elif text in ("3", "7", "一週"):
            mode = "7day"
        if mode:
            reply_text(
                event.reply_token,
                "📍 請選擇縣市分組：",
                quick_reply=build_city_group_quick_reply()
            )
            set_state(user_id, {"step": "weather_forecast_city_group", "mode": mode})
        else:
            reply_text(event.reply_token, "⚠️ 請選擇按鈕")
    
    elif current_step == "weather_forecast_district":
        if handle_district_pagination(event, user_id, state, text):
            return
        districts = state.get("districts", [])
        city = state.get("city", "")
        mode = state.get("mode", "12h")
        if text in districts:
            if mode == "12h":
                result = fetch_12h(city, text)
            elif mode == "3day":
                result = fetch_3day(city, text)
            else:
                result = fetch_7day(city, text)
            reply_text(event.reply_token, result, quick_reply=build_main_menu_quick_reply())
            clear_state(user_id)
        else:
            reply_text(event.reply_token, "⚠️ 請從按鈕選擇行政區")
    
    # ======================== 戶外活動建議流程 ========================
    elif current_step == "outdoor_district":
        if handle_district_pagination(event, user_id, state, text):
            return
        districts = state.get("districts", [])
        city = state.get("city", "")
        if text in districts:
            reply_text(event.reply_token, get_outdoor_advice(city, text), quick_reply=build_main_menu_quick_reply())
            clear_state(user_id)
        else:
            reply_text(event.reply_token, "⚠️ 請從按鈕選擇行政區")
    
    # ======================== 紫外線流程 ========================
    elif current_step == "uv_city":
        if text in CITY_MAP:
            reply_text(event.reply_token, get_uv_info(text), quick_reply=build_main_menu_quick_reply())
            clear_state(user_id)
        else:
            reply_text(event.reply_token, "⚠️ 請從按鈕選擇縣市")
    
    # ======================== 推播設定流程 ========================
    elif current_step == "push_menu":
        if text == "0":
            clear_state(user_id)
            reply_text(event.reply_token, "已返回主選單。", quick_reply=build_main_menu_quick_reply())
        elif text == "1":
            sub = get_user_sub(user_id)
            status = "✅ 已開啟" if sub["aqi_alert"]["enabled"] else "❌ 已關閉"
            items = [
                ("🔄 開啟/關閉", "1"),
                ("📊 設定門檻", "2"),
                ("🔙 返回", "0"),
            ]
            reply_text(event.reply_token, (
                f"🚨 AQI 警示設定\n目前狀態：{status}\n門檻：{sub['aqi_alert']['threshold']}\n\n"
                "請選擇操作："
            ), quick_reply=build_quick_reply(items))
            set_state(user_id, {"step": "push_aqi_alert"})
        elif text == "2":
            sub = get_user_sub(user_id)
            status = "✅ 已開啟" if sub["earthquake"]["enabled"] else "❌ 已關閉"
            items = [
                ("🔄 開啟/關閉", "1"),
                ("📊 設定最小規模", "2"),
                ("🔙 返回", "0"),
            ]
            reply_text(event.reply_token, (
                f"🌏 地震速報設定\n目前狀態：{status}\n最小規模：M{sub['earthquake']['min_magnitude']}\n\n"
                "請選擇操作："
            ), quick_reply=build_quick_reply(items))
            set_state(user_id, {"step": "push_earthquake"})
        elif text == "3":
            sub = get_user_sub(user_id)
            status = "✅ 已開啟" if sub["aqi_forecast"]["enabled"] else "❌ 已關閉"
            items = [
                ("🔄 開啟/關閉", "1"),
                ("📍 設定縣市", "2"),
                ("🔙 返回", "0"),
            ]
            reply_text(event.reply_token, (
                f"🔮 空品預報設定（每日 7:00）\n目前狀態：{status}\n縣市：{sub['aqi_forecast']['city']}\n\n"
                "請選擇操作："
            ), quick_reply=build_quick_reply(items))
            set_state(user_id, {"step": "push_forecast"})
        else:
            reply_text(event.reply_token, "⚠️ 請從按鈕選擇")
    
    elif current_step == "push_aqi_alert":
        if text == "0":
            clear_state(user_id)
            reply_text(event.reply_token, "已返回。", quick_reply=build_main_menu_quick_reply())
        elif text == "1":
            sub = get_user_sub(user_id)
            new_val = not sub["aqi_alert"]["enabled"]
            update_user_sub(user_id, "aqi_alert", "enabled", new_val)
            reply_text(event.reply_token, f"✅ AQI 警示已{'開啟' if new_val else '關閉'}", quick_reply=build_main_menu_quick_reply())
            clear_state(user_id)
        elif text == "2":
            items = [("100", "100"), ("150", "150"), ("200", "200")]
            reply_text(event.reply_token, "請選擇新門檻：", quick_reply=build_quick_reply(items))
            set_state(user_id, {"step": "push_aqi_threshold"})
        else:
            reply_text(event.reply_token, "⚠️ 請從按鈕選擇")
    
    elif current_step == "push_aqi_threshold":
        if text in ("100", "150", "200"):
            update_user_sub(user_id, "aqi_alert", "threshold", int(text))
            reply_text(event.reply_token, f"✅ 已設定門檻為 {text}", quick_reply=build_main_menu_quick_reply())
            clear_state(user_id)
        else:
            reply_text(event.reply_token, "⚠️ 請從按鈕選擇")
    
    elif current_step == "push_earthquake":
        if text == "0":
            clear_state(user_id)
            reply_text(event.reply_token, "已返回。", quick_reply=build_main_menu_quick_reply())
        elif text == "1":
            sub = get_user_sub(user_id)
            new_val = not sub["earthquake"]["enabled"]
            update_user_sub(user_id, "earthquake", "enabled", new_val)
            reply_text(event.reply_token, f"✅ 地震速報已{'開啟' if new_val else '關閉'}", quick_reply=build_main_menu_quick_reply())
            clear_state(user_id)
        elif text == "2":
            items = [("M3.0", "3"), ("M4.0", "4"), ("M5.0", "5")]
            reply_text(event.reply_token, "請選擇最小規模：", quick_reply=build_quick_reply(items))
            set_state(user_id, {"step": "push_eq_magnitude"})
        else:
            reply_text(event.reply_token, "⚠️ 請從按鈕選擇")
    
    elif current_step == "push_eq_magnitude":
        if text in ("3", "4", "5"):
            update_user_sub(user_id, "earthquake", "min_magnitude", float(text))
            reply_text(event.reply_token, f"✅ 已設定最小規模為 M{text}", quick_reply=build_main_menu_quick_reply())
            clear_state(user_id)
        else:
            reply_text(event.reply_token, "⚠️ 請從按鈕選擇")
    
    elif current_step == "push_forecast":
        if text == "0":
            clear_state(user_id)
            reply_text(event.reply_token, "已返回。", quick_reply=build_main_menu_quick_reply())
        elif text == "1":
            sub = get_user_sub(user_id)
            new_val = not sub["aqi_forecast"]["enabled"]
            update_user_sub(user_id, "aqi_forecast", "enabled", new_val)
            reply_text(event.reply_token, f"✅ 空品預報已{'開啟' if new_val else '關閉'}", quick_reply=build_main_menu_quick_reply())
            clear_state(user_id)
        elif text == "2":
            reply_text(
                event.reply_token,
                "📍 請選擇縣市分組：",
                quick_reply=build_city_group_quick_reply()
            )
            set_state(user_id, {"step": "push_forecast_city_group"})
        else:
            reply_text(event.reply_token, "⚠️ 請從按鈕選擇")
    
    elif current_step == "push_forecast_city_group":
        if text in CITY_GROUPS:
            reply_text(
                event.reply_token,
                f"📍 {text}縣市：",
                quick_reply=build_city_quick_reply(text)
            )
            set_state(user_id, {"step": "push_forecast_city"})
        else:
            reply_text(event.reply_token, "⚠️ 請從按鈕選擇分組", quick_reply=build_city_group_quick_reply())
    
    elif current_step == "push_forecast_city":
        if text in CITY_MAP:
            update_user_sub(user_id, "aqi_forecast", "city", text)
            reply_text(event.reply_token, f"✅ 已設定預報縣市為 {text}", quick_reply=build_main_menu_quick_reply())
            clear_state(user_id)
        else:
            reply_text(event.reply_token, "⚠️ 請從按鈕選擇縣市")
    
    else:
        reply_text(event.reply_token, (
            "🌤️ 聊天機器人\n\n"
            "請選擇要查詢的功能："
        ), quick_reply=build_main_menu_quick_reply())
        set_state(user_id, {"step": "main_menu"})

# ======================
# run（只用於本地測試；雲端用 gunicorn 直接 import app:app，不會進到這裡）
# ======================
if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    print("🚀 LINE Bot 已啟動")
    print(f"⏰ 排程器：AQI 每 30 分鐘 / 地震每 5 分鐘 / 空品預報每天 7:00")
    print(f"📁 訂閱資料：{SUBSCRIPTIONS_FILE}")
    print(f"🌐 監聽 port：{port}")
    app.run(host="0.0.0.0", port=port, use_reloader=False)