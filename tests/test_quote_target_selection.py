import pytest
from fastapi import HTTPException

from webapp.server import InputPayload, normalized_quote_selections


def quote_payload(**overrides):
    values = {
        "origin_zip": "91342",
        "destination_zip": "90001",
        "pallet_count": 1,
        "length_in": 48,
        "width_in": 48,
        "height_in": 48,
        "weight_lb": 1000,
        "pieces": 1,
        "pickup_city": "SYLMAR, CA",
    }
    values.update(overrides)
    return InputPayload(**values)


def test_separate_targets_support_default_mycarrier_and_all_direct_carriers():
    payload = quote_payload(
        broker_targets=["MYCARRIER"],
        direct_carrier_targets=["TOTAL", "CENTRAL TRANSPORTATION", "NUMARK", "TFORCE", "GLOVALINK"],
    )

    brokers, direct = normalized_quote_selections(payload)

    assert brokers == {"MYCARRIER"}
    assert direct == {"TOTAL", "CENTRAL TRANSPORTATION", "NUMARK", "TFORCE", "GLOVALINK"}


def test_separate_targets_support_custom_combination():
    payload = quote_payload(
        broker_targets=["SCHNEIDER"],
        direct_carrier_targets=["NUMARK", "TFORCE"],
    )

    brokers, direct = normalized_quote_selections(payload)

    assert brokers == {"SCHNEIDER"}
    assert direct == {"NUMARK", "TFORCE"}


def test_separate_targets_require_at_least_one_selection():
    payload = quote_payload(broker_targets=[], direct_carrier_targets=[])

    with pytest.raises(HTTPException, match="Select at least one"):
        normalized_quote_selections(payload)


def test_legacy_all_target_still_selects_everything():
    brokers, direct = normalized_quote_selections(quote_payload(quote_target="ALL"))

    assert {"MYCARRIER", "SCHNEIDER", "PRIORITY1"} <= brokers
    assert direct == {"TOTAL", "CENTRAL TRANSPORTATION", "NUMARK", "TFORCE", "GLOVALINK"}
