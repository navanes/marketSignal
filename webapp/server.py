from __future__ import annotations

import asyncio
import base64
from datetime import date
from datetime import datetime
from datetime import timedelta
from html import unescape
from uuid import uuid4
import json
import os
from pathlib import Path
import re
import socket
import sys
from typing import Optional
from urllib.parse import quote
from urllib.request import Request as UrlRequest
from urllib.request import urlopen

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
STATIC_DIR = BASE_DIR / "static"
SERVICE_ACCOUNT_FILE = PROJECT_ROOT / "service_account.json"
PROFILES_FILE = PROJECT_ROOT / "company_profiles.json"
TRACKING_CACHE_FILE = PROJECT_ROOT / ".tracking_check_cache.json"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import gspread
from gspread.exceptions import WorksheetNotFound
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.oauth2.service_account import Credentials
from pydantic import BaseModel, Field

load_dotenv(dotenv_path=PROJECT_ROOT / ".env", override=False)

try:
    from quote_central import quote_central
    CENTRAL_IMPORT_ERROR = ""
except ImportError as exc:
    quote_central = None
    CENTRAL_IMPORT_ERROR = str(exc)
from quote_mycarrier import quote_mycarrier
from quote_priority1 import quote_priority1
from quote_numark import quote_numark
from quote_glovalink import quote_glovalink
from quote_glt import quote_glt
from quote_schneider import quote_schneider, quote_schneider_async
from quote_tforce import quote_tforce
from quote_total import quote_total, write_result
from sheets_utils import with_gsheets_retry
from write_brokers import write_brokers

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_NAME = "Freight Quote Agent (MVP)"
INPUT_TAB = "Input"
LOGS_TAB = "LOGS"
TRACKING_TAB = "TRACKING"
TRACKING_LOG_TAB = "TRACKING LOG"
LOGS_HEADERS = [
    "Date",
    "Customer",
    "Destination City",
    "Pallet Qty",
    "Pallet Weight",
    "Dimenstions(48 x 48 x 20)",
    "Top 3 cheapest",
]

CELL_MAP = {
    "order_number": "B2",
    "origin_zip": "B3",
    "destination_zip": "B4",
    "pallet_count": "B5",
    "length_in": "B6",
    "width_in": "B7",
    "height_in": "B8",
    "weight_lb": "B9",
    "pieces": "B10",
    "freight_class": "B13",
    "pickup_city": "B14",
    "delivery_city": "B15",
    "shipment_date": "B16",
}

def today_form_date() -> str:
    return date.today().isoformat()


def default_form_values():
    return {
        "order_number": "",
        "origin_zip": "91342",
        "destination_zip": "",
        "pallet_count": "",
        "length_in": "",
        "width_in": "",
        "height_in": "",
        "weight_lb": "",
        "pieces": "",
        "freight_class": "",
        "pickup_city": "SYLMAR, CA",
        "delivery_city": "",
        "shipment_date": today_form_date(),
    }


def local_ipv4_addresses() -> list[str]:
    seen: set[str] = set()
    addresses: list[str] = []

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            host = sock.getsockname()[0]
            if host and not host.startswith("127."):
                seen.add(host)
                addresses.append(host)
    except OSError:
        pass

    try:
        host = socket.gethostbyname(socket.gethostname())
        if host and not host.startswith("127.") and host not in seen:
            seen.add(host)
            addresses.append(host)
    except OSError:
        pass

    return addresses


def local_mdns_hostname() -> str:
    host = (socket.gethostname() or "").strip()
    if not host:
        return ""
    return host if host.endswith(".local") else f"{host}.local"

app = FastAPI(title="Freight Quote Agent Web")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
RUN_CANCEL_FLAGS: dict[str, bool] = {}
QUOTE_RUN_LOCK = asyncio.Lock()
TRACKING_UPDATE_LOCK = asyncio.Lock()
WEBAPP_ENABLE_GLT = False
WEBAPP_GLT_HEADLESS = True
WEBAPP_GLT_SLOW_MO = 0
WEBAPP_GLT_ONLY_DEBUG = False
WEBAPP_REVEAL_SLOW_MO = int((os.getenv("WEBAPP_REVEAL_SLOW_MO") or "250").strip() or "250")
RESULT_DISPLAY_ORDER = [
    "GLT",
    "SCHNEIDER",
    "MYCARRIER",
    "PRIORITY1",
    "TOTAL",
    "CENTRAL TRANSPORTATION",
    "NUMARK",
    "TFORCE",
    "GLOVALINK",
]
ASSISTANT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini"
ASSISTANT_SYSTEM_PROMPT = """You are the in-app assistant for Windgate's Freight Quote Agent.

Your job is to help users fill out the quote form, understand quoting results, troubleshoot issues, and explain next steps clearly.

Rules:
- Be concise, practical, and specific to the current page.
- Prefer short bullet points or short step lists over long essays.
- Use the actual form fields and latest results from context when available.
- If a screenshot is attached, inspect it carefully and reference visible UI details.
- If there is an error, structure the reply around:
  1. likely cause
  2. what to check now
  3. whether to escalate
- If the user should escalate, explicitly tell them to copy diagnostics and paste them to the developer.
- Do not claim you changed the app or fixed the backend. You are a support assistant inside the app.
- Do not invent missing values. If required data is missing, say exactly what is missing.
- If the user asks about shipment values, freight class, pallet setup, or quoting workflow, answer directly.
- If context includes recent quote results, use them.
"""

TRACKING_YELLOW = {"red": 1.0, "green": 0.949, "blue": 0.8}
TRACKING_PARTIAL_YELLOW = {"red": 1.0, "green": 1.0, "blue": 0.0}
TRACKING_GREEN = {"red": 0.851, "green": 0.918, "blue": 0.827}
TRACKING_CHECK_COOLDOWN = timedelta(hours=2)
TRACKING_ETA_CHECK_WINDOW_DAYS = 2
TRACKING_CHECKED_PATTERN = re.compile(r"\(checked\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2})\)", re.I)
TRACKING_CACHE_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"
TRACKING_LOG_HEADERS = [
    "Checked At",
    "Result",
    "Row #",
    "Carrier",
    "Tracking #",
    "Old ETA",
    "New ETA",
    "Old Actual",
    "New Actual",
    "Note",
]


def gs_client():
    creds = Credentials.from_service_account_file(str(SERVICE_ACCOUNT_FILE), scopes=SCOPES)
    return gspread.authorize(creds)


def spreadsheet():
    client = gs_client()
    return with_gsheets_retry(lambda: client.open(SHEET_NAME))


def input_sheet():
    ss = spreadsheet()
    return with_gsheets_retry(lambda: ss.worksheet(INPUT_TAB))


def spreadsheet_worksheet(ss, title: str):
    return with_gsheets_retry(lambda: ss.worksheet(title))


def load_profiles() -> dict:
    if not PROFILES_FILE.exists():
        return {}
    try:
        return json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
    except:
        return {}


def save_profiles(profiles: dict):
    PROFILES_FILE.write_text(json.dumps(profiles, indent=2), encoding="utf-8")


def profile_data(record):
    if isinstance(record, dict) and "data" in record:
        return record.get("data") or {}
    return record if isinstance(record, dict) else {}


def profile_summary(record):
    if isinstance(record, dict):
        return record.get("last_quote_summary") or {}
    return {}


def no_cache_headers() -> dict[str, str]:
    return {
        "Cache-Control": "no-store, no-cache, must-revalidate, max-age=0",
        "Pragma": "no-cache",
        "Expires": "0",
    }


def profile_input_data_from_payload(payload: "InputPayload") -> dict:
    return payload.model_dump(
        exclude={
            "quote_target",
            "broker_targets",
            "direct_carrier_targets",
            "browser_visibility",
        }
    )


def fetch_text(url: str, *, encoding: str = "utf-8", timeout: int = 30) -> str:
    request = UrlRequest(url, headers={"User-Agent": "Mozilla/5.0"})
    with urlopen(request, timeout=timeout) as response:
        raw = response.read()
    return raw.decode(encoding, errors="ignore")


def clean_tracking_text(value: str) -> str:
    text = re.sub(r"<[^>]+>", " ", value or "")
    text = unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def parse_us_date(value: str) -> Optional[date]:
    text = (value or "").strip()
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if not match:
        return None
    month, day, year = (int(part) for part in match.groups())
    return date(year, month, day)


def parse_us_date_flexible(value: str) -> Optional[date]:
    text = (value or "").strip()
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", text)
    if not match:
        return None
    month = int(match.group(1))
    day = int(match.group(2))
    year = int(match.group(3))
    if year < 100:
        year += 2000
    return date(year, month, day)


def parse_compact_date(value: str) -> Optional[date]:
    text = (value or "").strip()
    if not re.fullmatch(r"\d{8}", text):
        return None
    return date(int(text[:4]), int(text[4:6]), int(text[6:8]))


def parse_month_day(value: str, *, roll_past_forward: bool = True) -> Optional[date]:
    text = value or ""
    if re.search(r"\btoday\b", text, re.I):
        return date.today()
    if re.search(r"\btomorrow\b", text, re.I):
        return date.today() + timedelta(days=1)

    match = re.search(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2})\b",
        text,
        re.I,
    )
    if not match:
        return None
    month_names = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    today = date.today()
    parsed = date(today.year, month_names[match.group(1).lower()], int(match.group(2)))
    if roll_past_forward and parsed < today - timedelta(days=30):
        parsed = date(today.year + 1, parsed.month, parsed.day)
    return parsed


def parse_named_date(value: str) -> Optional[date]:
    text = value or ""
    month_names = {
        "january": 1,
        "february": 2,
        "march": 3,
        "april": 4,
        "may": 5,
        "june": 6,
        "july": 7,
        "august": 8,
        "september": 9,
        "october": 10,
        "november": 11,
        "december": 12,
    }
    match = re.search(
        r"\b(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{1,2}),?\s+(\d{4})\b",
        text,
        re.I,
    )
    if match:
        return date(int(match.group(3)), month_names[match.group(1).lower()], int(match.group(2)))

    match = re.search(
        r"\b(\d{1,2})\s+(January|February|March|April|May|June|July|August|September|October|November|December)\s+(\d{4})\b",
        text,
        re.I,
    )
    if match:
        return date(int(match.group(3)), month_names[match.group(2).lower()], int(match.group(1)))
    return None


def parse_ups_date_from_text(text: str, label: str) -> Optional[date]:
    match = re.search(rf"{label}\s+([\s\S]{{0,180}})", text or "", re.I)
    if not match:
        return None

    snippet = match.group(1)
    stop_match = re.search(
        r"\n(?:Your package|Ship To|Label Created|We Have Your Package|On the Way|Out for Delivery|Delivery|Track Another Package)\b",
        snippet,
        re.I,
    )
    if stop_match:
        snippet = snippet[: stop_match.start()]

    return parse_named_date(snippet) or parse_month_day(
        snippet,
        roll_past_forward=not re.search(r"Delivered", label, re.I),
    )


def parse_ups_shipment_piece_count(text: str) -> Optional[int]:
    match = re.search(r"\b\d+\s+of\s+(\d+)\s+Piece\s+Shipment\b", text or "", re.I)
    if not match:
        return None
    return int(match.group(1))


def parse_ups_package_blocks(text: str) -> list[dict]:
    content = text or ""
    section_match = re.search(
        r"(?:Other|All) Packages in this Shipment([\s\S]+?)(?:Stay Safe|Track Another Package|Shipment Details|$)",
        content,
        re.I,
    )
    section = section_match.group(1) if section_match else content
    matches = list(re.finditer(r"\b1Z[A-Z0-9]{16}\b", section, re.I))
    packages: list[dict] = []
    seen: set[str] = set()
    for index, match in enumerate(matches):
        tracking_number = match.group(0).upper()
        if tracking_number in seen:
            continue
        seen.add(tracking_number)
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        block = section[match.end() : end]
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        status = ""
        for line in lines:
            if re.search(r"copy|tracking|latest update|delivered on", line, re.I):
                continue
            status = clean_tracking_text(line)
            break
        if not status:
            status = "Delivered" if re.search(r"\bDelivered\b", block, re.I) else "tracking"
        actual = parse_ups_date_from_text(block, r"Delivered(?:\s+On)?") if re.search(r"\bDelivered\b", status, re.I) else None
        eta = parse_ups_date_from_text(block, r"Estimated delivery")
        packages.append(
            {
                "tracking": tracking_number,
                "status": status,
                "eta": eta,
                "actual": actual,
            }
        )
    return packages


def summarize_ups_tracking(text: str, tracking: str) -> dict:
    eta = parse_ups_date_from_text(text, r"Estimated delivery")
    actual = parse_ups_date_from_text(text, r"Delivered(?:\s+On)?")
    piece_count = parse_ups_shipment_piece_count(text)
    packages = parse_ups_package_blocks(text) if piece_count and piece_count > 1 else []

    if piece_count and piece_count > 1:
        if len(packages) < piece_count:
            return {
                "eta": eta,
                "actual": None,
                "partial": True,
                "note": f"UPS: {piece_count} package shipment; could not read every package status",
            }

        delivered_packages = [package for package in packages if re.search(r"\bDelivered\b", package["status"], re.I)]
        pending_packages = [package for package in packages if package not in delivered_packages]
        if pending_packages:
            pending = pending_packages[0]
            package_etas = [package["eta"] for package in packages if package["eta"]]
            pending_text = f"{pending['tracking']} {pending['status']}".strip()
            return {
                "eta": eta or (min(package_etas) if package_etas else None),
                "actual": None,
                "partial": True,
                "note": f"UPS: {len(delivered_packages)}/{len(packages)} packages delivered; pending {pending_text}",
            }

        actual_dates = [package["actual"] for package in packages if package["actual"]]
        actual = max(actual_dates) if actual_dates else actual
        return {
            "eta": eta,
            "actual": actual,
            "partial": False,
            "note": f"UPS: All {len(packages)} packages delivered {sheet_date(actual)}".strip(),
        }

    return {
        "eta": eta,
        "actual": actual,
        "partial": False,
        "note": f"UPS: {'Delivered' if actual else 'ETA' if eta else 'tracking'} {sheet_date(actual or eta)}".strip(),
    }


def parse_first_us_date_near_label(text: str, label: str, *, pick_last: bool = False, window: int = 300) -> Optional[date]:
    match = re.search(label, text or "", re.I)
    if not match:
        return None
    snippet = (text or "")[match.end() : match.end() + window]
    dates = re.findall(r"\d{1,2}/\d{1,2}/\d{4}", snippet)
    if not dates:
        return None
    return parse_us_date(dates[-1] if pick_last else dates[0])


def parse_estes_tracking_text(text: str) -> tuple[Optional[date], Optional[date], str]:
    content = text or ""
    actual = None
    eta = None

    status_match = re.search(r"\b(Delivered|Delivery Attempted|In Transit|Picked Up|Out for Delivery)\b", content, re.I)
    status = status_match.group(1).title() if status_match else ""

    if re.fullmatch(r"Delivered", status, re.I):
        actual = (
            parse_first_us_date_near_label(content, r"Actual Delivery Date")
            or parse_first_us_date_near_label(content, r"Delivered Date")
            or parse_first_us_date_near_label(content, r"Delivery Date")
            or parse_first_us_date_near_label(content, r"Delivery Completed")
        )

    if not actual:
        eta = parse_first_us_date_near_label(content, r"Estimated Delivery Date")
        if not eta:
            eta = parse_first_us_date_near_label(content, r"Appointment Date")
        if not eta:
            eta = parse_first_us_date_near_label(content, r"Estimated Delivery", pick_last=True)

    if not eta and not actual and not re.fullmatch(r"Delivered", status, re.I):
        row_match = re.search(
            r"(\d{6,})\s+(\d{1,2}/\d{1,2}/\d{4})\s+\S+\s+(\d{1,2}/\d{1,2}/\d{4})\s+(Delivered|In Transit|Picked Up|Out for Delivery)",
            content,
            re.I,
        )
        if row_match:
            eta = parse_us_date(row_match.group(3))

    status = status or ("Delivered" if actual else "ETA" if eta else "tracking")
    return eta, actual, status


def parse_tforce_tracking_text(text: str) -> tuple[Optional[date], Optional[date], str]:
    content = text or ""
    actual = None
    eta = parse_first_us_date_near_label(content, r"Estimated Delivery", window=180)
    if not eta:
        eta = parse_us_date_flexible(content)

    delivered_match = re.search(r"\bDelivered\b[\s\S]{0,180}?(\d{1,2}/\d{1,2}/\d{2,4})", content, re.I)
    if delivered_match:
        actual = parse_us_date_flexible(delivered_match.group(1))

    status_match = re.search(r"\b(Delivered|In Transit|Picked Up|Out for Delivery)\b", content, re.I)
    status = status_match.group(1).title() if status_match else ("Delivered" if actual else "ETA" if eta else "tracking")
    if status == "Delivered" and not actual:
        actual = (
            parse_first_us_date_near_label(content, r"\bDelivery\b", window=160)
            or parse_first_us_date_near_label(content, r"Delivery\s+Date", window=160)
        )
    if actual:
        eta = None
    return eta, actual, status


def parse_xpo_tracking_text(text: str) -> tuple[Optional[date], Optional[date], str]:
    content = text or ""
    actual = None
    eta = None

    eta = parse_first_us_date_near_label(content, r"Est\.?\s*Delivery", window=160) or parse_first_us_date_near_label(
        content,
        r"Estimated Delivery",
        window=160,
    )
    if not eta:
        eta_match = re.search(r"\bEst\.?\s*Delivery\s+(\d{1,2}/\d{1,2}/\d{2,4})", content, re.I)
        if eta_match:
            eta = parse_us_date_flexible(eta_match.group(1))

    delivered_match = re.search(r"\bDelivered\b[\s\S]{0,180}?(\d{1,2}/\d{1,2}/\d{2,4})", content, re.I)
    if delivered_match:
        actual = parse_us_date_flexible(delivered_match.group(1))

    status_match = re.search(r"\b(Delivered|In Transit|Picked Up|At Final Service Center|Out for Delivery)\b", content, re.I)
    status = status_match.group(1).title() if status_match else ("Delivered" if actual else "ETA" if eta else "tracking")
    return eta, actual, status


def parse_total_tracking_text(text: str) -> tuple[Optional[date], Optional[date], str]:
    content = text or ""
    status_match = re.search(r"\bStatus:\s*([^\n\r]+)", content, re.I)
    status = clean_tracking_text(status_match.group(1)).rstrip(".") if status_match else ""

    eta = parse_first_us_date_near_label(content, r"Estimated Delivery Date", window=120)
    actual = None
    if re.search(r"\b(delivered|delivery complete)\b", status, re.I):
        actual = parse_first_us_date_near_label(content, r"Delivered Date", window=80)
        if not actual:
            actual = parse_first_us_date_near_label(content, r"\bDelivered\b", window=220)
        if not actual:
            actual = parse_first_us_date_near_label(content, r"\bStatus:\s*Delivered\b", window=220)

    if not status:
        status_match = re.search(
            r"\b(PICKUP COMPLETED|Pickup Requested|Picked Up|In Transit|Out for Delivery|Delivered)\b",
            content,
            re.I,
        )
        status = status_match.group(1).title() if status_match else ("Delivered" if actual else "ETA" if eta else "tracking")
    return eta, actual, status


def parse_abf_tracking_text(text: str) -> tuple[Optional[date], Optional[date], str]:
    content = text or ""
    current_status_match = re.search(r"\bCURRENT STATUS\s+([^\n\r]+)", content, re.I)
    status = clean_tracking_text(current_status_match.group(1)).title() if current_status_match else ""
    eta = (
        parse_first_us_date_near_label(content, r"Estimated\s+Delivery(?:\s+Date)?", window=220)
        or parse_first_us_date_near_label(content, r"Scheduled\s+Delivery(?:\s+Date)?", window=220)
        or parse_first_us_date_near_label(content, r"Expected\s+Delivery(?:\s+Date)?", window=220)
    )
    actual = None
    delivered_on_match = re.search(r"\bdelivered\s+on\s+(\d{1,2}/\d{1,2}/\d{2,4})", content, re.I)
    if delivered_on_match:
        actual = parse_us_date_flexible(delivered_on_match.group(1))
    elif re.fullmatch(r"Delivered", status or "", re.I):
        actual = (
            parse_first_us_date_near_label(content, r"Actual\s+Delivery(?:\s+Date)?", window=220)
            or parse_first_us_date_near_label(content, r"Delivered(?:\s+On)?", window=220)
            or parse_first_us_date_near_label(content, r"Delivery\s+Date", window=220)
        )

    if not status:
        status_match = re.search(
            r"\b(Delivered|Out for Delivery|In Transit|Arrived(?: at)? Terminal|At Terminal|Picked Up|Pickup Requested|Appointment Scheduled)\b",
            content,
            re.I,
        )
        status = status_match.group(1).title() if status_match else ("Delivered" if actual else "ETA" if eta else "tracking")
    return eta, actual, status


def parse_roadrunner_tracking_text(text: str) -> tuple[Optional[date], Optional[date], str]:
    content = text or ""
    status_match = re.search(r"\bShipment\s+Status\s+([^\n\r]+)", content, re.I)
    status = clean_tracking_text(status_match.group(1)).title() if status_match else ""

    eta = (
        parse_first_us_date_near_label(content, r"Estimated\s+Delivery", window=160)
        or parse_first_us_date_near_label(content, r"Scheduled\s+Delivery", window=160)
        or parse_first_us_date_near_label(content, r"Expected\s+Delivery", window=160)
    )

    actual = None
    if re.search(r"\bDelivered\b", status, re.I):
        actual = (
            parse_first_us_date_near_label(content, r"Actual\s+Delivery(?:\s+Date)?", window=180)
            or parse_first_us_date_near_label(content, r"Delivered(?:\s+On)?", window=180)
            or parse_first_us_date_near_label(content, r"Delivery\s+Date", window=180)
        )

    if not status:
        status_match = re.search(
            r"\b(Delivered|Out for Delivery|In Transit|On the way|We Have Your Shipment|Picked Up)\b",
            content,
            re.I,
        )
        status = status_match.group(1).title() if status_match else ("Delivered" if actual else "ETA" if eta else "tracking")

    if actual:
        eta = None
    return eta, actual, status


def parse_usps_tracking_text(text: str) -> tuple[Optional[date], Optional[date], str]:
    content = text or ""
    actual = None
    eta = (
        parse_first_us_date_near_label(content, r"Expected\s+Delivery(?:\s+on)?", window=220)
        or parse_named_date(content)
        or parse_month_day(content)
    )

    delivered_match = re.search(r"\bDelivered\b[\s\S]{0,220}", content, re.I)
    if delivered_match:
        snippet = delivered_match.group(0)
        actual = parse_first_us_date_near_label(snippet, r"\bDelivered\b", window=220) or parse_named_date(snippet) or parse_month_day(
            snippet,
            roll_past_forward=False,
        )

    status_match = re.search(
        r"\b(Delivered|Out for Delivery|Arrived at Post Office|In Transit|Moving Through Network|Accepted|Pre-Shipment)\b",
        content,
        re.I,
    )
    status = status_match.group(1).title() if status_match else ("Delivered" if actual else "ETA" if eta else "tracking")
    if not re.fullmatch(r"Delivered", status or "", re.I):
        actual = None
    if actual:
        eta = None
    return eta, actual, status


def parse_glovalink_tracking_text(text: str) -> tuple[Optional[date], Optional[date], str]:
    content = text or ""
    if re.search(r"Cannot\s+Find\s+Order|No\s+(?:tracking|shipment|order)\s+found|not\s+found", content, re.I):
        return None, None, "Cannot Find Order"

    actual = None
    eta = (
        parse_first_us_date_near_label(content, r"Estimated\s+Delivery(?:\s+Date)?", window=220)
        or parse_first_us_date_near_label(content, r"Scheduled\s+Delivery(?:\s+Date)?", window=220)
        or parse_first_us_date_near_label(content, r"Expected\s+Delivery(?:\s+Date)?", window=220)
        or parse_first_us_date_near_label(content, r"\bETA\b", window=160)
        or parse_first_us_date_near_label(content, r"Appointment\s+Date", window=160)
    )

    delivered_match = re.search(r"\bDelivered\b[\s\S]{0,240}?(\d{1,2}/\d{1,2}/\d{2,4})", content, re.I)
    if delivered_match:
        actual = parse_us_date_flexible(delivered_match.group(1))
    if not actual:
        actual = (
            parse_first_us_date_near_label(content, r"Actual\s+Delivery(?:\s+Date)?", window=220)
            or parse_first_us_date_near_label(content, r"Delivered(?:\s+On)?", window=220)
            or parse_first_us_date_near_label(content, r"Delivery\s+Date", window=220)
        )

    status_match = re.search(
        r"\b(Delivered|Out for Delivery|In Transit|Picked Up|At Terminal|Arrived(?: at)? Terminal|Appointment Scheduled|Order Received)\b",
        content,
        re.I,
    )
    status = status_match.group(1).title() if status_match else ("Delivered" if actual else "ETA" if eta else "tracking")
    return eta, actual, status


def sheet_date(value: date | None) -> str:
    if not value:
        return ""
    return f"{value.month}/{value.day}/{value.year}"


def tracking_log_value(value) -> str:
    if isinstance(value, date):
        return sheet_date(value)
    return str(value or "").strip()


def column_letter(index_one_based: int) -> str:
    result = ""
    index = index_one_based
    while index:
        index, remainder = divmod(index - 1, 26)
        result = chr(65 + remainder) + result
    return result


def sheets_formula_string(value: str) -> str:
    return str(value or "").replace('"', '""')


def hyperlink_formula(label: str, url: str) -> str:
    return f'=HYPERLINK("{sheets_formula_string(url)}","{sheets_formula_string(label)}")'


def update_agent_update_cell(ws, row_number: int, agent_update_col: int, note: str, url: str = ""):
    cell = f"{column_letter(agent_update_col + 1)}{row_number}"
    value = hyperlink_formula(note, url) if url else note
    with_gsheets_retry(lambda: ws.update(cell, [[value]], value_input_option="USER_ENTERED"))


def tracking_log_sheet(ss):
    try:
        log_ws = spreadsheet_worksheet(ss, TRACKING_LOG_TAB)
    except WorksheetNotFound:
        log_ws = with_gsheets_retry(lambda: ss.add_worksheet(title=TRACKING_LOG_TAB, rows=1000, cols=len(TRACKING_LOG_HEADERS)))

    values = with_gsheets_retry(lambda: log_ws.get_all_values())
    if not values or [str(value or "").strip() for value in values[0][: len(TRACKING_LOG_HEADERS)]] != TRACKING_LOG_HEADERS:
        with_gsheets_retry(lambda: log_ws.update("A1:J1", [TRACKING_LOG_HEADERS]))
        with_gsheets_retry(lambda: log_ws.format("A1:J1", {"textFormat": {"bold": True}}))
        with_gsheets_retry(lambda: log_ws.freeze(rows=1))
    return log_ws


def append_tracking_log_rows(ss, rows: list[list[str]]):
    if not rows:
        return
    log_ws = tracking_log_sheet(ss)
    with_gsheets_retry(lambda: log_ws.append_rows(rows, value_input_option="USER_ENTERED"))


def tracking_log_row(
    checked_at: datetime,
    result: str,
    row_number: int,
    carrier: str,
    tracking: str,
    old_eta: str = "",
    new_eta: str = "",
    old_actual: str = "",
    new_actual: str = "",
    note: str = "",
) -> list[str]:
    return [
        checked_at.strftime("%Y-%m-%d %H:%M:%S"),
        result,
        str(row_number),
        carrier,
        tracking,
        tracking_log_value(old_eta),
        tracking_log_value(new_eta),
        tracking_log_value(old_actual),
        tracking_log_value(new_actual),
        clean_agent_update_label(note),
    ]


def tracking_checked_stamp(now: Optional[datetime] = None) -> str:
    return (now or datetime.now()).strftime("%Y-%m-%d %H:%M")


def with_tracking_checked_stamp(note: str, now: Optional[datetime] = None) -> str:
    base = TRACKING_CHECKED_PATTERN.sub("", note or "").strip()
    base = re.sub(r"\s{2,}", " ", base).strip()
    return f"{base} (checked {tracking_checked_stamp(now)})" if base else f"Checked (checked {tracking_checked_stamp(now)})"


def last_tracking_checked_at(note: str) -> Optional[datetime]:
    match = TRACKING_CHECKED_PATTERN.search(note or "")
    if not match:
        return None
    try:
        return datetime.strptime(match.group(1), "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def tracking_checked_recently(note: str, now: Optional[datetime] = None) -> bool:
    checked_at = last_tracking_checked_at(note)
    if not checked_at:
        return False
    current = now or datetime.now()
    return timedelta(0) <= current - checked_at < TRACKING_CHECK_COOLDOWN


def tracking_eta_due_for_recheck(eta_value: str, current_date: Optional[date] = None) -> bool:
    eta_date = parse_us_date_flexible(eta_value)
    if not eta_date:
        return True
    today = current_date or date.today()
    return eta_date <= today + timedelta(days=TRACKING_ETA_CHECK_WINDOW_DAYS)


def tracking_cache_key(carrier: str, tracking: str) -> str:
    cleaned_tracking = re.sub(r"\s+", "", tracking or "").upper()
    return f"{normalize_carrier(carrier)}::{cleaned_tracking}"


def load_tracking_check_cache() -> dict[str, str]:
    if not TRACKING_CACHE_FILE.exists():
        return {}
    try:
        data = json.loads(TRACKING_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def save_tracking_check_cache(cache: dict[str, str]):
    try:
        TRACKING_CACHE_FILE.write_text(json.dumps(cache, indent=2, sort_keys=True), encoding="utf-8")
    except Exception:
        pass


def cache_checked_at(cache: dict[str, str], carrier: str, tracking: str) -> Optional[datetime]:
    value = str(cache.get(tracking_cache_key(carrier, tracking)) or "").strip()
    if not value:
        return None
    try:
        return datetime.strptime(value, TRACKING_CACHE_TIME_FORMAT)
    except ValueError:
        return None


def cached_tracking_checked_recently(cache: dict[str, str], carrier: str, tracking: str, now: Optional[datetime] = None) -> bool:
    checked_at = cache_checked_at(cache, carrier, tracking)
    if not checked_at:
        return False
    current = now or datetime.now()
    return timedelta(0) <= current - checked_at < TRACKING_CHECK_COOLDOWN


def remember_tracking_checked(cache: dict[str, str], carrier: str, tracking: str, now: Optional[datetime] = None):
    cache[tracking_cache_key(carrier, tracking)] = (now or datetime.now()).strftime(TRACKING_CACHE_TIME_FORMAT)


def tracking_note_with_link(carrier: str, note: str, tracking: str) -> tuple[str, str]:
    url = tracking_url(carrier, tracking)
    if not url:
        return note, ""
    return f"{note} | Open {normalize_carrier(carrier)}", url


def clean_agent_update_label(note: str) -> str:
    label = re.sub(r"\s*\|\s*https?://\S+", "", note or "", flags=re.I)
    label = re.sub(r"\s*https?://\S+", "", label, flags=re.I)
    return label.strip(" |") or "Open tracking"


def tracking_url(carrier: str, tracking: str) -> str:
    normalized = normalize_carrier(carrier)
    encoded = quote(tracking)
    if normalized == "UPS":
        return f"https://www.ups.com/track?loc=en_US&tracknum={encoded}&requester=ST/trackdetails"
    if normalized == "USPS":
        return f"https://tools.usps.com/go/TrackConfirmAction?tLabels={encoded}"
    if normalized == "ESTES":
        return f"https://www.estes-express.com/myestes/shipment-tracking/?query={encoded}&type=PRO"
    if normalized == "TFORCE":
        return f"https://www.tforcefreight.com/ltl/apps/Tracking?proNumbers={encoded}%3B"
    if normalized == "TFWW":
        return f"https://tfww.hyperiontms.com/shipmenttracking?loadnumber={encoded}"
    if normalized == "XPO":
        return f"https://ext-web.ltl-xpo.com/public-app/shipments?referenceNumber={encoded}"
    if normalized == "TOTAL":
        return "http://tracking.carrierlogistics.com/scripts/tot.pol/facts"
    if normalized == "ABF":
        return f"https://view.arcb.com/nlo/tools/tracking/{encoded}"
    if normalized == "ROADRUNNER":
        return "https://freight.rrts.com/Pages/Home.aspx"
    if normalized == "R&L":
        return f"https://www2.rlcarriers.com/freight/shipping/shipment-tracing?pro={encoded}&docType=PRO&source=web"
    if normalized == "DTI":
        return "https://my.dtitrans.com/shipmenttracking"
    if normalized == "NUMARK":
        return f"http://tracking.numarktransportation.net/cgibin/wbprotrk?wbfb={encoded}&wbscac="
    if normalized == "GLOVALINK":
        return "https://orders.glovalink.com/ENTRACK2/Track/QuickTrack"
    return ""


def normalize_carrier(value: str) -> str:
    carrier = re.sub(r"\s+", " ", (value or "").strip().upper())
    if carrier in {"R&L", "R+L", "R L", "RL CARRIERS", "R AND L"}:
        return "R&L"
    if re.fullmatch(r"UPS(?:\s*\(\s*SAMPLES?\s*\)|\s+SAMPLES?)?", carrier):
        return "UPS"
    if carrier in {"USPS", "U S P S", "UNITED STATES POSTAL SERVICE"}:
        return "USPS"
    if carrier in {"ESTES"}:
        return "ESTES"
    if carrier in {"TFORCE", "T FORCE", "TFORCE FREIGHT", "T FORCE FREIGHT", "TFORCE FREIGHT INC"}:
        return "TFORCE"
    if carrier in {"TFWW", "T F W W", "TFWW FREIGHT"}:
        return "TFWW"
    if carrier in {"XPO", "XPO LTL", "XPO LOGISTICS"}:
        return "XPO"
    if carrier in {"TOTAL", "TOTAL TRANSPORTATION", "TOTAL TRANSPORTATION AND DISTRIBUTION", "TOTAL TRANSPORTATION & DISTRIBUTION"}:
        return "TOTAL"
    if carrier in {"ABF", "ABF FREIGHT", "ARCBEST", "ARC BEST", "ARCBEST ABF", "ABF FREIGHT SYSTEM"}:
        return "ABF"
    if carrier in {"ROADRUNNER", "ROADRUNNER FREIGHT", "RRTS", "ROAD RUNNER", "ROAD RUNNER FREIGHT"}:
        return "ROADRUNNER"
    if carrier in {"NUMARK"}:
        return "NUMARK"
    if carrier in {"GLOVALINK", "GLOVA LINK", "GLOVA-LINK", "GLOVA LINK FREIGHT", "GLOVALINK FREIGHT"}:
        return "GLOVALINK"
    if carrier == "GOLD EAGLE TRANSPORTATION" or re.match(r"^DTI(?:\b|[\s/\\-])", carrier):
        return "DTI"
    return carrier


def get_numark_tracking(pro: str) -> dict:
    html = fetch_text(tracking_url("NUMARK", pro), encoding="iso-8859-1")
    if re.search(r"Bill not found", html, re.I):
        return {"eta": None, "actual": None, "note": "Numark: Bill not found"}

    actual = None
    eta = None
    latest = ""
    for row in re.findall(r'<TR[^>]*bgcolor="#EAEADF"[^>]*>([\s\S]*?)</TR>', html, flags=re.I):
        cells = [clean_tracking_text(cell) for cell in re.findall(r"<TD[^>]*>[\s\S]*?<font[^>]*>([\s\S]*?)</font>", row, flags=re.I)]
        if len(cells) < 3:
            continue
        status = cells[1]
        row_date = parse_compact_date(cells[2])
        if not row_date:
            continue
        latest = f"{status} {sheet_date(row_date)}"
        if re.search(r"DELIVERED", status, re.I):
            actual = row_date
        elif not actual and re.search(r"OUT FOR DELVRY|OUT FOR DELIVERY", status, re.I):
            eta = row_date
    return {"eta": eta, "actual": actual, "note": f"Numark: {latest}" if latest else "Numark: tracking found"}


def get_rl_tracking(pro: str) -> dict:
    html = fetch_text(tracking_url("R&L", pro))
    text = clean_tracking_text(html)
    status_match = re.search(r'st-shipment__hd-status[^>]*>([\s\S]*?)</p>', html, re.I)
    status = clean_tracking_text(status_match.group(1)) if status_match else ""
    eta_match = re.search(r"estimated due date of\s+(\d{1,2}/\d{1,2}/\d{4})", text, re.I)
    eta = parse_us_date(eta_match.group(1)) if eta_match else None
    actual = None
    delivered_match = re.search(r"delivered(?:\s+on)?\s+(\d{1,2}/\d{1,2}/\d{4})", text, re.I)
    if re.search(r"delivered", status, re.I) and delivered_match:
        actual = parse_us_date(delivered_match.group(1))
    note_date = actual or eta
    note_status = status or ("Delivered" if actual else "In Transit" if eta else "tracking found")
    return {"eta": eta, "actual": actual, "note": f"R&L: {note_status} {sheet_date(note_date)}".strip()}


def get_dti_tracking(load_number: str) -> dict:
    url = f"https://my.dtitrans.com/api/home/tracking?type=0&trackingnumbers={quote(load_number)}"
    data = json.loads(fetch_text(url))
    if not data:
        return {"eta": None, "actual": None, "note": "DTI: tracking not found"}
    shipment = data[0]
    eta = parse_us_date(str(shipment.get("deliveryDate") or ""))
    actual = parse_us_date(str(shipment.get("deliverStatusDate") or shipment.get("deliveryDate") or "")) if shipment.get("isDelivered") else None
    status = str(shipment.get("status") or "tracking found")
    note_date = actual or eta
    return {"eta": eta, "actual": actual, "note": f"DTI: {status} {sheet_date(note_date)}".strip()}


def browser_tracking_updates(rows: list[dict]) -> dict[int, dict]:
    if not rows:
        return {}
    from playwright.sync_api import sync_playwright

    headless = (os.getenv("TRACKING_BROWSER_HEADLESS") or "false").strip().lower() in {"1", "true", "yes"}
    updates: dict[int, dict] = {}
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=headless)
        page = browser.new_page()

        def visible_text() -> str:
            try:
                return page.locator("body").inner_text(timeout=10000)
            except Exception:
                frame_texts = []
                for frame in page.frames:
                    try:
                        frame_texts.append(frame.locator("body").inner_text(timeout=3000))
                    except Exception:
                        pass
                return "\n".join(frame_texts)

        def show_manual_captcha_notice(carrier_name: str, wait_seconds: int):
            try:
                page.bring_to_front()
                page.evaluate(
                    """({ carrierName, waitSeconds }) => {
                        const existing = document.getElementById("freight-agent-captcha-notice");
                        if (existing) existing.remove();
                        const notice = document.createElement("div");
                        notice.id = "freight-agent-captcha-notice";
                        notice.textContent = `${carrierName} needs you to click the CAPTCHA checkbox. Waiting up to ${Math.round(waitSeconds / 60)} minutes, then tracking will continue automatically.`;
                        Object.assign(notice.style, {
                            position: "fixed",
                            top: "18px",
                            left: "50%",
                            transform: "translateX(-50%)",
                            zIndex: "2147483647",
                            background: "#fff200",
                            color: "#111",
                            border: "3px solid #111",
                            borderRadius: "8px",
                            boxShadow: "0 10px 30px rgba(0,0,0,0.3)",
                            fontFamily: "Arial, sans-serif",
                            fontSize: "18px",
                            fontWeight: "700",
                            lineHeight: "1.35",
                            maxWidth: "760px",
                            padding: "16px 20px",
                            textAlign: "center",
                        });
                        document.body.appendChild(notice);
                    }""",
                    {"carrierName": carrier_name, "waitSeconds": wait_seconds},
                )
            except Exception:
                pass

        for row in rows:
            carrier = normalize_carrier(row["carrier"])
            tracking = row["tracking"]
            try:
                if carrier == "USPS":
                    try:
                        page.close()
                    except Exception:
                        pass
                    page = browser.new_page()
                page.goto(tracking_url(carrier, tracking), wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(6000)
                text = visible_text()
                if carrier == "UPS":
                    if "Estimated delivery" not in text and page.locator("input").count() > 0:
                        try:
                            page.locator("input").first.fill(tracking, timeout=5000)
                            page.keyboard.press("Enter")
                            page.wait_for_timeout(8000)
                            text = page.locator("body").inner_text(timeout=10000)
                        except Exception:
                            pass
                    piece_count = parse_ups_shipment_piece_count(text)
                    package_count = len(parse_ups_package_blocks(text))
                    if piece_count and piece_count > 1 and package_count < piece_count:
                        for label in (
                            r"All Packages in this Shipment",
                            r"Other Packages in this Shipment",
                            r"\d+\s+of\s+\d+\s+Piece\s+Shipment",
                            r"Shipment Details",
                        ):
                            try:
                                page.get_by_text(re.compile(label, re.I)).first.click(timeout=10000)
                                page.wait_for_timeout(3000)
                                text = visible_text()
                                if len(parse_ups_package_blocks(text)) >= piece_count:
                                    break
                            except Exception:
                                continue

                    updates[row["row_number"]] = summarize_ups_tracking(text, tracking)
                elif carrier == "USPS":
                    if tracking not in text or not re.search(r"\b(Delivered|Out for Delivery|Expected Delivery|In Transit)\b", text, re.I):
                        try:
                            field = page.locator(
                                "input[name='tLabels'], input[id*='tracking' i], input[placeholder*='Tracking' i], input[type='text']"
                            ).first
                            field.fill(tracking, timeout=10000)
                            try:
                                page.locator("button, input[type='submit'], [role='button']").filter(
                                    has_text=re.compile(r"track|search", re.I)
                                ).first.click(timeout=10000)
                            except Exception:
                                field.press("Enter", timeout=5000)
                            page.wait_for_timeout(8000)
                            text = visible_text()
                        except Exception:
                            pass

                    try:
                        page.wait_for_function(
                            """() => /\b(Delivered|Out for Delivery|Expected Delivery|In Transit|Arrived at Post Office)\b/i.test(document.body.innerText || "")""",
                            timeout=20000,
                        )
                    except Exception:
                        page.wait_for_timeout(5000)

                    text = visible_text()
                    if tracking not in text:
                        updates[row["row_number"]] = {
                            "eta": None,
                            "actual": None,
                            "delivered": False,
                            "note": "USPS: manual check required",
                        }
                        continue
                    eta, actual, status = parse_usps_tracking_text(text)
                    if not eta and not actual and re.fullmatch(r"tracking", status or "", re.I):
                        updates[row["row_number"]] = {
                            "eta": None,
                            "actual": None,
                            "delivered": False,
                            "note": "USPS: manual check required",
                        }
                        continue
                    updates[row["row_number"]] = {
                        "eta": eta,
                        "actual": actual,
                        "delivered": bool(actual),
                        "note": f"USPS: {status} {sheet_date(actual or eta)}".strip(),
                    }
                elif carrier == "GLOVALINK":
                    if tracking not in text or re.search(r"\bQuickTrack\b", text, re.I):
                        try:
                            field = page.locator(
                                "input[name*='pro' i], input[id*='pro' i], input[placeholder*='pro' i], input[type='text'], input:not([type])"
                            ).first
                            field.fill(tracking, timeout=10000)
                            try:
                                page.locator("button, input[type='submit'], [role='button']").filter(
                                    has_text=re.compile(r"search", re.I)
                                ).first.click(timeout=10000)
                            except Exception:
                                field.press("Enter", timeout=5000)
                            page.wait_for_timeout(8000)
                            text = visible_text()
                        except Exception:
                            pass

                    try:
                        page.wait_for_function(
                            """(tracking) => {
                                const text = document.body.innerText || "";
                                return text.includes(tracking)
                                    || /Cannot Find Order|Delivered|In Transit|Estimated|Scheduled|Out for Delivery|Status/i.test(text);
                            }""",
                            arg=tracking,
                            timeout=20000,
                        )
                    except Exception:
                        page.wait_for_timeout(5000)

                    text = visible_text()
                    eta, actual, status = parse_glovalink_tracking_text(text)
                    if not eta and not actual and re.fullmatch(r"tracking", status or "", re.I):
                        updates[row["row_number"]] = {
                            "eta": None,
                            "actual": None,
                            "delivered": False,
                            "note": "GlovaLink: manual check required",
                        }
                        continue
                    updates[row["row_number"]] = {
                        "eta": eta,
                        "actual": actual,
                        "delivered": bool(actual),
                        "note": f"GlovaLink: {status} {sheet_date(actual or eta)}".strip(),
                    }
                elif carrier == "ESTES":
                    try:
                        criteria = page.locator(
                            "#criteria, textarea[name='criteria'], textarea[aria-label*='tracking' i], textarea"
                        ).first
                        criteria.scroll_into_view_if_needed(timeout=5000)
                        if not (criteria.input_value(timeout=5000) or "").strip():
                            page.goto(tracking_url(carrier, tracking), wait_until="domcontentloaded", timeout=60000)
                            page.wait_for_timeout(3000)
                    except Exception:
                        pass

                    clicked_search = False
                    for selector in (
                        "#shipmentTrackingSubmitButton",
                        "button:has-text('SEARCH')",
                        "input[type='submit']",
                    ):
                        button = None
                        try:
                            button = page.locator(selector).first
                            if button.count() > 0:
                                button.scroll_into_view_if_needed(timeout=5000)
                                button.click(timeout=5000)
                                clicked_search = True
                                break
                        except Exception:
                            if button is not None:
                                try:
                                    button.click(timeout=5000, force=True)
                                    clicked_search = True
                                    break
                                except Exception:
                                    pass

                    if not clicked_search:
                        try:
                            button = page.locator("#shipmentTrackingSubmitButton").first
                            box = button.bounding_box(timeout=5000)
                            if box:
                                page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                                clicked_search = True
                        except Exception:
                            pass

                    if not clicked_search:
                        try:
                            clicked_search = bool(
                                page.evaluate(
                                    """() => {
                                        const elements = Array.from(document.querySelectorAll("button,input[type='submit'],a"));
                                        const target = elements.find((el) => /search/i.test(
                                            el.innerText || el.value || el.getAttribute("aria-label") || ""
                                        ));
                                        if (!target) return false;
                                        target.click();
                                        return true;
                                    }"""
                                )
                            )
                        except Exception:
                            pass

                    if not clicked_search:
                        try:
                            page.keyboard.press("Enter")
                        except Exception:
                            pass

                    try:
                        page.wait_for_function(
                            """() => /Tracking Results/i.test(document.body.innerText || "")
                                && /\b(In Transit|Delivered|Delivery Attempted|Picked Up|Out for Delivery)\b/i.test(document.body.innerText || "")""",
                            timeout=15000,
                        )
                    except Exception:
                        page.wait_for_timeout(8000)
                    text = page.locator("body").inner_text(timeout=10000)
                    eta, actual, status = parse_estes_tracking_text(text)
                    if not actual and (not eta or re.fullmatch(r"Delivered", status or "", re.I)):
                        expanded = False
                        for selector in (
                            "button:has-text('Expand All')",
                            "mat-expansion-panel-header",
                            "[role='button']:has-text('Delivered')",
                        ):
                            try:
                                target = page.locator(selector).last
                                if target.count() > 0:
                                    target.scroll_into_view_if_needed(timeout=5000)
                                    target.click(timeout=5000)
                                    page.wait_for_timeout(2500)
                                    expanded = True
                                    break
                            except Exception:
                                continue
                        if not expanded:
                            try:
                                page.get_by_text(re.compile(r"Delivered", re.I)).last.click(timeout=5000)
                                page.wait_for_timeout(2500)
                            except Exception:
                                pass
                        text = page.locator("body").inner_text(timeout=10000)
                        eta, actual, status = parse_estes_tracking_text(text)
                    updates[row["row_number"]] = {
                        "eta": eta,
                        "actual": actual,
                        "delivered": bool(actual),
                        "note": f"Estes: {status} {sheet_date(actual or eta)}".strip(),
                    }
                elif carrier in {"TFORCE", "TFWW"}:
                    if carrier == "TFWW" and (
                        tracking not in text
                        or not re.search(r"\b(Delivered|In Transit|Estimated Delivery|Out for Delivery|Pickup Information)\b", text, re.I)
                    ):
                        try:
                            search_type = page.locator("select").first
                            if search_type.count() > 0:
                                search_type.evaluate(
                                    """(sel) => {
                                        const options = Array.from(sel.options || []);
                                        const match = options.find((opt) => /load\\s*number/i.test(opt.textContent || opt.value || ""));
                                        if (!match) return;
                                        sel.value = match.value;
                                        match.selected = true;
                                        sel.dispatchEvent(new Event("input", { bubbles: true }));
                                        sel.dispatchEvent(new Event("change", { bubbles: true }));
                                    }"""
                                )
                        except Exception:
                            pass
                        try:
                            field = page.locator("textarea, input[type='text'], input:not([type])").first
                            field.fill(tracking, timeout=10000)
                            page.locator("button:has-text('Track'), input[value='Track']").first.click(timeout=10000)
                            page.wait_for_timeout(6000)
                            text = visible_text()
                        except Exception:
                            pass

                    try:
                        page.wait_for_function(
                            """() => /PRO\\(S\\) RELATED TO|Pickup Information|Shipment Detail|\\b(In Transit|Delivered|Estimated Delivery|Out for Delivery)\\b/i.test(document.body.innerText || "")""",
                            timeout=25000,
                        )
                    except Exception:
                        page.wait_for_timeout(8000)

                    text = visible_text()
                    eta, actual, status = parse_tforce_tracking_text(text)
                    carrier_label = "TFWW" if carrier == "TFWW" else "TForce"
                    updates[row["row_number"]] = {
                        "eta": eta,
                        "actual": actual,
                        "delivered": bool(actual),
                        "note": f"{carrier_label}: {status} {sheet_date(actual or eta)}".strip(),
                    }
                elif carrier == "XPO":
                    text = page.locator("body").inner_text(timeout=10000)
                    if re.search(r"captcha|not a robot", text, re.I):
                        wait_seconds = 180
                        if headless:
                            updates[row["row_number"]] = {
                                "eta": None,
                                "actual": None,
                                "note": "XPO: CAPTCHA requires visible browser",
                            }
                            continue
                        show_manual_captcha_notice("XPO", wait_seconds)
                        try:
                            page.wait_for_function(
                                """() => !/captcha|not a robot/i.test(document.body.innerText || "")
                                    || /\b(In Transit|Delivered|Est\\. Delivery|Estimated Delivery)\b/i.test(document.body.innerText || "")""",
                                timeout=wait_seconds * 1000,
                            )
                        except Exception:
                            pass
                        text = page.locator("body").inner_text(timeout=10000)
                        if re.search(r"captcha|not a robot", text, re.I) and not re.search(
                            r"\b(In Transit|Delivered|Est\. Delivery|Estimated Delivery)\b",
                            text,
                            re.I,
                        ):
                            updates[row["row_number"]] = {
                                "eta": None,
                                "actual": None,
                                "note": "XPO: CAPTCHA requires manual check",
                            }
                            continue

                    text = page.locator("body").inner_text(timeout=10000)
                    if not re.search(r"\b(In Transit|Delivered|Est\. Delivery|Estimated Delivery)\b", text, re.I):
                        try:
                            field = page.locator("input[placeholder*='Tracking' i], input, textarea").first
                            field.fill(tracking, timeout=10000)
                            page.locator("button, [role='button']").filter(has_text=re.compile(r"search|track", re.I)).first.click(timeout=10000)
                            page.wait_for_timeout(10000)
                            text = page.locator("body").inner_text(timeout=10000)
                        except Exception:
                            pass

                    eta, actual, status = parse_xpo_tracking_text(text)
                    if not eta and not actual and re.fullmatch(r"tracking", status or "", re.I):
                        updates[row["row_number"]] = {
                            "eta": None,
                            "actual": None,
                            "delivered": False,
                            "note": "XPO: manual check required",
                        }
                        continue
                    updates[row["row_number"]] = {
                        "eta": eta,
                        "actual": actual,
                        "delivered": bool(actual),
                        "note": f"XPO: {status} {sheet_date(actual or eta)}".strip(),
                    }
                elif carrier == "TOTAL":
                    if not re.search(r"\bShipment Number:\b|\bEstimated Delivery Date:\b", text, re.I):
                        total_frame = page.frame(name="mainf") or page
                        quick_track = total_frame.locator("input[placeholder*='Quick Track' i]").first
                        if quick_track.count() == 0:
                            quick_track = total_frame.locator("input[name*='prolist' i]").first
                        quick_track.fill(tracking, timeout=10000)
                        try:
                            total_frame.locator("button:has-text('Track'), input[value='Track']").last.click(timeout=10000)
                        except Exception:
                            quick_track.press("Enter", timeout=5000)
                        try:
                            page.wait_for_function(
                                """() => /\bShipment Number:\b|\bEstimated Delivery Date:\b|\bStatus:\b/i.test(document.body.innerText || "")""",
                                timeout=15000,
                            )
                        except Exception:
                            page.wait_for_timeout(5000)

                    text = visible_text()
                    eta, actual, status = parse_total_tracking_text(text)
                    updates[row["row_number"]] = {
                        "eta": eta,
                        "actual": actual,
                        "note": f"TOTAL: {status} {sheet_date(actual or eta)}".strip(),
                    }
                elif carrier == "ABF":
                    if re.search(r"Fetching Data", text, re.I):
                        try:
                            page.wait_for_function(
                                """() => {
                                    const text = document.body.innerText || "";
                                    return !/Fetching Data/i.test(text)
                                        || /CURRENT STATUS|STATUS DETAIL|Estimated|Scheduled|Delivered on/i.test(text);
                                }""",
                                timeout=30000,
                            )
                        except Exception:
                            pass
                        text = visible_text()

                    if re.search(r"technical difficulties", text, re.I):
                        updates[row["row_number"]] = {
                            "eta": None,
                            "actual": None,
                            "delivered": False,
                            "note": "ABF: tracking requires visible browser",
                        }
                        continue

                    if tracking not in text or not re.search(r"\b(Delivered|In Transit|Estimated|Scheduled|Status)\b", text, re.I):
                        if page.locator(
                            "input[aria-label*='Tracking Number' i], input[placeholder*='Tracking' i], input[type='text']"
                        ).count() == 0:
                            try:
                                page.reload(wait_until="domcontentloaded", timeout=60000)
                                page.wait_for_function(
                                    """() => /CURRENT STATUS|STATUS DETAIL|Estimated|Scheduled|Delivered on/i.test(document.body.innerText || "")""",
                                    timeout=30000,
                                )
                            except Exception:
                                pass
                            text = visible_text()
                            if tracking not in text or not re.search(r"\b(Delivered|In Transit|Estimated|Scheduled|Status)\b", text, re.I):
                                updates[row["row_number"]] = {
                                    "eta": None,
                                    "actual": None,
                                    "delivered": False,
                                    "note": "ABF: Timeout waiting for tracking",
                                }
                                continue
                        try:
                            page.get_by_role("button", name=re.compile(r"Dismiss alert", re.I)).first.click(timeout=3000)
                        except Exception:
                            pass
                        field = page.locator(
                            "input[aria-label*='Tracking Number' i], input[placeholder*='Tracking' i], input[type='text']"
                        ).first
                        field.fill(tracking, timeout=10000)
                        page.get_by_role("button", name=re.compile(r"Track Shipment", re.I)).first.click(timeout=10000)
                        try:
                            page.wait_for_function(
                                """(tracking) => {
                                    const text = document.body.innerText || "";
                                    return text.includes(tracking)
                                        && /Delivered|In Transit|Estimated|Scheduled|Status|Shipment/i.test(text);
                                }""",
                                arg=tracking,
                                timeout=25000,
                            )
                        except Exception:
                            page.wait_for_timeout(8000)

                    text = visible_text()
                    eta, actual, status = parse_abf_tracking_text(text)
                    updates[row["row_number"]] = {
                        "eta": eta,
                        "actual": actual,
                        "delivered": bool(actual),
                        "note": f"ABF: {status} {sheet_date(actual or eta)}".strip(),
                    }
                elif carrier == "ROADRUNNER":
                    if tracking not in text or not re.search(r"\bShipment\s+Status\b|\bEstimated\s+Delivery\b", text, re.I):
                        try:
                            field = page.locator("textarea:visible").first
                            if field.count() == 0:
                                field = page.locator(
                                    "input[placeholder*='tracking' i]:visible, input[placeholder*='pro' i]:visible, input[type='text']:visible"
                                ).first
                            field.fill(tracking, timeout=10000)
                            try:
                                page.locator(
                                    "input[type='submit'][value='Track']:visible, button:has-text('Track'):visible"
                                ).first.click(timeout=10000)
                            except Exception:
                                field.press("Enter", timeout=5000)
                            try:
                                page.wait_for_url(re.compile(r"LTLTrack|searchValues", re.I), timeout=15000)
                            except Exception:
                                pass
                            try:
                                page.wait_for_function(
                                    """(tracking) => {
                                        const text = document.body.innerText || "";
                                        return text.includes(tracking)
                                            && /Shipment Status|Estimated Delivery|Delivered|In Transit/i.test(text);
                                    }""",
                                    arg=tracking,
                                    timeout=20000,
                                )
                            except Exception:
                                page.wait_for_timeout(8000)
                            text = visible_text()
                        except Exception:
                            try:
                                page.goto(
                                    f"https://tools.rrts.com/LTLTrack/?searchValues={quote(tracking)}",
                                    wait_until="domcontentloaded",
                                    timeout=60000,
                                )
                                page.wait_for_timeout(8000)
                                text = visible_text()
                            except Exception:
                                pass

                    if tracking not in text or not re.search(r"\bShipment\s+Status\b|\bEstimated\s+Delivery\b", text, re.I):
                        try:
                            page.goto(
                                f"https://tools.rrts.com/LTLTrack/?searchValues={quote(tracking)}",
                                wait_until="domcontentloaded",
                                timeout=60000,
                            )
                            try:
                                page.wait_for_function(
                                    """(tracking) => {
                                        const text = document.body.innerText || "";
                                        return text.includes(tracking)
                                            && /Shipment Status|Estimated Delivery|Delivered|In Transit/i.test(text);
                                    }""",
                                    arg=tracking,
                                    timeout=20000,
                                )
                            except Exception:
                                page.wait_for_timeout(8000)
                            text = visible_text()
                        except Exception:
                            pass

                    eta, actual, status = parse_roadrunner_tracking_text(text)
                    if not eta and not actual and re.fullmatch(r"tracking", status or "", re.I):
                        updates[row["row_number"]] = {
                            "eta": None,
                            "actual": None,
                            "delivered": False,
                            "note": "Roadrunner: manual check required",
                        }
                        continue
                    updates[row["row_number"]] = {
                        "eta": eta,
                        "actual": actual,
                        "delivered": bool(actual),
                        "note": f"Roadrunner: {status} {sheet_date(actual or eta)}".strip(),
                    }
            except Exception as exc:
                updates[row["row_number"]] = {"eta": None, "actual": None, "note": f"{carrier}: {exc}"}
        browser.close()
    return updates


class PalletItem(BaseModel):
    pallet_number: int
    pieces: int = Field(default=1)
    length_in: float
    width_in: float
    height_in: float
    weight_lb: float
    freight_class: Optional[str] = Field(default="")


class InputPayload(BaseModel):
    company_name: Optional[str] = Field(default="")
    order_number: Optional[str] = Field(default="")
    quote_target: Optional[str] = Field(default="ALL")
    broker_targets: Optional[list[str]] = Field(default=None)
    direct_carrier_targets: Optional[list[str]] = Field(default=None)
    browser_visibility: Optional[str] = Field(default="CONCEAL")
    origin_zip: str
    destination_zip: str
    pallet_count: int
    length_in: float
    width_in: float
    height_in: float
    weight_lb: float
    pieces: int
    freight_class: Optional[str] = Field(default="")
    pickup_city: str
    delivery_city: Optional[str] = Field(default="")
    shipment_date: Optional[str] = Field(default="")
    pallet_items: list[PalletItem] = Field(default_factory=list)


class AssistantMessage(BaseModel):
    role: str
    text: str


class AssistantContext(BaseModel):
    form: Optional[dict] = None
    latest_results: list[dict] = Field(default_factory=list)
    status_text: Optional[str] = ""


def assistant_client():
    api_key = (os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_AI_API_KEY") or "").strip()
    if not api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is missing from .env")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise HTTPException(
            status_code=500,
            detail="The openai package is not installed. Add it to the environment before using the assistant.",
        ) from exc
    return OpenAI(api_key=api_key)


def safe_json_load(text: Optional[str], fallback):
    if not text:
        return fallback
    try:
        return json.loads(text)
    except Exception:
        return fallback


def compact_assistant_context(raw_context: dict) -> dict:
    context = raw_context if isinstance(raw_context, dict) else {}
    form = context.get("form") if isinstance(context.get("form"), dict) else {}
    results = context.get("latest_results") if isinstance(context.get("latest_results"), list) else []
    compact_results = []
    for item in results[:8]:
        if not isinstance(item, dict):
            continue
        compact_results.append(
            {
                "carrier": item.get("carrier"),
                "price": item.get("price"),
                "transit_days": item.get("transit_days"),
                "status": item.get("status"),
                "error": item.get("error"),
                "broker_quote_count": item.get("broker_quote_count"),
            }
        )
    return {
        "status_text": str(context.get("status_text") or "").strip(),
        "form": form,
        "latest_results": compact_results,
    }


def build_assistant_input(history: list[dict], user_message: str, context: dict, screenshot_data_url: str = ""):
    input_messages = []
    for item in history[-8:]:
        role = str(item.get("role") or "").strip().lower()
        text = str(item.get("text") or "").strip()
        if role not in {"user", "assistant"} or not text:
            continue
        input_messages.append({"role": role, "content": text})

    context_json = json.dumps(context, ensure_ascii=True)
    context_text = f"Page context:\n{context_json}"
    if user_message.strip():
        context_text = f"{context_text}\n\nUser request:\n{user_message.strip()}"

    if screenshot_data_url:
        user_parts = [{"type": "input_text", "text": context_text}]
        user_parts.append(
            {
                "type": "input_image",
                "image_url": screenshot_data_url,
            }
        )
        input_messages.append({"role": "user", "content": user_parts})
    else:
        input_messages.append({"role": "user", "content": context_text})
    return input_messages


def density_to_freight_class(density: float) -> str:
    if density >= 50:
        return "50"
    if density >= 35:
        return "55"
    if density >= 30:
        return "60"
    if density >= 22.5:
        return "65"
    if density >= 15:
        return "70"
    if density >= 13.5:
        return "77.5"
    if density >= 12:
        return "85"
    if density >= 10.5:
        return "92.5"
    if density >= 9:
        return "100"
    if density >= 8:
        return "110"
    if density >= 7:
        return "125"
    if density >= 6:
        return "150"
    if density >= 5:
        return "175"
    if density >= 4:
        return "200"
    if density >= 3:
        return "250"
    if density >= 2:
        return "300"
    if density >= 1:
        return "400"
    return "500"


def format_numeric_value(value) -> str:
    if value is None or value == "":
        return ""
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value.is_integer():
            return str(int(value))
        text = f"{value:.4f}".rstrip("0").rstrip(".")
        return text
    return str(value).strip()


def estimated_freight_class(payload: InputPayload) -> str:
    volume = aggregate_volume(payload)
    if volume <= 0:
        return str(payload.freight_class or "").strip()
    density = aggregate_weight(payload) / volume
    return density_to_freight_class(density)


def normalized_pallet_items(payload: InputPayload):
    items = []
    for idx, item in enumerate(payload.pallet_items or [], start=1):
        volume = (item.length_in * item.width_in * item.height_in) / 1728 if item.length_in and item.width_in and item.height_in else 0
        density = (item.weight_lb / volume) if volume else 0
        pallet_class = str(item.freight_class or "").strip() or (density_to_freight_class(density) if density else "")
        items.append(
            {
                "pallet_number": item.pallet_number or idx,
                "pieces": int(item.pieces or 1),
                "length": format_numeric_value(item.length_in),
                "width": format_numeric_value(item.width_in),
                "height": format_numeric_value(item.height_in),
                "weight": format_numeric_value(item.weight_lb),
                "freight_class": pallet_class,
            }
        )
    return items


def aggregate_weight(payload: InputPayload) -> float:
    items = payload.pallet_items or []
    if items:
        return float(sum(item.weight_lb for item in items))
    return float(payload.weight_lb or 0)


def aggregate_pieces(payload: InputPayload) -> int:
    items = payload.pallet_items or []
    if items:
        return int(sum(int(item.pieces or 0) for item in items))
    return int(payload.pieces or 0)


def aggregate_volume(payload: InputPayload) -> float:
    items = payload.pallet_items or []
    if items:
        return sum((item.length_in * item.width_in * item.height_in) / 1728 for item in items)
    return (payload.length_in * payload.width_in * payload.height_in) / 1728


def first_pallet_dimensions(payload: InputPayload):
    items = payload.pallet_items or []
    if items:
        first = items[0]
        return (
            format_numeric_value(first.length_in),
            format_numeric_value(first.width_in),
            format_numeric_value(first.height_in),
        )
    return (
        format_numeric_value(payload.length_in),
        format_numeric_value(payload.width_in),
        format_numeric_value(payload.height_in),
    )


def payload_to_sheet_values(payload: InputPayload):
    freight_class = estimated_freight_class(payload)
    length, width, height = first_pallet_dimensions(payload)
    return {
        "order_number": payload.order_number or "",
        "origin_zip": format_numeric_value(payload.origin_zip),
        "destination_zip": format_numeric_value(payload.destination_zip),
        "pallet_count": format_numeric_value(payload.pallet_count),
        "length_in": length,
        "width_in": width,
        "height_in": height,
        "weight_lb": format_numeric_value(aggregate_weight(payload)),
        "pieces": format_numeric_value(aggregate_pieces(payload)),
        "freight_class": freight_class,
        "pickup_city": payload.pickup_city,
        "delivery_city": payload.delivery_city,
        "shipment_date": payload.shipment_date or today_form_date(),
    }


def write_input_values(ws, payload: InputPayload):
    data = payload_to_sheet_values(payload)
    updates = [
        {"range": cell, "values": [[str(data.get(key, ""))]]}
        for key, cell in CELL_MAP.items()
    ]
    with_gsheets_retry(lambda: ws.batch_update(updates, value_input_option="USER_ENTERED"))


def clear_input_values(ws):
    defaults = default_form_values()
    updates = [
        {"range": cell, "values": [[str(defaults.get(key, ""))]]}
        for key, cell in CELL_MAP.items()
    ]
    with_gsheets_retry(lambda: ws.batch_update(updates, value_input_option="USER_ENTERED"))


def payload_to_quote_data(payload: InputPayload):
    freight_class = estimated_freight_class(payload)
    length, width, height = first_pallet_dimensions(payload)
    return {
        "origin_zip": format_numeric_value(payload.origin_zip),
        "dest_zip": format_numeric_value(payload.destination_zip),
        "pallets": format_numeric_value(payload.pallet_count),
        "length": length,
        "width": width,
        "height": height,
        "weight": format_numeric_value(aggregate_weight(payload)),
        "pieces": format_numeric_value(aggregate_pieces(payload)),
        "freight_class": freight_class,
        "pickup_city": str(payload.pickup_city).strip(),
        "delivery_city": str(payload.delivery_city).strip(),
        "shipment_date": str(payload.shipment_date or today_form_date()).strip(),
        "pallet_items": normalized_pallet_items(payload),
    }


def result_with_inputs(result: dict, quote_data: dict):
    return {
        **result,
        "inputs": {
            "origin_zip": quote_data["origin_zip"],
            "origin_city": quote_data["pickup_city"],
            "destination_zip": quote_data["dest_zip"],
            "destination_city": quote_data["delivery_city"],
            "freight_class": quote_data["freight_class"],
            "weight": quote_data["weight"],
            "length": quote_data["length"],
            "width": quote_data["width"],
            "height": quote_data["height"],
            "pieces": quote_data["pieces"],
            "pallets": quote_data["pallets"],
            "shipment_date": quote_data["shipment_date"],
            "pallet_items": quote_data.get("pallet_items", []),
        },
        "debug": {
            "cn_total": result.get("debug_total_cn", ""),
            "us_total": result.get("debug_total_us", ""),
            "raw_money": result.get("raw_money", []),
        },
    }


def should_skip_carrier_error(message: str) -> bool:
    text = (message or "").strip()
    if not text:
        return False
    patterns = [
        r"serviceable area",
        r"unable to process your request online",
        r"unable to process your request",
        r"doesn[']?t ship",
        r"does not ship",
        r"destination zip",
        r"not ship to",
        r"not available for this destination",
        r"could not detect .*results",
        r"http error 500",
        r"net::err_aborted",
        r"server problem",
        r"please try again later",
        r"contact customer service",
        r"not serviceable",
        r"out of the serviceable area",
    ]
    return any(re.search(pattern, text, re.I) for pattern in patterns)


def skipped_result(carrier_name: str, quote_data: dict, exc: Exception):
    message = str(exc).strip() or "Skipped"
    wrapped = result_with_inputs(
        {
            "carrier": carrier_name,
            "price": None,
            "transit_days": None,
            "raw_money": [],
            "status": "skipped",
            "error": message,
        },
        quote_data,
    )
    return wrapped


def should_retry_skipped_result(result: dict) -> bool:
    if result.get("status") != "skipped":
        return False
    return should_skip_carrier_error(result.get("error", ""))


def mark_retry_exhausted(result: dict):
    error = str(result.get("error") or "Skipped").strip()
    retry_note = "Retried once after the full carrier pass."
    if retry_note not in error:
        result["error"] = f"{error} {retry_note}".strip()
    result["retry_attempted"] = True
    return result


def run_one_carrier(ss, carrier_name: str, fn, quote_data: dict):
    try:
        result = fn(quote_data)
        write_result(
            ss,
            carrier_name=carrier_name,
            result=result,
            origin_city=quote_data["pickup_city"],
            dest_city=quote_data["delivery_city"],
        )
        return result_with_inputs(result, quote_data)
    except Exception as exc:
        if should_skip_carrier_error(str(exc)):
            return skipped_result(carrier_name, quote_data, exc)
        raise


def upsert_result(result_map: dict[str, dict], result: dict):
    carrier_name = str(result.get("carrier") or "").strip()
    if not carrier_name:
        carrier_name = f"result-{len(result_map) + 1}"
    result_map[carrier_name] = result


def ordered_results(result_map: dict[str, dict]) -> list[dict]:
    display_rank = {name: index for index, name in enumerate(RESULT_DISPLAY_ORDER)}
    return sorted(
        result_map.values(),
        key=lambda item: (
            display_rank.get(str(item.get("carrier") or "").strip(), len(display_rank)),
            str(item.get("carrier") or "").strip(),
        ),
    )


def write_direct_result(ss, carrier_name: str, result: dict, quote_data: dict):
    write_result(
        ss,
        carrier_name=carrier_name,
        result=result,
        origin_city=quote_data["pickup_city"],
        dest_city=quote_data["delivery_city"],
    )


def run_glt_job(quote_data: dict, *, headless: Optional[bool] = None, slow_mo: Optional[int] = None):
    try:
        raw_glt_results = quote_glt(
            quote_data,
            headless=WEBAPP_GLT_HEADLESS if headless is None else headless,
            slow_mo=WEBAPP_GLT_SLOW_MO if slow_mo is None else slow_mo,
        )
        wrapped_glt = wrap_glt_results(raw_glt_results, quote_data)
        return {
            "carrier": "GLT",
            "wrapped": wrapped_glt,
            "covered_direct_carriers": glt_covered_direct_carriers(raw_glt_results),
        }
    except Exception as exc:
        print(f"GLT quote failed: {exc}", flush=True)
        return {
            "carrier": "GLT",
            "wrapped": skipped_result("GLT", quote_data, exc),
            "covered_direct_carriers": set(),
        }


def run_schneider_job(quote_data: dict, *, headless: bool = True, slow_mo: int = 0):
    try:
        raw_schneider_results = quote_schneider(quote_data, headless=headless, slow_mo=slow_mo)
        wrapped_schneider = wrap_schneider_results(raw_schneider_results, quote_data)
        return {
            "carrier": "SCHNEIDER",
            "wrapped": wrapped_schneider,
            "covered_direct_carriers": set(),
        }
    except Exception as exc:
        print(f"SCHNEIDER quote failed: {exc}", flush=True)
        return {
            "carrier": "SCHNEIDER",
            "wrapped": skipped_result("SCHNEIDER", quote_data, exc),
            "covered_direct_carriers": set(),
        }


async def run_schneider_job_async(quote_data: dict, *, headless: bool = True, slow_mo: int = 0):
    try:
        raw_schneider_results = await quote_schneider_async(quote_data, headless=headless, slow_mo=slow_mo)
        wrapped_schneider = wrap_schneider_results(raw_schneider_results, quote_data)
        return {
            "carrier": "SCHNEIDER",
            "wrapped": wrapped_schneider,
            "covered_direct_carriers": set(),
        }
    except Exception as exc:
        print(f"SCHNEIDER quote failed: {exc}", flush=True)
        return {
            "carrier": "SCHNEIDER",
            "wrapped": skipped_result("SCHNEIDER", quote_data, exc),
            "covered_direct_carriers": set(),
        }


def run_mycarrier_job(quote_data: dict, *, headless: bool = True, slow_mo: int = 0):
    try:
        raw_mycarrier_results = quote_mycarrier(quote_data, headless=headless, slow_mo=slow_mo)
        wrapped_mycarrier = wrap_mycarrier_results(raw_mycarrier_results, quote_data)
        return {
            "carrier": "MYCARRIER",
            "wrapped": wrapped_mycarrier,
            "covered_direct_carriers": mycarrier_covered_direct_carriers(raw_mycarrier_results),
        }
    except Exception as exc:
        print(f"MYCARRIER quote failed: {exc}", flush=True)
        return {
            "carrier": "MYCARRIER",
            "wrapped": skipped_result("MYCARRIER", quote_data, exc),
            "covered_direct_carriers": set(),
        }


def run_priority1_job(quote_data: dict, *, headless: bool = True, slow_mo: int = 0):
    try:
        raw_priority1_results = quote_priority1(quote_data, headless=headless, slow_mo=slow_mo)
        wrapped_priority1 = wrap_priority1_results(raw_priority1_results, quote_data)
        return {
            "carrier": "PRIORITY1",
            "wrapped": wrapped_priority1,
            "covered_direct_carriers": set(),
        }
    except Exception as exc:
        print(f"PRIORITY1 quote failed: {exc}", flush=True)
        return {
            "carrier": "PRIORITY1",
            "wrapped": skipped_result("PRIORITY1", quote_data, exc),
            "covered_direct_carriers": set(),
        }


def run_direct_carrier_job(carrier_name: str, fn, quote_data: dict, *, headless: bool = True, slow_mo: int = 0):
    try:
        result = fn(quote_data, headless=headless, slow_mo=slow_mo)
        return {
            "carrier": carrier_name,
            "wrapped": result_with_inputs(result, quote_data),
            "direct_result": result,
        }
    except Exception as exc:
        print(f"{carrier_name} quote failed: {exc}", flush=True)
        return {
            "carrier": carrier_name,
            "wrapped": skipped_result(carrier_name, quote_data, exc),
            "direct_result": None,
        }


async def run_jobs_sequentially(jobs: list[tuple[str, callable]], *, should_stop=None):
    for job_name, job_fn in jobs:
        if should_stop and should_stop():
            return
        yield job_name, await job_fn()


def thread_job(fn, *args, **kwargs):
    async def job():
        return await asyncio.to_thread(fn, *args, **kwargs)

    return job


def coroutine_job(fn, *args, **kwargs):
    async def job():
        return await fn(*args, **kwargs)

    return job


def carrier_queue():
    carriers = [("TOTAL", quote_total)]
    if callable(quote_central):
        carriers.append(("CENTRAL TRANSPORTATION", quote_central))
    else:
        carriers.append(("CENTRAL TRANSPORTATION", None))
    carriers.extend(
        [
            ("NUMARK", quote_numark),
            ("TFORCE", quote_tforce),
            ("GLOVALINK", quote_glovalink),
        ]
    )
    return carriers


def available_quote_targets() -> list[dict[str, str]]:
    targets = [{"value": "ALL", "label": "All Carriers + Brokers"}]
    if WEBAPP_ENABLE_GLT:
        targets.append({"value": "GLT", "label": "GLT"})
    if not WEBAPP_GLT_ONLY_DEBUG:
        targets.extend(
            [
                {"value": "SCHNEIDER", "label": "SCHNEIDER"},
                {"value": "MYCARRIER", "label": "MYCARRIER"},
                {"value": "PRIORITY1", "label": "PRIORITY1"},
            ]
        )
        targets.extend({"value": carrier_name, "label": carrier_name} for carrier_name, _ in carrier_queue())
    return targets


def available_broker_targets() -> list[dict[str, str]]:
    targets: list[dict[str, str]] = []
    if WEBAPP_ENABLE_GLT:
        targets.append({"value": "GLT", "label": "GLT"})
    if not WEBAPP_GLT_ONLY_DEBUG:
        targets.extend(
            [
                {"value": "MYCARRIER", "label": "MyCarrier"},
                {"value": "SCHNEIDER", "label": "Schneider"},
                {"value": "PRIORITY1", "label": "Priority1"},
            ]
        )
    return targets


def available_direct_carrier_targets() -> list[dict[str, str]]:
    if WEBAPP_GLT_ONLY_DEBUG:
        return []
    labels = {
        "CENTRAL TRANSPORTATION": "Central",
        "NUMARK": "Numark",
        "TFORCE": "TForce",
        "TOTAL": "Total",
        "GLOVALINK": "GlovaLink",
    }
    return [
        {"value": carrier_name, "label": labels.get(carrier_name, carrier_name.title())}
        for carrier_name, _ in carrier_queue()
    ]


def normalized_quote_target(value: Optional[str]) -> str:
    raw = str(value or "ALL").strip().upper() or "ALL"
    allowed = {item["value"] for item in available_quote_targets()}
    if raw not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported quote target: {raw}")
    return raw


def normalized_quote_selections(payload: InputPayload) -> tuple[set[str], set[str]]:
    broker_allowed = {item["value"] for item in available_broker_targets()}
    direct_allowed = {item["value"] for item in available_direct_carrier_targets()}

    if payload.broker_targets is not None or payload.direct_carrier_targets is not None:
        brokers = {str(value or "").strip().upper() for value in (payload.broker_targets or [])}
        direct = {str(value or "").strip().upper() for value in (payload.direct_carrier_targets or [])}
        unsupported = (brokers - broker_allowed) | (direct - direct_allowed)
        if unsupported:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported quote target(s): {', '.join(sorted(unsupported))}",
            )
        if not brokers and not direct:
            raise HTTPException(status_code=400, detail="Select at least one broker or individual carrier.")
        return brokers, direct

    legacy_target = normalized_quote_target(payload.quote_target)
    if legacy_target == "ALL":
        return broker_allowed, direct_allowed
    if legacy_target in broker_allowed:
        return {legacy_target}, set()
    if legacy_target in direct_allowed:
        return set(), {legacy_target}
    raise HTTPException(status_code=400, detail=f"Unsupported quote target: {legacy_target}")


def normalized_browser_visibility(value: Optional[str]) -> str:
    raw = str(value or "CONCEAL").strip().upper() or "CONCEAL"
    allowed = {"CONCEAL", "REVEAL"}
    if raw not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported browser visibility: {raw}")
    return raw


def quote_browser_options(payload: InputPayload) -> dict[str, int | bool]:
    reveal_browser = normalized_browser_visibility(payload.browser_visibility) == "REVEAL"
    return {
        "headless": not reveal_browser,
        "slow_mo": WEBAPP_REVEAL_SLOW_MO if reveal_browser else 0,
    }


def normalize_carrier_key(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", str(name or "").upper()).strip()


def quote_price_number(value) -> Optional[float]:
    if value in (None, "", 0, 0.0):
        return None
    try:
        cleaned = re.sub(r"[^0-9.]+", "", str(value))
        if not cleaned:
            return None
        price = float(cleaned)
    except (TypeError, ValueError):
        return None
    return price if price > 0 else None


def ranked_cheapest_quotes(results: list[dict]) -> list[dict]:
    best_by_carrier: dict[str, dict] = {}
    for item in results or []:
        broker_quotes = item.get("broker_quotes") or []
        if broker_quotes:
            for broker_quote in broker_quotes:
                price = quote_price_number(broker_quote.get("price"))
                if price is None:
                    continue
                quote = {
                    "carrier": broker_quote.get("carrier", ""),
                    "price": price,
                    "transit_days": broker_quote.get("transit_days"),
                    "source": item.get("carrier", ""),
                    "inputs": item.get("inputs") or {},
                }
                carrier_key = normalize_carrier_key(quote.get("carrier"))
                existing = best_by_carrier.get(carrier_key)
                if carrier_key and (existing is None or price < float(existing.get("price"))):
                    best_by_carrier[carrier_key] = quote
            continue

        price = quote_price_number(item.get("price"))
        if price is None or item.get("status") == "skipped":
            continue
        quote = {
            **item,
            "price": price,
        }
        carrier_key = normalize_carrier_key(quote.get("carrier"))
        existing = best_by_carrier.get(carrier_key)
        if carrier_key and (existing is None or price < float(existing.get("price"))):
            best_by_carrier[carrier_key] = quote

    return sorted(best_by_carrier.values(), key=lambda item: float(item.get("price")))


def format_log_price(value) -> str:
    price = quote_price_number(value)
    if price is None:
        return ""
    return f"${price:,.2f}"


def top_cheapest_log_text(top_quotes: list[dict]) -> str:
    parts = []
    for index, quote in enumerate(top_quotes[:3], start=1):
        transit_days = quote.get("transit_days")
        transit_text = f" - {transit_days} day(s)" if transit_days not in (None, "") else ""
        source = quote.get("source")
        source_text = f" via {source}" if source and source != quote.get("carrier") else ""
        parts.append(f"#{index} {quote.get('carrier') or '-'} - {format_log_price(quote.get('price'))}{transit_text}{source_text}")
    return "\n".join(parts)


def log_dimensions_label(payload: InputPayload) -> str:
    pallet_items = normalized_pallet_items(payload)
    if len(pallet_items) > 1:
        dimensions = [
            f"{item.get('length') or '-'} x {item.get('width') or '-'} x {item.get('height') or '-'}"
            for item in pallet_items
        ]
        unique_dimensions = list(dict.fromkeys(dimensions))
        if len(unique_dimensions) == 1:
            return unique_dimensions[0]
        return "; ".join(f"#{index + 1} {value}" for index, value in enumerate(dimensions))

    length, width, height = first_pallet_dimensions(payload)
    return f"{length or '-'} x {width or '-'} x {height or '-'}"


def logs_worksheet(ss):
    try:
        ws = with_gsheets_retry(lambda: ss.worksheet(LOGS_TAB))
    except WorksheetNotFound:
        ws = with_gsheets_retry(lambda: ss.add_worksheet(title=LOGS_TAB, rows=1000, cols=len(LOGS_HEADERS)))

    first_row = with_gsheets_retry(lambda: ws.row_values(1))
    if first_row[: len(LOGS_HEADERS)] != LOGS_HEADERS:
        with_gsheets_retry(lambda: ws.update(f"A1:G1", [LOGS_HEADERS], value_input_option="USER_ENTERED"))
        try:
            ws.freeze(rows=1)
            ws.format(
                "A1:G1",
                {
                    "backgroundColor": {"red": 0.93, "green": 0.96, "blue": 0.91},
                    "horizontalAlignment": "CENTER",
                    "textFormat": {"bold": True},
                    "wrapStrategy": "WRAP",
                },
            )
        except Exception:
            pass
    return ws


def append_quote_log(ss, payload: InputPayload, results: list[dict]):
    top_quotes = ranked_cheapest_quotes(results)[:3]
    if not top_quotes:
        return

    row = [
        date.today().isoformat(),
        (payload.company_name or "").strip(),
        (payload.delivery_city or "").strip(),
        format_numeric_value(payload.pallet_count),
        f"{format_numeric_value(aggregate_weight(payload))} lb",
        log_dimensions_label(payload),
        top_cheapest_log_text(top_quotes),
    ]
    ws = logs_worksheet(ss)
    with_gsheets_retry(lambda: ws.append_row(row, value_input_option="USER_ENTERED"))


MYCARRIER_DIRECT_MAP = {
    "CENTRAL TRANSPORT": "CENTRAL TRANSPORTATION",
    "TFORCE FREIGHT": "TFORCE",
}

GLT_DIRECT_MAP = {
    "CENTRAL TRANSPORT": "CENTRAL TRANSPORTATION",
    "CENTRAL TRANSPORTATION": "CENTRAL TRANSPORTATION",
    "TFORCE FREIGHT": "TFORCE",
    "TFORCE FREIGHT LTL": "TFORCE",
}


def wrap_glt_results(raw_results: list[dict], quote_data: dict):
    cheapest = None
    priced = [item for item in raw_results if item.get("price") not in (None, "", 0, 0.0)]
    if priced:
        cheapest = min(priced, key=lambda item: float(item.get("price")))

    return result_with_inputs(
        {
            "carrier": "GLT",
            "price": cheapest.get("price") if cheapest else None,
            "transit_days": cheapest.get("transit_days") if cheapest else None,
            "service_level": cheapest.get("service_level") if cheapest else "",
            "time": cheapest.get("time") if cheapest else "",
            "broker_quotes": raw_results,
            "broker_quote_count": len(raw_results),
        },
        quote_data,
    )


def wrap_mycarrier_results(raw_results: list[dict], quote_data: dict):
    cheapest = None
    priced = [item for item in raw_results if item.get("price") not in (None, "", 0, 0.0)]
    if priced:
        cheapest = min(priced, key=lambda item: float(item.get("price")))

    return result_with_inputs(
        {
            "carrier": "MYCARRIER",
            "price": cheapest.get("price") if cheapest else None,
            "transit_days": cheapest.get("transit_days") if cheapest else None,
            "service_level": cheapest.get("service_level") if cheapest else "",
            "time": cheapest.get("time") if cheapest else "",
            "broker_quotes": raw_results,
            "broker_quote_count": len(raw_results),
        },
        quote_data,
    )


def wrap_schneider_results(raw_results: list[dict], quote_data: dict):
    cheapest = None
    priced = [item for item in raw_results if item.get("price") not in (None, "", 0, 0.0)]
    if priced:
        cheapest = min(priced, key=lambda item: float(item.get("price")))

    return result_with_inputs(
        {
            "carrier": "SCHNEIDER",
            "price": cheapest.get("price") if cheapest else None,
            "transit_days": cheapest.get("transit_days") if cheapest else None,
            "service_level": cheapest.get("service_level") if cheapest else "",
            "time": cheapest.get("time") if cheapest else "",
            "broker_quotes": raw_results,
            "broker_quote_count": len(raw_results),
        },
        quote_data,
    )


def wrap_priority1_results(raw_results: list[dict], quote_data: dict):
    cheapest = None
    priced = [item for item in raw_results if item.get("price") not in (None, "", 0, 0.0)]
    if priced:
        cheapest = min(priced, key=lambda item: float(item.get("price")))

    return result_with_inputs(
        {
            "carrier": "PRIORITY1",
            "price": cheapest.get("price") if cheapest else None,
            "transit_days": cheapest.get("transit_days") if cheapest else None,
            "service_level": cheapest.get("service_level") if cheapest else "",
            "time": cheapest.get("time") if cheapest else "",
            "broker_quotes": raw_results,
            "broker_quote_count": len(raw_results),
        },
        quote_data,
    )


def mycarrier_covered_direct_carriers(raw_results: list[dict]) -> set[str]:
    covered = set()
    for item in raw_results or []:
        direct_name = MYCARRIER_DIRECT_MAP.get(normalize_carrier_key(item.get("carrier")))
        if direct_name:
            covered.add(direct_name)
    return covered


def glt_covered_direct_carriers(raw_results: list[dict]) -> set[str]:
    covered = set()
    for item in raw_results or []:
        direct_name = GLT_DIRECT_MAP.get(normalize_carrier_key(item.get("carrier")))
        if direct_name:
            covered.add(direct_name)
    return covered


def update_profile_last_quote(company_name: str, results: list[dict], latest_profile_data: Optional[dict] = None):
    name = (company_name or "").strip()
    if not name:
        return
    profiles = load_profiles()
    record = profiles.get(name, {})
    stored_data = latest_profile_data if isinstance(latest_profile_data, dict) and latest_profile_data else (profile_data(record) or {})

    if not results:
        profiles[name] = {
            "data": stored_data,
            "last_quote_summary": profile_summary(record),
        }
        save_profiles(profiles)
        return

    top_quotes = ranked_cheapest_quotes(results)[:3]
    if not top_quotes:
        profiles[name] = {
            "data": stored_data,
            "last_quote_summary": profile_summary(record),
        }
        save_profiles(profiles)
        return

    cheapest = top_quotes[0]
    inputs = cheapest.get("inputs") or {}
    profiles[name] = {
        "data": stored_data,
        "last_quote_summary": {
            "carrier": cheapest.get("carrier", ""),
            "price": cheapest.get("price"),
            "transit_days": cheapest.get("transit_days"),
            "source": cheapest.get("source", ""),
            "top_quotes": [
                {
                    "carrier": item.get("carrier", ""),
                    "price": item.get("price"),
                    "transit_days": item.get("transit_days"),
                    "source": item.get("source", ""),
                }
                for item in top_quotes
            ],
            "quoted_at": date.today().isoformat(),
            "origin_zip": inputs.get("origin_zip", ""),
            "destination_zip": inputs.get("destination_zip", ""),
            "origin_city": inputs.get("origin_city", ""),
            "destination_city": inputs.get("destination_city", ""),
            "pallets": inputs.get("pallets", ""),
            "pieces": inputs.get("pieces", ""),
            "length": inputs.get("length", ""),
            "width": inputs.get("width", ""),
            "height": inputs.get("height", ""),
            "weight": inputs.get("weight", ""),
            "freight_class": inputs.get("freight_class", ""),
        },
    }
    save_profiles(profiles)


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html", headers=no_cache_headers())


@app.get("/Windgate.png")
def windgate_logo():
    return FileResponse(BASE_DIR / "Windgate.png", headers=no_cache_headers())


@app.get("/LOGO-blue.png")
def loader_logo():
    return FileResponse(BASE_DIR / "LOGO-blue.png", headers=no_cache_headers())


@app.get("/favicon.ico")
def favicon():
    return FileResponse(BASE_DIR / "LOGO-blue.png", headers=no_cache_headers(), media_type="image/png")


@app.post("/api/input")
def save_input(payload: InputPayload):
    ws = input_sheet()
    write_input_values(ws, payload)
    return {"ok": True, "written": {k: CELL_MAP[k] for k in CELL_MAP}}


@app.get("/api/profiles")
def get_profiles():
    return {"ok": True, "profiles": load_profiles()}


@app.get("/api/network-info")
def network_info(request: Request):
    scheme = request.url.scheme or "http"
    port = request.url.port
    if port is None:
        port = 443 if scheme == "https" else 80

    def build_url(host: str) -> str:
        default_port = 443 if scheme == "https" else 80
        port_suffix = "" if port == default_port else f":{port}"
        return f"{scheme}://{host}{port_suffix}"

    lan_urls = [build_url(ip) for ip in local_ipv4_addresses()]
    hostname = local_mdns_hostname()
    hostname_url = build_url(hostname) if hostname else ""

    return {
        "ok": True,
        "lan_urls": lan_urls,
        "hostname_url": hostname_url,
        "current_origin": str(request.base_url).rstrip("/"),
        "quote_targets": available_quote_targets(),
        "broker_targets": available_broker_targets(),
        "direct_carrier_targets": available_direct_carrier_targets(),
    }


@app.post("/api/assistant")
async def assistant_reply(
    message: str = Form(default=""),
    history: str = Form(default="[]"),
    context: str = Form(default="{}"),
    screenshot: Optional[UploadFile] = File(default=None),
):
    user_message = str(message or "").strip()
    if not user_message and screenshot is None:
        raise HTTPException(status_code=400, detail="Enter a message or attach a screenshot.")

    history_items = safe_json_load(history, [])
    context_payload = compact_assistant_context(safe_json_load(context, {}))

    screenshot_data_url = ""
    screenshot_name = ""
    if screenshot is not None:
        screenshot_bytes = await screenshot.read()
        if not screenshot_bytes:
            raise HTTPException(status_code=400, detail="Attached screenshot is empty.")
        if len(screenshot_bytes) > 5 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="Screenshot is too large. Keep it under 5 MB.")
        content_type = (screenshot.content_type or "").strip().lower()
        if content_type not in {"image/png", "image/jpeg", "image/webp", "image/gif"}:
            raise HTTPException(status_code=400, detail="Use a PNG, JPEG, WEBP, or GIF screenshot.")
        encoded = base64.b64encode(screenshot_bytes).decode("ascii")
        screenshot_data_url = f"data:{content_type};base64,{encoded}"
        screenshot_name = screenshot.filename or "screenshot"

    client = assistant_client()
    input_messages = build_assistant_input(history_items, user_message, context_payload, screenshot_data_url)

    try:
        response = client.responses.create(
            model=ASSISTANT_MODEL,
            instructions=ASSISTANT_SYSTEM_PROMPT,
            input=input_messages,
            text={"verbosity": "medium"},
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Assistant request failed: {exc}") from exc

    answer = str(getattr(response, "output_text", "") or "").strip()
    if not answer:
        answer = "I could not generate a usable reply. Try rephrasing the question or attaching a clearer screenshot."

    return {
        "ok": True,
        "answer": answer,
        "model": ASSISTANT_MODEL,
        "used_screenshot": bool(screenshot_data_url),
        "screenshot_name": screenshot_name,
    }


class ProfilePayload(BaseModel):
    company_name: str
    data: InputPayload


@app.post("/api/profiles")
def save_profile(payload: ProfilePayload):
    profiles = load_profiles()
    name = payload.company_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Company name is required")
    existing = profiles.get(name, {})
    profiles[name] = {
        "data": profile_input_data_from_payload(payload.data),
        "last_quote_summary": profile_summary(existing),
    }
    save_profiles(profiles)
    return {"ok": True, "profiles": profiles, "selected": name}


@app.delete("/api/profiles/{company_name}")
def delete_profile(company_name: str):
    name = company_name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Company name is required")

    profiles = load_profiles()
    if name not in profiles:
        raise HTTPException(status_code=404, detail="Company profile not found")

    profiles.pop(name, None)
    save_profiles(profiles)
    return {"ok": True, "profiles": profiles, "deleted": name}


class StopPayload(BaseModel):
    run_id: str


@app.post("/api/stop")
def stop_run(payload: StopPayload):
    RUN_CANCEL_FLAGS[payload.run_id] = True
    return {"ok": True, "stopped": payload.run_id}


def tracking_header_indexes(headers: list[str]) -> dict[str, int]:
    normalized = [str(header or "").strip().upper() for header in headers]
    required = {
        "date_shipped": "DATE SHIPPED",
        "eta": "ETA",
        "actual": "ACTUAL",
        "carrier": "CARRIER",
        "tracking": "TRACKING #",
        "agent_update": "AGENT UPDATE",
    }
    indexes: dict[str, int] = {}
    for key, header in required.items():
        try:
            indexes[key] = normalized.index(header)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=f"TRACKING tab is missing header: {header}") from exc
    return indexes


def row_value(row: list[str], index: int) -> str:
    return str(row[index]).strip() if index < len(row) else ""


def apply_tracking_row_color(ws, row_number: int, delivered: bool, *, partial: bool = False):
    color = TRACKING_PARTIAL_YELLOW if partial else TRACKING_GREEN if delivered else TRACKING_YELLOW
    with_gsheets_retry(lambda: ws.format(f"A{row_number}:H{row_number}", {"backgroundColor": color}))


def update_tracking_sheet(*, force_recent: bool = False) -> dict:
    ss = spreadsheet()
    try:
        ws = spreadsheet_worksheet(ss, TRACKING_TAB)
    except WorksheetNotFound as exc:
        raise HTTPException(status_code=404, detail="Could not find TRACKING tab") from exc

    rows = with_gsheets_retry(ws.get_all_values)
    if not rows:
        raise HTTPException(status_code=400, detail="TRACKING tab is empty")

    indexes = tracking_header_indexes(rows[0])
    checked = 0
    updated_eta = 0
    updated_actual = 0
    updated_notes = 0
    skipped_recent = 0
    skipped_today = 0
    skipped_eta_not_due = 0
    errors: list[str] = []
    browser_rows: list[dict] = []
    xpo_browser_rows: list[dict] = []
    tracking_log_rows: list[list[str]] = []
    logged = 0
    browser_row_limit = int((os.getenv("TRACKING_BROWSER_ROW_LIMIT") or "100").strip() or "100")
    now = datetime.now()
    tracking_cache = load_tracking_check_cache()

    for offset, row in enumerate(rows[1:], start=2):
        carrier = normalize_carrier(row_value(row, indexes["carrier"]))
        tracking = row_value(row, indexes["tracking"])
        date_shipped_existing = row_value(row, indexes["date_shipped"])
        eta_existing = row_value(row, indexes["eta"])
        actual_existing = row_value(row, indexes["actual"])

        if not tracking:
            continue

        agent_update_existing = row_value(row, indexes["agent_update"])
        shipped_date = parse_us_date_flexible(date_shipped_existing)
        if shipped_date == now.date():
            skipped_today += 1
            tracking_log_rows.append(
                tracking_log_row(
                    now,
                    "SKIPPED_TODAY",
                    offset,
                    carrier,
                    tracking,
                    eta_existing,
                    eta_existing,
                    actual_existing,
                    actual_existing,
                    "Shipment date is today",
                )
            )
            continue

        if actual_existing and not force_recent:
            tracking_log_rows.append(
                tracking_log_row(
                    now,
                    "SKIPPED_DELIVERED",
                    offset,
                    carrier,
                    tracking,
                    eta_existing,
                    eta_existing,
                    actual_existing,
                    actual_existing,
                    "Already delivered",
                )
            )
            continue

        eta_date = parse_us_date_flexible(eta_existing)
        if eta_existing and eta_date and not tracking_eta_due_for_recheck(eta_existing, now.date()):
            skipped_eta_not_due += 1
            tracking_log_rows.append(
                tracking_log_row(
                    now,
                    "SKIPPED_ETA_NOT_DUE",
                    offset,
                    carrier,
                    tracking,
                    eta_existing,
                    eta_existing,
                    actual_existing,
                    actual_existing,
                    f"ETA {eta_existing} is more than {TRACKING_ETA_CHECK_WINDOW_DAYS} days away",
                )
            )
            continue

        if not force_recent and (
            tracking_checked_recently(agent_update_existing, now)
            or cached_tracking_checked_recently(tracking_cache, carrier, tracking, now)
        ):
            skipped_recent += 1
            tracking_log_rows.append(
                tracking_log_row(
                    now,
                    "SKIPPED_RECENT",
                    offset,
                    carrier,
                    tracking,
                    eta_existing,
                    eta_existing,
                    actual_existing,
                    actual_existing,
                    agent_update_existing or "Checked in the last 2 hours",
                )
            )
            continue

        result: Optional[dict] = None
        try:
            if carrier == "NUMARK" and (not eta_existing or not actual_existing):
                result = get_numark_tracking(tracking)
                remember_tracking_checked(tracking_cache, carrier, tracking, now)
            elif carrier == "R&L" and (not eta_existing or not actual_existing):
                result = get_rl_tracking(tracking)
                remember_tracking_checked(tracking_cache, carrier, tracking, now)
            elif carrier == "DTI" and (not eta_existing or not actual_existing):
                result = get_dti_tracking(tracking)
                remember_tracking_checked(tracking_cache, carrier, tracking, now)
            elif carrier in {"UPS", "USPS", "GLOVALINK", "ESTES", "TFORCE", "TFWW", "XPO", "TOTAL", "ABF", "ROADRUNNER"}:
                needs_browser_check = force_recent or not eta_existing or not actual_existing
                if needs_browser_check and len(browser_rows) + len(xpo_browser_rows) < browser_row_limit:
                    target_browser_rows = xpo_browser_rows if carrier == "XPO" else browser_rows
                    target_browser_rows.append(
                        {
                            "row_number": offset,
                            "carrier": carrier,
                            "tracking": tracking,
                            "old_eta": eta_existing,
                            "old_actual": actual_existing,
                        }
                    )
                elif needs_browser_check:
                    tracking_log_rows.append(
                        tracking_log_row(
                            now,
                            "SKIPPED_BROWSER_LIMIT",
                            offset,
                            carrier,
                            tracking,
                            eta_existing,
                            eta_existing,
                            actual_existing,
                            actual_existing,
                            "Browser tracking row limit reached",
                        )
                    )
                continue
        except Exception as exc:
            remember_tracking_checked(tracking_cache, carrier, tracking, now)
            save_tracking_check_cache(tracking_cache)
            note = with_tracking_checked_stamp(f"{carrier}: {exc}", now)
            update_agent_update_cell(ws, offset, indexes["agent_update"], note)
            errors.append(f"Row {offset}: {note}")
            tracking_log_rows.append(
                tracking_log_row(
                    now,
                    "ERROR",
                    offset,
                    carrier,
                    tracking,
                    eta_existing,
                    eta_existing,
                    actual_existing,
                    actual_existing,
                    note,
                )
            )
            continue

        if result is None:
            if not actual_existing:
                tracking_log_rows.append(
                    tracking_log_row(
                        now,
                        "SKIPPED_UNSUPPORTED",
                        offset,
                        carrier,
                        tracking,
                        eta_existing,
                        eta_existing,
                        actual_existing,
                        actual_existing,
                        f"{carrier}: automatic tracking is not configured",
                    )
                )
            continue

        checked += 1
        old_eta = eta_existing
        old_actual = actual_existing
        if not eta_existing and result.get("eta"):
            with_gsheets_retry(lambda r=offset, v=sheet_date(result["eta"]): ws.update_cell(r, indexes["eta"] + 1, v))
            eta_existing = sheet_date(result["eta"])
            updated_eta += 1
        if not actual_existing and result.get("actual"):
            with_gsheets_retry(lambda r=offset, v=sheet_date(result["actual"]): ws.update_cell(r, indexes["actual"] + 1, v))
            actual_existing = sheet_date(result["actual"])
            updated_actual += 1
        if (result.get("partial") or result.get("delivered") is False) and actual_existing:
            with_gsheets_retry(lambda r=offset: ws.update_cell(r, indexes["actual"] + 1, ""))
            actual_existing = ""
            updated_actual += 1
        if result.get("note"):
            note, url = tracking_note_with_link(carrier, result["note"], tracking)
            update_agent_update_cell(ws, offset, indexes["agent_update"], with_tracking_checked_stamp(note, now), url)
            updated_notes += 1
        apply_tracking_row_color(ws, offset, bool(actual_existing) and not result.get("partial"), partial=bool(result.get("partial")))
        save_tracking_check_cache(tracking_cache)
        changed = old_eta != eta_existing or old_actual != actual_existing
        tracking_log_rows.append(
            tracking_log_row(
                now,
                "CHECKED_UPDATED" if changed else "CHECKED_NO_CHANGE",
                offset,
                carrier,
                tracking,
                old_eta,
                eta_existing,
                old_actual,
                actual_existing,
                result.get("note") or "",
            )
        )

    def apply_browser_tracking_results(rows_to_apply: list[dict]):
        nonlocal updated_eta, updated_actual, updated_notes, tracking_log_rows
        if not rows_to_apply:
            return
        browser_results = browser_tracking_updates(rows_to_apply)
        browser_rows_by_number = {int(row["row_number"]): row for row in rows_to_apply}
        for row_number, result in browser_results.items():
            current_row = rows[row_number - 1] if row_number - 1 < len(rows) else []
            browser_row = browser_rows_by_number.get(int(row_number), {})
            eta_existing = row_value(current_row, indexes["eta"])
            actual_existing = row_value(current_row, indexes["actual"])
            old_eta = str(browser_row.get("old_eta") or eta_existing)
            old_actual = str(browser_row.get("old_actual") or actual_existing)
            carrier = row_value(current_row, indexes["carrier"])
            tracking = row_value(current_row, indexes["tracking"])
            if not eta_existing and result.get("eta"):
                with_gsheets_retry(lambda r=row_number, v=sheet_date(result["eta"]): ws.update_cell(r, indexes["eta"] + 1, v))
                eta_existing = sheet_date(result["eta"])
                updated_eta += 1
            if not actual_existing and result.get("actual"):
                with_gsheets_retry(lambda r=row_number, v=sheet_date(result["actual"]): ws.update_cell(r, indexes["actual"] + 1, v))
                actual_existing = sheet_date(result["actual"])
                updated_actual += 1
            if (result.get("partial") or result.get("delivered") is False) and actual_existing:
                with_gsheets_retry(lambda r=row_number: ws.update_cell(r, indexes["actual"] + 1, ""))
                actual_existing = ""
                updated_actual += 1
            note = result.get("note") or f"{carrier} tracking"
            note, url = tracking_note_with_link(carrier, note, tracking)
            update_agent_update_cell(ws, row_number, indexes["agent_update"], with_tracking_checked_stamp(note, now), url)
            updated_notes += 1
            apply_tracking_row_color(ws, row_number, bool(actual_existing) and not result.get("partial"), partial=bool(result.get("partial")))
            changed = old_eta != eta_existing or old_actual != actual_existing
            result_label = "CHECKED_UPDATED" if changed else "CHECKED_NO_CHANGE"
            if re.search(r":\s*.+Error|Timeout|failed|Exception", str(result.get("note") or ""), re.I):
                result_label = "ERROR"
            remember_tracking_checked(tracking_cache, carrier, tracking, now)
            save_tracking_check_cache(tracking_cache)
            tracking_log_rows.append(
                tracking_log_row(
                    now,
                    result_label,
                    row_number,
                    carrier,
                    tracking,
                    old_eta,
                    eta_existing,
                    old_actual,
                    actual_existing,
                    result.get("note") or note,
                )
            )

    if browser_rows:
        apply_browser_tracking_results(browser_rows)
        append_tracking_log_rows(ss, tracking_log_rows)
        logged += len(tracking_log_rows)
        tracking_log_rows = []

    if xpo_browser_rows and tracking_log_rows:
        append_tracking_log_rows(ss, tracking_log_rows)
        logged += len(tracking_log_rows)
        tracking_log_rows = []

    if xpo_browser_rows:
        apply_browser_tracking_results(xpo_browser_rows)

    append_tracking_log_rows(ss, tracking_log_rows)
    logged += len(tracking_log_rows)

    return {
        "ok": True,
        "checked": checked + len(browser_rows) + len(xpo_browser_rows),
        "updated_eta": updated_eta,
        "updated_actual": updated_actual,
        "updated_notes": updated_notes,
        "logged": logged,
        "skipped_recent": skipped_recent,
        "skipped_today": skipped_today,
        "skipped_eta_not_due": skipped_eta_not_due,
        "browser_checked": len(browser_rows) + len(xpo_browser_rows),
        "errors": errors[:10],
    }


class UpdateTrackingPayload(BaseModel):
    force_recent: bool = False


@app.post("/api/update-tracking")
async def update_tracking(payload: UpdateTrackingPayload):
    try:
        async with TRACKING_UPDATE_LOCK:
            return await asyncio.to_thread(update_tracking_sheet, force_recent=payload.force_recent)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/run")
async def run_quote(payload: InputPayload):
    try:
        async with QUOTE_RUN_LOCK:
            ss = await asyncio.to_thread(spreadsheet)
            ws = await asyncio.to_thread(spreadsheet_worksheet, ss, INPUT_TAB)
            await asyncio.to_thread(write_input_values, ws, payload)

            broker_result = await asyncio.to_thread(write_brokers, spreadsheet=ss)
            if not broker_result["ok"]:
                raise HTTPException(status_code=400, detail=broker_result["message"])

            quote_data = payload_to_quote_data(payload)
            selected_brokers, selected_direct_carriers = normalized_quote_selections(payload)
            browser_options = quote_browser_options(payload)
            result_map: dict[str, dict] = {}
            covered_direct_carriers = set()
            direct_carriers = carrier_queue()

            initial_jobs = []
            if WEBAPP_ENABLE_GLT and "GLT" in selected_brokers:
                initial_jobs.append(
                    (
                        "GLT",
                        thread_job(run_glt_job, quote_data, **browser_options),
                    )
                )
            if not WEBAPP_GLT_ONLY_DEBUG:
                if "SCHNEIDER" in selected_brokers:
                    initial_jobs.append(
                        (
                            "SCHNEIDER",
                            coroutine_job(run_schneider_job_async, quote_data, **browser_options),
                        )
                    )
                if "MYCARRIER" in selected_brokers:
                    initial_jobs.append(
                        (
                            "MYCARRIER",
                            thread_job(run_mycarrier_job, quote_data, **browser_options),
                        )
                    )
                if "PRIORITY1" in selected_brokers:
                    initial_jobs.append(
                        (
                            "PRIORITY1",
                            thread_job(run_priority1_job, quote_data, **browser_options),
                        )
                    )

            async for _, job_result in run_jobs_sequentially(initial_jobs):
                upsert_result(result_map, job_result["wrapped"])
                covered_direct_carriers |= job_result.get("covered_direct_carriers", set())

            if not WEBAPP_GLT_ONLY_DEBUG and selected_direct_carriers:
                queued_direct = []
                for carrier_name, fn in direct_carriers:
                    if carrier_name not in selected_direct_carriers:
                        continue
                    if fn is None:
                        upsert_result(
                            result_map,
                            skipped_result(
                                carrier_name,
                                quote_data,
                                RuntimeError(f"Carrier unavailable: {CENTRAL_IMPORT_ERROR or 'missing quote_central export'}"),
                            ),
                        )
                        continue
                    queued_direct.append(
                        (
                            carrier_name,
                            thread_job(run_direct_carrier_job, carrier_name, fn, quote_data, **browser_options),
                        )
                    )

                async for _, job_result in run_jobs_sequentially(queued_direct):
                    if job_result.get("direct_result") is not None:
                        await asyncio.to_thread(write_direct_result, ss, job_result["carrier"], job_result["direct_result"], quote_data)
                    upsert_result(result_map, job_result["wrapped"])

                for carrier_name, fn in direct_carriers:
                    if carrier_name not in selected_direct_carriers:
                        continue
                    existing_result = result_map.get(carrier_name)
                    if not existing_result or not should_retry_skipped_result(existing_result):
                        continue
                    if fn is None:
                        continue
                    retried_job = await asyncio.to_thread(run_direct_carrier_job, carrier_name, fn, quote_data, **browser_options)
                    if retried_job.get("direct_result") is not None:
                        await asyncio.to_thread(write_direct_result, ss, carrier_name, retried_job["direct_result"], quote_data)
                    retried_result = retried_job["wrapped"]
                    if should_retry_skipped_result(retried_result):
                        retried_result = mark_retry_exhausted(retried_result)
                    else:
                        retried_result["retry_attempted"] = True
                    upsert_result(result_map, retried_result)

            results = ordered_results(result_map)

            await asyncio.to_thread(update_profile_last_quote, payload.company_name, results, profile_input_data_from_payload(payload))
            await asyncio.to_thread(append_quote_log, ss, payload, results)

            return {
                "ok": True,
                "message": "Input saved, broker rows prepared, carrier quotes written, and Top 3 results logged. Latest input values were kept in the Input sheet.",
                "batch_id": broker_result["batch_id"],
                "broker_rows_written": broker_result["row_count"],
                "results": results,
                "reset_form": payload_to_sheet_values(payload),
            }
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


def stream_line(payload: dict):
    return json.dumps(payload) + "\n"


@app.post("/api/run-stream")
async def run_quote_stream(payload: InputPayload):
    async def event_stream():
        run_id = str(uuid4())
        try:
            async with QUOTE_RUN_LOCK:
                ss = await asyncio.to_thread(spreadsheet)
                yield stream_line({"type": "status", "message": "Writing inputs to Google Sheet..."})
                ws = await asyncio.to_thread(spreadsheet_worksheet, ss, INPUT_TAB)
                await asyncio.to_thread(write_input_values, ws, payload)

                broker_result = await asyncio.to_thread(write_brokers, spreadsheet=ss)
                if not broker_result["ok"]:
                    yield stream_line({"type": "error", "message": broker_result["message"]})
                    return

                quote_data = payload_to_quote_data(payload)
                selected_brokers, selected_direct_carriers = normalized_quote_selections(payload)
                browser_options = quote_browser_options(payload)
                result_map: dict[str, dict] = {}
                covered_direct_carriers = set()
                direct_carriers = carrier_queue()
                yield stream_line(
                    {
                        "type": "started",
                        "run_id": run_id,
                        "batch_id": broker_result["batch_id"],
                        "broker_rows_written": broker_result["row_count"],
                    }
                )

                initial_jobs = []
                if WEBAPP_ENABLE_GLT and "GLT" in selected_brokers:
                    initial_jobs.append(
                        (
                            "GLT",
                            thread_job(run_glt_job, quote_data, **browser_options),
                        )
                    )
                if not WEBAPP_GLT_ONLY_DEBUG:
                    if "SCHNEIDER" in selected_brokers:
                        initial_jobs.append(
                            (
                                "SCHNEIDER",
                                coroutine_job(run_schneider_job_async, quote_data, **browser_options),
                            )
                        )
                    if "MYCARRIER" in selected_brokers:
                        initial_jobs.append(
                            (
                                "MYCARRIER",
                                thread_job(run_mycarrier_job, quote_data, **browser_options),
                            )
                        )
                    if "PRIORITY1" in selected_brokers:
                        initial_jobs.append(
                            (
                                "PRIORITY1",
                                thread_job(run_priority1_job, quote_data, **browser_options),
                            )
                        )

                if initial_jobs:
                    yield stream_line({"type": "status", "message": "Quoting broker portals one at a time..."})
                async for _, job_result in run_jobs_sequentially(initial_jobs):
                    upsert_result(result_map, job_result["wrapped"])
                    covered_direct_carriers |= job_result.get("covered_direct_carriers", set())
                    results = ordered_results(result_map)
                    yield stream_line(
                        {
                            "type": "result",
                            "run_id": run_id,
                            "batch_id": broker_result["batch_id"],
                            "result": job_result["wrapped"],
                            "results": results,
                        }
                    )

                if not WEBAPP_GLT_ONLY_DEBUG and selected_direct_carriers:
                    if RUN_CANCEL_FLAGS.get(run_id):
                        yield stream_line(
                            {
                                "type": "stopped",
                                "run_id": run_id,
                                "message": "Run stopped before starting direct carriers.",
                                "batch_id": broker_result["batch_id"],
                                "results": ordered_results(result_map),
                            }
                        )
                        return

                    direct_jobs = []
                    for carrier_name, fn in direct_carriers:
                        if carrier_name not in selected_direct_carriers:
                            continue
                        if fn is None:
                            wrapped = skipped_result(
                                carrier_name,
                                quote_data,
                                RuntimeError(f"Carrier unavailable: {CENTRAL_IMPORT_ERROR or 'missing quote_central export'}"),
                            )
                            upsert_result(result_map, wrapped)
                            yield stream_line(
                                {
                                    "type": "result",
                                    "run_id": run_id,
                                    "batch_id": broker_result["batch_id"],
                                    "result": wrapped,
                                    "results": ordered_results(result_map),
                                }
                            )
                            continue
                        direct_jobs.append(
                            (
                                carrier_name,
                                thread_job(run_direct_carrier_job, carrier_name, fn, quote_data, **browser_options),
                            )
                        )

                    if direct_jobs:
                        yield stream_line({"type": "status", "message": "Quoting direct carriers one at a time..."})
                    async for _, job_result in run_jobs_sequentially(
                        direct_jobs,
                        should_stop=lambda: RUN_CANCEL_FLAGS.get(run_id, False),
                    ):
                        if job_result.get("direct_result") is not None:
                            await asyncio.to_thread(write_direct_result, ss, job_result["carrier"], job_result["direct_result"], quote_data)
                        upsert_result(result_map, job_result["wrapped"])
                        results = ordered_results(result_map)
                        yield stream_line(
                            {
                                "type": "result",
                                "run_id": run_id,
                                "batch_id": broker_result["batch_id"],
                                "result": job_result["wrapped"],
                                "results": results,
                            }
                        )

                    if RUN_CANCEL_FLAGS.get(run_id):
                        yield stream_line(
                            {
                                "type": "stopped",
                                "run_id": run_id,
                                "message": "Run stopped before retrying skipped carriers.",
                                "batch_id": broker_result["batch_id"],
                                "results": ordered_results(result_map),
                            }
                        )
                        return

                    for carrier_name, fn in direct_carriers:
                        if carrier_name not in selected_direct_carriers:
                            continue
                        existing_result = result_map.get(carrier_name)
                        if not existing_result or not should_retry_skipped_result(existing_result):
                            continue
                        if fn is None:
                            continue
                        yield stream_line(
                            {
                                "type": "status",
                                "message": f"Retrying {carrier_name} after full carrier pass...",
                            }
                        )
                        retried_job = await asyncio.to_thread(run_direct_carrier_job, carrier_name, fn, quote_data, **browser_options)
                        if retried_job.get("direct_result") is not None:
                            await asyncio.to_thread(write_direct_result, ss, carrier_name, retried_job["direct_result"], quote_data)
                        retried_result = retried_job["wrapped"]
                        if should_retry_skipped_result(retried_result):
                            retried_result = mark_retry_exhausted(retried_result)
                        else:
                            retried_result["retry_attempted"] = True
                        upsert_result(result_map, retried_result)
                        results = ordered_results(result_map)
                        yield stream_line(
                            {
                                "type": "result",
                                "run_id": run_id,
                                "batch_id": broker_result["batch_id"],
                                "result": retried_result,
                                "results": results,
                            }
                        )

                results = ordered_results(result_map)
                await asyncio.to_thread(update_profile_last_quote, payload.company_name, results, profile_input_data_from_payload(payload))
                yield stream_line({"type": "status", "message": "Writing Top 3 results to LOGS..."})
                await asyncio.to_thread(append_quote_log, ss, payload, results)
                yield stream_line(
                    {
                        "type": "complete",
                        "run_id": run_id,
                        "message": "Quotes completed, written to Broker Result, and logged to LOGS. Latest input values were kept in the Input sheet.",
                        "batch_id": broker_result["batch_id"],
                        "broker_rows_written": broker_result["row_count"],
                        "results": results,
                        "reset_form": payload_to_sheet_values(payload),
                    }
                )
        except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError, GeneratorExit):
            return
        except Exception as exc:
            try:
                yield stream_line({"type": "error", "message": str(exc)})
            except (BrokenPipeError, ConnectionResetError, asyncio.CancelledError, GeneratorExit):
                return
        finally:
            RUN_CANCEL_FLAGS.pop(run_id, None)

    return StreamingResponse(event_stream(), media_type="application/x-ndjson")
