"""What the command line prints: JSON that stays valid, and install hints that stay whole.

The JSON tests run the real command with its output piped. The narrow width and forced colour
are what used to wrap or colour the text. No Ollama: the model is a little server on this machine.
"""

import importlib.util
import json
import os
import subprocess
import sys

import pytest
from typer.testing import CliRunner

from doctordoc import cli, parse
from doctordoc.config import Config
from doctordoc.router import Router
from test_llm_limits import SlowModel

# one very long line, with text that a terminal library likes to rewrite
ODD = "[bold]not markup[/bold] :smile: café 井 \x1b[31mred "
LONG_LINE = "start " + ("word " * 2000) + ODD + "end"

runner = CliRunner()


@pytest.fixture
def settings(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text("gpu: 'off'\npage_chains:\n  digital: [pdf_text]\n")
    return str(path)


@pytest.fixture
def long_file(tmp_path):
    path = tmp_path / "long.txt"
    path.write_text(LONG_LINE + "\n", encoding="utf-8")
    return path


def piped(*arguments):
    """`doctordoc ...` as a separate program with its output in a pipe."""
    env = {**os.environ, "COLUMNS": "20", "FORCE_COLOR": "1", "PYTHONIOENCODING": "utf-8"}
    command = [sys.executable, "-m", "doctordoc.cli", *map(str, arguments)]
    return subprocess.run(
        command, env=env, capture_output=True, encoding="utf-8", timeout=120, check=False
    )


def assert_plain_json(text):
    assert "\x1b" not in text  # no colour codes
    return json.loads(text)


def test_parse_json_with_a_very_long_line_is_valid_and_unchanged(long_file, settings):
    run = piped("parse", long_file, "-f", "json", "-c", settings)
    assert run.returncode == 0, run.stderr
    printed = assert_plain_json(run.stdout)
    library = parse(long_file, Config.from_yaml(settings))
    assert printed["pages"] == json.loads(library.to_json())["pages"]
    assert ":smile:" in run.stdout and "[bold]not markup[/bold]" in run.stdout
    assert max(map(len, run.stdout.splitlines())) > len(LONG_LINE)  # not wrapped


def test_extract_json_with_very_long_lines_is_valid_and_unchanged(long_file, settings):
    answer = {"invoice_number": "INV-1", "vendor_name": LONG_LINE}
    model = SlowModel(
        answer={
            "message": {"content": json.dumps(answer)},
            "done_reason": "stop",
            "prompt_eval_count": 7500,  # sets off the length guard and its long message
            "eval_count": 10,
        }
    )
    try:
        run = piped(
            "extract",
            long_file,
            "--format",
            "json",
            "--base-url",
            model.config().llm.base_url,
            "-c",
            settings,
        )
    finally:
        model.close()
    assert run.returncode == 0, run.stderr
    report = assert_plain_json(run.stdout)
    values = {f["field"]: f["value"] for f in report["fields"]}
    assert values["vendor_name"] == LONG_LINE
    [issue] = [i for i in report["validation_issues"] if i["rule"] == "prompt_length"]
    assert len(issue["message"]) > 100
    assert "warning" in run.stderr  # warnings stay out of the JSON


def test_parse_json_of_a_folder_is_one_valid_list(tmp_path, settings):
    folder = tmp_path / "in"
    folder.mkdir()
    (folder / "a.txt").write_text(LONG_LINE, encoding="utf-8")
    (folder / "b.csv").write_text("x,y\n1,2\n3,4\n")
    (folder / "c-empty.txt").write_text("")
    run = piped("parse", folder, "-f", "json", "-c", settings)
    assert run.returncode == 1  # one file failed, and the others are still there
    documents = assert_plain_json(run.stdout)
    assert [d["source"] for d in documents] == ["a.txt", "b.csv", "c-empty.txt"]
    assert [bool(d["error"]) for d in documents] == [False, False, True]
    for name in ("a.txt", "b.csv"):  # the full paths are still shown, but not in the JSON
        assert str(folder / name) in run.stderr


@pytest.mark.parametrize("files, expected", [(0, list), (1, dict)])
def test_parse_json_of_a_folder_with_none_or_one_file(tmp_path, settings, files, expected):
    folder = tmp_path / "in"
    folder.mkdir()
    if files:
        (folder / "a.txt").write_text("some words here")
    run = piped("parse", folder, "-f", "json", "-c", settings)
    assert run.returncode == 0, run.stderr
    assert type(assert_plain_json(run.stdout)) is expected


def test_json_files_written_with_out_are_one_per_input(tmp_path, long_file, settings):
    out = tmp_path / "out"
    run = piped("parse", long_file, "-f", "json", "-o", out, "-c", settings)
    assert run.returncode == 0 and run.stdout == ""
    assert_plain_json((out / "long.txt.json").read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------- install hints


def hide_packages(monkeypatch, *names):
    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda n, *a: None if n in names else real(n, *a)
    )


@pytest.mark.parametrize("command, package", [("ui", "streamlit"), ("api", "fastapi")])
def test_a_missing_extra_is_named_in_the_hint(monkeypatch, command, package):
    hide_packages(monkeypatch, package)
    result = runner.invoke(cli.app, [command])
    assert result.exit_code == 1
    assert f'pip install "doctordoc[{command}]"' in " ".join(result.output.split())


OPTIONAL = ("docling", "markitdown", "rapidocr", "onnxruntime", "paddleocr", "paddle")


def test_the_engines_table_shows_every_hint_whole(monkeypatch):
    hide_packages(monkeypatch, *OPTIONAL)
    monkeypatch.setattr(cli.out, "_width", 400)  # wide enough that no cell wraps
    result = runner.invoke(cli.app, ["engines"])
    hints = {e.install_hint for e in Router(Config()).engines.values() if e.install_hint}
    assert result.exit_code == 0 and len(hints) >= 4
    for hint in hints:
        assert hint in result.output, hint


def test_the_decision_log_shows_the_hint_whole(monkeypatch, tmp_path, invoice_pdf):
    hide_packages(monkeypatch, *OPTIONAL)
    monkeypatch.setattr(cli.err, "_width", 400)
    path = tmp_path / "invoice.pdf"
    path.write_bytes(invoice_pdf)
    result = runner.invoke(cli.app, ["parse", str(path), "--explain", "--no-gpu"])
    assert result.exit_code == 0 and 'pip install "doctordoc[docling]"' in result.output
