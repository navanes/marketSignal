import asyncio
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import date
from uuid import uuid4
import json
from pathlib import Path
import re
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
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from google.oauth2.service_account import Credentials
from pydantic import BaseModel, Field

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

app = FastAPI(title="Freight Quote Agent Web")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
RUN_CANCEL_FLAGS: dict[str, bool] = {}
WEBAPP_ENABLE_GLT = False
WEBAPP_GLT_HEADLESS = True
WEBAPP_GLT_SLOW_MO = 0
WEBAPP_GLT_ONLY_DEBUG = False
INITIAL_PARALLEL_WORKERS = 3
DIRECT_PARALLEL_WORKERS = 2
RESULT_DISPLAY_ORDER = [
    "GLT",
    "SCHNEIDER",
    "MYCARRIER",
    "TOTAL",
    "CENTRAL TRANSPORTATION",
    "NUMARK",
    "TFORCE",
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


def run_glt_job(quote_data: dict):
    try:
        raw_glt_results = quote_glt(
            quote_data,
            headless=WEBAPP_GLT_HEADLESS,
            slow_mo=WEBAPP_GLT_SLOW_MO,
        )
        wrapped_glt = wrap_glt_results(raw_glt_results, quote_data)
        return {
            "carrier": "GLT",
            "wrapped": wrapped_glt,
            "covered_direct_carriers": glt_covered_direct_carriers(raw_glt_results),
        }
    except Exception as exc:
        if should_skip_carrier_error(str(exc)):
            return {
                "carrier": "GLT",
                "wrapped": skipped_result("GLT", quote_data, exc),
                "covered_direct_carriers": set(),
            }
        raise


def run_schneider_job(quote_data: dict):
    try:
        raw_schneider_results = quote_schneider(quote_data)
        wrapped_schneider = wrap_schneider_results(raw_schneider_results, quote_data)
        return {
            "carrier": "SCHNEIDER",
            "wrapped": wrapped_schneider,
            "covered_direct_carriers": set(),
        }
    except Exception as exc:
        if should_skip_carrier_error(str(exc)):
            return {
                "carrier": "SCHNEIDER",
                "wrapped": skipped_result("SCHNEIDER", quote_data, exc),
                "covered_direct_carriers": set(),
            }
        raise


def run_mycarrier_job(quote_data: dict):
    try:
        raw_mycarrier_results = quote_mycarrier(quote_data)
        wrapped_mycarrier = wrap_mycarrier_results(raw_mycarrier_results, quote_data)
        return {
            "carrier": "MYCARRIER",
            "wrapped": wrapped_mycarrier,
            "covered_direct_carriers": mycarrier_covered_direct_carriers(raw_mycarrier_results),
        }
    except Exception as exc:
        if should_skip_carrier_error(str(exc)):
            return {
                "carrier": "MYCARRIER",
                "wrapped": skipped_result("MYCARRIER", quote_data, exc),
                "covered_direct_carriers": set(),
            }
        raise


def run_direct_carrier_job(carrier_name: str, fn, quote_data: dict):
    try:
        result = fn(quote_data)
        return {
            "carrier": carrier_name,
            "wrapped": result_with_inputs(result, quote_data),
            "direct_result": result,
        }
    except Exception as exc:
        if should_skip_carrier_error(str(exc)):
            return {
                "carrier": carrier_name,
                "wrapped": skipped_result(carrier_name, quote_data, exc),
                "direct_result": None,
            }
        raise


def run_jobs_limited(jobs: list[tuple[str, callable]], *, max_workers: int, should_stop=None):
    if not jobs:
        return

    worker_count = max(1, min(max_workers, len(jobs)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        job_iter = iter(jobs)
        in_flight: dict = {}

        def submit_next():
            if should_stop and should_stop():
                return False
            try:
                job_name, job_fn = next(job_iter)
            except StopIteration:
                return False
            in_flight[executor.submit(job_fn)] = job_name
            return True

        for _ in range(worker_count):
            if not submit_next():
                break

        while in_flight:
            done, _ = wait(list(in_flight.keys()), return_when=FIRST_COMPLETED)
            for future in done:
                job_name = in_flight.pop(future)
                yield job_name, future.result()
                submit_next()


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


def normalize_carrier_key(name: str) -> str:
    return re.sub(r"[^A-Z0-9]+", " ", str(name or "").upper()).strip()


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


def update_profile_last_quote(company_name: str, results: list[dict]):
    name = (company_name or "").strip()
    if not name or not results:
        return
    priced = []
    for item in results:
        broker_quotes = item.get("broker_quotes") or []
        if broker_quotes:
            for broker_quote in broker_quotes:
                if broker_quote.get("price") not in (None, "", 0, 0.0):
                    priced.append(
                        {
                            "carrier": broker_quote.get("carrier", ""),
                            "price": broker_quote.get("price"),
                            "transit_days": broker_quote.get("transit_days"),
                            "source": item.get("carrier", ""),
                            "inputs": item.get("inputs") or {},
                        }
                    )
            continue
        if item.get("price") not in (None, "", 0, 0.0):
            priced.append(item)
    if not priced:
        return
    cheapest = min(priced, key=lambda item: float(item.get("price")))
    profiles = load_profiles()
    record = profiles.get(name, {})
    profiles[name] = {
        "data": profile_data(record) or {},
        "last_quote_summary": {
            "carrier": cheapest.get("carrier", ""),
            "price": cheapest.get("price"),
            "transit_days": cheapest.get("transit_days"),
            "source": cheapest.get("source", ""),
            "quoted_at": date.today().isoformat(),
            "origin_zip": (cheapest.get("inputs") or {}).get("origin_zip", ""),
            "destination_zip": (cheapest.get("inputs") or {}).get("destination_zip", ""),
            "origin_city": (cheapest.get("inputs") or {}).get("origin_city", ""),
            "destination_city": (cheapest.get("inputs") or {}).get("destination_city", ""),
            "pallets": (cheapest.get("inputs") or {}).get("pallets", ""),
            "pieces": (cheapest.get("inputs") or {}).get("pieces", ""),
            "length": (cheapest.get("inputs") or {}).get("length", ""),
            "width": (cheapest.get("inputs") or {}).get("width", ""),
            "height": (cheapest.get("inputs") or {}).get("height", ""),
            "weight": (cheapest.get("inputs") or {}).get("weight", ""),
            "freight_class": (cheapest.get("inputs") or {}).get("freight_class", ""),
        },
    }
    save_profiles(profiles)


@app.get("/")
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/Windgate.png")
def windgate_logo():
    return FileResponse(BASE_DIR / "Windgate.png")


@app.get("/LOGO-blue.png")
def loader_logo():
    return FileResponse(BASE_DIR / "LOGO-blue.png")


@app.post("/api/input")
def save_input(payload: InputPayload):
    ws = input_sheet()
    write_input_values(ws, payload)
    return {"ok": True, "written": {k: CELL_MAP[k] for k in CELL_MAP}}


@app.get("/api/profiles")
def get_profiles():
    return {"ok": True, "profiles": load_profiles()}


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
        "data": payload.data.model_dump(),
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
def run_quote(payload: InputPayload):
    try:
        ss = spreadsheet()
        write_input_values(spreadsheet_worksheet(ss, INPUT_TAB), payload)

        broker_result = write_brokers(spreadsheet=ss)
        if not broker_result["ok"]:
            raise HTTPException(status_code=400, detail=broker_result["message"])

        quote_data = payload_to_quote_data(payload)
        result_map: dict[str, dict] = {}
        covered_direct_carriers = set()

        initial_jobs = []
        if WEBAPP_ENABLE_GLT:
            initial_jobs.append(("GLT", lambda quote_data=quote_data: run_glt_job(quote_data)))
        if not WEBAPP_GLT_ONLY_DEBUG:
            initial_jobs.append(("SCHNEIDER", lambda quote_data=quote_data: run_schneider_job(quote_data)))
            initial_jobs.append(("MYCARRIER", lambda quote_data=quote_data: run_mycarrier_job(quote_data)))

        for _, job_result in run_jobs_limited(initial_jobs, max_workers=INITIAL_PARALLEL_WORKERS):
            upsert_result(result_map, job_result["wrapped"])
            covered_direct_carriers |= job_result.get("covered_direct_carriers", set())

        if not WEBAPP_GLT_ONLY_DEBUG:
            queued_direct = []
            for carrier_name, fn in carrier_queue():
                if carrier_name in covered_direct_carriers:
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
                        lambda carrier_name=carrier_name, fn=fn, quote_data=quote_data: run_direct_carrier_job(carrier_name, fn, quote_data),
                    )
                )

            for _, job_result in run_jobs_limited(queued_direct, max_workers=DIRECT_PARALLEL_WORKERS):
                if job_result.get("direct_result") is not None:
                    write_direct_result(ss, job_result["carrier"], job_result["direct_result"], quote_data)
                upsert_result(result_map, job_result["wrapped"])

            for carrier_name, fn in carrier_queue():
                existing_result = result_map.get(carrier_name)
                if not existing_result or not should_retry_skipped_result(existing_result):
                    continue
                if carrier_name in covered_direct_carriers or fn is None:
                    continue
                retried_job = run_direct_carrier_job(carrier_name, fn, quote_data)
                if retried_job.get("direct_result") is not None:
                    write_direct_result(ss, carrier_name, retried_job["direct_result"], quote_data)
                retried_result = retried_job["wrapped"]
                if should_retry_skipped_result(retried_result):
                    retried_result = mark_retry_exhausted(retried_result)
                else:
                    retried_result["retry_attempted"] = True
                upsert_result(result_map, retried_result)

        results = ordered_results(result_map)

        update_profile_last_quote(payload.company_name, results)

        clear_input_values(spreadsheet_worksheet(ss, INPUT_TAB))

        return {
            "ok": True,
            "message": "Input saved, broker rows prepared, and carrier quotes written.",
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
def run_quote_stream(payload: InputPayload):
    def event_stream():
        run_id = str(uuid4())
        try:
            ss = spreadsheet()
            yield stream_line({"type": "status", "message": "Writing inputs to Google Sheet..."})
            write_input_values(spreadsheet_worksheet(ss, INPUT_TAB), payload)

            broker_result = write_brokers(spreadsheet=ss)
            if not broker_result["ok"]:
                yield stream_line({"type": "error", "message": broker_result["message"]})
                return

            quote_data = payload_to_quote_data(payload)
            result_map: dict[str, dict] = {}
            covered_direct_carriers = set()
            yield stream_line(
                {
                    "type": "started",
                    "run_id": run_id,
                    "batch_id": broker_result["batch_id"],
                    "broker_rows_written": broker_result["row_count"],
                }
            )

            initial_jobs = []
            if WEBAPP_ENABLE_GLT:
                initial_jobs.append(("GLT", lambda quote_data=quote_data: run_glt_job(quote_data)))
            if not WEBAPP_GLT_ONLY_DEBUG:
                initial_jobs.append(("SCHNEIDER", lambda quote_data=quote_data: run_schneider_job(quote_data)))
                initial_jobs.append(("MYCARRIER", lambda quote_data=quote_data: run_mycarrier_job(quote_data)))

            if initial_jobs:
                yield stream_line({"type": "status", "message": "Quoting broker portals in parallel..."})
            for _, job_result in run_jobs_limited(initial_jobs, max_workers=INITIAL_PARALLEL_WORKERS):
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

            if not WEBAPP_GLT_ONLY_DEBUG:
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
                for carrier_name, fn in carrier_queue():
                    if carrier_name in covered_direct_carriers:
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
                            lambda carrier_name=carrier_name, fn=fn, quote_data=quote_data: run_direct_carrier_job(carrier_name, fn, quote_data),
                        )
                    )

                if direct_jobs:
                    yield stream_line({"type": "status", "message": "Quoting direct carriers with limited parallelism..."})
                for _, job_result in run_jobs_limited(
                    direct_jobs,
                    max_workers=DIRECT_PARALLEL_WORKERS,
                    should_stop=lambda: RUN_CANCEL_FLAGS.get(run_id, False),
                ):
                    if job_result.get("direct_result") is not None:
                        write_direct_result(ss, job_result["carrier"], job_result["direct_result"], quote_data)
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

                for carrier_name, fn in carrier_queue():
                    existing_result = result_map.get(carrier_name)
                    if not existing_result or not should_retry_skipped_result(existing_result):
                        continue
                    if carrier_name in covered_direct_carriers or fn is None:
                        continue
                    yield stream_line(
                        {
                            "type": "status",
                            "message": f"Retrying {carrier_name} after full carrier pass...",
                        }
                    )
                    retried_job = run_direct_carrier_job(carrier_name, fn, quote_data)
                    if retried_job.get("direct_result") is not None:
                        write_direct_result(ss, carrier_name, retried_job["direct_result"], quote_data)
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
            update_profile_last_quote(payload.company_name, results)
            clear_input_values(spreadsheet_worksheet(ss, INPUT_TAB))
            yield stream_line(
                {
                    "type": "complete",
                    "run_id": run_id,
                    "message": "Quotes completed and written to Broker Result.",
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
