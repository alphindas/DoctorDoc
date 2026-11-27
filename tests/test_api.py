"""The HTTP API, with FastAPI's TestClient. No Ollama, no Docling and no network: the model is
the same scripted one the extraction tests use, and the `config` fixture loads no heavy engine.
"""

from __future__ import annotations

import asyncio
import io
import json
import socket
import tempfile
import threading
import time
import zipfile

import httpx
import pytest

pytest.importorskip("fastapi", reason="the 'api' extra is not installed")
pytest.importorskip("python_multipart", reason="the 'api' extra is not installed")
pytest.importorskip("uvicorn", reason="the 'api' extra is not installed")

import uvicorn  # noqa: E402
import yaml  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import samples  # noqa: E402
from doctordoc import __version__, parse  # noqa: E402
from doctordoc.api import app as api_module  # noqa: E402
from doctordoc.api import create_app  # noqa: E402
from doctordoc.api.uploads import FORM_OVERHEAD_BYTES  # noqa: E402
from doctordoc.cli import _is_local  # noqa: E402
from doctordoc.cli import app as cli_app  # noqa: E402
from doctordoc.config import LLMConfig  # noqa: E402
from doctordoc.router import Router  # noqa: E402
from test_extract import GOOD_INVOICE, FakeLLM  # noqa: E402
from test_prompt_guard import Ollama  # noqa: E402

REAL_HTTPX_GET = httpx.get


# ---------------------------------------------------------------------------- fixtures


@pytest.fixture(autouse=True)
def temp_root(tmp_path, monkeypatch):
    """Every temporary file goes here, and none may be left after any test."""
    root = tmp_path / "temp"
    root.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(root))
    yield root
    assert list(root.iterdir()) == []


def server_answers(monkeypatch):
    def get(url, **kwargs):
        body = {"models": [{"name": "llama3.2:3b"}]}
        return httpx.Response(200, json=body, request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)


def server_is_down(monkeypatch):
    def get(url, **kwargs):
        raise httpx.ConnectError("refused", request=httpx.Request("GET", url))

    monkeypatch.setattr(httpx, "get", get)


@pytest.fixture(autouse=True)
def no_real_model_server(monkeypatch):
    """By default there is no model server, and using an unscripted model is a test bug."""
    server_is_down(monkeypatch)

    def refuse(config):
        raise AssertionError("this test did not script a model")

    monkeypatch.setattr("doctordoc.extract.extractor.LLMClient", refuse)


def script_model(monkeypatch, fake):
    server_answers(monkeypatch)
    monkeypatch.setattr("doctordoc.extract.extractor.LLMClient", lambda config: fake.client())


@pytest.fixture
def client(config) -> TestClient:
    return TestClient(create_app(config))


def small_client(config, max_file_mb) -> TestClient:
    config.max_file_mb = max_file_mb
    return TestClient(create_app(config))


def send(client, path, name, content, **kwargs):
    return client.post(path, files={"file": (name, content)}, **kwargs)


def extract_request(client, name, data, schema="invoice"):
    return send(client, "/extract", name, data, data={"schema": schema})


def forbid_parsing(monkeypatch):
    def refuse(*args, **kwargs):
        raise AssertionError("the file must not be parsed")

    monkeypatch.setattr(api_module, "parse", refuse)


def broken_docx() -> bytes:
    """A ZIP that looks like a Word file, with rubbish inside: detection accepts it."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("word/document.xml", "<this is not a word document")
    return buffer.getvalue()


# ---------------------------------------------------------------------------- health, engines


@pytest.mark.parametrize("set_server, reachable", [(server_answers, True), (server_is_down, False)])
def test_health_says_whether_ollama_answers(client, monkeypatch, set_server, reachable):
    set_server(monkeypatch)
    reply = client.get("/health")
    assert reply.status_code == 200
    assert reply.json() == {"status": "ok", "version": __version__, "ollama_reachable": reachable}


def test_health_with_a_really_closed_port(config, monkeypatch):
    monkeypatch.setattr(httpx, "get", REAL_HTTPX_GET)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    config.llm.base_url = f"http://127.0.0.1:{port}"
    client = TestClient(create_app(config))
    assert client.get("/health").json()["ollama_reachable"] is False
    assert extract_request(client, "a.csv", samples.csv_bytes()).status_code == 503


@pytest.mark.parametrize("url", ["not a url", "http://[broken", "ftp://127.0.0.1"])
def test_health_never_crashes_on_a_bad_server_setting(config, monkeypatch, url):
    monkeypatch.setattr(httpx, "get", REAL_HTTPX_GET)
    config.llm.base_url = url
    reply = TestClient(create_app(config)).get("/health")
    assert reply.status_code == 200 and reply.json()["ollama_reachable"] is False


def test_health_does_not_report_ollama_for_another_provider(config):
    config.llm = LLMConfig(provider="openai", base_url="http://127.0.0.1:1/v1")
    assert TestClient(create_app(config)).get("/health").json()["ollama_reachable"] is None


def test_engines_is_the_same_data_as_the_engines_command(client, config):
    rows = client.get("/engines").json()["engines"]
    assert rows == Router(config).engine_report()
    assert {"native", "docling", "rapidocr", "tesseract"} <= {r["name"] for r in rows}
    assert all(r["license"] and r["status"] for r in rows)


# ---------------------------------------------------------------------------- parse


@pytest.mark.parametrize(
    "name, make, file_type, expected",
    [
        ("rates.csv", samples.csv_bytes, "csv", "38.7"),
        ("wells.json", samples.json_bytes, "json", "B-2"),
        ("report.docx", samples.docx_bytes, "docx", "tripped out of the hole"),
    ],
)
def test_parse_returns_the_document(client, config, name, make, file_type, expected):
    data = make()
    reply = send(client, "/parse", name, data)
    assert reply.status_code == 200
    body = reply.json()
    assert (body["source"], body["file_type"], body["error"]) == (name, file_type, None)
    assert expected in reply.text and body["decisions"]
    local = json.loads(parse(data, config, filename=name).to_json())
    assert body["pages"] == local["pages"]  # the same result as the library


def test_parse_as_markdown(client, config):
    data = samples.docx_bytes()
    reply = send(client, "/parse?format=markdown", "report.docx", data)
    assert reply.status_code == 200
    assert reply.headers["content-type"].startswith("text/markdown")
    assert reply.text == parse(data, config, filename="report.docx").to_markdown()
    assert "# Daily Report" in reply.text


def test_explain_adds_the_decision_log_to_markdown_for_every_file(client):
    reply = send(client, "/parse?format=markdown&explain=true", "all.zip", samples.zip_bytes())
    assert reply.status_code == 200 and set(reply.json()) == {"markdown", "decisions"}
    decisions = reply.json()["decisions"]
    assert {"data.csv", "invoice.pdf"} <= {d["source"] for d in decisions}
    assert {"scope", "engine", "outcome", "reason", "quality", "seconds"} <= set(decisions[0])


def test_the_json_document_holds_the_decision_log_with_or_without_explain(client):
    plain = send(client, "/parse", "rates.csv", samples.csv_bytes()).json()
    explained = send(client, "/parse?explain=true", "rates.csv", samples.csv_bytes()).json()
    assert set(plain) == set(explained) and explained["decisions"][0]["engine"] == "native"


def test_an_unknown_format_or_a_missing_file_is_refused(client):
    assert send(client, "/parse?format=pdf", "a.csv", samples.csv_bytes()).status_code == 422
    assert client.post("/parse").status_code == 422
    assert client.post("/parse", data={"file": "just text"}).status_code == 422


@pytest.mark.parametrize(
    "name, data, words",
    [
        ("broken.pdf", b"%PDF-1.7\nthis is not really a pdf", "corrupt"),
        ("locked.pdf", samples.digital_pdf(encrypt="secret"), "password"),
        ("empty.csv", b"", "empty"),
        ("blob.bin", b"\x00\x01\x02\x03" * 200, "nrecognised"),
        ("broken.zip", b"PK\x03\x04 not really a zip", "damaged"),
        ("broken.docx", broken_docx(), "No engine could read this file"),
        ("broken.png", b"\x89PNG\r\n\x1a\n" + b"junk" * 50, "No engine could read this file"),
    ],
    ids=["pdf", "password", "empty", "binary", "zip", "docx", "png"],
)
def test_unsupported_and_broken_files_get_415(client, name, data, words):
    for reply in (send(client, "/parse", name, data), send_markdown(client, name, data)):
        assert reply.status_code == 415, reply.text
        assert words in reply.json()["detail"]
        assert "Traceback" not in reply.text


def send_markdown(client, name, data):
    return send(client, "/parse?format=markdown", name, data)


def test_a_readable_page_with_no_text_is_not_refused(client):
    reply = send(client, "/parse", "blank.pdf", samples.blank_pdf())
    assert reply.status_code == 200 and reply.json()["pages"][0]["kind"] == "blank"


# ---------------------------------------------------------------------------- upload limit


def test_a_too_large_upload_gets_413_and_is_not_parsed(config, monkeypatch):
    forbid_parsing(monkeypatch)
    client = small_client(config, 1)
    for path, extra in [("/parse", {}), ("/extract", {"data": {"schema": "invoice"}})]:
        reply = send(client, path, "big.csv", b"a,b\n" * 600_000, **extra)
        assert reply.status_code == 413
        assert reply.json() == {"detail": "The upload is over the 1 MB limit (max_file_mb)."}


def test_the_limit_on_the_file_itself_is_exact(config):
    client = small_client(config, 0.01)  # 10485.76 bytes
    row = b"abc,def\n"
    fits = (row * 2000)[:10485]
    assert send(client, "/parse", "a.csv", fits).status_code == 200
    # one byte more is still inside the allowance for the form around the file
    assert send(client, "/parse", "a.csv", fits + b"x").status_code == 413


def drive(app, chunks, headers=()):
    """Feed a raw request to the app piece by piece: (status, body, bytes the app asked for)."""
    pulled, sent = [], []
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/parse",
        "raw_path": b"/parse",
        "query_string": b"",
        "root_path": "",
        "headers": [(b"content-type", b"multipart/form-data; boundary=XX"), *headers],
        "client": ("127.0.0.1", 50000),
        "server": ("127.0.0.1", 8000),
    }
    source = iter(chunks)

    async def receive():
        chunk = next(source, None)
        if chunk is None:
            return {"type": "http.request", "body": b"", "more_body": False}
        pulled.append(len(chunk))
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def collect(message):
        sent.append(message)

    asyncio.run(app(scope, receive, collect))
    status = next(m["status"] for m in sent if m["type"] == "http.response.start")
    body = b"".join(m.get("body", b"") for m in sent if m["type"] == "http.response.body")
    return status, json.loads(body), sum(pulled)


def endless_upload(chunk_size, count):
    yield b'--XX\r\nContent-Disposition: form-data; name="file"; filename="big.csv"\r\n\r\n'
    for _ in range(count):
        yield b"a" * chunk_size


def test_an_upload_of_unknown_size_is_stopped_while_it_is_still_arriving(config, monkeypatch):
    forbid_parsing(monkeypatch)
    config.max_file_mb = 1
    chunk = 64 * 1024
    status, body, pulled = drive(create_app(config), endless_upload(chunk, 1600))  # 100 MB
    assert status == 413 and "1 MB limit" in body["detail"]
    assert pulled <= 1024 * 1024 + FORM_OVERHEAD_BYTES + chunk  # not the 100 MB on offer


def test_a_declared_size_over_the_limit_is_refused_before_anything_is_read(config):
    config.max_file_mb = 1
    headers = [(b"content-length", b"104857600")]
    status, body, pulled = drive(create_app(config), endless_upload(1024, 5), headers)
    assert (status, pulled) == (413, 0) and "1 MB limit" in body["detail"]


# ---------------------------------------------------------------------------- temporary files


def spy_on_parse(monkeypatch, then=None):
    seen = []

    def spy(path, config, **kwargs):
        seen.append((path, path.exists(), path.read_bytes(), kwargs.get("filename")))
        if then:
            raise then
        return parse(path, config, **kwargs)

    monkeypatch.setattr(api_module, "parse", spy)
    return seen


def test_the_upload_is_on_disk_only_during_its_request(client, temp_root, monkeypatch):
    seen = spy_on_parse(monkeypatch)
    assert send(client, "/parse", "rates.csv", samples.csv_bytes()).status_code == 200
    [(path, existed, content, _)] = seen
    assert existed and content == samples.csv_bytes()
    assert path.parent.parent == temp_root and path.parent.name.startswith("doctordoc-api-")
    assert not path.exists() and not path.parent.exists()


def test_an_unexpected_error_gives_no_details_and_leaves_no_file(config, monkeypatch):
    seen = spy_on_parse(monkeypatch, then=RuntimeError("internal secret"))
    client = TestClient(create_app(config), raise_server_exceptions=False)
    reply = send(client, "/parse", "rates.csv", samples.csv_bytes())
    assert reply.status_code == 500
    assert reply.json() == {"detail": "Internal error. See the server log."}
    assert seen[0][1] and not seen[0][0].parent.exists()


@pytest.mark.parametrize(
    "sent, cleaned",
    [
        ("../../etc/passwd.csv", "passwd.csv"),
        ("..\\..\\windows\\evil.csv", "evil.csv"),
        ("..", ".."),
        (".", "unnamed"),
        ("rm -rf $(x);.csv", "rm -rf _(x)_.csv"),
        ("井" * 300 + ".csv", "井" * 120),
    ],
)
def test_file_names_are_cleaned(client, temp_root, monkeypatch, sent, cleaned):
    seen = spy_on_parse(monkeypatch)
    reply = send(client, "/parse", sent, samples.csv_bytes())
    assert reply.status_code == 200, reply.text
    assert reply.json()["source"] == cleaned
    path = seen[0][0]
    assert path.parent.parent == temp_root and path.name.startswith("upload-")
    assert len(path.name.encode()) < 100


def test_the_parse_cache_is_off_so_nothing_is_stored(config, tmp_path):
    config.cache_dir = tmp_path / "cache"
    client = TestClient(create_app(config))
    assert send(client, "/parse", "rates.csv", samples.csv_bytes()).status_code == 200
    assert not (tmp_path / "cache").exists()
    assert config.cache_dir == tmp_path / "cache"  # the caller's settings are not changed


def test_there_are_no_cors_headers(client):
    headers = {"Origin": "https://example.com"}
    assert "access-control-allow-origin" not in client.get("/health", headers=headers).headers
    preflight = client.options(
        "/parse", headers={**headers, "Access-Control-Request-Method": "POST"}
    )
    assert preflight.status_code == 405
    assert "access-control-allow-origin" not in preflight.headers


# ---------------------------------------------------------------------------- extract


def test_extract_returns_the_same_json_as_the_command_line(
    config, client, invoice_pdf, tmp_path, monkeypatch
):
    script_model(monkeypatch, FakeLLM(GOOD_INVOICE))
    reply = extract_request(client, "invoice.pdf", invoice_pdf)
    assert reply.status_code == 200, reply.text

    pdf, settings = tmp_path / "invoice.pdf", tmp_path / "settings.yaml"
    pdf.write_bytes(invoice_pdf)
    keys = {"gpu", "page_chains", "file_chains"}
    settings.write_text(yaml.safe_dump(config.model_dump(mode="json", include=keys)))
    script_model(monkeypatch, FakeLLM(GOOD_INVOICE))
    command = ["extract", str(pdf), "--schema", "invoice", "--format", "json", "-c", str(settings)]
    cli = CliRunner().invoke(cli_app, command)
    assert cli.exit_code == 0, cli.output
    assert reply.json() == json.loads(cli.stdout)
    assert reply.json()["fields"] and reply.json()["needs_review"] == []


def test_extract_shows_the_length_guard_on_the_review_list(client, invoice_pdf, monkeypatch):
    script_model(monkeypatch, Ollama(prompt_eval_count=7500))
    reply = extract_request(client, "invoice.pdf", invoice_pdf)
    assert reply.status_code == 200
    assert "document" in reply.json()["needs_review"]
    assert "prompt_length" in [i["rule"] for i in reply.json()["validation_issues"]]


@pytest.mark.parametrize("schema", ["nope", "os:system", "doctordoc.extract:Invoice", "Invoice"])
def test_only_built_in_schema_names_are_accepted(client, monkeypatch, schema):
    forbid_parsing(monkeypatch)
    monkeypatch.setattr(
        "doctordoc.extract.extractor.importlib.import_module",
        lambda name: pytest.fail(f"imported {name}"),
    )
    reply = extract_request(client, "a.csv", samples.csv_bytes(), schema)
    assert reply.status_code == 422
    assert reply.json() == {
        "detail": "Unknown schema. Use one of: invoice, receipt, drilling, form."
    }


def test_the_schema_name_is_required(client):
    reply = send(client, "/extract", "a.csv", samples.csv_bytes())
    assert reply.status_code == 422


def test_extract_without_ollama_gets_503_before_any_parsing(client, monkeypatch):
    forbid_parsing(monkeypatch)
    reply = extract_request(client, "a.csv", samples.csv_bytes())
    assert reply.status_code == 503
    assert reply.json()["detail"].startswith("Cannot reach Ollama, so /extract cannot run.")
    assert "Traceback" not in reply.text and "11434" not in reply.text


def test_ollama_going_away_during_a_request_also_gets_503(client, invoice_pdf, monkeypatch):
    class Dies(FakeLLM):
        def client(self):
            def handler(request):
                server_is_down(monkeypatch)
                raise httpx.ConnectError("refused")

            return api_module_llm(handler)

    script_model(monkeypatch, Dies())
    reply = extract_request(client, "invoice.pdf", invoice_pdf)
    assert reply.status_code == 503 and "Cannot reach Ollama" in reply.json()["detail"]


def api_module_llm(handler):
    from doctordoc.extract import LLMClient

    return LLMClient(LLMConfig(), transport=httpx.MockTransport(handler))


def test_a_failed_model_step_gets_502_with_the_report(client, invoice_pdf, monkeypatch):
    class NoSuchModel(FakeLLM):
        def client(self):
            return api_module_llm(lambda r: httpx.Response(404, json={"error": "model not found"}))

    script_model(monkeypatch, NoSuchModel())
    reply = extract_request(client, "invoice.pdf", invoice_pdf)
    assert reply.status_code == 502
    assert "model not found" in reply.json()["error"] and reply.json()["fields"] == []


def test_extract_from_a_broken_file_gets_415(client, monkeypatch):
    script_model(monkeypatch, FakeLLM({}))
    for name, data in [("x.pdf", b"%PDF-1.7 junk"), ("x.docx", broken_docx())]:
        assert extract_request(client, name, data).status_code == 415


def test_extract_from_a_file_without_text_gets_422_with_the_report(client, monkeypatch):
    script_model(monkeypatch, FakeLLM({}))
    reply = extract_request(client, "blank.pdf", samples.blank_pdf())
    assert reply.status_code == 422
    assert reply.json()["error"] == "The document has no text to extract from."


# ---------------------------------------------------------------------------- one at a time


def test_heavy_requests_take_turns_and_health_still_answers(config, monkeypatch):
    script_model(monkeypatch, FakeLLM(GOOD_INVOICE))
    running, most, started, release = [], [], threading.Event(), threading.Event()
    real_parse, real_extract = api_module.parse, api_module.extract

    def counted(real):
        def work(*args, **kwargs):
            running.append(1)
            most.append(len(running))
            started.set()
            release.wait(10)
            time.sleep(0.05)
            try:
                return real(*args, **kwargs)
            finally:
                running.pop()

        return work

    monkeypatch.setattr(api_module, "parse", counted(real_parse))
    monkeypatch.setattr(api_module, "extract", counted(real_extract))
    replies = []
    with TestClient(create_app(config)) as client:  # one event loop, like a real server
        jobs = [
            lambda: send(client, "/parse", "a.csv", samples.csv_bytes()),
            lambda: send(client, "/parse", "b.csv", samples.csv_bytes()),
            lambda: extract_request(client, "c.csv", samples.csv_bytes()),
        ]
        threads = [threading.Thread(target=lambda j=job: replies.append(j())) for job in jobs]
        try:
            for thread in threads:
                thread.start()
            first_started = started.wait(10)
            time.sleep(0.5)  # time for the other two to reach the lock
            health = client.get("/health")  # asked while a request is working
            while_busy = (len(most), len(replies))
        finally:  # no assert may come before this, or a failing test would hang
            release.set()
            for thread in threads:
                thread.join(30)
    assert first_started and health.status_code == 200
    assert while_busy == (1, 0)  # one is working, the other two are waiting
    assert [r.status_code for r in replies] == [200, 200, 200]
    assert len(most) == 4 and max(most) == 1  # parse, parse, then parse + model for extract


# ---------------------------------------------------------------------------- the command


@pytest.mark.parametrize(
    "arguments, host, port, warns",
    [
        ([], "127.0.0.1", 8000, False),
        (["--host", "localhost", "--port", "9001"], "localhost", 9001, False),
        (["--host", "0.0.0.0"], "0.0.0.0", 8000, True),
    ],
)
def test_the_api_command_starts_the_server(monkeypatch, arguments, host, port, warns):
    started = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: started.append((app, kwargs)))
    result = CliRunner().invoke(cli_app, ["api", *arguments])
    assert result.exit_code == 0, result.output
    [(app, kwargs)] = started
    assert kwargs == {"host": host, "port": port} and app.title == "DoctorDoc"
    assert ("no login" in result.output) is warns


@pytest.mark.parametrize(
    "host, local",
    [
        ("127.0.0.1", True),
        ("127.8.8.8", True),
        ("::1", True),
        ("localhost", True),
        ("0.0.0.0", False),
        ("::", False),
        ("192.168.1.20", False),
        ("my-laptop.lan", False),
        ("", False),
    ],
)
def test_which_hosts_are_local(host, local):
    assert _is_local(host) is local


def test_the_api_command_says_how_to_install_the_extra(monkeypatch):
    import importlib.util

    real = importlib.util.find_spec
    monkeypatch.setattr(
        importlib.util, "find_spec", lambda n, *a: None if n == "fastapi" else real(n, *a)
    )
    result = CliRunner().invoke(cli_app, ["api"])
    assert result.exit_code == 1 and 'pip install "doctordoc[api]"' in result.output
