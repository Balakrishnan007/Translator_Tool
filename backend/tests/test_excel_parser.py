from parsers.excel_parser import parse_xlsx


def test_parses_real_price_list(dataset_dir):
    segments = parse_xlsx(f"{dataset_dir}\\02_DE_Excel_Preisliste_Linea120.xlsx")
    assert len(segments) > 100  # real file has 121 row segments

def test_all_segments_are_row_level(dataset_dir):
    segments = parse_xlsx(f"{dataset_dir}\\02_DE_Excel_Preisliste_Linea120.xlsx")
    assert all(s["type"] == "row" for s in segments)

def test_header_row_flagged_correctly(dataset_dir):
    segments = parse_xlsx(f"{dataset_dir}\\02_DE_Excel_Preisliste_Linea120.xlsx")
    header_rows = [s for s in segments if s["is_header"]]
    assert len(header_rows) == 1
    assert "Artikelnummer" in header_rows[0]["text"]
    assert "Preis netto (€)" in header_rows[0]["text"]

def test_data_rows_not_flagged_as_header(dataset_dir):
    segments = parse_xlsx(f"{dataset_dir}\\02_DE_Excel_Preisliste_Linea120.xlsx")
    data_rows = [s for s in segments if not s["is_header"]]
    assert len(data_rows) > 0
    assert all(s["row_index"] > 1 for s in data_rows)

def test_row_bundles_all_columns_with_context(dataset_dir):
    """A data row should carry every column together (article number, name,
    material, price, ...), not just an isolated cell. That's the whole
    point of row-level over cell-level chunking."""
    segments = parse_xlsx(f"{dataset_dir}\\02_DE_Excel_Preisliste_Linea120.xlsx")
    data_row = next(s for s in segments if not s["is_header"])
    assert len(data_row["cells"]) >= 5
    assert all("header" in c and "value" in c for c in data_row["cells"])

def test_protected_article_numbers_present(dataset_dir):
    segments = parse_xlsx(f"{dataset_dir}\\02_DE_Excel_Preisliste_Linea120.xlsx")
    assert any("RP-LN120-" in s["text"] for s in segments)
