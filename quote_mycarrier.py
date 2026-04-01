import argparse
import math
import os
import re
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from quote_total import SHEET_NAME, gs_client, read_input

load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=True)

STEP_DELAY_MS = 350


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
    locator.wait_for(state="visible", timeout=15000)
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


def maybe_close_banner(page):
    candidates = [
        "button:has-text('Accept')",
        "button:has-text('Accept All')",
        "button:has-text('Close')",
        "button[aria-label='Close']",
        ".osano-cm-close",
        ".cc-dismiss",
        "#hs-eu-confirmation-button",
    ]
    for selector in candidates:
        locator = page.locator(selector).first
        try:
            if locator.count() > 0 and locator.is_visible():
                locator.click(force=True)
                wait_after_step(page)
        except Exception:
            pass


def quote_line_items(data: dict):
    default_item = {
        "count": integer_string(data.get("pallets") or "1") or "1",
        "length": integer_string(data.get("length"), round_up=True),
        "width": integer_string(data.get("width"), round_up=True),
        "height": integer_string(data.get("height"), round_up=True),
        "freight_class": str(data.get("freight_class") or "").strip(),
        "pieces": integer_string(data.get("pieces") or "1") or "1",
        "weight": integer_string(data.get("weight"), round_up=True),
    }
    return [default_item]


def login_complete(page) -> bool:
    url = page.url or ""
    body_text = current_body_text(page)
    success_markers = [
        "/ui/customer/home",
        "/ui/customer/quote",
        "LTL Quote",
        "Quote History",
        "Start a Quote",
        "Home",
        "Shipments",
    ]
    return any(marker in url or marker in body_text for marker in success_markers)


def do_login(page, username: str, password: str):
    page.goto(env_first("MYCARRIER_LOGIN_URL"), wait_until="networkidle", timeout=120000)
    page.get_by_label("Email address").fill(username)
    page.get_by_text("Continue", exact=True).click()
    page.wait_for_load_state("networkidle", timeout=120000)
    page.locator("input[type=password]").first.fill(password)
    page.get_by_text("Continue", exact=True).click()
    try:
        page.wait_for_load_state("domcontentloaded", timeout=120000)
    except Exception:
        pass

    for _ in range(12):
        if login_complete(page):
            return
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        page.wait_for_timeout(3000)

    body_text = current_body_text(page)
    Path("mycarrier_login_failed.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path="mycarrier_login_failed.png", full_page=True)
    raise RuntimeError(
        f"MyCarrier login failed at URL {page.url!r}. "
        f"Visible text sample: {body_text[:300]!r}. "
        "Saved mycarrier_login_failed.png and mycarrier_login_failed.html"
    )


def goto_ltl_quote(page):
    def quote_form_loaded() -> bool:
        body_text = current_body_text(page)
        return any(
            marker in body_text
            for marker in [
                "Shipment items",
                "Select Carrier",
                "Deliver to: City/Zip or Address Book",
            ]
        )

    attempts = [
        ("sidebar LTL Quote", lambda: page.get_by_role("link", name="LTL Quote").click(timeout=5000)),
        ("dashboard Start a Quote", lambda: page.get_by_role("button", name="Start a Quote").click(timeout=5000)),
        ("direct /ui/customer/quote", lambda: page.goto("https://mycarriertms.com/ui/customer/quote", wait_until="domcontentloaded", timeout=120000)),
    ]

    for label, action in attempts:
        try:
            print(f"MyCarrier opening quote page via {label}...", flush=True)
            action()
        except Exception:
            continue

        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(6000)
        maybe_close_banner(page)

        if quote_form_loaded():
            return

    body_text = current_body_text(page)
    Path("mycarrier_ltl_quote_missing.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path="mycarrier_ltl_quote_missing.png", full_page=True)
    raise RuntimeError(
        f"Could not open MyCarrier quote page from URL {page.url!r}. "
        f"Visible text sample: {body_text[:300]!r}. "
        "Saved mycarrier_ltl_quote_missing.png and mycarrier_ltl_quote_missing.html"
    )


def ensure_origin_defaults_match(page, origin_zip: str):
    origin_block = page.locator("cup-origin").first
    text = normalize_space(origin_block.inner_text())
    if origin_zip and origin_zip not in text:
        print(f"MyCarrier origin block did not show ZIP {origin_zip}; keeping default ship-from selection.", flush=True)


def fill_origin(page, origin_zip: str):
    print(f"MyCarrier origin ZIP -> {origin_zip}", flush=True)
    origin_dropdown = page.locator("[data-id='quote-origin-ship-from']").first
    if origin_dropdown.count() == 0:
        origin_dropdown = page.locator("cup-origin itm-dropdown").first
    if origin_dropdown.count() == 0:
        page.screenshot(path="mycarrier_origin_missing.png", full_page=True)
        raise RuntimeError("Could not find MyCarrier Ship from dropdown. Saved mycarrier_origin_missing.png")

    origin_dropdown.scroll_into_view_if_needed()
    origin_dropdown.click(force=True)
    wait_after_step(page, 2)

    option = page.locator(
        f".ui-dropdown-item:has-text('{origin_zip}'), [role='option']:has-text('{origin_zip}'), li:has-text('{origin_zip}')"
    ).first
    if option.count() == 0:
        all_options = page.locator(".ui-dropdown-item, [role='option'], .p-dropdown-item")
        if all_options.count() > 1:
            option = all_options.nth(1)
        else:
            option = all_options.first
    if option.count() == 0:
        page.screenshot(path="mycarrier_origin_option_missing.png", full_page=True)
        raise RuntimeError("Could not find MyCarrier Ship from option. Saved mycarrier_origin_option_missing.png")

    option.click(force=True)
    wait_after_step(page, 2)
    print("MyCarrier origin selected.", flush=True)


def fill_destination(page, dest_zip: str):
    print(f"MyCarrier destination ZIP -> {dest_zip}", flush=True)
    dest_input = page.locator("input[data-placeholder='Deliver to: City/Zip or Address Book']").first
    dest_input.wait_for(state="visible", timeout=15000)
    dest_input.scroll_into_view_if_needed()
    dest_input.click(force=True)
    dest_input.fill("")
    dest_input.type(dest_zip, delay=60)
    wait_after_step(page, 2)

    def destination_selected() -> bool:
        body_text = current_body_text(page)
        if "Select result from list" in body_text:
            return False
        try:
            value = normalize_space(dest_input.input_value())
        except Exception:
            value = ""
        if value and value != normalize_space(dest_zip):
            return True
        if "Destination Zip:" in body_text and dest_zip in body_text:
            return True
        return False

    option_selectors = [
        f"mat-option:has-text('{dest_zip}')",
        f".mat-option:has-text('{dest_zip}')",
        f"[role='option']:has-text('{dest_zip}')",
        f".mat-option-text:has-text('{dest_zip}')",
        "mat-option",
        ".mat-option",
        ".mat-option-text",
        ".pac-item",
        ".address-lookup__results li",
        "[role='option']",
        ".mat-mdc-option",
        ".mdc-list-item",
    ]
    for _ in range(3):
        for selector in option_selectors:
            option = page.locator(selector).first
            try:
                if option.count() > 0 and option.is_visible():
                    option.click(force=True)
                    wait_after_step(page, 2)
                    if destination_selected():
                        print("MyCarrier destination selected from suggestions.", flush=True)
                        return
            except Exception:
                pass

        try:
            dest_input.click(force=True)
            dest_input.press("ArrowDown")
            wait_after_step(page)
            dest_input.press("Enter")
            wait_after_step(page, 2)
            if destination_selected():
                print("MyCarrier destination selected by keyboard.", flush=True)
                return
        except Exception:
            pass

        search_button = page.locator("#destination-addressesBox-72PT6Y button, [id^='destination-addressesBox-'] button").first
        try:
            if search_button.count() > 0:
                search_button.click(force=True)
                wait_after_step(page, 2)
                if destination_selected():
                    print("MyCarrier destination fallback search click used.", flush=True)
                    return
        except Exception:
            pass

    page.screenshot(path="mycarrier_destination_selection_failed.png", full_page=True)
    Path("mycarrier_destination_selection_failed.html").write_text(page.content(), encoding="utf-8")
    raise RuntimeError(
        "Could not confirm MyCarrier destination selection. "
        "Saved mycarrier_destination_selection_failed.png and mycarrier_destination_selection_failed.html"
    )


def shipment_item_row(page, index: int):
    return page.locator("cup-shipment-items .shipment-item").nth(index)


def add_handling_unit(page):
    add_link = page.locator("a.add-h-u-link, #shipmentI-addHandlingUnit-MTzZkc").first
    if add_link.count() == 0:
        raise RuntimeError("Could not find MyCarrier Add H/U control.")
    add_link.scroll_into_view_if_needed()
    add_link.click(force=True)
    wait_after_step(page, 2)


def fill_shipment_items(page, data: dict):
    items = quote_line_items(data)
    for idx, item in enumerate(items):
        print(f"MyCarrier filling shipment row {idx + 1}: {item}", flush=True)
        if idx > 0:
            add_handling_unit(page)
        row = shipment_item_row(page, idx)
        row.wait_for(state="visible", timeout=20000)
        count_input = row.locator("[formcontrolname='handlingUnitCount'] input, input[formcontrolname='handlingUnitCount']").first
        length_input = row.locator("[formcontrolname='length'] input, input[formcontrolname='length']").first
        width_input = row.locator("[formcontrolname='width'] input, input[formcontrolname='width']").first
        height_input = row.locator("[formcontrolname='height'] input, input[formcontrolname='height']").first
        pieces_input = row.locator("[formcontrolname='commodityTotalPieces'] input, input[formcontrolname='commodityTotalPieces']").first
        weight_input = row.locator("[formcontrolname='commodityTotalWeight'] input, input[formcontrolname='commodityTotalWeight']").first
        class_input = row.locator("input[id^='list-search-']").last

        required = [count_input, length_input, width_input, height_input, pieces_input, weight_input, class_input]
        for locator in required:
            if locator.count() == 0:
                page.screenshot(path="mycarrier_shipment_inputs_missing.png", full_page=True)
                raise RuntimeError("Could not find required MyCarrier shipment inputs. Saved mycarrier_shipment_inputs_missing.png")

        set_input_value(count_input, item["count"])
        set_input_value(length_input, item["length"])
        set_input_value(width_input, item["width"])
        set_input_value(height_input, item["height"])
        set_input_value(pieces_input, item["pieces"])
        set_input_value(weight_input, item["weight"])
        print("MyCarrier shipment numeric fields filled.", flush=True)

        set_input_value(class_input, item["freight_class"])
        wait_after_step(page, 2)
        print(f"MyCarrier class search typed: {item['freight_class']}", flush=True)
        class_option = row.locator("[role='option']").first
        if class_option.count() == 0:
            class_option = page.locator("[role='option']").first
        try:
            if class_option.count() > 0 and class_option.is_visible():
                class_option.click(force=True)
            else:
                class_input.press("Enter")
        except Exception:
            class_input.press("Enter")
        wait_after_step(page, 2)
        print("MyCarrier class selection applied.", flush=True)


def submit_quote(page):
    def results_loaded() -> bool:
        try:
            if page.locator("cup-delivery-day").count() > 0:
                return True
        except Exception:
            pass
        try:
            body_text = page.locator("body").inner_text()
        except Exception:
            return False
        if "PRINT QUOTE" in body_text or re.search(r"\$[0-9,]+\.[0-9]{2}", body_text):
            return True
        return False

    def insurance_validation_visible() -> bool:
        body_text = current_body_text(page)
        return (
            "Cargo value required to rate with insurance" in body_text
            or "Please correct errors to continue" in body_text
        )

    def clear_rate_with_insurance() -> bool:
        print("MyCarrier clearing Rate with Insurance state...", flush=True)
        cargo_input = page.locator("[data-testid='fvp-insurance-cargo-value-input']").first
        checkbox_input = page.locator("[data-testid='fvp-insurance-checkbox'] input[type='checkbox']").first
        checkbox_host = page.locator("mat-checkbox[data-testid='fvp-insurance-checkbox']").first
        checkbox_label = page.locator("mat-checkbox[data-testid='fvp-insurance-checkbox'] label").first

        try:
            if cargo_input.count() > 0:
                cargo_input.fill("")
                cargo_input.press("Tab")
                wait_after_step(page)
        except Exception:
            pass

        click_targets = [checkbox_label, checkbox_host, checkbox_input]
        for target in click_targets:
            try:
                if target.count() > 0 and target.is_visible():
                    target.click(force=True, timeout=5000)
                    wait_after_step(page, 2)
                    if not insurance_validation_visible():
                        return True
            except Exception:
                pass

        try:
            handle = checkbox_host.element_handle()
            if handle:
                page.evaluate("(el) => el.click()", handle)
                wait_after_step(page, 2)
        except Exception:
            pass

        return not insurance_validation_visible()

    insurance_checkbox = page.locator("[data-testid='fvp-insurance-checkbox'] input[type='checkbox']").first
    insurance_host = page.locator("mat-checkbox[data-testid='fvp-insurance-checkbox']").first
    try:
        if insurance_checkbox.count() > 0 and insurance_checkbox.is_checked():
            print("MyCarrier disabling Rate with Insurance...", flush=True)
            insurance_checkbox.uncheck(force=True)
            wait_after_step(page, 2)
    except Exception:
        pass

    try:
        if insurance_host.count() > 0:
            classes = insurance_host.get_attribute("class") or ""
            checked = "mat-checkbox-checked" in classes
            if not checked and insurance_checkbox.count() > 0:
                checked = insurance_checkbox.is_checked()
            if checked:
                print("MyCarrier disabling Rate with Insurance via host click...", flush=True)
                insurance_host.click(force=True)
                wait_after_step(page, 2)
    except Exception:
        pass

    print("MyCarrier clicking Select Carrier...", flush=True)
    button_host = page.locator("[data-testid='select-carrier-btn']").first
    button = page.locator("[data-testid='select-carrier-btn'] button, [data-testid='select-carrier-btn']").first
    button.scroll_into_view_if_needed()

    for attempt in range(1, 4):
        print(f"MyCarrier Select Carrier attempt {attempt}...", flush=True)
        maybe_close_banner(page)
        try:
            button.click(force=True, timeout=5000)
        except Exception:
            pass
        if results_loaded():
            return
        try:
            button_host.click(force=True, timeout=5000)
        except Exception:
            pass
        if results_loaded():
            return
        try:
            box = button_host.bounding_box() or button.bounding_box()
            if box:
                page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        except Exception:
            pass
        if results_loaded():
            return
        try:
            handle = button_host.element_handle()
            if handle:
                page.evaluate("(el) => el.click()", handle)
        except Exception:
            pass
        try:
            page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        page.wait_for_timeout(8000)
        maybe_close_banner(page)
        if insurance_validation_visible():
            print("MyCarrier submit blocked by insurance validation; retrying without insurance.", flush=True)
            clear_rate_with_insurance()
        if results_loaded():
            return

    body_text = page.locator("body").inner_text()
    if not results_loaded():
        error_text = []
        for selector in ["mat-error", ".mat-error", ".bou-error-popover", ".error", "[aria-invalid='true']"]:
            try:
                locator = page.locator(selector)
                count = min(locator.count(), 10)
                for idx in range(count):
                    text = normalize_space(locator.nth(idx).inner_text())
                    if text:
                        error_text.append(text)
            except Exception:
                pass
        if error_text:
            print("MyCarrier visible errors:", error_text, flush=True)
        print(f"MyCarrier result page not reached. URL={page.url}", flush=True)
        Path("mycarrier_carrier_selection_missing.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path="mycarrier_carrier_selection_missing.png", full_page=True)
        raise RuntimeError(
            "MyCarrier carrier results did not load. "
            "Saved mycarrier_carrier_selection_missing.png and mycarrier_carrier_selection_missing.html"
        )


def scrape_results(page):
    body_text = page.locator("body").inner_text()
    Path("mycarrier_results.html").write_text(page.content(), encoding="utf-8")
    print("MYCARRIER RESULTS PAGE TEXT:", flush=True)
    print(body_text[:6000], flush=True)

    results = []
    service_labels = ["STANDARD", "GUARANTEED AM", "GUARANTEED PM"]
    delivery_days = page.locator("cup-delivery-day")

    for col_idx in range(delivery_days.count()):
        column = delivery_days.nth(col_idx)
        header_text = normalize_space(column.locator(".header-schedule").inner_text())
        if "UNSPECIFIED TRANSIT TIME" in header_text.upper():
            continue

        transit_match = re.search(r"(\d+)\s*day", header_text, re.I)
        transit_days = int(transit_match.group(1)) if transit_match else None
        delivery_day = None
        date_match = re.search(r"\b([A-Z][a-z]{2})\s+(\d{2}/\d{2})\b", header_text)
        if date_match:
            delivery_day = f"{date_match.group(1).upper()} {date_match.group(2)}"

        groups = column.locator(".swiper-slide-content.filled")
        for group_idx in range(min(groups.count(), len(service_labels))):
            group = groups.nth(group_idx)
            service_level = service_labels[group_idx]
            cards = group.locator("cup-carrier-rate")
            for card_idx in range(cards.count()):
                card = cards.nth(card_idx)
                carrier = normalize_space(card.locator("img[alt]").first.get_attribute("alt") or "")
                if not carrier:
                    continue
                price_text = normalize_space(card.locator(".item-pkg-amt-value-hover").first.inner_text())
                time_text = normalize_space(card.locator(".item-pkg-time-wrap").first.inner_text())
                price_match = re.search(r"\$([0-9,]+\.[0-9]{2})", price_text)
                results.append(
                    {
                        "carrier": carrier,
                        "price": float(price_match.group(1).replace(",", "")) if price_match else None,
                        "transit_days": transit_days,
                        "delivery_day": delivery_day,
                        "service_level": service_level,
                        "time": time_text or None,
                    }
                )
    return results


def quote_mycarrier(data: dict, *, headless: bool = True, slow_mo: int = 0, keep_open_ms: int = 0):
    username = env_first("MYCARRIER_USERNAME")
    password = env_first("MYCARRIER_PASSWORD")
    if not username or not password:
        raise RuntimeError("Missing MYCARRIER_USERNAME or MYCARRIER_PASSWORD in .env")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo)
        page = browser.new_page(viewport={"width": 1900, "height": 950})
        try:
            do_login(page, username, password)
            goto_ltl_quote(page)
            ensure_origin_defaults_match(page, str(data.get("origin_zip") or "").strip())
            fill_origin(page, str(data.get("origin_zip") or "").strip())
            fill_destination(page, str(data.get("dest_zip") or "").strip())
            fill_shipment_items(page, data)
            page.screenshot(path="mycarrier_before_submit.png", full_page=True)
            submit_quote(page)
            page.screenshot(path="mycarrier_results.png", full_page=True)
            results = scrape_results(page)
            if keep_open_ms > 0:
                print(f"MyCarrier keeping browser open for {keep_open_ms} ms", flush=True)
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
    ss = gs_client().open(SHEET_NAME)
    data = read_input(ss)

    for key, arg_value in {
        "origin_zip": args.origin_zip,
        "dest_zip": args.dest_zip,
        "pallets": args.pallets,
        "length": args.length,
        "width": args.width,
        "height": args.height,
        "weight": args.weight,
        "pieces": args.pieces,
        "freight_class": args.freight_class,
    }.items():
        if has_value(arg_value):
            data[key] = arg_value

    print("MYCARRIER INPUT:", data, flush=True)
    results = quote_mycarrier(
        data,
        headless=not args.show_browser,
        slow_mo=args.slow_mo,
        keep_open_ms=args.keep_open_ms,
    )
    print("MYCARRIER RESULTS:", results, flush=True)


if __name__ == "__main__":
    main()
