import os
import zipfile
import pytest
from PIL import Image
from reportlab.pdfgen import canvas

from parsers.upload_validator import validate_upload


@pytest.fixture
def image_renamed_as_docx(tmp_path):
    path = tmp_path / "fake.docx"
    img_path = tmp_path / "real.jpg"
    Image.new("RGB", (100, 100), color="red").save(img_path, "JPEG")
    path.write_bytes(img_path.read_bytes())
    return str(path)

@pytest.fixture
def empty_docx(tmp_path):
    path = tmp_path / "empty.docx"
    path.write_bytes(b"")
    return str(path)

@pytest.fixture
def corrupted_docx(tmp_path):
    path = tmp_path / "corrupted.docx"
    path.write_bytes(b"PK\x03\x04" + os.urandom(200))
    return str(path)

@pytest.fixture
def text_renamed_as_pdf(tmp_path):
    path = tmp_path / "fake.pdf"
    path.write_text("This is just plain text, not a real PDF.", encoding="utf-8")
    return str(path)

@pytest.fixture
def random_zip_renamed_as_xlsx(tmp_path):
    path = tmp_path / "fake.xlsx"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("hello.txt", "just a random zip, not a real workbook")
    return str(path)

@pytest.fixture
def scanned_pdf_no_text(tmp_path):
    path = tmp_path / "scanned.pdf"
    c = canvas.Canvas(str(path))
    c.rect(100, 100, 200, 200, fill=1)  # image-like content, zero text drawn
    c.showPage()
    c.save()
    return str(path)


def test_rejects_image_renamed_as_docx(image_renamed_as_docx):
    result = validate_upload(image_renamed_as_docx)
    assert result["valid"] is False
    assert "content doesn't match" in result["error"]

def test_rejects_empty_file(empty_docx):
    result = validate_upload(empty_docx)
    assert result["valid"] is False
    assert "empty" in result["error"]

def test_rejects_corrupted_docx(corrupted_docx):
    result = validate_upload(corrupted_docx)
    assert result["valid"] is False
    assert "not a valid zip" in result["error"]

def test_rejects_text_renamed_as_pdf(text_renamed_as_pdf):
    result = validate_upload(text_renamed_as_pdf)
    assert result["valid"] is False

def test_rejects_random_zip_renamed_as_xlsx(random_zip_renamed_as_xlsx):
    result = validate_upload(random_zip_renamed_as_xlsx)
    assert result["valid"] is False
    assert "xl/workbook.xml" in result["error"]

def test_rejects_scanned_pdf_with_no_text(scanned_pdf_no_text):
    result = validate_upload(scanned_pdf_no_text)
    assert result["valid"] is False
    assert "No extractable text" in result["error"]

def test_rejects_unsupported_extension(tmp_path):
    path = tmp_path / "notes.txt"
    path.write_text("hello")
    result = validate_upload(str(path))
    assert result["valid"] is False
    assert "Unsupported file type" in result["error"]


@pytest.mark.parametrize("filename,expected_min_segments", [
    ("01_DE_word_technical_spec.docx", 300),
    ("02_DE_Excel_Preisliste_Linea120.xlsx", 100),
    ("03_DE_PDF_Katalog_Forma90.pdf", 30),
])
def test_accepts_real_dataset_files(dataset_dir, filename, expected_min_segments):
    result = validate_upload(f"{dataset_dir}\\{filename}")
    assert result["valid"] is True
    assert result["segment_count"] >= expected_min_segments
