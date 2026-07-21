from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_SYSTEM_PROMPT = """你是一位「市場說書人」,每天美股收盤後,用最白話的繁體中文,向一位幾乎沒有金融背景的「投資新手」,講清楚「今天市場發生了什麼故事」。每個專有名詞第一次出現,都用一句話解釋。

【鐵則】
- 只用收到的數字與搜到的當天新聞,絕不杜撰任何價格、百分比或事件。
- 不要說某新聞「導致」某漲跌,只說「X 今天 +N%」「同一天有 Y 消息」,讓讀者自己連結。
- 情緒指標只是「氛圍參考」,不是事實也不是預測。
- 全程像跟朋友解釋:白話、有畫面,但不浮誇、不喊單。
- 結尾固定加一行:「以上為市場資訊與情緒解讀,非投資建議,個股決策請自行判斷。」
- 輸出純文字,不要 # * ** 等 markdown 符號。"""

_USER_TEMPLATE = """【你會收到的技術面資料(當天收盤定盤數據,直接採用,不可更改任何數字)】
日期:{date}

大盤與資金流向(各標的當日%):
{gauges_text}
說明:SPY=標普500、QQQ=納斯達克科技、IWM=小型股、TLT=美國長債、GLD=黃金、HYG=高收益債(漲=市場敢冒險)、UUP=美元

板塊 ETF 計分板(已按當日漲跌由高到低排序):
{scoreboard_text}

市場情緒指標:
{indicators_text}
說明:VIX=恐慌指數(越高越怕,VIX 下跌代表恐慌降溫)、貪婪指數0–100(越低越恐慌)

【本地新聞 Feed（今日 Finnhub 抓取，供參考）】
{news_feed_text}
說明:以上為今日即時新聞源標題。若與你搜到的消息重疊或互補,可在敘事中引用;若無關聯可忽略,以你搜到的當天消息為主。

【你要主動做的事(敘事面)】
用網路搜尋,只找「{date} 當天、過去24小時內」的美股重大消息:財報、大會發布、政策/關稅、聯準會、地緣政治、龍頭公司動態。
嚴格只用當天消息。每條新聞標明發佈時間,超過24小時的丟棄,也不可當成今天的事。每條標媒體來源。

【輸出格式(全程繁體中文,純文字,不要 # * ** 等 markdown 符號)】

一、今天一句話
　像新聞標題一樣,一句話講完今天的故事。

二、市場氣氛(情緒面,白話)
　- 今天偏向 risk-on 還是偏向 risk-off (避險)?從 SPY/QQQ 對比 TLT/GLD 的方向判斷,並解釋給新手聽。
　- 用 VIX 和貪婪指數,各一句白話說明現在是貪婪還是恐懼(例:VIX 18 偏低=市場頗淡定)。

三、哪些板塊領升、哪些領跌(技術面)
　- 列當天漲最多的2–3個、跌最多的2–3個板塊,各用 ETF 的%數字。
　- 每個配一句「為什麼」:用你搜到的當天新聞當證據,把「板塊在動」和「發生了什麼」連起來。若找不到對應消息,就老實說「今日無明顯消息面,可能只是隨大盤波動」。

四、錢在往哪裡流
　- 綜合大盤 gauges,白話講今天資金流向:流入科技成長?流向防禦(公用/必需消費/醫療)?還是逃去債券黃金避險?也點出小型股(IWM)相對大盤偏強或偏弱(代表市場敢不敢買風險)。

五、今天的「敘事」是什麼
　- 用2–3條,講市場現在在炒什麼主題(例:AI 算力瓶頸、降息預期、AI 電力)。每條結合上面的板塊漲跌+新聞,並說明它在「升溫」還是「降溫」。

六、值得留意的異動(機會與風險,務必謹慎)
　- 漲跌幅在 ±1% 以內的板塊視為「持平」,此節不必提及。
　- 指出今天跌最兇或明顯被壓制的板塊。提醒新手:急跌有時是超賣機會、有時是基本面轉壞的警訊,不能只因為跌就買。

七、明天怎麼驗證
　- 給「一個」新手明天能自己看的具體指標或事件(例:看某 ETF 明天是否守住今天低點、某經濟數據幾點公布),用來驗證今天的故事還在不在。"""


def _fmt_news_feed(items: list) -> str:
    if not items:
        return "(無本地新聞)"
    return "\n".join(f"- {item.title}  [{item.source}]" for item in items)


def _fmt_gauges(gauges: dict) -> str:
    """Four grouped lines: 大盤 / 避險 / 風險胃納 / VIX.
    HYG is risk-ON (junk bonds) — kept separate so LLM doesn't conflate with safe-haven group."""
    lines = []
    broad = [(t, gauges[t]) for t in ["SPY", "QQQ", "IWM"] if t in gauges]
    if broad:
        lines.append("大盤: " + "  ".join(f"{t} {g.get('pct_change', 0):+.1f}%" for t, g in broad))
    safe = [(t, gauges[t]) for t in ["TLT", "GLD", "UUP"] if t in gauges]
    if safe:
        lines.append("避險: " + "  ".join(f"{t} {g.get('pct_change', 0):+.1f}%" for t, g in safe))
    if "HYG" in gauges:
        hyg = gauges["HYG"]
        lines.append(f"風險胃納: HYG {hyg.get('pct_change', 0):+.1f}% (高收益債,漲=市場敢冒險)")
    if "VIX" in gauges:
        vix = gauges["VIX"]
        vix_pct = vix.get("pct_change", 0)
        direction = "恐慌降溫" if vix_pct < 0 else "恐慌升溫"
        lines.append(f"VIX: {vix.get('current', 'N/A')} ({vix_pct:+.1f}% → {direction})")
    return "\n".join(lines) or "N/A"


def _fmt_scoreboard(scoreboard: list[dict]) -> str:
    return "\n".join(
        f"{e['label']} ({e['etf']}) {e['pct_change']:+.1f}%" for e in scoreboard
    ) or "N/A"


def _fmt_indicators(fear_greed: dict) -> str:
    if fear_greed.get("score") is not None:
        return f"貪婪指數 {fear_greed['score']} ({fear_greed.get('rating', 'N/A')})"
    return "N/A"


def summarize_digest(
    date_str: str,
    gauges: dict,
    scoreboard: list[dict],
    indicators: dict,
    items: list,
    model: str,
    api_key: str,
    client: httpx.Client,
) -> str:
    """Send structured market data to OpenRouter (with web search) and return plain-text narrative."""
    gauges_text = _fmt_gauges(gauges)
    scoreboard_text = _fmt_scoreboard(scoreboard)
    indicators_text = _fmt_indicators(indicators.get("fear_greed", {}))
    news_feed_text = _fmt_news_feed(items)
    user_message = _USER_TEMPLATE.format(
        date=date_str,
        gauges_text=gauges_text,
        scoreboard_text=scoreboard_text,
        indicators_text=indicators_text,
        news_feed_text=news_feed_text,
    )

    headers = {
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": "https://github.com/fin_news_daily",
        "X-Title": "fin-news-daily",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.4,
        "max_tokens": 4000,
    }

    logger.info("OpenRouter: sending market data for %s to model=%s", date_str, model)
    try:
        response = client.post(_OPENROUTER_URL, headers=headers, json=body, timeout=90.0)
        response.raise_for_status()
        data = response.json()
    except httpx.HTTPStatusError as e:
        raise RuntimeError(f"OpenRouter HTTP {e.response.status_code}: {e.response.text[:300]}") from e

    if "error" in data:
        raise RuntimeError(f"OpenRouter API error: {data['error']}")

    try:
        narrative = data["choices"][0]["message"]["content"].strip()
    except (KeyError, IndexError) as e:
        raise RuntimeError(f"Unexpected OpenRouter response shape: {data}") from e

    logger.info("OpenRouter: received %d chars", len(narrative))
    return narrative


def summarize_mock(
    date_str: str,
    gauges: dict | None = None,
    scoreboard: list[dict] | None = None,
    indicators: dict | None = None,
    items: list | None = None,
) -> str:
    """Return a mock narrative string without calling any API."""
    fg_score = (indicators or {}).get("fear_greed", {}).get("score", "N/A")
    board_summary = ", ".join(
        f"{e['label']} {e['pct_change']:+.1f}%" for e in (scoreboard or [])[:3]
    ) or "N/A"
    return (
        f"[MOCK 市場摘要] {date_str}\n\n"
        f"一、今天一句話\n　[MOCK] 科技股領漲,市場情緒偏樂觀。\n\n"
        f"二、市場氣氛\n　[MOCK] 貪婪指數 {fg_score},屬於貪婪區間。\n\n"
        f"三、板塊表現\n　[MOCK] 領漲板塊: {board_summary}\n\n"
        f"以上為市場資訊與情緒解讀,非投資建議,個股決策請自行判斷。"
    )
