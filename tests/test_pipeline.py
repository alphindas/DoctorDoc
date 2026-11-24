import samples
from doctordoc import parse, parse_many
from doctordoc.config import Config, IntakeLimits
from doctordoc.models import PageKind


def test_digital_pdf_end_to_end(config):
    doc = parse(samples.digital_pdf(), config, filename="inv.pdf")
    assert doc.file_type == "pdf" and doc.error is None
    assert "INV-2024-0042" in doc.text and doc.pages[0].kind == PageKind.DIGITAL
    assert doc.metadata["page_kinds"] == {"digital": 1}


def test_never_raises_on_bad_input(config):
    for data, name in [
        (b"", "a.pdf"),
        (samples.digital_pdf()[:300], "b.pdf"),
        (samples.digital_pdf(encrypt="pw"), "c.pdf"),
        (b"\x00\x01\xff junk", "d.bin"),
        (samples.zip_bytes()[:40], "e.zip"),
    ]:
        doc = parse(data, config, filename=name)
        assert doc.error, name


def test_missing_file_gives_error_not_exception(config):
    assert "Could not read" in parse("/no/such/file.pdf", config).error


def test_wrong_extension_is_warned(config):
    doc = parse(samples.docx_bytes(), config, filename="oops.pdf")
    assert doc.file_type == "docx" and any("Extension" in w for w in doc.warnings)


def test_zip_children_are_parsed_recursively(config):
    inner = samples.zip_bytes({"deep.txt": b"deep text inside inner zip"})
    outer = samples.zip_bytes({"inner.zip": inner, "top.txt": b"top text of the outer zip"})
    doc = parse(outer, config, filename="outer.zip")
    names = [d.source for d in doc.iter_all()]
    assert names == ["outer.zip", "inner.zip", "deep.txt", "top.txt"]


def test_depth_limit(config):
    data = b"leaf text for the deepest file"
    for level in range(5):
        data = samples.zip_bytes({f"level{level}.zip" if level < 4 else "leaf.txt": data})
    config.intake = IntakeLimits(max_depth=2)
    doc = parse(data, config, filename="start.zip")
    assert any("Not unpacked" in w for d in doc.iter_all() for w in d.warnings)


def test_eml_attachments_become_children(config):
    doc = parse(samples.eml_bytes(), config, filename="mail.eml")
    assert [c.file_type for c in doc.children] == ["pdf", "csv"]
    assert "Daily report attached" in doc.text


def test_cache_hit_in_parse_many_and_on_disk(config, tmp_path):
    data = samples.digital_pdf()
    docs = parse_many([data, data], config)
    assert docs[1].metadata.get("cache_hit") and not docs[0].metadata.get("cache_hit")
    config.cache_dir = tmp_path
    parse(data, config)
    assert list(tmp_path.glob("*.json"))
    assert parse(data, config).metadata.get("cache_hit")


def test_changing_settings_changes_cache_key(config, tmp_path):
    config.cache_dir = tmp_path
    parse(samples.digital_pdf(), config)
    config.quality_threshold = 0.9
    assert not parse(samples.digital_pdf(), config).metadata.get("cache_hit")


def test_config_from_yaml(tmp_path):
    path = tmp_path / "doctordoc.yaml"
    path.write_text("quality_threshold: 0.7\ngpu: 'off'\nllm:\n  model: qwen2.5:3b\n")
    cfg = Config.from_yaml(path)
    assert cfg.quality_threshold == 0.7 and cfg.llm.model == "qwen2.5:3b" and not cfg.use_gpu
