"""The API's waiting limit, docs switch, extraction warnings and cross-site checks.

Like test_api.py: no Ollama, no Docling and no network.
"""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

pytest.importorskip("fastapi", reason="the 'api' extra is not installed")
pytest.importorskip("python_multipart", reason="the 'api' extra is not installed")
pytest.importorskip("uvicorn", reason="the 'api' extra is not installed")

import uvicorn  # noqa: E402
import yaml  # noqa: E402
from pydantic import ValidationError  # noqa: E402
from typer.testing import CliRunner  # noqa: E402

import samples  # noqa: E402
from doctordoc.api import app as api_module  # noqa: E402
from doctordoc.api import create_app  # noqa: E402
from doctordoc.cli import app as cli_app  # noqa: E402
from doctordoc.config import ApiConfig, Config  # noqa: E402

# the helpers and fixtures of test_api.py (the two autouse fixtures apply here too)
from test_api import (  # noqa: E402
    BASE_URL,
    LOCAL_HOST,
    client,  # noqa: F401
    drive,
    endless_upload,
    extract_request,
    forbid_parsing,
    local_client,
    no_real_model_server,  # noqa: F401
    script_model,
    send,
    spy_on_parse,
    temp_root,  # noqa: F401
    upload_scope,
)
from test_extract import GOOD_INVOICE, FakeLLM  # noqa: E402


def csv_upload(client, **kwargs):  # noqa: F811
    return send(client, "/parse", "rates.csv", samples.csv_bytes(), **kwargs)


# ---------------------------------------------------------------------------- waiting limit


def hold_parsing(monkeypatch):
    """The controlled delay: every parse waits until the test releases it."""
    release, parsed = threading.Event(), []
    real = api_module.parse

    def held(*args, **kwargs):
        parsed.append(kwargs.get("filename"))
        release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(api_module, "parse", held)
    return release, parsed


def upload_folders(temp_root):  # noqa: F811
    return set(temp_root.glob("doctordoc-api-*"))


def wait_until(condition, seconds=10):
    deadline = time.monotonic() + seconds
    while not condition():
        if time.monotonic() > deadline:
            return False
        time.sleep(0.02)
    return True


@pytest.mark.parametrize("setting, waiting", [(0, 0), (2, 2), (None, 4)], ids=["0", "2", "default"])
def test_the_request_over_the_waiting_limit_gets_503_before_its_upload_is_read(
    config,
    temp_root,  # noqa: F811
    monkeypatch,
    setting,
    waiting,
):
    if setting is not None:
        config.api.max_waiting = setting
    release, parsed = hold_parsing(monkeypatch)
    app = create_app(config)
    replies, extra = [], {}
    with local_client(app) as client:  # noqa: F811  (one event loop, like a real server)
        threads = [
            threading.Thread(target=lambda: replies.append(csv_upload(client)), daemon=True)
            for _ in range(1 + waiting)  # one works, `waiting` wait
        ]
        try:
            for thread in threads:
                thread.start()
            # every one of them has its upload on disk, so every one has been let in
            all_in = wait_until(lambda: len(upload_folders(temp_root)) == 1 + waiting)
            folders = upload_folders(temp_root)
            one_more = threading.Thread(
                target=lambda: extra.update(reply=csv_upload(client)), daemon=True
            )
            one_more.start()
            one_more.join(5)
            refused_in_time = not one_more.is_alive()
            # the same request as raw pieces, to see how much of its body the server asks for
            raw_status, raw_body, pulled = drive(app, endless_upload(64 * 1024, 100))
            folders_after, working, done = upload_folders(temp_root), len(parsed), len(replies)
            others = [client.get(path).status_code for path in ("/health", "/engines")]
        finally:  # no assert may come before this, or a failing test would hang
            release.set()
            for thread in threads:
                thread.join(30)
        later = csv_upload(client)

    assert all_in and refused_in_time
    reply = extra["reply"]
    assert reply.status_code == 503 and reply.headers["retry-after"] == "30"
    assert f"(api.max_waiting is {waiting})" in reply.json()["detail"]
    assert (raw_status, pulled) == (503, 0)  # not one byte read, so none written to disk
    assert "The server is busy" in raw_body["detail"]
    assert folders_after == folders
    assert (working, done) == (1, 0)  # still only one working; nobody had finished
    assert others == [200, 200]  # these two never wait
    assert [r.status_code for r in replies] == [200] * (1 + waiting)
    assert later.status_code == 200  # the places are free again


def test_a_request_holds_its_place_from_the_moment_it_arrives(config):
    """Otherwise many uploads arriving together could all pass the check."""
    config.api.max_waiting = 0
    app = create_app(config)

    async def scenario():
        asked, hang_up, first_sent = asyncio.Event(), asyncio.Event(), []

        async def still_uploading():
            asked.set()
            await hang_up.wait()
            return {"type": "http.disconnect"}

        async def collect(message):
            first_sent.append(message)

        first = asyncio.create_task(app(upload_scope([LOCAL_HOST]), still_uploading, collect))
        await asyncio.wait_for(asked.wait(), 5)
        second = await asyncio.to_thread(drive, app, endless_upload(1024, 2))
        hang_up.set()  # the first client goes away without finishing
        await asyncio.wait_for(first, 5)
        return second

    status, body, pulled = asyncio.run(scenario())
    assert (status, pulled) == (503, 0) and "The server is busy" in body["detail"]
    assert csv_upload(local_client(app)).status_code == 200  # a lost client frees its place


def test_every_way_a_request_can_end_frees_its_place(config, invoice_pdf, monkeypatch):
    config.api.max_waiting = 0  # one place only: a single leak would show at once
    config.max_file_mb = 1
    app = create_app(config)
    client = local_client(app, raise_server_exceptions=False)  # noqa: F811
    big = b"a,b\n" * 600_000
    statuses = [
        drive(app, endless_upload(64 * 1024, 100))[0],  # stopped while arriving
        send(client, "/parse", "big.csv", big).status_code,  # refused by its declared size
        send(client, "/parse", "x.pdf", b"%PDF-1.7 junk").status_code,
        client.post("/parse").status_code,
        extract_request(client, "a.csv", samples.csv_bytes(), "nope").status_code,
        extract_request(client, "a.csv", samples.csv_bytes()).status_code,  # no model server
    ]
    spy_on_parse(monkeypatch, then=RuntimeError("boom"))
    statuses.append(csv_upload(client).status_code)
    monkeypatch.undo()
    statuses.append(csv_upload(client).status_code)
    assert statuses == [413, 413, 415, 422, 422, 503, 500, 200]


def test_a_503_for_a_missing_model_server_is_not_a_queue_refusal(client):  # noqa: F811
    reply = extract_request(client, "a.csv", samples.csv_bytes())
    assert reply.status_code == 503 and "retry-after" not in reply.headers
    assert "Cannot reach Ollama" in reply.json()["detail"]


def test_the_waiting_limit_setting(monkeypatch):
    assert Config().api.max_waiting == 4
    monkeypatch.setenv("DOCTORDOC_API__MAX_WAITING", "1")
    assert Config().api.max_waiting == 1
    with pytest.raises(ValidationError):
        ApiConfig(max_waiting=-1)


# ---------------------------------------------------------------------------- docs pages


DOC_PAGES = ["/docs", "/redoc", "/openapi.json"]


def test_the_docs_pages_are_on_by_default(client):  # noqa: F811
    assert [client.get(path).status_code for path in DOC_PAGES] == [200, 200, 200]


def test_the_docs_setting_turns_the_pages_off_and_nothing_else(config):
    config.api.docs = False
    client = local_client(create_app(config))  # noqa: F811
    for path in [*DOC_PAGES, "/docs/oauth2-redirect"]:
        assert client.get(path).status_code == 404, path
    assert client.get("/health").status_code == 200
    assert csv_upload(client).status_code == 200


def start_server(monkeypatch, *arguments):
    """Run `doctordoc api` without a real server: (the app it built, what it printed)."""
    started = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: started.append(app))
    result = CliRunner().invoke(cli_app, ["api", *arguments])
    assert result.exit_code == 0, result.output
    return started[0], result.output


def settings_file(tmp_path, **api):
    path = tmp_path / "settings.yaml"
    path.write_text(yaml.safe_dump({"api": api}))
    return str(path)


@pytest.mark.parametrize(
    "flags, setting, status",
    [([], None, 200), (["--no-docs"], None, 404), ([], False, 404), (["--no-docs"], True, 404)],
    ids=["default", "flag", "setting", "flag-wins"],
)
def test_the_no_docs_flag_and_setting(tmp_path, monkeypatch, flags, setting, status):
    if setting is not None:
        flags = [*flags, "--config", settings_file(tmp_path, docs=setting)]
    app, _ = start_server(monkeypatch, *flags)
    client = local_client(app)  # noqa: F811
    assert [client.get(path).status_code for path in DOC_PAGES] == [status] * 3


# ---------------------------------------------------------------------------- warnings


def warnings_for(client, name, data):  # noqa: F811
    reply = extract_request(client, name, data)
    assert reply.status_code == 200, reply.text
    return reply.json()["warnings"]


def test_a_wrong_file_extension_is_in_the_warnings(client, monkeypatch):  # noqa: F811
    script_model(monkeypatch, FakeLLM(GOOD_INVOICE))
    [warning] = warnings_for(client, "report.pdf", samples.docx_bytes())
    assert warning.startswith("Extension '.pdf' says pdf, but the content is docx.")


def test_the_command_line_json_has_no_warnings_key(config, tmp_path, monkeypatch):
    script_model(monkeypatch, FakeLLM(GOOD_INVOICE))
    wrong = tmp_path / "report.pdf"
    wrong.write_bytes(samples.docx_bytes())
    keys = {"gpu", "page_chains", "file_chains"}
    settings = tmp_path / "settings.yaml"
    settings.write_text(yaml.safe_dump(config.model_dump(mode="json", include=keys)))
    monkeypatch.setenv("COLUMNS", "100000")  # the known wrapping bug is not what is tested here
    cli = CliRunner().invoke(cli_app, ["extract", str(wrong), "-f", "json", "-c", str(settings)])
    assert cli.exit_code == 0, cli.output
    assert "warnings" not in json.loads(cli.stdout)
    assert "Extension '.pdf'" in cli.stderr  # still printed separately


def test_a_split_long_document_is_in_the_warnings(config, invoice_pdf, monkeypatch):
    config.llm.max_chars_per_call = 120
    fake = FakeLLM(GOOD_INVOICE)
    script_model(monkeypatch, fake)
    warnings = warnings_for(local_client(create_app(config)), "invoice.pdf", invoice_pdf)
    calls = len(fake.requests)
    assert calls > 1
    assert f"Long document: asked the model {calls} times (split by page)." in warnings


def test_ignored_zip_contents_are_in_the_warnings_of_the_422(client, monkeypatch):  # noqa: F811
    fake = FakeLLM(GOOD_INVOICE)
    script_model(monkeypatch, fake)
    reply = extract_request(client, "all.zip", samples.zip_bytes())
    assert reply.status_code == 422 and fake.requests == []
    [warning] = reply.json()["warnings"]
    assert warning.startswith("Ignored ZIP contents: 3 file(s) were not read (")
    assert "invoice.pdf, readme.txt, data.csv" in warning


def test_a_long_list_of_ignored_files_is_shortened(client, monkeypatch):  # noqa: F811
    script_model(monkeypatch, FakeLLM(GOOD_INVOICE))
    members = {f"f{n:02}.txt": f"text {n}".encode() for n in range(13)}
    reply = extract_request(client, "many.zip", samples.zip_bytes(members))
    [warning] = reply.json()["warnings"]
    assert "13 file(s)" in warning and "f09.txt and 3 more)" in warning
    assert "f10.txt" not in warning


def test_ignored_email_attachments_are_in_the_warnings(client, monkeypatch):  # noqa: F811
    fake = FakeLLM(GOOD_INVOICE)
    script_model(monkeypatch, fake)
    [warning] = warnings_for(client, "mail.eml", samples.eml_bytes())
    assert warning.startswith(
        "Ignored email attachments: 2 file(s) were not read (invoice.pdf, rates.csv)."
    )
    assert "The email's own text" in warning
    sent = json.dumps(fake.requests)
    assert "Please find the invoice attached" in sent and "rates.csv" in sent
    assert "38.7" not in sent  # the warning is true: the attached CSV's content was not sent


def test_nothing_ignored_gives_an_empty_list(client, monkeypatch):  # noqa: F811
    script_model(monkeypatch, FakeLLM(GOOD_INVOICE))
    assert warnings_for(client, "mail.eml", samples.eml_bytes(with_attachment=False)) == []


def test_skipped_zip_members_are_still_reported(config, monkeypatch):
    config.intake.max_member_mb = 0.00001  # every member is too large to unpack
    script_model(monkeypatch, FakeLLM(GOOD_INVOICE))
    reply = extract_request(local_client(create_app(config)), "all.zip", samples.zip_bytes())
    assert reply.status_code == 422
    assert any(w.startswith("Skipped 'invoice.pdf'") for w in reply.json()["warnings"])


def test_a_failed_model_step_also_has_the_warnings(client, monkeypatch):  # noqa: F811
    script_model(monkeypatch, FakeLLM("not json"))
    reply = extract_request(client, "report.pdf", samples.docx_bytes())
    assert reply.status_code == 502 and reply.json()["error"]
    assert reply.json()["warnings"][0].startswith("Extension '.pdf'")


def test_parse_is_unchanged(client):  # noqa: F811
    body = send(client, "/parse", "report.pdf", samples.docx_bytes()).json()
    assert [w[:16] for w in body["warnings"]] == ["Extension '.pdf'"]


# ---------------------------------------------------------------------------- Origin


BAD_ORIGINS = {
    "another site": "http://evil.example",
    "same name on it": "http://127.0.0.1.evil.example:8000",
    "another port": "http://127.0.0.1:3000",
    "no port": "http://127.0.0.1",
    "another local name": "http://localhost:8000",
    "null": "null",
    "userinfo trick": "http://127.0.0.1:8000@evil.example",
    "with a path": "http://127.0.0.1:8000/page",
    "other scheme": "chrome-extension://127.0.0.1:8000",
    "no scheme": "127.0.0.1:8000",
    "empty": "",
}


@pytest.mark.parametrize("origin", BAD_ORIGINS.values(), ids=BAD_ORIGINS.keys())
def test_a_request_with_another_origin_gets_403(client, monkeypatch, origin):  # noqa: F811
    forbid_parsing(monkeypatch)
    headers = {"Origin": origin}
    replies = [
        csv_upload(client, headers=headers),
        send(client, "/extract", "a.csv", b"a,b\n1,2\n", data={"schema": "x"}, headers=headers),
        client.get("/health", headers=headers),
        client.get("/engines", headers=headers),
        client.get("/docs", headers=headers),
    ]
    assert [r.status_code for r in replies] == [403] * 5
    assert replies[0].json()["detail"].startswith("Refused: the Origin header does not match")


@pytest.mark.parametrize(
    "host, origin",
    [
        ("127.0.0.1:8000", "http://127.0.0.1:8000"),
        ("localhost:8000", "http://localhost:8000"),
        ("LocalHost:8000", "HTTP://LOCALHOST:8000"),
        ("[::1]:8000", "http://[::1]:8000"),
        ("localhost", "http://localhost"),
        ("localhost", "http://localhost:80"),
        ("localhost:80", "http://localhost"),
        ("localhost", "https://localhost"),
    ],
)
def test_the_same_origin_is_accepted(client, host, origin):  # noqa: F811
    assert csv_upload(client, headers={"Host": host, "Origin": origin}).status_code == 200


def test_the_docs_page_and_its_upload_form_work(client, invoice_pdf, monkeypatch):  # noqa: F811
    """ "Try it out" on /docs is a same-origin fetch, so the browser sends these headers."""
    script_model(monkeypatch, FakeLLM(GOOD_INVOICE))
    for base in (BASE_URL, "http://localhost:8000"):
        host = base.removeprefix("http://")
        browser = {
            "Host": host,
            "Origin": base,
            "Referer": f"{base}/docs",
            "Sec-Fetch-Site": "same-origin",
        }
        page = client.get("/docs", headers={"Host": host})  # opening a page sends no Origin
        assert page.status_code == 200 and "swagger" in page.text
        paths = client.get("/openapi.json", headers=browser).json()["paths"]
        assert {"/parse", "/extract"} <= set(paths)
        assert csv_upload(client, headers=browser).status_code == 200
        reply = send(
            client,
            "/extract",
            "invoice.pdf",
            invoice_pdf,
            data={"schema": "invoice"},
            headers=browser,
        )
        assert reply.status_code == 200, reply.text


def test_a_cross_site_upload_is_not_read(config):
    app = create_app(config)
    headers = [(b"origin", b"http://evil.example")]
    status, body, pulled = drive(app, endless_upload(1024, 5), headers)
    assert (status, pulled) == (403, 0) and "another web site" in body["detail"]


def test_two_origin_headers_are_refused(config):
    headers = [(b"origin", b"http://127.0.0.1:8000"), (b"origin", b"http://evil.example")]
    status, _, pulled = drive(create_app(config), endless_upload(1024, 5), headers)
    assert (status, pulled) == (403, 0)


# ---------------------------------------------------------------------------- Host


@pytest.mark.parametrize(
    "host",
    [
        "localhost",
        "localhost:8000",
        "LOCALHOST:9999",
        "127.0.0.1",
        "127.0.0.1:8000",
        "[::1]",
        "[::1]:8000",
    ],
)
def test_local_host_names_are_accepted(client, host):  # noqa: F811
    assert client.get("/health", headers={"Host": host}).status_code == 200


@pytest.mark.parametrize(
    "host",
    [
        "testserver",
        "evil.example",
        "evil.example:8000",
        "127.0.0.1.evil.example",
        "localhost.evil.example:8000",
        "localhost:8000.evil.example",
        "evil.example@127.0.0.1",
        "127.0.0.1@evil.example",
        "localhost.",
        "localhost:",
        "::1",
        "0.0.0.0:8000",
        "192.168.1.20:8000",
        "",
    ],
)
def test_other_host_values_get_403(client, host):  # noqa: F811
    reply = client.get("/health", headers={"Host": host})
    assert reply.status_code == 403
    assert reply.json()["detail"].startswith("Refused: the server does not accept this Host")


def test_a_name_pointed_at_this_computer_is_refused(client, monkeypatch):  # noqa: F811
    """DNS rebinding: Origin and Host agree, but the name is not a local one."""
    forbid_parsing(monkeypatch)
    headers = {"Host": "evil.example:8000", "Origin": "http://evil.example:8000"}
    assert csv_upload(client, headers=headers).status_code == 403


@pytest.mark.parametrize("hosts", [(), (LOCAL_HOST, (b"host", b"evil.example"))], ids=["0", "2"])
def test_a_missing_or_doubled_host_header_is_refused(config, hosts):
    status, _, pulled = drive(create_app(config), endless_upload(1024, 5), host=hosts)
    assert (status, pulled) == (403, 0)


def statuses_for(app, *hosts):
    client = local_client(app)  # noqa: F811
    return [client.get("/health", headers={"Host": host}).status_code for host in hosts]


def test_the_allowed_hosts_setting_adds_names(config):
    config.api.allowed_hosts = ["MyBox.lan", "[fd00::1]"]
    for local_only in (True, False):  # the list is enforced however the server was started
        app = create_app(config, local_only=local_only)
        hosts = ["mybox.lan:8000", "[fd00::1]:8000", "localhost:8000", "127.0.0.1"]
        assert statuses_for(app, *hosts, "other.lan", "[fd00::2]") == [200] * 4 + [403] * 2


def test_a_non_local_server_without_a_list_accepts_any_host_but_checks_the_origin(config):
    app = create_app(config, local_only=False)
    assert statuses_for(app, "mybox.lan:8000", "192.168.1.20:8000") == [200, 200]
    client = local_client(app)  # noqa: F811
    for origin, status in [("http://mybox.lan:8000", 200), ("http://evil.example", 403)]:
        headers = {"Host": "mybox.lan:8000", "Origin": origin}
        assert client.get("/health", headers=headers).status_code == status


@pytest.mark.parametrize(
    "entry", ["mybox:8000", "http://mybox", "*", "*.lan", "", "[::1]:8000", "my box", "a@b"]
)
def test_an_allowed_hosts_entry_that_could_never_match_is_refused(entry):
    with pytest.raises(ValidationError, match="not a host name"):
        ApiConfig(allowed_hosts=[entry])


@pytest.mark.parametrize(
    "flags, listed, other_status, says_any",
    [
        ([], None, 403, False),
        (["--host", "localhost"], None, 403, False),
        (["--host", "0.0.0.0"], None, 200, True),
        (["--host", "0.0.0.0"], ["mybox.lan"], 403, False),
        ([], ["mybox.lan"], 403, False),
    ],
    ids=["default", "localhost", "open", "open-with-list", "list"],
)
def test_the_command_chooses_which_hosts_are_accepted(
    tmp_path, monkeypatch, flags, listed, other_status, says_any
):
    if listed:
        flags = [*flags, "--config", settings_file(tmp_path, allowed_hosts=listed)]
    app, output = start_server(monkeypatch, *flags)
    mybox = 200 if listed or other_status == 200 else 403
    assert statuses_for(app, "localhost", "mybox.lan", "other.lan") == [200, mybox, other_status]
    assert ("Any Host header is now accepted" in output) is says_any
