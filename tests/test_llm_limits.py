"""One bad document must not keep the model busy for ever.

The slow model here is a REAL little HTTP server on this machine. A mocked transport would be
useless: it ignores timeouts, so the tests would pass even if the limits did nothing.
"""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from doctordoc.config import Config, LLMConfig
from doctordoc.extract import Invoice, LLMClient, extract
from doctordoc.models import Block, BlockType, Document, Page

NOT_JSON = {"message": {"content": "still thinking"}, "done_reason": "stop"}


class SlowModel:
    """Speaks Ollama's protocol, slowly.

    delay    seconds of silence before the answer
    trickle  if set, never finish: send one byte every `trickle` seconds
    """

    def __init__(self, delay=0.0, trickle=None, answer=NOT_JSON):
        self.delay, self.trickle, self.answer = delay, trickle, answer
        self.requests: list[dict] = []
        self.stop = threading.Event()
        model = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers["Content-Length"])
                model.requests.append(json.loads(self.rfile.read(length)))
                try:
                    model.reply(self)
                except OSError:  # the client hung up, which is the point
                    pass

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.server.block_on_close = False
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def reply(self, handler):
        self.stop.wait(self.delay)
        handler.send_response(200)
        handler.end_headers()
        if self.trickle is None:
            handler.wfile.write(json.dumps(self.answer).encode())
            return
        while not self.stop.wait(self.trickle):
            handler.wfile.write(b" ")
            handler.wfile.flush()

    def config(self, **llm) -> Config:
        port = self.server.server_address[1]
        return Config(llm=LLMConfig(base_url=f"http://127.0.0.1:{port}", **llm))

    def close(self):
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def slow_model():
    models = []

    def start(**kwargs):
        models.append(SlowModel(**kwargs))
        return models[-1]

    yield start
    for model in models:
        model.close()


def document(pages=1, chars=50) -> Document:
    return Document(
        source="bad.pdf",
        pages=[
            Page(number=n, blocks=[Block(type=BlockType.PARAGRAPH, text="junk " * (chars // 5))])
            for n in range(1, pages + 1)
        ],
    )


def timed_extract(doc, config):
    started = time.monotonic()
    result = extract(doc, Invoice, config)
    return result, time.monotonic() - started


def test_a_silent_model_is_stopped_and_not_asked_again(slow_model):
    model = slow_model(delay=30)
    result, seconds = timed_extract(document(), model.config(timeout=0.5))
    assert "did not finish within" in result.error and "llm.timeout" in result.error
    assert seconds < 3 and len(model.requests) == 1


def test_a_model_that_trickles_bytes_cannot_dodge_the_time_limit(slow_model):
    # every gap is far shorter than the timeout, so only a clock-time limit can stop this
    model = slow_model(trickle=0.05)
    result, seconds = timed_extract(document(), model.config(timeout=0.6))
    assert "did not finish within" in result.error
    assert seconds < 3


def test_retries_share_one_time_budget_per_document(slow_model):
    model = slow_model(delay=0.3)  # each answer is in time, but never valid JSON
    config = model.config(timeout=5, max_retries=50, max_seconds_per_document=1.0)
    result, seconds = timed_extract(document(), config)
    assert result.error and seconds < 3
    assert 2 <= len(model.requests) <= 5  # nowhere near 51


def test_a_long_document_shares_the_budget_across_all_its_calls(slow_model):
    answer = {"message": {"content": "{}"}, "done_reason": "stop"}
    model = slow_model(delay=0.3, answer=answer)
    config = model.config(timeout=5, max_chars_per_call=100, max_seconds_per_document=1.0)
    result, seconds = timed_extract(document(pages=30, chars=90), config)
    assert result.error and seconds < 3
    assert len(model.requests) < 30


def test_an_answer_cut_off_at_the_cap_fails_at_once(slow_model):
    cut = {"message": {"content": '{"line_items": [{"descr'}, "done_reason": "length"}
    model = slow_model(answer=cut)
    result, _ = timed_extract(document(), model.config(max_output_tokens=77))
    assert "cut off at 77 tokens" in result.error
    assert len(model.requests) == 1  # a retry would be cut off at the same place
    assert model.requests[0]["options"]["num_predict"] == 77


def test_openai_style_servers_get_the_cap_and_report_a_cut_off_answer():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content))
        choice = {"message": {"content": "{"}, "finish_reason": "length"}
        return httpx.Response(200, json={"choices": [choice]})

    config = LLMConfig(provider="openai", base_url="http://x/v1", max_output_tokens=55)
    client = LLMClient(config, transport=httpx.MockTransport(handler))
    result = extract(document(), Invoice, Config(llm=config), llm=client)
    assert seen[0]["max_tokens"] == 55 and "cut off at 55 tokens" in result.error


def test_default_limits_keep_one_document_far_below_thirty_minutes():
    llm = LLMConfig()
    assert llm.max_output_tokens and llm.max_retries <= 1
    assert llm.timeout <= llm.max_seconds_per_document <= 600
