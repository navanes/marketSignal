from quote_glovalink import parse_glovalink_quote_result
import pytest


def test_parse_glovalink_quote_total_estimated_charge():
    text = """
    Charge Description Estimated Charge
    BASE 83.45
    FUEL 8.35
    SAMDAY 75.00
    TOTAL ESTIMATED CHARGE 166.80
    """

    result = parse_glovalink_quote_result(text)

    assert result["price"] == 166.80
    assert result["raw_money"] == [83.45, 8.35, 75.00, 166.80]


def test_parse_glovalink_quote_rejects_invalid_zero_rate():
    text = """
    Invalid Quote Result
    Rate Not Available. Please call customer service!
    Charge Description Estimated Charge
    TOTAL ESTIMATED CHARGE 0.00
    """

    with pytest.raises(RuntimeError, match="rate not available"):
        parse_glovalink_quote_result(text)
