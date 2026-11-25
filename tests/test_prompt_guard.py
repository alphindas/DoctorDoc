"""A prompt that does not fit the context window must never be cut in silence."""

import hashlib
import json
import logging
from pathlib import Path

import httpx
import pytest

from doctordoc import parse
from doctordoc.config import Config, LLMConfig
from doctordoc.engines.docling_engine import DoclingEngine
from doctordoc.extract import BUILTIN_SCHEMAS, Invoice, LLMClient, extract
from doctordoc.extract.extractor import _split_into_chunks
from doctordoc.extract.grounding import PageText
from doctordoc.extract.llm import (
    LLMPromptTooLong,
    count_problems,
    estimate_prompt_tokens,
    safe_prompt_limit,
)
from doctordoc.models import Block, BlockType, Document, Page

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "invoice_digital.pdf"
ANSWER = {"invoice_number": "INV-1", "total": 55.0}


class Ollama:
    """Scripted replies, with or without token counts. Records every request."""

    def __init__(self, *answers, **counts):
        self.answers = list(answers) or [ANSWER]
        self.counts = counts
        self.requests: list[dict] = []

    def client(self, **llm) -> LLMClient:
        def handler(request):
            self.requests.append(json.loads(request.content))
            answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
            content = answer if isinstance(answer, str) else json.dumps(answer)
            body = {"message": {"content": content}, "done_reason": "stop", **self.counts}
            return httpx.Response(200, json=body)

        return LLMClient(LLMConfig(**llm), transport=httpx.MockTransport(handler))


def run(model, text="Invoice INV-1\n\nTotal: 55.00", **llm):
    client = model.client(**llm)
    return extract(text, Invoice, Config(llm=client.config), llm=client)


def unmarked_lines(chunks):
    return [
        line for c in chunks for line in c.split("\n") if line and not line.startswith("[Page ")
    ]


# ------------------------------------------------------------------ splitting


def test_a_long_block_with_no_blank_lines_is_split_at_line_breaks():
    rows = [f"{i} | bolt M12 | 5 | 4.50 | 22.50" for i in range(2000)]  # one big table block
    chunks = _split_into_chunks([PageText.from_text("\n".join(rows))], 12000)
    assert len(chunks) > 4 and all(len(c) <= 12000 for c in chunks)
    assert unmarked_lines(chunks) == rows  # every row whole, once, in order


def test_one_endless_line_is_split_by_characters():
    text = "".join(str(i % 10) for i in range(50_000))
    chunks = _split_into_chunks([PageText.from_text(text)], 12000)
    assert all(len(c) <= 12000 for c in chunks)
    assert "".join(unmarked_lines(chunks)) == text


def test_a_long_block_between_ordinary_ones_keeps_their_order():
    text = "\n\n".join(["before", "\n".join(["row"] * 100), "after"])
    chunks = _split_into_chunks([PageText.from_text(text)], 100)
    assert all(len(c) <= 100 for c in chunks)
    assert unmarked_lines(chunks) == ["before", *["row"] * 100, "after"]


def test_a_block_that_is_not_over_the_limit_is_left_exactly_as_before():
    text = "x\n" * 6000  # 12000 characters, no blank line
    assert _split_into_chunks([PageText.from_text(text)], 12000) == [f"[Page 1]\n{text}"]


# ------------------------------------------------------------------ before sending


def test_a_prompt_at_the_limit_is_sent_and_one_over_it_is_not():
    model = Ollama()
    client = model.client()
    limit = safe_prompt_limit(client.config)
    assert limit == 8192 - 1024
    at_limit = [{"role": "user", "content": "a" * (2 * (limit - 8))}]
    over = [{"role": "user", "content": at_limit[0]["content"] + "a"}]
    assert estimate_prompt_tokens(at_limit) == limit
    assert estimate_prompt_tokens(over) == limit + 1
    client.chat(at_limit)
    assert len(model.requests) == 1
    with pytest.raises(LLMPromptTooLong, match="Not sent"):
        client.chat(over)
    assert len(model.requests) == 1 and client.calls[-1]["sent"] is False


def test_other_scripts_are_counted_more_heavily_than_ascii():
    def tokens(text):
        return estimate_prompt_tokens([{"role": "user", "content": text}]) - 8

    assert tokens("a" * 100) == tokens("д" * 100) == 50
    assert tokens("井" * 100) == 100 and tokens("😀" * 100) == 200


@pytest.mark.parametrize("schema", sorted(BUILTIN_SCHEMAS))
def test_a_full_default_chunk_of_english_text_is_still_sent(schema):
    model = Ollama({})
    client = model.client()
    text = ("Total 4,620.00 USD " * 700)[: client.config.max_chars_per_call - 20]
    extract(text, schema, Config(llm=client.config), llm=client)
    assert model.requests and client.calls[0]["sent"]


def test_a_small_context_window_gives_a_clear_error_and_sends_nothing():
    model = Ollama()
    result = run(model, num_ctx=1024)
    assert "Not sent" in result.error and "llm.num_ctx" in result.error
    assert model.requests == [] and result.fields == []
    assert any(w.startswith("Prompt size: Not sent") for w in result.warnings)


def test_the_openai_path_is_not_guarded_because_its_window_is_unknown():
    def handler(request):
        return httpx.Response(200, json={"choices": [{"message": {"content": "{}"}}]})

    config = LLMConfig(provider="openai", base_url="http://x/v1", num_ctx=1024)
    client = LLMClient(config, transport=httpx.MockTransport(handler))
    assert extract("Total: 1", Invoice, Config(llm=config), llm=client).error is None


def test_a_retry_that_would_not_fit_keeps_the_valid_part_of_the_first_answer():
    first = {"invoice_number": "INV-1", "total": "not a number", "padding": "x" * 20_000}
    model = Ollama(first)
    result = run(model)
    assert len(model.requests) == 1 and result.error is None and result.attempts == 1
    assert result.data["invoice_number"] == "INV-1" and "total" in result.needs_review
    assert "document" not in result.needs_review  # the prompt that was sent did fit
    assert any("Not sent" in w for w in result.warnings)


def test_a_retry_that_would_not_fit_after_unusable_text_is_an_error():
    model = Ollama("not json " * 3000)
    result = run(model)
    assert "never returned usable JSON" in result.error and len(model.requests) == 1


# ------------------------------------------------------------------ after the reply


@pytest.mark.parametrize(
    "prompt, answer, words",
    [
        (7500, 100, "at or over the safe limit"),
        (7168, 100, "at or over the safe limit"),
        (4098, 200, "about half of num_ctx"),  # what Ollama reported for a cut prompt
        (6500, 1800, "ran past num_ctx"),
    ],
)
def test_suspicious_token_counts_warn_and_send_every_field_to_review(caplog, prompt, answer, words):
    model = Ollama(prompt_eval_count=prompt, eval_count=answer)
    with caplog.at_level(logging.WARNING, logger="doctordoc.extract.llm"):
        result = run(model)
    assert words in caplog.text
    assert result.error is None and result.data["total"] == 55.0  # fields are kept
    assert "document" in result.needs_review
    assert {f.path for f in result.fields} <= set(result.needs_review)
    assert all(f.needs_review for f in result.fields)
    [issue] = [i for i in result.issues if i.rule == "prompt_length"]
    assert words in issue.message and any(words in w for w in result.warnings)
    report = result.to_report()
    assert "document" in report["needs_review"]
    assert result.llm_calls[0]["prompt_eval_count"] == prompt
    assert result.llm_calls[0]["eval_count"] == answer


@pytest.mark.parametrize("counts", [{"prompt_eval_count": 814, "eval_count": 217}, {}])
def test_normal_or_missing_counts_change_nothing(caplog, counts):
    result = run(Ollama(**counts))
    assert "document" not in result.needs_review and result.warnings == []
    assert not [i for i in result.issues if i.rule == "prompt_length"]
    assert result.field("total").needs_review is False
    assert result.llm_calls[0]["prompt_eval_count"] == counts.get("prompt_eval_count")
    assert result.llm_calls[0]["problems"] == []


def test_a_shared_client_does_not_pass_one_documents_warning_to_the_next():
    model = Ollama(prompt_eval_count=7500)
    client = model.client()
    config = Config(llm=client.config)
    assert "document" in extract("Total: 55.00", Invoice, config, llm=client).needs_review
    model.counts = {"prompt_eval_count": 800}
    second = extract("Total: 55.00", Invoice, config, llm=client)
    assert "document" not in second.needs_review and len(second.llm_calls) == 1


def test_count_checks_follow_the_configured_window():
    small = LLMConfig(num_ctx=4096)
    assert count_problems(2050, 10, small) and not count_problems(1500, 10, small)


# ------------------------------------------------------------------ the prompt itself
# Both hashes were recorded with the code BEFORE the guard was added (commit a630bfe).


def prompt_hash(model):
    text = "\x00".join(m["content"] for r in model.requests for m in r["messages"])
    return hashlib.sha256(text.encode()).hexdigest()


def test_the_prompt_for_ordinary_pages_is_unchanged():
    pages = [
        Page(
            number=n,
            blocks=[
                Block(type=BlockType.PARAGRAPH, text=f"Page {n} line one"),
                Block(type=BlockType.PARAGRAPH, text="Total: 4,620.00 USD " * 300),
            ],
        )
        for n in (1, 2, 3)
    ]
    model = Ollama({})
    client = model.client()
    extract(Document(source="fixed", pages=pages), "invoice", Config(llm=client.config), llm=client)
    assert len(model.requests) == 3
    assert prompt_hash(model) == "a8c3f4e96f59b41f7a0980eba5430ca85fbe5918b934727c2c2dc6f8dbe46ee7"


@pytest.mark.integration
def test_the_prompt_for_the_example_invoice_is_unchanged():
    if reason := DoclingEngine(Config(gpu="off")).missing_reason():
        pytest.skip(reason)
    document = parse(EXAMPLE, Config(gpu="off"))
    model = Ollama({})
    client = model.client()
    extract(document, "invoice", Config(llm=client.config), llm=client)
    assert len(model.requests) == 1
    assert prompt_hash(model) == "72ff358b81f477c79f000af066f80badcada88391857c5847196f0731ab018c8"
