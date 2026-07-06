from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from linebot.exceptions import InvalidSignatureError
from linebot.models import MessageEvent, TextMessage, TextSendMessage

import os

# 用你自己的 Access Token 和 Secret
LINE_CHANNEL_ACCESS_TOKEN = 'XWvqMbJny7tGnjh94zWWUNwPijamlulDp9CpYHDvhN5T3Lq3zhcZKy3Ty8hJC0xtkEDaHGVzvWME4X7gsTVUe9W4XjgwAwuSdTfPAbELMv0INZe4OzbQkhl1V/xqqzyozKxeskFQE9EgWnTM7LME0gdB04t89/1O/w1cDnyilFU='
LINE_CHANNEL_SECRET = 'c7e766b4dc5092341579fb64e0016365'

line_bot_api = LineBotApi(LINE_CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(LINE_CHANNEL_SECRET)

app = Flask(__name__)

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    # 印出群組 ID（如果是群組）
    if event.source.type == 'group':
        print("Group ID：", event.source.group_id)
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="你好，我收到了訊息！這是群組。")
        )
    else:
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(text="你好，我是羽球自動通知 Bot！")
        )

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
