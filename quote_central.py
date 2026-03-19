import argparse
import os
import re
import time
from datetime import datetime
from pathlib import Path

import gspread
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials
from playwright.sync_api import sync_playwright

# Always load .env from same folder as this script
load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=True)

# ---------- Google Sheets ----------
SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_NAME = "Freight Quote Agent (MVP)"
INPUT_SHEET = "Input"
BROKER_SHEET = "Broker Result"
DEFAULT_PICKUP_CITY = "SYLMAR, CA"
MAX_CENTRAL_LINE_ITEMS = 6


def env_first(*names: str):
    for name in names:
        value = os.getenv(name)
        if value:
            return value
    return None


def gs_client():
    creds = Credentials.from_service_account_file("service_account.json", scopes=SCOPES)
    return gspread.authorize(creds)


def central_line_items(data: dict):
    items = []
    for item in data.get("pallet_items") or []:
        items.append(
            {
                "freight_class": str(item.get("freight_class") or data.get("freight_class") or "").strip(),
                "weight": str(item.get("weight") or item.get("weight_lb") or "").strip(),
            }
        )

    if items:
        return items[:MAX_CENTRAL_LINE_ITEMS]

    pallet_count = max(1, min(int(str(data.get("pallets") or "1").strip() or "1"), MAX_CENTRAL_LINE_ITEMS))
    default_class = str(data.get("freight_class") or "").strip()
    default_weight = str(data.get("weight") or "").strip()
    return [{"freight_class": default_class, "weight": default_weight} for _ in range(pallet_count)]


def read_input(spreadsheet):
    """
    Based on your Input tab:
      Origin ZIP        -> B3
      Destination ZIP   -> B4
      Pallet Count      -> B5
      Length            -> B6
      Width             -> B7
      Height            -> B8
      Weight            -> B9
      Pieces            -> B10
      Freight Class     -> B13
      Pickup City       -> B14   (e.g. "SAN FERNANDO, CA")
      Delivery City     -> B15   (e.g. "CHATSWORTH, CA")
    """
    s = spreadsheet.worksheet(INPUT_SHEET)

    def v(cell):
        val = s.acell(cell).value
        return val.strip() if isinstance(val, str) else val

    return {
        "origin_zip": v("B3"),
        "dest_zip": v("B4"),
        "pallets": v("B5") or "1",
        "length": v("B6"),
        "width": v("B7"),
        "height": v("B8"),
        "weight": v("B9"),
        "pieces": v("B10") or "1",
        "freight_class": v("B13"),
        "pickup_city": DEFAULT_PICKUP_CITY,
        "delivery_city": v("B15") or "",
    }


# ---------- helpers ----------

def set_input_value(fr, locator, value: str):
    """Hard-set input value + trigger events (works for annoying JS fields)."""
    locator.scroll_into_view_if_needed()
    locator.click()
    locator.fill("")
    locator.evaluate(
        "(el, val) => { el.value = val; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }",
        str(value),
    )
    locator.press("Tab")


def wait_until_input_equals(locator, expected: str, timeout_ms=8000):
    locator.wait_for(state="visible", timeout=timeout_ms)
    start = time.time()
    while (time.time() - start) * 1000 < timeout_ms:
        try:
            v = locator.input_value()
            if v.strip() == expected:
                return True
        except:
            pass
        locator.page.wait_for_timeout(200)
    return False


def slow_type(locator, text, delay=60):
    locator.scroll_into_view_if_needed()
    locator.click()
    locator.fill("")
    locator.type(str(text), delay=delay)


def is_logged_in_any_frame(page) -> bool:
    for fr in page.frames:
        indicators = [
            fr.locator("text=/log\\s*out/i").first,
            fr.locator("text=/sign\\s*out/i").first,
            fr.locator("text=/account\\s*details/i").first,
            fr.locator("text=/rate\\s*quote/i").first,
        ]
        for indicator in indicators:
            try:
                if indicator.count() > 0:
                    return True
            except:
                pass
    return False


def find_login_controls_any_frame(page):
    """
    Returns (frame, user_input, pass_input, login_btn)
    """
    for fr in page.frames:
        # 1) Find button by visible text
        login_btn = fr.get_by_text(re.compile(r"^\s*Log\s*In\s*$", re.I)).first
        if login_btn.count() == 0:
            # 2) Find <input value="Log In"> or similar via XPath
            login_btn = fr.locator(
                "xpath=//input[contains(translate(@value,'LOGIN','login'),'log') and contains(translate(@value,'LOGIN','login'),'in')]"
                " | //button[contains(., 'Log In') or contains(., 'Login')]"
                " | //a[contains(., 'Log In') or contains(., 'Login')]"
            ).first

        if login_btn.count() > 0:
            pass_input = login_btn.locator("xpath=preceding::input[1]")
            user_input = login_btn.locator("xpath=preceding::input[2]")
            if user_input.count() > 0 and pass_input.count() > 0:
                return fr, user_input, pass_input, login_btn

        # 3) Fallback: Username:/Password: labels
        user_label = fr.locator("text=/Username\\s*:/i").first
        pass_label = fr.locator("text=/Password\\s*:/i").first
        if user_label.count() > 0 and pass_label.count() > 0:
            user_input = user_label.locator("xpath=following::input[1]")
            pass_input = pass_label.locator("xpath=following::input[1]")
            login_btn2 = fr.get_by_text(re.compile(r"^\s*Log\s*In\s*$", re.I)).first
            if login_btn2.count() == 0:
                login_btn2 = fr.locator(
                    "xpath=//input[contains(translate(@value,'LOGIN','login'),'log') and contains(translate(@value,'LOGIN','login'),'in')]"
                ).first
            if user_input.count() > 0 and pass_input.count() > 0 and login_btn2.count() > 0:
                return fr, user_input, pass_input, login_btn2

    return None, None, None, None


def do_login(page, username, password):
    if is_logged_in_any_frame(page):
        print("Already logged in. Skipping login.", flush=True)
        return

    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(700)

    fr, user_input, pass_input, login_btn = find_login_controls_any_frame(page)
    if fr is None:
        page.screenshot(path="login_controls_missing.png", full_page=True)
        raise RuntimeError("Could not find login controls. Saved login_controls_missing.png")

    slow_type(user_input, username, delay=80)
    page.wait_for_timeout(200)
    slow_type(pass_input, password, delay=80)
    page.wait_for_timeout(350)

    handle = login_btn.element_handle()
    if handle is None:
        page.screenshot(path="login_btn_no_handle.png", full_page=True)
        raise RuntimeError("Could not get handle for login button. Saved login_btn_no_handle.png")

    page.evaluate("(el) => el.click()", handle)

    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(1200)

    if not is_logged_in_any_frame(page):
        page.screenshot(path="login_failed.png", full_page=True)
        raise RuntimeError("Login failed (logout indicator not found). Saved login_failed.png")

    print("✅ Central login successful.", flush=True)


def goto_rate_quote(page):
    candidates = [page, *page.frames]
    for ctx in candidates:
        rate_quote = ctx.locator(
            "xpath=//a[contains(normalize-space(.), 'Rate Quote')]"
            " | //button[contains(normalize-space(.), 'Rate Quote')]"
            " | //input[contains(translate(@value,'RATE QUOTE','rate quote'),'rate quote')]"
        ).first
        try:
            if rate_quote.count() == 0:
                rate_quote = ctx.get_by_text(re.compile(r"^\s*Rate\s*Quote\s*$", re.I)).first
        except:
            pass

        try:
            if rate_quote.count() > 0:
                rate_quote.click(force=True)
                page.wait_for_load_state("domcontentloaded")
                page.wait_for_timeout(1200)
                return ctx
        except:
            pass

    page.screenshot(path="rate_quote_missing.png", full_page=True)
    raise RuntimeError("Could not find Rate Quote link. Saved rate_quote_missing.png")


def select_city_by_text(city_select_locator, desired_text: str):
    """
    Select a city option by visible label.
    """
    desired = " ".join((desired_text or "").strip().upper().split())
    if not desired:
        return False

    opts = city_select_locator.locator("option")
    n = opts.count()

    try:
        city_select_locator.select_option(label=desired_text)
        selected = selected_option_text(city_select_locator)
        if desired in " ".join(selected.upper().split()):
            return True
    except:
        pass

    # exact match
    for i in range(n):
        opt = opts.nth(i)
        label = " ".join((opt.inner_text() or "").strip().upper().split())
        if label == desired:
            val = opt.get_attribute("value")
            if val:
                city_select_locator.select_option(value=val)
            else:
                city_select_locator.select_option(label=opt.inner_text())
            selected = selected_option_text(city_select_locator)
            return desired in " ".join(selected.upper().split())

    # contains match
    for i in range(n):
        opt = opts.nth(i)
        label = " ".join((opt.inner_text() or "").strip().upper().split())
        if desired in label:
            val = opt.get_attribute("value")
            if val:
                city_select_locator.select_option(value=val)
            else:
                city_select_locator.select_option(label=opt.inner_text())
            selected = selected_option_text(city_select_locator)
            return desired in " ".join(selected.upper().split())

    return False


def wait_for_select_options(city_select_locator, timeout_ms=8000):
    city_select_locator.wait_for(state="attached", timeout=timeout_ms)
    start = time.time()
    while (time.time() - start) * 1000 < timeout_ms:
        try:
            if city_select_locator.locator("option").count() > 1:
                return True
        except:
            pass
        city_select_locator.page.wait_for_timeout(250)
    return False


def wait_for_select_option_text(city_select_locator, desired_text: str, timeout_ms=8000):
    desired = " ".join((desired_text or "").strip().upper().split())
    city_select_locator.wait_for(state="attached", timeout=timeout_ms)
    start = time.time()
    while (time.time() - start) * 1000 < timeout_ms:
        try:
            options = city_select_locator.locator("option").all_inner_texts()
            normalized = [" ".join((opt or "").strip().upper().split()) for opt in options]
            if any(desired == opt or desired in opt for opt in normalized):
                return True
        except:
            pass
        city_select_locator.page.wait_for_timeout(250)
    return False


def click_zip_search(label_locator, page):
    try:
        zip_search = label_locator.locator("xpath=following::a[contains(., 'Zip Search')][1]").first
        if zip_search.count() > 0:
            zip_search.click(force=True)
            page.wait_for_timeout(700)
            return True
    except:
        pass
    return False


def choose_city_from_zip_search_popup(page, desired_city: str, timeout_ms=8000):
    desired = " ".join((desired_city or "").strip().upper().replace(",", " ").split())
    start = time.time()
    seen_pages = set()

    while (time.time() - start) * 1000 < timeout_ms:
        for popup in page.context.pages:
            if popup == page or popup.is_closed():
                continue
            if popup in seen_pages:
                pass
            try:
                popup.wait_for_load_state("domcontentloaded", timeout=1000)
            except:
                pass

            try:
                title = popup.title()
            except:
                title = ""

            try:
                body_text = popup.locator("body").inner_text(timeout=1000)
            except:
                body_text = ""

            if "ZIP / CITY SEARCH" not in title.upper() and "DOUBLE-CLICK A ROW" not in body_text.upper():
                continue

            city_link = popup.get_by_text(re.compile(rf"^{re.escape(desired_city.split(',')[0].strip())}$", re.I)).first
            if city_link.count() > 0:
                city_link.dblclick(force=True)
                page.wait_for_timeout(800)
                return True

            rows = popup.locator("xpath=//tr[td]")
            row_count = rows.count()
            for i in range(row_count):
                row = rows.nth(i)
                row_text = " ".join((row.inner_text() or "").upper().replace(",", " ").split())
                if desired in row_text:
                    row.dblclick(force=True)
                    page.wait_for_timeout(800)
                    return True
        page.wait_for_timeout(250)

    return False


def city_label_matches(label_locator, desired_text: str) -> bool:
    desired = " ".join((desired_text or "").upper().split())
    try:
        actual = " ".join((label_locator.inner_text() or "").upper().split())
    except:
        return False
    return desired in actual


def frame_has_city_text(fr, desired_text: str) -> bool:
    desired = (desired_text or "").strip()
    if not desired:
        return False
    try:
        return fr.get_by_text(re.compile(rf"\b{re.escape(desired)}\b", re.I)).count() > 0
    except:
        return False


def selected_option_text(select_locator) -> str:
    try:
        return (select_locator.locator("option:checked").first.inner_text() or "").strip()
    except:
        return ""


def force_select_option_text(select_locator, desired_text: str):
    try:
        return select_locator.evaluate(
            """
            (sel, desiredText) => {
                const norm = (s) => (s || "").replace(/\\s+/g, " ").trim().toUpperCase();
                const desired = norm(desiredText);
                const options = Array.from(sel.options || []);
                const match =
                    options.find((opt) => norm(opt.textContent) === desired) ||
                    options.find((opt) => norm(opt.textContent).includes(desired));

                if (!match) {
                    return {
                        ok: false,
                        selected: sel.options[sel.selectedIndex]?.textContent?.trim() || "",
                        options: options.map((opt) => (opt.textContent || "").trim()),
                    };
                }

                sel.value = match.value;
                match.selected = true;
                sel.selectedIndex = options.indexOf(match);
                sel.dispatchEvent(new Event("input", { bubbles: true }));
                sel.dispatchEvent(new Event("change", { bubbles: true }));

                return {
                    ok: true,
                    selected: sel.options[sel.selectedIndex]?.textContent?.trim() || "",
                    options: options.map((opt) => (opt.textContent || "").trim()),
                };
            }
            """,
            desired_text,
        )
    except:
        return {"ok": False, "selected": "", "options": []}


def is_placeholder_city(selected_text: str) -> bool:
    normalized = " ".join((selected_text or "").upper().split())
    return normalized in {"", "-- PLEASE SELECT --", "PLEASE SELECT", "SELECT"}


def ensure_pickup_city_selected(fr, pickup_label, pickup_city_select, desired_pickup_city: str, page):
    if pickup_city_select.count() > 0:
        try:
            if pickup_city_select.is_visible():
                wait_for_select_options(pickup_city_select, timeout_ms=8000)
                if is_placeholder_city(selected_option_text(pickup_city_select)):
                    click_zip_search(pickup_label, page)
                    choose_city_from_zip_search_popup(page, desired_pickup_city, timeout_ms=8000)
                    wait_for_select_option_text(pickup_city_select, desired_pickup_city, timeout_ms=8000)

                if not select_city_by_text(pickup_city_select, desired_pickup_city):
                    forced = force_select_option_text(pickup_city_select, desired_pickup_city)
                    if not forced.get("ok"):
                        click_zip_search(pickup_label, page)
                        choose_city_from_zip_search_popup(page, desired_pickup_city, timeout_ms=8000)
                        wait_for_select_option_text(pickup_city_select, desired_pickup_city, timeout_ms=8000)
                        forced = force_select_option_text(pickup_city_select, desired_pickup_city)

                    if forced.get("ok"):
                        page.wait_for_timeout(300)
                    else:
                        try:
                            options = pickup_city_select.locator("option").all_inner_texts()
                        except:
                            options = forced.get("options", [])
                        page.screenshot(path="pickup_city_not_found.png", full_page=True)
                        raise RuntimeError(
                            f"Pickup city dropdown could not be set to {desired_pickup_city}. "
                            f"Current selection: {selected_option_text(pickup_city_select) or forced.get('selected') or 'none'}. "
                            f"Options seen: {options[:10]}. "
                            "Saved pickup_city_not_found.png"
                        )

                page.wait_for_timeout(300)
                selected = selected_option_text(pickup_city_select)
                if is_placeholder_city(selected):
                    forced = force_select_option_text(pickup_city_select, desired_pickup_city)
                    page.wait_for_timeout(300)
                    selected = selected_option_text(pickup_city_select) or forced.get("selected", "")

                if is_placeholder_city(selected) or desired_pickup_city.upper() not in selected.upper():
                    click_zip_search(pickup_label, page)
                    choose_city_from_zip_search_popup(page, desired_pickup_city, timeout_ms=8000)
                    wait_for_select_option_text(pickup_city_select, desired_pickup_city, timeout_ms=8000)
                    force_select_option_text(pickup_city_select, desired_pickup_city)
                    page.wait_for_timeout(300)
                    selected = selected_option_text(pickup_city_select)

                if is_placeholder_city(selected) or desired_pickup_city.upper() not in selected.upper():
                    try:
                        options = pickup_city_select.locator("option").all_inner_texts()
                    except:
                        options = []
                    page.screenshot(path="pickup_city_not_found.png", full_page=True)
                    raise RuntimeError(
                        f"Pickup city dropdown is still not set to {desired_pickup_city}. "
                        f"Current selection: {selected or 'none'}. "
                        f"Options seen: {options[:10]}. "
                        "Saved pickup_city_not_found.png"
                    )
                return
        except:
            raise

    if city_label_matches(pickup_label, desired_pickup_city) or frame_has_city_text(fr, desired_pickup_city):
        return

    page.screenshot(path="pickup_city_not_found.png", full_page=True)
    raise RuntimeError(
        f"Pickup city was not set to {desired_pickup_city}. "
        f"Current selection: {selected_option_text(pickup_city_select) or 'none'}. "
        "Saved pickup_city_not_found.png"
    )


def ensure_delivery_city_selected(deliver_label, deliver_city_select, desired_delivery_city: str, page):
    if not desired_delivery_city:
        return

    if city_label_matches(deliver_label, desired_delivery_city):
        return

    if deliver_city_select.count() == 0:
        return

    try:
        if not deliver_city_select.is_visible():
            return
    except:
        return

    wait_for_select_options(deliver_city_select, timeout_ms=8000)
    wait_for_select_option_text(deliver_city_select, desired_delivery_city, timeout_ms=8000)

    if not select_city_by_text(deliver_city_select, desired_delivery_city):
        forced = force_select_option_text(deliver_city_select, desired_delivery_city)
        page.wait_for_timeout(300)
        selected = selected_option_text(deliver_city_select) or forced.get("selected", "")
        if desired_delivery_city.upper() in selected.upper():
            return

        try:
            options = deliver_city_select.locator("option").all_inner_texts()
        except:
            options = forced.get("options", [])
        page.screenshot(path="delivery_city_not_found.png", full_page=True)
        raise RuntimeError(
            f"Delivery city dropdown could not be set to {desired_delivery_city}. "
            f"Current selection: {selected or 'none'}. "
            f"Options seen: {options[:10]}. "
            "Saved delivery_city_not_found.png"
        )

    page.wait_for_timeout(300)
    selected = selected_option_text(deliver_city_select)
    if desired_delivery_city.upper() not in selected.upper():
        try:
            options = deliver_city_select.locator("option").all_inner_texts()
        except:
            options = []
        page.screenshot(path="delivery_city_not_found.png", full_page=True)
        raise RuntimeError(
            f"Delivery city dropdown is still not set to {desired_delivery_city}. "
            f"Current selection: {selected or 'none'}. "
            f"Options seen: {options[:10]}. "
            "Saved delivery_city_not_found.png"
        )


def fill_rate_quote_form(fr, data, page):
    shipment_date = fr.locator(
        "xpath=(//td[contains(normalize-space(.), 'Shipment Date:')]/following-sibling::td//input[not(@type='hidden')])[1]"
    ).first
    origin_zip = fr.locator(
        "xpath=(//tr[td[normalize-space()='Origin:']]//input[not(@type='hidden')])[1]"
    ).first
    dest_zip = fr.locator(
        "xpath=(//tr[td[normalize-space()='Dest:']]//input[not(@type='hidden')])[1]"
    ).first
    if origin_zip.count() == 0 or dest_zip.count() == 0:
        page.screenshot(path="rate_quote_labels_missing.png", full_page=True)
        raise RuntimeError("Central rate form ZIP inputs not found. Saved rate_quote_labels_missing.png")

    shipment_date_text = datetime.now().strftime("%m/%d/%Y")
    if shipment_date.count() > 0:
        set_input_value(fr, shipment_date, shipment_date_text)

    set_input_value(fr, origin_zip, data["origin_zip"])
    set_input_value(fr, dest_zip, data["dest_zip"])

    line_items = central_line_items(data)
    for index, item in enumerate(line_items, start=1):
        class_select = fr.locator(
            f"xpath=(//tr[td[normalize-space()='{index}']]//select[not(@disabled)])[1]"
        ).first
        weight_input = fr.locator(
            f"xpath=(//tr[td[normalize-space()='{index}']]//input[not(@type='hidden') and not(@readonly)])[1]"
        ).first

        if class_select.count() == 0:
            page.screenshot(path="class_select_failed.png", full_page=True)
            raise RuntimeError(f"Could not find Central freight class select for row {index}. Saved class_select_failed.png")

        fc = str(item.get("freight_class") or "").strip()
        selected = False
        for opt in [fc, fc.zfill(3)]:
            try:
                class_select.select_option(value=opt)
                selected = True
                break
            except:
                pass
            try:
                class_select.select_option(label=opt)
                selected = True
                break
            except:
                pass
        if not selected:
            options = []
            try:
                options = class_select.locator("option").all_inner_texts()
            except:
                pass
            page.screenshot(path="class_select_failed.png", full_page=True)
            raise RuntimeError(
                f"Could not select Central freight class {fc} on row {index}. Options: {options[:20]}. Saved class_select_failed.png"
            )

        if weight_input.count() == 0:
            page.screenshot(path="weight_input_missing.png", full_page=True)
            raise RuntimeError(f"Could not find Central weight input for row {index}. Saved weight_input_missing.png")

        set_input_value(fr, weight_input, item.get("weight") or "")

    page.wait_for_timeout(300)
    page.screenshot(path="before_submit.png", full_page=True)
    print("✅ Filled Central form. Saved before_submit.png", flush=True)


def click_get_quote(fr, page):
    btn = fr.locator("button:has-text('Get Rate')").first
    if btn.count() == 0:
        btn = fr.locator(
            "xpath=//button[contains(.,'Get Rate') or contains(.,'Get Quote')]"
            " | //input[contains(translate(@value,'GETRATE','getrate'),'get rate')]"
            " | //input[contains(translate(@value,'GETQUOTE','getquote'),'get quote')]"
        ).first

    if btn.count() == 0:
        page.screenshot(path="get_quote_missing.png", full_page=True)
        raise RuntimeError("Get Rate button not found. Saved get_quote_missing.png")

    btn.click(force=True)
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(1500)

    page.screenshot(path="after_submit.png", full_page=True)
    print("✅ Submitted Central rate request. Saved after_submit.png", flush=True)


def scrape_results_any_frame(page, timeout_sec=90):
    start = time.time()

    def frame_text(ctx):
        try:
            return ctx.evaluate("() => document.body ? document.body.innerText : ''")
        except:
            return ""

    while time.time() - start < timeout_sec:
        page.wait_for_timeout(500)

        try:
            parsed = page.evaluate(
                """
                () => {
                    const pick = (id) => {
                        const el = document.getElementById(id);
                        return (el?.textContent || "").replace(/\\s+/g, " ").trim();
                    };

                    const quote = pick("ctl00_MainContentPlaceHolder_lQuoteNumber");
                    const usTotal = pick("ctl00_MainContentPlaceHolder_labelTotalCharge");
                    const cnTotal = pick("ctl00_MainContentPlaceHolder_lableTotalAnotherCurrency");
                    const destDays = pick("ctl00_MainContentPlaceHolder_lbDestDays");

                    if (!quote || !usTotal) return null;

                    return {
                        quote,
                        us_total: usTotal,
                        cn_total: cnTotal,
                        dest_days: destDays,
                    };
                }
                """
            )
        except:
            parsed = None

        if parsed and parsed.get("us_total"):
            us_total_match = re.search(r"US\s*\$([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2}))", parsed["us_total"], re.I)
            cn_total_match = re.search(r"CN\s*\$([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2}))", parsed.get("cn_total", ""), re.I)
            if us_total_match:
                us_total = us_total_match.group(1)
                cn_total = cn_total_match.group(1) if cn_total_match else ""
                days = int(parsed["dest_days"]) if str(parsed.get("dest_days", "")).isdigit() else None
                try:
                    page.screenshot(path="central_results_detected.png", full_page=True)
                except:
                    pass
                return {
                    "price": float(us_total.replace(",", "")),
                    "transit_days": days,
                    "raw_money": [f"$US {us_total}"] + ([f"$CN {cn_total}"] if cn_total else []),
                    "debug_total_us": us_total,
                    "debug_total_cn": cn_total,
                }

        for ctx in [page, *page.frames]:
            txt = frame_text(ctx)
            if not txt:
                continue

            quote_match = re.search(r"Quote\s*#\s*([0-9]+)", txt, re.I)
            us_total_match = re.search(r"\bUS\s*\$([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2}))", txt, re.I)
            cn_total_match = re.search(r"\bCN\s*\$([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2}))", txt, re.I)
            money_matches = re.findall(r"\$[0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2})", txt)

            if quote_match and (us_total_match or money_matches):
                us_total = us_total_match.group(1) if us_total_match else ""
                cn_total = cn_total_match.group(1) if cn_total_match else ""
                total_price = float((us_total or money_matches[-1].replace("$", "")).replace(",", ""))
                days = None
                m = re.search(r"\bOrigin:\s+[0-9]+\s+\w+\s+[0-9\- ]+\s+\([0-9]{3}\)\s*[0-9\-]+\s+([0-9]+)\b", txt, re.I)
                if not m:
                    m = re.search(r"\bDest:\s+[0-9]+\s+\w+\s+[0-9\- ]+\s+\([0-9]{3}\)\s*[0-9\-]+\s+([0-9]+)\b", txt, re.I)
                if not m:
                    m = re.search(r"\bDays\b.*?\b([0-9]+)\b", txt, re.I | re.S)
                if m:
                    days = int(m.group(1))

                try:
                    page.screenshot(path="central_results_detected.png", full_page=True)
                except:
                    pass

                return {
                    "price": total_price,
                    "transit_days": days,
                    "raw_money": ([f"$US {us_total}"] if us_total else []) + ([f"$CN {cn_total}"] if cn_total else []) + money_matches[-8:],
                    "debug_total_us": us_total,
                    "debug_total_cn": cn_total,
                }

    page.screenshot(path="results_not_found.png", full_page=True)
    try:
        html = page.content()
        with open("results_not_found.html", "w", encoding="utf-8") as f:
            f.write(html)
    except:
        pass

    raise RuntimeError("Could not detect results. Saved results_not_found.png/html")


def write_result(spreadsheet, carrier_name: str, result: dict, origin_city: str, dest_city: str):
    br = spreadsheet.worksheet(BROKER_SHEET)
    values = br.get_all_values()

    batch_id = None
    for r in values[1:]:
        if len(r) > 0 and r[0].strip():
            batch_id = r[0].strip()
            break
    if not batch_id:
        raise RuntimeError("No batch_id found in Broker Result. Run write_brokers.py first.")

    target_row = None
    for idx in range(2, len(values) + 1):
        row = br.row_values(idx)
        if len(row) >= 2 and row[0].strip() == batch_id and row[1].strip().upper() == carrier_name.upper():
            target_row = idx
            break
    if not target_row:
        raise RuntimeError(f"Could not find {carrier_name} row for batch {batch_id}.")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    br.update(f"C{target_row}", [[carrier_name]])
    br.update(f"D{target_row}", [[result.get("price") or ""]])
    br.update(f"E{target_row}", [[result.get("transit_days") or ""]])
    br.update(f"F{target_row}", [[now_str]])
    br.update(f"K{target_row}", [[f"Auto-quoted. Money found: {result.get('raw_money')}"]])

    # If you added Origin City column H and Destination City column I in Broker Result:
    try:
        br.update(f"H{target_row}", [[origin_city or ""]])
    except:
        pass
    try:
        br.update(f"I{target_row}", [[dest_city or ""]])
    except:
        pass


def quote_central(data: dict, *, headless: bool = True, slow_mo: int = 0) -> dict:
    username = env_first("CENTRAL_USERNAME", "CENTRAL_USER", "TOTAL_USERNAME")
    password = env_first("CENTRAL_PASSWORD", "CENTRAL_PASS", "TOTAL_PASSWORD")
    login_url = env_first("CENTRAL_LOGIN_URL", "CENTRAL_URL", "TOTAL_LOGIN_URL")

    if not username or not password or not login_url:
        raise RuntimeError(
            "Missing CENTRAL_USERNAME / CENTRAL_PASSWORD / CENTRAL_LOGIN_URL in .env"
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo)
        page = browser.new_page()

        print("Opening Central login page...", flush=True)
        page.goto(login_url, wait_until="domcontentloaded")
        page.wait_for_timeout(800)

        print("Logging into Central...", flush=True)
        do_login(page, username, password)

        print("Opening Central Rate Quote page...", flush=True)
        fr = goto_rate_quote(page)

        print("Filling Central rate form...", flush=True)
        fill_rate_quote_form(fr, data, page)

        print("Submitting Central rate form...", flush=True)
        click_get_quote(fr, page)

        print("Scraping Central results...", flush=True)
        scraped = scrape_results_any_frame(page, timeout_sec=90)

        browser.close()

        return {
            "carrier": "CENTRAL TRANSPORTATION",
            "price": scraped["price"],
            "transit_days": scraped["transit_days"],
            "raw_money": scraped["raw_money"],
        }


def quote_total(data: dict) -> dict:
    return quote_central(data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-browser", action="store_true")
    parser.add_argument("--slow-mo", type=int, default=0)
    args = parser.parse_args()

    client = gs_client()
    ss = client.open(SHEET_NAME)

    data = read_input(ss)
    result = quote_central(data, headless=not args.show_browser, slow_mo=args.slow_mo)

    print("CENTRAL TRANSPORTATION result:", result)

    write_result(
        ss,
        carrier_name="CENTRAL TRANSPORTATION",
        result=result,
        origin_city=data.get("pickup_city", ""),
        dest_city=data.get("delivery_city", ""),
    )

    print("✅ Wrote CENTRAL TRANSPORTATION quote into Broker Result.")


if __name__ == "__main__":
    main()
