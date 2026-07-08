from quote_total import write_result


class FakeWorksheet:
    def __init__(self, values):
        self.values = [list(row) for row in values]
        self.updates = []

    def get_all_values(self):
        return [list(row) for row in self.values]

    def row_values(self, idx):
        if idx - 1 < len(self.values):
            return list(self.values[idx - 1])
        return []

    def insert_row(self, row, index, value_input_option=None):
        self.values.insert(index - 1, list(row))

    def update(self, cell, values):
        self.updates.append((cell, values))


class FakeSpreadsheet:
    def __init__(self, worksheet):
        self._worksheet = worksheet

    def worksheet(self, name):
        return self._worksheet


def test_write_result_adds_missing_direct_carrier_row_for_current_batch():
    worksheet = FakeWorksheet(
        [
            ["Batch", "Broker"],
            ["20260707-140042", "TOTAL", "", "", "", "2026-07-07 14:00:42", "91342", "SYLMAR, CA", "90040", "COMMERCE, CA", "77.5", "300", ""],
        ]
    )

    write_result(
        FakeSpreadsheet(worksheet),
        "GLOVALINK",
        {"price": 166.8, "transit_days": "", "raw_money": [166.8]},
        "SYLMAR, CA",
        "COMMERCE, CA",
    )

    assert worksheet.values[2][0] == "20260707-140042"
    assert worksheet.values[2][1] == "GLOVALINK"
    assert worksheet.values[2][6] == "91342"
    assert worksheet.updates[0] == ("C3", [["GLOVALINK"]])
    assert ("D3", [[166.8]]) in worksheet.updates
