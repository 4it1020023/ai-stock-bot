# AI 股票虛擬貨幣分析機器人

## 本機測試

```bash
pip install -r requirements.txt
python app.py
```

## 部署到 Render（免費雲端）

1. 到 https://github.com 建立新的 Repository，把這整個資料夾上傳
2. 到 https://render.com 註冊免費帳號
3. 點「New Web Service」→ 連結你的 GitHub repo
4. 設定環境變數（Environment Variables）：
   - LINE_TOKEN   → 你的 Channel Access Token
   - LINE_SECRET  → 你的 Channel Secret（Basic settings 頁面）
   - LINE_USER_ID → Ue15c9c822c38815932c988e59f3abc6d
   - BASE_URL     → https://你的應用名稱.onrender.com
5. 部署完成後，到 LINE Developers Console
   → Messaging API → Webhook URL
   → 填入：https://你的應用名稱.onrender.com/webhook
   → 開啟 Use webhook

## LINE Bot 指令

| 輸入 | 功能 |
|------|------|
| BTC | 比特幣即時分析圖 |
| 2330 | 台積電即時分析圖 |
| 黃金 | 黃金即時分析圖 |
| 教學 | K線圖教學說明 |
| 清單 | 顯示監控清單 |

## 自動推播規則
- 加密貨幣：每 30 秒掃描一次
- 股票/黃金/白銀：每 5 分鐘掃描一次
- 只有出現重要訊號（RSI超買超賣、MACD交叉、布林突破、暴漲暴跌）才推播
