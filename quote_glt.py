import argparse
import math
import os
import re
import time
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from quote_total import SHEET_NAME, gs_client, read_input

load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=True)

STEP_DELAY_MS = 450
GLT_DEFAULT_LOGIN_URL = "https://myportal.goglt.com/login"


def env_first(*names):
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return ""


def has_value(value) -> bool:
    return value is not None and str(value).strip() != ""


def normalize_space(value: str) -> str:
    return " ".join((value or "").split())


def wait_after_step(page, multiplier=1):
    page.wait_for_timeout(STEP_DELAY_MS * multiplier)


def current_body_text(page) -> str:
    try:
        return normalize_space(page.locator("body").inner_text())
    except Exception:
        return ""


def normalized_login_url() -> str:
    url = env_first("GLT_LOGIN_URL", "LOGIN_URL") or GLT_DEFAULT_LOGIN_URL
    if url and not url.startswith("http"):
        url = "https://" + url.lstrip("/")
    return url


def integer_string(value, *, round_up: bool = False) -> str:
    if not has_value(value):
        return ""
    try:
        number = float(str(value).strip())
    except Exception:
        return str(value).strip()
    if round_up:
        return str(int(math.ceil(number)))
    return str(int(number))


def quote_date_string(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    match = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", raw)
    if match:
        year, month, day = match.groups()
        return f"{month}-{day}-{year}"
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", raw)
    if match:
        month, day, year = match.groups()
        return f"{int(month):02d}-{int(day):02d}-{year}"
    return raw


def set_input_value(locator, value: str):
    locator.wait_for(state="visible", timeout=20000)
    locator.scroll_into_view_if_needed()
    locator.click(force=True)
    try:
        locator.fill("")
        locator.fill(str(value))
    except Exception:
        locator.evaluate(
            """
            (el, val) => {
                el.focus();
                el.value = '';
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.value = String(val ?? '');
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                el.blur();
            }
            """,
            str(value),
        )
    wait_after_step(locator.page)


def first_present(page, selectors):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() > 0:
                return locator
        except Exception:
            pass
    return page.locator("xpath=/*[false()]")


def first_visible(page, selectors):
    for selector in selectors:
        locator = page.locator(selector)
        try:
            count = locator.count()
        except Exception:
            count = 0
        for idx in range(count):
            candidate = locator.nth(idx)
            try:
                if candidate.is_visible():
                    return candidate
            except Exception:
                pass
    return page.locator("xpath=/*[false()]")


def first_visible_in(scope, selectors):
    for selector in selectors:
        locator = scope.locator(selector)
        try:
            count = locator.count()
        except Exception:
            count = 0
        for idx in range(count):
            candidate = locator.nth(idx)
            try:
                if candidate.is_visible():
                    return candidate
            except Exception:
                pass
    return scope.locator("xpath=/*[false()]")


def glt_line_items(data: dict):
    pallet_items = data.get("pallet_items") or []
    if pallet_items:
        items = []
        for item in pallet_items:
            items.append(
                {
                    "units": "1",
                    "type": "Pallet",
                    "weight": integer_string(item.get("weight"), round_up=True),
                    "length": integer_string(item.get("length"), round_up=True),
                    "width": integer_string(item.get("width"), round_up=True),
                    "height": integer_string(item.get("height"), round_up=True),
                    "freight_class": str(item.get("freight_class") or data.get("freight_class") or "").strip(),
                    "commodity": "Sheet Metal Parts",
                }
            )
        return items

    return [
        {
            "units": integer_string(data.get("pallets") or "1") or "1",
            "type": "Pallet",
            "weight": integer_string(data.get("weight"), round_up=True),
            "length": integer_string(data.get("length"), round_up=True),
            "width": integer_string(data.get("width"), round_up=True),
            "height": integer_string(data.get("height"), round_up=True),
            "freight_class": str(data.get("freight_class") or "").strip(),
            "commodity": "Sheet Metal Parts",
        }
    ]


def login_complete(page) -> bool:
    url = page.url or ""
    body_text = current_body_text(page)
    return (
        "/quote-book/" in url
        or "/home" in url
        or "BOOK A LOAD" in body_text
        or ("Book a Load" in body_text and "My Loads" in body_text)
        or "Welcome," in body_text
    )


def glt_quote_form_ready(page) -> bool:
    try:
        pickup = page.locator("input[placeholder='Pickup']:visible").first
        delivery = page.locator("input[placeholder='Delivery']:visible").first
        if pickup.count() > 0 and delivery.count() > 0 and pickup.is_visible() and delivery.is_visible():
            return True
    except Exception:
        pass

    url = page.url or ""
    body_text = current_body_text(page)
    if "/quote-book/" in url and "Pickup Zip Code or Location Name" in body_text and "Delivery Zip Code or Location Name" in body_text:
        return True
    return False


def do_login(page, username: str, password: str):
    email_selectors = [
        "input[type='email']",
        "input[name='email']",
        "input.login-submit__input",
        "input[autocomplete='username']",
        "xpath=//label[contains(., 'Email')]/following::input[1]",
    ]
    password_selectors = [
        "input[type='password']",
        "input[name='password']",
        "input.input-password",
        "input[autocomplete='current-password']",
        "xpath=//label[contains(., 'Password')]/following::input[1]",
    ]
    login_selectors = [
        "button.login-button-container__button",
        "xpath=//button[contains(., 'Login')]",
        "text=/^\\s*Login\\s*$/i",
        "button[type='submit']",
    ]

    for attempt in range(2):
        page.goto(normalized_login_url(), wait_until="domcontentloaded", timeout=120000)

        email_input = page.locator("xpath=/*[false()]")
        password_input = page.locator("xpath=/*[false()]")
        login_button = page.locator("xpath=/*[false()]")

        for _ in range(30):
            if login_complete(page):
                page.wait_for_timeout(1500)
                return

            email_input = first_visible(page, email_selectors)
            password_input = first_visible(page, password_selectors)
            login_button = first_visible(page, login_selectors)
            if email_input.count() > 0 and password_input.count() > 0 and login_button.count() > 0:
                break

            try:
                page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass
            page.wait_for_timeout(1000)

        if email_input.count() == 0 or password_input.count() == 0 or login_button.count() == 0:
            Path("glt_login_controls_missing.html").write_text(page.content(), encoding="utf-8")
            page.screenshot(path="glt_login_controls_missing.png", full_page=True)
            raise RuntimeError("Could not find GLT login controls. Saved glt_login_controls_missing.png and glt_login_controls_missing.html")

        set_input_value(email_input, username)
        set_input_value(password_input, password)

        try:
            email_value = normalize_space(email_input.input_value())
        except Exception:
            email_value = ""
        try:
            password_value = password_input.input_value()
        except Exception:
            password_value = ""
        if email_value != normalize_space(username) or not password_value:
            page.screenshot(path="glt_login_value_commit_failed.png", full_page=True)
            Path("glt_login_value_commit_failed.html").write_text(page.content(), encoding="utf-8")
            raise RuntimeError(
                "GLT login fields did not keep their values. "
                "Saved glt_login_value_commit_failed.png and glt_login_value_commit_failed.html"
            )

        login_button.scroll_into_view_if_needed()
        login_button.click(force=True)
        page.wait_for_timeout(750)
        try:
            password_input.press("Enter")
        except Exception:
            pass

        retried_click = False
        for idx in range(30):
            if login_complete(page):
                page.wait_for_timeout(5000)
                return
            try:
                page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass
            if idx >= 8 and not retried_click:
                try:
                    login_button.evaluate("(el) => el.click()")
                    retried_click = True
                except Exception:
                    pass
            page.wait_for_timeout(1000)

    Path("glt_login_failed.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path="glt_login_failed.png", full_page=True)
    raise RuntimeError("GLT login failed. Saved glt_login_failed.png and glt_login_failed.html")


def goto_book_load(page):
    if glt_quote_form_ready(page):
        return

    page.wait_for_timeout(3000)

    attempts = [
        (
            "sidebar Book a Load",
            lambda: first_present(
                page,
                [
                    "#sidebar\\.quoteBook-menu",
                    "xpath=//*[@id='sidebar.quoteBook-menu']",
                    "xpath=//div[@id='sidebar.quoteBook-menu']",
                    "xpath=//span[contains(normalize-space(.), 'Book a Load')]/ancestor::*[@id='sidebar.quoteBook-menu'][1]",
                    "text=/^\\s*Book a Load\\s*$/i",
                ],
            ).click(force=True),
        ),
        (
            "direct quote-book route",
            lambda: page.goto("https://myportal.goglt.com/quote-book/all-options", wait_until="domcontentloaded", timeout=120000),
        ),
    ]

    for _, action in attempts:
        try:
            action()
        except Exception:
            continue

        for _ in range(25):
            if glt_quote_form_ready(page):
                page.wait_for_timeout(750)
                return
            try:
                page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass
            page.wait_for_timeout(1000)

    Path("glt_book_load_missing.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path="glt_book_load_missing.png", full_page=True)
    raise RuntimeError("Could not reach GLT Book a Load form. Saved glt_book_load_missing.png and glt_book_load_missing.html")


def city_terms(preferred_city: str):
    normalized = normalize_space(preferred_city)
    if not normalized:
        return []
    terms = [normalized.upper()]
    city_only = normalize_space(normalized.split(",")[0]).upper()
    if city_only and city_only not in terms:
        terms.append(city_only)
    return [term for term in terms if len(term) > 2]


def location_target_text(preferred_city: str, zip_code: str):
    city = normalize_space(preferred_city)
    zip_norm = normalize_space(zip_code)
    if not city:
        return ""
    if zip_norm and zip_norm in city:
        return city.upper()
    if "," in city:
        return f"{city}, {zip_norm}".strip(", ").upper()
    return f"{city}, CA, {zip_norm}".strip(", ").upper()


def location_value_matches(value: str, zip_code: str, preferred_city: str = ""):
    text_norm = normalize_space(value).upper()
    if not text_norm:
        return False
    target_text = location_target_text(preferred_city, zip_code)
    if target_text and target_text in text_norm:
        return True
    preferred_terms = city_terms(preferred_city)
    if preferred_terms:
        return any(term in text_norm for term in preferred_terms)
    if zip_code and zip_code in text_norm:
        return True
    return False


def type_location_zip(locator, page, zip_code: str):
    locator.wait_for(state="attached", timeout=20000)
    locator.scroll_into_view_if_needed()
    locator.click(force=True)
    try:
        locator.evaluate(
            """
            (el) => {
                el.removeAttribute('readonly');
                el.focus();
                el.value = '';
                el.dispatchEvent(new Event('input', { bubbles: true }));
            }
            """
        )
    except Exception:
        pass
    for hotkey in ("Meta+A", "Control+A"):
        try:
            locator.press(hotkey)
            break
        except Exception:
            pass
    try:
        locator.press("Backspace")
    except Exception:
        pass
    page.wait_for_timeout(250)
    page.keyboard.type(str(zip_code), delay=120)
    page.wait_for_timeout(900)


def commit_autocomplete_selection(locator, page, zip_code: str, preferred_city: str = ""):
    for _ in range(5):
        try:
            current_value = normalize_space(locator.input_value())
        except Exception:
            current_value = ""
        if location_value_matches(current_value, zip_code, preferred_city):
            return True

        selected_item = first_visible(
            page,
            [
                ".el-select-dropdown:not([style*='display: none']) .el-select-dropdown__item.selected",
                ".el-select-dropdown:not([style*='display: none']) li.selected",
            ],
        )
        try:
            if selected_item.count() > 0:
                selected_item.click(force=True)
        except Exception:
            pass
        try:
            locator.press("Enter")
        except Exception:
            pass
        page.wait_for_timeout(400)
        try:
            locator.evaluate("(el) => el.blur()")
        except Exception:
            pass
        page.wait_for_timeout(400)

    try:
        current_value = normalize_space(locator.input_value())
    except Exception:
        current_value = ""
    return location_value_matches(current_value, zip_code, preferred_city)


def pick_autocomplete_option(page, zip_code: str, preferred_city: str = ""):
    candidates = page.locator(
        ".el-select-dropdown:not([style*='display: none']) li, "
        ".el-select-dropdown:not([style*='display: none']) [role='option'], "
        ".el-select-dropdown:not([style*='display: none']) .el-select-dropdown__item"
    )
    preferred_terms = city_terms(preferred_city)
    target_text = location_target_text(preferred_city, zip_code)
    city_only = preferred_terms[1] if len(preferred_terms) > 1 else (preferred_terms[0] if preferred_terms else "")

    def score(text: str):
        text_norm = normalize_space(text).upper()
        points = 0
        if target_text and target_text == text_norm:
            points += 20
        elif target_text and target_text in text_norm:
            points += 12
        if zip_code and zip_code in text_norm:
            points += 2
        if preferred_terms and any(term in text_norm for term in preferred_terms):
            points += 6
        if preferred_terms and preferred_terms[0] in text_norm:
            points += 4
        return points

    best = None
    best_score = 0
    try:
        count = candidates.count()
    except Exception:
        count = 0
    for idx in range(count):
        candidate = candidates.nth(idx)
        try:
            if not candidate.is_visible():
                continue
            text = candidate.inner_text()
        except Exception:
            continue
        candidate_score = score(text)
        if candidate_score > best_score:
            best = candidate
            best_score = candidate_score

    if target_text:
        exact_match = None
        for idx in range(count):
            candidate = candidates.nth(idx)
            try:
                if not candidate.is_visible():
                    continue
                text = normalize_space(candidate.inner_text()).upper()
            except Exception:
                continue
            if text == target_text:
                exact_match = candidate
                break
        if exact_match is not None:
            try:
                exact_match.click(force=True)
                wait_after_step(page, 2)
                return True
            except Exception:
                pass

    if city_only:
        city_match = None
        city_match_score = 0
        for idx in range(count):
            candidate = candidates.nth(idx)
            try:
                if not candidate.is_visible():
                    continue
                text = normalize_space(candidate.inner_text()).upper()
            except Exception:
                continue
            if city_only not in text:
                continue
            candidate_score = score(text)
            if candidate_score > city_match_score:
                city_match = candidate
                city_match_score = candidate_score
        if city_match is not None:
            try:
                city_match.click(force=True)
                wait_after_step(page, 2)
                return True
            except Exception:
                pass

    if best is not None and best_score > 0:
        try:
            best.click(force=True)
            wait_after_step(page, 2)
            return True
        except Exception:
            pass

    if city_only:
        return False

    suggestions = [
        f".el-select-dropdown:not([style*='display: none']) [role='option']:has-text('{zip_code}')",
        f".el-select-dropdown:not([style*='display: none']) li:has-text('{zip_code}')",
        f"[role='option']:has-text('{zip_code}')",
        f"li:has-text('{zip_code}')",
        f"div:has-text('{zip_code}')",
    ]
    for selector in suggestions:
        locator = page.locator(selector).first
        try:
            if locator.count() > 0 and locator.is_visible():
                locator.click(force=True)
                wait_after_step(page, 2)
                return True
        except Exception:
            pass
    return False


def fill_location_input(locator, zip_code: str, page, preferred_city: str = ""):
    typed = False
    for _ in range(3):
        type_location_zip(locator, page, zip_code)
        try:
            autocomplete_id = locator.evaluate('(el) => el.closest(".autocomplete")?.id || ""')
            current_locator = locator.page.locator(f"#{autocomplete_id} input[name='localValue']").first
        except Exception:
            current_locator = locator
        try:
            value = normalize_space(current_locator.input_value())
        except Exception:
            value = ""
        if zip_code and zip_code in value:
            typed = True
            break
        try:
            current_locator.click(force=True)
        except Exception:
            pass

    if not typed:
        Path(f"glt_location_{zip_code}_typing_failed.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=f"glt_location_{zip_code}_typing_failed.png", full_page=True)
        raise RuntimeError(
            f"GLT did not keep typed ZIP {zip_code} in the location field. "
            f"Saved glt_location_{zip_code}_typing_failed.png and glt_location_{zip_code}_typing_failed.html"
        )

    page.wait_for_timeout(1200)

    for _ in range(25):
        if pick_autocomplete_option(page, str(zip_code), preferred_city):
            page.wait_for_timeout(600)
            if commit_autocomplete_selection(locator, page, zip_code, preferred_city):
                return
        try:
            locator.press("ArrowDown")
            page.wait_for_timeout(200)
            locator.press("Enter")
            page.wait_for_timeout(600)
            if commit_autocomplete_selection(locator, page, zip_code, preferred_city):
                return
        except Exception:
            pass
        page.wait_for_timeout(600)

    try:
        final_value = normalize_space(locator.input_value())
    except Exception:
        final_value = ""
    if not location_value_matches(final_value, zip_code, preferred_city):
        safe_name = re.sub(r"[^a-z0-9]+", "_", preferred_city.lower() or zip_code.lower()).strip("_") or "location"
        Path(f"glt_{safe_name}_selection_failed.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path=f"glt_{safe_name}_selection_failed.png", full_page=True)
        raise RuntimeError(
            f"GLT did not confirm a selected location for ZIP {zip_code} ({preferred_city or 'no preferred city'}). "
            f"Saved glt_{safe_name}_selection_failed.png and glt_{safe_name}_selection_failed.html"
        )

    page.wait_for_timeout(600)
    try:
        locator.press("Tab")
    except Exception:
        pass
    page.wait_for_timeout(600)


def resolve_location_input(page, selectors, field_name: str):
    for _ in range(35):
        locator = first_visible(page, selectors)
        try:
            if locator.count() > 0 and locator.is_visible():
                locator.scroll_into_view_if_needed()
                return locator
        except Exception:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=1500)
        except Exception:
            pass
        page.wait_for_timeout(1000)

    Path(f"glt_{field_name}_input_missing.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path=f"glt_{field_name}_input_missing.png", full_page=True)
    raise RuntimeError(
        f"Could not find GLT {field_name} input after waiting for the page to settle. "
        f"Saved glt_{field_name}_input_missing.png and glt_{field_name}_input_missing.html"
    )


def set_checkbox_by_label(page, label_text: str, checked: bool = True):
    label = first_present(
        page,
        [
            f"xpath=//label[contains(normalize-space(.), '{label_text}')]",
            f"text=/{re.escape(label_text)}/i",
        ],
    )
    if label.count() == 0:
        return False
    try:
        target = label.locator("xpath=.//input[@type='checkbox']").first
        if target.count() > 0:
            if target.is_checked() != checked:
                label.click(force=True)
                wait_after_step(page)
            return True
    except Exception:
        pass
    label.click(force=True)
    wait_after_step(page)
    return True


def choose_select_value(locator, desired_text: str):
    if locator.count() == 0 or not desired_text:
        return False

    try:
        locator.select_option(label=desired_text)
        wait_after_step(locator.page)
        return True
    except Exception:
        pass

    options = locator.locator("option")
    for idx in range(options.count()):
        text = normalize_space(options.nth(idx).inner_text())
        if text.upper() == desired_text.upper() or desired_text.upper() in text.upper():
            try:
                locator.select_option(label=text)
                wait_after_step(locator.page)
                return True
            except Exception:
                pass
    return False


def commit_text_choice(page, locator, desired_text: str):
    desired = normalize_space(desired_text)
    if locator.count() == 0 or not desired:
        return False

    for _ in range(5):
        set_input_value(locator, desired)
        try:
            locator.click(force=True)
        except Exception:
            pass
        page.wait_for_timeout(300)

        option = first_visible(
            page,
            [
                f".el-select-dropdown:not([style*='display: none']) .el-select-dropdown__item:has-text('{desired}')",
                f".el-select-dropdown:not([style*='display: none']) li:has-text('{desired}')",
                f"text=/^\\s*{re.escape(desired)}\\s*$/i",
                f"text=/{re.escape(desired)}/i",
            ],
        )
        try:
            if option.count() > 0:
                option.click(force=True)
                page.wait_for_timeout(400)
        except Exception:
            pass

        for key in ("ArrowDown", "Enter", "Tab"):
            try:
                locator.press(key)
                page.wait_for_timeout(250)
            except Exception:
                pass

        try:
            locator.evaluate("(el) => el.blur()")
        except Exception:
            pass
        page.wait_for_timeout(300)

        try:
            current = normalize_space(locator.input_value())
        except Exception:
            current = ""
        if desired.upper() in current.upper():
            return True

    return False


def line_row(page, index: int):
    text_loc = page.get_by_text(f"Line {index + 1}", exact=True)
    if text_loc.count() > 0:
        return text_loc.first.locator("xpath=ancestor::*[self::div or self::section][1]")
    return page.locator("xpath=//*[contains(normalize-space(.), 'Commodities catalogue')]/following::*[self::div or self::section][1]")


def wait_for_glt_loading(scope, timeout_ms: int = 20000):
    page = scope.page if hasattr(scope, "page") else scope
    deadline = time.monotonic() + (timeout_ms / 1000.0)
    while time.monotonic() < deadline:
        masks = scope.locator(".el-loading-mask")
        visible = False
        try:
            for idx in range(masks.count()):
                if masks.nth(idx).is_visible():
                    visible = True
                    break
        except Exception:
            visible = False
        if not visible:
            return
        page.wait_for_timeout(250)


def fill_line_row(page, index: int, item: dict):
    row = line_row(page, index)
    row.wait_for(timeout=20000)

    units_input = first_visible_in(row, [".hucount .rc-form-group input"])
    type_input = first_visible_in(row, [".hucount__dropdown .rc-form-group input"])
    weight_input = first_visible_in(row, [".weight__input .rc-form-group input"])
    dimension_inputs = row.locator(".dimensions__input .rc-form-group input")
    commodity_input = first_visible_in(row, [".commodity__input .rc-form-group input"])
    nmfc_select = first_visible_in(row, [".nmfc__dropdown .rc-select", ".nmfc__dropdown"])

    if (
        units_input.count() == 0
        or type_input.count() == 0
        or weight_input.count() == 0
        or dimension_inputs.count() < 3
        or commodity_input.count() == 0
        or nmfc_select.count() == 0
    ):
        page.screenshot(path="glt_line_inputs_missing.png", full_page=True)
        Path("glt_line_inputs_missing.html").write_text(page.content(), encoding="utf-8")
        raise RuntimeError("Could not find GLT line item inputs. Saved glt_line_inputs_missing.png and glt_line_inputs_missing.html")

    set_input_value(units_input, item["units"])
    commit_text_choice(page, type_input, item["type"])
    set_input_value(weight_input, item["weight"])
    set_input_value(dimension_inputs.nth(0), item["length"])
    set_input_value(dimension_inputs.nth(1), item["width"])
    set_input_value(dimension_inputs.nth(2), item["height"])
    wait_for_glt_loading(row)
    wait_for_glt_loading(page)
    for _ in range(6):
        try:
            nmfc_select.click(force=True)
        except Exception:
            pass
        page.wait_for_timeout(500)
        nmfc_option = first_visible(
            page,
            [
                f".el-select-dropdown:not([style*='display: none']) .el-select-dropdown__item:has-text('{item['freight_class']}')",
                f".el-select-dropdown:not([style*='display: none']) li:has-text('{item['freight_class']}')",
                f"text=/^\\s*{re.escape(str(item['freight_class']))}\\s*$/",
                f"text=/{re.escape(str(item['freight_class']))}/",
            ],
        )
        if nmfc_option.count() > 0:
            try:
                nmfc_option.click(force=True)
            except Exception:
                pass
            page.wait_for_timeout(500)
        else:
            try:
                nmfc_select.press("ArrowDown")
                page.wait_for_timeout(200)
                nmfc_select.press("Enter")
            except Exception:
                pass
        wait_for_glt_loading(row, timeout_ms=8000)
        wait_for_glt_loading(page, timeout_ms=8000)
        try:
            nmfc_value = normalize_space(row.locator(".nmfc__dropdown").first.inner_text())
        except Exception:
            nmfc_value = ""
        if str(item["freight_class"]) in nmfc_value:
            break
    set_input_value(commodity_input, item["commodity"])

    type_value = ""
    try:
        type_value = normalize_space(type_input.input_value())
    except Exception:
        pass
    nmfc_value = ""
    try:
        nmfc_value = normalize_space(row.locator(".nmfc__dropdown .rc-select__selected-option").first.inner_text())
    except Exception:
        try:
            nmfc_value = normalize_space(row.locator(".nmfc__dropdown").first.inner_text())
        except Exception:
            pass
    if item["type"].upper() not in type_value.upper() or str(item["freight_class"]) not in nmfc_value:
        page.screenshot(path="glt_line_value_commit_failed.png", full_page=True)
        Path("glt_line_value_commit_failed.html").write_text(page.content(), encoding="utf-8")
        raise RuntimeError(
            "GLT line item values did not commit correctly. "
            "Saved glt_line_value_commit_failed.png and glt_line_value_commit_failed.html"
        )


def fill_quote_form(page, data: dict):
    for _ in range(20):
        if glt_quote_form_ready(page):
            break
        try:
            page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        page.wait_for_timeout(1000)

    if not glt_quote_form_ready(page):
        Path("glt_quote_form_missing.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path="glt_quote_form_missing.png", full_page=True)
        raise RuntimeError("Could not open GLT quote form. Saved glt_quote_form_missing.png and glt_quote_form_missing.html")

    pickup_selectors = [
        "#location-selectorpickup input[name='localValue']",
        "#location-selectorpickup input.el-input__inner",
        "input[placeholder='Pickup']:visible",
        "xpath=//em[contains(normalize-space(.), 'Pickup Zip Code or Location Name')]/preceding::input[@placeholder='Pickup'][1]",
        "xpath=//label[contains(normalize-space(.), 'Pickup')]/following::input[1]",
    ]
    delivery_selectors = [
        "#location-selectordelivery input[name='localValue']",
        "#location-selectordelivery input.el-input__inner",
        "input[placeholder='Delivery']:visible",
        "xpath=//em[contains(normalize-space(.), 'Delivery Zip Code or Location Name')]/preceding::input[@placeholder='Delivery'][1]",
        "xpath=//label[contains(normalize-space(.), 'Delivery')]/following::input[1]",
    ]

    pickup_input = resolve_location_input(page, pickup_selectors, "pickup")
    delivery_input = resolve_location_input(page, delivery_selectors, "delivery")

    fill_location_input(
        pickup_input,
        str(data.get("origin_zip") or data.get("pickup_zip") or "").strip(),
        page,
        str(data.get("pickup_city") or "").strip(),
    )
    fill_location_input(
        delivery_input,
        str(data.get("dest_zip") or data.get("destination_zip") or data.get("delivery_zip") or "").strip(),
        page,
        str(data.get("delivery_city") or "").strip(),
    )

    shipment_date = quote_date_string(data.get("shipment_date") or data.get("ship_date"))
    if shipment_date:
        date_input = first_present(
            page,
            [
                "xpath=//input[contains(@value, '-') and contains(@value, '20')]",
                "xpath=//*[contains(normalize-space(.), 'Estimated Pickup Date')]/preceding::input[1]",
            ],
        )
        if date_input.count() > 0:
            set_input_value(date_input, shipment_date)

    set_checkbox_by_label(page, "Non Stackable", checked=True)

    line_items = glt_line_items(data)
    for idx, item in enumerate(line_items):
        if idx > 0:
            add_line = first_present(
                page,
                [
                    "xpath=//button[contains(., 'ADD LINE') or contains(., 'Add Line')]",
                    "text=/ADD LINE/i",
                ],
            )
            if add_line.count() == 0:
                Path("glt_add_line_missing.html").write_text(page.content(), encoding="utf-8")
                page.screenshot(path="glt_add_line_missing.png", full_page=True)
                raise RuntimeError("Could not find GLT Add Line button. Saved glt_add_line_missing.png and glt_add_line_missing.html")
            add_line.click(force=True)
            wait_after_step(page, 2)
        fill_line_row(page, idx, item)


def results_loaded(page) -> bool:
    result_cards = page.locator(
        "xpath=//*[self::div or self::section]"
        "[.//*[contains(normalize-space(.), 'Service class')]"
        " and .//*[contains(normalize-space(.), 'Transit time')]"
        " and (.//button[contains(., 'BOOK')] or .//*[contains(normalize-space(.), 'USD$')])]"
    )
    try:
        if result_cards.count() > 0:
            return True
    except Exception:
        pass

    body_text = current_body_text(page)
    if "Quote number:" in body_text and "Sort By:" in body_text and "BOOK WITH INSURANCE" in body_text:
        return True
    return False


def results_page_open(page) -> bool:
    url = page.url or ""
    body_text = current_body_text(page)
    return (
        "/quote-book/carrier-selection/" in url
        or ("Quote number:" in body_text and "Sort By:" in body_text)
    )


def submit_quote(page):
    button = first_visible(
        page,
        [
            "button.button-quote",
            "xpath=//button[contains(., 'Quote')]",
            "text=/^\\s*Quote\\s*$/i",
        ],
    )
    if button.count() == 0:
        Path("glt_quote_button_missing.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path="glt_quote_button_missing.png", full_page=True)
        raise RuntimeError("Could not find GLT Quote button. Saved glt_quote_button_missing.png and glt_quote_button_missing.html")

    button.scroll_into_view_if_needed()
    button.click(force=True)
    page.wait_for_timeout(1000)
    if not results_loaded(page):
        try:
            button.evaluate("(el) => el.click()")
        except Exception:
            pass

    for _ in range(120):
        if results_page_open(page) or results_loaded(page):
            return
        try:
            page.wait_for_load_state("networkidle", timeout=3000)
        except Exception:
            pass
        if not results_page_open(page) and not results_loaded(page):
            current_url = page.url or ""
            if "/quote-book/all-options" in current_url and page.locator("button.button-quote").count() > 0:
                try:
                    page.locator("button.button-quote").first.click(force=True)
                except Exception:
                    pass
        page.wait_for_timeout(1000)

    Path("glt_results_not_found.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path="glt_results_not_found.png", full_page=True)
    raise RuntimeError("Could not detect GLT results. Saved glt_results_not_found.png and glt_results_not_found.html")


def carrier_from_logo(card) -> str:
    image = card.locator("img").first
    for getter in ["alt", "title"]:
        try:
            text = normalize_space(image.get_attribute(getter) or "")
        except Exception:
            text = ""
        if text:
            return text
    try:
        src = (image.get_attribute("src") or "").lower()
    except Exception:
        src = ""
    logo_map = {
        "tforce": "TFORCE FREIGHT",
        "rl": "R+L CARRIERS",
        "daylight": "DAYLIGHT TRANSPORT",
        "estes": "ESTES EXPRESS LINES",
        "fedex_priority": "FEDEX FREIGHT PRIORITY",
        "fedex": "FEDEX FREIGHT",
        "roadrunner": "ROADRUNNER FREIGHT",
        "xpo": "XPO LOGISTICS",
        "central": "CENTRAL TRANSPORT",
        "abf": "ABF FREIGHT",
        "od": "OLD DOMINION FREIGHT LINE",
        "old": "OLD DOMINION FREIGHT LINE",
        "saia": "SAIA",
        "forward": "FORWARD AIR",
    }
    for key, name in logo_map.items():
        if key in src:
            return name
    return ""


def scrape_results(page):
    for _ in range(240):
        if results_loaded(page):
            break
        body_text = current_body_text(page)
        if "Checking and comparing our best prices" in body_text or "We are working to obtain carriers and trucks" in body_text:
            page.wait_for_timeout(1500)
            continue
        page.wait_for_timeout(1000)

    Path("glt_results.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path="glt_results.png", full_page=True)

    cards = page.locator(
        "xpath=//*[self::div or self::section]"
        "[.//*[contains(normalize-space(.), 'Service class')]"
        " and .//*[contains(normalize-space(.), 'Transit time')]"
        " and (.//button[contains(., 'BOOK')] or .//*[contains(normalize-space(.), 'USD$')])]"
    )
    results = []
    for idx in range(cards.count()):
        card = cards.nth(idx)
        text = normalize_space(card.inner_text())
        if "Service class" not in text or "Transit time" not in text:
            continue

        carrier = carrier_from_logo(card)
        if not carrier:
            before_service = normalize_space(text.split("Service class")[0])
            carrier = before_service or f"GLT CARRIER {idx + 1}"

        service_match = re.search(r"Service class\s+(.*?)\s+Transit time", text, re.I)
        transit_match = re.search(r"Transit time\s+(\d+)\s+Business days", text, re.I)
        exp_match = re.search(r"Quote Expiration Date\s+([0-9]{2}-[0-9]{2}-[0-9]{4})", text, re.I)
        price_match = re.search(r"USD\$\s*([0-9,]+\.[0-9]{2})", text)
        results.append(
            {
                "carrier": carrier,
                # GLT shows standard price first and insurance price second.
                "price": float(price_match.group(1).replace(",", "")) if price_match else None,
                "transit_days": int(transit_match.group(1)) if transit_match else None,
                "delivery_day": None,
                "service_level": normalize_space(service_match.group(1)) if service_match else "",
                "time": None,
                "quote_expiration": exp_match.group(1) if exp_match else "",
            }
        )

    if not results:
        body_text = current_body_text(page)
        Path("glt_results_not_found.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path="glt_results_not_found.png", full_page=True)
        raise RuntimeError(
            f"Could not parse GLT results. Visible text sample: {body_text[:500]!r}. "
            "Saved glt_results_not_found.png and glt_results_not_found.html"
        )
    return results


def quote_glt(data: dict, *, headless: bool = True, slow_mo: int = 0, keep_open_ms: int = 0):
    username = env_first("GLT_USERNAME")
    password = env_first("GLT_PASSWORD", "PASSWORD")
    if not username or not password:
        raise RuntimeError("Missing GLT_USERNAME or GLT_PASSWORD in .env")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo)
        page = browser.new_page(viewport={"width": 1900, "height": 1600})
        try:
            do_login(page, username, password)
            goto_book_load(page)
            fill_quote_form(page, data)
            page.screenshot(path="glt_before_submit.png", full_page=True)
            submit_quote(page)
            results = scrape_results(page)
            if keep_open_ms > 0:
                page.wait_for_timeout(keep_open_ms)
            return results
        finally:
            browser.close()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-browser", action="store_true")
    parser.add_argument("--slow-mo", type=int, default=0)
    parser.add_argument("--keep-open-ms", type=int, default=0)
    return parser.parse_args()


def main():
    args = parse_args()
    ss = gs_client().open(SHEET_NAME)
    data = read_input(ss)
    print("GLT INPUT:", data, flush=True)
    results = quote_glt(
        data,
        headless=not args.show_browser,
        slow_mo=args.slow_mo,
        keep_open_ms=args.keep_open_ms,
    )
    print("GLT RESULTS:", results, flush=True)


if __name__ == "__main__":
    main()
