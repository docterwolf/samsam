import os
import re
import sys
import asyncio
import subprocess
from typing import Dict, Any, Optional, List

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
    raise RuntimeError("BOT_TOKEN is missing. Set it in Render ENV.")

# For test post image
TEST_IMAGE_PATH = os.getenv("TEST_IMAGE_PATH", "assets/test.jpg")

# ---------------- FastAPI ----------------
api = FastAPI()

# ---------------- Telegram state per chat ----------------
# step: None / "phone" / "otp"
chat_state: Dict[int, Dict[str, Any]] = {}


def _get_state(chat_id: int) -> Dict[str, Any]:
    if chat_id not in chat_state:
        chat_state[chat_id] = {"step": None}
    return chat_state[chat_id]


def ensure_playwright_browser_installed():
    """
    Fix Render error: 'Executable doesn't exist...'
    by installing chromium on startup.
    """
    try:
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
        print("[startup] Playwright chromium installed.")
    except Exception as e:
        print("[startup] Playwright install failed:", repr(e))


# ---------------- Handlers ----------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام 👋\n"
        "دستورات:\n"
        "/login  ورود با شماره\n"
        "/status وضعیت سشن\n"
        "/post   ثبت آگهی تستی (با عکس ثابت)\n"
        "/logout خروج کامل\n"
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("در حال بررسی سشن...")
    ok = await has_valid_session()
    await update.message.reply_text("✅ سشن معتبره." if ok else "❌ سشن معتبر نیست.")


async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    st = _get_state(update.effective_chat.id)

    if await has_valid_session():
        await update.message.reply_text("سشن معتبره ✅\nاگر می‌خوای خارج شی /logout بزن.")
        st["step"] = None
        return

    st["step"] = "phone"
    await update.message.reply_text("شماره موبایل رو بفرست (09xxxxxxxxx):")


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("در حال خروج کامل از دیوار...")
    try:
        await logout(update.effective_chat.id)
        await update.message.reply_text("✅ خارج شدی. حالا /login بزن.")
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در logout: {e}")


async def cmd_post(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    if not await has_valid_session():
        await update.message.reply_text("❌ اول /login کن.")
        return

    # ensure test image exists
    if not os.path.exists(TEST_IMAGE_PATH):
        await update.message.reply_text(
            f"❌ عکس تست پیدا نشد: {TEST_IMAGE_PATH}\n"
            "یک عکس بذار داخل assets/test.jpg و پوش کن.\n"
            "یا env: TEST_IMAGE_PATH رو تنظیم کن."
        )
        return

    await update.message.reply_text("در حال ثبت آگهی تستی...")

    try:
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
        # e already includes stage + debug paths
        await update.message.reply_text(f"❌ {e}")


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    text = (update.message.text or "").strip()
    st = _get_state(chat_id)
    step = st.get("step")

    if step == "phone":
        phone = re.sub(r"\D", "", text)
        if phone.startswith("98"):
            phone = "0" + phone[2:]

        if not phone.startswith("09") or len(phone) != 11:
            await update.message.reply_text("❌ شماره معتبر نیست. مثال: 09351234567")
            return

        await update.message.reply_text("در حال درخواست کد...")
        try:
            await start_login(chat_id, phone)
            st["step"] = "otp"
            await update.message.reply_text("کد ۶ رقمی رو بفرست:")
        except Exception as e:
            st["step"] = None
            await update.message.reply_text(f"❌ خطا در درخواست کد: {e}")
        return

    if step == "otp":
        code = re.sub(r"\D", "", text)[:6]
        if len(code) != 6:
            await update.message.reply_text("❌ کد باید ۶ رقم باشه.")
            return

        await update.message.reply_text("در حال تایید...")
        try:
            ok = await verify_otp(chat_id, code)
            st["step"] = None
            await update.message.reply_text("✅ لاگین موفق!" if ok else "❌ لاگین ناموفق.")
        except Exception as e:
            st["step"] = None
            await update.message.reply_text(f"❌ خطا در تایید کد: {e}")
        return

    await update.message.reply_text("از دستورات استفاده کن: /login /status /post /logout")


# ---------------- Build Telegram app ----------------
def build_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("login", cmd_login))
    app.add_handler(CommandHandler("logout", cmd_logout))
    app.add_handler(CommandHandler("post", cmd_post))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    return app


telegram_app: Optional[Application] = None
telegram_task: Optional[asyncio.Task] = None


@api.on_event("startup")
async def on_startup():
    global telegram_app, telegram_task

    ensure_playwright_browser_installed()

    telegram_app = build_app()
    await telegram_app.initialize()
    await telegram_app.start()

    telegram_task = asyncio.create_task(telegram_app.updater.start_polling())
    print("[startup] Telegram polling started.")


@api.on_event("shutdown")
async def on_shutdown():
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

    print("[shutdown] Telegram stopped.")


@api.get("/")
async def root():
    return {"status": "ok"}


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    uvicorn.run("bot:api", host="0.0.0.0", port=port, log_level="info")
