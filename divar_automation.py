import os
import time
from typing import Dict, Tuple, List, Optional

from playwright.async_api import async_playwright, Playwright, Browser, BrowserContext, Page

DIVAR_NEW_URL = "https://divar.ir/new"
DIVAR_MYDIVAR_URL = "https://divar.ir/my-divar"

# ✅ مکان ثابت (بعداً راحت تغییر میدی)
DIVAR_CITY_SLUG = os.getenv("DIVAR_CITY_SLUG", "mashhad")
DIVAR_CITY_URL = f"https://divar.ir/s/{DIVAR_CITY_SLUG}"

STATE_PATH = os.getenv("DIVAR_STATE_PATH", "state.json")
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

# --- Login selectors (طبق HTML تو) ---
PHONE_INPUT = 'input[name="mobile"]'
OTP_INPUT   = 'input[name="code"]'
AUTH_SUBMIT = 'button.auth-actions__submit-button'  # "تأیید" / "ورود"

# --- New-post selectors (طبق HTML تو) ---
CATEGORY_TITLE = 'h2:has-text("انتخاب دستهٔ آگهی")'
CATEGORY_ITEM = 'div[role="button"].rawButton-W5tTZw'

IMAGES_INPUT = 'input[type="file"][name="Images"]'
TITLE_INPUT  = 'input[name="Title"]'
DESC_INPUT   = 'textarea[name="Description"]'

PRICE_INPUT  = 'input[name="price"]'
NEXT_BTN     = 'button[type="submit"]'
FINAL_SUBMIT = 'button[type="submit"]:has-text("ثبت اطلاعات")'

# صفحه تماس
CONTACT_CALL = 'input[name="Contact_CallEnabled"]'
CONTACT_CHAT = 'input[name="Contact_ChatEnabled"]'
CALL_METHOD_DIRECT = 'input[name="Contact_CallMethod"][value="DIRECT_CALL"]'

# اگر مکان هنوز تعیین نشده
LOCATION_DETERMINE_BTN = 'button.kt-action-field:has(span.kt-action-field__label:text("تعیین"))'

# --- نگه داشتن session بین phone و otp (خیلی مهم) ---
_login_sessions: Dict[int, Tuple[Playwright, Browser, BrowserContext, Page, float]] = {}
SESSION_TTL = 180  # ثانیه


def _state_exists() -> bool:
    return os.path.exists(STATE_PATH) and os.path.getsize(STATE_PATH) > 0


async def _close_login_session(chat_id: int) -> None:
    sess = _login_sessions.pop(chat_id, None)
    if not sess:
        return
    p, browser, context, page, ts = sess
    try:
        await context.close()
    except:
        pass
    try:
        await browser.close()
    except:
        pass
    try:
        await p.stop()
    except:
        pass


async def _cleanup_expired_sessions() -> None:
    now = time.time()
    expired = [cid for cid, (_, __, ___, ____, ts) in _login_sessions.items() if now - ts > SESSION_TTL]
    for cid in expired:
        await _close_login_session(cid)


async def has_valid_session() -> bool:
    """چک عملی سشن با my-divar"""
    if not _state_exists():
        return False
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=HEADLESS)
        context = await browser.new_context(storage_state=STATE_PATH)
        page = await context.new_page()
        await page.goto(DIVAR_MYDIVAR_URL, wait_until="domcontentloaded")
        html = await page.content()
        await context.close()
        await browser.close()
        return ("ورود به حساب کاربری" not in html and "شمارهٔ موبایل خود را وارد کنید" not in html)


async def start_login(chat_id: int, phone: str) -> None:
    """مرحله ۱: شماره موبایل → ارسال کد. (context باز می‌ماند)"""
    await _cleanup_expired_sessions()
    await _close_login_session(chat_id)

    p = await async_playwright().start()
    browser = await p.chromium.launch(headless=HEADLESS)
    context = await browser.new_context()
    page = await context.new_page()

    await page.goto(DIVAR_NEW_URL, wait_until="domcontentloaded")

    await page.wait_for_selector(PHONE_INPUT, timeout=15000)
    await page.fill(PHONE_INPUT, phone)
    await page.click(AUTH_SUBMIT)

    # باید وارد صفحه OTP بشه
    await page.wait_for_selector(OTP_INPUT, timeout=15000)

    _login_sessions[chat_id] = (p, browser, context, page, time.time())


async def verify_otp(chat_id: int, otp: str) -> bool:
    """مرحله ۲: OTP → ورود → ذخیره state.json"""
    sess = _login_sessions.get(chat_id)
    if not sess:
        return False

    p, browser, context, page, ts = sess
    try:
        await page.wait_for_selector(OTP_INPUT, timeout=10000)
        await page.fill(OTP_INPUT, otp)
        await page.click(AUTH_SUBMIT)

        # چک عملی لاگین
        await page.goto(DIVAR_MYDIVAR_URL, wait_until="domcontentloaded")
        html = await page.content()
        ok = ("ورود به حساب کاربری" not in html and "شمارهٔ موبایل خود را وارد کنید" not in html)

        if ok:
            await context.storage_state(path=STATE_PATH)

        return ok
    finally:
        await _close_login_session(chat_id)


async def _new_context_with_state() -> Tuple[Playwright, Browser, BrowserContext, Page]:
    p = await async_playwright().start()
    browser = await p.chromium.launch(headless=HEADLESS)

    if _state_exists():
        context = await browser.new_context(storage_state=STATE_PATH)
    else:
        context = await browser.new_context()

    page = await context.new_page()
    return p, browser, context, page


async def _set_fixed_city(page: Page) -> None:
    """✅ شهر ثابت: قبل از /new می‌ریم /s/<city> تا city روی سشن ست بشه."""
    await page.goto(DIVAR_CITY_URL, wait_until="domcontentloaded")
    await page.wait_for_timeout(800)
    await page.goto(DIVAR_NEW_URL, wait_until="domcontentloaded")


async def create_post_on_divar(
    category_index: int,
    title: str,
    description: str,
    price: str,
    image_paths: Optional[List[str]] = None,
) -> str:
    if not await has_valid_session():
        raise RuntimeError("لاگین نیستی. اول /login رو انجام بده.")

    p, browser, context, page = await _new_context_with_state()
    try:
        # ✅ شهر ثابت
        await _set_fixed_city(page)

        # --- مرحله انتخاب دسته (اگر نمایش داده شد) ---
        if await page.locator(CATEGORY_TITLE).count() > 0:
            await page.wait_for_selector(CATEGORY_ITEM, timeout=15000)
            items = page.locator(CATEGORY_ITEM)
            count = await items.count()
            if count == 0:
                raise RuntimeError("لیست دسته‌ها پیدا نشد.")
            if category_index < 0 or category_index >= count:
                raise RuntimeError(f"category_index خارج از محدوده است. (0..{count-1})")

            await items.nth(category_index).click()
            await page.wait_for_timeout(800)

        # --- مرحله عکس/عنوان/توضیح ---
        if image_paths:
            await page.wait_for_selector(IMAGES_INPUT, timeout=15000)
            await page.set_input_files(IMAGES_INPUT, image_paths)

        await page.wait_for_selector(TITLE_INPUT, timeout=15000)
        await page.fill(TITLE_INPUT, title)

        await page.wait_for_selector(DESC_INPUT, timeout=15000)
        await page.fill(DESC_INPUT, description)

        await page.click(NEXT_BTN)
        await page.wait_for_timeout(1200)

        # --- مرحله اطلاعات (price و ...) ---
        await page.wait_for_selector(PRICE_INPUT, timeout=15000)
        await page.fill(PRICE_INPUT, price)

        # اگر هنوز مکان "تعیین" بود، باید مودال مکان را هم اتومات کنیم
        if await page.locator(LOCATION_DETERMINE_BTN).count() > 0:
            raise RuntimeError(
                "بخش مکان هنوز روی «تعیین» است. "
                "برای اتوماسیون کامل باید HTML مودال انتخاب مکان را هم بفرستی."
            )

        await page.click(NEXT_BTN)
        await page.wait_for_timeout(1200)

        # --- مرحله تماس ---
        if await page.locator(CONTACT_CALL).count() > 0:
            if not await page.locator(CONTACT_CALL).is_checked():
                await page.locator(CONTACT_CALL).check()

        if await page.locator(CONTACT_CHAT).count() > 0:
            if not await page.locator(CONTACT_CHAT).is_checked():
                await page.locator(CONTACT_CHAT).check()

        if await page.locator(CALL_METHOD_DIRECT).count() > 0:
            await page.locator(CALL_METHOD_DIRECT).check()

        await page.click(FINAL_SUBMIT)
        await page.wait_for_timeout(1500)

        return "آگهی ارسال شد ✅ (تا مرحله «ثبت اطلاعات»)"
    finally:
        try:
            await context.close()
        except:
            pass
        try:
            await browser.close()
        except:
            pass
        try:
            await p.stop()
        except:
            pass
