import os
import asyncio
from typing import Optional, Dict, Any, List

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

DIVAR_NEW_URL = "https://divar.ir/new"
DIVAR_MYDIVAR_URL = "https://divar.ir/my-divar"

HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
DIVAR_CITY_SLUG = os.getenv("DIVAR_CITY_SLUG", "mashhad")
DIVAR_STATE_PATH = os.getenv("DIVAR_STATE_PATH", "/var/data/state.json")

# ---- Auth selectors ----
PHONE_INPUT = 'input[name="mobile"]'
OTP_INPUT = 'input[name="code"]'
SUBMIT_BTN = "button.auth-actions__submit-button"

# ---- Logout selectors (from your inspect) ----
# The logout item is a button containing a <p> with text "خروج"
LOGOUT_BTN = 'button.kt-fullwidth-link:has(p:has-text("خروج"))'
# Some pages require opening the dropdown first (heuristic)
PROFILE_MENU_TRIGGERS = [
    'button[aria-label*="حساب"]',
    'button[aria-label*="پروفایل"]',
    'button:has(i.kt-icon-person)',
    'a:has(i.kt-icon-person)',
]

# ---- Submit selectors (from your HTML) ----
CATEGORY_TITLE = 'h2:has-text("انتخاب دستهٔ آگهی")'
CATEGORY_ITEM = 'div[role="button"].rawButton-W5tTZw'

IMAGES_INPUT = 'input[type="file"][name="Images"]'
TITLE_INPUT  = 'input[name="Title"]'
DESC_INPUT   = 'textarea[name="Description"]'
PRICE_INPUT  = 'input[name="price"]'
NEXT_BTN     = 'button[type="submit"]'
FINAL_SUBMIT = 'button[type="submit"]:has-text("ثبت اطلاعات")'

LOCATION_DETERMINE_BTN = 'button.kt-action-field:has(span.kt-action-field__label:text("تعیین"))'

# ---------------- internal runtime context ----------------
_pw = None
_browser: Optional[Browser] = None
_context: Optional[BrowserContext] = None
_chat_ctx: Dict[int, Dict[str, Any]] = {}


def _state_file_exists() -> bool:
    try:
        return os.path.exists(DIVAR_STATE_PATH) and os.path.getsize(DIVAR_STATE_PATH) > 5
    except Exception:
        return False


async def _ensure_browser():
    global _pw, _browser, _context

    if _context and _browser:
        return

    _pw = await async_playwright().start()
    _browser = await _pw.chromium.launch(
        headless=HEADLESS,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--no-zygote"],
    )

    storage_state = DIVAR_STATE_PATH if _state_file_exists() else None

    _context = await _browser.new_context(
        storage_state=storage_state,
        locale="fa-IR",
        timezone_id="Asia/Tehran",
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
        viewport={"width": 1280, "height": 860},
    )

    # best-effort set city cookie
    try:
        await _context.add_cookies(
            [{"name": "city", "value": DIVAR_CITY_SLUG, "domain": ".divar.ir", "path": "/"}]
        )
    except Exception:
        pass


def _get_ctx(chat_id: int) -> Dict[str, Any]:
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


async def _save_storage_state():
    if not _context:
        return
    try:
        os.makedirs(os.path.dirname(DIVAR_STATE_PATH), exist_ok=True)
        await _context.storage_state(path=DIVAR_STATE_PATH)
    except Exception:
        pass


async def _is_logged_in(page: Page) -> bool:
    try:
        await page.goto(DIVAR_NEW_URL, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        pass

    if await page.locator(PHONE_INPUT).count() > 0:
        return False
    if await page.locator(OTP_INPUT).count() > 0:
        return False
    return True


# ===================== Public API used by bot.py =====================

async def has_valid_session() -> bool:
    await _ensure_browser()
    page = await _context.new_page()
    try:
        return await _is_logged_in(page)
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def start_login(chat_id: int, phone: str) -> None:
    page = await _get_page(chat_id)

    phone_digits = "".join([c for c in phone.strip() if c.isdigit()])
    await page.goto(DIVAR_NEW_URL, wait_until="domcontentloaded", timeout=60000)

    if await _is_logged_in(page):
        return

    await page.wait_for_selector(PHONE_INPUT, timeout=60000)
    mobile = page.locator(PHONE_INPUT)
    await mobile.click()
    await mobile.fill("")
    await mobile.type(phone_digits, delay=90)

    btn = page.locator(SUBMIT_BTN).first
    try:
        await page.wait_for_function(
            """(sel) => {
                const b = document.querySelector(sel);
                return b && !b.disabled && !b.classList.contains('kt-button--disabled');
            }""",
            SUBMIT_BTN,
            timeout=15000,
        )
    except PlaywrightTimeoutError:
        pass

    await btn.click()
    await page.wait_for_selector(OTP_INPUT, timeout=60000)


async def verify_otp(chat_id: int, code: str) -> bool:
    page = await _get_page(chat_id)

    code = "".join([c for c in code.strip() if c.isdigit()])[:6]
    await page.wait_for_selector(OTP_INPUT, timeout=60000)

    otp = page.locator(OTP_INPUT)
    await otp.click()
    await otp.fill("")
    await otp.type(code, delay=120)

    await page.keyboard.press("Tab")
    await asyncio.sleep(0.2)

    btn = page.locator(SUBMIT_BTN).first
    try:
        await page.wait_for_function(
            """(sel) => {
                const b = document.querySelector(sel);
                return b && !b.disabled && !b.classList.contains('kt-button--disabled');
            }""",
            SUBMIT_BTN,
            timeout=20000,
        )
        await btn.click()
    except PlaywrightTimeoutError:
        await otp.click()
        await page.keyboard.press("Enter")

    # Wait for OTP modal to disappear OR /new without auth
    try:
        await page.wait_for_selector(OTP_INPUT, state="detached", timeout=45000)
        await _save_storage_state()
        return True
    except PlaywrightTimeoutError:
        pass

    try:
        await page.goto(DIVAR_NEW_URL, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        pass

    ok = (await page.locator(PHONE_INPUT).count() == 0) and (await page.locator(OTP_INPUT).count() == 0)
    if ok:
        await _save_storage_state()
    return ok


async def logout(chat_id: int) -> bool:
    """
    1) tries UI logout by clicking menu item 'خروج'
    2) clears storage state file
    3) resets browser context so next run is fresh
    """
    global _context

    page = await _get_page(chat_id)

    # Best effort: open my-divar and try to find logout button
    try:
        await page.goto(DIVAR_MYDIVAR_URL, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        pass

    # If not logged in, just clear local state
    if await page.locator(PHONE_INPUT).count() > 0 or await page.locator(OTP_INPUT).count() > 0:
        # clear local state anyway
        await _clear_local_session(chat_id)
        return True

    # Try direct logout button if dropdown already open
    try:
        if await page.locator(LOGOUT_BTN).count() > 0:
            await page.locator(LOGOUT_BTN).first.click()
            await asyncio.sleep(1.0)
        else:
            # Try to open dropdown/profile menu first (heuristic)
            opened = False
            for trig in PROFILE_MENU_TRIGGERS:
                loc = page.locator(trig).first
                if await loc.count() > 0:
                    try:
                        await loc.click()
                        await asyncio.sleep(0.6)
                        if await page.locator(LOGOUT_BTN).count() > 0:
                            opened = True
                            break
                    except Exception:
                        pass

            if opened and await page.locator(LOGOUT_BTN).count() > 0:
                await page.locator(LOGOUT_BTN).first.click()
                await asyncio.sleep(1.0)
    except Exception:
        # ignore UI logout failure; we'll still clear session
        pass

    # Always clear local state & reset context
    await _clear_local_session(chat_id)
    return True


async def _clear_local_session(chat_id: int):
    global _context

    # Close page for chat
    try:
        ctx = _get_ctx(chat_id)
        if ctx.get("page") and not ctx["page"].is_closed():
            await ctx["page"].close()
        ctx["page"] = None
    except Exception:
        pass

    # Close and reset context (cookies/session cleared)
    try:
        if _context:
            await _context.close()
    except Exception:
        pass
    _context = None

    # Delete persisted state file
    try:
        if os.path.exists(DIVAR_STATE_PATH):
            os.remove(DIVAR_STATE_PATH)
    except Exception:
        pass


async def create_post_on_divar(
    category_index: int,
    title: str,
    description: str,
    price: str,
    image_paths: Optional[List[str]] = None,
    chat_id: int = 0,
) -> str:
    page = await _get_page(chat_id)

    if not await _is_logged_in(page):
        raise RuntimeError("لاگین نیستی. اول /login رو انجام بده.")

    await page.goto(DIVAR_NEW_URL, wait_until="domcontentloaded", timeout=60000)

    # Category chooser (if shows)
    if await page.locator(CATEGORY_TITLE).count() > 0:
        items = page.locator(CATEGORY_ITEM)
        await items.first.wait_for(timeout=60000)
        count = await items.count()
        if category_index < 0 or category_index >= count:
            raise RuntimeError(f"category_index خارج از محدوده است. (0..{count-1})")
        await items.nth(category_index).click()
        await page.wait_for_timeout(800)

    # Images
    if image_paths:
        try:
            await page.wait_for_selector(IMAGES_INPUT, timeout=60000)
            await page.set_input_files(IMAGES_INPUT, image_paths)
        except Exception:
            pass

    await page.wait_for_selector(TITLE_INPUT, timeout=60000)
    await page.fill(TITLE_INPUT, title)

    await page.wait_for_selector(DESC_INPUT, timeout=60000)
    await page.fill(DESC_INPUT, description)

    await page.click(NEXT_BTN)
    await page.wait_for_timeout(1200)

    await page.wait_for_selector(PRICE_INPUT, timeout=60000)
    await page.fill(PRICE_INPUT, price)

    # Location must be handled; currently if "تعیین" exists we stop
    if await page.locator(LOCATION_DETERMINE_BTN).count() > 0:
        raise RuntimeError("مکان هنوز روی «تعیین» است. بعداً مودال مکان را اضافه می‌کنیم یا دستی ست کن.")

    await page.click(NEXT_BTN)
    await page.wait_for_timeout(1200)

    await page.wait_for_selector(FINAL_SUBMIT, timeout=60000)
    await page.click(FINAL_SUBMIT)

    await asyncio.sleep(2.0)
    await _save_storage_state()
    return "✅ آگهی ارسال شد (در صورت نیاز به بازبینی، ممکن است بعداً منتشر شود)."
