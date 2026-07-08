import argparse
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv
from playwright.sync_api import sync_playwright

from quote_total import SHEET_NAME, gs_client, read_input, write_result

load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=True)

STEP_DELAY_MS = 350
DEFAULT_QUOTE_URL = "https://orders.glovalink.com/ENTRACK2/Quote"


def env_first(*names):
    for name in names:
        value = os.getenv(name)
        if value and value.strip():
            return value.strip()
    return ""


def wait_after_step(page, multiplier=1):
    page.wait_for_timeout(STEP_DELAY_MS * multiplier)


def normalize_space(value: str) -> str:
    return " ".join(str(value or "").split())


def format_zip(value) -> str:
    raw = str(value or "").strip()
    if re.fullmatch(r"\d+\.0+", raw):
        raw = str(int(float(raw)))
    return raw.zfill(5) if raw.isdigit() and len(raw) < 5 else raw


def format_int(value, default="1") -> str:
    raw = str(value or "").strip()
    if not raw:
        return default
    try:
        return str(max(0, int(round(float(raw.replace(",", ""))))))
    except ValueError:
        return raw


def parse_input_date(value: str) -> datetime:
    raw = str(value or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%-m/%-d/%Y"):
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            pass
    return datetime.now()


def visible_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=10000)
    except Exception:
        return ""


def set_input_value(locator, value: str):
    locator.wait_for(state="visible", timeout=10000)
    locator.scroll_into_view_if_needed()
    locator.fill(str(value))
    locator.evaluate(
        "(el) => { el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }"
    )
    locator.press("Tab")
    wait_after_step(locator.page)


def select_option_by_index(locator, index: int):
    locator.wait_for(state="visible", timeout=10000)
    options = locator.locator("option")
    if options.count() <= index:
        raise RuntimeError(f"GlovaLink select {locator} does not have option index {index}")
    value = options.nth(index).get_attribute("value")
    locator.select_option(value=value)
    wait_after_step(locator.page)


def option_values(locator):
    return locator.evaluate(
        """
        (sel) => Array.from(sel.options || []).map((opt) => ({
            value: String(opt.value || '').trim(),
            text: String(opt.textContent || '').replace(/\\s+/g, ' ').trim(),
        }))
        """
    )


def select_glovalink_date(locator, requested_date: datetime, *, prefer_future_time: bool = False):
    requested = requested_date.date()
    now = datetime.now()
    options = option_values(locator)
    fallback = ""
    for item in options:
        if not item["value"]:
            continue
        fallback = fallback or item["value"]
        try:
            option_date = datetime.strptime(item["text"], "%m/%d/%Y").date()
        except ValueError:
            continue
        if option_date < requested:
            continue
        if prefer_future_time and option_date == now.date() and now.hour >= 10:
            continue
        locator.select_option(value=item["value"])
        wait_after_step(locator.page)
        return item["value"]
    if fallback:
        locator.select_option(value=fallback)
        wait_after_step(locator.page)
        return fallback
    raise RuntimeError("Could not select a GlovaLink quote date")


def select_time(locator, value: str, fallback_index: int):
    try:
        locator.select_option(value=value)
    except Exception:
        select_option_by_index(locator, fallback_index)
        return
    wait_after_step(locator.page)


def login_if_needed(page, username: str, password: str, login_url: str):
    page.goto(login_url, wait_until="domcontentloaded", timeout=60000)
    wait_after_step(page, 4)
    if page.locator("text=/Logout|Log Out/i").count() > 0:
        return
    user_input = page.locator("input[type='text'], input[name*='user' i], input[id*='user' i], input[name*='login' i]").first
    pass_input = page.locator("input[type='password']").first
    user_input.fill(username, timeout=10000)
    pass_input.fill(password, timeout=10000)
    try:
        page.locator("button:has-text('Log In'), input[value='Log In'], input[type='submit']").first.click(timeout=10000)
    except Exception:
        pass_input.press("Enter", timeout=5000)
    page.wait_for_load_state("domcontentloaded", timeout=60000)
    wait_after_step(page, 4)
    if page.locator("text=/Logout|Log Out/i").count() == 0:
        page.screenshot(path="glovalink_login_failed.png", full_page=True)
        raise RuntimeError("GlovaLink login failed. Saved glovalink_login_failed.png")


def quote_url_from_login(login_url: str) -> str:
    raw = str(login_url or "").strip()
    match = re.match(r"^(https?://[^/]+/ENTRACK2)", raw, re.I)
    if match:
        return f"{match.group(1)}/Quote"
    return DEFAULT_QUOTE_URL


def open_quote_page(page, login_url: str):
    page.goto(quote_url_from_login(login_url), wait_until="domcontentloaded", timeout=60000)
    wait_after_step(page, 4)
    if page.locator("#GetQuoteButton").count() == 0:
        page.screenshot(path="glovalink_quote_form_missing.png", full_page=True)
        raise RuntimeError("Could not open GlovaLink quote form. Saved glovalink_quote_form_missing.png")


def fill_quote_form(page, data: dict):
    select_option_by_index(page.locator("#order_type").first, 1)

    requested_date = parse_input_date(data.get("shipment_date"))
    select_glovalink_date(page.locator("#requested_pickup_date").first, requested_date, prefer_future_time=True)
    select_time(page.locator("#requested_pickup_time").first, "1100", 45)

    set_input_value(page.locator("#pickup_zip").first, format_zip(data.get("origin_zip")))

    select_glovalink_date(page.locator("#requested_delivery_date").first, requested_date, prefer_future_time=True)
    select_time(page.locator("#requested_delivery_time").first, "1700", 69)

    set_input_value(page.locator("#delivery_zip").first, format_zip(data.get("dest_zip")))
    set_input_value(page.locator("#pieces").first, format_int(data.get("pieces"), "1"))
    set_input_value(page.locator("#pallets").first, format_int(data.get("pallets"), "1"))
    set_input_value(page.locator("#weight").first, format_int(data.get("weight"), "1"))


def parse_glovalink_quote_result(text: str) -> dict:
    content = normalize_space(text)
    if re.search(r"Invalid\s+Quote\s+Result|Rate\s+Not\s+Available|Please\s+call\s+customer\s+service", content, re.I):
        raise RuntimeError("GlovaLink rate not available. Please call customer service.")
    money_values = [float(value.replace(",", "")) for value in re.findall(r"\b(\d{1,3}(?:,\d{3})*\.\d{2})\b", content)]
    total_match = re.search(r"TOTAL\s+ESTIMATED\s+CHARGE\s+(\d{1,3}(?:,\d{3})*\.\d{2})", content, re.I)
    if not total_match:
        raise RuntimeError(f"Could not parse GlovaLink quote result. Visible text sample: {content[:500]!r}")
    price = float(total_match.group(1).replace(",", ""))
    return {
        "price": price,
        "raw_money": money_values,
    }


def submit_and_parse_quote(page):
    page.locator("#GetQuoteButton").click(timeout=10000)
    try:
        page.wait_for_function(
            """() => /TOTAL\\s+ESTIMATED\\s+CHARGE|validation-summary-errors|field-validation-error/i.test(document.body.innerText || "")""",
            timeout=30000,
        )
    except Exception:
        page.wait_for_timeout(8000)

    text = visible_text(page)
    if re.search(r"validation-summary-errors|field-validation-error|cannot be in the past|required|invalid", text, re.I) and not re.search(
        r"TOTAL\s+ESTIMATED\s+CHARGE",
        text,
        re.I,
    ):
        page.screenshot(path="glovalink_quote_validation_error.png", full_page=True)
        raise RuntimeError(f"GlovaLink quote validation failed: {normalize_space(text)[:500]}")
    return parse_glovalink_quote_result(text)


def quote_glovalink(data: dict, *, headless: bool = True, slow_mo: int = 0) -> dict:
    username = env_first("GLOVALINK_USERNAME")
    password = env_first("GLOVALINK_PASSWORD", "PASSWORD")
    login_url = env_first("GLOVALINK_LOGIN_URL") or "https://orders.glovalink.com/ENTRACK2/Home/Login"
    if not username or not password or not login_url:
        raise RuntimeError("Missing GLOVALINK_USERNAME / GLOVALINK_PASSWORD / GLOVALINK_LOGIN_URL in .env")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless, slow_mo=slow_mo)
        page = browser.new_page(viewport={"width": 1600, "height": 1400})
        try:
            login_if_needed(page, username, password, login_url)
            open_quote_page(page, login_url)
            fill_quote_form(page, data)
            scraped = submit_and_parse_quote(page)
            return {
                "carrier": "GLOVALINK",
                "price": scraped["price"],
                "transit_days": "",
                "raw_money": scraped["raw_money"],
            }
        except Exception:
            try:
                page.screenshot(path="glovalink_quote_failed.png", full_page=True)
            except Exception:
                pass
            raise
        finally:
            browser.close()


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin-zip")
    parser.add_argument("--dest-zip")
    parser.add_argument("--pallets", default="1")
    parser.add_argument("--weight")
    parser.add_argument("--pieces", default="1")
    parser.add_argument("--shipment-date")
    parser.add_argument("--show-browser", action="store_true")
    parser.add_argument("--slow-mo", type=int, default=0)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    if args.origin_zip and args.dest_zip and args.weight:
        quote_data = {
            "origin_zip": args.origin_zip,
            "dest_zip": args.dest_zip,
            "pallets": args.pallets,
            "weight": args.weight,
            "pieces": args.pieces,
            "shipment_date": args.shipment_date,
        }
    else:
        ss = gs_client().open(SHEET_NAME)
        quote_data = read_input(ss)
    result = quote_glovalink(quote_data, headless=not args.show_browser, slow_mo=args.slow_mo)
    print("GLOVALINK RESULT:", result, flush=True)
    ss = gs_client().open(SHEET_NAME)
    write_result(ss, result)
