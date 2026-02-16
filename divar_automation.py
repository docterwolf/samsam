# divar_automation.py
import os
import json
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
DIVAR_HOME = "https://divar.ir/"

# --- env ---
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
DIVAR_CITY_SLUG = os.getenv("DIVAR_CITY_SLUG", "mashhad")
DIVAR_STATE_PATH = os.getenv("DIVAR_STATE_PATH", "/var/data/state.json")

# --- selectors from your HTML ---
PHONE_INPUT = 'input[name="mobile"]'
OTP_INPUT = 'input[name="code"]'
SUBMIT_BTN = "button.auth-actions__submit-button"

# create ad page (first screen)
TITLE_INPUT = 'input[name="Title"]'
DESC_TEXTAREA = 'textarea[name="Description"]'
FIRST_NEXT_BTN = 'button[type="submit"]'

# category screen
CATEGORY_ROW = 'div[role="button"].rawButton-W5tTZw'

# third screen fields (examples from your HTML)
PRICE_INPUT = 'input[name="price"]'

# fourth screen submit
FINAL_SUBMIT_BTN = 'button[type="submit"]'

# location
LOCATION_BTN = 'button.kt-action-field'
LOCATION_SET_TEXT = "تعیین"  # label inside the location button


# ---------------- internal runtime context ----------------
_pw = None  # playwright instance
_browser: Optional[Browser] = None
_context: Optional[BrowserContext] = None

# per-chat page holder (simple)
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

    # chromium is default; playwright browser path will follow env (PLAYWRIGHT_BROWSERS_PATH)
    _browser = await _pw.chromium.launch(
        headless=HEADLESS,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-zygote",
        ],
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

    # best-effort: set city cookie if not already there
    # Diwar uses cookies like "city=mashhad" in your screenshot
    try:
        await _context.add_cookies(
            [
                {
                    "name": "city",
                    "value": DIVAR_CITY_SLUG,
                    "domain": ".divar.ir",
                    "path": "/",
                }
            ]
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

    if "page" in ctx and ctx["page"] and not ctx["page"].is_closed():
        return ctx["page"]

    page = await _context.new_page()
    ctx["page"] = page
    return page


async def _save_storage_state():
    """Persist login session to file (if disk exists)."""
    if not _context:
        return
    try:
        os.makedirs(os.path.dirname(DIVAR_STATE_PATH), exist_ok=True)
        await _context.storage_state(path=DIVAR_STATE_PATH)
    except Exception:
        pass


async def _is_logged_in(page: Page) -> bool:
    """
    Reliable check: if login modal isn't present and /new loads without forcing auth modal.
    """
    try:
        await page.goto(DIVAR_NEW_URL, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        # even if load fails, try checking DOM
        pass

    # If phone input exists, user is not logged in (login modal popped)
    if await page.locator(PHONE_INPUT).count() > 0:
        return False

    # If OTP input exists, also not logged in yet
    if await page.locator(OTP_INPUT).count() > 0:
        return False

    return True


# ===================== Public API used by bot.py =====================

async def has_valid_session() -> bool:
    """
    Global session check (uses a temp page).
    """
    await _ensure_browser()
    page = await _context.new_page()
    try:
        ok = await _is_logged_in(page)
        return ok
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def start_login(chat_id: int, phone: str) -> None:
    """
    Opens /new, fills phone, clicks 'تأیید' to request OTP.
    """
    page = await _get_page(chat_id)

    phone = phone.strip()
    # normalize digits
    phone_digits = "".join([c for c in phone if c.isdigit()])

    await page.goto(DIVAR_NEW_URL, wait_until="domcontentloaded", timeout=60000)

    # If already logged in, just return
    if await _is_logged_in(page):
        return

    # Wait for phone input
    await page.wait_for_selector(PHONE_INPUT, timeout=60000)
    mobile = page.locator(PHONE_INPUT)
    await mobile.click()
    await mobile.fill("")
    await mobile.type(phone_digits, delay=90)

    # Click confirm (same class, but at phone step text is "تأیید")
    btn = page.locator(SUBMIT_BTN).first

    # ensure it's enabled (sometimes it enables only after validation)
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
        # try anyway
        pass

    await btn.click()

    # Wait to reach OTP screen
    await page.wait_for_selector(OTP_INPUT, timeout=60000)


async def verify_otp(chat_id: int, code: str) -> bool:
    """
    Enters OTP and waits for login to complete.
    FIXED: waits until 'ورود' button becomes enabled; otherwise uses Enter fallback.
    """
    page = await _get_page(chat_id)

    code = "".join([c for c in code.strip() if c.isdigit()])[:6]
    await page.wait_for_selector(OTP_INPUT, timeout=60000)

    otp = page.locator(OTP_INPUT)
    await otp.click()
    await otp.fill("")
    # typing with delay triggers Diwar validation reliably
    await otp.type(code, delay=120)
    # blur to trigger state updates
    await page.keyboard.press("Tab")
    await asyncio.sleep(0.2)

    btn = page.locator(SUBMIT_BTN).first

    try:
        # Wait for enable
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
        # Fallback: press Enter in OTP field
        await otp.click()
        await page.keyboard.press("Enter")

    # Login success signals:
    # 1) OTP input disappears (modal closes)
    try:
        await page.wait_for_selector(OTP_INPUT, state="detached", timeout=45000)
        await _save_storage_state()
        return True
    except PlaywrightTimeoutError:
        pass

    # 2) If /new loads without showing phone/otp again
    try:
        await page.goto(DIVAR_NEW_URL, wait_until="domcontentloaded", timeout=60000)
    except Exception:
        pass

    ok = (await page.locator(PHONE_INPUT).count() == 0) and (await page.locator(OTP_INPUT).count() == 0)
    if ok:
        await _save_storage_state()
    return ok


async def create_post_on_divar(
    category_index: int,
    title: str,
    description: str,
    price: str,
    image_paths: Optional[List[str]] = None,
    chat_id: int = 0,
) -> str:
    """
    Creates an ad on divar.ir/new.
    - location: best-effort sets "Mashhad" (city cookie + tries location picker)
    - category: chooses category by index on category screen
    """
    page = await _get_page(chat_id)

    # Ensure logged in
    if not await _is_logged_in(page):
        raise RuntimeError("Not logged in. Please /login first.")

    await page.goto(DIVAR_NEW_URL, wait_until="domcontentloaded", timeout=60000)

    # ---- Screen 1: images + title + description ----
    # Upload images (optional)
    if image_paths:
        try:
            # find the first file input for images
            file_input = page.locator('input[type="file"][name="Images"]').first
            if await file_input.count() > 0:
                await file_input.set_input_files(image_paths)
        except Exception:
            # ignore image upload failures
            pass

    await page.wait_for_selector(TITLE_INPUT, timeout=60000)
    await page.locator(TITLE_INPUT).fill(title.strip())

    await page.wait_for_selector(DESC_TEXTAREA, timeout=60000)
    await page.locator(DESC_TEXTAREA).fill(description.strip())

    # click next
    await page.locator(FIRST_NEXT_BTN).first.click()

    # ---- Screen 2: select category (list) ----
    await page.wait_for_selector(CATEGORY_ROW, timeout=60000)
    cats = page.locator(CATEGORY_ROW)
    count = await cats.count()
    if count == 0:
        raise RuntimeError("Category list not found.")

    if category_index < 0 or category_index >= count:
        raise RuntimeError(f"Invalid category_index. Got {category_index}, available 0..{count-1}")

    await cats.nth(category_index).click()

    # after category pick, you move to next screen automatically OR you get the screen 3 with category+location
    # Wait for a known element on screen 3 (price or category link)
    # We'll wait for price input OR location button
    try:
        await page.wait_for_selector(LOCATION_BTN, timeout=60000)
    except PlaywrightTimeoutError:
        pass

    # ---- Screen 3: category & location & features ----
    # Best-effort set location:
    # - click "تعیین"
    # - try to search/select city "مشهد"
    await _best_effort_set_location_to_mashhad(page)

    # Fill price if present
    try:
        if await page.locator(PRICE_INPUT).count() > 0:
            await page.locator(PRICE_INPUT).click()
            await page.locator(PRICE_INPUT).fill("")
            await page.locator(PRICE_INPUT).type(str(price).strip(), delay=60)
    except Exception:
        pass

    # click next (same submit button)
    await page.locator(FIRST_NEXT_BTN).first.click()

    # ---- Screen 4: contact methods + final submit ----
    await page.wait_for_selector(FINAL_SUBMIT_BTN, timeout=60000)
    await page.locator(FINAL_SUBMIT_BTN).first.click()

    # If successful, often there is a redirect or a success page.
    await asyncio.sleep(2.0)

    await _save_storage_state()
    return "✅ آگهی ارسال شد (اگر دیوار مرحله‌ی تایید/بازبینی داشته باشد، ممکن است بعداً منتشر شود)."


# -------------------- Location helper --------------------

async def _best_effort_set_location_to_mashhad(page: Page):
    """
    The location UI can vary. We do best-effort:
    - find a location row with label 'تعیین' and click it
    - if a modal appears with search input, type 'مشهد' and click first match
    - if nothing found, we rely on city cookie set to mashhad
    """
    try:
        # Look for a button that contains "تعیین" label (location action field)
        # There might be multiple kt-action-field buttons; we select the one with label.
        buttons = page.locator(LOCATION_BTN)
        btn_count = await buttons.count()
        if btn_count == 0:
            return

        chosen = None
        for i in range(btn_count):
            b = buttons.nth(i)
            txt = (await b.inner_text()) if await b.count() else ""
            if LOCATION_SET_TEXT in txt:
                chosen = b
                break

        if chosen is None:
            # just click first location-like action field
            chosen = buttons.first

        await chosen.click()
        await asyncio.sleep(0.8)

        # Try common patterns for city selection modal
        # Search input candidates
        search_candidates = [
            'input[type="search"]',
            'input[placeholder*="جستجو"]',
            'input[placeholder*="شهر"]',
            'input[aria-label*="جستجو"]',
        ]

        search_box = None
        for sel in search_candidates:
            loc = page.locator(sel).first
            if await loc.count() > 0:
                # sometimes there are hidden inputs; ensure visible
                try:
                    if await loc.is_visible():
                        search_box = loc
                        break
                except Exception:
                    pass

        if search_box:
            await search_box.click()
            await search_box.fill("")
            await search_box.type("مشهد", delay=80)
            await asyncio.sleep(0.8)

            # Click first option that contains "مشهد"
            # We try a few generic clickable rows
            option_candidates = [
                'div[role="button"]',
                'li[role="button"]',
                'button',
                'div.kt-base-row',
            ]
            clicked = False
            for oc in option_candidates:
                opts = page.locator(f'{oc}:has-text("مشهد")')
                if await opts.count() > 0:
                    try:
                        await opts.first.click()
                        clicked = True
                        break
                    except Exception:
                        pass

            if clicked:
                await asyncio.sleep(0.8)

        # Some UIs require a confirm button
        confirm_candidates = [
            'button:has-text("تایید")',
            'button:has-text("ثبت")',
            'button:has-text("انجام")',
            'button:has-text("ادامه")',
        ]
        for cc in confirm_candidates:
            cbtn = page.locator(cc).first
            if await cbtn.count() > 0:
                try:
                    if await cbtn.is_enabled():
                        await cbtn.click()
                        break
                except Exception:
                    pass

    except Exception:
        # If location cannot be set, rely on cookies + continue.
        return
