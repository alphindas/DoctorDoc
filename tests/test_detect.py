import samples
from doctordoc.detect import detect


def test_detects_formats_from_bytes_not_extension():
    cases = {
        "pdf": samples.digital_pdf(),
        "docx": samples.docx_bytes(),
        "xlsx": samples.xlsx_bytes(),
        "pptx": samples.pptx_bytes(),
        "csv": samples.csv_bytes(),
        "json": samples.json_bytes(),
        "html": samples.html_bytes(),
        "eml": samples.eml_bytes(),
        "zip": samples.zip_bytes(),
    }
    for expected, data in cases.items():
        assert detect(data, "no_extension").file_type == expected


def test_tells_office_formats_apart_inside_the_zip():
    # all three are ZIP files; only the contents differ
    assert detect(samples.docx_bytes()).file_type == "docx"
    assert detect(samples.xlsx_bytes()).file_type == "xlsx"
    assert detect(samples.pptx_bytes()).file_type == "pptx"


def test_wrong_extension_gives_warning_and_real_type():
    info = detect(samples.docx_bytes(), "report.pdf")
    assert info.file_type == "docx"
    assert any("docx" in w for w in info.warnings)


def test_txt_extension_with_csv_inside_is_not_a_lie():
    assert detect(samples.csv_bytes(), "notes.txt").warnings == []


def test_empty_file():
    info = detect(b"", "x.pdf")
    assert info.empty and info.problem
    assert detect(b"\x00\x00  \n", "x.txt").empty


def test_encrypted_pdf():
    info = detect(samples.digital_pdf(encrypt="secret"), "x.pdf")
    assert info.file_type == "pdf" and info.encrypted and info.problem


def test_truncated_pdf_is_corrupt():
    info = detect(samples.digital_pdf()[:300], "x.pdf")
    assert info.file_type == "pdf" and info.corrupt


def test_broken_zip_is_corrupt():
    info = detect(samples.zip_bytes()[:60], "x.zip")
    assert info.corrupt


def test_binary_junk_is_unknown():
    assert detect(b"\x00\x01\x02\x03binary\xff\xfe junk", "x.bin").file_type == "unknown"


def test_pdf_page_count():
    assert detect(samples.digital_pdf([["a"], ["b"], ["c"]])).page_count == 3


def test_images():
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buf, format="PNG")
    assert detect(buf.getvalue(), "a.jpg").file_type == "png"
