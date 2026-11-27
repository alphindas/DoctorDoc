"""ONNX Runtime's telemetry is turned off before anything can start ONNX Runtime.

Every test starts a fresh Python program: this one has imported doctordoc already.
"""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from doctordoc.engines.ocr_rapid import RapidOcrEngine

NAME = "ORT_DISABLE_TELEMETRY"
SCANNED = Path(__file__).resolve().parent.parent / "examples" / "invoice_scanned.pdf"

SHOW = f"""
import os, sys
import doctordoc
print(repr(os.environ.get("{NAME}")), "onnxruntime" in sys.modules, "rapidocr" in sys.modules)
"""


def run_fresh(code, value=None, **extra):
    """Run code in a new Python with the variable unset, or set to `value` by "the user"."""
    env = {k: v for k, v in os.environ.items() if k != NAME}
    env.update(extra)
    if value is not None:
        env[NAME] = value
    run = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert run.returncode == 0, run.stderr
    return run.stdout.strip()


def test_importing_doctordoc_turns_telemetry_off_before_onnxruntime_is_loaded():
    assert run_fresh(SHOW) == "'1' False False"


@pytest.mark.parametrize("value", ["0", "1", "false", ""])
def test_a_value_the_user_set_is_not_overwritten(value):
    assert run_fresh(SHOW, value) == f"{value!r} False False"


@pytest.mark.parametrize("module", ["doctordoc.cli", "doctordoc.service", "doctordoc.engines"])
def test_importing_any_part_of_doctordoc_sets_it_too(module):
    code = f'import os, {module}; print(os.environ.get("{NAME}"))'
    assert run_fresh(code) == "1"


def test_it_is_set_when_a_parse_first_imports_onnxruntime(tmp_path):
    if RapidOcrEngine().missing_reason():
        pytest.skip("RapidOCR is not installed")
    code = f"""
import os, sys

class Watch:  # sees every module the moment Python first looks for it
    def find_spec(self, name, path=None, target=None):
        if name in ("onnxruntime", "rapidocr"):
            print("LOOK", name, os.environ.get("{NAME}"))
        return None

sys.meta_path.insert(0, Watch())
from doctordoc import parse
from doctordoc.config import Config

doc = parse({str(SCANNED)!r}, Config(gpu="off", page_chains={{"scanned": ["rapidocr"]}}))
print("ENGINE", doc.pages[0].engine, "onnxruntime" in sys.modules)
"""
    home, temp = tmp_path / "home", tmp_path / "temp"
    home.mkdir()
    temp.mkdir()
    lines = run_fresh(code, HOME=str(home), TMPDIR=str(temp)).splitlines()
    looks = [line.split() for line in lines if line.startswith("LOOK ")]
    assert {name for _, name, _ in looks} == {"onnxruntime", "rapidocr"}
    assert {value for _, _, value in looks} == {"1"}
    assert "ENGINE rapidocr True" in lines
    # Where ONNX Runtime put its telemetry files on Linux (seen with 1.32.0). If a later
    # version stops obeying the variable, this is where it shows. Other systems use other
    # places, so there this part proves nothing.
    assert not (home / ".cache" / "Microsoft").exists()
    assert not (temp / ".ses").exists()
