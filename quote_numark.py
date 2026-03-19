import argparse
import os
import re
import time
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from quote_total import SHEET_NAME, gs_client, read_input, write_result

load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=True)

STEP_DELAY_MS = 900


def env_first(*names):
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return ""


def has_value(value) -> bool:
    return value is not None and str(value).strip() != ""


def normalize_freight_class(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if re.fullmatch(r"\d+\.0+", raw):
        return str(int(float(raw)))
    return raw


def wait_after_step(page, multiplier=1):
    page.wait_for_timeout(STEP_DELAY_MS * multiplier)


def set_input_value(locator, value: str):
    locator.wait_for(state="visible", timeout=10000)
    locator.scroll_into_view_if_needed()
    locator.click(force=True)
    locator.fill("")
    locator.evaluate(
        "(el, val) => { el.value = val; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }",
        str(value),
    )
    locator.press("Tab")
    wait_after_step(locator.page)


def slow_type(locator, text, delay=45):
    locator.wait_for(state="visible", timeout=10000)
    locator.scroll_into_view_if_needed()
    locator.click(force=True)
    locator.fill("")
    locator.type(str(text), delay=delay)
    wait_after_step(locator.page)


def first_present(page, selectors):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() > 0:
                return locator
        except:
            pass
    return page.locator("xpath=/*[false()]")


def numark_quick_rater_panel(page):
    return first_present(
        page,
        [
            "xpath=(//select[.//option[contains(., 'WINDGATE PRODUCTS')]]/ancestor::*[.//*[contains(normalize-space(.), 'From Zip')] and .//*[contains(normalize-space(.), 'To Zip')] and .//*[contains(normalize-space(.), 'Pallets')] and .//*[contains(normalize-space(.), 'Weight')] and .//*[contains(normalize-space(.), 'Class')] and (.//button[contains(., 'Rate Now')] or .//input[contains(translate(@value,'RATE NOW','rate now'),'rate now')])])[1]",
            "xpath=(//*[contains(normalize-space(.), 'Quick Rater')]/ancestor::*[.//*[contains(normalize-space(.), 'From Zip')] and .//*[contains(normalize-space(.), 'To Zip')] and .//*[contains(normalize-space(.), 'Pallets')] and .//*[contains(normalize-space(.), 'Weight')] and .//*[contains(normalize-space(.), 'Class')] and (.//button[contains(., 'Rate Now')] or .//input[contains(translate(@value,'RATE NOW','rate now'),'rate now')])])[1]",
        ],
    )


def numark_class_select(quick_rater):
    selects = quick_rater.locator("xpath=.//select")
    count = selects.count()
    for idx in range(count):
        locator = selects.nth(idx)
        try:
            options = option_texts(locator)
        except:
            options = []
        normalized = {normalize_freight_class(opt.get("text")) for opt in options}
        if any(cls in normalized for cls in {"50", "55", "60", "65", "70", "77.5", "85", "92.5", "100", "110", "125", "150"}):
            return locator
    return quick_rater.locator("xpath=.//*[false()]")


def select_option_text(locator, desired_text: str):
    desired = normalize_freight_class(desired_text)
    if not desired or locator.count() == 0:
        return False

    for candidate in [desired, desired.zfill(3)]:
        try:
            locator.select_option(value=candidate)
            wait_after_step(locator.page)
            return True
        except:
            pass

    for candidate in [desired, desired.zfill(3)]:
        try:
            locator.select_option(label=candidate)
            wait_after_step(locator.page)
            return True
        except:
            pass

    options = locator.locator("option")
    count = options.count()
    desired_norm = " ".join(desired.upper().split())
    desired_padded = " ".join(desired.zfill(3).upper().split())
    for idx in range(count):
        try:
            label = " ".join((options.nth(idx).inner_text() or "").upper().split())
        except:
            continue
        if label in {desired_norm, desired_padded} or desired_norm in label or desired_padded in label:
            try:
                locator.select_option(label=options.nth(idx).inner_text())
                wait_after_step(locator.page)
                return True
            except:
                pass

    try:
        forced = locator.evaluate(
            """
            (sel, rawDesired) => {
                const norm = (s) => String(s || '').replace(/\\s+/g, ' ').trim().toUpperCase();
                const desired = norm(rawDesired);
                const padded = norm(String(rawDesired || '').padStart(3, '0'));
                const options = Array.from(sel.options || []);
                const match =
                    options.find((opt) => norm(opt.value) === desired) ||
                    options.find((opt) => norm(opt.value) === padded) ||
                    options.find((opt) => norm(opt.textContent) === desired) ||
                    options.find((opt) => norm(opt.textContent) === padded) ||
                    options.find((opt) => norm(opt.textContent).includes(desired)) ||
                    options.find((opt) => norm(opt.textContent).includes(padded));
                if (!match) return false;
                sel.value = match.value;
                match.selected = true;
                sel.dispatchEvent(new Event('input', { bubbles: true }));
                sel.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
            }
            """,
            desired,
        )
        if forced:
            wait_after_step(locator.page)
            return True
    except:
        pass
    return False


def selected_option_text(locator) -> str:
    try:
        return (locator.locator("option:checked").first.inner_text() or "").strip()
    except:
        return ""


def wait_for_select_options(locator, timeout_ms=5000):
    start = time.time()
    while (time.time() - start) * 1000 < timeout_ms:
        try:
            if locator.locator("option").count() > 1:
                return True
        except:
            pass
        locator.page.wait_for_timeout(150)
    return False


def option_texts(locator):
    try:
        return locator.evaluate(
            """
            (sel) => Array.from(sel.options || []).map((opt) => ({
                value: String(opt.value || '').trim(),
                text: String(opt.textContent || '').replace(/\\s+/g, ' ').trim(),
            }))
            """
        )
    except:
        return []


def force_select_numark_class(locator, desired_text: str):
    desired = normalize_freight_class(desired_text)
    try:
        return locator.evaluate(
            """
            (sel, rawDesired) => {
                const norm = (s) => String(s || '').replace(/\\s+/g, ' ').trim().toUpperCase();
                const desired = norm(rawDesired);
                const padded = norm(String(rawDesired || '').padStart(3, '0'));
                const options = Array.from(sel.options || []);
                const match =
                    options.find((opt) => norm(opt.value) === desired) ||
                    options.find((opt) => norm(opt.value) === padded) ||
                    options.find((opt) => norm(opt.textContent) === desired) ||
                    options.find((opt) => norm(opt.textContent) === padded) ||
                    options.find((opt) => norm(opt.textContent).includes(desired)) ||
                    options.find((opt) => norm(opt.textContent).includes(padded));
                if (!match) {
                    return {
                        ok: false,
                        selected: sel.options[sel.selectedIndex]?.textContent?.trim() || '',
                        options: options.map((opt) => ({
                            value: String(opt.value || '').trim(),
                            text: String(opt.textContent || '').replace(/\\s+/g, ' ').trim(),
                        })),
                    };
                }
                sel.value = match.value;
                match.selected = true;
                sel.selectedIndex = options.indexOf(match);
                sel.dispatchEvent(new Event('input', { bubbles: true }));
                sel.dispatchEvent(new Event('change', { bubbles: true }));
                sel.dispatchEvent(new Event('blur', { bubbles: true }));
                return {
                    ok: true,
                    selected: sel.options[sel.selectedIndex]?.textContent?.trim() || '',
                    options: options.map((opt) => ({
                        value: String(opt.value || '').trim(),
                        text: String(opt.textContent || '').replace(/\\s+/g, ' ').trim(),
                    })),
                };
            }
            """,
            desired,
        )
    except:
        return {"ok": False, "selected": "", "options": []}


def class_matches(selected: str, desired: str) -> bool:
    selected_norm = normalize_freight_class(selected)
    desired_norm = normalize_freight_class(desired)
    return selected_norm == desired_norm or selected_norm == desired_norm.zfill(3)


def select_numark_class(locator, desired_text: str):
    desired = normalize_freight_class(desired_text)
    if not desired:
        return False, "", option_texts(locator)

    options = option_texts(locator)
    target_index = None
    target_value = None
    target_label = None
    desired_upper = desired.upper()
    desired_padded = desired.zfill(3).upper()

    for idx, opt in enumerate(options):
        value = normalize_freight_class(opt.get("value"))
        text = normalize_freight_class(opt.get("text"))
        text_upper = text.upper()
        if value in {desired, desired.zfill(3)} or text_upper in {desired_upper, desired_padded}:
            target_index = idx
            target_value = opt.get("value", "")
            target_label = opt.get("text", "")
            break
    if target_index is None:
        for idx, opt in enumerate(options):
            text_upper = normalize_freight_class(opt.get("text")).upper()
            if desired_upper in text_upper or desired_padded in text_upper:
                target_index = idx
                target_value = opt.get("value", "")
                target_label = opt.get("text", "")
                break

    if target_index is None:
        return False, "", options

    for mode, value in [
        ("value", target_value),
        ("label", target_label),
        ("index", target_index),
    ]:
        try:
            if mode == "value" and value != "":
                locator.select_option(value=str(value))
            elif mode == "label" and value:
                locator.select_option(label=str(value))
            elif mode == "index":
                locator.select_option(index=int(value))
            wait_after_step(locator.page)
            try:
                locator.press("Tab")
            except:
                pass
            wait_after_step(locator.page)
            selected = selected_option_text(locator) or locator.input_value().strip()
            if class_matches(selected, desired):
                return True, selected, options
        except:
            pass

    try:
        locator.click(force=True)
        wait_after_step(locator.page)
        locator.press("Home")
        for _ in range(target_index):
            locator.press("ArrowDown")
        locator.press("Enter")
        locator.press("Tab")
        wait_after_step(locator.page, 2)
        selected = selected_option_text(locator) or locator.input_value().strip()
        if class_matches(selected, desired):
            return True, selected, options
    except:
        pass

    forced = force_select_numark_class(locator, desired)
    selected = (forced.get("selected") or "").strip()
    if forced.get("ok") and class_matches(selected, desired):
        try:
            locator.press("Tab")
        except:
            pass
        wait_after_step(locator.page)
        return True, selected, options
    return False, selected, forced.get("options") or options


def already_logged_in(page) -> bool:
    indicators = [
        page.get_by_text(re.compile(r"customer portal", re.I)).first,
        page.get_by_text(re.compile(r"quick rater", re.I)).first,
        page.get_by_text(re.compile(r"you are signed on as", re.I)).first,
        page.get_by_text(re.compile(r"customer options", re.I)).first,
    ]
    for locator in indicators:
        try:
            if locator.count() > 0:
                return True
        except:
            pass
    return False


def open_numark_login(page, login_url: str):
    page.goto(login_url, wait_until="domcontentloaded")
    wait_after_step(page, 2)
    if already_logged_in(page):
        return

    my_numark_candidates = [
        page.get_by_role("link", name=re.compile(r"^\s*My Numark\s*$", re.I)).first,
        page.get_by_role("button", name=re.compile(r"^\s*My Numark\s*$", re.I)).first,
        page.locator("xpath=//a[contains(normalize-space(.), 'My Numark')]").first,
        page.locator("xpath=//button[contains(normalize-space(.), 'My Numark')]").first,
        page.get_by_text(re.compile(r"^\s*My Numark\s*$", re.I)).first,
    ]
    for candidate in my_numark_candidates:
        try:
            if candidate.count() == 0:
                continue
            href = ""
            try:
                href = (candidate.get_attribute("href") or "").strip()
            except:
                href = ""

            before_pages = list(page.context.pages)
            current_url = page.url
            candidate.click(force=True)
            wait_after_step(page, 3)

            after_pages = list(page.context.pages)
            if len(after_pages) > len(before_pages):
                new_page = after_pages[-1]
                try:
                    new_page.wait_for_load_state("domcontentloaded")
                except:
                    pass
                page = new_page

            if page.url != current_url:
                try:
                    page.wait_for_load_state("domcontentloaded")
                except:
                    pass
                wait_after_step(page, 2)

            if already_logged_in(page):
                return page

            username_input, password_input, _ = find_login_controls(page)
            if username_input is not None and password_input is not None:
                return page

            if href and not href.lower().startswith("javascript"):
                try:
                    page.goto(href, wait_until="domcontentloaded")
                    wait_after_step(page, 3)
                except:
                    pass
                if already_logged_in(page):
                    return page
                username_input, password_input, _ = find_login_controls(page)
                if username_input is not None and password_input is not None:
                    return page
        except:
            pass

    if already_logged_in(page):
        return page

    username_input, password_input, _ = find_login_controls(page)
    if username_input is not None and password_input is not None:
        return page

    page.screenshot(path="numark_after_my_numark_click.png", full_page=True)
    return page


def wait_for_numark_post_login(page, timeout_sec=20):
    start = time.time()
    while time.time() - start < timeout_sec:
        if already_logged_in(page):
            return True
        try:
            body_text = page.locator("body").inner_text(timeout=1000)
        except PlaywrightTimeoutError:
            body_text = ""
        except:
            body_text = ""

        if re.search(r"login details|user id|password", body_text or "", re.I):
            return False

        # CoralTree loading page / spinner between auth and portal
        if re.search(r"coraltree|loading|users online", body_text or "", re.I):
            page.wait_for_timeout(800)
            continue

        page.wait_for_timeout(500)
    return already_logged_in(page)


def find_login_controls(page):
    username = first_present(
        page,
        [
            "xpath=//label[contains(., 'User ID')]/following::input[1]",
            "input[type='text']",
        ],
    )
    password = first_present(
        page,
        [
            "input[type='password']",
            "xpath=//label[contains(., 'Password')]/following::input[1]",
        ],
    )
    login_button = first_present(
        page,
        [
            "xpath=//button[contains(., 'Log In') or contains(., 'Login')]",
            "xpath=//input[contains(translate(@value,'LOGIN','login'),'log in')]",
            "text=/^\\s*Log In\\s*$/i",
        ],
    )
    if username.count() == 0 or password.count() == 0 or login_button.count() == 0:
        return None, None, None
    return username, password, login_button


def do_login(page, username, password):
    if already_logged_in(page):
        return

    user_input, pass_input, login_button = find_login_controls(page)
    if user_input is None:
        page.screenshot(path="numark_login_controls_missing.png", full_page=True)
        raise RuntimeError("Could not find Numark login controls. Saved numark_login_controls_missing.png")

    slow_type(user_input, username, delay=55)
    slow_type(pass_input, password, delay=55)
    login_button.click(force=True)
    try:
        page.wait_for_load_state("domcontentloaded")
    except:
        pass
    wait_after_step(page, 2)

    if not wait_for_numark_post_login(page, timeout_sec=20):
        page.screenshot(path="numark_login_failed.png", full_page=True)
        raise RuntimeError("Numark login failed. Saved numark_login_failed.png")


def numark_line_items(data: dict):
    items = []
    for idx, item in enumerate(data.get("pallet_items") or [], start=1):
        items.append(
            {
                "row": idx,
                "pallets": "1",
                "weight": item.get("weight") or item.get("weight_lb") or "",
                "freight_class": item.get("freight_class") or data.get("freight_class"),
            }
        )
    if items:
        return items
    return [
        {
            "row": 1,
            "pallets": data.get("pallets") or "1",
            "weight": data.get("weight") or "",
            "freight_class": data.get("freight_class") or "",
        }
    ]


def fill_quick_rater(page, data):
    quick_rater = numark_quick_rater_panel(page)
    if quick_rater.count() == 0:
        page.screenshot(path="numark_form_missing.png", full_page=True)
        raise RuntimeError("Could not find Numark Quick Rater panel. Saved numark_form_missing.png")

    customer_select = quick_rater.locator(
        "xpath=.//select[.//option[contains(., 'WINDGATE PRODUCTS') or contains(., 'GRISWOLD')]]"
    ).first
    if customer_select.count() == 0:
        page.screenshot(path="numark_form_missing.png", full_page=True)
        raise RuntimeError("Could not find Numark customer select in Quick Rater. Saved numark_form_missing.png")

    # Anchor all field lookups from the Quick Rater customer select so sidebar widgets never win.
    from_zip = customer_select.locator("xpath=following::input[not(@type='hidden')][1]").first
    to_zip = customer_select.locator("xpath=following::input[not(@type='hidden')][2]").first
    pallets_input = customer_select.locator("xpath=following::input[not(@type='hidden')][3]").first
    weight_input = customer_select.locator("xpath=following::input[not(@type='hidden')][4]").first
    class_select = numark_class_select(quick_rater)

    required_missing = []
    if has_value(data.get("dest_zip")) and to_zip.count() == 0:
        required_missing.append("dest_zip")
    if has_value(data.get("weight")) and weight_input.count() == 0:
        required_missing.append("weight")
    if has_value(data.get("freight_class")) and class_select.count() == 0:
        required_missing.append("freight_class")
    if required_missing:
        page.screenshot(path="numark_form_missing.png", full_page=True)
        raise RuntimeError(
            f"Could not find required Numark quick rater fields: {', '.join(required_missing)}. "
            "Saved numark_form_missing.png"
        )

    # When shipping from the selected customer, Numark allows leaving From Zip blank.
    # Only fill it if a visible field exists and it is still empty after customer selection.
    if from_zip.count() > 0 and has_value(data.get("origin_zip")):
        try:
            current = (from_zip.input_value() or "").strip()
        except:
            current = ""
        if not current:
            set_input_value(from_zip, data.get("origin_zip"))
    if to_zip.count() > 0 and has_value(data.get("dest_zip")):
        set_input_value(to_zip, data.get("dest_zip"))

    line_items = numark_line_items(data)
    if pallets_input.count() > 0:
        if len(line_items) > 1:
            set_input_value(pallets_input, str(len(line_items)))
        else:
            set_input_value(pallets_input, line_items[0].get("pallets") or data.get("pallets") or "1")
    if weight_input.count() > 0:
        total_weight = sum(float(str(item.get("weight") or "0")) for item in line_items)
        set_input_value(weight_input, str(int(total_weight) if float(total_weight).is_integer() else total_weight))
    if class_select.count() > 0:
        desired_class = normalize_freight_class(line_items[0].get("freight_class") or data.get("freight_class"))
        wait_for_select_options(class_select, timeout_ms=4000)
        ok, selected, options = select_numark_class(class_select, desired_class)
        if not ok:
            page.screenshot(path="numark_class_select_failed.png", full_page=True)
            raise RuntimeError(
                f"Could not select Numark freight class {desired_class}. "
                f"Selected: {selected}. Options: {options}. "
                "Saved numark_class_select_failed.png"
            )

    page.screenshot(path="numark_before_submit.png", full_page=True)


def click_rate_now(page):
    rate_button = first_present(
        page,
        [
            "xpath=//button[contains(., 'Rate Now')]",
            "xpath=//input[contains(translate(@value,'RATE NOW','rate now'),'rate now')]",
            "text=/^\\s*Rate Now\\s*$/i",
        ],
    )
    if rate_button.count() == 0:
        page.screenshot(path="numark_rate_now_missing.png", full_page=True)
        raise RuntimeError("Could not find Numark Rate Now button. Saved numark_rate_now_missing.png")

    rate_button.click(force=True)
    page.wait_for_load_state("domcontentloaded")
    wait_after_step(page, 3)
    page.screenshot(path="numark_after_submit.png", full_page=True)


def numark_service_error(page) -> str:
    try:
        body_text = page.locator("body").inner_text(timeout=1500)
    except:
        body_text = ""
    patterns = [
        r"ERR\d+.*error text not found",
        r"unable to process",
        r"not serviceable",
        r"destination.*not.*found",
        r"zip.*not.*found",
    ]
    for pattern in patterns:
        match = re.search(pattern, body_text or "", re.I)
        if match:
            return "Numark could not quote this destination zip"
    return ""


def scrape_results(page, timeout_sec=60):
    start = time.time()
    while time.time() - start < timeout_sec:
        page.wait_for_timeout(500)
        try:
            body_text = page.locator("body").inner_text(timeout=1500)
        except PlaywrightTimeoutError:
            continue
        except:
            continue

        service_error = numark_service_error(page)
        if service_error:
            page.screenshot(path="numark_service_error.png", full_page=True)
            raise RuntimeError(service_error)

        parsed_rate = None
        try:
            parsed_rate = page.evaluate(
                """
                () => {
                    const norm = (s) => (s || "").replace(/[ \\t\\f\\v]+/g, " ").trim();
                    const rawText = document.body ? document.body.innerText : "";
                    if (!/Freight Details/i.test(rawText)) return null;

                    const lines = rawText
                        .split(/\\r?\\n/)
                        .map((line) => norm(line))
                        .filter(Boolean);
                    const freightIndex = lines.findIndex((line) => /Freight Details/i.test(line));
                    if (freightIndex === -1) return null;

                    for (let i = 0; i < Math.min(lines.length, freightIndex + 30); i++) {
                        const line = lines[i];
                        if (!/Total Amount:/i.test(line)) continue;
                        const amountMatch = line.match(/([0-9]+(?:,[0-9]{3})*\\.[0-9]{2})\\s*$/);
                        if (amountMatch) {
                            return {
                                price: amountMatch[1],
                                source: "total_amount",
                                row: line,
                            };
                        }
                    }

                    for (let i = freightIndex; i < Math.min(lines.length, freightIndex + 30); i++) {
                        const line = lines[i];
                        if (!/FREIGHT CHARGES PREPAID/i.test(line)) continue;
                        const amountMatch = line.match(/([0-9]+(?:,[0-9]{3})*\\.[0-9]{2})\\s*$/);
                        if (amountMatch) {
                            return {
                                price: amountMatch[1],
                                source: "freight_charges_prepaid",
                                row: line,
                            };
                        }
                    }

                    for (let i = freightIndex; i < Math.min(lines.length, freightIndex + 20); i++) {
                        const line = lines[i];
                        if (!/\\bFAK\\b/i.test(line)) continue;
                        const directMatch = line.match(
                            /\\bFAK\\b\\s+([0-9]+(?:,[0-9]{3})*\\.[0-9]{2})\\s+([0-9]+(?:,[0-9]{3})*\\.[0-9]{2})/i
                        );
                        if (directMatch) {
                            return {
                                price: directMatch[1],
                                amount: directMatch[2],
                                source: "fak_rate",
                                row: line,
                            };
                        }
                        const nums = line.match(/([0-9]+(?:,[0-9]{3})*\\.[0-9]{2})/g) || [];
                        if (nums.length < 2) continue;
                        return {
                            price: nums[0],
                            amount: nums[1],
                            source: "fak_rate",
                            row: line,
                        };
                    }
                    return null;
                }
                """
            )
        except:
            parsed_rate = None

        if parsed_rate and parsed_rate.get("price"):
            price = float(str(parsed_rate["price"]).replace(",", ""))
            raw_money = []
            if parsed_rate.get("amount"):
                raw_money.append(f"${parsed_rate['amount']}")
            raw_money.append(f"${parsed_rate['price']}")
            days = None
            for pattern in [
                r"Transit(?: Time| Days)?\s*[:\-]?\s*([0-9]+)",
                r"Business Days?\s*[:\-]?\s*([0-9]+)",
                r"\b([0-9]+)\s*(?:business )?days\b",
            ]:
                match = re.search(pattern, body_text or "", re.I)
                if match:
                    days = int(match.group(1))
                    break

            page.screenshot(path="numark_results_detected.png", full_page=True)
            return {
                "price": price,
                "transit_days": days,
                "raw_money": raw_money,
            }

        money = re.findall(r"\$[0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2})", body_text or "")
        if not money:
            continue

        price = float(money[-1].replace("$", "").replace(",", ""))
        days = None
        for pattern in [
            r"Transit(?: Time| Days)?\s*[:\-]?\s*([0-9]+)",
            r"Business Days?\s*[:\-]?\s*([0-9]+)",
            r"\b([0-9]+)\s*(?:business )?days\b",
        ]:
            match = re.search(pattern, body_text or "", re.I)
            if match:
                days = int(match.group(1))
                break

        page.screenshot(path="numark_results_detected.png", full_page=True)
        return {
            "price": price,
            "transit_days": days,
            "raw_money": money[-8:],
        }

    page.screenshot(path="numark_results_not_found.png", full_page=True)
    raise RuntimeError("Could not detect Numark results. Saved numark_results_not_found.png")


def quote_numark(
    data: dict,
    pause_on_result: bool = False,
    *,
    headless: bool = True,
    slow_mo: int = 0,
) -> dict:
    username = env_first("NUMARK_USERNAME", "NUMARK_USER", "NUMARK_LOGIN_USERNAME")
    password = env_first("NUMARK_PASSWORD", "NUMARK_PASS", "NUMARK_LOGIN_PASSWORD")
    login_url = env_first("NUMARK_LOGIN_URL", "NUMARK_URL", "NUMARK_HOME_URL")

    if not username or not password or not login_url:
        raise RuntimeError("Missing NUMARK_USERNAME / NUMARK_PASSWORD / NUMARK_LOGIN_URL in .env")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo)
        page = browser.new_page()

        page = open_numark_login(page, login_url)
        do_login(page, username, password)
        fill_quick_rater(page, data)
        click_rate_now(page)
        scraped = scrape_results(page, timeout_sec=60)

        if pause_on_result:
            try:
                input("Numark result is on screen. Press Enter to close the browser...")
            except EOFError:
                pass

        browser.close()
        return {
            "carrier": "NUMARK",
            "price": scraped["price"],
            "transit_days": scraped["transit_days"],
            "raw_money": scraped["raw_money"],
        }


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin-zip")
    parser.add_argument("--dest-zip")
    parser.add_argument("--freight-class")
    parser.add_argument("--weight")
    parser.add_argument("--pallets")
    parser.add_argument("--shipment-date")
    parser.add_argument("--pickup-city", default="SYLMAR, CA")
    parser.add_argument("--delivery-city", default="")
    parser.add_argument("--pause-on-result", action="store_true")
    parser.add_argument("--show-browser", action="store_true")
    parser.add_argument("--slow-mo", type=int, default=0)
    return parser.parse_args()


def cli_data(args):
    if not any([args.origin_zip, args.dest_zip, args.freight_class, args.weight, args.pallets, args.shipment_date]):
        return None
    return {
        "origin_zip": args.origin_zip or "",
        "dest_zip": args.dest_zip or "",
        "pallets": args.pallets or "1",
        "length": "",
        "width": "",
        "height": "",
        "weight": args.weight or "",
        "pieces": "",
        "freight_class": args.freight_class or "",
        "pickup_city": args.pickup_city or "",
        "delivery_city": args.delivery_city or "",
        "shipment_date": args.shipment_date or datetime.now().strftime("%m/%d/%Y"),
    }


def main():
    args = parse_args()
    data = cli_data(args)
    ss = None

    if data is None:
        client = gs_client()
        ss = client.open(SHEET_NAME)
        data = read_input(ss)

    result = quote_numark(
        data,
        pause_on_result=bool(args.pause_on_result and data is not None),
        headless=not args.show_browser,
        slow_mo=args.slow_mo,
    )
    print("NUMARK result:", result)

    if ss is not None:
        write_result(
            ss,
            carrier_name="NUMARK",
            result=result,
            origin_city=data.get("pickup_city", ""),
            dest_city=data.get("delivery_city", ""),
        )
        print("✅ Wrote NUMARK quote into Broker Result.")


if __name__ == "__main__":
    main()
