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
from sheets_utils import with_gsheets_retry

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


def today_sheet_date() -> str:
    return datetime.now().strftime("%-m/%-d/%Y")


def gs_client():
    creds = Credentials.from_service_account_file("service_account.json", scopes=SCOPES)
    return gspread.authorize(creds)


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
      Shipment Date     -> B16   (e.g. "3/11/2026")
    """
    s = spreadsheet.worksheet(INPUT_SHEET)

    def v(cell):
        val = with_gsheets_retry(lambda: s.acell(cell).value)
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
        "shipment_date": v("B16") or today_sheet_date(),
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
        if fr.locator("text=/log\\s*out/i").count() > 0:
            return True
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
        print("Already logged in. Skipping login.")
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
        raise RuntimeError("Login failed (Log Out not found). Saved login_failed.png")

    print("✅ Login successful.")


def goto_rate_quote(page):
    """Hover Shipping -> click Rate Quote"""
    target_frame = None
    shipping = None

    for fr in page.frames:
        loc = fr.locator("text=/^\\s*Shipping\\s*$/i").first
        if loc.count() > 0:
            target_frame = fr
            shipping = loc
            break

    if not target_frame:
        page.screenshot(path="shipping_missing.png", full_page=True)
        raise RuntimeError("Could not find Shipping menu. Saved shipping_missing.png")

    shipping.hover()
    page.wait_for_timeout(600)

    rate_quote = target_frame.locator("text=/Rate Quote/i").first
    if rate_quote.count() == 0:
        page.screenshot(path="rate_quote_missing.png", full_page=True)
        raise RuntimeError("Could not find Rate Quote menu item. Saved rate_quote_missing.png")

    rate_quote.click()
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(1200)

    return target_frame


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


def total_line_items(data: dict):
    items = []
    for idx, item in enumerate(data.get("pallet_items") or [], start=1):
        items.append(
            {
                "pieces": str(item.get("pieces") or "1"),
                "pallets": "1",
                "weight": str(item.get("weight") or ""),
                "length": str(item.get("length") or ""),
                "width": str(item.get("width") or ""),
                "height": str(item.get("height") or ""),
                "row": idx,
            }
        )
    if items:
        return items
    return [
        {
            "pieces": str(data["pieces"]),
            "pallets": str(data["pallets"]),
            "weight": str(data["weight"]),
            "length": str(data["length"]),
            "width": str(data["width"]),
            "height": str(data["height"]),
            "row": 1,
        }
    ]


def ensure_total_line_rows(fr, page, header, needed_rows: int):
    add_line = fr.get_by_text(re.compile(r"Add Line Item", re.I)).first
    for _ in range(max(0, needed_rows - 2)):
        try:
            if add_line.count() > 0:
                add_line.click(force=True)
                page.wait_for_timeout(400)
        except:
            break


def fill_total_line_row(header, page, row_index: int, freight_class: str, item: dict):
    input_offset = row_index * 6
    pieces_input = header.locator("xpath=following::input").nth(input_offset)
    pallets_input = header.locator("xpath=following::input").nth(input_offset + 1)
    weight_input = header.locator("xpath=following::input").nth(input_offset + 2)
    length_input = header.locator("xpath=following::input").nth(input_offset + 3)
    width_input = header.locator("xpath=following::input").nth(input_offset + 4)
    height_input = header.locator("xpath=following::input").nth(input_offset + 5)
    class_select = header.locator("xpath=following::select").nth(row_index)

    slow_type(pieces_input, item["pieces"], delay=55)
    page.wait_for_timeout(120)
    slow_type(pallets_input, item["pallets"], delay=55)
    page.wait_for_timeout(120)
    slow_type(weight_input, item["weight"], delay=55)
    page.wait_for_timeout(200)

    fc = str(freight_class).strip()
    tries = [fc, fc.zfill(3)]
    selected = False
    for opt in tries:
        try:
            class_select.select_option(opt)
            selected = True
            break
        except:
            pass
    if not selected:
        page.screenshot(path="class_select_failed.png", full_page=True)
        raise RuntimeError("Could not select freight class. Saved class_select_failed.png")

    page.wait_for_timeout(200)
    slow_type(length_input, item["length"], delay=45)
    page.wait_for_timeout(120)
    slow_type(width_input, item["width"], delay=45)
    page.wait_for_timeout(120)
    slow_type(height_input, item["height"], delay=45)
    page.wait_for_timeout(300)


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
    # Pickup/Deliver labels
    pickup_label = fr.locator("text=/Pickup\\s*From\\s*:/i").first
    deliver_label = fr.locator("text=/Deliver\\s*To\\s*:/i").first
    if pickup_label.count() == 0 or deliver_label.count() == 0:
        page.screenshot(path="rate_quote_labels_missing.png", full_page=True)
        raise RuntimeError("Pickup/Deliver labels not found. Saved rate_quote_labels_missing.png")

    pickup_zip = pickup_label.locator("xpath=following::input[1]")
    pickup_city_select = pickup_label.locator("xpath=following::select[1]")

    deliver_zip = deliver_label.locator("xpath=following::input[1]")
    deliver_city_select = deliver_label.locator("xpath=following::select[1]")

    # Fill pickup zip
    slow_type(pickup_zip, data["origin_zip"], delay=65)
    pickup_zip.press("Tab")
    page.wait_for_timeout(900)
    click_zip_search(pickup_label, page)

    desired_pickup_city = DEFAULT_PICKUP_CITY
    ensure_pickup_city_selected(fr, pickup_label, pickup_city_select, desired_pickup_city, page)

    # Fill deliver zip (hard set + verify)
    dest_zip = str(data["dest_zip"]).strip()
    set_input_value(fr, deliver_zip, dest_zip)

    ok = wait_until_input_equals(deliver_zip, dest_zip, timeout_ms=8000)
    if not ok:
        page.screenshot(path="deliver_zip_not_set.png", full_page=True)
        raise RuntimeError(f"Deliver To ZIP did not set correctly. Expected {dest_zip}. Saved deliver_zip_not_set.png")

    page.wait_for_timeout(900)

    # Select delivery city if provided
    ensure_delivery_city_selected(deliver_label, deliver_city_select, data["delivery_city"], page)

    # Line item header
    header = fr.locator("text=/Pieces\\s*\\*?\\s*Pallets\\s*\\*?\\s*Weight\\s*\\*?\\s*Class/i").first
    if header.count() == 0:
        page.screenshot(path="line_header_missing.png", full_page=True)
        raise RuntimeError("Could not find line header. Saved line_header_missing.png")

    line_items = total_line_items(data)
    ensure_total_line_rows(fr, page, header, len(line_items))

    for row_index, item in enumerate(line_items):
        fill_total_line_row(header, page, row_index, data["freight_class"], item)

    page.screenshot(path="before_submit.png", full_page=True)
    print("✅ Filled form. Saved before_submit.png")


def click_get_quote(fr, page):
    pickup_city_select = fr.locator("text=/Pickup\\s*From\\s*:/i").first.locator("xpath=following::select[1]")
    if pickup_city_select.count() > 0:
        try:
            if pickup_city_select.is_visible() and is_placeholder_city(selected_option_text(pickup_city_select)):
                page.screenshot(path="pickup_city_not_selected_before_submit.png", full_page=True)
                raise RuntimeError(
                    "Pickup city dropdown is still on the placeholder before submitting. "
                    "Saved pickup_city_not_selected_before_submit.png"
                )
        except:
            raise

    btn = fr.locator("button:has-text('Get Quote')").first
    if btn.count() == 0:
        # XPath fallback (some pages use input instead of button)
        btn = fr.locator(
            "xpath=//button[contains(.,'Get Quote')] | //input[contains(translate(@value,'GETQUOTE','getquote'),'get quote')]"
        ).first

    if btn.count() == 0:
        page.screenshot(path="get_quote_missing.png", full_page=True)
        raise RuntimeError("Get Quote button not found. Saved get_quote_missing.png")

    btn.click(force=True)
    page.wait_for_load_state("domcontentloaded")
    page.wait_for_timeout(1500)

    page.screenshot(path="after_submit.png", full_page=True)
    print("✅ Submitted. Saved after_submit.png")


def total_service_error(page) -> str:
    patterns = [
        r"unable to process your request online",
        r"delivery zip code is out of the serviceable area",
        r"out of the serviceable area",
        r"not serviceable",
    ]
    texts = []
    for fr in page.frames:
        try:
            text = fr.evaluate("() => document.body ? document.body.innerText : ''")
        except:
            text = ""
        if text:
            texts.append(text)
    joined = "\n".join(texts)
    for pattern in patterns:
        match = re.search(pattern, joined, re.I)
        if match:
            if re.search(r"serviceable area", joined, re.I):
                return "The delivery zip code is out of the serviceable area"
            return match.group(0)
    return ""


def scrape_results_any_frame(page, timeout_sec=90):
    """
    Detect results by:
      - Business Days:
      - buttons: Make Changes / Get New Quote / Get Quote #
      - any $xx.xx on the results page
    """
    start = time.time()

    def frame_text(fr):
        try:
            return fr.evaluate("() => document.body ? document.body.innerText : ''")
        except:
            return ""

    while time.time() - start < timeout_sec:
        page.wait_for_timeout(500)

        service_error = total_service_error(page)
        if service_error:
            try:
                page.screenshot(path="total_service_error.png", full_page=True)
            except:
                pass
            raise RuntimeError(service_error)

        for fr in page.frames:
            txt = frame_text(fr)
            if not txt:
                continue

            has_days = re.search(r"Business Days:\s*\d+", txt, re.I) is not None
            has_buttons = (
                fr.locator("text=/Make\\s*Changes/i").count() > 0
                or fr.locator("text=/Get\\s*New\\s*Quote/i").count() > 0
                or fr.locator("text=/Get\\s*Quote\\s*#/i").count() > 0
            )
            money_matches = re.findall(r"\$[0-9]+(?:\.[0-9]{2})", txt.replace(",", ""))

            if (has_days or has_buttons) and money_matches:
                total_price = float(money_matches[-1].replace("$", ""))

                days = None
                m = re.search(r"Business Days:\s*([0-9]+)", txt, re.I)
                if m:
                    days = int(m.group(1))

                try:
                    page.screenshot(path="results_detected.png", full_page=True)
                except:
                    pass

                return {
                    "price": total_price,
                    "transit_days": days,
                    "raw_money": money_matches[-8:],
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
    values = with_gsheets_retry(br.get_all_values)

    batch_id = None
    for r in values[1:]:
        if len(r) > 0 and r[0].strip():
            batch_id = r[0].strip()
            break
    if not batch_id:
        raise RuntimeError("No batch_id found in Broker Result. Run write_brokers.py first.")

    target_row = None
    for idx in range(2, len(values) + 1):
        row = with_gsheets_retry(lambda idx=idx: br.row_values(idx))
        if len(row) >= 2 and row[0].strip() == batch_id and row[1].strip().upper() == carrier_name.upper():
            target_row = idx
            break
    if not target_row:
        raise RuntimeError(f"Could not find {carrier_name} row for batch {batch_id}.")

    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    note_parts = ["Auto-quoted."]
    if result.get("price") is not None:
        note_parts.append(f"Quoted total: ${result.get('price')}")
    if result.get("transit_days") is not None:
        note_parts.append(f"Transit days: {result.get('transit_days')}")

    with_gsheets_retry(lambda: br.update(f"C{target_row}", [[carrier_name]]))
    with_gsheets_retry(lambda: br.update(f"D{target_row}", [[result.get("price") or ""]]))
    with_gsheets_retry(lambda: br.update(f"E{target_row}", [[result.get("transit_days") or ""]]))
    with_gsheets_retry(lambda: br.update(f"F{target_row}", [[now_str]]))
    with_gsheets_retry(lambda: br.update(f"M{target_row}", [[" ".join(note_parts)]]))

    # Broker Result layout:
    # G Origin ZIP, H Origin City, I Destination ZIP, J Destination City, K Freight Class, L Weight, M Notes
    try:
        with_gsheets_retry(lambda: br.update(f"H{target_row}", [[origin_city or ""]]))
    except:
        pass
    try:
        with_gsheets_retry(lambda: br.update(f"J{target_row}", [[dest_city or ""]]))
    except:
        pass


def quote_total(data: dict, *, headless: bool = True, slow_mo: int = 0) -> dict:
    username = os.getenv("TOTAL_USERNAME")
    password = os.getenv("TOTAL_PASSWORD")
    login_url = os.getenv("TOTAL_LOGIN_URL")

    if not username or not password or not login_url:
        raise RuntimeError("Missing TOTAL_USERNAME / TOTAL_PASSWORD / TOTAL_LOGIN_URL in .env")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo)
        page = browser.new_page()

        page.goto(login_url, wait_until="domcontentloaded")
        page.wait_for_timeout(800)

        do_login(page, username, password)

        fr = goto_rate_quote(page)

        fill_rate_quote_form(fr, data, page)

        click_get_quote(fr, page)

        scraped = scrape_results_any_frame(page, timeout_sec=90)

        browser.close()

        return {
            "carrier": "TOTAL",
            "price": scraped["price"],
            "transit_days": scraped["transit_days"],
            "raw_money": scraped["raw_money"],
        }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-browser", action="store_true")
    parser.add_argument("--slow-mo", type=int, default=0)
    args = parser.parse_args()

    client = gs_client()
    ss = client.open(SHEET_NAME)

    data = read_input(ss)
    result = quote_total(data, headless=not args.show_browser, slow_mo=args.slow_mo)

    print("TOTAL result:", result)

    write_result(
        ss,
        carrier_name="TOTAL",
        result=result,
        origin_city=data.get("pickup_city", ""),
        dest_city=data.get("delivery_city", ""),
    )

    print("✅ Wrote TOTAL quote into Broker Result.")


if __name__ == "__main__":
    main()
