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

    await page.goto(DIVAR_NEW_URL, timeout=120000)

    await page.wait_for_selector('input[name="Title"]', timeout=1200000)
    await page.fill('input[name="Title"]', title)
    await page.fill('textarea[name="Description"]', description)

    image_adder = page.locator('input[type="file"][name="Images"]')
    await image_adder.wait_for(state="attached", timeout=1200000)
    await image_adder.set_input_files("https://s8.uupload.ir/files/aa49505504015b9df1265b50fa943237-donoghte.com__asnj.jpg")
    
    await page.click('button[type="submit"]')
    await page.wait_for_timeout(120000)

    categories = page.locator('div[role="button"].rawButton-W5tTZw')
    await categories.nth(category_index).click()

    await page.wait_for_timeout(120000)
    await page.fill('input[name="price"]', price)
    await page.click('button[type="submit"]')

    await page.wait_for_timeout(1000)
    await _save_state()

    return "✅ آگهی ارسال شد"
