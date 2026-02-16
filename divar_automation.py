import os
import time
from typing import Dict, Tuple, List, Optional
from playwright.async_api import async_playwright, Playwright, Browser, BrowserContext, Page

DIVAR_NEW_URL = "https://divar.ir/new"
DIVAR_MYDIVAR_URL = "https://divar.ir/my-divar"

DIVAR_CITY_SLUG = os.getenv("DIVAR_CITY_SLUG", "mashhad")
DIVAR_CITY_URL = f"https://divar.ir/s/{DIVAR_CITY_SLUG}"

STATE_PATH = os.getenv("DIVAR_STATE_PATH", "state.json")
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

# Login selectors
PHONE_INPUT = 'input[name="mobile"]'
OTP_INPUT   = 'input[name="code"]'
AUTH_SUBMIT = 'button.auth-actions__submit-button'

# Submit selectors
CATEGORY_TITLE = 'h2:has-text("انتخاب دستهٔ آگهی")'
CATEGORY_ITEM = 'div[role="button"].rawButton-W5tTZw'
IMAGES_INPUT = 'input[type="file"][name="Images"]'
TITLE_INPUT  = 'input[name="Title"]'
DESC_INPUT   = 'textarea[name="Description"]'
PRICE_INPUT  = 'input[name="price"]'
NEXT_BTN     = 'button[type="submit"]'
FINAL_SUBMIT = 'button[type="submit"]:has-text("ثبت اطلاعات")'

CONTACT_CALL = 'input[name="Contact_CallEnabled"]'
CONTACT_CHAT = 'input[name="Contact_ChatEnabled"]'
CALL_METHOD_DIRECT = 'input[name="Contact_CallMethod"][value="DIRECT_CALL"]'

LOCATION_DETERMINE_BTN = 'button.kt-action-field:has(span.kt-action-field__label:text("تعیین"))'

_login_sessions: Dict[int, Tuple[Playwright, Browser, BrowserContext, Page, float]] = {}
SESSION_TTL = 180


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

    await page.wait_for_selector(OTP_INPUT, timeout=15000)
    _login_sessions[chat_id] = (p, browser, context, page, time.time())


async def verify_otp(chat_id: int, otp: str) -> bool:
    sess = _login_sessions.get(chat_id)
    if not sess:
        return False

    p, browser, context, page, ts = sess
    try:
        await page.fill(OTP_INPUT, otp)
        await page.click(AUTH_SUBMIT)

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
    context = await browser.new_context(storage_state=STATE_PATH) if _state_exists() else await browser.new_context()
    page = await context.new_page()
    return p, browser, context, page


async def _set_fixed_city(page: Page) -> None:
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
        await _set_fixed_city(page)

        if await page.locator(CATEGORY_TITLE).count() > 0:
            items = page.locator(CATEGORY_ITEM)
            await items.first.wait_for(timeout=15000)
            count = await items.count()
            if category_index < 0 or category_index >= count:
                raise RuntimeError(f"category_index خارج از محدوده است. (0..{count-1})")
            await items.nth(category_index).click()
            await page.wait_for_timeout(800)

        if image_paths:
            await page.wait_for_selector(IMAGES_INPUT, timeout=15000)
            await page.set_input_files(IMAGES_INPUT, image_paths)

        await page.wait_for_selector(TITLE_INPUT, timeout=15000)
        await page.fill(TITLE_INPUT, title)

        await page.wait_for_selector(DESC_INPUT, timeout=15000)
        await page.fill(DESC_INPUT, description)

        await page.click(NEXT_BTN)
        await page.wait_for_timeout(1200)

        await page.wait_for_selector(PRICE_INPUT, timeout=15000)
        await page.fill(PRICE_INPUT, price)

        if await page.locator(LOCATION_DETERMINE_BTN).count() > 0:
            raise RuntimeError("مکان هنوز روی «تعیین» است. برای اتوماسیون کامل مودال مکان لازم است.")

        await page.click(NEXT_BTN)
        await page.wait_for_timeout(1200)

        if await page.locator(CONTACT_CALL).count() > 0 and not await page.locator(CONTACT_CALL).is_checked():
            await page.locator(CONTACT_CALL).check()

        if await page.locator(CONTACT_CHAT).count() > 0 and not await page.locator(CONTACT_CHAT).is_checked():
            await page.locator(CONTACT_CHAT).check()

        if await page.locator(CALL_METHOD_DIRECT).count() > 0:
            await page.locator(CALL_METHOD_DIRECT).check()

        await page.click(FINAL_SUBMIT)
        await page.wait_for_timeout(1500)

        return "آگهی ارسال شد ✅ (تا مرحله ثبت اطلاعات)"
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
