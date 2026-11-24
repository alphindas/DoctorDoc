import pytest

from doctordoc.quality import quality_report, text_quality

CLEAN = [
    "The drilling crew tripped out of the hole at 14:30 and changed the bit.",
    "Depth | ROP | WOB\n1200 | 35.2 | 12\n1300 | 40.1 | 14",
    "MWD BHA RPM TVD NPT rig up and test BOP stack before drilling ahead",
    "Le forage a atteint la profondeur de 3200 mètres hier soir.",
    "Page 1",
]
GARBLED = [
    "(cid:12)(cid:45)(cid:78)(cid:12)(cid:45)(cid:78) (cid:3)(cid:9)(cid:11)",
    "���� ��� Th� ��",
    " ",
    "Xq#$k%z^& a3$f9 q@w!e#r $%^&*( zx9#k2",
    "Kjhg fdsa zxcv qwrt plkm njbh vgcf xdrs zsxdc wsxcd rfvbg",
]


@pytest.mark.parametrize("text", CLEAN)
def test_clean_text_scores_high(text):
    assert text_quality(text) >= 0.9


@pytest.mark.parametrize("text", GARBLED)
def test_garbled_text_scores_low(text):
    assert text_quality(text) < 0.5


def test_empty_is_zero():
    assert text_quality("") == 0.0
    assert text_quality("  \n\t ") == 0.0


def test_report_explains_the_problem():
    report = quality_report("(cid:1)(cid:2)(cid:3) (cid:4)")
    assert "junk" in report.reason


def test_all_caps_abbreviations_are_not_penalised():
    assert text_quality("BHA MWD RPM NPT TVD ROP WOB") >= 0.9
