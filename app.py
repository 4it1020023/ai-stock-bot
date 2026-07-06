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
BASE_URL     = os.environ.get("BASE_URL",     "https://你的應用名稱.onrender.com")  # 部署後填入

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
    """
    改用 CoinGecko API 抓取加密貨幣 OHLC 資料（不需要 Key，不被 Render 封鎖）
    symbol: BTCUSDT -> bitcoin, ETHUSDT -> ethereum ...
    """
    # Binance 代號 → CoinGecko ID
    CG_IDS = {
        "BTCUSDT": "bitcoin",    "ETHUSDT": "ethereum",
        "BNBUSDT": "binancecoin","SOLUSDT": "solana",
        "XRPUSDT": "ripple",     "DOGEUSDT": "dogecoin",
        "ADAUSDT": "cardano",    "AVAXUSDT": "avalanche-2",
    }
    cg_id = CG_IDS.get(symbol.upper())
    if not cg_id:
        raise Exception(f"不支援的幣種: {symbol}")

    url = f"https://api.coingecko.com/api/v3/coins/{cg_id}/ohlc"
    r = requests.get(url, params={"vs_currency": "usd", "days": "7"}, timeout=15)
    r.raise_for_status()
    data = r.json()   # [[timestamp, open, high, low, close], ...]
    df = pd.DataFrame(data, columns=["open_time", "open", "high", "low", "close"])
    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms")
    df["volume"] = 0.0   # CoinGecko OHLC 不含成交量，補 0
    for c in ["open","high","low","close","volume"]:
        df[c] = df[c].astype(float)
    return df.set_index("open_time").dropna().tail(limit)



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
        important.append(f"🚀 本根K棒暴漲 {pct_change:.1f}%！")
    elif pct_change <= -SPIKE_THRESHOLD:
        important.append(f"💥 本根K棒暴跌 {pct_change:.1f}%！")

    # ── RSI ──
    rsi = latest["rsi"]
    if rsi > RSI_OVERBOUGHT:
        important.append(f"⚠️ RSI 超買（{rsi:.1f}），小心拉回")
    elif rsi < RSI_OVERSOLD:
        important.append(f"⚠️ RSI 超賣（{rsi:.1f}），留意反彈")

    # ── MACD 交叉 ──
    if prev["macd"] < prev["macd_signal"] and latest["macd"] > latest["macd_signal"]:
        important.append("🟢 MACD 黃金交叉（偏多訊號）")
    elif prev["macd"] > prev["macd_signal"] and latest["macd"] < latest["macd_signal"]:
        important.append("🔴 MACD 死亡交叉（偏空訊號）")

    # ── 布林通道 ──
    if latest["close"] > latest["bb_upper"]:
        important.append("🔺 突破布林上軌（波動加劇，留意拉回）")
    elif latest["close"] < latest["bb_lower"]:
        important.append("🔻 跌破布林下軌（波動加劇，留意反彈）")

    # ── 均線 ──
    trend = "📈 均線偏多" if latest["ma20"] > latest["ma60"] else "📉 均線偏空"
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
    """產生 K 線圖並存成 PNG，回傳檔案路徑"""
    name   = NAME_MAP.get(symbol, symbol)
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
    ax1.plot(idx, (bb_m + 2*bb_s).values, color="#64748b", lw=0.8, ls="--", label="布林上軌")
    ax1.plot(idx, (bb_m - 2*bb_s).values, color="#64748b", lw=0.8, ls="--", label="布林下軌")
    ax1.legend(loc="upper left", fontsize=7, facecolor="#1a1a2e",
               labelcolor="#cccccc", framealpha=0.5)
    ax1.set_title(f"📊 {name}（{symbol}）K 線分析圖",
                  color="#e2e8f0", fontsize=11, pad=8)
    ax1.set_xlim(-1, len(df_plot))
    ax1.set_ylabel("價格", color="#cccccc", fontsize=8)

    # ── 成交量 ──
    vol_colors = ["#ef5350" if r["close"] >= r["open"] else "#26a69a"
                  for _, r in df_plot.iterrows()]
    ax2.bar(idx, df_plot["volume"].values, color=vol_colors, alpha=0.7)
    ax2.set_ylabel("成交量", color="#cccccc", fontsize=8)
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
    fig.text(0.99, 0.01, f"產生時間：{now_str}  |  僅供參考，非投資建議",
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
        f"📊 【{name}（{symbol}）】",
        f"🕐 分析時間：{now}",
        f"💰 目前價格：{result['price']}",
        f"📈 RSI 指標：{result['rsi']}",
        f"📊 本根漲跌：{result['pct_change']:+.2f}%",
        "─────────────",
    ]
    if result["important"]:
        lines.append("🚨 重要訊號：")
        lines += result["important"]
    else:
        lines.append("✅ 目前無重要訊號")
    lines += result["info"]
    lines.append("─────────────")
    lines.append("⚠️ 以上僅供參考，請自行評估風險")
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
    "台積電":"2330.TW", "鴻海":"2317.TW", "大立光":"3008.TW", "長榮":"2603.TW",
    "輝達":"NVDA", "微軟":"MSFT", "谷歌":"GOOGL", "蘋果":"AAPL",
    "黃金":"GC=F", "金":"GC=F", "白銀":"SI=F", "銀":"SI=F",
    "比特幣":"BTCUSDT", "BTC":"BTCUSDT",
    "以太幣":"ETHUSDT", "ETH":"ETHUSDT",
    "BNB":"BNBUSDT", "SOL":"SOLUSDT",
    "XRP":"XRPUSDT", "狗狗幣":"DOGEUSDT", "DOGE":"DOGEUSDT",
    "ADA":"ADAUSDT", "AVAX":"AVAXUSDT",
    "2330":"2330.TW", "2317":"2317.TW", "3008":"3008.TW", "2603":"2603.TW",
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

        # ── 查詢個股/幣種 ──
        symbol = KEYWORD_MAP.get(text) or KEYWORD_MAP.get(text.upper())
        # 也允許直接輸入完整代號如 NVDA、2330.TW
        if not symbol:
            for k, v in KEYWORD_MAP.items():
                if text == k.upper():
                    symbol = v
                    break
        if not symbol and (text.endswith(".TW") or text in [s.upper() for s in DEFAULT_STOCKS + DEFAULT_CRYPTOS]):
            symbol = text

        if symbol:
            reply_text(reply_token, f"⏳ 正在分析 {NAME_MAP.get(symbol, symbol)}，請稍候...")
            try:
                is_crypto = symbol.endswith("USDT")
                df  = get_crypto_df(symbol) if is_crypto else get_stock_df(symbol)
                df  = add_indicators(df)
                res = analyze(df)
                msg = format_analysis(symbol, res)
                chart_path = generate_chart(df, symbol)
                push_text(msg, user_id)
                push_image(chart_path, user_id)
            except Exception as e:
                push_text(f"⚠️ 查詢失敗：{e}", user_id)
        else:
            reply_text(reply_token,
                "❓ 找不到此代號\n\n"
                "輸入範例：\n"
                "  BTC → 比特幣\n"
                "  2330 → 台積電\n"
                "  黃金 → 黃金\n"
                "  教學 → K線說明\n"
                "  清單 → 監控清單")

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
    scheduler.start()
    log.info("🤖 AI 股票虛擬貨幣機器人啟動！")
    log.info(f"📋 監控股票：{len(DEFAULT_STOCKS)} 檔 | 加密貨幣：{len(DEFAULT_CRYPTOS)} 個")
    log.info("📅 加密貨幣每 30 秒掃描 | 股票每 5 分鐘掃描")
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))
