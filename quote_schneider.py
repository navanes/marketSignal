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


def normalized_login_url() -> str:
    url = env_first("SCHNEIDER_LOGIN_URL", "SCHNEIDER_URL")
    if url and not url.startswith("http"):
        url = "https://" + url.lstrip("/")
    return url


def set_input_value(locator, value: str):
    locator.wait_for(state="visible", timeout=20000)
    locator.scroll_into_view_if_needed()
    locator.click(force=True)
    locator.fill("")
    locator.fill(str(value))
    locator.press("Tab")
    wait_after_step(locator.page)


def choose_dropdown_option(dropdown, option_text: str, page):
    dropdown.scroll_into_view_if_needed()
    dropdown.click(force=True)
    wait_after_step(page, 2)

    option = page.get_by_text(option_text, exact=True).last
    option.wait_for(state="visible", timeout=10000)
    option.click(force=True)
    wait_after_step(page, 2)


def schneider_line_items(data: dict):
    pallet_items = data.get("pallet_items") or []
    if pallet_items:
        items = []
        for item in pallet_items:
            items.append(
                {
                    "quantity": "1",
                    "length": integer_string(item.get("length"), round_up=True),
                    "width": integer_string(item.get("width"), round_up=True),
                    "height": integer_string(item.get("height"), round_up=True),
                    "weight": integer_string(item.get("weight"), round_up=True),
                    "freight_class": str(item.get("freight_class") or data.get("freight_class") or "").strip(),
                }
            )
        return items

    return [
        {
            "quantity": integer_string(data.get("pallets") or "1") or "1",
            "length": integer_string(data.get("length"), round_up=True),
            "width": integer_string(data.get("width"), round_up=True),
            "height": integer_string(data.get("height"), round_up=True),
            "weight": integer_string(data.get("weight"), round_up=True),
            "freight_class": str(data.get("freight_class") or "").strip(),
        }
    ]


def login_complete(page) -> bool:
    return "/shipper/home" in (page.url or "") or "Welcome," in current_body_text(page)


def do_login(page, username: str, password: str):
    page.goto(normalized_login_url(), wait_until="domcontentloaded", timeout=120000)
    page.get_by_label("Email Address").fill(username)
    page.get_by_label("Password").fill(password)
    page.get_by_role("button", name="Sign in").click()

    for _ in range(12):
        if login_complete(page):
            return
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass
        page.wait_for_timeout(2000)

    Path("schneider_login_failed.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path="schneider_login_failed.png", full_page=True)
    raise RuntimeError("Schneider login failed. Saved schneider_login_failed.png and schneider_login_failed.html")


def goto_quote_form(page):
    page.get_by_text("Quote", exact=True).click()
    page.wait_for_timeout(3000)
    page.get_by_text("Less than truckload", exact=True).click()
    page.wait_for_timeout(3000)

    body_text = current_body_text(page)
    if "Shipping details" not in body_text or "Commodities" not in body_text:
        Path("schneider_quote_missing.html").write_text(page.content(), encoding="utf-8")
        page.screenshot(path="schneider_quote_missing.png", full_page=True)
        raise RuntimeError("Could not open Schneider quote form. Saved schneider_quote_missing.png and schneider_quote_missing.html")


def fill_shipping_details(page, data: dict):
    postal_inputs = page.locator("[data-testid='postal-code-input'] input")
    if postal_inputs.count() < 2:
        page.screenshot(path="schneider_postal_inputs_missing.png", full_page=True)
        raise RuntimeError("Could not find Schneider postal inputs. Saved schneider_postal_inputs_missing.png")

    set_input_value(postal_inputs.nth(0), str(data.get("origin_zip") or "").strip())
    set_input_value(postal_inputs.nth(1), str(data.get("dest_zip") or "").strip())

    dropdowns = page.locator("[data-testid='dropdown']")
    if dropdowns.count() < 3:
        page.screenshot(path="schneider_dropdowns_missing.png", full_page=True)
        raise RuntimeError("Could not find Schneider dropdowns. Saved schneider_dropdowns_missing.png")

    choose_dropdown_option(dropdowns.nth(0), "Business with loading dock", page)
    choose_dropdown_option(dropdowns.nth(1), "Business with loading dock", page)


def fill_commodity_row(page, row, item: dict):
    dropdowns = row.locator("[data-testid='dropdown']")
    choose_dropdown_option(dropdowns.nth(0), item["freight_class"], page)

    numeric_inputs = row.locator("input[type='number']")
    if numeric_inputs.count() < 5:
        page.screenshot(path="schneider_commodity_inputs_missing.png", full_page=True)
        raise RuntimeError("Could not find Schneider commodity inputs. Saved schneider_commodity_inputs_missing.png")

    set_input_value(numeric_inputs.nth(0), item["quantity"])
    set_input_value(numeric_inputs.nth(1), item["length"])
    set_input_value(numeric_inputs.nth(2), item["width"])
    set_input_value(numeric_inputs.nth(3), item["height"])
    set_input_value(numeric_inputs.nth(4), item["weight"])


def fill_commodities(page, data: dict):
    items = schneider_line_items(data)
    for idx, item in enumerate(items):
        if idx > 0:
            page.get_by_role("button", name="Add another commodity").click(force=True)
            page.wait_for_timeout(1200)
        rows = page.locator(".commodity-form")
        row = rows.nth(idx)
        row.wait_for(state="visible", timeout=20000)
        fill_commodity_row(page, row, item)


def quote_results_loaded(page) -> bool:
    if page.locator(".rate-card").count() > 0:
        return True
    body_text = current_body_text(page)
    if "Hang on while we search for the best rates" in body_text:
        return False
    return "Quote rates" in body_text and "View details" in body_text


def submit_quote(page):
    page.get_by_role("button", name="Get quote").click(force=True)

    for _ in range(45):
        if quote_results_loaded(page):
            return
        try:
            page.wait_for_load_state("networkidle", timeout=4000)
        except Exception:
            pass
        page.wait_for_timeout(1000)

    body_text = current_body_text(page)
    Path("schneider_quote_submit_failed.html").write_text(page.content(), encoding="utf-8")
    page.screenshot(path="schneider_quote_submit_failed.png", full_page=True)
    raise RuntimeError(
        f"Schneider quote results did not load. Visible text sample: {body_text[:500]!r}. "
        "Saved schneider_quote_submit_failed.png and schneider_quote_submit_failed.html"
    )


def scrape_results(page):
    cards = page.locator(".rate-card")
    results = []

    for idx in range(cards.count()):
        card = cards.nth(idx)
        carrier = normalize_space(card.locator(".carrier-title").inner_text())
        if not carrier:
            continue

        fields = card.locator(".field")
        transit_days = None
        service_level = ""
        delivery_day = None
        for field_idx in range(fields.count()):
            field = fields.nth(field_idx)
            label = normalize_space(field.locator(".label, .label-small").first.inner_text())
            value = normalize_space(field.locator(".value").first.inner_text())
            label_upper = label.upper()
            if "TRANSIT TIME" in label_upper:
                match = re.search(r"(\d+)", value)
                transit_days = int(match.group(1)) if match else None
            elif "SERVICE" in label_upper:
                service_level = value
            elif "DELIVERY DATE" in label_upper:
                delivery_day = value

        price_text = normalize_space(card.locator(".price").first.inner_text())
        match = re.search(r"\$([0-9,]+\.[0-9]{2})", price_text)
        results.append(
            {
                "carrier": carrier,
                "price": float(match.group(1).replace(",", "")) if match else None,
                "transit_days": transit_days,
                "delivery_day": delivery_day,
                "service_level": service_level,
                "time": None,
            }
        )

    Path("schneider_results.html").write_text(page.content(), encoding="utf-8")
    print("SCHNEIDER RESULTS PAGE TEXT:", flush=True)
    print(page.locator("body").inner_text()[:8000], flush=True)
    return results


def quote_schneider(data: dict, *, headless: bool = True, slow_mo: int = 0, keep_open_ms: int = 0):
    username = env_first("SCHNEIDER_USERNAME")
    password = env_first("SCHNEIDER_PASSWORD", "PASSWORD")
    if not username or not password or not normalized_login_url():
        raise RuntimeError("Missing Schneider credentials in .env")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo)
        page = browser.new_page(viewport={"width": 1700, "height": 1800})
        try:
            do_login(page, username, password)
            goto_quote_form(page)
            fill_shipping_details(page, data)
            fill_commodities(page, data)
            page.screenshot(path="schneider_before_submit.png", full_page=True)
            submit_quote(page)
            page.screenshot(path="schneider_results.png", full_page=True)
            results = scrape_results(page)
            if keep_open_ms > 0:
                print(f"Schneider keeping browser open for {keep_open_ms} ms", flush=True)
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
    print("SCHNEIDER INPUT:", data, flush=True)
    results = quote_schneider(
        data,
        headless=not args.show_browser,
        slow_mo=args.slow_mo,
        keep_open_ms=args.keep_open_ms,
    )
    print("SCHNEIDER RESULTS:", results, flush=True)


if __name__ == "__main__":
    main()
