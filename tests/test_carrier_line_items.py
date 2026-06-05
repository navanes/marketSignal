from quote_mycarrier import quote_line_items
from quote_schneider import schneider_line_items


MULTI_PALLET_DATA = {
    "pallets": "2",
    "pieces": "80",
    "length": "48",
    "width": "48",
    "height": "50",
    "weight": "4200",
    "freight_class": "65",
    "commodity_description": "Sheet Metal Parts",
    "pallet_items": [
        {
            "pieces": "40",
            "length": "48",
            "width": "48",
            "height": "50",
            "weight": "1800",
            "freight_class": "65",
        },
        {
            "pieces": "40",
            "length": "48",
            "width": "48",
            "height": "60",
            "weight": "2400",
            "freight_class": "60",
        },
    ],
}


def test_mycarrier_uses_one_handling_unit_per_pallet_item():
    assert quote_line_items(MULTI_PALLET_DATA) == [
        {
            "count": "1",
            "length": "48",
            "width": "48",
            "height": "50",
            "commodity_description": "Sheet Metal Parts",
            "freight_class": "65",
            "pieces": "40",
            "weight": "1800",
        },
        {
            "count": "1",
            "length": "48",
            "width": "48",
            "height": "60",
            "commodity_description": "Sheet Metal Parts",
            "freight_class": "60",
            "pieces": "40",
            "weight": "2400",
        },
    ]


def test_mycarrier_falls_back_to_single_combined_item():
    data = {
        "pallets": "2",
        "pieces": "80",
        "length": "48",
        "width": "48",
        "height": "50",
        "weight": "4200",
        "freight_class": "65",
    }

    assert quote_line_items(data) == [
        {
            "count": "2",
            "length": "48",
            "width": "48",
            "height": "50",
            "commodity_description": "Sheet Metal Parts",
            "freight_class": "65",
            "pieces": "80",
            "weight": "4200",
        }
    ]


def test_schneider_uses_each_pallet_piece_count_as_quantity():
    assert schneider_line_items(MULTI_PALLET_DATA) == [
        {
            "quantity": "40",
            "length": "48",
            "width": "48",
            "height": "50",
            "weight": "1800",
            "freight_class": "65",
        },
        {
            "quantity": "40",
            "length": "48",
            "width": "48",
            "height": "60",
            "weight": "2400",
            "freight_class": "60",
        },
    ]
