# fin_news_daily

> 每天美股收盤後，自動產生一份繁體中文市場敘事，推送到 Telegram。

Finnhub 抓行情 → 18 支板塊 ETF 計分板 → DeepSeek `:online` 生成七節白話敘事 → Telegram 推送 + Markdown 歸檔。
每日 08:00 AWST (Perth) 自動執行，透過 GitHub Actions。

---

## 範例輸出

```
📈 Daily Market Digest
📅 09 Jun 2026 · 12 stories · 🇨🇳 🌍 🇭🇰
板塊: AI記憶體/DRAM +8.5% · 半導體/AI晶片 +5.0% · 量子 +3.2% · … · 儲能/鋰電 -1.6%

一、今天一句話
晶片股強力反彈帶動納指上漲1.5%，市場焦點轉向SpaceX史上最大IPO前夕。

二、市場氣氛(情緒面,白話)
- 今天是risk-on環境，資金從避險資產流出（TLT跌0.5%），湧向科技股（QQQ漲1.6%），
  小型股IWM漲0.9%也顯示風險偏好回升。
- VIX下跌1.6%至23.91，恐慌情緒降溫，但仍在中等偏高水準（VIX高於20算緊張）。

三、哪些板塊領升、哪些領跌(技術面)
領升：
1. AI記憶體/DRAM (DRAM) +8.5%：美光科技反彈5%帶動，黃仁勳訪韓確認HBM4供應鏈進展
2. 半導體/AI晶片 (SMH) +5.0%：瑞銀稱科技基本面仍強，英偉達、博通止跌回升
領跌：
1. 儲能/鋰電 (LIT) -1.6%：今日無明顯消息面，可能受上周科技股拋售餘波影響

四、錢在往哪裡流
資金明顯回流科技成長股（QQQ領漲），小型股表現優於大盤（IWM漲0.9%＞SPY漲0.2%），
避險資產TLT下跌顯示資金從債市撤出，市場風險胃納回升（HYG小漲0.1%）。

五、今天的「敘事」是什麼
1. 「晶片股超跌反彈」正在升溫：費半指數上周暴跌10%後，美光、英偉達帶頭反攻
2. 「SpaceX IPO前躁動」持續升溫：史上最大IPO（1.8兆美元估值）將於周五登場

六、值得留意的異動
- DRAM板塊暴漲8.5%：需注意美光從上周高點仍跌8%，反彈能否持續要看HBM4實際出貨進度

七、明天怎麼驗證
觀察SMH能否站回50日均線（目前約在325美元），若成功站穩將確認半導體板塊短期底部形成。

以上為市場資訊與情緒解讀，非投資建議，個股決策請自行判斷。

───────────────────────────────
Stories — 09 Jun 2026

AI Compute & Chips
• 🇺🇸 Nasdaq Composite Reaches New All-Time High Amid Tech Earnings Rally (CNBC)

Macro & Other
• 🌍 Dollar eases as Middle East hopes outweigh prospects of higher US rates (Reuters)
• 🌍 Gold steady as lower oil offsets US rate-hike fears (Reuters)
• 🌍 Trump's trade war has a new target: forced labor (CNBC)
...
```

---

## 運作原理

```
Finnhub
  ├─ market news (1 call)          ─→  bullet brief (新聞快訊)
  ├─ quotes: 18 ETF + 8 gauges
  │   + 15 anchor stocks           ─→  板塊計分板 + 大盤儀表
  └─ Fear & Greed (CNN keyless)    ─→  情緒指標

DeepSeek :online
  ├─ 輸入：計分板 + 大盤 + 情緒 + 本地新聞 feed
  └─ 輸出：七節繁體中文敘事 (web search 補全當天消息)

Telegram  →  header · narrative · bullet brief
archive/  →  YYYY-MM-DD.md (YAML frontmatter + 敘事 + 故事索引)
```

### 板塊 ETF 計分板（18 支）

| 板塊 | ETF | 板塊 | ETF |
|------|-----|------|-----|
| 半導體/AI晶片 | SMH | AI 記憶體/DRAM | DRAM |
| AI 應用 | AIQ | 數據中心 | DTCR |
| 雲計算 | SKYY | 機器人 | BOTZ |
| 量子 | QTUM | 網絡安全 | CIBR |
| 核電 | NLR | 光伏 | TAN |
| 儲能/鋰電 | LIT | 天然氣 | FCG |
| 太空 | ARKX | 軍工/國防 | ITA |
| 白銀 | SLV | 醫療 | XLV |
| 必需消費 | XLP | 工業 | XLI |

### 大盤儀表（gauges）

| 組別 | Tickers |
|------|---------|
| 大盤 | SPY · QQQ · IWM |
| 避險 | TLT · GLD · UUP |
| 風險胃納 | HYG（高收益債，漲 = 市場敢冒險） |
| 波動率 | VIX（Finnhub 若回 0 自動換 VIXY） |

---

## 快速開始

### 1. Clone & 安裝

```bash
git clone https://github.com/YOUR_USERNAME/fin_news_daily.git
cd fin_news_daily
pip install -r requirements.txt
```

### 2. 取得 API Keys

| 服務 | 註冊 | 費用 |
|------|------|------|
| Finnhub | [finnhub.io](https://finnhub.io/register) | 免費 (60 req/min) |
| OpenRouter | [openrouter.ai](https://openrouter.ai/) | ~$0.005/次 (DeepSeek) |
| Telegram Bot | [@BotFather](https://t.me/BotFather) | 免費 |

**取得 Telegram Chat ID：**
1. 對你的 bot 傳一則訊息
2. 瀏覽 `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. 找 `result[0].message.chat.id`

### 3. 設定 `.env`

```bash
cp .env.example .env
```

```env
FINNHUB_API_KEY=your_key
OPENROUTER_API_KEY=your_key
TELEGRAM_BOT_TOKEN=your_token
TELEGRAM_CHAT_ID=your_chat_id
MOCK=0
```

> `.env` 已在 `.gitignore`，不會被 commit。

### 4. 測試執行（不消耗 API 額度）

```bash
python -m src.main --mock
```

輸出印到 console，archive 寫入 `archive/`，不發 Telegram。

### 5. 正式執行

```bash
python -m src.main
```

---

## GitHub Actions 自動排程

`.github/workflows/daily.yml` 每日 `0 0 * * *` UTC（= 08:00 Perth）執行。

**在 repo 設定 4 個 Secrets：**  
Settings → Secrets and variables → Actions

```
FINNHUB_API_KEY
OPENROUTER_API_KEY
TELEGRAM_BOT_TOKEN
TELEGRAM_CHAT_ID
```

workflow 執行後會自動 commit `archive/YYYY-MM-DD.md` 回 repo。  
手動觸發：Actions → Daily Market Digest → Run workflow。

---

## 自訂配置

主要設定在 `config/settings.yml`：

```yaml
# 換模型（任何 OpenRouter 支援的）
model:
  openrouter_model: "deepseek/deepseek-chat:online"

# 調整板塊 ETF
scoreboard_etfs:
  - {label: "半導體/AI晶片", etf: SMH}
  - {label: "AI記憶體/DRAM", etf: DRAM}
  # ...

# 價格徽章用的個股（出現在新聞標題旁）
anchor_tickers: [NVDA, AMD, TSLA, MSFT, GOOGL, AMZN, META, AAPL, AVGO, MU, ANET, PLTR, LMT, RTX, VIXY]

# 排名權重
rank_weights:
  coverage:  0.35   # 多少媒體報導
  recency:   0.25   # 越新越高
  sentiment: 0.20   # 情緒強度
  move:      0.20   # 股價波動

# 每日最多幾條新聞
selection:
  max_items_total: 12
```

---

## 專案結構

```
fin_news_daily/
├── .github/workflows/daily.yml   # 每日自動排程
├── config/settings.yml           # 板塊 ETF、模型、權重設定
├── src/
│   ├── main.py                   # 流水線總控
│   ├── models.py                 # NewsItem dataclass
│   ├── config.py                 # settings + secrets 載入
│   ├── fetch_finnhub.py          # Finnhub 新聞 / 報價
│   ├── fetch_indicators.py       # CNN Fear & Greed
│   ├── normalize.py              # 原始 API → NewsItem
│   ├── dedupe.py                 # URL 正規化 + 模糊標題去重
│   ├── tag.py                    # 地區 / 主題標籤
│   ├── rank.py                   # 評分 + 板塊計分板
│   ├── summarize.py              # DeepSeek 敘事生成
│   ├── store.py                  # Markdown 歸檔
│   └── notify.py                 # Telegram 推送
├── tests/fixtures/               # mock 模式用的 API 回傳樣本
├── archive/                      # 每日摘要（由 Action 自動 commit）
├── .env.example
└── requirements.txt
```

---

## API 額度說明

| 服務 | 免費限制 | 本專案用量 |
|------|---------|----------|
| Finnhub | 60 req/min | ~40 個 ticker 報價 + 1 條市場新聞，< 2 分鐘 |
| OpenRouter | 按 token 計費 | ~$0.005/次（DeepSeek，12 條新聞） |
| CNN Fear & Greed | 非官方 keyless | 1 req/次，若掛掉 VIX 仍可撐起情緒面 |
| Telegram | ~30 msg/sec | 每次 3–5 則訊息，遠低於限制 |

---

> **免責聲明：** 本工具為資訊彙整用途，非投資建議。所有內容來自公開 API 並由 AI 摘要。請勿僅憑自動化摘要做交易決策。
