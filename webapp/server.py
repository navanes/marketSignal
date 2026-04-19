import asyncio
import base64
from datetime import date
from uuid import uuid4
import json
import os
from pathlib import Path
import re
import socket
import sys
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent
STATIC_DIR = BASE_DIR / "static"
SERVICE_ACCOUNT_FILE = PROJECT_ROOT / "service_account.json"
PROFILES_FILE = PROJECT_ROOT / "company_profiles.json"

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
from quote_numark import quote_numark
from quote_glt import quote_glt
from quote_schneider import quote_schneider
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
WEBAPP_ENABLE_GLT = False
WEBAPP_GLT_HEADLESS = True
WEBAPP_GLT_SLOW_MO = 0
WEBAPP_GLT_ONLY_DEBUG = False
WEBAPP_REVEAL_SLOW_MO = int((os.getenv("WEBAPP_REVEAL_SLOW_MO") or "250").strip() or "250")
RESULT_DISPLAY_ORDER = [
    "GLT",
    "SCHNEIDER",
    "MYCARRIER",
    "TOTAL",
    "CENTRAL TRANSPORTATION",
    "NUMARK",
    "TFORCE",
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
    return payload.model_dump(exclude={"quote_target", "browser_visibility"})


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


def safe_json_load(text: str | None, fallback):
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


def run_glt_job(quote_data: dict, *, headless: bool | None = None, slow_mo: int | None = None):
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
        yield job_name, await asyncio.to_thread(job_fn)


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
            ]
        )
        targets.extend({"value": carrier_name, "label": carrier_name} for carrier_name, _ in carrier_queue())
    return targets


def normalized_quote_target(value: str | None) -> str:
    raw = str(value or "ALL").strip().upper() or "ALL"
    allowed = {item["value"] for item in available_quote_targets()}
    if raw not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported quote target: {raw}")
    return raw


def normalized_browser_visibility(value: str | None) -> str:
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
    }


@app.post("/api/assistant")
async def assistant_reply(
    message: str = Form(default=""),
    history: str = Form(default="[]"),
    context: str = Form(default="{}"),
    screenshot: UploadFile | None = File(default=None),
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
            quote_target = normalized_quote_target(payload.quote_target)
            browser_options = quote_browser_options(payload)
            run_all_targets = quote_target == "ALL"
            result_map: dict[str, dict] = {}
            covered_direct_carriers = set()
            direct_carriers = carrier_queue()

            initial_jobs = []
            if WEBAPP_ENABLE_GLT and (run_all_targets or quote_target == "GLT"):
                initial_jobs.append(
                    (
                        "GLT",
                        lambda quote_data=quote_data, browser_options=browser_options: run_glt_job(quote_data, **browser_options),
                    )
                )
            if not WEBAPP_GLT_ONLY_DEBUG:
                if run_all_targets or quote_target == "SCHNEIDER":
                    initial_jobs.append(
                        (
                            "SCHNEIDER",
                            lambda quote_data=quote_data, browser_options=browser_options: run_schneider_job(quote_data, **browser_options),
                        )
                    )
                if run_all_targets or quote_target == "MYCARRIER":
                    initial_jobs.append(
                        (
                            "MYCARRIER",
                            lambda quote_data=quote_data, browser_options=browser_options: run_mycarrier_job(quote_data, **browser_options),
                        )
                    )

            async for _, job_result in run_jobs_sequentially(initial_jobs):
                upsert_result(result_map, job_result["wrapped"])
                covered_direct_carriers |= job_result.get("covered_direct_carriers", set())

            selected_direct_target = None if run_all_targets else quote_target
            if not WEBAPP_GLT_ONLY_DEBUG and (run_all_targets or any(carrier_name == selected_direct_target for carrier_name, _ in direct_carriers)):
                queued_direct = []
                for carrier_name, fn in direct_carriers:
                    if selected_direct_target and carrier_name != selected_direct_target:
                        continue
                    if run_all_targets and carrier_name in covered_direct_carriers:
                        upsert_result(
                            result_map,
                            skipped_result(
                                carrier_name,
                                quote_data,
                                RuntimeError("Covered by MyCarrier results."),
                            ),
                        )
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
                            lambda carrier_name=carrier_name, fn=fn, quote_data=quote_data, browser_options=browser_options: run_direct_carrier_job(
                                carrier_name,
                                fn,
                                quote_data,
                                **browser_options,
                            ),
                        )
                    )

                async for _, job_result in run_jobs_sequentially(queued_direct):
                    if job_result.get("direct_result") is not None:
                        await asyncio.to_thread(write_direct_result, ss, job_result["carrier"], job_result["direct_result"], quote_data)
                    upsert_result(result_map, job_result["wrapped"])

                for carrier_name, fn in direct_carriers:
                    if selected_direct_target and carrier_name != selected_direct_target:
                        continue
                    existing_result = result_map.get(carrier_name)
                    if not existing_result or not should_retry_skipped_result(existing_result):
                        continue
                    if (run_all_targets and carrier_name in covered_direct_carriers) or fn is None:
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
                quote_target = normalized_quote_target(payload.quote_target)
                browser_options = quote_browser_options(payload)
                run_all_targets = quote_target == "ALL"
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
                if WEBAPP_ENABLE_GLT and (run_all_targets or quote_target == "GLT"):
                    initial_jobs.append(
                        (
                            "GLT",
                            lambda quote_data=quote_data, browser_options=browser_options: run_glt_job(quote_data, **browser_options),
                        )
                    )
                if not WEBAPP_GLT_ONLY_DEBUG:
                    if run_all_targets or quote_target == "SCHNEIDER":
                        initial_jobs.append(
                            (
                                "SCHNEIDER",
                                lambda quote_data=quote_data, browser_options=browser_options: run_schneider_job(quote_data, **browser_options),
                            )
                        )
                    if run_all_targets or quote_target == "MYCARRIER":
                        initial_jobs.append(
                            (
                                "MYCARRIER",
                                lambda quote_data=quote_data, browser_options=browser_options: run_mycarrier_job(quote_data, **browser_options),
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

                selected_direct_target = None if run_all_targets else quote_target
                if not WEBAPP_GLT_ONLY_DEBUG and (run_all_targets or any(carrier_name == selected_direct_target for carrier_name, _ in direct_carriers)):
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
                        if selected_direct_target and carrier_name != selected_direct_target:
                            continue
                        if run_all_targets and carrier_name in covered_direct_carriers:
                            wrapped = skipped_result(
                                carrier_name,
                                quote_data,
                                RuntimeError("Covered by MyCarrier results."),
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
                                lambda carrier_name=carrier_name, fn=fn, quote_data=quote_data, browser_options=browser_options: run_direct_carrier_job(
                                    carrier_name,
                                    fn,
                                    quote_data,
                                    **browser_options,
                                ),
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
                        if selected_direct_target and carrier_name != selected_direct_target:
                            continue
                        existing_result = result_map.get(carrier_name)
                        if not existing_result or not should_retry_skipped_result(existing_result):
                            continue
                        if (run_all_targets and carrier_name in covered_direct_carriers) or fn is None:
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
