import os
import asyncio
from typing import Optional, Dict, Any, List
from playwright.async_api import async_playwright, Browser, BrowserContext, Page

DIVAR_NEW_URL = "https://divar.ir/new"
DIVAR_HOME_URL = "https://divar.ir/"
DIVAR_MYDIVAR_URL = "https://divar.ir/my-divar"

HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
DIVAR_CITY_SLUG = os.getenv("DIVAR_CITY_SLUG", "mashhad")
DIVAR_STATE_PATH = os.getenv("DIVAR_STATE_PATH", "/var/data/state.json")

PHONE_INPUT = 'input[name="mobile"]'
OTP_INPUT = 'input[name="code"]'
SUBMIT_BTN = "button.auth-actions__submit-button"
LOGOUT_BTN = 'button:has-text("خروج")'

_pw = None
_browser: Optional[Browser] = None
_context: Optional[BrowserContext] = None
_chat_ctx: Dict[int, Dict[str, Any]] = {}


def _state_exists():
    return os.path.exists(DIVAR_STATE_PATH) and os.path.getsize(DIVAR_STATE_PATH) > 10


async def _ensure_browser():
    global _pw, _browser, _context

    if _context:
        return

    _pw = await async_playwright().start()
    _browser = await _pw.chromium.launch(
        headless=HEADLESS,
        args=["--no-sandbox", "--disable-dev-shm-usage"],
    )

    storage = DIVAR_STATE_PATH if _state_exists() else None

    _context = await _browser.new_context(
        storage_state=storage,
        locale="fa-IR",
        timezone_id="Asia/Tehran",
        viewport={"width": 1280, "height": 850},
    )

    try:
        await _context.add_cookies([
            {"name": "city", "value": DIVAR_CITY_SLUG, "domain": ".divar.ir", "path": "/"}
        ])
    except:
        pass


def _get_ctx(chat_id: int):
    if chat_id not in _chat_ctx:
        _chat_ctx[chat_id] = {}
    return _chat_ctx[chat_id]


async def _get_page(chat_id: int) -> Page:
    await _ensure_browser()
    ctx = _get_ctx(chat_id)

    if ctx.get("page") and not ctx["page"].is_closed():
        return ctx["page"]

    page = await _context.new_page()
    ctx["page"] = page
    return page


async def _save_state():
    if not _context:
        return
    os.makedirs(os.path.dirname(DIVAR_STATE_PATH), exist_ok=True)
    await _context.storage_state(path=DIVAR_STATE_PATH)


async def has_valid_session() -> bool:
    if not _state_exists():
        return False

    await _ensure_browser()
    page = await _context.new_page()

    try:
        await page.goto(DIVAR_NEW_URL, timeout=60000)
        if await page.locator(PHONE_INPUT).count() > 0:
            return False
        if await page.locator(OTP_INPUT).count() > 0:
            return False
        return True
    except:
        return False
    finally:
        await page.close()


# ---------------- LOGIN ----------------

async def start_login(chat_id: int, phone: str):
    page = await _get_page(chat_id)
    phone = "".join([c for c in phone if c.isdigit()])

    await page.goto(DIVAR_NEW_URL, timeout=60000)
    await page.wait_for_selector(PHONE_INPUT, timeout=60000)

    await page.fill(PHONE_INPUT, phone)
    await page.locator(SUBMIT_BTN).click()
    await page.wait_for_selector(OTP_INPUT, timeout=60000)


async def verify_otp(chat_id: int, code: str) -> bool:
    page = await _get_page(chat_id)

    code = "".join([c for c in code if c.isdigit()])[:6]
    await page.wait_for_selector(OTP_INPUT, timeout=60000)

    await page.fill(OTP_INPUT, "")
    await page.type(OTP_INPUT, code, delay=120)

    await page.keyboard.press("Enter")
    await page.wait_for_timeout(1000)

    check_login_button = page.locator("button", has_text="ورود")

    if not await check_login_button.is_visible():
        await _save_state()
        return True

    return False


# ---------------- LOGOUT FIXED ----------------

async def logout(chat_id: int) -> bool:
    global _context

    try:
        page = await _get_page(chat_id)
        await page.goto(DIVAR_MYDIVAR_URL, timeout=60000)

        # تلاش برای کلیک روی خروج
        if await LOGOUT_BTN.is_visible():
            await page.locator(LOGOUT_BTN).click()
            await page.locator(
                "div.kt-snackbar--open",
                has_text="شما از حساب خود خارج شدید."
            ).wait_for(timeout=10000)

        # پاک کردن کامل storage
        await page.evaluate("localStorage.clear()")
        await page.evaluate("sessionStorage.clear()")

    except:
        pass

    # بستن context (کوکی‌ها حذف می‌شوند)
    try:
        if _context:
            await _context.close()
    except:
        pass

    _context = None

    # حذف فایل سشن
    try:
        if os.path.exists(DIVAR_STATE_PATH):
            os.remove(DIVAR_STATE_PATH)
    except:
        pass

    return True


# ---------------- CREATE POST ----------------

async def create_post_on_divar(
    category_index: int,
    title: str,
    description: str,
    price: str,
    image_paths: Optional[List[str]] = None,
    chat_id: int = 0,
):
    page = await _get_page(chat_id)

    if not await has_valid_session():
        raise RuntimeError("لاگین نیستی.")

    await page.goto(DIVAR_NEW_URL, timeout=60000)

    await page.wait_for_selector('input[name="Title"]', timeout=60000)
    await page.fill('input[name="Title"]', title)
    await page.fill('textarea[name="Description"]', description)

    image_adder = page.locator('input[type="file"][name="Images"]')
    await image_adder.wait_for(state="attached", timeout=60000)
    await image_adder.set_input_files("data:image/jpeg;base64,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")

    await page.click('button[type="submit"]')
    await page.wait_for_timeout(1000)

    categories = page.locator('div[role="button"].rawButton-W5tTZw')
    await categories.nth(category_index).click()

    await page.wait_for_timeout(1000)
    await page.fill('input[name="price"]', price)
    await page.click('button[type="submit"]')

    await page.wait_for_timeout(1000)
    await _save_state()

    return "✅ آگهی ارسال شد"
