"""Turning scanned pages the right way up before OCR."""

import io

import pytest
from PIL import Image, ImageDraw, ImageFont

from doctordoc.config import Config
from doctordoc.engines._ocr_layout import OcrBox
from doctordoc.engines._orientation import read_upright, upright_score
from doctordoc.engines.ocr_rapid import RapidOcrEngine
from doctordoc.engines.ocr_tesseract import TesseractEngine


def wide(text="hello", confidence=0.9):
    return OcrBox(text, 0, 0, 100, 20, confidence)


def tall(text="hello", confidence=0.99):
    return OcrBox(text, 0, 0, 20, 100, confidence)


def test_score_counts_only_horizontal_text():
    assert upright_score([wide(confidence=0.9)]) == pytest.approx(0.9)
    assert upright_score([tall()]) == 0.0
    assert upright_score([wide(confidence=0.8), tall()]) == pytest.approx(0.4)
    assert upright_score([]) == 0.0


def fake_reader(by_size_and_pixel):
    """A picture with one black corner pixel tells the fake reader which way it was turned."""

    def read(image):
        corners = [(0, 0), (image.width - 1, 0), (image.width - 1, image.height - 1)]
        corners.append((0, image.height - 1))
        at = next(i for i, xy in enumerate(corners) if image.getpixel(xy) == 0)
        return by_size_and_pixel[at * 90]  # degrees turned clockwise so far

    return read


def marked_picture():
    image = Image.new("L", (60, 40), 255)
    image.putpixel((0, 0), 0)
    return image


@pytest.mark.parametrize("correct", [0, 90, 180, 270])
def test_the_angle_with_the_best_horizontal_confidence_wins(correct):
    reads = {a: [wide(confidence=0.5)] for a in (0, 90, 180, 270)}
    reads[(correct + 90) % 360] = [tall(confidence=1.0)]  # very sure, but the lines run down
    reads[correct] = [wide(f"turn {correct}", 0.95)]
    turned, boxes = read_upright(marked_picture(), fake_reader(reads))
    assert turned == correct and boxes[0].text == f"turn {correct}"


def test_a_tiny_gain_is_not_enough_to_turn_a_page():
    reads = {a: [wide(confidence=0.90)] for a in (0, 90, 270)}
    reads[180] = [wide(confidence=0.93)]
    assert read_upright(marked_picture(), fake_reader(reads))[0] == 0


def test_a_page_with_no_text_is_left_alone():
    assert read_upright(Image.new("L", (60, 40), 255), lambda image: []) == (0, [])


def counting_reader(confidence_as_is: float):
    calls = []

    def read(image):
        calls.append(image.size)
        return [wide(confidence=confidence_as_is if len(calls) == 1 else 0.5)]

    return read, calls


@pytest.mark.parametrize("score, reads", [(0.9, 1), (0.75, 1), (0.749, 4), (0.2, 4)])
def test_early_stop_reads_once_only_when_the_page_as_it_is_scores_enough(score, reads):
    read, calls = counting_reader(score)
    read_upright(marked_picture(), read, early_stop=0.75)
    assert len(calls) == reads


def test_without_a_cutoff_all_four_angles_are_read():
    read, calls = counting_reader(0.99)
    read_upright(marked_picture(), read)
    assert len(calls) == 4


def test_the_cutoff_comes_from_config():
    assert Config().rotation_early_stop == 0.75


# ------------------------------------------------------------------ real engines

LINES = [
    "The crew changed the drill bit at noon.",
    "Mud weight was held through the sand section.",
    "No safety incidents were reported today.",
    "Supplies for the next section arrive on Monday.",
]


def page_png(clockwise: int) -> bytes:
    image = Image.new("RGB", (1000, 500), "white")
    draw = ImageDraw.Draw(image)
    for i, line in enumerate(LINES):
        draw.text((50, 60 + i * 90), line, fill="black", font=ImageFont.load_default(size=36))
    image = image.rotate(-clockwise, expand=True)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


@pytest.mark.integration
@pytest.mark.parametrize("engine_class", [RapidOcrEngine, TesseractEngine])
@pytest.mark.parametrize("laid", [0, 90, 180, 270])
def test_real_engines_turn_the_page_upright_and_keep_line_order(engine_class, laid):
    engine = engine_class(Config(gpu="off"))
    if reason := engine.missing_reason():
        pytest.skip(reason)
    [page] = engine.parse(page_png(laid), "png")
    assert page.turned == (360 - laid) % 360
    squash = lambda s: "".join(c for c in s.lower() if c.isalnum())  # noqa: E731
    text = squash(page.text)
    positions = [text.find(squash(line)[:12]) for line in LINES]
    assert -1 not in positions and positions == sorted(positions), page.text


@pytest.mark.integration
def test_fix_rotation_can_be_switched_off():
    engine = TesseractEngine(Config(gpu="off", fix_rotation=False))
    if reason := engine.missing_reason():
        pytest.skip(reason)
    [page] = engine.parse(page_png(180), "png")
    assert page.turned == 0
