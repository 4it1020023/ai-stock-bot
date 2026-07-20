"""
AI 股票虛擬貨幣分析機器人 - 完整版
====================================
功能:
  ✅ 繁體中文介面
  ✅ LINE Webhook 互動（輸入股票/幣種代號即可查詢）
  ✅ K 線圖生成並傳送到 LINE
  ✅ 暴漲暴跌偵測
  ✅ 黃金、白銀監控
  ✅ K 線教學指令
  ✅ 每 30 秒自動掃描加密貨幣、每 5 分鐘掃描股票
  ✅ 有重要訊號才推播，不洗版
  ✅ 部署到 Render 雲端
"""

import os, io, hmac, hashlib, base64, json, logging
import requests
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib import rcParams
from flask import Flask, request, abort, send_file
from datetime import datetime
from apscheduler.schedulers.background import BackgroundScheduler
from ta.momentum import RSIIndicator
from ta.trend import MACD, SMAIndicator
from ta.volatility import BollingerBands

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

# ──────────────────────────────────────────
# ★ 設定區（本機測試直接填；雲端用環境變數）
# ──────────────────────────────────────────
LINE_TOKEN   = os.environ.get("LINE_TOKEN",   "CENMQLiRTPVhogKccfDOrfzLAmMFAahsBgjw7vC4/uehaXo7838lmVagwV9U7+sl9a3uSA+FhLGW81+z9qWDUR0nL7TtGXgc6s7ZiDpxRXG71985Zudhv/T+FhAmm5trQHHjrfGLoQmsBh+OaMz7GgdB04t89/1O/w1cDnyilFU=")
LINE_SECRET  = os.environ.get("LINE_SECRET",  "e67d7e422929119a021b82187a93fec5")
LINE_USER_ID = os.environ.get("LINE_USER_ID", "Ue15c9c822c38815932c988e59f3abc6d")
BASE_URL     = os.environ.get("BASE_URL",     "https://ai-stock-bot-q20c.onrender.com")  # 部署後填入

CHART_DIR = "/tmp/charts"
os.makedirs(CHART_DIR, exist_ok=True)

# ── 預設監控清單 ──────────────────────────
DEFAULT_STOCKS = [
    "2330.TW", "2317.TW", "3008.TW", "2603.TW",  # 台股
    "NVDA", "TSM", "MSFT", "GOOGL", "AAPL",        # 美股
    "GC=F", "SI=F",                                 # 黃金、白銀
]
DEFAULT_CRYPTOS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT",
    "XRPUSDT", "DOGEUSDT", "ADAUSDT", "AVAXUSDT",
]

# 名稱對照表
NAME_MAP = {
    "2330.TW":"台積電", "2317.TW":"鴻海", "3008.TW":"大立光", "2603.TW":"長榮",
    "NVDA":"輝達", "TSM":"台積電ADR", "MSFT":"微軟", "GOOGL":"谷歌", "AAPL":"蘋果",
    "GC=F":"黃金", "SI=F":"白銀",
    "BTCUSDT":"比特幣", "ETHUSDT":"以太幣", "BNBUSDT":"幣安幣",
    "SOLUSDT":"Solana", "XRPUSDT":"XRP", "DOGEUSDT":"狗狗幣",
    "ADAUSDT":"Cardano", "AVAXUSDT":"Avalanche",
}

# 暴漲暴跌門檻（%）
SPIKE_THRESHOLD = 5.0   # 單根 K 棒漲跌超過 5% 視為暴漲/暴跌
RSI_OVERBOUGHT  = 75
RSI_OVERSOLD    = 25


# ══════════════════════════════════════════
#  LINE 推播 / 傳圖
# ══════════════════════════════════════════

def _line_headers():
    return {"Content-Type": "application/json",
            "Authorization": f"Bearer {LINE_TOKEN}"}

def push_text(text: str, user_id: str = None):
    uid = user_id or LINE_USER_ID
    body = {"to": uid, "messages": [{"type": "text", "text": text}]}
    r = requests.post("https://api.line.me/v2/bot/message/push",
                      headers=_line_headers(), json=body, timeout=10)
    log.info(f"[LINE文字] {r.status_code}")

def reply_text(reply_token: str, text: str):
    body = {"replyToken": reply_token,
            "messages": [{"type": "text", "text": text}]}
    r = requests.post("https://api.line.me/v2/bot/message/reply",
                      headers=_line_headers(), json=body, timeout=10)
    log.info(f"[LINE回覆] {r.status_code}")

def push_image(image_path: str, user_id: str = None):
    """上傳圖片到 LINE（需要 BASE_URL 可公開存取）"""
    uid = user_id or LINE_USER_ID
    filename = os.path.basename(image_path)
    img_url  = f"{BASE_URL}/charts/{filename}"
    body = {
        "to": uid,
        "messages": [{
            "type": "image",
            "originalContentUrl": img_url,
            "previewImageUrl":    img_url,
        }]
    }
    r = requests.post("https://api.line.me/v2/bot/message/push",
                      headers=_line_headers(), json=body, timeout=10)
    log.info(f"[LINE圖片] {r.status_code}")

def reply_image(reply_token: str, image_path: str):
    filename = os.path.basename(image_path)
    img_url  = f"{BASE_URL}/charts/{filename}"
    body = {
        "replyToken": reply_token,
        "messages": [{
            "type": "image",
            "originalContentUrl": img_url,
            "previewImageUrl":    img_url,
        }]
    }
    r = requests.post("https://api.line.me/v2/bot/message/reply",
                      headers=_line_headers(), json=body, timeout=10)
    log.info(f"[LINE回覆圖片] {r.status_code}")


# ══════════════════════════════════════════
#  資料抓取
# ══════════════════════════════════════════

def get_stock_df(symbol: str) -> pd.DataFrame:
    import yfinance as yf
    df = yf.download(symbol, period="3mo", interval="1d", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    return df.dropna()

def get_crypto_df(symbol: str, interval="1h", limit=200) -> pd.DataFrame:
    """改用 yfinance 抓加密貨幣（不被 Render 封鎖）"""
    import yfinance as yf
    # Binance 代號 -> Yahoo Finance 代號
    YF_MAP = {
        "BTCUSDT": "BTC-USD",  "ETHUSDT": "ETH-USD",
        "BNBUSDT": "BNB-USD",  "SOLUSDT": "SOL-USD",
        "XRPUSDT": "XRP-USD",  "DOGEUSDT": "DOGE-USD",
        "ADAUSDT": "ADA-USD",  "AVAXUSDT": "AVAX-USD",
    }
    yf_symbol = YF_MAP.get(symbol.upper(), symbol.replace("USDT", "-USD"))
    df = yf.download(yf_symbol, period="7d", interval="1h", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    if "adj close" in df.columns:
        df = df.drop(columns=["adj close"])
    return df.dropna().tail(limit)




# ══════════════════════════════════════════
#  技術指標
# ══════════════════════════════════════════

def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    c = df["close"]
    df["rsi"]         = RSIIndicator(close=c, window=14).rsi()
    m                 = MACD(close=c)
    df["macd"]        = m.macd()
    df["macd_signal"] = m.macd_signal()
    df["macd_hist"]   = m.macd_diff()
    df["ma20"]        = SMAIndicator(close=c, window=20).sma_indicator()
    df["ma60"]        = SMAIndicator(close=c, window=60).sma_indicator()
    bb                = BollingerBands(close=c, window=20, window_dev=2)
    df["bb_upper"]    = bb.bollinger_hband()
    df["bb_lower"]    = bb.bollinger_lband()
    df["bb_mid"]      = bb.bollinger_mavg()
    return df


# ══════════════════════════════════════════
#  訊號判斷
# ══════════════════════════════════════════

def analyze(df: pd.DataFrame) -> dict:
    latest = df.iloc[-1]
    prev   = df.iloc[-2]
    important, info = [], []

    # ── 暴漲/暴跌偵測 ──
    pct_change = (latest["close"] - latest["open"]) / latest["open"] * 100
    if pct_change >= SPIKE_THRESHOLD:
        important.append("暴漲 " + f"{pct_change:.1f}" + "%！注意風險")
    elif pct_change <= -SPIKE_THRESHOLD:
        important.append("暴跌 " + f"{pct_change:.1f}" + "%！注意風險")

    # ── RSI ──
    rsi = latest["rsi"]
    if rsi > RSI_OVERBOUGHT:
        important.append("RSI 超買（" + f"{rsi:.1f}" + "），留意拉回")
    elif rsi < RSI_OVERSOLD:
        important.append("RSI 超賣（" + f"{rsi:.1f}" + "），留意反彈")

    # ── MACD 交叉 ──
    if prev["macd"] < prev["macd_signal"] and latest["macd"] > latest["macd_signal"]:
        important.append("MACD 黃金交叉（偏多訊號）")
    elif prev["macd"] > prev["macd_signal"] and latest["macd"] < latest["macd_signal"]:
        important.append("MACD 死亡交叉（偏空訊號）")

    # ── 布林通道 ──
    if latest["close"] > latest["bb_upper"]:
        important.append("突破布林上軌，留意拉回")
    elif latest["close"] < latest["bb_lower"]:
        important.append("跌破布林下軌，留意反彈")

    # ── 均線 ──
    trend = "均線偏多（MA20 > MA60）" if latest["ma20"] > latest["ma60"] else "均線偏空（MA20 < MA60）"
    info.append(trend)

    return {
        "price":      round(float(latest["close"]), 4),
        "rsi":        round(float(rsi), 2),
        "pct_change": round(float(pct_change), 2),
        "important":  important,
        "info":       info,
        "has_alert":  len(important) > 0,
    }


# ══════════════════════════════════════════
#  K 線圖生成
# ══════════════════════════════════════════

def generate_chart(df: pd.DataFrame, symbol: str, n_candles: int = 60) -> str:
    import matplotlib
    matplotlib.rcParams["font.family"] = "DejaVu Sans"
    matplotlib.rcParams["axes.unicode_minus"] = False
    matplotlib.rcParams["font.sans-serif"] = ["DejaVu Sans"]

    df_plot = df.tail(n_candles)[["open","high","low","close","volume"]].copy()

    fig, axes = plt.subplots(3, 1, figsize=(12, 9),
                              gridspec_kw={"height_ratios": [4, 1.2, 1.2]},
                              facecolor="#1a1a2e")
    ax1, ax2, ax3 = axes
    for ax in axes:
        ax.set_facecolor("#1a1a2e")
        ax.tick_params(colors="#cccccc", labelsize=8)
        ax.spines[:].set_color("#333355")

    # ── K 線 ──
    idx = range(len(df_plot))
    for i, (_, row) in enumerate(df_plot.iterrows()):
        color = "#ef5350" if row["close"] >= row["open"] else "#26a69a"
        ax1.plot([i, i], [row["low"], row["high"]], color=color, lw=0.8)
        rect = mpatches.FancyBboxPatch(
            (i - 0.3, min(row["open"], row["close"])),
            0.6, abs(row["close"] - row["open"]) or 0.001,
            boxstyle="square,pad=0", color=color)
        ax1.add_patch(rect)

    # ── MA & 布林通道 ──
    ma20 = df_plot["close"].rolling(20).mean()
    ma60 = df_plot["close"].rolling(60).mean()
    bb_m = df_plot["close"].rolling(20).mean()
    bb_s = df_plot["close"].rolling(20).std()
    ax1.plot(idx, ma20.values,          color="#f59e0b", lw=1,   label="MA20")
    ax1.plot(idx, ma60.values,          color="#818cf8", lw=1,   label="MA60")
    ax1.plot(idx, (bb_m + 2*bb_s).values, color="#64748b", lw=0.8, ls="--", label="BB Upper")
    ax1.plot(idx, (bb_m - 2*bb_s).values, color="#64748b", lw=0.8, ls="--", label="BB Lower")
    ax1.legend(loc="upper left", fontsize=7, facecolor="#1a1a2e",
               labelcolor="#cccccc", framealpha=0.5)
    ax1.set_title(f"{symbol} - Candlestick Chart | MA20 MA60 BB RSI",
                  color="#e2e8f0", fontsize=11, pad=8)
    ax1.set_xlim(-1, len(df_plot))
    ax1.set_ylabel("Price", color="#cccccc", fontsize=8)

    # ── 成交量 ──
    vol_colors = ["#ef5350" if r["close"] >= r["open"] else "#26a69a"
                  for _, r in df_plot.iterrows()]
    ax2.bar(idx, df_plot["volume"].values, color=vol_colors, alpha=0.7)
    ax2.set_ylabel("Volume", color="#cccccc", fontsize=8)
    ax2.set_xlim(-1, len(df_plot))

    # ── RSI ──
    rsi_vals = RSIIndicator(close=df_plot["close"], window=14).rsi()
    ax3.plot(idx, rsi_vals.values, color="#a78bfa", lw=1.2)
    ax3.axhline(RSI_OVERBOUGHT, color="#ef5350", lw=0.8, ls="--")
    ax3.axhline(RSI_OVERSOLD,   color="#26a69a", lw=0.8, ls="--")
    ax3.axhline(50,             color="#64748b", lw=0.5, ls=":")
    ax3.fill_between(idx, rsi_vals.values, RSI_OVERBOUGHT,
                     where=[v > RSI_OVERBOUGHT for v in rsi_vals.values],
                     color="#ef5350", alpha=0.3)
    ax3.fill_between(idx, rsi_vals.values, RSI_OVERSOLD,
                     where=[v < RSI_OVERSOLD for v in rsi_vals.values],
                     color="#26a69a", alpha=0.3)
    ax3.set_ylabel("RSI", color="#cccccc", fontsize=8)
    ax3.set_ylim(0, 100)
    ax3.set_xlim(-1, len(df_plot))

    now_str = datetime.now().strftime("%Y/%m/%d %H:%M")
    fig.text(0.99, 0.01, f"Generated: {now_str}  |  For reference only - not investment advice",
             color="#555577", fontsize=7, ha="right")

    plt.tight_layout(pad=1.5)
    path = os.path.join(CHART_DIR, f"{symbol.replace('=','_')}.png")
    plt.savefig(path, dpi=130, bbox_inches="tight", facecolor="#1a1a2e")
    plt.close(fig)
    return path


# ══════════════════════════════════════════
#  訊息組合
# ══════════════════════════════════════════

def format_analysis(symbol: str, result: dict) -> str:
    name = NAME_MAP.get(symbol, symbol)
    now  = datetime.now().strftime("%Y/%m/%d %H:%M")
    lines = [
        "【" + name + "（" + symbol + "）】",
        "分析時間：" + now,
        "目前價格：" + str(result["price"]),
        "RSI 指標：" + str(result["rsi"]),
        "本根漲跌：" + f"{result['pct_change']:+.2f}" + "%",
        "─────────────",
    ]
    if result["important"]:
        lines.append("重要訊號：")
        lines += result["important"]
    else:
        lines.append("目前無重要訊號")
    lines += result["info"]
    lines.append("─────────────")
    lines.append("以上僅供參考，請自行評估風險")
    return "\n".join(lines)


# ══════════════════════════════════════════
#  K 線教學內容
# ══════════════════════════════════════════

KLINE_EDUCATION = """📚【K 線圖入門教學】

━━━━━ 什麼是 K 線？ ━━━━━
K 線（蠟燭圖）記錄一段時間內的
開盤、收盤、最高、最低價格。

🟥 紅 K（陽線）= 收盤 > 開盤（上漲）
🟦 綠/黑 K（陰線）= 收盤 < 開盤（下跌）

━━━━━ 常見指標說明 ━━━━━
📌 RSI（相對強弱指標）
  • > 75：超買，可能拉回
  • < 25：超賣，可能反彈
  • 50 附近：中性

📌 MACD（指數平滑異同移動平均）
  • 黃金交叉（MACD 向上穿越訊號線）
    → 偏多，可能上漲
  • 死亡交叉（MACD 向下穿越訊號線）
    → 偏空，可能下跌

📌 均線（MA20 / MA60）
  • MA20 > MA60 → 短期趨勢偏多
  • MA20 < MA60 → 短期趨勢偏空
  • 黃金交叉：短線突破長線（偏多）
  • 死亡交叉：短線跌破長線（偏空）

📌 布林通道
  • 上軌突破 → 強勢，但留意拉回
  • 下軌跌破 → 弱勢，但留意反彈
  • 收斂 → 盤整中，可能即將大漲/跌

━━━━━ 指令說明 ━━━━━
輸入股票代號查詢，例如：
  2330  →  台積電分析圖
  BTC   →  比特幣分析圖
  黃金  →  黃金分析圖
  教學  →  顯示此說明
  清單  →  顯示監控清單"""

WATCHLIST_MSG = """📋【目前監控清單】

🏦 台股
  2330.TW 台積電
  2317.TW 鴻海
  3008.TW 大立光
  2603.TW 長榮

🇺🇸 美股
  NVDA 輝達
  TSM  台積電ADR
  MSFT 微軟
  GOOGL 谷歌
  AAPL 蘋果

🥇 貴金屬
  GC=F 黃金
  SI=F 白銀

💰 加密貨幣
  BTC ETH BNB SOL
  XRP DOGE ADA AVAX

━━━━━
輸入代號可查詢即時分析圖
例如：BTC、2330、黃金"""

# 關鍵字對照
KEYWORD_MAP = {
    # 台股中文名稱
    "台積電":"2330.TW", "鴻海":"2317.TW", "大立光":"3008.TW", "長榮":"2603.TW",
    "宏達電":"2498.TW", "聯發科":"2454.TW", "台塑":"1301.TW", "中鋼":"2002.TW",
    "富邦金":"2881.TW", "國泰金":"2882.TW", "玉山金":"2884.TW", "兆豐金":"2886.TW",
    "中信金":"2891.TW", "南亞":"1303.TW", "廣達":"2382.TW", "仁寶":"2324.TW",
    "華碩":"2357.TW", "宏碁":"2353.TW", "台達電":"2308.TW", "研華":"2395.TW",
    # 美股中文名稱
    "輝達":"NVDA", "微軟":"MSFT", "谷歌":"GOOGL", "蘋果":"AAPL",
    "特斯拉":"TSLA", "亞馬遜":"AMZN", "臉書":"META", "網飛":"NFLX",
    # 貴金屬
    "黃金":"GC=F", "金":"GC=F", "白銀":"SI=F", "銀":"SI=F",
    # 加密貨幣中文
    "比特幣":"BTCUSDT", "以太幣":"ETHUSDT", "狗狗幣":"DOGEUSDT",
    # 英文代號
    "BTC":"BTCUSDT", "ETH":"ETHUSDT", "BNB":"BNBUSDT", "SOL":"SOLUSDT",
    "XRP":"XRPUSDT", "DOGE":"DOGEUSDT", "ADA":"ADAUSDT", "AVAX":"AVAXUSDT",
    # 台股數字代號
    "2330":"2330.TW", "2317":"2317.TW", "3008":"3008.TW", "2603":"2603.TW",
    "2498":"2498.TW", "2454":"2454.TW", "1301":"1301.TW", "2002":"2002.TW",
    "2881":"2881.TW", "2882":"2882.TW", "2884":"2884.TW", "2886":"2886.TW",
    "2891":"2891.TW", "1303":"1303.TW", "2382":"2382.TW", "2357":"2357.TW",
    "2353":"2353.TW", "2308":"2308.TW",
}


# ══════════════════════════════════════════
#  LINE Webhook 處理
# ══════════════════════════════════════════

def verify_signature(body: bytes, signature: str) -> bool:
    if LINE_SECRET == "你的_Channel_Secret":
        return True  # 本機測試時跳過驗證
    mac = hmac.new(LINE_SECRET.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(mac).decode() == signature

@app.route("/webhook", methods=["POST"])
def webhook():
    signature = request.headers.get("X-Line-Signature", "")
    body      = request.get_data()
    if not verify_signature(body, signature):
        abort(400)

    data = json.loads(body)
    for event in data.get("events", []):
        if event["type"] != "message" or event["message"]["type"] != "text":
            continue
        reply_token = event["replyToken"]
        user_id     = event["source"].get("userId", LINE_USER_ID)
        text        = event["message"]["text"].strip().upper()

        # ── 教學 ──
        if text in ["教學", "TEACH", "教学", "HELP", "說明"]:
            reply_text(reply_token, KLINE_EDUCATION)
            continue

        # ── 清單 ──
        if text in ["清單", "LIST", "列表"]:
            reply_text(reply_token, WATCHLIST_MSG)
            continue

        # ── 智慧搜尋：任何代號/公司名稱 ──
        # 先查關鍵字對照表
        symbol = KEYWORD_MAP.get(text) or KEYWORD_MAP.get(text.upper())

        # 若對照表沒有，自動嘗試常見格式
        if not symbol:
            t = text.upper()
            CRYPTO_LIST = ["BTC","ETH","BNB","SOL","XRP","DOGE","ADA","AVAX",
                           "MATIC","DOT","LINK","UNI","ATOM","LTC","BCH","NEAR","APT","OP"]
            if t in CRYPTO_LIST:
                symbol = t + "USDT"
            elif t.isdigit() and len(t) == 4:
                symbol = t + ".TW"
            elif t.endswith(".TW"):
                symbol = t
            else:
                symbol = t

        reply_text(reply_token, "正在分析 " + symbol + "，請稍候...")
        try:
            is_crypto = symbol.endswith("USDT")

            if is_crypto:
                df = get_crypto_df(symbol)
            else:
                df = get_stock_df(symbol)
                # 資料不足且沒有 .TW，才嘗試補 .TW
                if (df is None or df.empty or len(df) < 5) and not symbol.endswith(".TW"):
                    new_sym = symbol + ".TW"
                    try:
                        df2 = get_stock_df(new_sym)
                        if df2 is not None and len(df2) >= 5:
                            df = df2
                            symbol = new_sym
                    except:
                        pass

            if df is None or df.empty or len(df) < 5:
                push_text("找不到 " + symbol + " 的資料\n請確認代號是否正確\n範例：TSLA / 2330 / BTC / NVDA / 黃金", user_id)
            else:
                df  = add_indicators(df)
                res = analyze(df)
                msg = format_analysis(symbol, res)
                chart_path = generate_chart(df, symbol)
                push_text(msg, user_id)
                push_image(chart_path, user_id)
        except Exception as e:
            push_text("查詢失敗 [" + symbol + "]\n" + str(e)[:80] + "\n請嘗試：TSLA / 2330 / BTC / NVDA", user_id)


    return "OK"

@app.route("/charts/<filename>")
def serve_chart(filename):
    path = os.path.join(CHART_DIR, filename)
    if not os.path.exists(path):
        abort(404)
    return send_file(path, mimetype="image/png")

@app.route("/")
def index():
    return "🤖 AI 股票虛擬貨幣機器人運作中"


# ══════════════════════════════════════════
#  自動掃描排程
# ══════════════════════════════════════════

def scan_cryptos():
    """每 30 秒掃描加密貨幣"""
    log.info("🔍 掃描加密貨幣...")
    alerts = []
    for symbol in DEFAULT_CRYPTOS:
        try:
            df  = get_crypto_df(symbol, interval="5m", limit=100)
            df  = add_indicators(df)
            res = analyze(df)
            if res["has_alert"]:
                alerts.append(format_analysis(symbol, res))
                path = generate_chart(df, symbol)
                push_image(path)
        except Exception as e:
            log.error(f"[加密貨幣掃描錯誤] {symbol}: {e}")
    if alerts:
        push_text("🚨 加密貨幣重要訊號通知！\n\n" + "\n\n".join(alerts))

def scan_stocks():
    """每 5 分鐘掃描股票與貴金屬"""
    log.info("🔍 掃描股票與貴金屬...")
    alerts = []
    for symbol in DEFAULT_STOCKS:
        try:
            df  = get_stock_df(symbol)
            df  = add_indicators(df)
            res = analyze(df)
            if res["has_alert"]:
                alerts.append(format_analysis(symbol, res))
                path = generate_chart(df, symbol)
                push_image(path)
        except Exception as e:
            log.error(f"[股票掃描錯誤] {symbol}: {e}")
    if alerts:
        push_text("🚨 股票/貴金屬重要訊號通知！\n\n" + "\n\n".join(alerts))


# ══════════════════════════════════════════
#  啟動
# ══════════════════════════════════════════

if __name__ == "__main__":
    scheduler = BackgroundScheduler(timezone="Asia/Taipei")
    scheduler.add_job(scan_cryptos, "interval", seconds=30,  id="crypto_scan")
    scheduler.add_job(scan_stocks,  "interval", minutes=5,   id="stock_scan")

    # Keep Alive：每 14 分鐘 ping 自己，避免 Render 免費版休眠
    def keep_alive():
        try:
            requests.get(BASE_URL, timeout=10)
            log.info("[Keep Alive] ping 成功")
        except Exception as e:
            log.warning(f"[Keep Alive] {e}")
    scheduler.add_job(keep_alive, "interval", minutes=14, id="keep_alive")
    scheduler.start()
    log.info("🤖 AI 股票虛擬貨幣機器人啟動！")
    log.info(f"📋 監控股票：{len(DEFAULT_STOCKS)} 檔 | 加密貨幣：{len(DEFAULT_CRYPTOS)} 個")
    log.info("📅 加密貨幣每 30 秒掃描 | 股票每 5 分鐘掃描")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
