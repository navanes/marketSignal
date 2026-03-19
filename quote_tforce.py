import argparse
import os
import re
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright

from quote_total import SHEET_NAME, gs_client, read_input, write_result

load_dotenv(dotenv_path=Path(__file__).with_name(".env"), override=True)

STEP_DELAY_MS = 250
MAX_TFORCE_ATTEMPTS = 2


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


def format_tforce_date(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return datetime.now().strftime("%m/%d/%Y")
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%-m/%-d/%Y"):
        try:
            return datetime.strptime(raw, fmt).strftime("%m/%d/%Y")
        except ValueError:
            pass
    return raw


def wait_after_step(page, multiplier=1):
    page.wait_for_timeout(STEP_DELAY_MS * multiplier)
    move_mouse_to_safe_zone(page)


def move_mouse_to_safe_zone(page):
    try:
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        page.mouse.move(max(20, viewport["width"] // 2), max(20, viewport["height"] - 40))
    except:
        pass


def set_input_value(locator, value: str):
    locator.wait_for(state="visible", timeout=10000)
    locator.scroll_into_view_if_needed()
    try:
        locator.click()
    except:
        dismiss_overlay_if_present(locator.page)
        maybe_close_tforce_popup(locator.page)
        locator.click(force=True)
    locator.fill("")
    locator.evaluate(
        "(el, val) => { el.value = val; el.dispatchEvent(new Event('input', {bubbles:true})); el.dispatchEvent(new Event('change', {bubbles:true})); }",
        str(value),
    )
    locator.press("Tab")
    wait_after_step(locator.page)


def slow_type(locator, value: str, delay=40):
    locator.wait_for(state="visible", timeout=10000)
    locator.scroll_into_view_if_needed()
    try:
        locator.click()
    except:
        dismiss_overlay_if_present(locator.page)
        maybe_close_tforce_popup(locator.page)
        locator.click(force=True)
    locator.fill("")
    locator.type(str(value), delay=delay)
    locator.press("Tab")
    wait_after_step(locator.page)


def select_option_by_text(locator, desired_text: str):
    desired = normalize_space(str(desired_text).upper())
    if not desired or locator.count() == 0:
        return False

    desired_raw = str(desired_text).strip()
    for value in [desired_raw, desired_raw.zfill(3)]:
        try:
            locator.select_option(value=value)
            wait_after_step(locator.page)
            return True
        except:
            pass

    try:
        locator.select_option(label=desired_text)
        wait_after_step(locator.page)
        return True
    except:
        pass

    options = locator.locator("option")
    option_count = options.count()

    for idx in range(option_count):
        label = normalize_space(options.nth(idx).inner_text())
        if label.upper() == desired:
            try:
                locator.select_option(label=label)
                wait_after_step(locator.page)
                return True
            except:
                pass

    for idx in range(option_count):
        label = normalize_space(options.nth(idx).inner_text())
        if desired in label.upper():
            try:
                locator.select_option(label=label)
                wait_after_step(locator.page)
                return True
            except:
                pass

    try:
        forced = locator.evaluate(
            """
            (sel, desiredValue) => {
                const desired = String(desiredValue || '').trim().toUpperCase();
                const options = Array.from(sel.options || []);
                const match =
                  options.find((opt) => String(opt.value || '').trim().toUpperCase() === desired) ||
                  options.find((opt) => String(opt.textContent || '').replace(/\\s+/g, ' ').trim().toUpperCase() === desired) ||
                  options.find((opt) => String(opt.textContent || '').replace(/\\s+/g, ' ').trim().toUpperCase().includes(desired));
                if (!match) return false;
                match.selected = true;
                sel.value = match.value;
                sel.dispatchEvent(new Event('input', { bubbles: true }));
                sel.dispatchEvent(new Event('change', { bubbles: true }));
                return true;
            }
            """,
            desired_raw,
        )
        if forced:
            wait_after_step(locator.page)
            return True
    except:
        pass

    return False


def tforce_line_items(data: dict):
    items = []
    for idx, item in enumerate(data.get("pallet_items") or [], start=1):
        items.append(
            {
                "row": idx,
                "freight_class": item.get("freight_class") or data.get("freight_class"),
                "weight": item.get("weight") or item.get("weight_lb") or "",
            }
        )
    if items:
        return items
    return [
        {
            "row": 1,
            "freight_class": data.get("freight_class"),
            "weight": data.get("weight"),
        }
    ]


def first_visible(page, selectors):
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() > 0:
                return locator
        except:
            pass
    return page.locator("xpath=/*[false()]")


def install_tforce_popup_watchers(page):
    try:
        page.on("dialog", lambda dialog: dialog.dismiss())
    except:
        pass

    try:
        page.add_init_script(
            """
            (() => {
                if (window.__tforcePopupWatcherInstalled) return;
                window.__tforcePopupWatcherInstalled = true;

                const closePopup = () => {
                    const root = document;
                    const selectors = [
                        "[role='dialog'] button[aria-label='Close']",
                        "[role='dialog'] .close",
                        "[role='dialog'] .Close",
                        ".modal button[aria-label='Close']",
                        ".modal .close",
                        ".modal .Close",
                        "button[aria-label='Close']",
                        "[aria-label='Dismiss']",
                        "[aria-label='Close banner']",
                        "[aria-label='Close dialog']",
                        "[id*='cookie'] button",
                        "[class*='cookie'] button",
                        "[class*='Cookie'] button",
                        "[id*='consent'] button",
                        "[class*='consent'] button",
                    ];

                    for (const selector of selectors) {
                        for (const el of root.querySelectorAll(selector)) {
                            if (el instanceof HTMLElement && el.offsetParent !== null) {
                                el.click();
                                return;
                            }
                        }
                    }

                    const clickables = Array.from(
                        root.querySelectorAll("button, [role='button'], a, span, div, label")
                    );
                    const labels = ["close", "ok", "no thanks", "not now", "dismiss", "x", "×", "✕", "accept", "accept all", "i agree", "got it"];
                    for (const el of clickables) {
                        const text = (el.textContent || "").replace(/\\s+/g, " ").trim().toLowerCase();
                        const aria = (el.getAttribute("aria-label") || "").trim().toLowerCase();
                        if (!text && !aria) continue;
                        if (!labels.includes(text) && !labels.includes(aria)) continue;
                        if (!(el instanceof HTMLElement) || el.offsetParent === null) continue;
                        el.click();
                        return;
                    }

                    const cookieBlocks = Array.from(root.querySelectorAll("div, section, aside"));
                    for (const block of cookieBlocks) {
                        const text = (block.textContent || "").replace(/\\s+/g, " ").trim().toLowerCase();
                        if (!text.includes("cookie") && !text.includes("privacy notice")) continue;
                        const closers = block.querySelectorAll("button, [role='button'], a, span");
                        for (const el of closers) {
                            const label = ((el.textContent || "") + " " + (el.getAttribute("aria-label") || "")).replace(/\\s+/g, " ").trim().toLowerCase();
                            if (!label) continue;
                            if (!["close", "dismiss", "x", "×", "✕", "accept", "accept all", "got it"].includes(label)) continue;
                            if (el instanceof HTMLElement && el.offsetParent !== null) {
                                el.click();
                                return;
                            }
                        }
                    }
                };

                closePopup();
                new MutationObserver(() => closePopup()).observe(document.documentElement, {
                    childList: true,
                    subtree: true,
                    attributes: true,
                });
                window.setInterval(closePopup, 300);
            })();
            """
        )
    except:
        pass


def dismiss_overlay_if_present(page):
    closers = [
        page.locator("button:has-text('Ok')").first,
        page.locator("button:has-text('OK')").first,
        page.locator("button:has-text('Accept')").first,
        page.locator("button:has-text('Accept All')").first,
        page.locator("button:has-text('Got It')").first,
        page.locator("xpath=//button[contains(., 'Ok') or contains(., 'OK')]").first,
        page.locator("button[aria-label='Close']").first,
        page.locator("[aria-label='Dismiss']").first,
        page.locator("button:has-text('Close')").first,
        page.locator("button:has-text('No Thanks')").first,
        page.locator("button:has-text('Not Now')").first,
        page.locator("xpath=//*[@role='dialog']//*[normalize-space(text())='×' or normalize-space(text())='✕']").first,
        page.locator("xpath=//*[@role='dialog']//*[contains(@class,'close') or contains(@class,'Close')]").first,
        page.locator("xpath=//button[contains(., 'Close') or contains(., 'close')]").first,
        page.locator("xpath=//*[@role='dialog']//*[contains(@class,'close') or @aria-label='Close']").first,
        page.locator("xpath=//button[contains(., 'Accept') or contains(., 'I Agree')]").first,
        page.locator("xpath=//*[contains(., 'This website uses cookies')]/following::*[normalize-space(text())='×' or normalize-space(text())='✕'][1]").first,
        page.locator("xpath=//*[contains(., 'This website uses cookies')]//*[contains(@class,'close') or @aria-label='Close']").first,
        page.locator("xpath=//*[contains(., 'Privacy Notice') or contains(., 'cookie') or contains(., 'Cookie Settings')]//*[self::button or self::a or self::span][normalize-space(text())='×' or normalize-space(text())='✕' or contains(., 'Close') or contains(., 'Accept') or contains(., 'Got It')]").first,
    ]
    for closer in closers:
        try:
            if closer.count() > 0 and closer.is_visible():
                closer.click(force=True)
                wait_after_step(page, 2)
                return
        except:
            pass

    try:
        cookie_banner = page.locator("xpath=//*[contains(., 'Privacy Notice') or contains(., 'Cookie Settings') or contains(., 'This website uses cookies')]").first
        if cookie_banner.count() > 0 and cookie_banner.is_visible():
            for closer in [
                cookie_banner.locator("button").first,
                cookie_banner.locator("[role='button']").first,
                cookie_banner.locator("xpath=.//*[normalize-space(text())='×' or normalize-space(text())='✕']").first,
            ]:
                try:
                    if closer.count() > 0 and closer.is_visible():
                        closer.click(force=True)
                        wait_after_step(page, 2)
                        return
                except:
                    pass
    except:
        pass


def dismiss_tracking_flyout(page):
    flyout = page.locator(
        "xpath=//*[contains(@class,'track') or @aria-label='Track' or .//*[normalize-space(text())='Track']][.//input[contains(@placeholder,'PRO Number') or contains(@placeholder,'PRO number')]]"
    ).first
    try:
        if flyout.count() > 0 and flyout.is_visible():
            try:
                page.keyboard.press("Escape")
                page.wait_for_timeout(150)
            except:
                pass
            try:
                page.locator("body").click(position={"x": 5, "y": 5})
                page.wait_for_timeout(150)
            except:
                pass
    except:
        pass


def clear_tforce_interference(page):
    move_mouse_to_safe_zone(page)
    dismiss_tracking_flyout(page)
    dismiss_overlay_if_present(page)
    wait_for_tforce_popup_to_clear(page, timeout_ms=1500)
    dismiss_tracking_flyout(page)
    move_mouse_to_safe_zone(page)


def maybe_close_tforce_popup(page):
    popup = page.locator(
        "xpath=//*[self::div or self::section][(.//*[@role='dialog'] or contains(@class,'modal') or contains(@class,'dialog')) and .//*[contains(., 'The Speed You Need') or contains(., 'Guaranteed')]]"
    ).first
    if popup.count() == 0:
        popup = page.locator("xpath=//*[contains(., 'The Speed You Need') and contains(., 'Do not show again')]").first
    if popup.count() > 0:
        try:
            if popup.is_visible():
                try:
                    suppress = popup.locator(
                        "xpath=(.//label[contains(., 'Do not show again')]/preceding::input[@type='checkbox'][1] | .//input[@type='checkbox'])[1]"
                    ).first
                    if suppress.count() > 0 and not suppress.is_checked():
                        suppress.check(force=True)
                        page.wait_for_timeout(120)
                except:
                    pass

                explicit_closers = [
                    popup.locator("xpath=.//button[normalize-space()='Ok' or normalize-space()='OK']").first,
                    popup.locator("xpath=.//*[@aria-label='Close']").first,
                    popup.locator("xpath=.//*[normalize-space(text())='×' or normalize-space(text())='✕']").first,
                    popup.locator("xpath=.//*[contains(@class,'close') or contains(@class,'Close')]").first,
                ]
                for closer in explicit_closers:
                    try:
                        if closer.count() > 0 and closer.is_visible():
                            closer.click(force=True)
                            page.wait_for_timeout(120)
                            return True
                    except:
                        pass
        except:
            pass

    try:
        suppress = page.locator("xpath=//label[contains(., 'Do not show again')]/preceding::input[@type='checkbox'][1]").first
        if suppress.count() > 0 and suppress.is_visible() and not suppress.is_checked():
            suppress.check(force=True)
            page.wait_for_timeout(120)
    except:
        pass

    closers = [
        page.locator("xpath=//button[normalize-space()='Ok' or normalize-space()='OK']").first,
        page.locator("xpath=//*[@aria-label='Close']").first,
        page.locator("xpath=//*[@role='dialog']//button[contains(., 'Close') or contains(., 'close')]").first,
        page.locator("xpath=//*[@role='dialog']//*[contains(@class,'close')]").first,
        page.locator("xpath=//div[contains(@class,'modal') or @role='dialog']//button[contains(., 'No Thanks') or contains(., 'Not Now') or contains(., 'Close')]").first,
    ]
    for closer in closers:
        try:
            if closer.count() > 0 and closer.is_visible():
                closer.click(force=True)
                page.wait_for_timeout(120)
                return True
        except:
            pass
    return False


def on_tforce_results_page(page) -> bool:
    try:
        url = (page.url or "").lower()
    except:
        url = ""
    if "ratingresult" in url:
        return True
    try:
        return (
            page.locator("text=/Rating Result/i").first.count() > 0
            or page.locator("text=/TForce Freight LTL/i").first.count() > 0
            or page.locator("text=/Days In Transit/i").first.count() > 0
        )
    except:
        return False


def wait_for_tforce_popup_to_clear(page, timeout_ms=5000):
    start = time.time()
    while (time.time() - start) * 1000 < timeout_ms:
        closed = False
        try:
            closed = maybe_close_tforce_popup(page)
        except:
            closed = False
        dismiss_overlay_if_present(page)
        try:
            popup_visible = page.locator(
                "xpath=//*[contains(., 'The Speed You Need') and contains(., 'Guaranteed') and (.//*[@type='checkbox'] or .//button[normalize-space()='Ok' or normalize-space()='OK'])]"
            ).first.is_visible()
        except:
            popup_visible = False
        if not popup_visible:
            return True
        if not closed:
            page.wait_for_timeout(150)
    return False


def already_logged_in(page) -> bool:
    url = ""
    try:
        url = page.url or ""
    except:
        pass

    if "/myltl/" in url.lower() or "rateestimate" in url.lower():
        return True

    indicators = [
        page.get_by_text(re.compile(r"log out", re.I)).first,
        page.locator("text=/Rate Estimate/i").first,
        page.locator("text=/Quote and Ship/i").first,
        page.locator("text=/Shipping Information/i").first,
        page.locator("text=/Customer Service/i").first,
    ]
    for locator in indicators:
        try:
            if locator.count() > 0:
                return True
        except:
            pass

    return False


def page_body_text(page) -> str:
    try:
        return page.locator("body").inner_text(timeout=2000)
    except:
        return ""


def page_is_not_found(page) -> bool:
    text = page_body_text(page)
    return re.search(r"requested url was not found|404|page not found", text, re.I) is not None


def page_is_server_error(page) -> bool:
    text = page_body_text(page)
    return re.search(r"http error 500|this page isn[’']t working|currently unable to handle this request", text, re.I) is not None


def on_public_quote_landing_page(page) -> bool:
    text = page_body_text(page)
    return re.search(r"Get Started with TForce Freight LTL|Quote and Ship|Transit Times", text, re.I) is not None


def site_root(url: str) -> str:
    parts = urlsplit(url)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def maybe_open_myltl_login(page):
    candidates = [
        page.get_by_role("link", name=re.compile(r"myltl", re.I)).first,
        page.get_by_text(re.compile(r"^\s*MyLTL\s*$", re.I)).first,
        page.get_by_role("link", name=re.compile(r"log in|sign in", re.I)).first,
    ]
    for candidate in candidates:
        try:
            if candidate.count() > 0:
                candidate.click(force=True)
                page.wait_for_load_state("domcontentloaded")
                wait_after_step(page, 2)
                return
        except:
            pass


def open_login_surface(page, login_url: str):
    targets = [login_url]
    root_url = site_root(login_url)
    if root_url and root_url not in targets:
        targets.append(root_url)

    for target in targets:
        try:
            page.goto(target, wait_until="domcontentloaded")
            wait_after_step(page, 2)
        except Exception as exc:
            if "ERR_ABORTED" in str(exc).upper() or "net::ERR_ABORTED" in str(exc):
                continue
            raise

        if page_is_not_found(page):
            continue
        if already_logged_in(page):
            return
        maybe_open_myltl_login(page)
        if already_logged_in(page):
            return
        username_input, password_input, _ = find_login_controls(page)
        if username_input is not None and password_input is not None:
            return

    maybe_open_myltl_login(page)


def find_login_controls(page):
    password_input = first_visible(
        page,
        [
            "input[type='password']",
            "xpath=//input[contains(translate(@name,'PASSWORD','password'),'password')]",
            "xpath=//input[contains(translate(@id,'PASSWORD','password'),'password')]",
        ],
    )
    if password_input.count() == 0:
        return None, None, None

    username_input = first_visible(
        page,
        [
            "input[type='email']",
            "input[autocomplete='username']",
            "xpath=//input[contains(translate(@name,'USERNAMEEMAILLOGIN','usernameemaillogin'),'user')]",
            "xpath=//input[contains(translate(@name,'USERNAMEEMAILLOGIN','usernameemaillogin'),'email')]",
            "xpath=//input[contains(translate(@id,'USERNAMEEMAILLOGIN','usernameemaillogin'),'user')]",
            "xpath=//input[contains(translate(@id,'USERNAMEEMAILLOGIN','usernameemaillogin'),'email')]",
            "input[type='text']",
        ],
    )

    login_button = first_visible(
        page,
        [
            "xpath=//button[contains(., 'Log In') or contains(., 'Login') or contains(., 'Sign In')]",
            "xpath=//input[contains(translate(@value,'LOGIN SIGNIN','login signin'),'log in') or contains(translate(@value,'LOGIN SIGNIN','login signin'),'sign in')]",
            "xpath=//a[contains(., 'Log In') or contains(., 'Login') or contains(., 'Sign In')]",
        ],
    )

    if username_input.count() == 0 or login_button.count() == 0:
        return None, None, None

    return username_input, password_input, login_button


def do_login(page, username, password):
    if already_logged_in(page):
        return

    username_input, password_input, login_button = find_login_controls(page)
    if username_input is None:
        if on_public_quote_landing_page(page):
            return
        page.screenshot(path="tforce_login_controls_missing.png", full_page=True)
        raise RuntimeError("Could not find TForce login controls. Saved tforce_login_controls_missing.png")

    slow_type(username_input, username, delay=55)
    slow_type(password_input, password, delay=55)
    login_button.click(force=True)
    page.wait_for_load_state("domcontentloaded")
    wait_after_step(page, 4)

    username_input_after, password_input_after, _ = find_login_controls(page)
    login_form_still_visible = (
        username_input_after is not None and password_input_after is not None
    )

    if not already_logged_in(page) and login_form_still_visible:
        page.screenshot(path="tforce_login_failed.png", full_page=True)
        try:
            Path("tforce_login_failed_url.txt").write_text(f"{page.url}\n", encoding="utf-8")
        except:
            pass
        try:
            Path("tforce_login_failed.html").write_text(page.content(), encoding="utf-8")
        except:
            pass
        raise RuntimeError("TForce login failed. Saved tforce_login_failed.png")


def goto_rate_estimate(page):
    # Give the app shell a chance to render after auth.
    wait_after_step(page, 3)

    def on_rate_estimate_page():
        url = ""
        try:
            url = page.url or ""
        except:
            pass
        if "rateestimate" in url.lower():
            return True
        try:
            required_form_bits = [
                page.locator("text=/Shipment Information/i").first,
                page.locator("text=/Commodity Information/i").first,
                page.locator("xpath=//button[contains(., 'Get Rate')]").first,
                page.locator("xpath=//label[contains(., 'Shipping Date')]").first,
            ]
            return any(bit.count() > 0 for bit in required_form_bits)
        except:
            return False

    def on_public_ltl_landing_page():
        try:
            return (
                page.locator("text=/Get Started with TForce Freight LTL/i").first.count() > 0
                or page.locator("text=/Quote and Ship/i").first.count() > 0
                or page.locator("text=/Transit Times/i").first.count() > 0
            )
        except:
            return False

    def try_open_quote_and_ship():
        quote_candidates = [
            page.locator("xpath=//a[contains(@href, 'quote') and contains(., 'Quote and Ship')]").first,
            page.locator("xpath=//a[contains(@href, 'rate') and contains(., 'Quote and Ship')]").first,
            page.get_by_role("link", name=re.compile(r"^\s*Quote and Ship\s*$", re.I)).first,
            page.get_by_text(re.compile(r"^\s*Quote and Ship\s*$", re.I)).first,
        ]
        for candidate in quote_candidates:
            try:
                if candidate.count() == 0:
                    continue
                href = ""
                try:
                    href = (candidate.get_attribute("href") or "").strip()
                except:
                    href = ""
                if href and not href.lower().startswith("javascript:"):
                    page.goto(urljoin(page.url or "", href), wait_until="domcontentloaded")
                else:
                    candidate.scroll_into_view_if_needed()
                    dismiss_overlay_if_present(page)
                    wait_for_tforce_popup_to_clear(page, timeout_ms=1500)
                    candidate.click(force=True)
                page.wait_for_load_state("domcontentloaded")
                wait_after_step(page, 2)
                wait_for_tforce_popup_to_clear(page, timeout_ms=2500)
                dismiss_overlay_if_present(page)
                if on_rate_estimate_page():
                    return True
            except:
                pass

        # Fallback for the public landing page: navigate directly to the app quote entrypoints.
        for target in [
            "https://www.tforcefreight.com/ltl/apps/RateEstimate",
            "https://www.tforcefreight.com/ltl/apps/rateestimate",
            "https://www.tforcefreight.com/ltl/myltl/rateestimate",
        ]:
            try:
                page.goto(target, wait_until="domcontentloaded")
                wait_after_step(page, 2)
                wait_for_tforce_popup_to_clear(page, timeout_ms=2500)
                dismiss_overlay_if_present(page)
                if on_rate_estimate_page():
                    return True
            except:
                pass
        return False

    if on_rate_estimate_page():
        return

    if on_public_ltl_landing_page() and try_open_quote_and_ship():
        return

    if page_is_server_error(page):
        root = site_root(page.url or "")
        retry_targets = [
            root,
            f"{root}/ltl/myltl/home",
        ]
        for target in retry_targets:
            try:
                page.goto(target, wait_until="domcontentloaded")
                wait_after_step(page, 3)
                maybe_close_tforce_popup(page)
                if on_rate_estimate_page():
                    return
            except:
                pass

    shipping_nav = first_visible(
        page,
        [
            "xpath=//a[contains(., 'Shipping')]",
            "xpath=//button[contains(., 'Shipping')]",
        ],
    )
    if shipping_nav.count() > 0:
        try:
            shipping_nav.click(force=True)
            wait_after_step(page, 2)
        except:
            pass

    candidates = [
        page.locator("xpath=//h2[contains(., 'Rate Estimate') or self::h3[contains(., 'Rate Estimate')]]/following::a[contains(., 'Quote and Ship')][1]").first,
        page.locator("xpath=//a[contains(., 'Quote and Ship') and preceding::*[contains(., 'Rate Estimate')][1]]").first,
        page.get_by_role("link", name=re.compile(r"rate estimate", re.I)).first,
        page.get_by_role("link", name=re.compile(r"quote and ship", re.I)).first,
        page.get_by_text(re.compile(r"Quote and Ship", re.I)).first,
        page.locator("xpath=//a[contains(@href, 'rate') and (contains(., 'Quote') or contains(., 'Rate'))]").first,
    ]

    for candidate in candidates:
        try:
            if candidate.count() == 0:
                continue
            candidate.click(force=True)
            page.wait_for_load_state("domcontentloaded")
            wait_after_step(page, 2)
            wait_for_tforce_popup_to_clear(page, timeout_ms=2500)
            if on_rate_estimate_page():
                return
        except:
            pass

    root = site_root(page.url or "")
    direct_urls = [
        f"{root}/ltl/myltl/rateestimate",
        f"{root}/ltl/myltl/rateEstimate",
        f"{root}/ltl/myltl/shipping/rateestimate",
        f"{root}/ltl/apps/rateestimate",
        f"{root}/ltl/apps/RateEstimate",
    ]
    for target in direct_urls:
        try:
            page.goto(target, wait_until="domcontentloaded")
            wait_after_step(page, 3)
            maybe_close_tforce_popup(page)
            if on_rate_estimate_page():
                return
            if page_is_server_error(page):
                continue
        except:
            pass

    page.screenshot(path="tforce_rate_estimate_missing.png", full_page=True)
    try:
        Path("tforce_rate_estimate_missing_url.txt").write_text(f"{page.url}\n", encoding="utf-8")
    except:
        pass
    raise RuntimeError("Could not find TForce Rate Estimate link. Saved tforce_rate_estimate_missing.png")


def locate_zip_inputs(page):
    origin_zip = page.locator(
        "xpath=(//label[contains(., 'ZIP Code')]/following::input[not(@type='hidden') and not(contains(@style,'display:none'))])[1]"
    ).first
    dest_zip = page.locator(
        "xpath=(//label[contains(., 'ZIP Code')]/following::input[not(@type='hidden') and not(contains(@style,'display:none'))])[2]"
    ).first

    try:
        if origin_zip.count() > 0 and not origin_zip.is_visible():
            origin_zip = page.locator(
                "input:visible[id*='Zip'], input:visible[id*='zip'], input:visible[name*='Zip'], input:visible[name*='zip']"
            ).nth(0)
    except:
        pass

    try:
        if dest_zip.count() > 0 and not dest_zip.is_visible():
            dest_zip = page.locator(
                "input:visible[id*='Zip'], input:visible[id*='zip'], input:visible[name*='Zip'], input:visible[name*='zip']"
            ).nth(1)
    except:
        pass

    if origin_zip.count() == 0:
        origin_zip = page.locator(
            "input:visible[id*='Zip'], input:visible[id*='zip'], input:visible[name*='Zip'], input:visible[name*='zip']"
        ).nth(0)
    if dest_zip.count() == 0:
        dest_zip = page.locator(
            "input:visible[id*='Zip'], input:visible[id*='zip'], input:visible[name*='Zip'], input:visible[name*='zip']"
        ).nth(1)

    return origin_zip, dest_zip


def expand_section(page, heading_text: str):
    header = page.locator(
        f"xpath=//*[self::div or self::button or self::span or self::h1 or self::h2 or self::h3][normalize-space(.)='{heading_text}']"
    ).first
    if header.count() == 0:
        header = page.locator(
            f"xpath=//*[self::div or self::button or self::span or self::h1 or self::h2 or self::h3][contains(normalize-space(.), '{heading_text}')]"
        ).first
    if header.count() == 0:
        return

    # If visible content for the section is already present, do nothing.
    section_checks = {
        "Shipment Information": [
            page.locator("text=/Ship From/i").first,
            page.locator("text=/Shipping Date/i").first,
            page.locator("text=/ZIP Code/i").first,
        ],
        "Commodity Information": [
            page.locator("text=/Weight \\(lbs\\)/i").first,
            page.locator("text=/NMFC-Sub No/i").first,
            page.locator("text=/Class \\*/i").first,
        ],
    }
    for check in section_checks.get(heading_text, []):
        try:
            if check.count() > 0:
                return
        except:
            pass

    try:
        bar = header.locator("xpath=ancestor::*[self::div or self::section][1]")
        plus_button = bar.locator(
            "xpath=.//*[self::button or self::span or self::div][normalize-space(.)='+' or @aria-label='Expand' or @aria-expanded='false']"
        ).first
        if plus_button.count() > 0 and plus_button.is_visible():
            plus_button.click(force=True)
            wait_after_step(page, 2)
    except:
        pass

    toggle_candidates = [
        page.locator(
            f"xpath=//*[contains(normalize-space(.), '{heading_text}')]/following::*[normalize-space(text())='+'][1]"
        ).first,
        page.locator(
            f"xpath=//*[contains(normalize-space(.), '{heading_text}')]/ancestor::*[self::div or self::section][1]//*[normalize-space(text())='+'][1]"
        ).first,
        page.locator(
            f"xpath=//*[contains(normalize-space(.), '{heading_text}')]/ancestor::*[self::div or self::section][1]//*[@aria-expanded='false'][1]"
        ).first,
    ]

    for toggle in toggle_candidates:
        try:
            if toggle.count() > 0 and toggle.is_visible():
                toggle.click(force=True)
                wait_after_step(page, 2)
                return
        except:
            pass

    try:
        container = header.locator("xpath=ancestor::*[self::div or self::section][1]")
        toggle = container.locator("xpath=.//button[.//*[contains(., '+')]] | .//*[contains(@class, 'plus')][1]").first
        if toggle.count() > 0:
            toggle.click(force=True)
            wait_after_step(page, 2)
            return
    except:
        pass
    try:
        header.click(force=True)
        wait_after_step(page, 2)
    except:
        pass

    try:
        bar = header.locator("xpath=ancestor::*[self::div or self::section][1]")
        bar.click(force=True)
        wait_after_step(page, 2)
    except:
        pass


def locate_form_fields(page):
    origin_zip, dest_zip = locate_zip_inputs(page)
    shipment_date = page.locator(
        "xpath=(//label[contains(., 'Shipping Date')]/following::input[not(@type='hidden')])[1]"
    ).first
    if shipment_date.count() == 0:
        shipment_date = page.locator(
            "xpath=(//input[(contains(@name,'date') or contains(@id,'date') or contains(@name,'Date') or contains(@id,'Date')) and not(@type='hidden')])[1]"
        ).first
    relation_select = page.locator("xpath=//label[contains(., 'Relation to Shipper')]/following::select[1]").first
    freight_service = page.locator("xpath=//label[contains(., 'Freight Service')]/following::select[1]").first
    class_select = page.locator("xpath=//label[contains(., 'Class')]/following::select[1]").first
    weight_input = page.locator("xpath=//label[contains(., 'Weight')]/following::input[1]").first
    nmfc_mode_button = page.get_by_role("button", name=re.compile(r"NMFC Class", re.I)).first
    add_commodity_button = page.get_by_role("button", name=re.compile(r"Add Another Commodity", re.I)).first
    if add_commodity_button.count() == 0:
        add_commodity_button = page.get_by_text(re.compile(r"Add Another Commodity", re.I)).first
    prepaid_radio = page.locator("xpath=//label[contains(., 'Prepaid')]/preceding::input[@type='radio'][1]").first
    if prepaid_radio.count() == 0:
        prepaid_radio = page.locator("input[type='radio'][value='Prepaid']").first

    return {
        "origin_zip": origin_zip,
        "dest_zip": dest_zip,
        "shipment_date": shipment_date,
        "relation_select": relation_select,
        "freight_service": freight_service,
        "class_select": class_select,
        "weight_input": weight_input,
        "nmfc_mode_button": nmfc_mode_button,
        "add_commodity_button": add_commodity_button,
        "prepaid_radio": prepaid_radio,
    }


def tforce_visible_commodity_rows(page):
    rows = page.locator(
        "xpath=//*[self::div or self::tr][.//*[contains(normalize-space(.), 'Class *')] and .//*[contains(normalize-space(.), 'Weight (lbs) *')] and .//*[contains(normalize-space(.), 'NMFC-Sub No.')]]"
    )
    visible_rows = []
    count = rows.count()
    for idx in range(count):
        row = rows.nth(idx)
        try:
            if row.is_visible():
                visible_rows.append(row)
        except:
            pass
    return visible_rows


def tforce_commodity_section(page):
    return page.locator(
        "xpath=//*[self::div or self::section][.//*[contains(normalize-space(.), 'Commodity Information')] and .//*[contains(normalize-space(.), 'NMFC Class')]]"
    ).first


def tforce_visible_class_selects(page):
    section = tforce_commodity_section(page)
    selects = section.locator("xpath=.//select[not(@disabled)]")
    visible = []
    count = selects.count()
    for idx in range(count):
        locator = selects.nth(idx)
        try:
            if locator.is_visible():
                option_labels = [normalize_space(opt).upper() for opt in locator.locator("option").all_inner_texts()[:6]]
                if any(label in {"UNITED STATES", "CANADA", "MEXICO"} for label in option_labels):
                    continue
                if not any(label in {"--", "SELECT"} or label.startswith("--") for label in option_labels):
                    continue
                visible.append(locator)
        except:
            pass
    return visible


def tforce_visible_weight_inputs(page):
    section = tforce_commodity_section(page)
    inputs = section.locator(
        "xpath=.//input[(contains(@class,'txtPerCommodityWeight') or contains(@id,'CommodityWeight') or contains(@id,'Weight')) and not(@type='hidden') and not(@disabled)]"
    )
    visible = []
    count = inputs.count()
    for idx in range(count):
        locator = inputs.nth(idx)
        try:
            if locator.is_visible():
                visible.append(locator)
        except:
            pass
    return visible


def tforce_row_class_select(row):
    candidates = [
        row.locator("xpath=.//select[contains(@id,'Class') and not(@disabled)]").first,
        row.locator("xpath=.//select[contains(@name,'Class') and not(@disabled)]").first,
        row.locator("xpath=.//select[not(@disabled) and .//option[normalize-space(.)='--']]").first,
        row.locator("xpath=.//select[not(@disabled)]").first,
    ]
    for candidate in candidates:
        try:
            if candidate.count() > 0 and candidate.is_visible():
                return candidate
        except:
            pass
    return row.locator("xpath=.//*[false()]")


def tforce_row_weight_input(row):
    candidates = [
        row.locator("xpath=.//input[contains(@id,'CommodityWeight') and not(@type='hidden') and not(@disabled)]").first,
        row.locator("xpath=.//input[contains(@name,'Weight') and not(@type='hidden') and not(@disabled)]").first,
        row.locator("xpath=.//input[contains(@class,'Weight') and not(@type='hidden') and not(@disabled)]").first,
        row.locator("xpath=.//input[not(@type='hidden') and not(@disabled)]").first,
    ]
    for candidate in candidates:
        try:
            if candidate.count() > 0 and candidate.is_visible():
                return candidate
        except:
            pass
    return row.locator("xpath=.//*[false()]")


def wait_for_tforce_commodity_rows(page, expected_count: int, timeout_ms=8000):
    start = time.time()
    while (time.time() - start) * 1000 < timeout_ms:
        rows = tforce_visible_commodity_rows(page)
        if len(rows) >= expected_count:
            return rows
        maybe_close_tforce_popup(page)
        page.wait_for_timeout(250)
    return tforce_visible_commodity_rows(page)


def tforce_class_select_for_index(page, index: int):
    selectors = [
        f"select:visible[id='ddlClass{index}']",
        f"select:visible[id='cboClass{index}']",
        f"select:visible[id*='Class'][id$='{index}']",
        f"select:visible[name*='Class'][name$='{index}']",
    ]
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() > 0 and locator.is_visible():
                option_labels = [normalize_space(opt).upper() for opt in locator.locator('option').all_inner_texts()[:6]]
                if any(label in {"UNITED STATES", "CANADA", "MEXICO"} for label in option_labels):
                    continue
                return locator
        except:
            pass
    return page.locator("xpath=//*[false()]")


def tforce_weight_input_for_index(page, index: int):
    selectors = [
        f"input:visible[id='txtPerCommodityWeight{index}']:not([disabled])",
        f"input:visible[id*='CommodityWeight'][id$='{index}']:not([disabled])",
        f"input:visible[name*='Weight'][name$='{index}']:not([disabled])",
    ]
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            if locator.count() > 0 and locator.is_visible():
                return locator
        except:
            pass
    return page.locator("xpath=//*[false()]")


def ensure_city_loaded(locator, expected_text: str):
    if locator.count() == 0 or not expected_text:
        return
    start = time.time()
    while time.time() - start < 8:
        try:
            block = normalize_space(locator.locator("xpath=following::*[1]").inner_text())
            if expected_text.upper() in block.upper():
                return
        except:
            pass
        locator.page.wait_for_timeout(250)


def fill_rate_estimate_form(page, data):
    wait_for_tforce_popup_to_clear(page, timeout_ms=5000)
    clear_tforce_interference(page)
    expand_section(page, "Shipment Information")
    expand_section(page, "Commodity Information")
    wait_for_tforce_popup_to_clear(page, timeout_ms=5000)
    clear_tforce_interference(page)
    fields = locate_form_fields(page)

    required_missing = []
    if has_value(data.get("origin_zip")) and fields["origin_zip"].count() == 0:
        required_missing.append("origin_zip")
    if has_value(data.get("dest_zip")) and fields["dest_zip"].count() == 0:
        required_missing.append("dest_zip")

    if required_missing:
        page.screenshot(path="tforce_form_missing.png", full_page=True)
        raise RuntimeError(
            f"Could not find required TForce fields: {', '.join(required_missing)}. "
            "Saved tforce_form_missing.png"
        )

    if fields["nmfc_mode_button"].count() > 0:
        try:
            fields["nmfc_mode_button"].click(force=True)
            wait_after_step(page)
        except:
            pass

    if fields["relation_select"].count() > 0:
        select_option_by_text(fields["relation_select"], "Shipper")

    if fields["freight_service"].count() > 0:
        select_option_by_text(fields["freight_service"], "TForce Freight LTL")

    if fields["prepaid_radio"].count() > 0:
        try:
            if not fields["prepaid_radio"].is_checked():
                fields["prepaid_radio"].check(force=True)
                wait_after_step(page)
        except:
            pass

    if fields["shipment_date"].count() > 0:
        try:
            if fields["shipment_date"].is_visible():
                set_input_value(fields["shipment_date"], format_tforce_date(data.get("shipment_date")))
        except:
            pass

    set_input_value(fields["origin_zip"], data.get("origin_zip", ""))
    ensure_city_loaded(fields["origin_zip"], data.get("pickup_city", ""))

    set_input_value(fields["dest_zip"], data.get("dest_zip", ""))
    ensure_city_loaded(fields["dest_zip"], data.get("delivery_city", ""))

    line_items = tforce_line_items(data)
    for _ in range(max(0, len(line_items) - 1)):
        try:
            if fields["add_commodity_button"].count() > 0:
                clear_tforce_interference(page)
                fields["add_commodity_button"].scroll_into_view_if_needed()
                try:
                    fields["add_commodity_button"].click(force=True)
                except:
                    handle = fields["add_commodity_button"].element_handle()
                    if handle is not None:
                        page.evaluate("(el) => el.click()", handle)
                wait_after_step(page, 2)
                wait_for_tforce_popup_to_clear(page, timeout_ms=3000)
                clear_tforce_interference(page)
        except:
            pass

    commodity_rows = wait_for_tforce_commodity_rows(page, len(line_items))
    for index, item in enumerate(line_items, start=1):
        row_class_select = tforce_class_select_for_index(page, index)
        if row_class_select.count() == 0 and len(commodity_rows) >= index:
            row_class_select = tforce_row_class_select(commodity_rows[index - 1])
        if row_class_select.count() == 0:
            class_selects = tforce_visible_class_selects(page)
            if len(class_selects) >= index:
                row_class_select = class_selects[index - 1]

        if row_class_select.count() == 0:
            page.screenshot(path="tforce_class_select_failed.png", full_page=True)
            raise RuntimeError(f"Could not locate TForce commodity row {index}. Saved tforce_class_select_failed.png")

        row_weight_input = tforce_weight_input_for_index(page, index)
        if row_weight_input.count() == 0 and len(commodity_rows) >= index:
            row_weight_input = tforce_row_weight_input(commodity_rows[index - 1])
        if row_weight_input.count() == 0:
            weight_inputs = tforce_visible_weight_inputs(page)
            if len(weight_inputs) >= index:
                row_weight_input = weight_inputs[index - 1]
        if row_weight_input.count() == 0:
            row_weight_input = page.locator("xpath=//*[false()]")

        if row_class_select.count() > 0 and has_value(item.get("freight_class")):
            if not select_option_by_text(row_class_select, str(item.get("freight_class"))):
                options = row_class_select.locator("option").all_inner_texts()
                page.screenshot(path="tforce_class_select_failed.png", full_page=True)
                raise RuntimeError(
                    f"Could not select TForce freight class {item.get('freight_class')} on row {index}. "
                    f"Options: {options[:15]}. Saved tforce_class_select_failed.png"
                )

        if row_weight_input.count() > 0 and has_value(item.get("weight")):
            clear_tforce_interference(page)
            set_input_value(row_weight_input, item.get("weight"))

    wait_after_step(page, 2)
    page.screenshot(path="tforce_before_submit.png", full_page=True)


def click_get_rate(page):
    button = first_visible(
        page,
        [
            "xpath=//button[contains(., 'Get Rate')]",
            "xpath=//input[contains(translate(@value,'GET RATE','get rate'),'get rate')]",
        ],
    )
    if button.count() == 0:
        page.screenshot(path="tforce_get_rate_missing.png", full_page=True)
        raise RuntimeError("Could not find TForce Get Rate button. Saved tforce_get_rate_missing.png")

    button.scroll_into_view_if_needed()
    dismiss_overlay_if_present(page)
    wait_for_tforce_popup_to_clear(page, timeout_ms=3000)

    clicked = False
    for _ in range(3):
        dismiss_overlay_if_present(page)
        wait_for_tforce_popup_to_clear(page, timeout_ms=2000)
        try:
            button.scroll_into_view_if_needed()
        except:
            pass
        try:
            button.click(force=True)
            clicked = True
        except:
            try:
                handle = button.element_handle()
                if handle is not None:
                    page.evaluate("(el) => el.click()", handle)
                    clicked = True
            except:
                pass
        wait_after_step(page, 2)
        if on_tforce_results_page(page):
            break
        page.wait_for_timeout(1200)

    if not clicked:
        page.screenshot(path="tforce_get_rate_click_failed.png", full_page=True)
        raise RuntimeError("Could not click TForce Get Rate button. Saved tforce_get_rate_click_failed.png")

    # TForce may update in-place or navigate. Only continue when results page indicators appear.
    for _ in range(30):
        maybe_close_tforce_popup(page)
        dismiss_overlay_if_present(page)
        if on_tforce_results_page(page):
            break
        page.wait_for_timeout(500)
    else:
        page.screenshot(path="tforce_after_submit_not_results.png", full_page=True)
        raise RuntimeError(
            "TForce Get Rate did not reach the results page. Saved tforce_after_submit_not_results.png"
        )

    page.screenshot(path="tforce_after_submit.png", full_page=True)


def parse_money_from_text(text: str):
    match = re.search(r"\$([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2}))", text or "")
    if not match:
        return None
    return float(match.group(1).replace(",", ""))


def scrape_results(page, timeout_sec=90):
    start = time.time()
    while time.time() - start < timeout_sec:
        maybe_close_tforce_popup(page)
        dismiss_overlay_if_present(page)
        page.wait_for_timeout(500)

        if not on_tforce_results_page(page):
            continue

        service_result = None
        try:
            service_result = page.evaluate(
                """
                () => {
                    const norm = (s) => (s || "").replace(/\\s+/g, " ").trim();
                    const sections = Array.from(document.querySelectorAll("body *"))
                      .map((el) => norm(el.textContent))
                      .filter(Boolean);

                    const ltlRowText = sections.find((text) =>
                      /TForce Freight LTL/i.test(text) && /\\$[0-9,]+(?:\\.[0-9]{2})/.test(text)
                    );
                    if (!ltlRowText) return null;

                    const priceMatch = ltlRowText.match(/\\$([0-9]+(?:,[0-9]{3})*(?:\\.[0-9]{2}))/);
                    const daysMatch = ltlRowText.match(/Days In Transit\\s*([0-9]+)/i);

                    return {
                      row_text: ltlRowText,
                      price: priceMatch ? priceMatch[1] : "",
                      days: daysMatch ? daysMatch[1] : "",
                    };
                }
                """
            )
        except:
            service_result = None

        if service_result and service_result.get("price"):
            price_text = service_result["price"]
            days_text = service_result.get("days") or ""
            page.screenshot(path="tforce_results_detected.png", full_page=True)
            return {
                "price": float(price_text.replace(",", "")),
                "transit_days": int(days_text) if str(days_text).isdigit() else None,
                "raw_money": [f"${price_text}"],
            }

        try:
            body_text = page.locator("body").inner_text(timeout=1500)
        except PlaywrightTimeoutError:
            continue
        except:
            continue

        raw_money = []

        try:
            labeled_rows = page.evaluate(
                """
                () => {
                    const norm = (s) => (s || "").replace(/\\s+/g, " ").trim();
                    return Array.from(document.querySelectorAll("body *"))
                      .map((el) => norm(el.textContent))
                      .filter((t) =>
                        t &&
                        /(?:total|net charge|estimate|quote)/i.test(t) &&
                        /\\$[0-9,]+(?:\\.[0-9]{2})/.test(t)
                      )
                      .slice(-20);
                }
                """
            )
        except:
            labeled_rows = []

        best_price = None
        preferred_patterns = [
            r"(?:total(?: charges?)?|net charges?|quote total|estimated charges?)\s*[:\-]?\s*\$([0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2}))",
        ]
        for row in reversed(labeled_rows or []):
            for pattern in preferred_patterns:
                match = re.search(pattern, row, re.I)
                if match:
                    best_price = float(match.group(1).replace(",", ""))
                    raw_money.append(f"${match.group(1)}")
                    break
            if best_price is not None:
                break

        if best_price is None:
            all_money = re.findall(r"\$[0-9]+(?:,[0-9]{3})*(?:\.[0-9]{2})", body_text or "")
            if all_money:
                best_price = float(all_money[-1].replace("$", "").replace(",", ""))
                raw_money = all_money[-8:]

        if best_price is None:
            continue

        days = None
        transit_patterns = [
            r"Transit(?: Time| Days)?\s*[:\-]?\s*([0-9]+)",
            r"Estimated(?: Delivery)?\s*[:\-]?\s*([0-9]+)\s*(?:business )?days",
            r"\b([0-9]+)\s*(?:business )?days\b",
        ]
        for pattern in transit_patterns:
            match = re.search(pattern, body_text or "", re.I)
            if match:
                days = int(match.group(1))
                break

        page.screenshot(path="tforce_results_detected.png", full_page=True)
        return {
            "price": best_price,
            "transit_days": days,
            "raw_money": raw_money,
        }

    page.screenshot(path="tforce_results_not_found.png", full_page=True)
    raise RuntimeError("Could not detect TForce results. Saved tforce_results_not_found.png")


def quote_tforce(data: dict, *, headless: bool = True, slow_mo: int = 0) -> dict:
    username = env_first("TFORCE_USERNAME", "TFORCE_USER", "TFORCE_LOGIN_USERNAME")
    password = env_first(
        "TFORCE_PASSWORD",
        "TFORCE_PASWORD",
        "TFORCE_PASS",
        "TFORCE_LOGIN_PASSWORD",
    )
    login_url = env_first("TFORCE_LOGIN_URL", "TFORCE_URL", "TFORCE_HOME_URL")

    if not username or not password or not login_url:
        raise RuntimeError(
            "Missing TFORCE_USERNAME / TFORCE_PASSWORD / TFORCE_LOGIN_URL in .env"
        )

    last_error = None
    with sync_playwright() as p:
        for attempt in range(1, MAX_TFORCE_ATTEMPTS + 1):
            browser = p.chromium.launch(headless=headless, slow_mo=slow_mo)
            page = browser.new_page()
            try:
                print(f"TForce attempt {attempt}: opening login surface...", flush=True)
                install_tforce_popup_watchers(page)
                open_login_surface(page, login_url)
                print(f"TForce attempt {attempt}: logging in...", flush=True)
                do_login(page, username, password)
                maybe_close_tforce_popup(page)
                print(f"TForce attempt {attempt}: opening rate estimate...", flush=True)
                goto_rate_estimate(page)
                maybe_close_tforce_popup(page)
                print(f"TForce attempt {attempt}: filling form...", flush=True)
                fill_rate_estimate_form(page, data)
                print(f"TForce attempt {attempt}: submitting rate request...", flush=True)
                click_get_rate(page)
                print(f"TForce attempt {attempt}: scraping results...", flush=True)
                scraped = scrape_results(page, timeout_sec=90)

                browser.close()

                return {
                    "carrier": "TFORCE",
                    "price": scraped["price"],
                    "transit_days": scraped["transit_days"],
                    "raw_money": scraped["raw_money"],
                }
            except Exception as exc:
                last_error = exc
                print(f"TForce attempt {attempt} failed: {exc}", flush=True)
                try:
                    page.screenshot(path=f"tforce_attempt_{attempt}_failed.png", full_page=True)
                except:
                    pass
                browser.close()
                if attempt < MAX_TFORCE_ATTEMPTS:
                    time.sleep(2)
                    continue

    raise last_error if last_error else RuntimeError("TForce quote failed")


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--origin-zip")
    parser.add_argument("--dest-zip")
    parser.add_argument("--freight-class")
    parser.add_argument("--weight")
    parser.add_argument(
        "--item",
        action="append",
        default=[],
        help="Repeatable commodity row in the format weight,class . Example: --item 1470,65 --item 1000,55",
    )
    parser.add_argument("--shipment-date")
    parser.add_argument("--pickup-city", default="SYLMAR, CA")
    parser.add_argument("--delivery-city", default="")
    parser.add_argument("--show-browser", action="store_true")
    parser.add_argument("--slow-mo", type=int, default=0)
    return parser.parse_args()


def parse_cli_items(raw_items):
    items = []
    for idx, raw in enumerate(raw_items or [], start=1):
        text = str(raw or "").strip()
        if not text:
            continue
        parts = [part.strip() for part in text.split(",")]
        if len(parts) != 2:
            raise ValueError(
                f"Invalid --item value '{text}'. Expected weight,class like 1470,65"
            )
        weight, freight_class = parts
        items.append(
            {
                "pallet_number": idx,
                "pieces": 1,
                "length": "",
                "width": "",
                "height": "",
                "weight": weight,
                "weight_lb": weight,
                "freight_class": freight_class,
            }
        )
    return items


def cli_data(args):
    if not any([args.origin_zip, args.dest_zip, args.freight_class, args.weight, args.shipment_date, args.item]):
        return None
    pallet_items = parse_cli_items(args.item)
    default_weight = args.weight or (pallet_items[0]["weight"] if pallet_items else "")
    default_class = args.freight_class or (pallet_items[0]["freight_class"] if pallet_items else "")
    return {
        "origin_zip": args.origin_zip or "",
        "dest_zip": args.dest_zip or "",
        "pallets": str(len(pallet_items) or 1),
        "length": "",
        "width": "",
        "height": "",
        "weight": default_weight,
        "pieces": "",
        "freight_class": default_class,
        "pickup_city": args.pickup_city or "",
        "delivery_city": args.delivery_city or "",
        "shipment_date": args.shipment_date or datetime.now().strftime("%m/%d/%Y"),
        "pallet_items": pallet_items,
    }


def main():
    args = parse_args()
    data = cli_data(args)
    ss = None

    if data is None:
        client = gs_client()
        ss = client.open(SHEET_NAME)
        data = read_input(ss)

    result = quote_tforce(data, headless=not args.show_browser, slow_mo=args.slow_mo)
    print("TFORCE result:", result)

    if ss is not None:
        write_result(
            ss,
            carrier_name="TFORCE",
            result=result,
            origin_city=data.get("pickup_city", ""),
            dest_city=data.get("delivery_city", ""),
        )
        print("✅ Wrote TFORCE quote into Broker Result.")


if __name__ == "__main__":
    main()
