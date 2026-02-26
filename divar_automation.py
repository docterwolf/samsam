"""
divar_automation.py

این فایل تمام منطق اتوماسیون سایت دیوار با Playwright را نگه می‌دارد:
- ساخت/مدیریت مرورگر و Context
- ذخیره/لود سشن (storage_state)
- لاگین با شماره موبایل + OTP
- لاگ‌اوت کامل (UI + پاک کردن storage + حذف state)
- ثبت آگهی (به صورت مرحله‌ای + گزارش دقیق اینکه کجا گیر کرده)

چرا این فایل جداست؟
- bot.py فقط با تلگرام و وب‌سرویس سروکار دارد
- این فایل فقط کار «مرورگر و دیوار» را انجام می‌دهد
"""

import os
import re
import time
import asyncio
from typing import Optional, Dict, Any, List, Tuple

# Playwright async API
from playwright.async_api import (
    async_playwright,
    Browser,
    BrowserContext,
    Page,
    TimeoutError as PlaywrightTimeoutError,
)

# -------------------------------
# 1) تنظیمات و آدرس‌ها
# -------------------------------

# صفحه ساخت آگهی (ورودی اصلی ما)
DIVAR_NEW_URL = "https://divar.ir/new"

# صفحه "مای دیوار" (برای تست وضعیت ورود و همچنین خروج)
DIVAR_MYDIVAR_URL = "https://divar.ir/my-divar"

# HEADLESS = true یعنی مرورگر بدون UI اجرا می‌شود (روی Render معمولاً لازم است)
# اگر دیوار ضدبات شدید داشت، موقتاً HEADLESS=false تست کنید.
HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"

# شهر پیش‌فرض؛ دیوار بعضی چیزها را با شهر محدود می‌کند
# برای مشهد: mashhad
DIVAR_CITY_SLUG = os.getenv("DIVAR_CITY_SLUG", "mashhad")

# مسیر فایل سشن:
# روی Render اگر Persistent Disk نداری، /tmp امن‌ترین است.
DIVAR_STATE_PATH = os.getenv("DIVAR_STATE_PATH", "/tmp/divar_state.json")

# -------------------------------
# 2) سلکتورها (Selector) های مهم
# -------------------------------

# لاگین - صفحه شماره
PHONE_INPUT = 'input[name="mobile"]'            # فیلد شماره موبایل
# لاگین - صفحه کد
OTP_INPUT = 'input[name="code"]'                # فیلد کد ۶ رقمی
# دکمه تایید/ورود
SUBMIT_BTN = "button.auth-actions__submit-button"

# ثبت آگهی - صفحه اول (عکس/عنوان/توضیحات)
IMAGES_INPUT = 'input[type="file"][name="Images"]'
TITLE_INPUT = 'input[name="Title"]'
DESC_INPUT = 'textarea[name="Description"]'
NEXT_BTN = 'button[type="submit"]'              # دکمه "بعدی" در فرم‌ها (در چند صفحه مشترک است)

# صفحه انتخاب دسته
CATEGORY_TITLE = 'h2:has-text("انتخاب دستهٔ آگهی")'
CATEGORY_ITEM = 'div[role="button"].rawButton-W5tTZw'   # آیتم‌های دسته‌ها

# صفحه قیمت/ویژگی‌ها
PRICE_INPUT = 'input[name="price"]'

# مکان - اگر هنوز مکان تعیین نشده باشد، یک action-field با label "تعیین" وجود دارد
# توجه: این selector ممکن است با تغییر UI تغییر کند.
LOCATION_DETERMINE_BTN = 'button.kt-action-field:has-text("تعیین")'

# صفحه آخر - دکمه ثبت اطلاعات
FINAL_SUBMIT = 'button[type="submit"]:has-text("ثبت اطلاعات")'

# لاگ‌اوت - از HTML که دادی
LOGOUT_BTN = 'button.kt-fullwidth-link:has(p:has-text("خروج"))'


# -------------------------------
# 3) متغیرهای سراسری runtime
# -------------------------------

# Playwright engine object
_pw = None

# Browser object (chromium)
_browser: Optional[Browser] = None

# BrowserContext:
# context یعنی پروفایل مرورگر (کوکی + localStorage + …)
# با storage_state می‌توان آن را ذخیره/لود کرد.
_context: Optional[BrowserContext] = None

# برای هر chat_id یک page جدا نگه می‌داریم تا جریان کاربر قطع نشود.
_chat_ctx: Dict[int, Dict[str, Any]] = {}


# -------------------------------
# 4) ابزارهای کمکی
# -------------------------------

def _log(step: str, msg: str):
    """
    لاگ ساده برای دیدن مرحله‌ها در Render logs.
    """
    print(f"[DIVAR][{step}] {msg}")


def _normalize_digits(s: str) -> str:
    """
    فقط رقم‌ها را نگه می‌دارد (برای شماره موبایل و کد).
    """
    return re.sub(r"\D+", "", s or "")


def _state_exists() -> bool:
    """
    بررسی می‌کند فایل state واقعاً وجود دارد و خالی نیست.
    """
    try:
        return os.path.exists(DIVAR_STATE_PATH) and os.path.getsize(DIVAR_STATE_PATH) > 10
    except Exception:
        return False


async def _ensure_browser():
    """
    اگر context ساخته نشده باشد:
    - playwright را start می‌کند
    - chromium را launch می‌کند
    - context را با storage_state (اگر موجود بود) می‌سازد

    نکته:
    Context را یک بار می‌سازیم و استفاده می‌کنیم.
    """
    global _pw, _browser, _context

    # اگر context از قبل ساخته شده، کاری نمی‌کنیم
    if _context:
        return

    _log("ensure_browser", "Starting Playwright and launching Chromium...")

    # start playwright
    _pw = await async_playwright().start()

    # launch browser
    _browser = await _pw.chromium.launch(
        headless=HEADLESS,
        args=[
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--no-zygote",
        ],
    )

    # اگر فایل state هست از آن استفاده می‌کنیم
    storage_state = DIVAR_STATE_PATH if _state_exists() else None

    # ساخت context (پروفایل مرورگر)
    _context = await _browser.new_context(
        storage_state=storage_state,
        locale="fa-IR",
        timezone_id="Asia/Tehran",
        viewport={"width": 1280, "height": 860},
        # user-agent واقعی‌تر برای کاهش احتمال بلاک
        user_agent=(
            "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
        ),
    )

    # تلاش برای ست کردن کوکی شهر
    # این بخش تضمینی نیست ولی کمک می‌کند
    try:
        await _context.add_cookies(
            [{"name": "city", "value": DIVAR_CITY_SLUG, "domain": ".divar.ir", "path": "/"}]
        )
        _log("ensure_browser", f"City cookie set: {DIVAR_CITY_SLUG}")
    except Exception as e:
        _log("ensure_browser", f"Could not set city cookie: {e}")


def _get_ctx(chat_id: int) -> Dict[str, Any]:
    """
    برای هر chat_id یک dict نگه می‌داریم.
    """
    if chat_id not in _chat_ctx:
        _chat_ctx[chat_id] = {}
    return _chat_ctx[chat_id]


async def _get_page(chat_id: int) -> Page:
    """
    یک page (تب) برای chat_id می‌سازد یا همان قبلی را برمی‌گرداند.
    """
    await _ensure_browser()
    ctx = _get_ctx(chat_id)

    # اگر page قبلی وجود دارد و بسته نشده، همان را بده
    if ctx.get("page") and not ctx["page"].is_closed():
        return ctx["page"]

    # در غیر اینصورت یک صفحه جدید بساز
    page = await _context.new_page()
    ctx["page"] = page
    return page


async def _save_state():
    """
    storage_state را ذخیره می‌کند تا سشن باقی بماند.
    اگر مسیر قابل نوشتن نباشد، silently fail می‌کند و شما باید env را درست کنید.
    """
    if not _context:
        return
    try:
        os.makedirs(os.path.dirname(DIVAR_STATE_PATH), exist_ok=True)
        await _context.storage_state(path=DIVAR_STATE_PATH)
        _log("save_state", f"Saved storage_state to: {DIVAR_STATE_PATH}")
    except Exception as e:
        _log("save_state", f"Failed to save storage_state: {e}")


async def debug_dump(page: Page, step: str) -> Tuple[Optional[str], Optional[str]]:
    """
    وقتی خطا می‌خوریم، برای اینکه بفهمیم صفحه چه شکلی بوده:
    - اسکرین‌شات می‌گیریم
    - HTML را ذخیره می‌کنیم

    این فایل‌ها داخل Render در /tmp هستن:
      /tmp/divar_debug/<step>_<timestamp>.png
      /tmp/divar_debug/<step>_<timestamp>.html
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

        _log("debug_dump", f"Saved debug: {png_path} | {html_path}")
        return png_path, html_path
    except Exception as e:
        _log("debug_dump", f"Failed to dump debug files: {e}")
        return None, None


async def _is_logged_in(page: Page) -> bool:
    """
    روش عملی چک لاگین:
    - می‌رویم /new
    - اگر فیلد phone یا otp دیدیم -> لاگین نیست
    - اگر ندیدیم -> احتمالاً لاگین هست و صفحه ثبت آگهی بالا آمده

    این کار از چک کردن یک کوکی خاص مطمئن‌تر است چون ممکن است ساختار کوکی تغییر کند.
    """
    await page.goto(DIVAR_NEW_URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(1200)

    if await page.locator(PHONE_INPUT).count() > 0:
        return False
    if await page.locator(OTP_INPUT).count() > 0:
        return False
    return True


# =========================================================
# 5) توابعی که bot.py صدا می‌زند (Public API)
# =========================================================

async def has_valid_session() -> bool:
    """
    این تابع به bot می‌گوید آیا سشن معتبر است یا نه.

    نکته مهم:
    اگر فایل state وجود ندارد، سریع False می‌دهیم
    (چون یعنی چیزی برای لود سشن نداریم)
    """
    if not _state_exists():
        _log("has_valid_session", "No state file -> not logged in")
        return False

    await _ensure_browser()
    page = await _context.new_page()

    try:
        ok = await _is_logged_in(page)
        _log("has_valid_session", f"logged_in={ok}")
        return ok
    except Exception as e:
        _log("has_valid_session", f"check failed: {e}")
        return False
    finally:
        try:
            await page.close()
        except Exception:
            pass


async def start_login(chat_id: int, phone: str) -> None:
    """
    مرحله درخواست کد:
    - می‌رویم /new
    - اگر لاگین بودیم: return
    - اگر روی OTP بودیم: return (یعنی کد قبلاً درخواست شده)
    - اگر روی phone input بودیم: شماره را می‌زنیم و submit می‌کنیم و منتظر OTP می‌مانیم
    - اگر هیچکدام نبود: احتمال anti-bot یا صفحه عجیب -> debug dump + error

    این تابع فقط «درخواست کد» را انجام می‌دهد.
    """
    step = "start_login"
    page = await _get_page(chat_id)

    phone_digits = _normalize_digits(phone)

    # تبدیل +98... به 0...
    if phone_digits.startswith("98"):
        phone_digits = "0" + phone_digits[2:]

    _log(step, f"Opening /new for login. phone={phone_digits}")

    await page.goto(DIVAR_NEW_URL, wait_until="domcontentloaded", timeout=60000)
    await page.wait_for_timeout(1500)

    # اگر صفحه ثبت آگهی بالا باشد و phone/otp نباشد یعنی لاگین هست
    if await page.locator(TITLE_INPUT).count() > 0 and await page.locator(PHONE_INPUT).count() == 0:
        _log(step, "Already logged in (Title input exists).")
        return

    # اگر روی OTP هستیم یعنی درخواست کد قبلاً انجام شده
    if await page.locator(OTP_INPUT).count() > 0:
        _log(step, "Already on OTP step.")
        return

    # اگر فیلد phone وجود دارد، شماره را وارد کن
    if await page.locator(PHONE_INPUT).count() > 0:
        _log(step, "Phone input detected. Filling phone...")

        await page.fill(PHONE_INPUT, phone_digits)

        # بهتر: صبر کنیم دکمه submit از حالت disabled خارج شود
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
            _log(step, "Submit button did not become enabled quickly (continuing anyway).")

        await page.click(SUBMIT_BTN)
        _log(step, "Submit clicked. Waiting for OTP input...")

        await page.wait_for_selector(OTP_INPUT, timeout=60000)
        _log(step, "OTP input appeared.")
        return

    # هیچکدام از حالت‌های طبیعی نبود -> احتمال anti-bot
    await debug_dump(page, "login_unexpected_page")
    raise RuntimeError("صفحه ورود لود نشد یا دیوار صفحه متفاوتی نمایش داد (احتمال anti-bot).")


async def verify_otp(chat_id: int, code: str) -> bool:
    """
    مرحله تایید کد:
    - کد ۶ رقمی را در input وارد می‌کنیم
    - صبر می‌کنیم دکمه ورود enabled شود
    - کلیک می‌کنیم
    - سپس با _is_logged_in چک می‌کنیم آیا واقعا وارد شدیم
    - اگر وارد شدیم: storage_state را ذخیره می‌کنیم

    نکته:
    قبلاً مشکل این بود که دکمه "ورود" disabled می‌ماند و click timeout می‌خورد.
    اینجا با wait_for_function، منتظر enabled شدن می‌مانیم.
    """
    step = "verify_otp"
    page = await _get_page(chat_id)

    code_digits = _normalize_digits(code)[:6]
    _log(step, f"Verifying OTP: {code_digits}")

    await page.wait_for_selector(OTP_INPUT, timeout=60000)

    # پاک کردن و تایپ با delay برای طبیعی‌تر شدن
    await page.fill(OTP_INPUT, "")
    await page.type(OTP_INPUT, code_digits, delay=120)

    # صبر برای فعال شدن دکمه ورود
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
        _log(step, "Clicked submit on OTP screen.")
    except PlaywrightTimeoutError:
        # اگر دکمه enabled نشد، Enter را امتحان می‌کنیم
        _log(step, "Submit did not enable; pressing Enter fallback.")
        await page.keyboard.press("Enter")

    # تلاش برای اینکه OTP input از DOM برود
    try:
        await page.wait_for_selector(OTP_INPUT, state="detached", timeout=45000)
    except PlaywrightTimeoutError:
        _log(step, "OTP input not detached; continuing to login check...")

    # چک نهایی لاگین
    ok = False
    try:
        ok = await _is_logged_in(page)
        _log(step, f"Login check result: {ok}")
    except Exception as e:
        _log(step, f"Login check failed: {e}")

    # اگر لاگین موفق بود state را ذخیره کن
    if ok:
        await _save_state()

    return ok


async def logout(chat_id: int) -> bool:
    """
    خروج واقعی و "قوی":

    چرا فقط کلیک روی خروج کافی نیست؟
    چون ممکنه localStorage / sessionStorage یا کوکی‌ها هنوز باقی بمانند
    و چک ما دوباره فکر کند سشن معتبر است.

    بنابراین:
    1) می‌رویم /my-divar و اگر دکمه خروج دیده شد کلیک می‌کنیم
    2) localStorage و sessionStorage پاک می‌کنیم
    3) cookies را clear می‌کنیم
    4) context را می‌بندیم (حذف کامل state runtime)
    5) فایل DIVAR_STATE_PATH را حذف می‌کنیم
    """
    global _context
    step = "logout"

    _log(step, "Starting strong logout...")

    try:
        page = await _get_page(chat_id)

        # رفتن به my-divar
        try:
            await page.goto(DIVAR_MYDIVAR_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(1200)
            _log(step, "Opened my-divar.")
        except Exception as e:
            _log(step, f"Could not open my-divar: {e}")

        # کلیک روی خروج (اگر در همین صفحه قابل مشاهده باشد)
        try:
            if await page.locator(LOGOUT_BTN).count() > 0:
                await page.locator(LOGOUT_BTN).first.click()
                await page.wait_for_timeout(1000)
                _log(step, "Clicked logout button in UI.")
            else:
                _log(step, "Logout button not found in UI (will still clear storage).")
        except Exception as e:
            _log(step, f"UI logout click failed: {e}")

        # پاک کردن storage های JS
        try:
            await page.evaluate("localStorage.clear()")
            await page.evaluate("sessionStorage.clear()")
            _log(step, "Cleared localStorage/sessionStorage.")
        except Exception as e:
            _log(step, f"Failed clearing storages: {e}")

        # پاک کردن cookies از context
        try:
            if _context:
                await _context.clear_cookies()
                _log(step, "Cleared cookies.")
        except Exception as e:
            _log(step, f"Failed clearing cookies: {e}")

    except Exception as e:
        _log(step, f"General logout error: {e}")

    # بستن context برای reset کامل
    try:
        if _context:
            await _context.close()
            _log(step, "Closed browser context.")
    except Exception as e:
        _log(step, f"Failed closing context: {e}")

    _context = None

    # حذف فایل state
    try:
        if os.path.exists(DIVAR_STATE_PATH):
            os.remove(DIVAR_STATE_PATH)
            _log(step, f"Deleted state file: {DIVAR_STATE_PATH}")
    except Exception as e:
        _log(step, f"Failed deleting state file: {e}")

    # پاک کردن page handle برای این چت
    try:
        ctx = _get_ctx(chat_id)
        ctx["page"] = None
    except Exception:
        pass

    _log(step, "Logout finished.")
    return True


# =========================================================
# 6) ثبت آگهی (مرحله‌ای + مشخص کردن دقیق مشکل)
# =========================================================

async def _pick_category_from_list(page: Page, category_index: int):
    """
    از صفحه انتخاب دسته‌ها:
    - منتظر می‌مانیم آیتم‌های دسته ظاهر شوند
    - category_index ام را کلیک می‌کنیم
    """
    step = "pick_category"
    items = page.locator(CATEGORY_ITEM)

    await items.first.wait_for(timeout=60000)
    count = await items.count()

    _log(step, f"Category items count={count}")

    if count <= 0:
        raise RuntimeError("لیست دسته‌ها خالیه.")

    if category_index < 0 or category_index >= count:
        raise RuntimeError(f"category_index نامعتبره. بازه: 0..{count-1}")

    await items.nth(category_index).scroll_into_view_if_needed()
    await items.nth(category_index).click()
    _log(step, f"Clicked category index={category_index}")


async def create_post_on_divar(
    category_index: int,
    title: str,
    description: str,
    price: str,
    image_paths: Optional[List[str]] = None,
    chat_id: int = 0,
) -> str:
    """
    ثبت آگهی:
    - مرحله 1: باز کردن /new
    - مرحله 2: آپلود عکس (در دیوار معمولاً اجباری است)
    - مرحله 3: پر کردن Title
    - مرحله 4: پر کردن Description
    - مرحله 5: Next -> انتظار برای رفتن به صفحه دسته یا صفحه بعدی
    - مرحله 6: اگر صفحه دسته آمد، انتخاب دسته
    - مرحله 7: پر کردن price
    - مرحله 8: مکان (فعلاً فقط چک می‌کنیم تعیین نشده باشد؛ چون تو گفتی بعداً ادیت می‌کنی)
    - مرحله 9: Next
    - مرحله 10: ثبت اطلاعات

    نکته مهم:
    اگر هرجایی خطا بخورد:
    - screenshot + HTML ذخیره می‌کنیم
    - خطا را با "مرحله: ..." برمی‌گردانیم تا دقیق بفهمی گیر کجاست.
    """
    page = await _get_page(chat_id)

    # حتما باید لاگین باشیم
    if not await has_valid_session():
        raise RuntimeError("لاگین نیستی. اول /login کن.")

    step = "open_new"
    try:
        _log(step, "Opening /new ...")
        await page.goto(DIVAR_NEW_URL, wait_until="domcontentloaded", timeout=60000)
        await page.wait_for_timeout(1500)

        # بعضی وقت‌ها دیوار مستقیم صفحه دسته را نشان می‌دهد
        step = "maybe_category_first"
        if await page.locator(CATEGORY_TITLE).count() > 0 and await page.locator(CATEGORY_ITEM).count() > 0:
            _log(step, "Category page appeared first. Picking category...")
            await _pick_category_from_list(page, category_index)
            await page.wait_for_timeout(1200)

        # -------- Step: upload_image --------
        step = "upload_image"
        _log(step, f"Uploading images: {image_paths}")

        # چون خیلی وقت‌ها اجباری است، اگر خالی باشد همینجا خطا می‌دهیم تا شفاف باشد
        if not image_paths or len(image_paths) == 0:
            raise RuntimeError("عکس اجباریه؛ image_paths خالیه.")

        await page.wait_for_selector(IMAGES_INPUT, timeout=60000)
        await page.set_input_files(IMAGES_INPUT, image_paths)
        await page.wait_for_timeout(1500)

        # -------- Step: fill_title --------
        step = "fill_title"
        _log(step, f"Filling title: {title}")

        await page.wait_for_selector(TITLE_INPUT, timeout=60000)
        await page.fill(TITLE_INPUT, title.strip())

        # -------- Step: fill_description --------
        step = "fill_description"
        _log(step, f"Filling description: {len(description)} chars")

        await page.wait_for_selector(DESC_INPUT, timeout=60000)
        await page.fill(DESC_INPUT, description.strip())

        # -------- Step: click_next_1 --------
        step = "click_next_1"
        _log(step, "Clicking Next (page 1 -> next)")

        await page.click(NEXT_BTN)
        await page.wait_for_timeout(1500)

        # -------- Step: wait_after_next_1 --------
        step = "wait_after_next_1"
        _log(step, "Waiting for either category list OR price/location page...")

        # بعد از next ممکنه یکی از این‌ها بیاد:
        # - لیست دسته‌ها
        # - صفحه‌ای که price دارد
        # - یا block دسته/مکان (#Category)
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
            raise RuntimeError("بعد از «بعدی» هیچ صفحه مورد انتظار لود نشد (ممکن است فیلد اجباری یا خطا وجود داشته باشد).")

        # -------- Step: pick_category_if_list --------
        step = "pick_category_if_list"
        if await page.locator(CATEGORY_ITEM).count() > 0:
            _log(step, "Category list detected. Picking...")
            await _pick_category_from_list(page, category_index)
            await page.wait_for_timeout(1500)
        else:
            _log(step, "No category list detected. Continuing...")

        # -------- Step: fill_price --------
        step = "fill_price"
        _log(step, f"Filling price: {price}")

        await page.wait_for_selector(PRICE_INPUT, timeout=60000)
        await page.fill(PRICE_INPUT, "")
        await page.type(PRICE_INPUT, str(price).strip(), delay=60)

        # -------- Step: location_check --------
        step = "location_check"
        _log(step, "Checking location is set or still 'تعیین'...")

        # اینجا فعلاً مکان را اتومات نمی‌کنیم؛ فقط می‌فهمیم آیا مشکل از اینجاست یا نه
        # اگر "تعیین" باشد، یعنی مکان ست نشده و احتمالاً دیوار اجازه رفتن به مرحله بعد را ندهد.
        if await page.locator(LOCATION_DETERMINE_BTN).count() > 0:
            raise RuntimeError("مکان آگهی هنوز روی «تعیین» است (باید ست شود).")

        # -------- Step: click_next_2 --------
        step = "click_next_2"
        _log(step, "Clicking Next (price/location -> contact)")

        await page.click(NEXT_BTN)
        await page.wait_for_timeout(1500)

        # -------- Step: final_submit --------
        step = "final_submit"
        _log(step, "Submitting final info (ثبت اطلاعات)")

        if await page.locator(FINAL_SUBMIT).count() > 0:
            await page.click(FINAL_SUBMIT)
        else:
            # fallback اگر متن دکمه تغییر کرد: آخرین submit را می‌زنیم
            await page.locator('button[type="submit"]').last.click()

        await page.wait_for_timeout(2500)

        # ذخیره سشن بعد از موفقیت
        await _save_state()

        _log("success", "Post flow finished (assumed success).")
        return "✅ ثبت انجام شد (اگر دیوار بررسی کند ممکن است انتشار با تاخیر باشد)."

    except Exception as e:
        # اگر خطا شد، دیباگ dump می‌گیریم تا بفهمیم صفحه چه بوده
        png, html = await debug_dump(page, step)

        # خطا را با مرحله دقیق برمی‌گردانیم تا در تلگرام معلوم شود
        raise RuntimeError(
            "ثبت آگهی شکست خورد.\n"
            f"مرحله: {step}\n"
            f"خطا: {e}\n"
            f"Debug PNG: {png}\n"
            f"Debug HTML: {html}"
        )
