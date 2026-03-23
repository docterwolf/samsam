import os
import requests
from flask import Flask, request

TOKEN = os.environ.get("TOKEN")
app = Flask(__name__)

def send_message(chat_id, text):
    requests.post(f"https://api.telegram.org/bot{TOKEN}/sendMessage", json={
        "chat_id": chat_id,
        "text": text
    })

def send_video(chat_id, file_path):
    with open(file_path, "rb") as f:
        requests.post(f"https://api.telegram.org/bot{TOKEN}/sendVideo",
                      data={"chat_id": chat_id},
                      files={"video": f})

@app.route("/", methods=["POST"])
def webhook():
    data = request.json

    if "message" in data:
        chat_id = data["message"]["chat"]["id"]
        url = data["message"].get("text", "")

        if url.startswith("http"):
            send_message(chat_id, "در حال دانلود...")

            try:
                file_path = "video.mp4"

                # دانلود فایل
                r = requests.get(url, stream=True)
                with open(file_path, "wb") as f:
                    for chunk in r.iter_content(chunk_size=8192):
                        if chunk:
                            f.write(chunk)

                # ارسال به تلگرام
                send_video(chat_id, file_path)

                os.remove(file_path)

            except Exception as e:
                send_message(chat_id, f"خطا: {str(e)}")

    return "ok"