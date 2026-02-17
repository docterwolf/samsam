import os
import re
import time
import asyncio
from typing import Optional, Dict, Any, List, Tuple

from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

# ---------------- Config ----------------
DIVAR_NEW_URL = "https://divar.ir/new"
DIVAR_MYDIVAR_URL = "https://divar.ir/my-divar"

HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
DIVAR_CITY_SLUG = os.getenv("DIVAR_CITY_SLUG", "mashhad")

# ✅ Default to /tmp (Render-safe)
DIVAR_STATE_PATH = os.getenv("DIVAR_STATE_PATH", "/tmp/divar_state.json")

# ---------------- Selectors (from your inspect) ----------------
PHONE_INPUT = 'input[name="mobile"]'
OTP_INPUT = 'input[name="code"]'
SUBMIT_BTN = "button.auth-actions__submit-button"

# create post - page 1
IMAGES_INPUT = 'input[type="file"][name="Images"]'
TITLE_INPUT = 'input[name="Title"]'
DESC_INPUT = 'textarea[name="Description"]'
NEXT_BTN = 'button[type="submit"]'

# category page
CATEGORY_TITLE = 'h2:has-text("انتخاب دستهٔ آگهی")'
CATEGORY_ITEM = 'div[role="button"].rawButton-W5tTZw'

# later pages
PRICE_INPUT = 'input[name="price"]'

# location action-field label "تعیین"
LOCATION_DETERMINE_BTN = 'button.kt-action-field:has(span.kt-action-field__label:text("تعیین"))'

# final submit
FINAL_SUBMIT = 'button[type="submit"]:has-text("ثبت اطلاعات")'

# logout (from your inspect)
LOGOUT_BTN = 'button.kt-fullwidth-link:has(p:has-text("خروج"))'

# ---------------- Runtime singletons ----------------
_pw = None
_browser: Optional[Browser] = None
_context: Optional[BrowserContext] = None
_chat_ctx: Dict[int, Dict[str, Any]] = {}


def _normalize_digits(s: str) -> str:
    return re.sub(r"\D+", "", s or "")


def _state_exists() -> bool:
    try:
        return os.path.exists(DIVAR_STATE_PATH) and os.path.getsize(DIVAR_STATE_PATH) > 10
    except Exception:
        return False


async def _ensure_browser():
    global _pw, _browser, _context
    if _context:
        return

    _pw = await async_playwright().start()
    _browser = await _pw.chromium.launch(
        headless=HEADLESS,
        args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu", "--no-zygote"],
    )

    storage_state = DIVAR_STATE_PATH if _state_exists() else None

    _context = await _browser.new_context(
        storage_state=storage_state,
        locale="fa-IR",
        timezone_id="Asia/Tehran",
        viewport={"width": 1280, "height": 860},
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
    )

    # best-effort city cookie
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


async def _save_state():
    if not _context:
        return
    try:
        os.makedirs(os.path.dirname(DIVAR_STATE_PATH), exist_ok=True)
        await _context.storage_state(path=DIVAR_STATE_PATH)
    except Exception:
        # swallow, but user should set correct path
        pass


async def debug_dump(page: Page, step: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Save screenshot + HTML for debugging on Render:
      /tmp/divar_debug/<step>_<ts>.png
      /tmp/divar_debug/<step>_<ts>.html
    """
    try:
        ts = int(time.time())
        folder = "/tmp/divar_debug"
        os.makedirs(folder, exist_ok=True)

        png_path = f"{folder}/{step}_{ts}.png"
        html_path = f"{folder}/{step}_{ts}.html"

        await page.screenshot(path=png_path, full_page=True)
        content = await page.content()
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(content)

        return png_path, html_path
    except Exception:
        return None, None


async def _is_logged_in(page: Page) -> bool:
    # A practical check: if /new shows login inputs -> not logged in
    await page.goto(DIVAR_NEW_URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(1200)

    if await page.locator(PHONE_INPUT).count() > 0:
        return False
    if await page.locator(OTP_INPUT).count() > 0:
        return False
    return True


# ===================== Public API for bot.py =====================

async def has_valid_session() -> bool:
    # If no persisted state, treat as not logged in
    if not _state_exists():
        return False

    await _ensure_browser()
    page = await _context.new_page()
    try:
        return await _is_logged_in(page)
    except Exception:
        return False
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def start_login(chat_id: int, phone: str) -> None:
    """
    Opens /new and requests OTP for phone (if login screen exists).
    Handles cases:
      - already logged in
      - already on OTP screen
      - anti-bot/blank page
    """
    page = await _get_page(chat_id)
    phone_digits = _normalize_digits(phone)

    # normalize +98... to 0...
    if phone_digits.startswith("98"):
        phone_digits = "0" + phone_digits[2:]

    await page.goto(DIVAR_NEW_URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(1500)

    # already logged in?
    if await page.locator(TITLE_INPUT).count() > 0 and await page.locator(PHONE_INPUT).count() == 0:
        return

    # already at OTP screen?
    if await page.locator(OTP_INPUT).count() > 0:
        return

    # phone screen
    if await page.locator(PHONE_INPUT).count() > 0:
        await page.fill(PHONE_INPUT, phone_digits)

        # wait submit enabled (best-effort)
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

        await page.click(SUBMIT_BTN)

        await page.wait_for_selector(OTP_INPUT, timeout=60000)
        return

    # otherwise weird page
    await debug_dump(page, "login_unexpected_page")
    raise RuntimeError("صفحه ورود لود نشد یا دیوار صفحه متفاوتی نمایش داد (احتمال anti-bot).")


async def verify_otp(chat_id: int, code: str) -> bool:
    """
    Enters OTP and completes login, then saves storage state.
    Includes wait-for-enabled on submit button to avoid disabled click timeout.
    """
    page = await _get_page(chat_id)
    code_digits = _normalize_digits(code)[:6]

    await page.wait_for_selector(OTP_INPUT, timeout=60000)
    await page.fill(OTP_INPUT, "")
    await page.type(OTP_INPUT, code_digits, delay=120)

    # wait submit enabled (important)
    try:
        await page.wait_for_function(
            """(sel) => {
                const b = document.querySelector(sel);
                return b && !b.disabled && !b.classList.contains('kt-button--disabled');
            }""",
            SUBMIT_BTN,
            timeout=25000,
        )
        await page.click(SUBMIT_BTN)
    except PlaywrightTimeoutError:
        # fallback enter
        await page.keyboard.press("Enter")

    # wait OTP input gone OR /new without login fields
    try:
        await page.wait_for_selector(OTP_INPUT, state="detached", timeout=45000)
    except PlaywrightTimeoutError:
        pass

    # final check
    try:
        ok = await _is_logged_in(page)
        if ok:
            await _save_state()
        return ok
    except Exception:
        return False


async def logout(chat_id: int) -> bool:
    """
    Strong logout:
      - try UI 'خروج' if possible
      - clear localStorage/sessionStorage
      - clear cookies
      - close context
      - delete persisted state file
    """
    global _context

    try:
        page = await _get_page(chat_id)
        # go my-divar; try logout button if menu already open
        try:
            await page.goto(DIVAR_MYDIVAR_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(1200)
        except Exception:
            pass

        # try click logout if exists
        try:
            if await page.locator(LOGOUT_BTN).count() > 0:
                await page.locator(LOGOUT_BTN).first.click()
                await page.wait_for_timeout(1000)
        except Exception:
            pass

        # clear storages
        try:
            await page.evaluate("localStorage.clear()")
            await page.evaluate("sessionStorage.clear()")
        except Exception:
            pass

        # clear cookies via context if still alive
        try:
            if _context:
                await _context.clear_cookies()
        except Exception:
            pass

    except Exception:
        pass

    # close context entirely (strong reset)
    try:
        if _context:
            await _context.close()
    except Exception:
        pass
    _context = None

    # delete state file
    try:
        if os.path.exists(DIVAR_STATE_PATH):
            os.remove(DIVAR_STATE_PATH)
    except Exception:
        pass

    # also drop per-chat page handle
    try:
        ctx = _get_ctx(chat_id)
        ctx["page"] = None
    except Exception:
        pass

    return True


# ===================== Post creation with step-by-step debug =====================

async def _pick_category_from_list(page: Page, category_index: int):
    items = page.locator(CATEGORY_ITEM)
    await items.first.wait_for(timeout=60000)
    count = await items.count()
    if count <= 0:
        raise RuntimeError("لیست دسته‌ها خالیه.")
    if category_index < 0 or category_index >= count:
        raise RuntimeError(f"category_index نامعتبره. بازه: 0..{count-1}")
    await items.nth(category_index).scroll_into_view_if_needed()
    await items.nth(category_index).click()


async def create_post_on_divar(
    category_index: int,
    title: str,
    description: str,
    price: str,
    image_paths: Optional[List[str]] = None,
    chat_id: int = 0,
) -> str:
    """
    Creates post and tells exactly which step failed + saves debug files.
    NOTE: Location step is not automated here yet.
          If location is still 'تعیین', it will stop and report that step.
    """
    page = await _get_page(chat_id)

    # must be logged in
    if not await has_valid_session():
        raise RuntimeError("لاگین نیستی. اول /login کن.")

    step = "open_new"
    try:
        await page.goto(DIVAR_NEW_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1500)

        # Sometimes category screen appears first
        step = "maybe_category_first"
        if await page.locator(CATEGORY_TITLE).count() > 0 and await page.locator(CATEGORY_ITEM).count() > 0:
            await _pick_category_from_list(page, category_index)
            await page.wait_for_timeout(1200)

        # -------- Step 1: Upload image (required by divar often) --------
        step = "upload_image"
        if image_paths and len(image_paths) > 0:
            await page.wait_for_selector(IMAGES_INPUT, timeout=60000)
            await page.set_input_files(IMAGES_INPUT, image_paths)
            await page.wait_for_timeout(1500)
        else:
            # Still allow, but warn by failing early (better for debugging)
            raise RuntimeError("عکس اجباریه؛ image_paths خالیه.")

        # -------- Step 2: Fill title --------
        step = "fill_title"
        await page.wait_for_selector(TITLE_INPUT, timeout=60000)
        await page.fill(TITLE_INPUT, title.strip())

        # -------- Step 3: Fill description --------
        step = "fill_description"
        await page.wait_for_selector(DESC_INPUT, timeout=60000)
        await page.fill(DESC_INPUT, description.strip())

        # -------- Step 4: Click next (go to category list or next flow) --------
        step = "click_next_1"
        await page.click(NEXT_BTN)
        await page.wait_for_timeout(1500)

        # -------- Step 5: Wait for either category list OR price/location page --------
        step = "wait_after_next_1"
        try:
            await page.wait_for_function(
                """() => {
                    const catList = document.querySelector('div[role="button"].rawButton-W5tTZw');
                    const price = document.querySelector('input[name="price"]');
                    const categoryBlock = document.querySelector('#Category');
                    return !!(catList || price || categoryBlock);
                }""",
                timeout=60000,
            )
        except PlaywrightTimeoutError:
            raise RuntimeError("بعد از «بعدی» صفحه مورد انتظار لود نشد.")

        # -------- Step 6: If category list exists, pick category --------
        step = "pick_category_if_list"
        if await page.locator(CATEGORY_ITEM).count() > 0:
            await _pick_category_from_list(page, category_index)
            await page.wait_for_timeout(1500)

        # -------- Step 7: Fill price --------
        step = "fill_price"
        await page.wait_for_selector(PRICE_INPUT, timeout=60000)
        await page.fill(PRICE_INPUT, "")
        await page.type(PRICE_INPUT, str(price).strip(), delay=60)

        # -------- Step 8: Location check (not automated yet) --------
        step = "location_check"
        # if still تعیین -> stop and tell user this is the blocker
        if await page.locator(LOCATION_DETERMINE_BTN).count() > 0:
            raise RuntimeError("مکان آگهی هنوز روی «تعیین» است (باید ست شود).")

        # -------- Step 9: Next to contact page --------
        step = "click_next_2"
        await page.click(NEXT_BTN)
        await page.wait_for_timeout(1500)

        # -------- Step 10: Final submit --------
        step = "final_submit"
        if await page.locator(FINAL_SUBMIT).count() > 0:
            await page.click(FINAL_SUBMIT)
        else:
            # fallback: last submit
            await page.locator('button[type="submit"]').last.click()

        await page.wait_for_timeout(2500)

        await _save_state()
        return "✅ ثبت انجام شد (اگر دیوار نیاز به بررسی داشته باشد ممکن است با تاخیر منتشر شود)."

    except Exception as e:
        png, html = await debug_dump(page, step)
        raise RuntimeError(
            "ثبت آگهی شکست خورد.\n"
            f"مرحله: {step}\n"
            f"خطا: {e}\n"
            f"Debug PNG: {png}\n"
            f"Debug HTML: {html}"
        )
