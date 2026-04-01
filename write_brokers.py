from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials
from sheets_utils import with_gsheets_retry

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

SHEET_NAME = "Freight Quote Agent (MVP)"
INPUT_SHEET = "Input"
CARRIERS_SHEET = "Carriers"
BROKER_RESULT_SHEET = "Broker Result"

START_ROW = 2
CLEAR_TO_ROW = 200
WRITE_COLUMNS = "A:M"


def gs_client():
    creds = Credentials.from_service_account_file("service_account.json", scopes=SCOPES)
    return gspread.authorize(creds)


def build_broker_rows(spreadsheet):
    input_sheet = with_gsheets_retry(lambda: spreadsheet.worksheet(INPUT_SHEET))
    carriers_sheet = with_gsheets_retry(lambda: spreadsheet.worksheet(CARRIERS_SHEET))

    origin_zip = with_gsheets_retry(lambda: input_sheet.acell("B3").value)
    destination_zip = with_gsheets_retry(lambda: input_sheet.acell("B4").value)
    origin_city = with_gsheets_retry(lambda: input_sheet.acell("B14").value)
    destination_city = with_gsheets_retry(lambda: input_sheet.acell("B15").value)
    freight_class = with_gsheets_retry(lambda: input_sheet.acell("B13").value)
    weight_lb = with_gsheets_retry(lambda: input_sheet.acell("B9").value)

    all_rows = with_gsheets_retry(carriers_sheet.get_all_values)
    broker_rows = all_rows[2:] if len(all_rows) > 2 else []

    now = datetime.now()
    batch_id = now.strftime("%Y%m%d-%H%M%S")
    quote_time = now.strftime("%Y-%m-%d %H:%M:%S")

    rows_to_write = []
    for row in broker_rows:
        if len(row) < 2:
            continue

        broker_name = row[0].strip()
        broker_website = row[1].strip()
        if not broker_name:
            continue

        rows_to_write.append([
            batch_id,
            broker_name,
            "",
            "",
            "",
            quote_time,
            origin_zip,
            origin_city,
            destination_zip,
            destination_city,
            freight_class,
            weight_lb,
            f"Pending quote: {broker_website}",
        ])

    return {
        "batch_id": batch_id,
        "quote_time": quote_time,
        "rows": rows_to_write,
    }


def write_brokers(spreadsheet=None):
    spreadsheet = spreadsheet or with_gsheets_retry(lambda: gs_client().open(SHEET_NAME))
    broker_result = with_gsheets_retry(lambda: spreadsheet.worksheet(BROKER_RESULT_SHEET))

    payload = build_broker_rows(spreadsheet)
    rows_to_write = payload["rows"]
    if not rows_to_write:
        return {
            "ok": False,
            "message": "No brokers found in Carriers tab.",
            "batch_id": payload["batch_id"],
            "row_count": 0,
        }

    start_col, end_col = WRITE_COLUMNS.split(":")
    with_gsheets_retry(lambda: broker_result.batch_clear([f"{start_col}{START_ROW}:{end_col}{CLEAR_TO_ROW}"]))

    end_row = START_ROW + len(rows_to_write) - 1
    target_range = f"A{START_ROW}:M{end_row}"
    with_gsheets_retry(lambda: broker_result.update(target_range, rows_to_write))

    return {
        "ok": True,
        "message": f"Wrote {len(rows_to_write)} broker rows.",
        "batch_id": payload["batch_id"],
        "quote_time": payload["quote_time"],
        "target_range": target_range,
        "row_count": len(rows_to_write),
    }


def main():
    result = write_brokers()
    if not result["ok"]:
        print(result["message"])
        raise SystemExit(0)

    print(f"✅ {result['message']}")
    print(f"Batch ID: {result['batch_id']}")
    print(f"Range: {result['target_range']}")


if __name__ == "__main__":
    main()
