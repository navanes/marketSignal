from webapp.server import (
    parse_estes_tracking_text,
    parse_total_tracking_text,
    parse_usps_tracking_text,
    normalize_carrier,
    sheet_date,
    summarize_ups_tracking,
    tracking_url,
)


def test_ups_partial_multi_piece_shipment_stays_undelivered():
    text = """
    Tracking Details
    Delivered
    1Z0JK2750315002604
    1 of 3 Piece Shipment
    Other Packages in this Shipment
    1Z0JK2750315002604
    Delivered
    Delivered On: Wednesday, April 29 at 11:18 A.M. Dock
    1Z0JK2750309761225
    Delivered
    Delivered On: Wednesday, April 29 at 11:18 A.M. Dock
    1Z0JK2750319343613
    Shipment Ready for UPS
    Latest Update: Estimated delivery date will be available when UPS receives the package.
    """

    result = summarize_ups_tracking(text, "1Z0JK2750315002604")

    assert result["actual"] is None
    assert result["partial"] is True
    assert "2/3 packages delivered" in result["note"]
    assert "1Z0JK2750319343613 Shipment Ready for UPS" in result["note"]


def test_ups_all_packages_delivered_uses_latest_actual_date():
    text = """
    1 of 2 Piece Shipment
    Other Packages in this Shipment
    1Z0JK2750315002604
    Delivered
    Delivered On: Wednesday, April 29 at 11:18 A.M. Dock
    1Z0JK2750309761225
    Delivered
    Delivered On: Thursday, April 30 at 9:00 A.M. Dock
    """

    result = summarize_ups_tracking(text, "1Z0JK2750315002604")

    assert result["partial"] is False
    assert sheet_date(result["actual"]) == "4/30/2026"
    assert result["note"] == "UPS: All 2 packages delivered 4/30/2026"


def test_ups_samples_normalizes_to_ups_for_tracking():
    assert normalize_carrier("UPS(SAMPLES)") == "UPS"
    assert normalize_carrier("UPS(SAMPLE)") == "UPS"
    assert normalize_carrier("UPS (SAMPLES)") == "UPS"
    assert normalize_carrier("UPS ( SAMPLE )") == "UPS"


def test_usps_normalizes_and_builds_tracking_link():
    assert normalize_carrier("USPS") == "USPS"
    assert normalize_carrier("United States Postal Service") == "USPS"
    assert tracking_url("USPS", "9405511206241461951679") == "https://tools.usps.com/tracking/9405511206241461951679"


def test_usps_out_for_delivery_uses_expected_delivery_date():
    text = """
    USPS Tracking
    Tracking Number: 9405511206241461951679
    Expected Delivery on
    FRIDAY
    5
    June
    2026
    by 6:50pm
    Your item is out for delivery on June 5, 2026 at 6:10 am in HONOLULU, HI 96819.
    Out for Delivery
    Arrived at Post Office
    """

    eta, actual, status = parse_usps_tracking_text(text)

    assert sheet_date(eta) == "6/5/2026"
    assert actual is None
    assert status == "Out For Delivery"


def test_usps_delivered_uses_actual_date():
    text = """
    USPS Tracking
    Delivered
    Delivered, In/At Mailbox
    June 4, 2026 at 2:15 pm
    """

    eta, actual, status = parse_usps_tracking_text(text)

    assert eta is None
    assert sheet_date(actual) == "6/4/2026"
    assert status == "Delivered"


def test_estes_out_for_delivery_does_not_fill_actual_date():
    text = """
    Tracking Results
    Estimated Delivery Date 05/07/2026
    Status Out for Delivery
    Delivery Attempted - Freight Unavailable
    Appointment Date 05/07/2026
    """

    eta, actual, status = parse_estes_tracking_text(text)

    assert sheet_date(eta) == "5/7/2026"
    assert actual is None
    assert status == "Out For Delivery"


def test_total_delivery_complete_uses_delivered_date():
    text = """
    DELIVERY DETAILS
    Delivered Date: 05/05/2026
    Arrival time: 10:12 AM
    Status: DELIVERY COMPLETE
    """

    eta, actual, status = parse_total_tracking_text(text)

    assert eta is None
    assert sheet_date(actual) == "5/5/2026"
    assert status == "DELIVERY COMPLETE"
