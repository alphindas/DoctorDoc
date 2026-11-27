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

import samples
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


# ---------------------------------------------------------------------------- exact Markdown

HINDI = "नमस्ते दुनिया, यह एक परीक्षण है"
# a very long line, things Rich rewrites, a raw terminal code (click would strip it), a tab
MARKDOWN_TEXT = f"{'word ' * 2000}:smile: [red]x[/red] {HINDI} \x1b[31mred\x1b[0m\ttab end"

LOCALES = {
    "utf-8": {"PYTHONIOENCODING": "utf-8"},
    "ascii C locale": {"LC_ALL": "C", "PYTHONUTF8": "0", "PYTHONCOERCECLOCALE": "0"},
    "latin-1": {"PYTHONIOENCODING": "latin-1"},
    "cp1252": {"PYTHONIOENCODING": "cp1252"},
    "ascii, strict": {"PYTHONIOENCODING": "ascii:strict"},
}


def run_bytes(arguments, locale=None, **kwargs):
    """`doctordoc ...` as a separate program; stdout and stderr are the raw bytes."""
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONIOENCODING", "LANG", "LC_ALL")}
    env.update({"COLUMNS": "20", "FORCE_COLOR": "1", **(locale or LOCALES["utf-8"])})
    kwargs.setdefault("stdout", subprocess.PIPE)
    command = [sys.executable, "-m", "doctordoc.cli", *map(str, arguments)]
    return subprocess.run(
        command, env=env, stderr=subprocess.PIPE, timeout=120, check=False, **kwargs
    )


@pytest.fixture
def markdown_source(tmp_path, settings):
    """An input file, and the bytes that `--out` writes for it."""
    path = tmp_path / "odd.txt"
    path.write_text(MARKDOWN_TEXT + "\n\nsecond paragraph :smile:\n", encoding="utf-8")
    run = run_bytes(["parse", path, "-o", tmp_path / "out", "-c", settings])
    assert run.returncode == 0, run.stderr
    written = (tmp_path / "out" / "odd.txt.md").read_bytes()
    for part in (":smile:", "[red]x[/red]", HINDI, "\x1b[31mred", "word " * 2000):
        assert part.encode() in written  # the --out file itself holds the text unchanged
    return path, written


@pytest.mark.parametrize("locale", LOCALES.values(), ids=LOCALES.keys())
def test_markdown_on_stdout_is_byte_for_byte_the_out_file(markdown_source, settings, locale):
    path, written = markdown_source
    run = run_bytes(["parse", path, "-c", settings], locale)
    assert run.returncode == 0, run.stderr
    assert run.stdout == written


def test_markdown_redirected_to_a_file_is_byte_for_byte_too(markdown_source, settings, tmp_path):
    path, written = markdown_source
    target = tmp_path / "redirected.md"
    with target.open("wb") as handle:
        assert run_bytes(["parse", path, "-c", settings], stdout=handle).returncode == 0
    assert target.read_bytes() == written


def test_explain_goes_to_stderr_and_leaves_the_markdown_exact(markdown_source, settings):
    path, written = markdown_source
    run = run_bytes(["parse", path, "--explain", "-c", settings])
    assert run.stdout == written
    assert b"Router decisions" in run.stderr


@pytest.mark.parametrize("locale", ["utf-8", "latin-1"])
def test_a_folder_prints_each_document_exactly_after_its_path(tmp_path, settings, locale):
    folder = tmp_path / f"in {'long-folder-name-' * 6}:smile:"
    folder.mkdir()
    (folder / "a.txt").write_text(MARKDOWN_TEXT, encoding="utf-8")
    (folder / "b [red].txt").write_text(HINDI, encoding="utf-8")
    assert run_bytes(["parse", folder, "-o", tmp_path / "out", "-c", settings]).returncode == 0
    expected = b"".join(
        f"\n===== {folder / name} =====\n".encode() + (tmp_path / "out" / f"{name}.md").read_bytes()
        for name in ("a.txt", "b [red].txt")
    )
    run = run_bytes(["parse", folder, "-c", settings], LOCALES[locale])
    assert run.returncode == 0, run.stderr
    assert run.stdout == expected


def test_a_reader_that_stops_early_gets_no_traceback(tmp_path, settings):
    path = tmp_path / "big.txt"
    path.write_text("a line of words\n\n" * 40_000)  # far more than a pipe holds
    command = [sys.executable, "-m", "doctordoc.cli", "parse", str(path), "-c", settings]
    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert process.stdout.read(10) == b"a line of "
    process.stdout.close()  # like `| head`
    errors = process.stderr.read()
    process.wait(60)
    assert b"Traceback" not in errors and b"BrokenPipe" not in errors, errors


@pytest.mark.parametrize("separator", ["\n", "\r\n"])
def test_line_endings_follow_the_system_as_the_out_file_does(monkeypatch, capsysbinary, separator):
    monkeypatch.setattr(cli.os, "linesep", separator)  # "\r\n" is what Windows uses
    cli._print_markdown("one\ntwo\n")
    assert capsysbinary.readouterr().out == f"one{separator}two{separator}".encode()


# ---------------------------------------------------------------------------- --out names


def write_files(folder, contents):
    for name, text in contents.items():
        path = folder / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def parse_into(tmp_path, settings, contents, *extra):
    """Parse a folder with --out: (output name -> text, what was printed on stderr)."""
    source, out = tmp_path / "in", tmp_path / "out"
    write_files(source, contents)
    run = run_bytes(["parse", source, "-o", out, "-c", settings, *extra])
    assert run.returncode == 0, run.stderr
    written = {p.name: p.read_text(encoding="utf-8") for p in out.iterdir()}
    return written, run.stderr.decode()


def test_two_files_with_the_same_name_both_keep_their_result(tmp_path, settings):
    contents = {"a/report.txt": "first words here", "b/report.txt": "second words here"}
    written, log = parse_into(tmp_path, settings, {**contents, "plain.txt": "third words here"})
    assert {name: text.strip() for name, text in written.items()} == {
        "a_report.txt.md": "first words here",
        "b_report.txt.md": "second words here",
        "plain.txt.md": "third words here",  # no clash: the name stays as before
    }
    for source, output in [
        ("a/report.txt", "a_report.txt.md"),
        ("b/report.txt", "b_report.txt.md"),
        ("plain.txt", "plain.txt.md"),
    ]:  # the mapping: each input and the file it went to
        line = f"{tmp_path / 'in' / source} -> {tmp_path / 'out' / output}"
        assert line in log, log
    assert "Outputs renamed so that no result is lost: 2" in log


def test_a_file_at_the_top_keeps_its_name_and_a_taken_name_gets_a_number(tmp_path, settings):
    contents = {
        "report.txt": "top words here",
        "a/report.txt": "inner words here",
        "a_report.txt": "already called that",  # the name the inner one would have taken
    }
    written, _ = parse_into(tmp_path, settings, contents)
    assert {name: text.strip() for name, text in written.items()} == {
        "report.txt.md": "top words here",
        "a_report.txt.md": "already called that",
        "a_report-2.txt.md": "inner words here",
    }


@pytest.mark.parametrize("extension", ["md", "json"])
def test_no_result_is_lost_in_awkward_cases(tmp_path, settings, extension):
    deep = "/".join("d" * 60 for _ in range(6))  # too long to put into one file name
    contents = {
        "p/q/report.txt": "words number one",
        "p_q/report.txt": "words number two",  # same folder-based name as the one above
        "x/Report.txt": "words number three",  # one file on Windows and macOS
        f"{deep}/report.txt": "words number four",
        f"{deep}/more/report.txt": "words number five",
        "[red] :smile:/report.txt": "words number six",
    }
    written, log = parse_into(tmp_path, settings, contents, "-f", extension)
    assert len(written) == len(contents)
    assert len({name.casefold() for name in written}) == len(contents)
    assert all(len(name.encode()) <= 255 and name.endswith(extension) for name in written)
    for text in contents.values():
        assert sum(text in output for output in written.values()) == 1, text
    assert f"{tmp_path / 'in' / '[red] :smile:' / 'report.txt'} -> " in log  # printed literally


@pytest.mark.parametrize(
    "name",
    ["n" * 248 + ".txt", "न" * 82 + ".txt", "a." + "x" * 248],
    ids=["long name", "long Hindi name", "long extension"],
)
def test_clashing_names_at_the_length_limit_still_finish(tmp_path, settings, name):
    """With ".md" added, these names are at or near the 255 bytes a file name may have."""
    assert 250 <= len(f"{name}.md".encode()) <= 255
    contents = {f"{folder}/{name}": f"words from folder {folder}" for folder in "abc"}
    written, _ = parse_into(tmp_path, settings, contents)
    assert sorted(text.strip() for text in written.values()) == [
        f"words from folder {folder}" for folder in "abc"
    ]
    assert all(len(output.encode()) <= 255 for output in written)


def test_without_a_clash_the_names_are_the_same_as_before(tmp_path, settings):
    contents = {"one.txt": "words here one", "sub/two.txt": "words here two"}
    written, log = parse_into(tmp_path, settings, contents)
    assert sorted(written) == ["one.txt.md", "two.txt.md"]
    assert "renamed" not in log


# ---------------------------------------------------------------------------- not UTF-8


NOT_UTF8 = {
    name: LOCALES[name] for name in ("ascii C locale", "latin-1", "cp1252", "ascii, strict")
}
EVERY_LOCALE = {**NOT_UTF8, "utf-8": LOCALES["utf-8"], "plain C locale": {"LC_ALL": "C"}}


@pytest.fixture
def hindi_files(tmp_path):
    text = tmp_path / "hindi.txt"
    text.write_text(HINDI + "\n", encoding="utf-8")
    pdf = tmp_path / f"{HINDI.split()[0]}.pdf"  # a file name the locale may lack
    pdf.write_bytes(samples.digital_pdf())
    return text, pdf


def model_that_answers(value):
    answer = json.dumps({"vendor_name": value})  # ASCII escapes, as it travels
    return SlowModel(answer={"message": {"content": answer}, "done_reason": "stop"})


def assert_no_crash(run):
    assert b"UnicodeEncodeError" not in run.stdout + run.stderr, run.stderr[-600:]


@pytest.mark.parametrize("locale", EVERY_LOCALE.values(), ids=EVERY_LOCALE.keys())
def test_json_is_utf8_whatever_the_locale(hindi_files, settings, locale):
    text, _ = hindi_files
    model = model_that_answers(HINDI)
    try:
        runs = [
            run_bytes(["parse", text, "-f", "json", "-c", settings], locale),
            run_bytes(
                ["extract", text, "-f", "json", "--base-url", model.config().llm.base_url]
                + ["-c", settings],
                locale,
            ),
        ]
    finally:
        model.close()
    for run in runs:
        assert_no_crash(run)
        assert run.returncode == 0, run.stderr
        assert HINDI.encode() in run.stdout  # real UTF-8, not "?" and not escapes
        json.loads(run.stdout.decode("utf-8"))


@pytest.mark.parametrize("locale", EVERY_LOCALE.values(), ids=EVERY_LOCALE.keys())
@pytest.mark.parametrize("command", ["extract", "engines", "triage"])
def test_the_tables_do_not_crash(hindi_files, settings, command, locale):
    text, pdf = hindi_files
    model = model_that_answers(HINDI)
    arguments = {
        "extract": ["extract", text, "--base-url", model.config().llm.base_url],
        "engines": ["engines"],
        "triage": ["triage", pdf],
    }[command]
    try:
        run = run_bytes([*arguments, "-c", settings], {**locale, "COLUMNS": "200"})
    finally:
        model.close()
    assert_no_crash(run)
    assert run.returncode == 0, run.stderr
    expected = {"extract": b"vendor_name", "engines": b"pdf_text", "triage": b"digital"}
    assert expected[command] in run.stdout


def test_a_table_replaces_only_what_the_terminal_cannot_show(hindi_files, settings):
    text, _ = hindi_files
    model = model_that_answers(f"café {HINDI}")
    try:
        arguments = ["extract", text, "--base-url", model.config().llm.base_url, "-c", settings]
        latin = run_bytes(arguments, {**LOCALES["latin-1"], "COLUMNS": "200"})
        utf8 = run_bytes(arguments, {**LOCALES["utf-8"], "COLUMNS": "200"})
    finally:
        model.close()
    shown = latin.stdout.decode("latin-1")
    assert "café" in shown and "?????? ??????" in shown  # é exists in latin-1, Hindi does not
    assert f"café {HINDI}" in utf8.stdout.decode("utf-8")  # nothing is replaced in UTF-8


@pytest.mark.parametrize("extra", [["-f", "json"], []], ids=["json", "table"])
def test_a_broken_character_from_the_model_does_not_crash(hindi_files, settings, extra):
    text, _ = hindi_files
    model = model_that_answers("bad \ud800 value")  # half of a character pair
    try:
        url = model.config().llm.base_url
        run = run_bytes(["extract", text, "--base-url", url, "-c", settings, *extra])
    finally:
        model.close()
    assert_no_crash(run)
    assert run.returncode == 0, run.stderr
    if extra:  # the JSON keeps the value, written as a JSON escape
        [value] = [f["value"] for f in json.loads(run.stdout.decode("utf-8"))["fields"]]
        assert value == "bad \ud800 value"
