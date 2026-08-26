from __future__ import annotations

import logging

import httpx

from src import render

logger = logging.getLogger(__name__)

_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

_SYSTEM_PROMPT = """你是一位「市場說書人」,每天美股收盤後,用最白話的繁體中文,向一位幾乎沒有金融背景的「投資新手」,講清楚「今天市場發生了什麼故事」。每個專有名詞第一次出現,都用一句話解釋。

【鐵則:數字】
- 只用收到的數字與搜到的當天新聞,絕不杜撰任何價格、百分比或事件。
- 程式已經幫你算好價格表與板塊表,你直接引用,不要自己重算、也不要憑印象改寫任何數字。
- 不要說某新聞「導致」某漲跌,只說「X 今天 +N%」「同一天有 Y 消息」,讓讀者自己連結。
- 情緒指標只是「氛圍參考」,不是事實也不是預測。

【鐵則:觀點】
- 事實與觀點必須分開。事實照抄數據與新聞;觀點一律寫在「我的解讀」裡,並明講這是主觀判斷。
- 每一個「我的解讀」都要附一句「如果我看錯了,會先看到什麼訊號」,讓讀者能反過來檢驗你。
- 不要給買賣建議,不要喊單,不要講目標價,不要浮誇。

【鐵則:標記】
每一條重大事件開頭用一個標記,幫讀者一眼判斷方向:
🔴 = 明顯利空 / 重大衝擊
🟢 = 明顯利多 / 結構性好消息
🟡 = 方向不明 / 各方說法互相矛盾
⚠️ = 風險警告 / 需要持續追蹤

【鐵則:格式】
- 全程繁體中文,純文字。不要 # * ** ` 等 markdown 符號,不要 markdown 表格。
- 全程像跟朋友解釋:白話、有畫面。
- 結尾固定加一行:「以上為市場資訊與情緒解讀,非投資建議,個股決策請自行判斷。」"""

_USER_TEMPLATE = """【你會收到的技術面資料(當天收盤定盤數據,直接採用,不可更改任何數字)】
日期:{date} {date_cn}

大盤與資金流向(各標的當日%):
{gauges_text}
說明:SPY=標普500、QQQ=納斯達克科技、IWM=小型股、TLT=美國長債、GLD=黃金、HYG=高收益債(漲=市場敢冒險)、UUP=美元

個股快照(程式已填好價格,你不要重打這張表,只寫要點):
{snapshot_text}
分組意義:算力核心=大型高beta,漲跌本身就有訊息量;二階供應鏈=算力的上游水電工;雲端錨=低beta,但它們的資本支出決定前兩組的訂單;其他高beta=非AI線。

板塊輪動(程式已算好各大類的當日平均漲跌,由高到低。這是判斷資金流向的主要依據):
{rotation_text}

板塊 ETF 計分板(已按當日漲跌由高到低排序):
{scoreboard_text}

市場情緒指標:
{indicators_text}
說明:VIX=恐慌指數(越高越怕,VIX 下跌代表恐慌降溫)、貪婪指數0–100(越低越恐慌)

未來一週財報行事曆(只列你追蹤的標的,程式從 Finnhub 取得):
{earnings_text}

未來一週總體經濟數據(已過濾掉庫存與公債標售等雜訊,只留會動盤的):
{econ_text}

加密現貨(CoinGecko,24h 滾動漲跌 — 注意這跟股票的「當日收盤對前收」不是同一種數字,不可混講):
{crypto_text}

【今日新聞 Feed — 這是本篇的主要素材,不是參考資料】
以下是今天從 Finnhub 與各家媒體 RSS(CNBC / CoinDesk / The Defiant / CoinTelegraph / The Block)抓到的新聞,
已用事件分數排序:分數高代表標題在講「發生了一件事」,低代表只是在描述盤面。
格式:[事件分數] 標題 — 來源 · 市場

{news_feed_text}

【上一份日報說了什麼(敘事連續性)】
{prev_context_text}

【你要主動做的事】
1. 先讀完上面的新聞 Feed,那是你的主要素材。從事件分數高的開始看。
2. 再用網路搜尋補充細節:上面哪一條最重要?去查它的具體內容 —— 誰、做了什麼、金額/數量、對方是誰、市場反應。
   例:看到「某公司與某公司簽約」,就去查金額多少、期限多久、佔營收多少。細節才是價值,標題本身沒有價值。
3. 也可搜尋 Feed 沒抓到的當天重大消息。

嚴格只用「{date} 當天」或過去24小時內的消息。每條標明發佈時間與媒體來源,超過24小時的丟棄,不可當成今天的事。

【來源品質要求】
- 優先用一手或權威來源:公司公告、SEC 文件、CNBC、Reuters、Bloomberg、CoinDesk、The Block、The Defiant、官方新聞稿。
- 不要引用內容農場、個人部落格、聚合站的二手轉載(例:各種 xxx.tw / xxx.cn 的行情轉貼站)。找不到好來源就不要寫那一條。
- 引用時寫明媒體名。同一件事有多家報導就說「多家報導」。

【輸出格式(全程繁體中文,純文字,不要 # * ** 等 markdown 符號)】

一、今天一句話
　像新聞標題一樣,一句話講完今天的故事。

二、今日最大事件(宏觀/美股)
　- 挑 3–5 條當天最重要的消息,每條這樣寫:
　　第一行:標記emoji + 事件標題(一行講完,要有主詞和動作)
　　第二行:發生了什麼 —— 必須包含具體細節:誰、對誰、金額或數量、期限、佔比。加媒體來源與發佈時間。
　　第三行:「所以呢:」一句白話說明這對一般投資人代表什麼
　- 排序照重要性,不是照時間。
　- 「某股跌了 5%」不算事件,那是盤面,屬於第四節。這一節只放「發生了一件事」:
　　財報、併購、人事異動、監管/法案、大額合約、產品發表、分析師大幅調整、政策轉向。
　- 找不到夠格的大事就少寫幾條,寧可寫 2 條紮實的,不要湊 5 條空的。

三、加密貨幣與數位資產
　- 這是獨立的一節,不是美股的附屬品。分兩塊寫:
　　(a) 幣價:用上面的加密現貨表,講 BTC/ETH 的位置與 24h 方向,以及主流幣之間有沒有分歧。
　　(b) 產業事件:從 Feed 的加密新聞裡挑 2–4 條最重要的,格式同第二節(標記/細節+來源/所以呢)。
　　　重點放在結構性的事:監管與法案進度、機構進場、穩定幣、代幣化、交易所與託管、上市公司財報。
　- 特別注意「加密股 vs 幣價」的背離:BTC 平盤但 CRCL/COIN/GLXY 大漲,代表市場在買監管或基礎設施,不是在買幣價。
　　個股快照的「加密相關」那一組就是給這個用的。
　- 若今天加密沒有值得講的事,就寫「今天加密市場沒有結構性新聞,只有價格波動」,不要用幣價變動硬撐一節。

四、個股快照要點
　- 上面那張價格表已經印出來了,不要重印。
　- 從表裡挑 3–5 檔「今天最值得講」的(漲跌最大、或當天有消息的),每檔一行:
　　代號 漲跌% ｜ 一句話要點(有消息就寫消息+來源;沒消息就老實說「今日無明顯消息面,可能只是隨大盤波動」)。
　- 挑選時優先看「跨組背離」,那比單一檔漲跌更有訊息量,例如:
　　二階供應鏈大漲但算力核心沒動、雲端錨走弱但供應鏈仍強、同組裡有一檔明顯脫隊。
　- 若當天出現這種背離,額外用一句話點出來;若整張表都同方向,就直說「今天整條 AI 鏈同步,沒有背離」。

五、板塊與資金流向
　- 列當天漲最多的2–3個、跌最多的2–3個板塊,各用 ETF 的%數字。每個配一句「為什麼」,用當天新聞當證據;
　　找不到對應消息就老實說「今日無明顯消息面」。漲跌幅在 ±1% 以內的視為持平,不必解釋。
　- 先講輪動:看上面的「板塊輪動」表,直接指出今天錢從哪一大類流到哪一大類(例:AI鏈 -1.2% 但傳統景氣 +1.8%,錢從科技換到金融)。
　　用最高和最低那兩類的實際數字。若各大類差距都在 0.5% 以內,就直說「今天沒有明顯輪動,大家一起漲/跌」,不要硬掰故事。
　- 特別留意這兩種組合,出現時要點名:
　　AI鏈弱但傳統景氣強 → 資金離開成長股;商品/抗通膨強且 TLT 弱 → 市場在擔心通膨或美元,而不是在追成長。
　- 今天偏向 risk-on 還是 risk-off(避險)?從 SPY/QQQ 對比 TLT/GLD 的方向判斷,並解釋給新手聽。
　- 用 VIX 和貪婪指數,各一句白話說明現在是貪婪還是恐慌(例:VIX 18 偏低=市場頗淡定)。
　- 點出小型股(IWM)相對大盤偏強或偏弱(代表市場敢不敢買風險)。

六、今天的敘事是什麼
　- 用 2–3 條,講市場現在在炒什麼主題(例:AI 算力瓶頸、降息預期、AI 電力)。
　- 每條結合上面的板塊漲跌+新聞,並說明它在「升溫」還是「降溫」。

七、我的解讀
　- 2–3 段,明確標為主觀判斷。這一節是全篇唯一可以下判斷的地方。
　- 每段的寫法:我覺得今天真正重要的是 X,因為 Y;但反方的說法是 Z。
　- 每段結尾一句:「如果我看錯了,會先看到:____」(給一個具體、明天就能觀察到的訊號)。
　- 若今天資訊不足以形成看法,就直說「今天沒有值得下判斷的東西」,不要硬掰。

八、值得留意的異動(機會與風險,務必謹慎)
　- 指出今天跌最兇或明顯被壓制的板塊。
　- 提醒新手:急跌有時是超賣機會、有時是基本面轉壞的警訊,不能只因為跌就買。

九、昨天的帳 & 明天怎麼驗證
　- 先結算:上一份日報的「明天怎麼驗證」那個指標,今天結果如何?兌現了、落空了,還是還沒揭曉?一句話講清楚,錯了就大方承認。(若上面沒有提供上一份日報,這段寫「今天沒有可對照的前一份日報」。)
　- 再下注:給「一個」新手明天能自己看的具體指標或事件,用來驗證今天的故事還在不在。
　　優先順序:
　　(1) 總經數據行事曆裡明天有 ★★★ 的 → 寫出名稱、幾點(UTC)公佈、市場預估多少。
　　(2) 財報行事曆裡明天(或今天盤後)有你追蹤的標的 → 寫出代號、盤前/盤後、預估 EPS 與營收。
　　(3) 兩張表都空的,才改用技術面指標(例:看某 ETF 明天是否守住今天低點)。
　　盡量寫出「預估值」,這樣隔天才有辦法對答案。
　　但若行事曆那一欄是「—」,代表市場預估尚未公布(通常提前約一週才有),
　　這時就照實寫「市場預估尚未公布」,並改用「公布後看它相對前值是升是降」當驗證方式。
　　絕對不要因為表上沒有預估值就自己編一個數字。

十、今日總結
　- 2–3 句,把「最大事件 + 今天的敘事 + 明天的驗證點」串成一段收尾,不要重複第一節的原句。"""


_MARKET_CN = {"crypto": "加密", "equity": "美股", "macro": "宏觀"}


def _fmt_news_feed(items: list) -> str:
    """Group by market, tag each line with its event score, and include the summary snippet.

    The old format was title + source only, which gave the model nothing to write detail
    from — it had to go searching, and settled for whatever aggregator ranked first.
    """
    if not items:
        return "(無新聞)"

    by_market: dict[str, list] = {}
    for item in items:
        by_market.setdefault(item.market, []).append(item)

    blocks: list[str] = []
    for market in ("equity", "crypto", "macro"):
        market_items = by_market.get(market)
        if not market_items:
            continue
        blocks.append(f"── {_MARKET_CN.get(market, market)} ──")
        for item in market_items:
            blocks.append(
                f"[{item.event_score:.2f}] {item.title} — {item.source}"
            )
            snippet = " ".join(item.summary.split())[:220]
            if snippet:
                blocks.append(f"        {snippet}")
        blocks.append("")
    return "\n".join(blocks).strip()


def _fmt_crypto(rows: list[dict]) -> str:
    return render.crypto_plain(rows or []) or "N/A (今日無加密報價)"


def _fmt_earnings(rows: list[dict]) -> str:
    return render.earnings_plain(rows or []) or "(未來一週你追蹤的標的沒有財報)"


def _fmt_econ(rows: list[dict]) -> str:
    return render.econ_plain(rows or []) or "(沒有總經行事曆資料 — 本地 publisher 可能沒跑)"


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


def _fmt_snapshot(snapshot_rows: list[dict]) -> str:
    return render.snapshot_plain(snapshot_rows or []) or "N/A"


def _fmt_rotation(rotation: list[dict]) -> str:
    return render.rotation_plain(rotation or []) or "N/A"


def _fmt_indicators(fear_greed: dict) -> str:
    if fear_greed.get("score") is not None:
        return f"貪婪指數 {fear_greed['score']} ({fear_greed.get('rating', 'N/A')})"
    return "N/A"


def build_user_message(
    date_str: str,
    date_cn: str,
    gauges: dict,
    scoreboard: list[dict],
    indicators: dict,
    items: list,
    snapshot_rows: list[dict] | None = None,
    prev_context: str = "",
    rotation: list[dict] | None = None,
    crypto_prices: list[dict] | None = None,
    earnings: list[dict] | None = None,
    econ_events: list[dict] | None = None,
) -> str:
    """Assemble the prompt — separated from the HTTP call so it can be inspected in mock mode."""
    return _USER_TEMPLATE.format(
        date=date_str,
        date_cn=date_cn,
        gauges_text=_fmt_gauges(gauges),
        crypto_text=_fmt_crypto(crypto_prices),
        earnings_text=_fmt_earnings(earnings),
        econ_text=_fmt_econ(econ_events),
        rotation_text=_fmt_rotation(rotation),
        scoreboard_text=_fmt_scoreboard(scoreboard),
        snapshot_text=_fmt_snapshot(snapshot_rows),
        indicators_text=_fmt_indicators(indicators.get("fear_greed", {})),
        news_feed_text=_fmt_news_feed(items),
        prev_context_text=prev_context.strip() or "(沒有可對照的前一份日報)",
    )


def summarize_digest(
    date_str: str,
    date_cn: str,
    gauges: dict,
    scoreboard: list[dict],
    indicators: dict,
    items: list,
    model: str,
    api_key: str,
    client: httpx.Client,
    snapshot_rows: list[dict] | None = None,
    prev_context: str = "",
    rotation: list[dict] | None = None,
    crypto_prices: list[dict] | None = None,
    earnings: list[dict] | None = None,
    econ_events: list[dict] | None = None,
) -> str:
    """Send structured market data to OpenRouter (with web search) and return plain-text narrative."""
    user_message = build_user_message(
        date_str, date_cn, gauges, scoreboard, indicators, items,
        snapshot_rows, prev_context, rotation, crypto_prices, earnings, econ_events,
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
        "max_tokens": 6000,
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
    snapshot_rows: list[dict] | None = None,
    prev_context: str = "",
    rotation: list[dict] | None = None,
    crypto_prices: list[dict] | None = None,
    earnings: list[dict] | None = None,
    econ_events: list[dict] | None = None,
) -> str:
    """Return a mock narrative in the live output shape, without calling any API."""
    fg_score = (indicators or {}).get("fear_greed", {}).get("score", "N/A")
    board = scoreboard or []
    leaders, laggards = render.scoreboard_extremes(board)
    up = ", ".join(f"{e['label']} {e['pct_change']:+.1f}%" for e in leaders) or "無"
    down = ", ".join(f"{e['label']} {e['pct_change']:+.1f}%" for e in laggards) or "無"
    movers = sorted(snapshot_rows or [], key=lambda r: abs(r["pct_change"]), reverse=True)[:3]
    mover_lines = "\n".join(
        f"　{r['ticker']} {r['pct_change']:+.1f}% ｜ [MOCK] 今日無明顯消息面。" for r in movers
    ) or "　[MOCK] 無個股快照資料。"
    prev_line = "[MOCK] 已讀入上一份日報。" if prev_context else "今天沒有可對照的前一份日報。"
    rot_line = " → ".join(
        f"{b['bucket']} {b['avg_pct']:+.1f}%" for b in (rotation or [])
    ) or "無輪動資料"
    earn_line = "  ".join(
        f"{r['symbol']}({r['date']} {r['hour']})" for r in (earnings or [])[:3]
    ) or "未來一週無追蹤標的財報"
    crypto_line = "  ".join(
        f"{r['symbol']} ${r['usd']:,.0f} {r['pct_24h']:+.1f}%" for r in (crypto_prices or [])
    ) or "無加密報價"
    return (
        f"[MOCK 市場摘要] {date_str}\n\n"
        f"一、今天一句話\n　[MOCK] 科技股領漲,市場情緒偏樂觀。\n\n"
        f"二、今日最大事件\n　🟢 [MOCK] 事件標題\n　　[MOCK] 事件內容(來源)\n　　所以呢:[MOCK] 對新手的意義。\n\n"
        f"三、加密貨幣與數位資產\n　[MOCK] {crypto_line}\n\n"
        f"四、個股快照要點\n{mover_lines}\n\n"
        f"五、板塊與資金流向\n　領漲: {up}\n　領跌: {down}\n"
        f"　[MOCK] 輪動: {rot_line}\n　[MOCK] 貪婪指數 {fg_score}。\n\n"
        f"六、今天的敘事是什麼\n　[MOCK] AI 算力主題升溫。\n\n"
        f"七、我的解讀\n　[MOCK] 主觀判斷占位。如果我看錯了,會先看到:[MOCK] 訊號。\n\n"
        f"八、值得留意的異動(機會與風險,務必謹慎)\n　[MOCK] 佔位。\n\n"
        f"九、昨天的帳 & 明天怎麼驗證\n　{prev_line}\n　[MOCK] 財報行事曆: {earn_line}\n\n"
        f"十、今日總結\n　[MOCK] 收尾。\n\n"
        f"以上為市場資訊與情緒解讀,非投資建議,個股決策請自行判斷。"
    )
