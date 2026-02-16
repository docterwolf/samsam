import os
import sys
import asyncio
import subprocess
from dataclasses import dataclass
from typing import Optional, List

from dotenv import load_dotenv
from fastapi import FastAPI
import uvicorn

from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters
)

from divar_automation import (
    has_valid_session, start_login, verify_otp, create_post_on_divar, logout
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is not set")

PHONE, OTP = range(2)
CAT, TITLE, DESC, PRICE, CONFIRM = range(2, 7)

@dataclass
class PostDraft:
    category_index: int = 0
    title: str = ""
    description: str = ""
    price: str = ""
    image_paths: Optional[List[str]] = None


# -------------------- Playwright bootstrap --------------------
def ensure_playwright_browser_installed():
    try:
        print("[startup] Ensuring Playwright Chromium is installed...")
        subprocess.check_call([sys.executable, "-m", "playwright", "install", "chromium"])
        print("[startup] Playwright Chromium OK.")
    except Exception as e:
        print("[startup] Playwright install failed:", repr(e))


# -------------------- Telegram handlers --------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "سلام!\n"
        "/login برای ورود\n"
        "/logout برای خروج\n"
        "/newpost برای ثبت آگهی\n"
        "/cancel برای لغو"
    )


async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await has_valid_session():
        await update.message.reply_text("سشن معتبره ✅\nاگر می‌خوای خارج شی /logout بزن.")
        return ConversationHandler.END
    await update.message.reply_text("شماره موبایل رو بفرست (09xxxxxxxxx):")
    return PHONE


async def login_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    await update.message.reply_text("در حال درخواست کد...")

    try:
        await start_login(update.effective_chat.id, phone)
    except Exception as e:
        await update.message.reply_text(f"خطا در درخواست کد: {e}")
        return ConversationHandler.END

    await update.message.reply_text("کد ۶ رقمی رو بفرست:")
    return OTP


async def login_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    otp = update.message.text.strip()
    await update.message.reply_text("در حال تایید...")

    try:
        ok = await verify_otp(update.effective_chat.id, otp)
        await update.message.reply_text("لاگین موفق ✅" if ok else "لاگین ناموفق ❌")
    except Exception as e:
        await update.message.reply_text(f"خطا: {e}")

    return ConversationHandler.END


async def cmd_logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("در حال خروج از حساب دیوار و پاک کردن سشن...")
    try:
        await logout(update.effective_chat.id)
        await update.message.reply_text("✅ خارج شدی. حالا می‌تونی دوباره /login بزنی.")
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در logout: {e}")


async def cmd_newpost(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await has_valid_session():
        await update.message.reply_text("اول /login رو انجام بده.")
        return ConversationHandler.END

    context.user_data["draft"] = PostDraft()
    await update.message.reply_text("category_index رو بفرست (مثلاً 0):")
    return CAT


async def post_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["draft"].category_index = int(update.message.text.strip())
    except:
        await update.message.reply_text("فقط عدد بفرست (مثلاً 0).")
        return CAT
    await update.message.reply_text("عنوان آگهی:")
    return TITLE


async def post_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["draft"].title = update.message.text.strip()
    await update.message.reply_text("توضیحات آگهی:")
    return DESC


async def post_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["draft"].description = update.message.text.strip()
    await update.message.reply_text("قیمت (عدد):")
    return PRICE


async def post_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["draft"].price = update.message.text.strip()
    d: PostDraft = context.user_data["draft"]
    await update.message.reply_text(
        "تایید نهایی؟ فقط ✅ بفرست\n\n"
        f"category_index: {d.category_index}\n"
        f"عنوان: {d.title}\n"
        f"قیمت: {d.price}"
    )
    return CONFIRM


async def post_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message.text.strip() != "✅":
        await update.message.reply_text("لغو شد.")
        return ConversationHandler.END

    d: PostDraft = context.user_data["draft"]
    await update.message.reply_text("در حال ثبت آگهی...")

    try:
        res = await create_post_on_divar(
            category_index=d.category_index,
            title=d.title,
            description=d.description,
            price=d.price,
            image_paths=d.image_paths,
            chat_id=update.effective_chat.id,
        )
        await update.message.reply_text(res)
    except Exception as e:
        await update.message.reply_text(f"خطا: {e}")

    return ConversationHandler.END


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("لغو شد.")
    return ConversationHandler.END


def build_telegram_app() -> Application:
    app = Application.builder().token(BOT_TOKEN).build()

    login_conv = ConversationHandler(
        entry_points=[CommandHandler("login", cmd_login)],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_phone)],
            OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_otp)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    post_conv = ConversationHandler(
        entry_points=[CommandHandler("newpost", cmd_newpost)],
        states={
            CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_cat)],
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_title)],
            DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_desc)],
            PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_price)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cmd_cancel)],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("logout", cmd_logout))
    app.add_handler(login_conv)
    app.add_handler(post_conv)
    return app


# -------------------- FastAPI (Render Web Service) --------------------
api = FastAPI()
telegram_app: Optional[Application] = None
telegram_task: Optional[asyncio.Task] = None


@api.get("/")
async def root():
    return {"status": "ok", "telegram": "running" if telegram_task and not telegram_task.done() else "stopped"}


@api.get("/health")
async def health():
    return {"ok": True}


@api.on_event("startup")
async def on_startup():
    global telegram_app, telegram_task

    # Ensure chromium exists (Render fix)
    ensure_playwright_browser_installed()

    telegram_app = build_telegram_app()

    async def runner():
        await telegram_app.initialize()
        await telegram_app.start()
        await telegram_app.updater.start_polling()
        while True:
            await asyncio.sleep(3600)

    telegram_task = asyncio.create_task(runner())


@api.on_event("shutdown")
async def on_shutdown():
    global telegram_app, telegram_task
    try:
        if telegram_app:
            await telegram_app.updater.stop()
            await telegram_app.stop()
            await telegram_app.shutdown()
    except Exception:
        pass
    try:
        if telegram_task and not telegram_task.done():
            telegram_task.cancel()
    except Exception:
        pass


if __name__ == "__main__":
    port = int(os.getenv("PORT", "10000"))
    uvicorn.run("bot:api", host="0.0.0.0", port=port, log_level="info")
