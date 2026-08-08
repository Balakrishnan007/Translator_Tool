from parsers.pdf_parser import parse_pdf


def test_parses_real_catalog(dataset_dir):
    segments = parse_pdf(f"{dataset_dir}\\03_DE_PDF_Katalog_Forma90.pdf")
    assert len(segments) > 30  # heuristic paragraph grouping yields ~54 blocks

def test_all_8_pages_represented(dataset_dir):
    segments = parse_pdf(f"{dataset_dir}\\03_DE_PDF_Katalog_Forma90.pdf")
    pages = {s["page"] for s in segments}
    assert pages == set(range(1, 9))

def test_deliberately_untranslated_tagline_present(dataset_dir):
    """'Timeless Living' is the deliberately-kept-in-English marketing tagline
    built into this document. Confirms text extraction isn't mangling it."""
    segments = parse_pdf(f"{dataset_dir}\\03_DE_PDF_Katalog_Forma90.pdf")
    assert any("Timeless Living" in s["text"] for s in segments)

def test_protected_article_number_present(dataset_dir):
    segments = parse_pdf(f"{dataset_dir}\\03_DE_PDF_Katalog_Forma90.pdf")
    assert any("RP-FM90-7788" in s["text"] for s in segments)
