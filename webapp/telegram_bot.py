from __future__ import annotations

import json
import os
import re
import time
import base64
import mimetypes
import threading
import uuid
from datetime import date
from pathlib import Path
from typing import Callable, Optional
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from webapp.telegram_utils import TelegramConfig
from webapp.telegram_utils import load_telegram_config
from webapp.telegram_utils import send_telegram_message


PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROFILES_FILE = PROJECT_ROOT / "company_profiles.json"
BOT_STATE_FILE = PROJECT_ROOT / ".telegram_bot_state.json"
LOCAL_APP_URL = (os.getenv("TELEGRAM_LOCAL_APP_URL") or "http://127.0.0.1:8001").rstrip("/")
OPENAI_MODEL = (os.getenv("OPENAI_MODEL") or "gpt-4o-mini").strip() or "gpt-4o-mini"
PENDING_QUOTE_TTL_SECONDS = int((os.getenv("TELEGRAM_PENDING_QUOTE_TTL_SECONDS") or "900").strip() or "900")
MISSING_QUOTE_PROMPT_DEDUP_SECONDS = 120
IMAGE_QUOTE_PROMPT = """Read this freight pick list image and extract the fields needed for an LTL quote.

Return only valid JSON with these keys:
is_picklist, company, ship_to_address, ship_to_city, ship_to_state, ship_to_zip, pallet_lines, notes.

Rules:
- If the image is not a pick list page/document, return {"is_picklist": false, "company": null, "ship_to_address": null, "ship_to_city": null, "ship_to_state": null, "ship_to_zip": null, "pallet_lines": [], "notes": "not a picklist"}.
- A pick list usually has visible text like "Pick List", "Ship To", Windgate Products, item table columns, and bottom freight fields.
- Do not use pallet/package photos unless a pick list page/document is clearly visible.
- If the image is a pick list, return is_picklist true.
- Use the Ship To box for company/address/city/state/zip.
- The top-left WINDGATE PRODUCTS CO., INC. text is the shipper/header, not the customer. Never return Windgate/Windgate Products as company.
- The company must be the first customer line inside the Ship To box, above the street address.
- Use every bottom handwritten/printed shipment line for Weight, Dimensions, Box Qty, and Pallet Qty.
- pallet_lines must be an array. Each item must have weight_lb, length_in, width_in, height_in, box_qty, pallet_qty.
- Dimensions are length x width x height in inches.
- box_qty means pieces.
- If there is only one bottom shipment line, still return one item in pallet_lines.
- Return null for any field you cannot read confidently.
- Keep notes short and mention uncertain fields only."""

QUOTE_TEMPLATE = """Please send the quote details by replying to this message.

For one pallet:

Company:
Pallet qty:
Pieces: optional
Weight:
Dimensions:

Example:
Company: ACCO COMMERCE
Pallet qty: 1
Pieces: 1
Weight: 300
Dimensions: 48 x 42 x 19

For multiple pallets, you can use pallet lines:

Company: CROWN PRODUCTS COMPANY
Pallet qty: 2
Pieces: 91
Weight: 3790 lb
Pallet lines:
#1: 1730 lb, 51 x 51 x 42, pieces 46, pallets 1
#2: 2060 lb, 51 x 51 x 41, pieces 45, pallets 1
Ship to ZIP: 32216"""

DEFAULT_BROKER_TARGETS = ["MYCARRIER"]
DEFAULT_DIRECT_CARRIER_TARGETS = ["TOTAL", "CENTRAL TRANSPORTATION", "NUMARK", "TFORCE", "GLOVALINK"]
BROKER_OPTIONS = [
    ("MYCARRIER", "MyCarrier"),
    ("SCHNEIDER", "Schneider"),
    ("PRIORITY1", "Priority1"),
]
DIRECT_CARRIER_OPTIONS = [
    ("TOTAL", "Total"),
    ("CENTRAL TRANSPORTATION", "Central"),
    ("NUMARK", "Numark"),
    ("TFORCE", "TForce"),
    ("GLOVALINK", "GlovaLink"),
]
MENU_KEYBOARD = {
    "keyboard": [
        [{"text": "/quote"}, {"text": "/carriers"}],
        [{"text": "/tracking"}, {"text": "/menu"}],
    ],
    "resize_keyboard": True,
    "is_persistent": True,
}
TRACKING_JOBS: dict[str, dict] = {}
QUOTE_RESULT_LIMITS = {3, 5, 10}
DEFAULT_QUOTE_RESULT_LIMIT = 5


class PicklistNotFoundError(RuntimeError):
    pass


def telegram_api_request(config: TelegramConfig, method: str, payload: Optional[dict] = None, *, timeout: int = 35) -> dict:
    if not config.bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN is not configured")
    token = quote(config.bot_token, safe=":")
    data = json.dumps(payload or {}).encode("utf-8")
    request = Request(
        f"https://api.telegram.org/bot{token}/{method}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=timeout) as response:
        result = json.loads(response.read().decode("utf-8") or "{}")
    if not result.get("ok"):
        raise RuntimeError(f"Telegram API error: {result}")
    return result


def telegram_file_url(config: TelegramConfig, file_path: str) -> str:
    token = quote(config.bot_token, safe=":")
    return f"https://api.telegram.org/file/bot{token}/{file_path}"


def download_telegram_file(config: TelegramConfig, file_id: str, *, opener: Callable = urlopen) -> tuple[bytes, str]:
    payload = telegram_api_request(config, "getFile", {"file_id": file_id}, timeout=30).get("result") or {}
    file_path = str(payload.get("file_path") or "")
    if not file_path:
        raise RuntimeError("Telegram did not return a file path for the image.")
    with opener(telegram_file_url(config, file_path), timeout=45) as response:
        image_bytes = response.read()
    content_type = mimetypes.guess_type(file_path)[0] or "image/jpeg"
    if content_type not in {"image/png", "image/jpeg", "image/webp"}:
        content_type = "image/jpeg"
    if not image_bytes:
        raise RuntimeError("Telegram image download was empty.")
    if len(image_bytes) > 10 * 1024 * 1024:
        raise RuntimeError("Image is too large. Please send a smaller screenshot/photo.")
    return image_bytes, content_type


def get_updates(config: TelegramConfig, offset: int = 0, *, timeout: int = 25) -> list[dict]:
    payload = {"timeout": timeout}
    if offset:
        payload["offset"] = offset
    return telegram_api_request(config, "getUpdates", payload, timeout=timeout + 10).get("result") or []


def answer_callback_query(config: TelegramConfig, callback_query_id: str, text: str = ""):
    payload = {"callback_query_id": callback_query_id}
    if text:
        payload["text"] = text[:200]
    return telegram_api_request(config, "answerCallbackQuery", payload, timeout=10)


def set_bot_commands(config: TelegramConfig):
    commands = [
        {"command": "menu", "description": "Open the freight quote menu"},
        {"command": "quote", "description": "Quote from a picklist image"},
        {"command": "carriers", "description": "Select brokers and carriers"},
        {"command": "tracking", "description": "Update tracking ETA and actual dates"},
        {"command": "help", "description": "Show quote instructions"},
    ]
    return telegram_api_request(config, "setMyCommands", {"commands": commands}, timeout=10)


def set_bot_menu_button(config: TelegramConfig):
    return telegram_api_request(config, "setChatMenuButton", {"menu_button": {"type": "commands"}}, timeout=10)


def load_bot_state() -> dict:
    if not BOT_STATE_FILE.exists():
        return {}
    try:
        return json.loads(BOT_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_bot_state(state: dict):
    BOT_STATE_FILE.write_text(json.dumps(state, indent=2), encoding="utf-8")


def save_bot_offset(offset: int):
    state = load_bot_state()
    state["offset"] = int(offset or 0)
    save_bot_state(state)


def chat_state(state: dict, chat_id: str) -> dict:
    chats = state.setdefault("chats", {})
    return chats.setdefault(str(chat_id), {})


def chat_quote_targets(state: dict, chat_id: str) -> tuple[list[str], list[str]]:
    settings = chat_state(state, chat_id).get("quote_targets")
    if not isinstance(settings, dict):
        return DEFAULT_BROKER_TARGETS[:], DEFAULT_DIRECT_CARRIER_TARGETS[:]
    brokers = settings.get("brokers") if isinstance(settings.get("brokers"), list) else DEFAULT_BROKER_TARGETS
    direct = settings.get("direct") if isinstance(settings.get("direct"), list) else DEFAULT_DIRECT_CARRIER_TARGETS
    broker_allowed = {value for value, _ in BROKER_OPTIONS}
    direct_allowed = {value for value, _ in DIRECT_CARRIER_OPTIONS}
    broker_targets = [str(value).upper() for value in brokers if str(value).upper() in broker_allowed]
    direct_targets = [str(value).upper() for value in direct if str(value).upper() in direct_allowed]
    return broker_targets, direct_targets


def set_chat_quote_targets(state: dict, chat_id: str, brokers: list[str], direct: list[str]):
    chat_state(state, chat_id)["quote_targets"] = {
        "brokers": brokers,
        "direct": direct,
    }
    save_bot_state(state)


def remember_recent_image(chat_id: str, message: dict):
    state = load_bot_state()
    chat = chat_state(state, chat_id)
    chat["recent_image"] = {
        "saved_at": time.time(),
        "photo": message.get("photo") or [],
        "document": message.get("document") or {},
    }
    save_bot_state(state)


def recent_image_message(chat_id: str, *, max_age_seconds: int = 1800) -> Optional[dict]:
    recent = chat_state(load_bot_state(), chat_id).get("recent_image")
    if not isinstance(recent, dict):
        return None
    saved_at = float(recent.get("saved_at") or 0)
    if saved_at and time.time() - saved_at > max_age_seconds:
        return None
    return {
        "photo": recent.get("photo") or [],
        "document": recent.get("document") or {},
    }


def missing_quote_prompt_key(message: dict, missing: list[str]) -> str:
    media_group_id = str(message.get("media_group_id") or "").strip()
    if media_group_id:
        return f"media_group:{media_group_id}"
    return f"message:{message.get('message_id') or best_photo_file_id(message) or ','.join(missing)}"


def should_send_missing_quote_prompt(chat_id: str, message: dict, missing: list[str]) -> bool:
    state = load_bot_state()
    chat = chat_state(state, chat_id)
    key = missing_quote_prompt_key(message, missing)
    now = time.time()
    last = chat.get("last_missing_quote_prompt") if isinstance(chat.get("last_missing_quote_prompt"), dict) else {}
    if last.get("key") == key and now - float(last.get("sent_at") or 0) < MISSING_QUOTE_PROMPT_DEDUP_SECONDS:
        return False
    chat["last_missing_quote_prompt"] = {"key": key, "sent_at": now}
    save_bot_state(state)
    return True


def media_group_key(message: dict) -> str:
    media_group_id = str(message.get("media_group_id") or "").strip()
    return f"media_group:{media_group_id}" if media_group_id else ""


def media_group_already_handled(chat_id: str, message: dict) -> bool:
    key = media_group_key(message)
    if not key:
        return False
    handled = chat_state(load_bot_state(), chat_id).get("handled_media_groups")
    return isinstance(handled, dict) and key in handled


def mark_media_group_handled(chat_id: str, message: dict):
    key = media_group_key(message)
    if not key:
        return
    state = load_bot_state()
    chat = chat_state(state, chat_id)
    handled = chat.setdefault("handled_media_groups", {})
    if not isinstance(handled, dict):
        handled = {}
        chat["handled_media_groups"] = handled
    now = time.time()
    for stored_key, saved_at in list(handled.items()):
        if now - float(saved_at or 0) > 3600:
            handled.pop(stored_key, None)
    handled[key] = now
    save_bot_state(state)


def normalize_lookup(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def load_profiles() -> dict:
    if not PROFILES_FILE.exists():
        return {}
    try:
        return json.loads(PROFILES_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def profile_data(record: dict) -> dict:
    if isinstance(record, dict) and isinstance(record.get("data"), dict):
        return record.get("data") or {}
    return record if isinstance(record, dict) else {}


def main_menu_markup() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "Quote From Picklist", "callback_data": "menu:quote"}],
            [{"text": "Select Carriers/Brokers", "callback_data": "menu:targets"}],
            [{"text": "Update Tracking ETA", "callback_data": "menu:tracking"}],
        ]
    }


def target_selection_markup(brokers: list[str], direct: list[str]) -> dict:
    rows = []
    for value, label in BROKER_OPTIONS:
        mark = "✓ " if value in brokers else ""
        rows.append([{"text": f"{mark}Broker: {label}", "callback_data": f"target:broker:{value}"}])
    for value, label in DIRECT_CARRIER_OPTIONS:
        mark = "✓ " if value in direct else ""
        rows.append([{"text": f"{mark}{label}", "callback_data": f"target:direct:{value}"}])
    rows.extend(
        [
            [
                {"text": "All", "callback_data": "target:all"},
                {"text": "Defaults", "callback_data": "target:defaults"},
                {"text": "Clear", "callback_data": "target:clear"},
            ],
            [{"text": "Done", "callback_data": "target:done"}],
        ]
    )
    return {"inline_keyboard": rows}


def quote_result_limit(fields: dict) -> int:
    try:
        limit = int(fields.get("report_limit") or DEFAULT_QUOTE_RESULT_LIMIT)
    except (TypeError, ValueError):
        limit = DEFAULT_QUOTE_RESULT_LIMIT
    return limit if limit in QUOTE_RESULT_LIMITS else DEFAULT_QUOTE_RESULT_LIMIT


def quote_confirmation_markup(fields: Optional[dict] = None) -> dict:
    selected_limit = quote_result_limit(fields or {})
    limit_buttons = []
    for limit in (3, 5, 10):
        mark = "✓ " if limit == selected_limit else ""
        limit_buttons.append({"text": f"{mark}Top {limit}", "callback_data": f"quote:limit:{limit}"})
    return {
        "inline_keyboard": [
            [{"text": "Select Carriers/Brokers", "callback_data": "menu:targets"}],
            limit_buttons,
            [
                {"text": "Confirm Quote", "callback_data": "quote:confirm"},
                {"text": "Edit Numbers", "callback_data": "quote:edit"},
            ],
            [{"text": "Cancel", "callback_data": "quote:cancel"}],
        ]
    }


def tracking_confirmation_markup() -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "Start Tracking Update", "callback_data": "tracking:confirm"},
                {"text": "Cancel", "callback_data": "tracking:cancel"},
            ]
        ]
    }


def tracking_running_markup() -> dict:
    return {
        "inline_keyboard": [
            [{"text": "Cancel Tracking Update", "callback_data": "tracking:cancel_running"}],
        ]
    }


def quote_help_text() -> str:
    return (
        "Upload a picklist image. I will read the Ship To address and pallet lines automatically, "
        "then ask you to confirm before running rates."
    )


def targets_status_text(brokers: list[str], direct: list[str]) -> str:
    broker_labels = [label for value, label in BROKER_OPTIONS if value in brokers]
    direct_labels = [label for value, label in DIRECT_CARRIER_OPTIONS if value in direct]
    return (
        "Current Telegram quote targets:\n"
        f"Brokers: {', '.join(broker_labels) if broker_labels else 'None'}\n"
        f"Carriers: {', '.join(direct_labels) if direct_labels else 'None'}"
    )


def pending_quote_summary(fields: dict) -> str:
    summary = (
        f"I read:\n"
        f"Company: {fields['company']}\n"
        f"Pallet qty: {fields['pallet_qty']}\n"
        f"Pieces: {fields.get('pieces') or fields['pallet_qty']}\n"
        f"Weight: {fields['weight']} lb"
    )
    pallet_lines = fields.get("pallet_lines") or []
    if len(pallet_lines) > 1:
        line_text = []
        for idx, line in enumerate(pallet_lines, start=1):
            length, width, height = line["dimensions"]
            line_text.append(
                f"#{idx}: {line['weight']} lb, {length} x {width} x {height}, pieces {line.get('pieces') or 1}, pallets {line.get('pallet_qty') or 1}"
            )
        summary += "\nPallet lines:\n" + "\n".join(line_text)
    else:
        summary += f"\nDimensions: {fields['dimensions'][0]} x {fields['dimensions'][1]} x {fields['dimensions'][2]}"
    if fields.get("ship_to_zip"):
        summary += f"\nShip to ZIP: {fields['ship_to_zip']}"
    return summary


def quote_confirmation_text(fields: dict) -> str:
    return pending_quote_summary(fields) + f"\n\nI will send the Top {quote_result_limit(fields)} cheapest. Confirm before I run the rate."


def edit_quote_template(fields: dict) -> str:
    pallet_lines = fields.get("pallet_lines") or []
    lines = [
        "Send the corrected values like this:",
        "",
        f"Company: {fields.get('company') or ''}",
        f"Ship to ZIP: {fields.get('ship_to_zip') or ''}",
    ]
    if len(pallet_lines) > 1:
        for idx, line in enumerate(pallet_lines, start=1):
            length, width, height = line["dimensions"]
            lines.extend(
                [
                    f"Line {idx} weight: {line['weight']}",
                    f"Line {idx} dimensions: {length} x {width} x {height}",
                    f"Line {idx} pieces: {line.get('pieces') or 1}",
                    f"Line {idx} pallets: {line.get('pallet_qty') or 1}",
                ]
            )
    else:
        lines.extend(
            [
                f"Weight: {fields.get('weight') or ''}",
                f"Dimensions: {fields['dimensions'][0]} x {fields['dimensions'][1]} x {fields['dimensions'][2]}",
                f"Pieces: {fields.get('pieces') or ''}",
                f"Pallet qty: {fields.get('pallet_qty') or ''}",
            ]
        )
    return "\n".join(lines)


def find_company_profile(company_name: str, profiles: dict) -> tuple[Optional[str], Optional[dict], list[str]]:
    target = normalize_lookup(company_name)
    if not target:
        return None, None, []
    normalized = {normalize_lookup(name): name for name in profiles}
    exact_name = normalized.get(target)
    if exact_name:
        return exact_name, profile_data(profiles[exact_name]), []

    matches = [name for name in profiles if target in normalize_lookup(name)]
    if len(matches) == 1:
        return matches[0], profile_data(profiles[matches[0]]), []
    return None, None, sorted(matches)[:8]


def parse_number(value: str) -> Optional[float]:
    match = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    return float(match.group(0)) if match else None


def parse_int(value: str) -> Optional[int]:
    number = parse_number(value)
    return int(number) if number is not None else None


def parse_dimensions(value: str) -> Optional[tuple[float, float, float]]:
    numbers = re.findall(r"\d+(?:\.\d+)?", str(value or ""))
    if len(numbers) < 3:
        return None
    return float(numbers[0]), float(numbers[1]), float(numbers[2])


def parse_float_value(value) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return parse_number(str(value))


def parse_int_value(value) -> Optional[int]:
    number = parse_float_value(value)
    return int(number) if number is not None else None


def parse_json_object(text: str) -> dict:
    raw = str(text or "").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw, flags=re.IGNORECASE)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", raw, flags=re.DOTALL)
        if not match:
            raise
        payload = json.loads(match.group(0))
    return payload if isinstance(payload, dict) else {}


def openai_client():
    api_key = (os.getenv("OPENAI_API_KEY") or os.getenv("OPEN_AI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is missing from .env")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("The openai package is not installed.") from exc
    return OpenAI(api_key=api_key)


def extract_picklist_quote_fields(image_bytes: bytes, content_type: str) -> dict:
    encoded = base64.b64encode(image_bytes).decode("ascii")
    response = openai_client().responses.create(
        model=OPENAI_MODEL,
        instructions="You extract structured data from freight pick list images. Return only valid JSON.",
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": IMAGE_QUOTE_PROMPT},
                    {"type": "input_image", "image_url": f"data:{content_type};base64,{encoded}"},
                ],
            }
        ],
        text={"verbosity": "medium"},
    )
    data = parse_json_object(getattr(response, "output_text", "") or "")
    return fields_from_picklist_data(data)


def normalize_pallet_lines(data: dict) -> list[dict]:
    raw_lines = data.get("pallet_lines")
    if not isinstance(raw_lines, list):
        raw_lines = []
    if not raw_lines and any(data.get(key) is not None for key in ("weight_lb", "length_in", "width_in", "height_in", "box_qty", "pallet_qty")):
        raw_lines = [data]

    lines = []
    for raw in raw_lines:
        if not isinstance(raw, dict):
            continue
        length = parse_float_value(raw.get("length_in"))
        width = parse_float_value(raw.get("width_in"))
        height = parse_float_value(raw.get("height_in"))
        weight = parse_float_value(raw.get("weight_lb"))
        pallet_qty = parse_int_value(raw.get("pallet_qty")) or 1
        pieces = parse_int_value(raw.get("box_qty")) or parse_int_value(raw.get("pieces"))
        if not all(value is not None for value in (length, width, height, weight)):
            continue
        lines.append(
            {
                "pallet_qty": max(1, pallet_qty),
                "pieces": pieces if pieces is not None else 1,
                "weight": weight,
                "dimensions": (length, width, height),
            }
        )
    return lines


def is_windgate_company(value: str) -> bool:
    normalized = normalize_lookup(value)
    return bool(normalized) and "windgate" in normalized


def looks_like_address_line(value: str) -> bool:
    text = str(value or "").strip()
    return bool(
        re.search(r"\d", text)
        or re.search(
            r"\b(ave|avenue|st|street|dr|drive|rd|road|blvd|boulevard|ln|lane|ct|court|way|dock|suite|ste|unit)\b",
            text,
            re.I,
        )
    )


def first_ship_to_company_line(data: dict) -> str:
    candidates: list[str] = []
    for key in ("ship_to_company", "ship_to_name", "ship_to_address"):
        value = data.get(key)
        if isinstance(value, str):
            candidates.extend(line.strip() for line in value.splitlines())
    if isinstance(data.get("ship_to"), str):
        candidates.extend(line.strip() for line in data["ship_to"].splitlines())
    for line in candidates:
        if not line or is_windgate_company(line) or looks_like_address_line(line):
            continue
        return line
    return ""


def expand_pallet_items(pallet_lines: list[dict]) -> list[dict]:
    items = []
    for line in pallet_lines:
        qty = max(1, int(line.get("pallet_qty") or 1))
        length, width, height = line["dimensions"]
        pieces = int(line.get("pieces") or 1)
        weight = float(line["weight"])
        for _ in range(qty):
            items.append(
                {
                    "pallet_number": len(items) + 1,
                    "pieces": pieces,
                    "length_in": length,
                    "width_in": width,
                    "height_in": height,
                    "weight_lb": weight,
                    "freight_class": "",
                }
            )
    return items


def fields_from_picklist_data(data: dict) -> dict:
    if data.get("is_picklist") is False:
        raise PicklistNotFoundError("This image does not look like a picklist.")
    company = str(data.get("company") or "").strip()
    if is_windgate_company(company):
        company = first_ship_to_company_line(data)
    pallet_lines = normalize_pallet_lines(data)
    first_line = pallet_lines[0] if pallet_lines else {}
    dimensions = None
    if first_line.get("dimensions"):
        dimensions = first_line["dimensions"]
    parsed = {
        "company": company,
        "ship_to_address": str(data.get("ship_to_address") or "").strip(),
        "ship_to_city": str(data.get("ship_to_city") or "").strip(),
        "ship_to_state": str(data.get("ship_to_state") or "").strip(),
        "ship_to_zip": str(data.get("ship_to_zip") or "").strip(),
        "pallet_qty": sum(int(line.get("pallet_qty") or 1) for line in pallet_lines) if pallet_lines else None,
        "pieces": sum(int(line.get("pieces") or 0) * int(line.get("pallet_qty") or 1) for line in pallet_lines) if pallet_lines else None,
        "weight": sum(float(line.get("weight") or 0) * int(line.get("pallet_qty") or 1) for line in pallet_lines) if pallet_lines else None,
        "dimensions": dimensions,
        "pallet_lines": pallet_lines,
        "notes": str(data.get("notes") or "").strip(),
    }
    return parsed


def parse_pallet_lines_text(text: str) -> list[dict]:
    lines = []
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if not re.match(r"^(?:#\s*)?\d+\s*:", line):
            continue
        _, value = line.split(":", 1)
        weight_match = re.search(r"(\d+(?:\.\d+)?)\s*(?:lb|lbs|pound|pounds)\b", value, re.I)
        dimensions_value = re.sub(r"\d+(?:\.\d+)?\s*(?:lb|lbs|pound|pounds)\b", "", value, count=1, flags=re.I)
        dimensions = parse_dimensions(dimensions_value)
        pieces_match = re.search(r"\b(?:pieces|piece|box\s*qty|boxes)\s*[:=]?\s*(\d+)", value, re.I)
        pallets_match = re.search(r"\b(?:pallets?|pallet\s*qty)\s*[:=]?\s*(\d+)", value, re.I)
        weight = parse_float_value(weight_match.group(1)) if weight_match else None
        pieces = parse_int_value(pieces_match.group(1)) if pieces_match else None
        pallet_qty = parse_int_value(pallets_match.group(1)) if pallets_match else 1
        if not dimensions or weight is None:
            continue
        length, width, height = dimensions
        lines.append(
            {
                "weight_lb": weight,
                "length_in": length,
                "width_in": width,
                "height_in": height,
                "box_qty": pieces if pieces is not None else 1,
                "pallet_qty": pallet_qty or 1,
            }
        )
    return normalize_pallet_lines({"pallet_lines": lines})


def refresh_fields_from_pallet_lines(fields: dict, pallet_lines: list[dict]) -> dict:
    refreshed = fields_from_picklist_data(
        {
            "company": fields.get("company", ""),
            "ship_to_address": fields.get("ship_to_address", ""),
            "ship_to_city": fields.get("ship_to_city", ""),
            "ship_to_state": fields.get("ship_to_state", ""),
            "ship_to_zip": fields.get("ship_to_zip", ""),
            "pallet_lines": [
                {
                    "weight_lb": line.get("weight"),
                    "length_in": (line.get("dimensions") or [None, None, None])[0],
                    "width_in": (line.get("dimensions") or [None, None, None])[1],
                    "height_in": (line.get("dimensions") or [None, None, None])[2],
                    "box_qty": line.get("pieces"),
                    "pallet_qty": line.get("pallet_qty"),
                }
                for line in pallet_lines
            ],
        }
    )
    refreshed["report_limit"] = quote_result_limit(fields)
    return refreshed


def parse_quote_fields(text: str) -> tuple[dict, list[str]]:
    fields: dict[str, str] = {}
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or line.lower().startswith("/quote"):
            continue
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = normalize_lookup(key)
        fields[key] = value.strip()

    company = fields.get("company") or fields.get("company name")
    pallet_qty = parse_int(fields.get("pallet qty") or fields.get("pallets") or fields.get("pallet quantity") or "")
    pieces = parse_int(fields.get("pieces") or "")
    weight = parse_number(fields.get("weight") or "")
    dimensions = parse_dimensions(fields.get("dimensions") or fields.get("dims") or "")
    pallet_lines = parse_pallet_lines_text(text)
    if pallet_lines:
        first_line = pallet_lines[0]
        pallet_qty = sum(int(line.get("pallet_qty") or 1) for line in pallet_lines)
        pieces = sum(int(line.get("pieces") or 0) * int(line.get("pallet_qty") or 1) for line in pallet_lines)
        weight = sum(float(line.get("weight") or 0) * int(line.get("pallet_qty") or 1) for line in pallet_lines)
        dimensions = first_line.get("dimensions")

    parsed = {
        "company": company or "",
        "pallet_qty": pallet_qty,
        "pieces": pieces if pieces is not None else pallet_qty,
        "weight": weight,
        "dimensions": dimensions,
        "pallet_lines": pallet_lines,
        "ship_to_zip": fields.get("ship to zip") or fields.get("zip") or fields.get("destination zip") or "",
    }
    missing = []
    if not parsed["company"]:
        missing.append("Company")
    if not parsed["pallet_qty"]:
        missing.append("Pallet qty")
    if not parsed["weight"]:
        missing.append("Weight")
    if not parsed["dimensions"]:
        missing.append("Dimensions")
    return parsed, missing


def quote_payload_from_fields(
    fields: dict,
    profile: dict,
    company_name: str,
    *,
    broker_targets: Optional[list[str]] = None,
    direct_carrier_targets: Optional[list[str]] = None,
) -> dict:
    length, width, height = fields["dimensions"]
    pallet_qty = int(fields["pallet_qty"])
    pieces = int(fields.get("pieces") or pallet_qty)
    pallet_items = expand_pallet_items(fields.get("pallet_lines") or [])
    return {
        "company_name": company_name,
        "broker_targets": broker_targets if broker_targets is not None else DEFAULT_BROKER_TARGETS,
        "direct_carrier_targets": direct_carrier_targets if direct_carrier_targets is not None else DEFAULT_DIRECT_CARRIER_TARGETS,
        "browser_visibility": "CONCEAL",
        "origin_zip": str(profile.get("origin_zip") or "91342"),
        "pickup_city": str(profile.get("pickup_city") or "SYLMAR, CA"),
        "destination_zip": str(profile.get("destination_zip") or ""),
        "delivery_city": str(profile.get("delivery_city") or ""),
        "pallet_count": pallet_qty,
        "pieces": pieces,
        "weight_lb": float(fields["weight"]),
        "length_in": length,
        "width_in": width,
        "height_in": height,
        "freight_class": "",
        "shipment_date": date.today().isoformat(),
        "pallet_items": pallet_items if len(pallet_items) > 1 else [],
    }


def profile_from_extracted_ship_to(fields: dict) -> dict:
    city_state = ", ".join(item for item in [fields.get("ship_to_city"), fields.get("ship_to_state")] if item)
    return {
        "origin_zip": "91342",
        "pickup_city": "SYLMAR, CA",
        "destination_zip": str(fields.get("ship_to_zip") or ""),
        "delivery_city": city_state,
    }


def post_local_json(path: str, payload: dict, *, timeout: int = 900, opener: Callable = urlopen) -> dict:
    request = Request(
        f"{LOCAL_APP_URL}{path}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with opener(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Local app rejected the request: {detail}") from exc
    except URLError as exc:
        raise RuntimeError(f"Could not reach local app at {LOCAL_APP_URL}: {exc.reason}") from exc


def missing_quote_fields(fields: dict) -> list[str]:
    missing = []
    if not fields.get("company"):
        missing.append("Company")
    if not fields.get("pallet_qty"):
        missing.append("Pallet qty")
    if not fields.get("weight"):
        missing.append("Weight")
    if not fields.get("dimensions"):
        missing.append("Dimensions")
    return missing


def save_pending_quote(chat_id: str, fields: dict, *, awaiting_edit: bool = False):
    state = load_bot_state()
    chat = chat_state(state, chat_id)
    fields = dict(fields)
    fields["report_limit"] = quote_result_limit(fields)
    chat["pending_quote"] = fields
    chat["pending_quote_saved_at"] = time.time()
    chat.pop("pending_quote_expired_notice_sent", None)
    chat["awaiting_edit"] = awaiting_edit
    save_bot_state(state)


def pop_pending_quote(chat_id: str) -> Optional[dict]:
    state = load_bot_state()
    chat = chat_state(state, chat_id)
    pending = chat.pop("pending_quote", None)
    chat.pop("pending_quote_saved_at", None)
    chat.pop("pending_quote_expired_notice_sent", None)
    chat.pop("awaiting_edit", None)
    save_bot_state(state)
    return pending if isinstance(pending, dict) else None


def pending_quote_expired(chat: dict, *, now: Optional[float] = None) -> bool:
    if not isinstance(chat.get("pending_quote"), dict):
        return False
    saved_at = float(chat.get("pending_quote_saved_at") or 0)
    if not saved_at:
        return False
    return (now or time.time()) - saved_at >= PENDING_QUOTE_TTL_SECONDS


def expire_pending_quote(chat_id: str) -> bool:
    state = load_bot_state()
    chat = chat_state(state, chat_id)
    if not pending_quote_expired(chat):
        return False
    chat.pop("pending_quote", None)
    chat.pop("pending_quote_saved_at", None)
    chat.pop("awaiting_edit", None)
    chat["pending_quote_expired_notice_sent"] = time.time()
    save_bot_state(state)
    return True


def get_pending_quote(chat_id: str) -> Optional[dict]:
    if expire_pending_quote(chat_id):
        return None
    pending = chat_state(load_bot_state(), chat_id).get("pending_quote")
    return pending if isinstance(pending, dict) else None


def chat_awaiting_edit(chat_id: str) -> bool:
    if expire_pending_quote(chat_id):
        return False
    return bool(chat_state(load_bot_state(), chat_id).get("awaiting_edit"))


def expire_pending_quotes(config: TelegramConfig):
    state = load_bot_state()
    changed = False
    notices: list[str] = []
    for chat_id, chat in (state.get("chats") or {}).items():
        if not isinstance(chat, dict) or not pending_quote_expired(chat):
            continue
        chat.pop("pending_quote", None)
        chat.pop("pending_quote_saved_at", None)
        chat.pop("awaiting_edit", None)
        if not chat.get("pending_quote_expired_notice_sent"):
            chat["pending_quote_expired_notice_sent"] = time.time()
            notices.append(str(chat_id))
        changed = True
    if changed:
        save_bot_state(state)
    for chat_id in notices:
        try:
            send_telegram_message(
                chat_id,
                "Quote request expired. You have 15 minutes to confirm after I read a picklist. Please upload the picklist again to start over.",
                config=config,
                reply_markup=MENU_KEYBOARD,
            )
        except Exception as exc:
            print(f"Telegram expiry notice failed for {chat_id}: {exc}", flush=True)


def apply_quote_edits(fields: dict, text: str) -> tuple[dict, list[str]]:
    updated = json.loads(json.dumps(fields))
    replacement_pallet_lines = parse_pallet_lines_text(text)
    line_edits: dict[int, dict[str, str]] = {}
    top_fields: dict[str, str] = {}
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        key, value = line.split(":", 1)
        normalized_key = normalize_lookup(key)
        value = value.strip()
        match = re.match(r"line\s+(\d+)\s+(weight|dimensions|pieces|pallets?|pallet qty)", normalized_key)
        if match:
            idx = int(match.group(1)) - 1
            line_edits.setdefault(idx, {})[match.group(2)] = value
        else:
            top_fields[normalized_key] = value

    if top_fields.get("company"):
        updated["company"] = top_fields["company"].strip()
    if top_fields.get("ship to zip") or top_fields.get("zip") or top_fields.get("destination zip"):
        updated["ship_to_zip"] = (top_fields.get("ship to zip") or top_fields.get("zip") or top_fields.get("destination zip") or "").strip()
    if replacement_pallet_lines:
        refreshed = refresh_fields_from_pallet_lines(updated, replacement_pallet_lines)
        return refreshed, missing_quote_fields(refreshed)

    pallet_lines = updated.get("pallet_lines") if isinstance(updated.get("pallet_lines"), list) else []
    if pallet_lines and line_edits:
        for idx, edits in line_edits.items():
            if idx < 0 or idx >= len(pallet_lines):
                continue
            line = pallet_lines[idx]
            if edits.get("weight"):
                line["weight"] = parse_float_value(edits["weight"]) or line.get("weight")
            if edits.get("dimensions"):
                line["dimensions"] = parse_dimensions(edits["dimensions"]) or line.get("dimensions")
            if edits.get("pieces"):
                line["pieces"] = parse_int_value(edits["pieces"]) or line.get("pieces")
            pallet_value = edits.get("pallets") or edits.get("pallet") or edits.get("pallet qty")
            if pallet_value:
                line["pallet_qty"] = parse_int_value(pallet_value) or line.get("pallet_qty") or 1
        refreshed = refresh_fields_from_pallet_lines(updated, pallet_lines)
        return refreshed, missing_quote_fields(refreshed)

    single_fields = []
    if top_fields.get("weight"):
        updated["weight"] = parse_float_value(top_fields["weight"])
    if top_fields.get("dimensions") or top_fields.get("dims"):
        updated["dimensions"] = parse_dimensions(top_fields.get("dimensions") or top_fields.get("dims") or "")
    if top_fields.get("pieces"):
        updated["pieces"] = parse_int_value(top_fields["pieces"])
    if top_fields.get("pallet qty") or top_fields.get("pallets"):
        updated["pallet_qty"] = parse_int_value(top_fields.get("pallet qty") or top_fields.get("pallets") or "")
    if updated.get("dimensions") and updated.get("weight"):
        length, width, height = updated["dimensions"]
        single_fields.append(
            {
                "weight_lb": updated.get("weight"),
                "length_in": length,
                "width_in": width,
                "height_in": height,
                "box_qty": updated.get("pieces"),
                "pallet_qty": updated.get("pallet_qty"),
            }
        )
        updated["pallet_lines"] = normalize_pallet_lines({"pallet_lines": single_fields})
    return updated, missing_quote_fields(updated)


def run_quote_from_fields(chat_id: str, fields: dict, config: TelegramConfig, *, prefer_extracted_address: bool = False):
    state = load_bot_state()
    broker_targets, direct_carrier_targets = chat_quote_targets(state, chat_id)
    if not broker_targets and not direct_carrier_targets:
        send_telegram_message(
            chat_id,
            "No brokers or carriers are selected. Please tap Select Carriers/Brokers and choose at least one.",
            config=config,
            reply_markup=target_selection_markup(broker_targets, direct_carrier_targets),
        )
        return
    extracted_profile = profile_from_extracted_ship_to(fields)
    if prefer_extracted_address and extracted_profile["destination_zip"]:
        company_name = fields["company"]
        profile = extracted_profile
        payload = quote_payload_from_fields(
            fields,
            profile,
            company_name,
            broker_targets=broker_targets,
            direct_carrier_targets=direct_carrier_targets,
        )
        send_telegram_message(chat_id, f"Running quote for {company_name}. I will send the report here when it finishes.", config=config)
        run_result = post_local_json("/api/run", payload)
        if not run_result.get("ok"):
            send_telegram_message(chat_id, f"Quote failed: {run_result.get('message') or run_result}", config=config)
            return
        report_payload = {
            "batch_id": run_result.get("batch_id", ""),
            "results": run_result.get("results") or [],
            "limit": quote_result_limit(fields),
        }
        post_local_json("/api/telegram/report", report_payload, timeout=60)
        return

    company_name, profile, matches = find_company_profile(fields["company"], load_profiles())
    if not profile:
        if matches:
            send_telegram_message(chat_id, "I found multiple companies:\n" + "\n".join(matches) + "\n\nPlease resend with the exact company name.", config=config)
            return
        if extracted_profile["destination_zip"]:
            company_name = fields["company"]
            profile = extracted_profile
        else:
            send_telegram_message(chat_id, f"I could not find saved company or read a destination ZIP for: {fields['company']}", config=config)
            return

    payload = quote_payload_from_fields(
        fields,
        profile,
        company_name or fields["company"],
        broker_targets=broker_targets,
        direct_carrier_targets=direct_carrier_targets,
    )
    if not payload["destination_zip"]:
        send_telegram_message(chat_id, f"Saved company {company_name} is missing destination ZIP.", config=config)
        return

    send_telegram_message(chat_id, f"Running quote for {company_name}. I will send the report here when it finishes.", config=config)
    run_result = post_local_json("/api/run", payload)
    if not run_result.get("ok"):
        send_telegram_message(chat_id, f"Quote failed: {run_result.get('message') or run_result}", config=config)
        return
    report_payload = {
        "batch_id": run_result.get("batch_id", ""),
        "results": run_result.get("results") or [],
        "limit": quote_result_limit(fields),
    }
    post_local_json("/api/telegram/report", report_payload, timeout=60)


def tracking_job_for_chat(chat_id: str) -> Optional[dict]:
    job = TRACKING_JOBS.get(str(chat_id))
    if not job:
        return None
    thread = job.get("thread")
    if isinstance(thread, threading.Thread) and thread.is_alive():
        return job
    TRACKING_JOBS.pop(str(chat_id), None)
    return None


def run_tracking_update_job(chat_id: str, config: TelegramConfig, cancel_key: str):
    try:
        result = post_local_json(
            "/api/update-tracking",
            {"force_recent": True, "skip_captcha": True, "cancel_key": cancel_key},
            timeout=900,
        )
    except Exception as exc:
        job = TRACKING_JOBS.get(str(chat_id)) or {}
        if job.get("cancel_requested"):
            send_telegram_message(chat_id, "Tracking update was cancelled.", config=config, reply_markup=MENU_KEYBOARD)
        else:
            send_telegram_message(chat_id, f"Tracking update failed: {exc}", config=config, reply_markup=MENU_KEYBOARD)
        TRACKING_JOBS.pop(str(chat_id), None)
        return
    cancelled = bool(result.get("cancelled"))
    summary = (
        ("Tracking update cancelled.\n" if cancelled else "Tracking update finished.\n") +
        f"Rows checked: {result.get('checked', 0)}\n"
        f"ETA updated: {result.get('updated_eta', 0)}\n"
        f"Actual updated: {result.get('updated_actual', 0)}\n"
        f"Notes updated: {result.get('updated_notes', 0)}"
    )
    skipped = result.get("skipped") or 0
    if skipped:
        summary += f"\nSkipped: {skipped}"
    send_telegram_message(chat_id, summary, config=config, reply_markup=MENU_KEYBOARD)
    TRACKING_JOBS.pop(str(chat_id), None)


def start_tracking_update(chat_id: str, config: TelegramConfig):
    if tracking_job_for_chat(chat_id):
        send_telegram_message(
            chat_id,
            "A tracking update is already running. You can cancel it or keep working on quotes.",
            config=config,
            reply_markup=tracking_running_markup(),
        )
        return
    cancel_key = uuid.uuid4().hex
    thread = threading.Thread(
        target=run_tracking_update_job,
        args=(chat_id, config, cancel_key),
        daemon=True,
    )
    TRACKING_JOBS[str(chat_id)] = {"cancel_key": cancel_key, "thread": thread, "cancel_requested": False}
    thread.start()
    send_telegram_message(
        chat_id,
        "Tracking update started in the background. You can keep quoting while it runs.",
        config=config,
        reply_markup=tracking_running_markup(),
    )


def cancel_tracking_update(chat_id: str, config: TelegramConfig):
    job = tracking_job_for_chat(chat_id)
    if not job:
        send_telegram_message(chat_id, "No tracking update is running right now.", config=config, reply_markup=MENU_KEYBOARD)
        return
    job["cancel_requested"] = True
    cancel_key = str(job.get("cancel_key") or "")
    try:
        post_local_json("/api/update-tracking/cancel", {"cancel_key": cancel_key}, timeout=20)
    except Exception as exc:
        send_telegram_message(chat_id, f"I tried to cancel tracking, but the app did not answer: {exc}", config=config)
        return
    send_telegram_message(
        chat_id,
        "Cancel requested. Tracking will stop after the current row/page finishes.",
        config=config,
        reply_markup=MENU_KEYBOARD,
    )


def ask_tracking_confirmation(chat_id: str, config: TelegramConfig):
    send_telegram_message(
        chat_id,
        "Start updating the TRACKING tab now? This can take a while. Captcha rows will be skipped for manual check.",
        config=config,
        reply_markup=tracking_confirmation_markup(),
    )


def handle_callback_query(callback_query: dict, config: TelegramConfig):
    callback_id = str(callback_query.get("id") or "")
    message = callback_query.get("message") or {}
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    data = str(callback_query.get("data") or "")
    if callback_id:
        try:
            answer_callback_query(config, callback_id)
        except Exception:
            pass
    if not chat_id or (config.allowed_chat_ids and chat_id not in config.allowed_chat_ids):
        return

    state = load_bot_state()
    if data == "menu:quote":
        send_telegram_message(chat_id, quote_help_text(), config=config, reply_markup=MENU_KEYBOARD)
        return
    if data == "menu:targets":
        brokers, direct = chat_quote_targets(state, chat_id)
        send_telegram_message(chat_id, targets_status_text(brokers, direct), config=config, reply_markup=target_selection_markup(brokers, direct))
        return
    if data == "menu:tracking":
        ask_tracking_confirmation(chat_id, config)
        return
    if data == "tracking:confirm":
        start_tracking_update(chat_id, config)
        return
    if data == "tracking:cancel":
        send_telegram_message(chat_id, "Tracking update cancelled.", config=config, reply_markup=MENU_KEYBOARD)
        return
    if data == "tracking:cancel_running":
        cancel_tracking_update(chat_id, config)
        return

    if data.startswith("target:"):
        brokers, direct = chat_quote_targets(state, chat_id)
        parts = data.split(":", 2)
        action = parts[1] if len(parts) > 1 else ""
        value = parts[2] if len(parts) > 2 else ""
        if action == "broker" and value:
            brokers = [item for item in brokers if item != value] if value in brokers else brokers + [value]
        elif action == "direct" and value:
            direct = [item for item in direct if item != value] if value in direct else direct + [value]
        elif action == "all":
            brokers = [value for value, _ in BROKER_OPTIONS]
            direct = [value for value, _ in DIRECT_CARRIER_OPTIONS]
        elif action == "defaults":
            brokers = DEFAULT_BROKER_TARGETS[:]
            direct = DEFAULT_DIRECT_CARRIER_TARGETS[:]
        elif action == "clear":
            brokers = []
            direct = []
        elif action == "done":
            send_telegram_message(chat_id, targets_status_text(brokers, direct), config=config, reply_markup=MENU_KEYBOARD)
            pending = get_pending_quote(chat_id)
            if pending:
                send_telegram_message(
                    chat_id,
                    quote_confirmation_text(pending),
                    config=config,
                    reply_markup=quote_confirmation_markup(pending),
                )
            else:
                send_telegram_message(
                    chat_id,
                    "No pending quote is waiting. Upload a picklist image to start a new one.",
                    config=config,
                    reply_markup=MENU_KEYBOARD,
                )
            return
        set_chat_quote_targets(state, chat_id, brokers, direct)
        send_telegram_message(chat_id, targets_status_text(brokers, direct), config=config, reply_markup=target_selection_markup(brokers, direct))
        return

    if data == "quote:confirm":
        pending = pop_pending_quote(chat_id)
        if not pending:
            send_telegram_message(
                chat_id,
                "There is no pending quote to confirm. Quote requests expire after 15 minutes. Please upload the picklist again.",
                config=config,
                reply_markup=MENU_KEYBOARD,
            )
            return
        run_quote_from_fields(chat_id, pending, config, prefer_extracted_address=True)
        return
    if data == "quote:edit":
        pending = get_pending_quote(chat_id)
        if not pending:
            send_telegram_message(
                chat_id,
                "There is no pending quote to edit. Quote requests expire after 15 minutes. Please upload the picklist again.",
                config=config,
                reply_markup=MENU_KEYBOARD,
            )
            return
        save_pending_quote(chat_id, pending, awaiting_edit=True)
        send_telegram_message(chat_id, edit_quote_template(pending), config=config)
        return
    if data.startswith("quote:limit:"):
        pending = get_pending_quote(chat_id)
        if not pending:
            send_telegram_message(
                chat_id,
                "There is no pending quote to change. Quote requests expire after 15 minutes. Please upload the picklist again.",
                config=config,
                reply_markup=MENU_KEYBOARD,
            )
            return
        try:
            limit = int(data.rsplit(":", 1)[-1])
        except ValueError:
            limit = DEFAULT_QUOTE_RESULT_LIMIT
        pending["report_limit"] = limit if limit in QUOTE_RESULT_LIMITS else DEFAULT_QUOTE_RESULT_LIMIT
        save_pending_quote(chat_id, pending)
        send_telegram_message(
            chat_id,
            quote_confirmation_text(pending),
            config=config,
            reply_markup=quote_confirmation_markup(pending),
        )
        return
    if data == "quote:cancel":
        pop_pending_quote(chat_id)
        send_telegram_message(chat_id, "Quote cancelled.", config=config, reply_markup=MENU_KEYBOARD)


def best_photo_file_id(message: dict) -> str:
    photos = message.get("photo") or []
    if not isinstance(photos, list) or not photos:
        return ""
    best = max(photos, key=lambda item: int((item or {}).get("file_size") or 0))
    return str((best or {}).get("file_id") or "")


def handle_image_quote(message: dict, config: TelegramConfig, chat_id: str, *, announce: bool = True, skip_non_picklist: bool = False):
    if not (message.get("photo") or message.get("document")) and isinstance(message.get("reply_to_message"), dict):
        message = message["reply_to_message"]
    if media_group_already_handled(chat_id, message):
        return
    file_id = best_photo_file_id(message)
    document = message.get("document") or {}
    if not file_id and str(document.get("mime_type") or "").startswith("image/"):
        file_id = str(document.get("file_id") or "")
    if not file_id:
        send_telegram_message(chat_id, "Please attach the picklist image with /quote in the caption.", config=config)
        return

    if announce:
        send_telegram_message(chat_id, "Reading the picklist image now.", config=config)
    try:
        image_bytes, content_type = download_telegram_file(config, file_id)
        fields = extract_picklist_quote_fields(image_bytes, content_type)
    except PicklistNotFoundError as exc:
        if not skip_non_picklist:
            send_telegram_message(chat_id, str(exc), config=config)
        return
    except Exception as exc:
        send_telegram_message(chat_id, f"I could not read the picklist image yet: {exc}", config=config)
        return
    missing = missing_quote_fields(fields)
    if missing:
        if skip_non_picklist and media_group_key(message):
            return
        save_pending_quote(chat_id, fields, awaiting_edit=True)
        if should_send_missing_quote_prompt(chat_id, message, missing):
            send_telegram_message(
                chat_id,
                "I could not read: "
                + ", ".join(missing)
                + "\n\nReply with the missing/corrected details. You can paste pallet lines like this:\n\n"
                + QUOTE_TEMPLATE,
                config=config,
            )
        return
    save_pending_quote(chat_id, fields)
    mark_media_group_handled(chat_id, message)
    send_telegram_message(
        chat_id,
        quote_confirmation_text(fields),
        config=config,
        reply_markup=quote_confirmation_markup(fields),
    )


def handle_message(message: dict, config: TelegramConfig):
    chat = message.get("chat") or {}
    chat_id = str(chat.get("id") or "")
    text = str(message.get("text") or message.get("caption") or "").strip()
    has_media = bool(message.get("photo") or message.get("document"))
    reply = message.get("reply_to_message") if isinstance(message.get("reply_to_message"), dict) else {}
    has_reply_media = bool(reply.get("photo") or reply.get("document"))
    if not chat_id or (not text and not has_media):
        return
    if config.allowed_chat_ids and chat_id not in config.allowed_chat_ids:
        return
    if has_media and not text:
        handle_image_quote(message, config, chat_id, announce=False, skip_non_picklist=True)
        return

    button_text = text.strip().lower()
    if button_text in {"menu", "/menu"}:
        send_telegram_message(chat_id, "Choose an action:", config=config, reply_markup=MENU_KEYBOARD)
        send_telegram_message(chat_id, "Menu:", config=config, reply_markup=main_menu_markup())
        return
    if button_text == "carriers":
        state = load_bot_state()
        brokers, direct = chat_quote_targets(state, chat_id)
        send_telegram_message(chat_id, targets_status_text(brokers, direct), config=config, reply_markup=target_selection_markup(brokers, direct))
        return
    if button_text == "update tracking":
        ask_tracking_confirmation(chat_id, config)
        return
    if button_text == "help":
        send_telegram_message(chat_id, quote_help_text(), config=config, reply_markup=MENU_KEYBOARD)
        return
    if chat_awaiting_edit(chat_id):
        pending = get_pending_quote(chat_id)
        if not pending:
            send_telegram_message(chat_id, "There is no pending quote to edit. Send a picklist image with /quote again.", config=config)
            return
        updated, missing = apply_quote_edits(pending, text)
        if missing:
            save_pending_quote(chat_id, updated, awaiting_edit=True)
            send_telegram_message(chat_id, "I still need: " + ", ".join(missing) + "\n\n" + edit_quote_template(updated), config=config)
            return
        save_pending_quote(chat_id, updated, awaiting_edit=False)
        send_telegram_message(
            chat_id,
            quote_confirmation_text(updated),
            config=config,
            reply_markup=quote_confirmation_markup(updated),
        )
        return

    command = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
    if command in {"/start", "/help"}:
        send_telegram_message(chat_id, "Choose an action:", config=config, reply_markup=MENU_KEYBOARD)
        send_telegram_message(chat_id, quote_help_text(), config=config, reply_markup=main_menu_markup())
        return
    if command == "/menu":
        send_telegram_message(chat_id, "Choose an action:", config=config, reply_markup=MENU_KEYBOARD)
        send_telegram_message(chat_id, "Menu:", config=config, reply_markup=main_menu_markup())
        return
    if command == "/carriers":
        state = load_bot_state()
        brokers, direct = chat_quote_targets(state, chat_id)
        send_telegram_message(chat_id, targets_status_text(brokers, direct), config=config, reply_markup=target_selection_markup(brokers, direct))
        return
    if command == "/tracking":
        ask_tracking_confirmation(chat_id, config)
        return
    if command == "/quote" and len(text.splitlines()) == 1:
        if has_media or has_reply_media:
            handle_image_quote(message, config, chat_id)
            return
        recent = recent_image_message(chat_id)
        if recent:
            handle_image_quote(recent, config, chat_id)
            return
        send_telegram_message(chat_id, QUOTE_TEMPLATE, config=config)
        return
    if command == "/company":
        query = text.replace(text.split()[0], "", 1).strip()
        matches = [name for name in load_profiles() if normalize_lookup(query) in normalize_lookup(name)] if query else []
        if matches:
            send_telegram_message(chat_id, "Found companies:\n" + "\n".join(matches[:10]), config=config)
        else:
            send_telegram_message(chat_id, "Send /company followed by part of the company name.", config=config)
        return

    if command == "/quote" or re.search(r"(?im)^company\s*:", text):
        fields, missing = parse_quote_fields(text)
        if missing:
            send_telegram_message(chat_id, "Missing: " + ", ".join(missing) + "\n\n" + QUOTE_TEMPLATE, config=config)
            return
        run_quote_from_fields(chat_id, fields, config)


def handle_update(update: dict, config: TelegramConfig):
    callback_query = update.get("callback_query")
    if callback_query:
        handle_callback_query(callback_query, config)
        return
    message = update.get("message") or update.get("channel_post")
    if message:
        handle_message(message, config)


def run_bot():
    config = load_telegram_config()
    if not config.enabled:
        raise SystemExit("Telegram is not configured. Set TELEGRAM_BOT_TOKEN and TELEGRAM_DEFAULT_CHAT_ID in .env.")
    try:
        set_bot_commands(config)
        set_bot_menu_button(config)
    except Exception as exc:
        print(f"Telegram command menu setup skipped: {exc}", flush=True)
    state = load_bot_state()
    offset = int(state.get("offset") or 0)
    if not offset and (os.getenv("TELEGRAM_SKIP_OLD_UPDATES") or "true").strip().lower() not in {"0", "false", "no"}:
        old_updates = get_updates(config, 0, timeout=0)
        if old_updates:
            offset = max(int(update.get("update_id") or 0) for update in old_updates) + 1
            save_bot_offset(offset)
    print("Telegram quote bot is running. Press Ctrl+C to stop.", flush=True)
    while True:
        try:
            expire_pending_quotes(config)
            updates = get_updates(config, offset)
            for update in updates:
                update_id = int(update.get("update_id") or 0)
                offset = max(offset, update_id + 1)
                save_bot_offset(offset)
                handle_update(update, config)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            print(f"Telegram bot error: {exc}", flush=True)
            time.sleep(5)
