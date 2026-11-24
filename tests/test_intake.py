import io
import zipfile

import samples
from doctordoc.config import IntakeLimits
from doctordoc.intake import Budget, extract_members, safe_name


def test_zip_members_are_extracted():
    members = extract_members(samples.zip_bytes(), "zip", Budget(IntakeLimits()))
    assert {m.name for m in members} == {"invoice.pdf", "notes/readme.txt", "data.csv"}


def test_eml_attachments_are_extracted():
    members = extract_members(samples.eml_bytes(), "eml", Budget(IntakeLimits()))
    assert [m.name for m in members] == ["invoice.pdf", "rates.csv"]
    assert members[0].data.startswith(b"%PDF")


def test_file_count_limit():
    big = samples.zip_bytes({f"f{i}.txt": b"hello world" for i in range(10)})
    budget = Budget(IntakeLimits(max_files=3))
    assert len(extract_members(big, "zip", budget)) == 3
    assert any("File limit" in w for w in budget.warnings)


def test_zip_bomb_is_stopped_by_size_limit():
    # 50 MB of zeros compresses to ~50 KB: a classic bomb shape
    bomb = io.BytesIO()
    with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("zeros.bin", b"\x00" * 50 * 1024 * 1024)
    budget = Budget(IntakeLimits(max_member_mb=10, max_total_mb=20))
    assert extract_members(bomb.getvalue(), "zip", budget) == []
    assert budget.warnings


def test_total_size_limit():
    data = samples.zip_bytes({f"f{i}.txt": b"x" * 600_000 for i in range(5)})
    budget = Budget(IntakeLimits(max_total_mb=1))
    assert len(extract_members(data, "zip", budget)) == 1


def test_broken_zip_gives_warning_not_exception():
    budget = Budget(IntakeLimits())
    assert extract_members(samples.zip_bytes()[:50], "zip", budget) == []
    assert budget.warnings


def test_safe_name_drops_directories():
    assert safe_name("../../etc/passwd") == "passwd"
    assert safe_name("a\\b\\c.txt") == "c.txt"
