import json
from datetime import date

import httpx
import pytest
from pydantic import BaseModel, Field

import samples
from doctordoc import parse
from doctordoc.config import Config, LLMConfig
from doctordoc.extract import (
    DailyDrillingReport,
    Invoice,
    LLMClient,
    extract,
    resolve_schema,
    rule,
)
from doctordoc.models import Block, BlockType, Document, Page

GOOD_INVOICE = {
    "invoice_number": "INV-2024-0042",
    "invoice_date": "2024-03-15",
    "due_date": "2024-04-14",
    "vendor_name": "ACME Drilling Supplies Ltd",
    "customer_name": "Northern Star Energy",
    "currency": "USD",
    "subtotal": 4200.0,
    "tax_amount": 420.0,
    "total": 4620.0,
    "line_items": [
        {
            "description": "Drill bit 8.5 inch",
            "quantity": 2,
            "unit_price": 1500.0,
            "amount": 3000.0,
        },
        {"description": "Mud additive", "quantity": 10, "unit_price": 120.0, "amount": 1200.0},
    ],
}


class FakeLLM:
    """Scripted answers for the Ollama protocol. Records every request it receives."""

    def __init__(self, *replies):
        self.replies = list(replies)
        self.requests: list[dict] = []

    def client(self, **overrides) -> LLMClient:
        def handler(request: httpx.Request) -> httpx.Response:
            self.requests.append(json.loads(request.content))
            reply = self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]
            content = reply if isinstance(reply, str) else json.dumps(reply)
            return httpx.Response(200, json={"message": {"content": content}})

        cfg = LLMConfig(**overrides)
        return LLMClient(cfg, transport=httpx.MockTransport(handler))


@pytest.fixture
def invoice_doc(config) -> Document:
    return parse(samples.digital_pdf(), config, filename="inv.pdf")


def run(doc, llm, schema=Invoice, config=None, **kw):
    return extract(doc, schema, config or Config(), llm=llm.client(), **kw)


def test_happy_path_every_value_is_grounded_with_a_source(invoice_doc):
    llm = FakeLLM(GOOD_INVOICE)
    result = run(invoice_doc, llm)
    assert result.error is None and result.attempts == 1
    assert result.needs_review == [] and result.issues == []
    total = result.field("total")
    assert total.grounded and total.confidence > 0.9
    page_text = invoice_doc.pages[0].text
    assert page_text[total.source.start : total.source.end] == "4620.00"
    assert total.source.page == 1
    assert result.field("invoice_date").value == date(2024, 3, 15)
    assert result.field("line_items[1].amount").grounded
    assert result.data["vendor_name"] == "ACME Drilling Supplies Ltd"


def test_the_request_carries_the_schema_and_document_text(invoice_doc):
    llm = FakeLLM(GOOD_INVOICE)
    run(invoice_doc, llm)
    request = llm.requests[0]
    assert request["format"]["properties"]["invoice_number"]  # structured output schema
    assert request["options"]["num_ctx"] == 8192 and request["stream"] is False
    assert "INV-2024-0042" in request["messages"][1]["content"]


def test_hallucinated_value_is_ungrounded_and_goes_to_review(invoice_doc):
    wrong = {**GOOD_INVOICE, "total": 9999.0, "customer_name": "Invented Corp"}
    result = run(invoice_doc, FakeLLM(wrong))
    total, customer = result.field("total"), result.field("customer_name")
    assert not total.grounded and total.confidence <= 0.4 and total.source is None
    assert not customer.grounded
    assert {"total", "customer_name"} <= set(result.needs_review)
    assert any(i.rule == "sum_of_fields" for i in result.issues)  # 4200 + 420 != 9999


def test_cross_field_checks_flag_both_sides(invoice_doc):
    bad = {**GOOD_INVOICE, "due_date": "2024-03-01"}
    bad["line_items"] = [
        {**GOOD_INVOICE["line_items"][0]},
        {**GOOD_INVOICE["line_items"][1], "amount": 1300.0},
    ]
    result = run(invoice_doc, FakeLLM(bad))
    rules = {i.rule for i in result.issues}
    assert {"date_order", "sum_equals", "row_product"} <= rules
    assert "due_date" in result.needs_review and "subtotal" in result.needs_review
    assert "line_items[1].amount" in result.needs_review


def test_invalid_json_is_retried_with_the_error_in_the_prompt(invoice_doc):
    llm = FakeLLM("this is not json {", GOOD_INVOICE)
    result = run(invoice_doc, llm)
    assert result.attempts == 2 and result.error is None and result.data["total"] == 4620.0
    retry = llm.requests[1]["messages"]
    assert retry[-2]["content"] == "this is not json {" and "not valid" in retry[-1]["content"]


def test_json_in_code_fence_is_accepted(invoice_doc):
    result = run(invoice_doc, FakeLLM("```json\n" + json.dumps(GOOD_INVOICE) + "\n```"))
    assert result.attempts == 1 and result.data["total"] == 4620.0


def test_invalid_values_are_dropped_after_retries_and_reported(invoice_doc):
    bad = {**GOOD_INVOICE, "total": "lots", "invoice_date": "yesterday"}
    result = run(invoice_doc, FakeLLM(bad))
    assert result.attempts == 3  # 1 try + 2 retries
    assert result.data["total"] is None and result.data["invoice_date"] is None
    assert result.data["invoice_number"] == "INV-2024-0042"  # the valid part is kept
    assert {i.path for i in result.issues if i.rule == "type"} == {"total", "invoice_date"}
    assert "total" in result.needs_review  # required() also complains about the missing value


def test_never_valid_json_gives_an_error_not_an_exception(invoice_doc):
    result = run(invoice_doc, FakeLLM("nope"))
    assert result.error and "usable JSON" in result.error


def test_llm_server_down_gives_an_error_not_an_exception(invoice_doc):
    def refuse(request):
        raise httpx.ConnectError("refused")

    client = LLMClient(LLMConfig(), transport=httpx.MockTransport(refuse))
    result = extract(invoice_doc, "invoice", Config(), llm=client)
    assert result.error and "Cannot reach the LLM server" in result.error


def test_low_ocr_confidence_lowers_field_confidence(config):
    def doc_with(conf):
        return Document(
            source="x",
            pages=[
                Page(
                    number=1,
                    blocks=[
                        Block(
                            type=BlockType.PARAGRAPH,
                            text="Invoice number: INV-2024-0042",
                            confidence=conf,
                        ),
                        Block(type=BlockType.PARAGRAPH, text="Total: 4620.00", confidence=conf),
                    ],
                )
            ],
        )

    reply = {"invoice_number": "INV-2024-0042", "total": 4620.0}
    clean = extract(doc_with(0.99), Invoice, Config(), llm=FakeLLM(reply).client())
    blurry = extract(doc_with(0.40), Invoice, Config(), llm=FakeLLM(reply).client())
    assert blurry.field("total").ocr_confidence == 0.4
    assert blurry.field("total").confidence < clean.field("total").confidence
    assert "total" in blurry.needs_review and "total" not in clean.needs_review


def test_custom_rule_and_custom_schema():
    class Pump(BaseModel):
        tag: str | None = Field(None, description="Equipment tag")
        flow: float | None = Field(None, description="Flow in m3/h")

    @rule("flow_must_be_positive")
    def positive(data):
        if data["flow"] is not None and data["flow"] <= 0:
            return ("flow", "flow must be positive")

    result = extract(
        "Pump P-101 flow -5 m3/h",
        Pump,
        Config(),
        rules=[positive],
        llm=FakeLLM({"tag": "P-101", "flow": -5.0}).client(),
    )
    assert result.schema_name == "Pump"
    assert [i.rule for i in result.issues] == ["flow_must_be_positive"]
    assert "flow" in result.needs_review and "tag" not in result.needs_review


def test_long_documents_are_split_by_page_and_merged():
    pages = [
        Page(number=n, blocks=[Block(type=BlockType.PARAGRAPH, text=t)])
        for n, t in [
            (1, "Invoice number: INV-7 " + "filler " * 40),
            (2, "Total: 55.00 " + "filler " * 40),
        ]
    ]
    llm = FakeLLM({"invoice_number": "INV-7"}, {"total": 55.0})
    cfg = Config()
    cfg.llm.max_chars_per_call = 300
    result = extract(Document(source="x", pages=pages), Invoice, cfg, llm=llm.client())
    assert result.attempts == 2 and len(llm.requests) == 2
    assert result.data["invoice_number"] == "INV-7" and result.data["total"] == 55.0
    assert result.field("total").source.page == 2


def test_text_input_and_empty_document():
    assert (
        extract("Total: 12.00", Invoice, Config(), llm=FakeLLM({"total": 12.0}).client())
        .field("total")
        .grounded
    )
    assert extract("   ", Invoice, Config(), llm=FakeLLM({}).client()).error


def test_extract_from_a_scanned_pdf_end_to_end_with_fake_llm(config):
    # the real OCR runs on a noisy scan; only the LLM is scripted
    result = extract(samples.scanned_pdf(), "invoice", config, llm=FakeLLM(GOOD_INVOICE).client())
    total = result.field("total")
    assert total.grounded and 0.9 < total.ocr_confidence <= 1.0
    assert total.source.text == "4620.00"


def test_resolve_schema():
    assert resolve_schema("drilling") is DailyDrillingReport
    assert resolve_schema("doctordoc.extract:Invoice") is Invoice
    with pytest.raises(ValueError, match="Unknown schema"):
        resolve_schema("nope")


def test_openai_protocol_falls_back_to_json_mode_when_schema_is_refused():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body["response_format"]["type"])
        if body["response_format"]["type"] == "json_schema":
            return httpx.Response(400, json={"error": "response_format not supported"})
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"total": 12.0}'}}]})

    client = LLMClient(
        LLMConfig(provider="openai", base_url="http://x/v1", api_key="k"),
        transport=httpx.MockTransport(handler),
    )
    result = extract("Total: 12.00", Invoice, Config(), llm=client)
    assert seen == ["json_schema", "json_object"] and result.data["total"] == 12.0
