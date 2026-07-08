from datetime import date

from webapp.server import (
    parse_estes_tracking_text,
    parse_glovalink_tracking_text,
    parse_roadrunner_tracking_text,
    parse_tforce_tracking_text,
    parse_total_tracking_text,
    parse_usps_tracking_text,
    normalize_carrier,
    sheet_date,
    summarize_ups_tracking,
    tracking_eta_due_for_recheck,
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


def test_ups_current_all_packages_panel_marks_two_delivered_boxes_complete():
    text = """
    Tracking Details
    Delivered
    Monday, June 22 Inside Delivery at 11:13 A.M.
    Shipment Details
    1 of 2 Piece Shipment
    All Packages in this Shipment
    1Z0JK2750312165202
    Delivered
    Delivered On: Monday, June 22 at 11:13 A.M. - Inside Delivery
    1Z0JK2750306422212
    Delivered
    Delivered On: Monday, June 22 at 11:13 A.M. - Inside Delivery
    Shipment Details
    """

    result = summarize_ups_tracking(text, "1Z0JK2750312165202")

    assert result["partial"] is False
    assert sheet_date(result["actual"]) == "6/22/2026"
    assert result["note"] == "UPS: All 2 packages delivered 6/22/2026"


def test_ups_samples_normalizes_to_ups_for_tracking():
    assert normalize_carrier("UPS(SAMPLES)") == "UPS"
    assert normalize_carrier("UPS(SAMPLE)") == "UPS"
    assert normalize_carrier("UPS (SAMPLES)") == "UPS"
    assert normalize_carrier("UPS ( SAMPLE )") == "UPS"


def test_usps_normalizes_and_builds_tracking_link():
    assert normalize_carrier("USPS") == "USPS"
    assert normalize_carrier("United States Postal Service") == "USPS"
    assert tracking_url("USPS", "9405511206241461951679") == "https://tools.usps.com/tracking/9405511206241461951679"


def test_glovalink_normalizes_and_uses_quicktrack_link():
    assert normalize_carrier("Glova Link") == "GLOVALINK"
    assert normalize_carrier("GlovaLink") == "GLOVALINK"
    assert tracking_url("GLOVALINK", "1542952") == "https://orders.glovalink.com/ENTRACK2/Track/QuickTrack"


def test_tfww_normalizes_and_uses_hyperion_tracking_link():
    assert normalize_carrier("TFWW") == "TFWW"
    assert normalize_carrier("TFWW Freight") == "TFWW"
    assert tracking_url("TFWW", "21125969") == "https://tfww.hyperiontms.com/shipmenttracking?loadnumber=21125969"


def test_roadrunner_normalizes_and_uses_home_tracking_link():
    assert normalize_carrier("Roadrunner") == "ROADRUNNER"
    assert normalize_carrier("Roadrunner Freight") == "ROADRUNNER"
    assert normalize_carrier("RRTS") == "ROADRUNNER"
    assert tracking_url("Roadrunner", "453030439") == "https://freight.rrts.com/Pages/Home.aspx"


def test_tracking_eta_recheck_window_skips_far_future_eta():
    today = date(2026, 6, 18)

    assert tracking_eta_due_for_recheck("6/23/2026", current_date=today) is False
    assert tracking_eta_due_for_recheck("6/20/2026", current_date=today) is True
    assert tracking_eta_due_for_recheck("6/17/2026", current_date=today) is True
    assert tracking_eta_due_for_recheck("", current_date=today) is True


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


def test_roadrunner_in_transit_uses_estimated_delivery():
    text = """
    You're seeing the same order status information that our Customer Service Team can access.
    Shipment Status
    In Transit
    Estimated Delivery
    07/08/2026
    Estimated between
    8:00 AM - 5:00 PM
    Tracking ID
    453030439
    From
    SYLMAR, CA
    07/02/2026, 01:53 PM
    We Have Your Shipment
    On the way
    """

    eta, actual, status = parse_roadrunner_tracking_text(text)

    assert sheet_date(eta) == "7/8/2026"
    assert actual is None
    assert status == "In Transit"


def test_glovalink_delivered_uses_actual_date():
    text = """
    QuickTrack
    PRO Number 1542952
    Status Delivered
    Actual Delivery Date 06/09/2026
    Estimated Delivery Date 06/10/2026
    """

    eta, actual, status = parse_glovalink_tracking_text(text)

    assert sheet_date(eta) == "6/10/2026"
    assert sheet_date(actual) == "6/9/2026"
    assert status == "Delivered"


def test_glovalink_cannot_find_order_is_not_found_status():
    eta, actual, status = parse_glovalink_tracking_text("QuickTrack Cannot Find Order Search")

    assert eta is None
    assert actual is None
    assert status == "Cannot Find Order"


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


def test_estes_delivered_uses_delivery_date_not_pickup_date():
    text = """
    Tracking Results
    PRO Number 2101269012
    Pickup Date 06/17/2026
    Estimated Delivery Guaranteed by 12 PM
    Status Delivered
    Delivery Completed - OK
    Delivery Date 06/24/2026
    """

    eta, actual, status = parse_estes_tracking_text(text)

    assert eta is None
    assert sheet_date(actual) == "6/24/2026"
    assert status == "Delivered"


def test_estes_delivered_without_expanded_details_does_not_use_pickup_as_eta():
    text = """
    Tracking Results
    PRO Number 2101269013
    Pickup Date 06/16/2026
    Status Delivered
    """

    eta, actual, status = parse_estes_tracking_text(text)

    assert eta is None
    assert actual is None
    assert status == "Delivered"


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


def test_tfww_delivered_uses_delivery_date_as_actual():
    text = """
    Shipment Tracking
    21125969
    Pickup Information
    6/12/2026 3:00 PM
    Status
    Delivered
    Delivery
    6/18/2026 11:00 AM
    Shipment Detail
    Load Number:
    21125969
    """

    eta, actual, status = parse_tforce_tracking_text(text)

    assert eta is None
    assert sheet_date(actual) == "6/18/2026"
    assert status == "Delivered"
