from parsers.word_parser import parse_docx


def test_parses_real_linea120_doc(dataset_dir):
    segments = parse_docx(f"{dataset_dir}\\01_DE_word_technical_spec.docx")
    assert len(segments) > 300  # real file has 342 segments; guards against silent regressions

def test_extracts_both_paragraphs_and_table_cells(dataset_dir):
    segments = parse_docx(f"{dataset_dir}\\01_DE_word_technical_spec.docx")
    types = {s["type"] for s in segments}
    assert types == {"paragraph", "table_cell"}

def test_preserves_reading_order(dataset_dir):
    segments = parse_docx(f"{dataset_dir}\\01_DE_word_technical_spec.docx")
    orders = [s["order"] for s in segments]
    assert orders == sorted(orders)

def test_detects_true_headings_by_style(dataset_dir):
    segments = parse_docx(f"{dataset_dir}\\01_DE_word_technical_spec.docx")
    heading = next(s for s in segments if s["text"] == "1. Produktübersicht")
    assert heading["style"] == "Heading 1"

def test_catches_the_deliberately_broken_heading(dataset_dir):
    """Section 6's heading is intentionally styled as plain text, not a real
    heading -- this is the formatting-irregularity trigger the dataset was
    built to test. If this ever starts passing as a real heading, the
    quality-check formatting detector downstream would silently miss it."""
    segments = parse_docx(f"{dataset_dir}\\01_DE_word_technical_spec.docx")
    broken = next(s for s in segments if "Artikelnummern und Bestellinformationen" in s["text"])
    assert broken["style"] != "Heading 1"

def test_protected_article_number_is_present_in_text(dataset_dir):
    segments = parse_docx(f"{dataset_dir}\\01_DE_word_technical_spec.docx")
    assert any("RP-LN120-4471" in s["text"] for s in segments)
