import argparse
import math
import os
import re
from html import unescape
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from quote_total import SHEET_NAME, gs_client, read_input

load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=True)

STEP_DELAY_MS = 350
PRIORITY1_DASHBOARD_URL = "https://dashboard.priority1.com/customerdashboard"


def env_first(*names):
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return ""


def has_value(value) -> bool:
    return value is not None and str(value).strip() != ""


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


def normalize_space(value: str) -> str:
    return " ".join((value or "").split())


def wait_after_step(page, multiplier=1):
    page.wait_for_timeout(STEP_DELAY_MS * multiplier)


def current_body_text(page) -> str:
    try:
        return normalize_space(page.locator("body").inner_text())
    except Exception:
        return ""


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
    locator.press("Tab")
    wait_after_step(locator.page)


def set_input_value_no_tab(locator, value: str):
    locator.wait_for(state="visible", timeout=20000)
    locator.scroll_into_view_if_needed()
    locator.click(force=True)
    locator.fill("")
    locator.fill(str(value))
    locator.evaluate(
        """
        (el) => {
            el.dispatchEvent(new Event('input', { bubbles: true }));
            el.dispatchEvent(new Event('change', { bubbles: true }));
        }
        """
    )
    wait_after_step(locator.page)


def visible_editable_inputs(page):
    candidates = page.locator("input:visible")
    inputs = []
    for idx in range(candidates.count()):
        locator = candidates.nth(idx)
        try:
            if not locator.is_visible():
                continue
            input_type = (locator.get_attribute("type") or "").lower()
            if input_type in {"hidden", "checkbox", "radio", "submit", "button"}:
                continue
            box = locator.bounding_box()
            if not box or box["width"] < 20 or box["height"] < 10:
                continue
            inputs.append(locator)
        except Exception:
            continue
    return inputs


def visible_selects(page):
    candidates = page.locator("select:visible")
    selects = []
    for idx in range(candidates.count()):
        locator = candidates.nth(idx)
        try:
            if not locator.is_visible():
                continue
            box = locator.bounding_box()
            if not box or box["width"] < 20 or box["height"] < 10:
                continue
            selects.append(locator)
        except Exception:
            continue
    return selects


def input_label_text(locator) -> str:
    try:
        return normalize_space(
            locator.evaluate(
                """
                (el) => {
                    const chunks = [];
                    const attrs = ['placeholder', 'aria-label', 'name', 'id'];
                    for (const attr of attrs) {
                        const value = el.getAttribute(attr);
                        if (value) chunks.push(value);
                    }
                    let node = el;
                    for (let i = 0; i < 4 && node; i++, node = node.parentElement) {
                        const text = (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
                        if (text) chunks.push(text);
                    }
                    return chunks.join(' ');
                }
                """
            )
        ).upper()
    except Exception:
        return ""


def option_text(locator) -> str:
    try:
        return normalize_space(
            locator.evaluate(
                """
                (sel) => Array.from(sel.options || []).map((opt) => opt.textContent || opt.value || '').join(' ')
                """
            )
        ).upper()
    except Exception:
        return ""


def priority1_visible_input_by_label(page, label: str, *, after_label: str = ""):
    label_upper = label.upper()
    after_upper = after_label.upper()
    inputs = visible_editable_inputs(page)
    start_index = 0
    if after_upper:
        for idx, locator in enumerate(inputs):
            if after_upper in input_label_text(locator):
                start_index = idx
                break
    for locator in inputs[start_index:]:
        if label_upper in input_label_text(locator):
            return locator
    raise RuntimeError(f"Could not find visible Priority1 input for {label}")


def priority1_class_select(page, row_index: int = 0):
    class_selects = []
    for locator in visible_selects(page):
        text = f"{input_label_text(locator)} {option_text(locator)}"
        if re.search(r"\b50\b", text) and re.search(r"\b100\b", text) and re.search(r"\b500\b", text):
            class_selects.append(locator)
    if len(class_selects) > row_index:
        return class_selects[row_index]
    return None


def select_native_option(select_locator, desired: str) -> bool:
    desired_text = normalize_space(str(desired or "")).upper()
    if not desired_text:
        return False
    try:
        return bool(
            select_locator.evaluate(
                """
                (sel, desiredText) => {
                    const norm = (value) => String(value || '').replace(/\\s+/g, ' ').trim().toUpperCase();
                    const options = Array.from(sel.options || []);
                    const match = options.find((opt) => norm(opt.textContent) === desiredText || norm(opt.value) === desiredText)
                        || options.find((opt) => norm(opt.textContent).includes(desiredText) || norm(opt.value).includes(desiredText));
                    if (!match) return false;
                    sel.value = match.value;
                    match.selected = true;
                    sel.dispatchEvent(new Event('input', { bubbles: true }));
                    sel.dispatchEvent(new Event('change', { bubbles: true }));
                    return true;
                }
                """,
                desired_text,
            )
        )
    except Exception:
        return False


def select_zip_suggestion(page, zip_code: str) -> bool:
    zip_text = str(zip_code or "").strip()
    if not zip_text:
        return False
    page.wait_for_timeout(800)
    pattern = re.compile(re.escape(zip_text), re.I)
    for selector in [".p1-select__option", "li", "[role='option']"]:
        options = page.locator(selector).filter(has_text=pattern)
        for idx in range(options.count()):
            option = options.nth(idx)
            try:
                if not option.is_visible(timeout=500):
                    continue
                option.scroll_into_view_if_needed()
                option.click(timeout=5000)
                page.wait_for_timeout(800)
                return True
            except Exception:
                continue
    try:
        page.keyboard.press("Enter")
        page.wait_for_timeout(800)
        return True
    except Exception:
        return False


def quote_line_items(data: dict):
    pallet_items = data.get("pallet_items") or []
    if pallet_items:
        items = []
        for item in pallet_items:
            items.append(
                {
                    "units": "1",
                    "pieces": integer_string(item.get("pieces") or "1") or "1",
                    "weight": integer_string(item.get("weight"), round_up=True),
                    "length": integer_string(item.get("length"), round_up=True),
                    "width": integer_string(item.get("width"), round_up=True),
                    "height": integer_string(item.get("height"), round_up=True),
                    "freight_class": str(item.get("freight_class") or data.get("freight_class") or "").strip(),
                }
            )
        return items

    return [
        {
            "units": integer_string(data.get("pallets") or "1") or "1",
            "pieces": integer_string(data.get("pieces") or "1") or "1",
            "weight": integer_string(data.get("weight"), round_up=True),
            "length": integer_string(data.get("length"), round_up=True),
            "width": integer_string(data.get("width"), round_up=True),
            "height": integer_string(data.get("height"), round_up=True),
            "freight_class": str(data.get("freight_class") or "").strip(),
        }
    ]


def priority1_pickup_date(value) -> str:
    raw = str(value or "").strip()
    if not raw:
        return datetime.now().strftime("%m/%d/%Y")
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%m-%d-%y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%m/%d/%Y")
        except ValueError:
            continue
    return raw


def login_complete(page) -> bool:
    body = current_body_text(page)
    url = page.url or ""
    return "Request LTL Quote" in body or "Reports" in body or "dashboard.priority1.com" in url


def do_login(page, username: str, password: str, login_url: str):
    page.goto(login_url, wait_until="domcontentloaded", timeout=120000)
    wait_after_step(page, 3)
    if login_complete(page):
        return

    user_input = page.get_by_label(re.compile(r"Username|Email", re.I)).first
    if user_input.count() == 0:
        user_input = page.locator("input[type='email'], input[name='username'], input[name='email'], input").first
    set_input_value(user_input, username)

    password_input = page.locator("input[type='password']").first
    if password_input.count() > 0:
        set_input_value(password_input, password)

    continue_button = page.get_by_role("button", name=re.compile(r"Continue|Log\s*in|Sign\s*in", re.I)).first
    if continue_button.count() == 0:
        continue_button = page.locator("button[type='submit'], input[type='submit']").first
    continue_button.click(force=True)
    wait_after_step(page, 4)

    if page.locator("input[type='password']").count() > 0 and not login_complete(page):
        password_input = page.locator("input[type='password']").first
        try:
            if not password_input.input_value():
                set_input_value(password_input, password)
        except Exception:
            set_input_value(password_input, password)
        submit = page.get_by_role("button", name=re.compile(r"Continue|Log\s*in|Sign\s*in", re.I)).first
        if submit.count() == 0:
            submit = page.locator("button[type='submit'], input[type='submit']").first
        submit.click(force=True)

    for _ in range(20):
        if login_complete(page):
            return
        try:
            page.wait_for_load_state("domcontentloaded", timeout=5000)
        except Exception:
            pass
        page.wait_for_timeout(3000)

    Path("priority1_login_failed.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path="priority1_login_failed.png", full_page=True)
    raise RuntimeError(
        f"Priority1 login failed at URL {page.url!r}. "
        f"Visible text sample: {current_body_text(page)[:300]!r}. "
        "Saved priority1_login_failed.png and priority1_login_failed.html"
    )


def goto_ltl_quote(page):
    if "quotes/create" in (page.url or "") and "Get Rates" in current_body_text(page):
        return

    candidates = [
        page.get_by_role("button", name=re.compile(r"Request\s+LTL\s+Quote", re.I)).first,
        page.get_by_text(re.compile(r"Request\s+LTL\s+Quote", re.I)).first,
    ]
    for candidate in candidates:
        try:
            if candidate.count() > 0:
                candidate.click(force=True, timeout=10000)
                for _ in range(20):
                    if "Get Rates" in current_body_text(page):
                        return
                    page.wait_for_timeout(1000)
        except Exception:
            pass

    page.goto(PRIORITY1_DASHBOARD_URL, wait_until="domcontentloaded", timeout=120000)
    wait_after_step(page, 4)
    button = page.get_by_role("button", name=re.compile(r"Request\s+LTL\s+Quote", re.I)).first
    if button.count() == 0:
        button = page.get_by_text(re.compile(r"Request\s+LTL\s+Quote", re.I)).first
    button.click(force=True)
    for _ in range(30):
        if "Get Rates" in current_body_text(page):
            return
        page.wait_for_timeout(1000)

    Path("priority1_quote_page_missing.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path="priority1_quote_page_missing.png", full_page=True)
    raise RuntimeError("Could not open Priority1 Request LTL Quote page. Saved priority1_quote_page_missing.png/html")


def labeled_input(page, label_pattern: str, index: int = 0):
    locator = page.locator(
        f"xpath=(//*[self::label or self::span or self::div][contains(translate(normalize-space(.), 'abcdefghijklmnopqrstuvwxyz', 'ABCDEFGHIJKLMNOPQRSTUVWXYZ'), '{label_pattern.upper()}')]/following::input[1])"
    )
    if locator.count() > index:
        return locator.nth(index)
    return page.locator("input").nth(index)


def fill_pickup_destination(page, data: dict):
    pickup_date = priority1_pickup_date(data.get("shipment_date"))
    try:
        pickup_date_input = priority1_visible_input_by_label(page, "PICKUP DATE")
        set_input_value(pickup_date_input, pickup_date)
    except Exception:
        pass

    pickup_zip = str(data.get("origin_zip") or "").strip()
    dest_zip = str(data.get("dest_zip") or "").strip()
    pickup_zip_input = page.locator("input[placeholder*='PICKUP ZIP' i], input[aria-label*='PICKUP ZIP' i]").first
    if pickup_zip_input.count() == 0:
        pickup_zip_input = priority1_visible_input_by_label(page, "PICKUP ZIP")
    destination_zip_input = page.locator(
        "input[placeholder*='DESTINATION ZIP' i], input[aria-label*='DESTINATION ZIP' i]"
    ).first
    if destination_zip_input.count() == 0:
        destination_zip_input = priority1_visible_input_by_label(page, "DESTINATION ZIP")

    set_input_value_no_tab(pickup_zip_input, pickup_zip)
    select_zip_suggestion(page, pickup_zip)
    set_input_value_no_tab(destination_zip_input, dest_zip)
    select_zip_suggestion(page, dest_zip)


def fill_items(page, data: dict):
    items = quote_line_items(data)
    for index, item in enumerate(items):
        if index > 0:
            add_button = page.get_by_role("button", name=re.compile(r"Add\s+An\s+Item", re.I)).first
            if add_button.count() > 0:
                add_button.click(force=True)
                wait_after_step(page, 2)

        visible_inputs = visible_editable_inputs(page)
        item_inputs = [locator for locator in visible_inputs if any(token in input_label_text(locator) for token in ["UNITS", "PIECES", "WEIGHT", "LENGTH", "WIDTH", "HEIGHT", "NMFC", "DESCRIPTION"])]
        if len(item_inputs) < 8:
            page.screenshot(path="priority1_item_inputs_missing.png", full_page=True)
            raise RuntimeError(
                f"Could not find Priority1 item inputs. Found {len(item_inputs)} visible item inputs. "
                "Saved priority1_item_inputs_missing.png"
            )

        row_start = index * 8
        row_inputs = item_inputs[row_start : row_start + 8]
        if len(row_inputs) < 8:
            raise RuntimeError(f"Could not find Priority1 item input row {index + 1}")

        for locator, value in zip(
            row_inputs,
            [
                item["units"],
                item["pieces"],
                item["weight"],
                item["length"],
                item["width"],
                item["height"],
                "0000",
                "SHEET METAL PARTS",
            ],
        ):
            set_input_value_no_tab(locator, value)

        class_select = priority1_class_select(page, index)
        if class_select is not None:
            select_native_option(class_select, item["freight_class"])

        stable_fields = {
            "Units": item["units"],
            "Pieces": item["pieces"],
            "Weight": item["weight"],
            "Length": item["length"],
            "Width": item["width"],
            "Height": item["height"],
            "Description": "SHEET METAL PARTS",
            "NMFC": "0000",
        }
        for field_name, value in stable_fields.items():
            field = page.locator(f"#Items_{index}__{field_name}").first
            if field.count() > 0:
                set_input_value_no_tab(field, value)

        try:
            page.evaluate("() => document.activeElement && document.activeElement.blur && document.activeElement.blur()")
        except Exception:
            pass
        wait_after_step(page)


def submit_quote(page):
    try:
        page.evaluate("() => document.activeElement && document.activeElement.blur && document.activeElement.blur()")
    except Exception:
        pass

    def click_get_rates():
        candidates = [
            page.get_by_role("button", name=re.compile(r"Get\s+Rates", re.I)).last,
            page.locator("button:visible").filter(has_text=re.compile(r"^\s*Get\s+Rates\s*$", re.I)).last,
            page.locator("xpath=(//button[normalize-space()='Get Rates'])[last()]"),
        ]
        last_error = None
        for button in candidates:
            try:
                if button.count() == 0:
                    continue
                button.wait_for(state="visible", timeout=8000)
                button.scroll_into_view_if_needed()
                button.click(timeout=8000)
                return
            except Exception as exc:
                last_error = exc
        if last_error:
            raise last_error
        raise RuntimeError("Could not find Priority1 Get Rates button.")

    def acknowledge_accessorial_warning():
        warning = page.locator(".p1-confirmation").filter(has_text=re.compile(r"Recommended Accessorials", re.I)).last
        try:
            if warning.count() == 0 or not warning.is_visible(timeout=1000):
                return False
        except Exception:
            return False
        ignore = warning.get_by_role("button", name=re.compile(r"Ignore\s+and\s+get\s+rates", re.I))
        ignore.wait_for(state="visible", timeout=5000)
        ignore.click(timeout=8000)
        return True

    click_get_rates()
    for _ in range(3):
        page.wait_for_timeout(1500)
        if acknowledge_accessorial_warning():
            break
        if re.search(r"/quotes/details/", page.url) or page.locator(".p1-quote-rate-card").count() > 0:
            break
        body = current_body_text(page)
        if re.search(r"\$[0-9,]+\.[0-9]{2}", body) and re.search(r"Select Quote|Rates|Transit Days", body, re.I):
            break
        if page.get_by_role("button", name=re.compile(r"Get\s+Rates", re.I)).count() > 0:
            click_get_rates()

    try:
        page.wait_for_url(re.compile(r"/quotes/details/"), timeout=120000)
    except Exception:
        pass
    saw_rates_at = None
    last_count = -1
    stable_ticks = 0
    for tick in range(180):
        body = current_body_text(page)
        has_rates = bool(re.search(r"\$[0-9,]+\.[0-9]{2}", body) and re.search(r"Select Quote|Rates|Transit Days", body, re.I))
        if has_rates and saw_rates_at is None:
            saw_rates_at = tick
        if has_rates:
            count = page.locator(".p1-quote-rate-card").count()
            if count == last_count and count > 0:
                stable_ticks += 1
            else:
                stable_ticks = 0
                last_count = count
            if stable_ticks >= 8 and tick - saw_rates_at >= 15:
                return
        page.wait_for_timeout(1000)

    if saw_rates_at is not None:
        return

    Path("priority1_results_missing.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path="priority1_results_missing.png", full_page=True)
    raise RuntimeError("Priority1 rates did not load. Saved priority1_results_missing.png/html")


def canonical_priority1_carrier_name(name: str) -> str:
    cleaned = normalize_space(name)
    upper = cleaned.upper()
    replacements = {
        "ABF FREIGHT": "ABF Freight",
        "BEST OVERNITE EXPRESS": "Best Overnite Express",
        "BEST YET EXPRESS": "Best Yet Express",
        "CUSTOM COMPANIES": "Custom Companies",
        "DAYLIGHT TRANSPORT": "Daylight Transport",
        "ESTES": "Estes",
        "FEDEX ECONOMY": "FedEx Economy",
        "FEDEX PRIORITY": "FedEx Priority",
        "FORWARD": "Forward",
        "GO2 LOGISTICS": "Go2 Logistics",
        "OAK HARBOR FREIGHT": "Oak Harbor Freight",
        "OLD DOMINION FREIGHT LINE": "Old Dominion Freight Line",
        "R&L CARRIERS": "R&L Carriers",
        "ROADRUNNER TRANSPORTATION SYSTEMS": "Roadrunner Transportation Systems",
        "SAIA LTL FREIGHT": "Saia LTL Freight",
        "TFORCE FREIGHT": "TForce Freight",
        "TOTAL TRANSPORTATION & DISTRIBUTION": "Total Transportation & Distribution",
        "WARP": "Warp",
        "XPO": "XPO",
    }
    return replacements.get(upper, cleaned.title() if cleaned.isupper() else cleaned)


def collect_priority1_rate_cards(page):
    results = []
    seen = set()
    stable_rounds = 0
    previous_count = 0

    for _ in range(18):
        cards = page.locator(".p1-quote-rate-card")
        for idx in range(cards.count()):
            try:
                text = normalize_space(cards.nth(idx).inner_text(timeout=5000))
            except Exception:
                continue
            price = parse_price(text)
            if price is None:
                continue
            carrier_match = re.search(r"([A-Z0-9 &'().,-]{3,80}?)\s+\|\s+([A-Z0-9]{2,8})", text)
            carrier = normalize_space(carrier_match.group(1)) if carrier_match else ""
            if not carrier:
                image_alt = cards.nth(idx).locator("img[alt]").first
                try:
                    carrier = normalize_space(image_alt.get_attribute("alt") or "")
                except Exception:
                    carrier = ""
            if not carrier:
                carrier = f"Priority1 Rate {idx + 1}"

            service_match = re.search(
                r"\b(Standard Rate|Standard Service|Market Rate|Customer Rate|Contract Rate|Volume Rate|LTL Standard Transit|Priority|Economy)\b",
                text,
                re.I,
            )
            key = (carrier.upper(), price)
            if key in seen:
                continue
            seen.add(key)
            results.append(
                {
                    "carrier": canonical_priority1_carrier_name(carrier),
                    "price": price,
                    "transit_days": parse_transit_days(text),
                    "service_level": service_match.group(1).upper() if service_match else "",
                    "time": None,
                }
            )

        current_count = len(results)
        if current_count == previous_count:
            stable_rounds += 1
        else:
            stable_rounds = 0
            previous_count = current_count
        if stable_rounds >= 4 and current_count >= 10:
            break
        try:
            page.mouse.wheel(0, 1600)
        except Exception:
            page.evaluate("() => window.scrollBy(0, 1600)")
        page.wait_for_timeout(1000)

    return sorted(results, key=lambda item: float(item.get("price") or 0))


def wait_for_priority1_rates_to_settle(page):
    last_count = -1
    stable_ticks = 0
    for _ in range(90):
        count = page.locator(".p1-quote-rate-card").count()
        if count == last_count and count > 0:
            stable_ticks += 1
        else:
            stable_ticks = 0
            last_count = count
        if stable_ticks >= 6:
            return
        page.wait_for_timeout(1000)


def parse_price(text: str):
    matches = re.findall(r"\$([0-9,]+\.[0-9]{2})", text or "")
    if not matches:
        return None
    return float(matches[0].replace(",", ""))


def parse_transit_days(text: str):
    match = re.search(r"Transit\s+Days\s+([0-9]+)\s+business\s+days?", text or "", re.I)
    if not match:
        match = re.search(r"\b([0-9]+)\s+business\s+days?\b", text or "", re.I)
    return int(match.group(1)) if match else None


def clean_html_text(value: str) -> str:
    return normalize_space(re.sub(r"<[^>]+>", " ", unescape(value or "")))


def scrape_results_table(html: str):
    results = []
    rows = re.findall(r"<tr[^>]*>([\s\S]*?)</tr>", html or "", re.I)
    for row in rows:
        cells = re.findall(r"<td[^>]*>([\s\S]*?)</td>", row, re.I)
        if len(cells) < 6:
            continue
        carrier = clean_html_text(cells[0]).strip("⠀ ")
        price_text = clean_html_text(cells[1])
        service = clean_html_text(cells[4]).strip("⠀ ")
        transit_text = clean_html_text(cells[5])
        price = parse_price(price_text)
        if not carrier or price is None:
            continue
        results.append(
            {
                "carrier": carrier,
                "price": price,
                "transit_days": parse_transit_days(transit_text) or parse_transit_days(f"{transit_text} business days"),
                "service_level": service.upper() if service else "",
                "time": None,
            }
        )
    return results


def scrape_results(page):
    wait_for_priority1_rates_to_settle(page)
    card_results = collect_priority1_rate_cards(page)
    body_text = page.locator("body").inner_text()
    html = page.content()
    Path("priority1_results.html").write_text(html, encoding="utf-8")
    print("PRIORITY1 RESULTS PAGE TEXT:", flush=True)
    print(body_text[:6000], flush=True)

    if card_results:
        return card_results

    table_results = scrape_results_table(html)
    if table_results:
        return table_results

    results = []
    cards = page.locator("xpath=//button[contains(., 'Select Quote')]/ancestor::div[contains(., '$')][1]")
    for idx in range(cards.count()):
        text = normalize_space(cards.nth(idx).inner_text())
        price = parse_price(text)
        if price is None:
            continue
        carrier = ""
        carrier_match = re.match(r"([A-Z0-9 &'().,-]+?)\s+\|", text)
        if carrier_match:
            carrier = normalize_space(carrier_match.group(1))
        if not carrier:
            parts = [part.strip() for part in re.split(r"\$[0-9,]+\.[0-9]{2}", text, maxsplit=1)[0].split("  ") if part.strip()]
            carrier = parts[-1] if parts else f"Priority1 Rate {idx + 1}"
        results.append(
            {
                "carrier": carrier,
                "price": price,
                "transit_days": parse_transit_days(text),
                "service_level": "STANDARD RATE" if re.search(r"STANDARD RATE", text, re.I) else "",
                "time": None,
            }
        )

    if not results:
        for match in re.finditer(r"([A-Z0-9 &'().,-]{3,80}?)\s+\|[^\n$]{0,250}\$([0-9,]+\.[0-9]{2})", body_text, re.I):
            text = normalize_space(match.group(0))
            results.append(
                {
                    "carrier": normalize_space(match.group(1)),
                    "price": float(match.group(2).replace(",", "")),
                    "transit_days": parse_transit_days(text),
                    "service_level": "STANDARD RATE" if re.search(r"STANDARD RATE", text, re.I) else "",
                    "time": None,
                }
            )

    return results


def quote_priority1(data: dict, *, headless: bool = True, slow_mo: int = 0, keep_open_ms: int = 0):
    username = env_first(
        "PRIORITY1_USERNAME",
        "PRIORITY1_EMAIL",
        "PRIORITY1_USER",
        "PRIORITY_USERNAME",
        "PRIORITY_EMAIL",
        "PRIORITY_USER",
        "P1_USERNAME",
        "P1_EMAIL",
        "P1_USER",
    )
    password = env_first(
        "PRIORITY1_PASSWORD",
        "PRIORITY1_PASS",
        "PRIORITY_PASSWORD",
        "PRIORITY_PASS",
        "P1_PASSWORD",
        "P1_PASS",
    )
    login_url = env_first(
        "PRIORITY1_LOGIN_URL",
        "PRIORITY1_URL",
        "PRIORITY_LOGIN_URL",
        "PRIORITY_URL",
        "P1_LOGIN_URL",
        "P1_URL",
    )
    if not username or not password or not login_url:
        raise RuntimeError(
            "Missing Priority1 credentials in .env. Expected PRIORITY1_USERNAME, "
            "PRIORITY1_PASSWORD, PRIORITY1_LOGIN_URL (or P1_* aliases)."
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo)
        page = browser.new_page(viewport={"width": 1900, "height": 950})
        try:
            do_login(page, username, password, login_url)
            goto_ltl_quote(page)
            fill_pickup_destination(page, data)
            fill_items(page, data)
            page.screenshot(path="priority1_before_submit.png", full_page=True)
            submit_quote(page)
            page.screenshot(path="priority1_results.png", full_page=True)
            results = scrape_results(page)
            if keep_open_ms > 0:
                print(f"Priority1 keeping browser open for {keep_open_ms} ms", flush=True)
                page.wait_for_timeout(keep_open_ms)
            return results
        finally:
            browser.close()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin-zip")
    parser.add_argument("--dest-zip")
    parser.add_argument("--pallets")
    parser.add_argument("--length")
    parser.add_argument("--width")
    parser.add_argument("--height")
    parser.add_argument("--weight")
    parser.add_argument("--pieces")
    parser.add_argument("--freight-class")
    parser.add_argument("--show-browser", action="store_true")
    parser.add_argument("--slow-mo", type=int, default=0)
    parser.add_argument("--keep-open-ms", type=int, default=0)
    return parser.parse_args()


def main():
    args = parse_args()
    overrides = {
        "origin_zip": args.origin_zip,
        "dest_zip": args.dest_zip,
        "pallets": args.pallets,
        "length": args.length,
        "width": args.width,
        "height": args.height,
        "weight": args.weight,
        "pieces": args.pieces,
        "freight_class": args.freight_class,
    }
    overrides = {key: value for key, value in overrides.items() if has_value(value)}

    if overrides:
        data = {
            "origin_zip": "91342",
            "dest_zip": "92806",
            "pallets": "1",
            "length": "48",
            "width": "42",
            "height": "32",
            "weight": "390",
            "pieces": "10",
            "freight_class": "92.5",
            "shipment_date": datetime.now().strftime("%m/%d/%Y"),
        }
        data.update(overrides)
    else:
        client = gs_client()
        ss = client.open(SHEET_NAME)
        data = read_input(ss)

    print("PRIORITY1 INPUT:", data, flush=True)
    results = quote_priority1(data, headless=not args.show_browser, slow_mo=args.slow_mo, keep_open_ms=args.keep_open_ms)
    print("PRIORITY1 RESULTS:", results, flush=True)


if __name__ == "__main__":
    main()
