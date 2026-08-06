import json

import pytest

from webapp.telegram_utils import TelegramConfig
from webapp.telegram_utils import parse_chat_ids
from webapp.telegram_utils import send_telegram_message
from webapp.telegram_utils import telegram_config_status
from webapp.server import telegram_quote_report_text
from webapp.telegram_bot import find_company_profile
from webapp.telegram_bot import fields_from_picklist_data
from webapp.telegram_bot import PicklistNotFoundError
from webapp.telegram_bot import apply_quote_edits
from webapp.telegram_bot import recent_image_message
from webapp.telegram_bot import remember_recent_image
from webapp.telegram_bot import save_bot_offset
from webapp.telegram_bot import save_pending_quote
from webapp.telegram_bot import expire_pending_quote
from webapp.telegram_bot import get_pending_quote
from webapp.telegram_bot import missing_quote_fields
from webapp.telegram_bot import merge_caption_fields
from webapp.telegram_bot import parse_caption_quote_fields
from webapp.telegram_bot import parse_quote_fields
from webapp.telegram_bot import parse_json_object
from webapp.telegram_bot import profile_from_extracted_ship_to
from webapp.telegram_bot import quote_payload_from_fields
from webapp.telegram_bot import quote_confirmation_markup
from webapp.telegram_bot import quote_result_limit
from webapp.telegram_bot import tracking_confirmation_markup
from webapp.telegram_bot import chat_quote_targets
from webapp.telegram_bot import set_chat_quote_targets
from webapp.telegram_bot import should_send_missing_quote_prompt
from webapp.telegram_bot import mark_media_group_handled
from webapp.telegram_bot import media_group_already_handled


def test_parse_chat_ids_trims_and_ignores_blanks():
    assert parse_chat_ids(" 111, ,222,333 ") == {"111", "222", "333"}


def test_telegram_status_reports_missing_config():
    status = telegram_config_status(TelegramConfig(bot_token="", default_chat_id="", allowed_chat_ids=set()))

    assert status == {
        "enabled": False,
        "has_bot_token": False,
        "has_default_chat_id": False,
        "allowed_chat_count": 0,
    }


def test_send_telegram_message_builds_send_message_request():
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b'{"ok": true, "result": {"message_id": 1}}'

    def fake_opener(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    payload = send_telegram_message(
        "123",
        "hello",
        config=TelegramConfig(bot_token="abc:token", default_chat_id="123", allowed_chat_ids={"123"}),
        opener=fake_opener,
    )

    assert payload["ok"] is True
    assert captured["url"].endswith("/botabc:token/sendMessage")
    assert captured["timeout"] == 20
    assert captured["body"] == {
        "chat_id": "123",
        "text": "hello",
        "disable_web_page_preview": True,
    }


def test_send_telegram_message_rejects_unapproved_chat_id():
    with pytest.raises(PermissionError):
        send_telegram_message(
            "999",
            "hello",
            config=TelegramConfig(bot_token="abc:token", default_chat_id="123", allowed_chat_ids={"123"}),
        )


def test_telegram_quote_report_text_summarizes_top_quotes():
    text = telegram_quote_report_text(
        "20260727-123456",
        [
            {
                "carrier": "MYCARRIER",
                "broker_quotes": [
                    {"carrier": "Carrier A", "price": 200, "transit_days": 3},
                    {"carrier": "Carrier B", "price": 100, "transit_days": 2},
                ],
                "inputs": {
                    "company_name": "ACCO COMMERCE",
                    "origin_zip": "91342",
                    "origin_city": "SYLMAR, CA",
                    "destination_zip": "33166",
                    "destination_city": "MIAMI, FL",
                    "freight_class": "70",
                    "weight": 300,
                    "length": 48,
                    "width": 42,
                    "height": 19,
                    "pieces": 1,
                    "pallets": 1,
                    "shipment_date": "2026-07-27",
                },
            },
            {"carrier": "XPO LTL", "price": 150, "transit_days": 1, "inputs": {}},
        ],
    )

    assert "Batch: 20260727-123456" in text
    assert "Company: ACCO COMMERCE" in text
    assert "Origin: 91342 SYLMAR, CA" in text
    assert "#1 Carrier B - $100.00 - 2 day(s) via MYCARRIER" in text
    assert "#2 XPO LTL - $150.00 - 1 day(s)" in text
    assert "Total results: 2" in text


def test_telegram_quote_report_text_can_show_top_five():
    results = [
        {"carrier": f"Carrier {index}", "price": index * 10, "transit_days": index, "inputs": {}}
        for index in range(1, 7)
    ]

    text = telegram_quote_report_text("batch", results, limit=5)

    assert "Top 5 Cheapest:" in text
    assert "#5 Carrier 5 - $50.00 - 5 day(s)" in text
    assert "#6 Carrier 6" not in text


def test_parse_quote_fields_defaults_pieces_to_pallet_qty():
    fields, missing = parse_quote_fields(
        """
        Company: ACCO COMMERCE
        Pallet qty: 2
        Weight: 600 lb
        Dimensions: 48 x 42 x 19
        """
    )

    assert missing == []
    assert fields["company"] == "ACCO COMMERCE"
    assert fields["pallet_qty"] == 2
    assert fields["pieces"] == 2
    assert fields["weight"] == 600
    assert fields["dimensions"] == (48, 42, 19)


def test_parse_quote_fields_accepts_multi_pallet_line_fallback():
    fields, missing = parse_quote_fields(
        """
        Company: CROWN PRODUCTS COMPANY
        Pallet qty: 2
        Pieces: 91
        Weight: 3790 lb
        Pallet lines:
        #1: 1730 lb, 51.0 x 51.0 x 42.0, pieces 46, pallets 1
        #2: 2060 lb, 51.0 x 51.0 x 41.0, pieces 45, pallets 1
        Ship to ZIP: 32216
        """
    )

    assert missing == []
    assert fields["company"] == "CROWN PRODUCTS COMPANY"
    assert fields["ship_to_zip"] == "32216"
    assert fields["pallet_qty"] == 2
    assert fields["pieces"] == 91
    assert fields["weight"] == 3790
    assert fields["dimensions"] == (51.0, 51.0, 42.0)
    assert fields["pallet_lines"] == [
        {"pallet_qty": 1, "pieces": 46, "weight": 1730.0, "dimensions": (51.0, 51.0, 42.0)},
        {"pallet_qty": 1, "pieces": 45, "weight": 2060.0, "dimensions": (51.0, 51.0, 41.0)},
    ]


def test_parse_caption_quote_fields_accepts_natural_photo_caption():
    fields = parse_caption_quote_fields(
        """
        Omni Sac 07/27/26
        1 Pallet 20 Boxes
        49x42x34
        750 Lbs
        """
    )

    assert fields["company"] == "Omni Sac"
    assert fields["pallet_qty"] == 1
    assert fields["pieces"] == 20
    assert fields["weight"] == 750
    assert fields["dimensions"] == (49.0, 42.0, 34.0)
    assert fields["pallet_lines"] == [
        {"pallet_qty": 1, "pieces": 20, "weight": 750.0, "dimensions": (49.0, 42.0, 34.0)}
    ]


def test_merge_caption_fields_overrides_unclear_picklist_values():
    fields = merge_caption_fields(
        {
            "company": "OMNI DUCT SYSTEMS",
            "ship_to_zip": "95691",
            "pallet_qty": 1,
            "pieces": 2,
            "weight": 50,
            "dimensions": (49.0, 42.0, 24.0),
            "pallet_lines": [
                {"pallet_qty": 1, "pieces": 2, "weight": 50.0, "dimensions": (49.0, 42.0, 24.0)}
            ],
        },
        """
        Omni Sac 07/27/26
        1 Pallet 20 Boxes
        49x42x34
        750 Lbs
        """,
    )

    assert fields["company"] == "Omni Sac"
    assert fields["ship_to_zip"] == "95691"
    assert fields["pallet_qty"] == 1
    assert fields["pieces"] == 20
    assert fields["weight"] == 750
    assert fields["dimensions"] == (49.0, 42.0, 34.0)


def test_apply_quote_edits_accepts_replacement_pallet_lines():
    updated, missing = apply_quote_edits(
        {"company": "CROWN PRODUCTS COMPANY", "ship_to_zip": "32216"},
        """
        Pallet lines:
        #1: 1730 lb, 51 x 51 x 42, pieces 46, pallets 1
        #2: 2060 lb, 51 x 51 x 41, pieces 45, pallets 1
        """,
    )

    assert missing == []
    assert updated["company"] == "CROWN PRODUCTS COMPANY"
    assert updated["ship_to_zip"] == "32216"
    assert updated["pallet_qty"] == 2
    assert updated["pieces"] == 91
    assert updated["weight"] == 3790
    assert updated["dimensions"] == (51.0, 51.0, 42.0)


def test_find_company_profile_matches_saved_company_case_insensitive():
    name, profile, matches = find_company_profile(
        "acco commerce",
        {"ACCO COMMERCE": {"data": {"destination_zip": "90001"}}},
    )

    assert name == "ACCO COMMERCE"
    assert profile == {"destination_zip": "90001"}
    assert matches == []


def test_quote_payload_from_fields_uses_saved_profile_destination_and_auto_class():
    payload = quote_payload_from_fields(
        {
            "pallet_qty": 1,
            "pieces": None,
            "weight": 300,
            "dimensions": (48, 42, 19),
        },
        {
            "origin_zip": "91342",
            "pickup_city": "SYLMAR, CA",
            "destination_zip": "33166",
            "delivery_city": "MIAMI, FL",
        },
        "ACCO COMMERCE",
    )

    assert payload["company_name"] == "ACCO COMMERCE"
    assert payload["destination_zip"] == "33166"
    assert payload["delivery_city"] == "MIAMI, FL"
    assert payload["pallet_count"] == 1
    assert payload["pieces"] == 1
    assert payload["freight_class"] == ""
    assert payload["broker_targets"] == ["MYCARRIER"]


def test_fields_from_picklist_data_supports_two_bottom_pallet_lines():
    fields = fields_from_picklist_data(
        {
            "company": "OMNI DUCT SYSTEMS",
            "ship_to_city": "BUENA PARK",
            "ship_to_state": "CA",
            "ship_to_zip": "90620",
            "pallet_lines": [
                {
                    "weight_lb": 250,
                    "length_in": 48,
                    "width_in": 48,
                    "height_in": 65,
                    "box_qty": 35,
                    "pallet_qty": 1,
                },
                {
                    "weight_lb": 1500,
                    "length_in": 48,
                    "width_in": 42,
                    "height_in": 80,
                    "box_qty": 48,
                    "pallet_qty": 1,
                },
            ],
        }
    )

    assert fields["company"] == "OMNI DUCT SYSTEMS"
    assert fields["ship_to_zip"] == "90620"
    assert fields["pallet_qty"] == 2
    assert fields["pieces"] == 83
    assert fields["weight"] == 1750
    assert fields["dimensions"] == (48, 48, 65)
    assert fields["pallet_lines"] == [
        {"pallet_qty": 1, "pieces": 35, "weight": 250.0, "dimensions": (48.0, 48.0, 65.0)},
        {"pallet_qty": 1, "pieces": 48, "weight": 1500.0, "dimensions": (48.0, 42.0, 80.0)},
    ]


def test_fields_from_picklist_data_rejects_windgate_header_as_customer():
    fields = fields_from_picklist_data(
        {
            "company": "WINDGATE PRODUCTS CO., INC.",
            "ship_to_address": "AIRTHO -UNH\n121 TECHNOLOGY DRIVE\nDOCK L8-A\nDURHAM, NH 03824",
            "ship_to_city": "DURHAM",
            "ship_to_state": "NH",
            "ship_to_zip": "03824",
            "pallet_lines": [
                {
                    "weight_lb": 130,
                    "length_in": 36,
                    "width_in": 24,
                    "height_in": 53,
                    "box_qty": 1,
                    "pallet_qty": 1,
                }
            ],
        }
    )

    assert fields["company"] == "AIRTHO -UNH"
    assert fields["ship_to_zip"] == "03824"
    assert fields["weight"] == 130
    assert fields["dimensions"] == (36.0, 24.0, 53.0)
    assert fields["pieces"] == 1
    assert fields["pallet_qty"] == 1


def test_fields_from_picklist_data_rejects_non_picklist_image():
    with pytest.raises(PicklistNotFoundError):
        fields_from_picklist_data({"is_picklist": False, "pallet_lines": []})


def test_quote_payload_from_fields_sends_multi_pallet_items():
    fields = fields_from_picklist_data(
        {
            "company": "OMNI DUCT SYSTEMS",
            "pallet_lines": [
                {"weight_lb": 250, "length_in": 48, "width_in": 48, "height_in": 65, "box_qty": 35, "pallet_qty": 1},
                {"weight_lb": 1500, "length_in": 48, "width_in": 42, "height_in": 80, "box_qty": 48, "pallet_qty": 1},
            ],
        }
    )
    payload = quote_payload_from_fields(
        fields,
        {"origin_zip": "91342", "pickup_city": "SYLMAR, CA", "destination_zip": "90620", "delivery_city": "BUENA PARK, CA"},
        "OMNI DUCT SYSTEMS",
    )

    assert payload["pallet_count"] == 2
    assert payload["pieces"] == 83
    assert payload["weight_lb"] == 1750
    assert payload["length_in"] == 48
    assert payload["width_in"] == 48
    assert payload["height_in"] == 65
    assert payload["pallet_items"] == [
        {
            "pallet_number": 1,
            "pieces": 35,
            "length_in": 48.0,
            "width_in": 48.0,
            "height_in": 65.0,
            "weight_lb": 250.0,
            "freight_class": "",
        },
        {
            "pallet_number": 2,
            "pieces": 48,
            "length_in": 48.0,
            "width_in": 42.0,
            "height_in": 80.0,
            "weight_lb": 1500.0,
            "freight_class": "",
        },
    ]


def test_parse_json_object_accepts_fenced_model_output():
    data = parse_json_object(
        """```json
        {"company": "GENSCO CORPORATE", "weight_lb": 200}
        ```"""
    )

    assert data == {"company": "GENSCO CORPORATE", "weight_lb": 200}


def test_missing_quote_fields_keeps_pieces_optional():
    missing = missing_quote_fields(
        {
            "company": "GENSCO CORPORATE",
            "pallet_qty": 1,
            "pieces": None,
            "weight": 200,
            "dimensions": (37, 37, 19),
        }
    )

    assert missing == []


def test_profile_from_extracted_ship_to_uses_picklist_destination():
    profile = profile_from_extracted_ship_to(
        {
            "ship_to_city": "Tacoma",
            "ship_to_state": "WA",
            "ship_to_zip": "98424",
        }
    )

    assert profile["origin_zip"] == "91342"
    assert profile["pickup_city"] == "SYLMAR, CA"
    assert profile["destination_zip"] == "98424"
    assert profile["delivery_city"] == "Tacoma, WA"


def test_recent_image_message_remembers_latest_uploaded_photo(tmp_path, monkeypatch):
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr("webapp.telegram_bot.BOT_STATE_FILE", state_file)

    remember_recent_image(
        "123",
        {
            "photo": [{"file_id": "small"}, {"file_id": "large"}],
            "document": {},
            "caption": "Omni Sac",
        },
    )

    assert recent_image_message("123") == {
        "photo": [{"file_id": "small"}, {"file_id": "large"}],
        "document": {},
        "caption": "Omni Sac",
    }


def test_missing_quote_prompt_sends_once_for_media_group(tmp_path, monkeypatch):
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr("webapp.telegram_bot.BOT_STATE_FILE", state_file)

    first = {"message_id": 1, "media_group_id": "album-1", "photo": [{"file_id": "a"}]}
    second = {"message_id": 2, "media_group_id": "album-1", "photo": [{"file_id": "b"}]}

    assert should_send_missing_quote_prompt("123", first, ["Weight"]) is True
    assert should_send_missing_quote_prompt("123", second, ["Weight"]) is False


def test_media_group_handled_state_applies_to_later_album_images(tmp_path, monkeypatch):
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr("webapp.telegram_bot.BOT_STATE_FILE", state_file)

    first = {"message_id": 1, "media_group_id": "album-2", "photo": [{"file_id": "picklist"}]}
    second = {"message_id": 2, "media_group_id": "album-2", "photo": [{"file_id": "pallet"}]}

    assert media_group_already_handled("123", first) is False
    mark_media_group_handled("123", first)
    assert media_group_already_handled("123", second) is True


def test_quote_confirmation_markup_includes_carrier_selection():
    markup = quote_confirmation_markup()
    buttons = [button["callback_data"] for row in markup["inline_keyboard"] for button in row]
    labels = [button["text"] for row in markup["inline_keyboard"] for button in row]

    assert "menu:targets" in buttons
    assert "quote:limit:3" in buttons
    assert "quote:limit:5" in buttons
    assert "quote:limit:10" in buttons
    assert "✓ Top 5" in labels
    assert "quote:confirm" in buttons
    assert "quote:edit" in buttons


def test_quote_confirmation_markup_marks_selected_report_limit():
    markup = quote_confirmation_markup({"report_limit": 10})
    buttons = [button for row in markup["inline_keyboard"] for button in row]

    assert next(button for button in buttons if button["callback_data"] == "quote:limit:10")["text"] == "✓ Top 10"
    assert quote_result_limit({"report_limit": 7}) == 5


def test_tracking_confirmation_markup_can_start_or_cancel():
    markup = tracking_confirmation_markup()
    buttons = [button["callback_data"] for row in markup["inline_keyboard"] for button in row]

    assert buttons == ["tracking:confirm", "tracking:cancel"]


def test_save_bot_offset_preserves_pending_quote(tmp_path, monkeypatch):
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr("webapp.telegram_bot.BOT_STATE_FILE", state_file)
    pending = {
        "company": "OMNI DUCT SYSTEMS",
        "pallet_qty": 2,
        "pieces": 83,
        "weight": 1750,
        "dimensions": (48, 44, 65),
    }

    save_pending_quote("123", pending)
    save_bot_offset(456)

    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["offset"] == 456
    assert data["chats"]["123"]["pending_quote"]["company"] == "OMNI DUCT SYSTEMS"


def test_chat_quote_targets_preserves_explicit_clear_then_mycarrier(tmp_path, monkeypatch):
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr("webapp.telegram_bot.BOT_STATE_FILE", state_file)

    set_chat_quote_targets({"chats": {"123": {}}}, "123", [], [])
    brokers, direct = chat_quote_targets(json.loads(state_file.read_text(encoding="utf-8")), "123")
    assert brokers == []
    assert direct == []

    state = json.loads(state_file.read_text(encoding="utf-8"))
    set_chat_quote_targets(state, "123", ["MYCARRIER"], [])
    brokers, direct = chat_quote_targets(json.loads(state_file.read_text(encoding="utf-8")), "123")

    assert brokers == ["MYCARRIER"]
    assert direct == []


def test_pending_quote_expires_after_fifteen_minutes(tmp_path, monkeypatch):
    state_file = tmp_path / "bot_state.json"
    monkeypatch.setattr("webapp.telegram_bot.BOT_STATE_FILE", state_file)
    monkeypatch.setattr("webapp.telegram_bot.PENDING_QUOTE_TTL_SECONDS", 900)
    pending = {
        "company": "ACCO",
        "pallet_qty": 1,
        "pieces": 6,
        "weight": 290,
        "dimensions": (46, 42, 20),
    }

    save_pending_quote("123", pending)
    data = json.loads(state_file.read_text(encoding="utf-8"))
    data["chats"]["123"]["pending_quote_saved_at"] -= 901
    state_file.write_text(json.dumps(data), encoding="utf-8")

    assert expire_pending_quote("123") is True
    assert get_pending_quote("123") is None

    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert "pending_quote" not in data["chats"]["123"]
    assert "awaiting_edit" not in data["chats"]["123"]
