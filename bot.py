import os
import re
import asyncio
import subprocess
import sys
from typing import Dict, Any

from fastapi import FastAPI
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from divar_automation import (
    has_valid_session,
    start_login,
    verify_otp,
    create_post_on_divar,
    logout,
)

# ---------------- ENV ----------------

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is missing in environment variables")

HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"


# ---------------- STATE ----------------

api = FastAPI()

# برای اینکه هر چت جداگانه مرحله خودش رو داشته باشه
user_state: Dict[int, Dict[str, Any]] = {}


def get_state(chat_id: int):
    if chat_id not in user_state:
        user_state[chat_id] = {
            "step": None,      # phone / otp / idle
            "phone": None,
        }
    return user_state[chat_id]


# ---------------- PLAYWRIGHT INSTALL ----------------

def ensure_playwright_browser():
    """
    Render ممکنه موقع build مرورگر رو دانلود نکنه.
    پس اینجا هنگام startup خودمون نصب chromium رو انجام میدیم.
    """
    try:
        subprocess.check_call(
            [sys.executable, "-m", "playwright", "install", "chromium"]
        )
    except Exception as e:
        print("Playwright install failed:", e)


# ---------------- TELEGRAM HANDLERS ----------------

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام ارباب 👋\n"
        "/login  شروع\n"
        "/post   ثبت آگهی\n"
        "/logout خروج کامل از دیوار\n"
        "/status وضعیت\n"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("در حال بررسی سشن...")
    ok = await has_valid_session()
    if ok:
        await update.message.reply_text("✅ سشن معتبر")
    else:
        await update.message.reply_text("❌ سشن معتبر نیست ")


async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    st = get_state(chat_id)

    # اگر از قبل لاگین بودی
    if await has_valid_session():
        await update.message.reply_text(
            "سشن معتبره ✅\n"
            "اگر می‌خوای خارج شی /logout بزن."
        )
        st["step"] = None
        return

    st["step"] = "phone"
    st["phone"] = None

    await update.message.reply_text("شماره موبایل ارباب (09xxxxxxxxx):")


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    await update.message.reply_text("در حال خروج کامل از دیوار...")

    try:
        await logout(chat_id)
        await update.message.reply_text(
            "✅ کامل خارج شد.\n"
        )
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در logout: {e}")


async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not await has_valid_session():
        await update.message.reply_text("❌ اول باید /login کنی.")
        return

    await update.message.reply_text("در حال ساخت آگهی نمونه...")

    try:
        result = await create_post_on_divar(
            chat_id=chat_id,
            category_index=0,  # اولین گزینه دسته
            title="سر تیتر اگهی",
            description="اناو ابراهام لینکلن بر فراز رود سفید سیاه",
            price="150000",
            image_paths=["assets/test.jpg"]

        )
        await update.message.reply_text(result)
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در ثبت آگهی: {e}")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()

    st = get_state(chat_id)
    step = st.get("step")

    # ---------------- STEP: PHONE ----------------
    if step == "phone":
        phone = re.sub(r"\D", "", text)

        # اگر کاربر 98 زد تبدیلش کنیم
        if phone.startswith("98"):
            phone = "0" + phone[2:]

        if not phone.startswith("09") or len(phone) != 11:
            await update.message.reply_text("❌ شماره معتبر نیست. مثال: 09351234567")
            return

        st["phone"] = phone

        await update.message.reply_text("در حال درخواست کد...")

        try:
            await start_login(chat_id, phone)
            st["step"] = "otp"
            await update.message.reply_text("کد ۶ رقمی به گوشیتون پیامک شد ارباب:")
        except Exception as e:
            st["step"] = None
            await update.message.reply_text(f"❌ خطا در درخواست کد: {e}")

        return

    # ---------------- STEP: OTP ----------------
    if step == "otp":
        code = re.sub(r"\D", "", text)[:6]

        if len(code) != 6:
            await update.message.reply_text("❌ کد باید ۶ رقم باشه.")
            return

        await update.message.reply_text("در حال تایید...")

        try:
            ok = await verify_otp(chat_id, code)
            if ok:
                st["step"] = None
                await update.message.reply_text("✅ لاگین انجام شد!")
            else:
                await update.message.reply_text("❌ کد اشتباهه یا تایید نشد. دوباره بفرست.")
        except Exception as e:
            st["step"] = None
            await update.message.reply_text(f"❌ خطا در تایید کد: {e}")

        return

    # ---------------- DEFAULT ----------------
    await update.message.reply_text(

        "/login\n"
        "/post\n"
        "/logout\n"
        "/status"
    )


# ---------------- TELEGRAM APP ----------------

def build_telegram_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("login", cmd_login))
    app.add_handler(CommandHandler("logout", cmd_logout))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("post", cmd_post))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    return app


telegram_app: Application = None
telegram_task = None


@api.on_event("startup")
async def on_startup():
    global telegram_app, telegram_task

    # نصب chromium برای playwright (روی Render ضروریه)
    ensure_playwright_browser()

    telegram_app = build_telegram_app()
    await telegram_app.initialize()
    await telegram_app.start()

    telegram_task = asyncio.create_task(telegram_app.updater.start_polling())
    print("Telegram bot started.")


@api.on_event("shutdown")
async def on_shutdown():
    global telegram_app, telegram_task

    try:
        if telegram_task:
            telegram_task.cancel()
    except:
        pass

    try:
        if telegram_app:
            await telegram_app.updater.stop()
            await telegram_app.stop()
            await telegram_app.shutdown()
    except:
        pass

    print("Telegram bot stopped.")


@api.get("/")
async def root():
    return {"status": "ok", "bot": "running"}
