import os
from dataclasses import dataclass
from typing import Optional, List

from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ConversationHandler,
    ContextTypes, filters
)

from divar_automation import has_valid_session, start_login, verify_otp, create_post_on_divar

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

# login states
PHONE, OTP = range(2)
# post states
CAT, TITLE, DESC, PRICE, CONFIRM = range(2, 7)


@dataclass
class PostDraft:
    category_index: int = 0
    title: str = ""
    description: str = ""
    price: str = ""
    image_paths: Optional[List[str]] = None  # بعداً اضافه می‌کنیم (دانلود عکس از تلگرام)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("سلام!\n/login برای ورود\n/newpost برای ثبت آگهی\n/cancel برای لغو")


# ---------- LOGIN ----------
async def login_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await has_valid_session():
        await update.message.reply_text("سشن معتبره ✅ نیازی به لاگین نیست.")
        return ConversationHandler.END

    await update.message.reply_text("شماره موبایل (مثل 09xxxxxxxxx) رو بفرست:")
    return PHONE


async def login_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip()
    context.user_data["phone"] = phone

    await update.message.reply_text("در حال درخواست کد تایید...")
    try:
        await start_login(update.effective_chat.id, phone)
    except Exception as e:
        await update.message.reply_text(f"خطا در درخواست کد: {e}")
        return ConversationHandler.END

    await update.message.reply_text("کد ۶ رقمی پیامک‌شده رو بفرست:")
    return OTP


async def login_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    otp = update.message.text.strip()
    await update.message.reply_text("در حال تایید کد...")

    try:
        ok = await verify_otp(update.effective_chat.id, otp)
        await update.message.reply_text("لاگین انجام شد ✅" if ok else "لاگین ناموفق ❌")
    except Exception as e:
        await update.message.reply_text(f"خطا: {e}")

    return ConversationHandler.END


# ---------- NEW POST ----------
async def newpost_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await has_valid_session():
        await update.message.reply_text("اول /login رو انجام بده.")
        return ConversationHandler.END

    context.user_data["draft"] = PostDraft()
    await update.message.reply_text("category_index رو بفرست (مثلاً 0).")
    return CAT


async def post_cat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        context.user_data["draft"].category_index = int(update.message.text.strip())
    except:
        await update.message.reply_text("فقط عدد بفرست (مثلاً 0).")
        return CAT

    await update.message.reply_text("عنوان آگهی رو بفرست:")
    return TITLE


async def post_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["draft"].title = update.message.text.strip()
    await update.message.reply_text("توضیحات آگهی رو بفرست:")
    return DESC


async def post_desc(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["draft"].description = update.message.text.strip()
    await update.message.reply_text("قیمت (عدد) رو بفرست:")
    return PRICE


async def post_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["draft"].price = update.message.text.strip()
    d: PostDraft = context.user_data["draft"]

    await update.message.reply_text(
        "تایید نهایی؟ فقط ✅ بفرست\n\n"
        f"category_index: {d.category_index}\n"
        f"عنوان: {d.title}\n"
        f"قیمت: {d.price}\n"
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
        )
        await update.message.reply_text(res)
    except Exception as e:
        await update.message.reply_text(f"خطا: {e}")

    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("لغو شد.")
    return ConversationHandler.END


def main():
    app = Application.builder().token(BOT_TOKEN).build()

    login_conv = ConversationHandler(
        entry_points=[CommandHandler("login", login_cmd)],
        states={
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_phone)],
            OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, login_otp)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    post_conv = ConversationHandler(
        entry_points=[CommandHandler("newpost", newpost_cmd)],
        states={
            CAT: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_cat)],
            TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_title)],
            DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_desc)],
            PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_price)],
            CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, post_confirm)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(login_conv)
    app.add_handler(post_conv)

    app.run_polling()


if __name__ == "__main__":
    main()
