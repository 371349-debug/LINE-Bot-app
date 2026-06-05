# 🌤️ Taiwan Weather LINE Bot

一個專為台灣使用者設計的 LINE 氣象機器人,整合中央氣象署(CWA)與環境部(MOENV)開放資料,提供即時天氣、空氣品質、紫外線、颱風、地震等資訊查詢與自動推播服務。

支援**個人聊天**與**群組聊天**雙模式,部署在 Render 雲端,24 小時運作。

---

## 📋 目錄

- [功能介紹](#-功能介紹)
- [技術架構](#-技術架構)
- [系統流程](#-系統流程)
- [使用方式](#-使用方式)
- [部署指南](#-部署指南)
- [環境變數](#-環境變數)
- [本地開發](#-本地開發)
- [客製化](#-客製化)
- [注意事項與限制](#-注意事項與限制)
- [檔案結構](#-檔案結構)

---

## ✨ 功能介紹

### 主動查詢功能(8 項)

| 編號 | 功能 | 說明 |
|------|------|------|
| 1 | 🌫️ 空品(AQI) | 查詢全台 AQI 測站,顯示 AQI、PM2.5、健康建議 |
| 2 | 📡 雷達圖 | 中央氣象署即時雷達回波圖 |
| 3 | 🌧️ 雨量圖 | 中央氣象署即時雨量分布圖 |
| 4 | 🌤️ 氣象 | 即時天氣 / 12 小時逐時 / 3 日 / 一週預報 |
| 5 | 🏃 戶外活動建議 | 綜合 AQI + 天氣 + 紫外線,給出 A~F 評等 |
| 6 | ☀️ 紫外線指數 | 各縣市測站 UV 數值,含當下時段估算 |
| 7 | 🌀 颱風資訊 | 目前活動中颱風的位置、強度、路徑 |
| 8 | 🔔 推播設定 | 訂閱以下三種自動推播 |

### 自動推播功能(3 項)

| 排程 | 觸發條件 | 推播內容 |
|------|---------|----------|
| 每 30 分鐘 | AQI 超過使用者設定門檻 | AQI 警示通知(列出超標測站) |
| 每 5 分鐘 | 有新顯著有感地震 | 地震速報(時間、震央、規模、震度) |
| 每天 7:00 | 訂閱用戶的指定縣市 | 當日空品預報 |

每位使用者可獨立設定:
- AQI 警示門檻(100 / 150 / 200)
- 地震最小規模(M3.0 / M4.0 / M5.0)
- 空品預報關注縣市

---

## 🛠 技術架構

- **語言**:Python 3
- **Web 框架**:Flask
- **WSGI 伺服器**:gunicorn(production)
- **LINE SDK**:line-bot-sdk v3
- **排程器**:APScheduler(in-process,timezone Asia/Taipei)
- **訂閱儲存**:JSON 檔案(可掛 Persistent Disk)
- **資料來源**:
  - [中央氣象署 CWA Open Data API](https://opendata.cwa.gov.tw/)
  - [環境部 MOENV Open Data API](https://data.moenv.gov.tw/)
- **部署平台**:[Render](https://render.com/)(Free Tier)
- **持續喚醒**:[UptimeRobot](https://uptimerobot.com/)

---

## 🏗 系統流程

```
┌──────────────────────────────────────────────────────────────┐
│                                                                │
│   📱 LINE User / 群組成員                                     │
│        │                                                       │
│        ▼ 傳訊息                                                │
│   🟢 LINE Platform                                            │
│        │                                                       │
│        ▼ POST webhook                                          │
│   ☁️  Render Web Service                                      │
│        ├─ gunicorn (workers=1, threads=4)                     │
│        ├─ Flask app (app.py)                                  │
│        │   ├─ /callback   ← LINE webhook                      │
│        │   ├─ /health     ← UptimeRobot ping                  │
│        │   └─ /cron-trigger ← (備援用)                        │
│        ├─ APScheduler                                          │
│        │   ├─ AQI 警示(每 30 min)                           │
│        │   ├─ 地震速報(每 5 min)                            │
│        │   └─ 空品預報(每天 7:00)                          │
│        └─ subscriptions.json(訂閱資料)                       │
│        │                                                       │
│        ▼ HTTP 查詢                                             │
│   🌐 CWA + MOENV Open Data API                                │
│                                                                │
│   ⏰ UptimeRobot ping /health 每 5 min  → 防 Render sleep    │
│   📦 GitHub repo → push 後自動 redeploy                       │
│                                                                │
└──────────────────────────────────────────────────────────────┘
```

---

## 💬 使用方式

### 個人聊天

直接私訊 Bot 任何文字,即會跳出主選單:

```
[You] 你好
[Bot] 🌤️ 聊天機器人
       請選擇要查詢的功能:
       [🌫️ 空品] [📡 雷達] [🌧️ 雨量] [🌤️ 氣象]
       [🏃 戶外] [☀️ 紫外線] [🌀 颱風] [🔔 推播]
```

接著點按鈕一路操作即可。

### 群組聊天

群組內 Bot 預設**安靜**,只有用觸發詞才會喚醒:

| 觸發詞 | 效果 |
|--------|------|
| `/天氣` | 跳出主選單 |
| `@天氣` | 同上 |
| `天氣機器人` | 同上 |
| `/天氣 空品` | 直接進入空品查詢 |
| `/天氣 4` | 直接進入氣象查詢 |

進入流程後,**該使用者**的 Quick Reply 按鈕可正常使用,**其他成員的訊息不受影響**(每個 user 在群組內有獨立 state)。

### 取消對話

任何時候打以下任一關鍵字,Bot 立刻收手:

```
取消 / 結束 / 離開 / 停 / 退出
cancel / exit / quit / stop
```

### 自動推播訂閱

在個人聊天中傳「推播」進入設定選單(群組內不支援推播訂閱)。

---

## 🚀 部署指南

### 前置準備

需要申請 4 把 API 金鑰:

1. **LINE Channel Access Token & Channel Secret**
   - 至 [LINE Developers Console](https://developers.line.biz/console/) 建立 Provider 與 Messaging API Channel
   - 在 Channel 的 **Messaging API** 頁籤取得這兩個值
2. **CWA API Key**
   - 至 [CWA 氣象開放資料平台](https://opendata.cwa.gov.tw/) 註冊並申請 API 授權碼
3. **MOENV API Key**
   - 至 [環境部開放資料平台](https://data.moenv.gov.tw/) 註冊並取得 API key

### 步驟一:Fork / Clone 此 repo

```bash
git clone https://github.com/你的帳號/LINE-Bot-app.git
```

確認 repo 包含以下檔案:
- `app.py`
- `requirements.txt`

### 步驟二:部署到 Render

1. 至 [Render](https://render.com/) 註冊並用 GitHub 帳號登入
2. **New +** → **Web Service** → 連到你的 repo
3. 設定:
   - **Region**:Singapore(離台灣最近)
   - **Branch**:`main`
   - **Runtime**:Python 3
   - **Build Command**:`pip install -r requirements.txt`
   - **Start Command**:
     ```
     gunicorn --workers 1 --threads 4 --timeout 60 --bind 0.0.0.0:$PORT app:app
     ```
   - **Plan**:Free
4. 在 **Environment Variables** 加入 4 個變數(見下節)
5. **Create Web Service** → 等待 2-5 分鐘部署完成
6. 取得網址,例如:`https://your-app-name.onrender.com`

> ⚠️ `--workers 1` 為必要設定,避免 APScheduler 重複觸發推播

### 步驟三:設定 LINE Webhook

1. 進 [LINE Developers Console](https://developers.line.biz/console/) → 你的 Channel → **Messaging API**
2. **Webhook URL** 填:
   ```
   https://your-app-name.onrender.com/callback
   ```
3. 按 **Update** → 按 **Verify** → 應該回 `Success`
4. **Use webhook** 切到開啟

### 步驟四:關閉自動回應(避免訊息打架)

1. 至 [LINE Official Account Manager](https://manager.line.biz/)
2. 進你的 Bot 帳號 → **設定** → **回應設定**
3. 確認:
   - ✅ Webhook 開啟
   - ⚫ 回應方式選「手動聊天」(不要選「+ 自動回應訊息」)
   - 建議關閉「加入好友的歡迎訊息」

### 步驟五:允許加入群組(選擇性)

如要支援群組:
1. **LINE Official Account Manager** → 設定 → 帳號設定
2. 「**加入群組或多人聊天室**」設為「**接受邀請**」
3. 即可在 LINE 群組設定中邀請 Bot 加入

### 步驟六:設定 UptimeRobot 防 sleep(必要)

Render 免費版閒置 15 分鐘會 sleep,Bot 會失去回應且 APScheduler 停止運作。

1. 至 [UptimeRobot](https://uptimerobot.com/) 註冊(免費,免綁卡)
2. **+ New Monitor**:
   - **Monitor Type**:HTTP(s)
   - **URL**:`https://your-app-name.onrender.com/health`
   - **Monitoring Interval**:5 分鐘
3. Save 即可

### 步驟七:Persistent Disk(選擇性)

如想保留訂閱資料(避免 redeploy 時清空):

1. Render Dashboard → **Disks** → **Add Disk**
2. Mount Path:`/var/data`,Size:1 GB(月費約 USD$0.25)
3. 在 Environment Variables 加入:
   ```
   SUBSCRIPTIONS_FILE=/var/data/subscriptions.json
   ```
4. 手動 redeploy 一次

---

## 🔐 環境變數

| 變數名稱 | 必填 | 預設 | 說明 |
|---------|------|------|------|
| `CHANNEL_ACCESS_TOKEN` | ✅ | — | LINE Channel Access Token |
| `CHANNEL_SECRET` | ✅ | — | LINE Channel Secret |
| `CWA_API_KEY` | ✅ | — | 中央氣象署 API 授權碼 |
| `MOENV_API_KEY` | ✅ | — | 環境部 API key |
| `SUBSCRIPTIONS_FILE` | ❌ | `subscriptions.json` | 訂閱資料儲存路徑 |
| `PORT` | ❌ | 5000 | 監聽 port(Render 會自動設定) |

---

## 💻 本地開發

### 環境準備

```bash
# Clone repo
git clone https://github.com/你的帳號/LINE-Bot-app.git
cd LINE-Bot-app

# 建立虛擬環境
python3 -m venv venv
source venv/bin/activate          # macOS/Linux
# venv\Scripts\activate           # Windows

# 安裝套件
pip install -r requirements.txt
```

### 設定環境變數

**macOS/Linux:**
```bash
export CHANNEL_ACCESS_TOKEN="..."
export CHANNEL_SECRET="..."
export CWA_API_KEY="..."
export MOENV_API_KEY="..."
```

**Windows PowerShell:**
```powershell
$env:CHANNEL_ACCESS_TOKEN="..."
$env:CHANNEL_SECRET="..."
$env:CWA_API_KEY="..."
$env:MOENV_API_KEY="..."
```

### 啟動

```bash
python3 app.py
```

預設監聽 `http://0.0.0.0:5000`。

### 本地測試 LINE Webhook

LINE 平台要打 webhook 必須是 HTTPS。本地測試可用 [ngrok](https://ngrok.com/):

```bash
ngrok http 5000
```

把 ngrok 給的 `https://xxxx.ngrok-free.app/callback` 填到 LINE Developer Console 的 Webhook URL,即可在本地測試。

---

## 🎨 客製化

### 修改群組觸發詞

`app.py` 約第 1410 行:

```python
TRIGGER_PREFIXES = ("/天氣", "@天氣", "天氣機器人")
```

加入或修改觸發詞,例如:
```python
TRIGGER_PREFIXES = ("/天氣", "氣象", "/bot", "weather")
```

> ⚠️ 避免使用過於常見的詞(如「天氣」、「你好」),容易在群組中誤觸發

### 修改取消關鍵字

`app.py` 約第 1433 行:

```python
CANCEL_KEYWORDS = ("取消", "結束", "離開", "停", "退出", "cancel", "exit", "quit", "stop")
```

### 修改排程頻率

`app.py` 排程器設定區段(約第 1175 行):

```python
scheduler.add_job(check_aqi_alert, "interval", minutes=30, id="aqi_alert")
scheduler.add_job(check_earthquake, "interval", minutes=5, id="earthquake")
scheduler.add_job(push_aqi_forecast, "cron", hour=7, minute=0, id="aqi_forecast")
```

### 修改群組 State 過期時間

`app.py` 約第 1310 行:

```python
GROUP_STATE_TTL = 300  # 群組 state 存活時間(秒),預設 5 分鐘
```

---

## ⚠️ 注意事項與限制

### LINE 訊息配額

| 類型 | 計費 | 說明 |
|------|------|------|
| 回覆訊息(Reply) | 🆓 免費、無上限 | 使用者主動傳訊息,Bot 用 reply token 回應 |
| 推播訊息(Push) | 💰 計入月度配額 | Bot 主動發送 |

LINE 免費版(輕用量)每月推播上限 **200 則**。本專案的推播來源主要是:
- 空品預報(每天 1 則 × 訂閱人數)
- AQI 警示(空氣品質不好時才推)
- 地震速報(顯著有感地震時才推)

**1 個訂閱者約用 50-80 則/月**,3 個以上訂閱所有通知會接近上限。

### Render Free Tier 限制

- 閒置 15 分鐘自動 sleep
- 每月 750 小時免費運行時間
- 沒有 Persistent Disk(需另付費)
- 配 0.1 vCPU + 512 MB RAM(對此專案夠用)

UptimeRobot 每 5 分鐘 ping `/health` 可有效避免 sleep。

### CWA / MOENV API 注意

- CWA API 偶有 SSL 憑證鏈問題,本專案已對 CWA 呼叫加上 `verify=False`
- MOENV 同樣加上 `verify=False`
- 兩者皆為公開資料,無安全風險

### 群組功能限制

- 群組成員若未加 Bot 為好友,Bot 拿不到該 user 的 user_id(會用 `anonymous` 替代)
- 多個匿名 user 在同一群組會共享 state
- 群組 state 預設 5 分鐘過期(避免殘留狀態誤觸發)

---

## 📁 檔案結構

```
LINE-Bot-app/
├── app.py                  # 主程式(~1860 行)
├── requirements.txt        # Python 套件相依
├── README.md               # 本檔案
└── subscriptions.json      # 訂閱資料(執行時自動產生)
```

### `app.py` 內部結構

```
├── 模組 import 與設定
├── 環境變數讀取(4 個 API key)
├── 城市對照表(CITY_MAP)
├── 紫外線測站對照表(UV_STATIONS,約 60 個測站)
├── 訂閱資料 I/O(load/save subscriptions)
├── 工具函式(icon、warning、truncate 等)
├── 時間與數值解析(parse_dt、get_smart_value)
├── 天氣預報函式(fetch_12h、fetch_3day、fetch_7day、get_weather_now)
├── AQI 函式(get_aqi_by_city、get_aqi_detail)
├── 雷達 / 雨量圖 URL 產生
├── 戶外活動建議(get_outdoor_advice)
├── 紫外線函式(get_uv_for_city、get_uv_info、get_uv_estimate_now)
├── 颱風函式(get_typhoon_info)
├── 地震函式(get_latest_earthquake)
├── 空品預報函式(get_aqi_forecast)
├── 推播核心函式(push_message、check_aqi_alert、check_earthquake、push_aqi_forecast)
├── APScheduler 排程設定
├── LINE Quick Reply 建構函式(主選單、縣市、行政區分頁等)
├── 使用者狀態管理(set_state、get_state、clear_state,含群組 TTL)
├── Flask 路由
│   ├── GET /              首頁
│   ├── GET /health        健康檢查
│   ├── POST /callback     LINE webhook
│   └── GET/POST /cron-trigger  外部 cron 備援
└── 主 LINE handler(處理 8 大功能 + 推播訂閱)
```

---

## 🤝 貢獻

歡迎 PR 或 issue。常見可延伸方向:

- 增加新功能(空氣品質歷史曲線、雷達動畫)
- 改進評分演算法(戶外活動建議納入更多參數)
- 多語系支援
- 加入單元測試(GitHub Actions workflow)

---

## 📜 License

MIT License — 自由使用、修改、散布,僅需保留原作者聲明。

---

## 🙏 資料來源致謝

- **中央氣象署**(Central Weather Administration)— 氣象、紫外線、颱風、地震資料
- **環境部**(Ministry of Environment)— 空氣品質、空品預報資料
- **LINE 開發者社群** — Messaging API 與相關範例
