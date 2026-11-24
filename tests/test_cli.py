import json

import pytest
from typer.testing import CliRunner

import samples
from doctordoc.cli import app
from doctordoc.engines.ocr_paddle import _blocks_from_result
from doctordoc.models import BlockType

runner = CliRunner()


@pytest.fixture
def cfg_file(tmp_path):
    path = tmp_path / "doctordoc.yaml"
    path.write_text(
        "gpu: 'off'\npage_chains:\n  digital: [pdf_text]\n  scanned: [pdf_text]\n"
        "file_chains:\n  txt: [native]\n  csv: [native]\n"
    )
    return str(path)


@pytest.fixture
def pdf(tmp_path):
    path = tmp_path / "invoice.pdf"
    path.write_bytes(samples.digital_pdf())
    return path


def test_parse_markdown_to_stdout(pdf, cfg_file):
    result = runner.invoke(app, ["parse", str(pdf), "-c", cfg_file])
    assert result.exit_code == 0 and "INV-2024-0042" in result.output


def test_parse_json_and_out_dir(pdf, cfg_file, tmp_path):
    out = tmp_path / "out"
    result = runner.invoke(app, ["parse", str(pdf), "-f", "json", "-o", str(out), "-c", cfg_file])
    assert result.exit_code == 0
    data = json.loads((out / "invoice.pdf.json").read_text())
    assert data["file_type"] == "pdf" and data["pages"][0]["kind"] == "digital"


def test_parse_folder_and_exit_code_on_errors(tmp_path, cfg_file):
    (tmp_path / "ok.txt").write_text("hello there world")
    (tmp_path / "empty.txt").write_text("")
    result = runner.invoke(app, ["parse", str(tmp_path), "-c", cfg_file])
    assert "hello there world" in result.output and result.exit_code == 1  # one file failed


def test_explain_prints_the_decision_table(pdf, cfg_file):
    result = runner.invoke(app, ["parse", str(pdf), "--explain", "-c", cfg_file])
    assert "Router decisions" in result.output and "pdf_text" in result.output


def test_triage_command(pdf):
    result = runner.invoke(app, ["triage", str(pdf)])
    assert result.exit_code == 0 and "digital" in result.output


def test_engines_command_lists_every_engine():
    result = runner.invoke(app, ["engines"])
    for name in (
        "native",
        "pdf_text",
        "docling",
        "markitdown",
        "paddle_vl",
        "rapidocr",
        "tesseract",
        "cloud_vlm",
    ):
        assert name in result.output


def test_paddle_result_mapping():
    res = {
        "parsing_res_list": [
            {
                "block_label": "doc_title",
                "block_content": "Report",
                "block_bbox": [10, 10, 200, 40],
            },
            {
                "block_label": "text",
                "block_content": "Some paragraph text.",
                "block_bbox": [10, 50, 400, 90],
            },
            {
                "block_label": "table",
                "block_content": "<table><tr><td>a</td><td>b</td></tr></table>",
                "block_bbox": [10, 100, 400, 200],
            },
            {"block_label": "image", "block_content": "", "block_bbox": [10, 210, 400, 400]},
            {"block_label": "text", "block_content": "", "block_bbox": [0, 0, 1, 1]},
        ]
    }
    blocks = _blocks_from_result(res, scale=2.0, engine="paddle_vl")
    assert [b.type for b in blocks] == [
        BlockType.HEADING,
        BlockType.PARAGRAPH,
        BlockType.TABLE,
        BlockType.IMAGE,
    ]
    assert blocks[0].level == 1 and blocks[2].table.rows == [["a", "b"]]
    assert blocks[0].bbox.x1 == 100.0  # pixels divided by scale -> PDF points


def test_ui_command_starts_streamlit_with_the_upload_limit_and_the_config(monkeypatch, cfg_file):
    import subprocess

    seen = {}

    def fake_call(command, env=None, cwd=None):
        seen.update(command=command, env=env, cwd=cwd)
        return 0

    monkeypatch.setattr(subprocess, "call", fake_call)
    result = runner.invoke(app, ["ui", "--port", "8123", "-c", cfg_file])
    assert result.exit_code == 0
    command = seen["command"]
    assert command[1:4] == ["-m", "streamlit", "run"] and command[4].endswith("ui/app.py")
    assert "8123" in command and "200" in command  # port, and max_file_mb as the upload limit
    assert seen["env"]["DOCTORDOC_UI_CONFIG"].endswith("doctordoc.yaml")


def test_extract_json_uses_the_stable_report_shape(pdf, cfg_file, monkeypatch):
    from doctordoc.extract.models import ExtractionResult, FieldResult

    fake = ExtractionResult(
        schema_name="Invoice",
        model="m",
        fields=[FieldResult(path="total", value=1.5, grounded=False, confidence=0.2)],
        needs_review=["total"],
    )
    import doctordoc.extract as extract_module

    monkeypatch.setattr(extract_module, "extract", lambda *a, **k: fake)
    result = runner.invoke(app, ["extract", str(pdf), "-f", "json", "-c", cfg_file])
    report = json.loads(result.output)
    assert report["fields"][0] == {
        "field": "total",
        "value": 1.5,
        "confidence": 0.2,
        "grounded": False,
        "page": None,
        "source_text": None,
    }
    assert report["needs_review"] == ["total"] and report["validation_issues"] == []
