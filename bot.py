import os
import re
import asyncio
from enum import Enum
from typing import Dict

from fastapi import FastAPI
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

from playwright.async_api import async_playwright, TimeoutError


# ================== ENV ==================

BOT_TOKEN = os.getenv("BOT_TOKEN")
DIVAR_CITY_SLUG = os.getenv("DIVAR_CITY_SLUG", "")
DIVAR_STATE_PATH = os.getenv("DIVAR_STATE_PATH", "/tmp/divar_state.json")
DIVAR_SUCCESS_TEXT = os.getenv("DIVAR_SUCCESS_TEXT", "ثبت آگهی")
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN missing")


def divar_new_url():
    if DIVAR_CITY_SLUG:
        return f"https://divar.ir/{DIVAR_CITY_SLUG}/new"
    return "https://divar.ir/new"


# ================== Playwright Manager ==================

class PW:
    pw = None
    browser = None
    contexts: Dict[int, any] = {}
    pages: Dict[int, any] = {}
    lock = asyncio.Lock()

    @classmethod
    async def start(cls):
        cls.pw = await async_playwright().start()
        cls.browser = await cls.pw.chromium.launch(
            headless=HEADLESS,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )

    @classmethod
    async def get_context(cls, user_id: int):
        if user_id in cls.contexts:
            return cls.contexts[user_id]

        ctx = await cls.browser.new_context()
        page = await ctx.new_page()

        cls.contexts[user_id] = ctx
        cls.pages[user_id] = page
        return ctx

    @classmethod
    def get_page(cls, user_id: int):
        return cls.pages[user_id]

    @classmethod
    async def screenshot(cls, user_id: int, name: str):
        page = cls.get_page(user_id)
        os.makedirs("/tmp/screens", exist_ok=True)
        await page.screenshot(path=f"/tmp/screens/{user_id}_{name}.png", full_page=True)


# ================== Telegram State ==================

class Step(str, Enum):
    IDLE = "IDLE"
    WAIT_PHONE = "WAIT_PHONE"
    WAIT_OTP = "WAIT_OTP"

user_steps: Dict[int, Step] = {}
user_phone: Dict[int, str] = {}


# ================== Helpers ==================

def normalize_phone(text: str):
    text = re.sub(r"\D", "", text)
    if text.startswith("98"):
        text = "0" + text[2:]
    if text.startswith("9"):
        text = "0" + text
    if not text.startswith("09") or len(text) != 11:
        raise ValueError("شماره صحیح نیست")
    return text

def normalize_otp(text: str):
    text = re.sub(r"\D", "", text)
    if len(text) != 6:
        raise ValueError("کد باید ۶ رقمی باشد")
    return text


# ================== DOM Checks ==================

async def wait_phone_modal(page):
    await page.goto(divar_new_url(), wait_until="domcontentloaded")
    await page.locator('section[role="dialog"]').wait_for(timeout=20000)
    await page.get_by_text("شمارهٔ موبایل خود را وارد کنید").wait_for(timeout=20000)

async def wait_otp_modal(page):
    await page.get_by_text("کد تأیید را وارد کنید").wait_for(timeout=20000)
    await page.locator('input[name="code"]').wait_for(timeout=20000)

async def login_success(page):
    try:
        await page.get_by_text(DIVAR_SUCCESS_TEXT).wait_for(timeout=15000)
        return True
    except TimeoutError:
        return False


# ================== Handlers ==================

async def cmd_login(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_steps[user_id] = Step.WAIT_PHONE
    await update.message.reply_text("شماره موبایل را ارسال کن:")

async def on_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    step = user_steps.get(user_id, Step.IDLE)

    if step == Step.WAIT_PHONE:
        try:
            phone = normalize_phone(update.message.text)
        except Exception as e:
            await update.message.reply_text(str(e))
            return

        user_phone[user_id] = phone

        async with PW.lock:
            try:
                await PW.get_context(user_id)
                page = PW.get_page(user_id)

                await wait_phone_modal(page)

                await page.fill('input[name="mobile"]', phone[1:])
                await page.get_by_role("button", name="تأیید").click()

                await wait_otp_modal(page)

                user_steps[user_id] = Step.WAIT_OTP
                await update.message.reply_text("کد پیامک را بفرست:")
            except Exception as e:
                await PW.screenshot(user_id, "phone_error")
                await update.message.reply_text(f"خطا: {e}\n/login بزن دوباره")

    elif step == Step.WAIT_OTP:
        try:
            otp = normalize_otp(update.message.text)
        except Exception as e:
            await update.message.reply_text(str(e))
            return

        async with PW.lock:
            try:
                page = PW.get_page(user_id)

                await page.fill('input[name="code"]', otp)
                await page.get_by_role("button", name="ورود").click()

                success = await login_success(page)

                if success:
                    user_steps[user_id] = Step.IDLE
                    await update.message.reply_text("✅ با موفقیت لاگین شدی")
                else:
                    kb = InlineKeyboardMarkup([
                        [InlineKeyboardButton("بررسی", callback_data="verify")]
                    ])
                    await update.message.reply_text(
                        "⚠️ ظاهراً لاگین نشدی",
                        reply_markup=kb
                    )

            except Exception as e:
                await PW.screenshot(user_id, "otp_error")
                await update.message.reply_text(f"خطا در OTP: {e}\n/login بزن")

async def verify_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    user_id = query.from_user.id

    async with PW.lock:
        try:
            page = PW.get_page(user_id)
            await page.goto(divar_new_url())

            if await login_success(page):
                await query.edit_message_text("✅ بررسی شد: لاگین موفق است")
            else:
                await query.edit_message_text("❌ هنوز لاگین نیستی. /login بزن")
                user_steps[user_id] = Step.WAIT_PHONE

        except Exception as e:
            await query.edit_message_text(f"خطا در بررسی: {e}")


# ================== FastAPI ==================

api = FastAPI()
tg_app = None

@api.on_event("startup")
async def startup():
    global tg_app
    await PW.start()

    tg_app = Application.builder().token(BOT_TOKEN).build()
    tg_app.add_handler(CommandHandler("login", cmd_login))
    tg_app.add_handler(CallbackQueryHandler(verify_callback, pattern="verify"))
    tg_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_message))

    await tg_app.initialize()
    await tg_app.start()

@api.on_event("shutdown")
async def shutdown():
    await PW.browser.close()
