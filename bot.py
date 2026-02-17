"""
bot.py

این فایل کارهای زیر را انجام می‌دهد:
1) وب‌سرویس FastAPI برای اینکه Render سرویس را "alive" نگه دارد
2) اجرای python-telegram-bot به شکل polling داخل startup
3) مدیریت state چت (کاربر الان شماره می‌دهد یا OTP)
4) ارسال درخواست‌ها به divar_automation.py و نمایش نتیجه

این فایل "هیچ اتوماسیون مرورگر" را مستقیم انجام نمی‌دهد.
همه چیز مربوط به مرورگر داخل divar_automation.py است.
"""

import os
import re
import sys
import asyncio
import subprocess
from typing import Dict, Any, Optional

from fastapi import FastAPI
import uvicorn

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# ماژول اتوماسیون دیوار
from divar_automation import (
    has_valid_session,
    start_login,
    verify_otp,
    create_post_on_divar,
    logout,
)

# -----------------------------
# 1) ENV (متغیرهای محیطی)
# -----------------------------

# توکن ربات تلگرام
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing. Set it in Render ENV.")

# مسیر عکس تستی برای ثبت آگهی
# اگر داخل پروژه assets/test.jpg باشد کافی است.
TEST_IMAGE_PATH = os.getenv("TEST_IMAGE_PATH", "assets/test.jpg")

# -----------------------------
# 2) FastAPI برای Render
# -----------------------------

# Render برای Web Service نیاز دارد یک پورت listen کند.
# ما یک FastAPI ساده می‌سازیم.
api = FastAPI()

# -----------------------------
# 3) وضعیت (State) هر چت
# -----------------------------

# برای هر chat_id ذخیره می‌کنیم کاربر الان در چه مرحله‌ای است:
# - step = None : هیچ فرآیند لاگینی در جریان نیست
# - step = "phone" : ربات منتظر شماره است
# - step = "otp" : ربات منتظر کد ۶ رقمی است
chat_state: Dict[int, Dict[str, Any]] = {}


def _log(msg: str):
    """
    لاگ ساده برای Render Logs
    """
    print(f"[BOT] {msg}")


def _get_state(chat_id: int) -> Dict[str, Any]:
    """
    اگر state برای این چت وجود ندارد، بساز.
    """
    if chat_id not in chat_state:
        chat_state[chat_id] = {"step": None}
    return chat_state[chat_id]


# -----------------------------
# 4) نصب Playwright chromium در Startup
# -----------------------------

def ensure_playwright_browser_installed():
    """
    چرا این کار لازم است؟
    چون روی Render گاهی Playwright نصب می‌شود ولی مرورگر Chromium دانلود نمی‌شود.
    نتیجه: خطای Executable doesn't exist...

    اینجا در startup:
      python -m playwright install chromium
    را اجرا می‌کنیم.
    """
    try:
        _log("Installing Playwright chromium (startup)...")
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
        _log("Playwright chromium installed.")
    except Exception as e:
        _log(f"Playwright install failed: {repr(e)}")


# -----------------------------
# 5) Command Handlers (دستورات تلگرام)
# -----------------------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start
    معرفی دستورات و راهنما
    """
    await update.message.reply_text(
        "سلام 👋\n"
        "دستورات:\n"
        "/login  ورود با شماره\n"
        "/status وضعیت سشن\n"
        "/post   ثبت آگهی تستی (با عکس ثابت)\n"
        "/logout خروج کامل\n"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /status
    بررسی اینکه آیا سشن معتبر است یا نه
    """
    await update.message.reply_text("در حال بررسی سشن...")
    ok = await has_valid_session()
    await update.message.reply_text("✅ سشن معتبره." if ok else "❌ سشن معتبر نیست.")


async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /login
    اگر سشن معتبر بود، پیام می‌دهیم.
    اگر نبود، وارد مرحله گرفتن شماره می‌شویم.
    """
    st = _get_state(update.effective_chat.id)

    if await has_valid_session():
        await update.message.reply_text("سشن معتبره ✅\nاگر می‌خوای خارج شی /logout بزن.")
        st["step"] = None
        return

    st["step"] = "phone"
    await update.message.reply_text("شماره موبایل رو بفرست (09xxxxxxxxx):")


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /logout
    خروج واقعی و کامل (طبق divar_automation.logout)
    """
    await update.message.reply_text("در حال خروج کامل از دیوار...")
    try:
        await logout(update.effective_chat.id)
        await update.message.reply_text("✅ خارج شدی. حالا /login بزن.")
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در logout: {e}")


async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /post
    یک آگهی تستی می‌سازد.
    چون دیوار خیلی وقت‌ها عکس را اجباری می‌کند، باید عکس داشته باشیم.
    """
    chat_id = update.effective_chat.id

    # اگر لاگین نیست، اجازه ثبت نمی‌دهیم
    if not await has_valid_session():
        await update.message.reply_text("❌ اول /login کن.")
        return

    # چک وجود فایل عکس
    if not os.path.exists(TEST_IMAGE_PATH):
        await update.message.reply_text(
            f"❌ عکس تست پیدا نشد: {TEST_IMAGE_PATH}\n"
            "یک عکس بذار داخل assets/test.jpg و پوش کن.\n"
            "یا env: TEST_IMAGE_PATH رو تنظیم کن."
        )
        return

    await update.message.reply_text("در حال ثبت آگهی تستی...")

    try:
        # اینجا create_post_on_divar به صورت مرحله‌ای اجرا می‌شود.
        # اگر خطا بخورد، متن خطا شامل 'مرحله: ...' خواهد بود.
        res = await create_post_on_divar(
            chat_id=chat_id,
            category_index=0,
            title="آگهی تستی ربات",
            description="این آگهی توسط ربات ساخته شده است.",
            price="150000",
            image_paths=[TEST_IMAGE_PATH],
        )
        await update.message.reply_text(res)
    except Exception as e:
        # پیام خطا را مستقیم برمی‌گردانیم (شامل مرحله + مسیر فایل دیباگ)
        await update.message.reply_text(f"❌ {e}")


# -----------------------------
# 6) Message Handler (پیام‌های عادی)
# -----------------------------

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    این تابع پیام‌های غیر از دستور را مدیریت می‌کند.
    بر اساس state:
    - اگر منتظر phone هستیم: شماره را می‌گیریم و start_login می‌زنیم
    - اگر منتظر otp هستیم: کد را می‌گیریم و verify_otp می‌زنیم
    """
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    st = _get_state(chat_id)
    step = st.get("step")

    # -------------------------
    # حالت 1: منتظر شماره
    # -------------------------
    if step == "phone":
        phone = re.sub(r"\D", "", text)

        # اگر کاربر 98 زد، تبدیل به 0...
        if phone.startswith("98"):
            phone = "0" + phone[2:]

        # اعتبارسنجی ساده شماره
        if not phone.startswith("09") or len(phone) != 11:
            await update.message.reply_text("❌ شماره معتبر نیست. مثال: 09351234567")
            return

        await update.message.reply_text("در حال درخواست کد...")

        try:
            # درخواست کد از دیوار
            await start_login(chat_id, phone)

            # اگر موفق بود، وارد مرحله otp می‌شویم
            st["step"] = "otp"
            await update.message.reply_text("کد ۶ رقمی رو بفرست:")
        except Exception as e:
            # اگر خطا شد، state را reset می‌کنیم تا گیج نشود
            st["step"] = None
            await update.message.reply_text(f"❌ خطا در درخواست کد: {e}")
        return

    # -------------------------
    # حالت 2: منتظر OTP
    # -------------------------
    if step == "otp":
        code = re.sub(r"\D", "", text)[:6]

        if len(code) != 6:
            await update.message.reply_text("❌ کد باید ۶ رقم باشه.")
            return

        await update.message.reply_text("در حال تایید...")

        try:
            ok = await verify_otp(chat_id, code)

            # در هر صورت از حالت otp خارج می‌شویم (موفق یا ناموفق)
            st["step"] = None

            await update.message.reply_text("✅ لاگین موفق!" if ok else "❌ لاگین ناموفق.")
        except Exception as e:
            st["step"] = None
            await update.message.reply_text(f"❌ خطا در تایید کد: {e}")
        return

    # -------------------------
    # حالت 3: هیچ فرآیند لاگین نداریم
    # -------------------------
    await update.message.reply_text("از دستورات استفاده کن: /login /status /post /logout")


# -----------------------------
# 7) ساخت اپلیکیشن تلگرام
# -----------------------------

def build_app() -> Application:
    """
    اینجا همه handler ها را اضافه می‌کنیم و app تلگرام را می‌سازیم.
    """
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("login", cmd_login))
    app.add_handler(CommandHandler("logout", cmd_logout))
    app.add_handler(CommandHandler("post", cmd_post))

    # پیام‌های متنی که دستور نیستند
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    return app


# -----------------------------
# 8) اجرای تلگرام داخل FastAPI lifecycle
# -----------------------------

telegram_app: Optional[Application] = None
telegram_task: Optional[asyncio.Task] = None


@api.on_event("startup")
async def on_startup():
    """
    وقتی Render سرویس را بالا می‌آورد:
    - chromium را نصب می‌کنیم (حل خطای Executable doesn't exist)
    - اپ تلگرام را initialize و start می‌کنیم
    - polling را در یک task جدا اجرا می‌کنیم
    """
    global telegram_app, telegram_task

    ensure_playwright_browser_installed()

    telegram_app = build_app()
    await telegram_app.initialize()
    await telegram_app.start()

    telegram_task = asyncio.create_task(telegram_app.updater.start_polling())
    _log("Telegram polling started.")


@api.on_event("shutdown")
async def on_shutdown():
    """
    هنگام خاموش شدن سرویس:
    - polling را stop
    - اپ تلگرام را shutdown
    """
    global telegram_app, telegram_task

    try:
        if telegram_task and not telegram_task.done():
            telegram_task.cancel()
    except Exception:
        pass

    try:
        if telegram_app:
            await telegram_app.updater.stop()
            await telegram_app.stop()
            await telegram_app.shutdown()
    except Exception:
        pass

    _log("Telegram stopped.")


@api.get("/")
async def root():
    """
    endpoint ساده برای health check
    """
    return {"status": "ok"}


if __name__ == "__main__":
    """
    اجرای لوکال:
      python bot.py
    روی Render معمولاً با uvicorn اجرا می‌شود.
    """
    port = int(os.getenv("PORT", "10000"))
    uvicorn.run("bot:api", host="0.0.0.0", port=port, log_level="info")
