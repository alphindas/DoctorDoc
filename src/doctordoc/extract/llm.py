"""A tiny client for a local or remote LLM. Two protocols:

  ollama  Ollama's own /api/chat. It lets us set the context window (num_ctx) and pass the
          JSON schema as `format` (structured output). Setting num_ctx does NOT stop silent
          cutting: see the prompt size guard below.
  openai  Any OpenAI-compatible /chat/completions server (vLLM, LM Studio, OpenAI ...).
          We ask for a JSON schema first and fall back to plain JSON mode if it is refused.

We use httpx directly: one small dependency we already have, nothing hidden.

Two limits keep one bad document from running for ever. The answer has a maximum length
(`max_output_tokens`), because a model reading junk text can keep writing for many minutes.
And every call has a time limit measured on the clock: a plain HTTP timeout only limits the
silence between two pieces of an answer, so a server that sends a little now and then would
never reach it.

Prompt size guard (Ollama only). Seen with Ollama 0.33.1 and llama3.2:3b at num_ctx 8192: a
prompt of 8116 tokens was read whole, one of 8237 tokens was cut and reported as 4098 tokens
(about half of num_ctx), with no error. An answer that ran past the window gave no error
either. So before sending, we estimate the prompt's tokens and do not send one that is
over the safe limit; after the reply, we check the token counts Ollama reports.
The estimate comes from characters, not from the model's tokenizer: it can be wrong for
other models and scripts, which is why the reply is checked too.
"""

from __future__ import annotations

import json
import logging
import math
import time
from typing import Any

import httpx

from doctordoc.config import LLMConfig

log = logging.getLogger(__name__)

ANSWER_RESERVE_TOKENS = 1024  # part of num_ctx that the prompt must leave for the answer
SYSTEM_CHARS_PER_TOKEN = 3.5  # instructions and schema: measured 3.9 to 4.2
TEXT_CHARS_PER_TOKEN = 2.0  # document text: measured 2.0 to 2.8 on the sample files
TOKENS_PER_MESSAGE = 8  # the chat template around each message
CUT_MARGIN_TOKENS = 16  # how close to num_ctx / 2 a count must be to look like a cut


def estimate_prompt_tokens(messages: list[dict[str, str]]) -> int:
    """A cautious guess, made from characters. ASCII and two-byte characters (accents, Greek,
    Cyrillic, Arabic) use the ratios above; other characters count as 1 token each (Chinese,
    Japanese, Indian scripts, symbols), and emoji and other 4-byte characters as 2."""
    total = 0.0
    for message in messages:
        system = message["role"] == "system"
        per_token = SYSTEM_CHARS_PER_TOKEN if system else TEXT_CHARS_PER_TOKEN
        for char in message["content"]:
            code = ord(char)
            total += 1 / per_token if code < 0x800 else 1 if code < 0x10000 else 2
        total += TOKENS_PER_MESSAGE
    return math.ceil(total)


def safe_prompt_limit(config: LLMConfig) -> int:
    """The most prompt tokens we send: num_ctx minus the room kept for the answer."""
    return config.num_ctx - min(ANSWER_RESERVE_TOKENS, config.max_output_tokens)


def count_problems(prompt: int | None, answer: int | None, config: LLMConfig) -> list[str]:
    """What the token counts in a reply say may have gone wrong (empty list: nothing seen)."""
    if prompt is None:
        return []
    problems = []
    half = config.num_ctx // 2
    if half <= prompt <= half + CUT_MARGIN_TOKENS:
        problems.append(
            f"The model read {prompt} prompt tokens, about half of num_ctx {config.num_ctx}. "
            "Ollama reports this when it has cut a prompt that did not fit."
        )
    elif prompt >= safe_prompt_limit(config):
        problems.append(
            f"The prompt used {prompt} tokens, at or over the safe limit of "
            f"{safe_prompt_limit(config)} (num_ctx {config.num_ctx} minus room for the answer)."
        )
    if answer is not None and prompt + answer > config.num_ctx:
        problems.append(
            f"Prompt plus answer ({prompt} + {answer} tokens) ran past num_ctx "
            f"{config.num_ctx}. Part of the prompt may have been dropped while answering."
        )
    return problems


class LLMError(Exception):
    """The LLM server could not be reached, or answered with an error."""


class LLMTimeout(LLMError):
    """The model did not finish its answer in the time allowed."""


class LLMPromptTooLong(LLMError):
    """The prompt would probably not fit in the context window, so it was not sent."""


class LLMClient:
    def __init__(self, config: LLMConfig, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
        self.calls: list[dict[str, Any]] = []  # one trace per Ollama call, oldest first
        headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
        self._http = httpx.Client(
            base_url=config.base_url.rstrip("/"),
            headers=headers,
            timeout=config.timeout,
            transport=transport,
        )

    def chat(
        self,
        messages: list[dict[str, str]],
        json_schema: dict[str, Any] | None = None,
        timeout: float | None = None,
    ) -> str:
        """Send the conversation, return the model's text (which should be JSON).

        `timeout` is the most seconds this call may take (default: the configured timeout).
        """
        limit = self.config.timeout if timeout is None else timeout
        deadline = time.monotonic() + limit
        try:
            if self.config.provider == "ollama":
                return self._ollama(messages, json_schema, deadline)
            return self._openai(messages, json_schema, deadline)
        except (httpx.TimeoutException, _OutOfTime) as err:
            raise LLMTimeout(
                f"The model did not finish within {limit:.0f} s and the call was stopped. "
                "On a slow machine or a very long document, raise llm.timeout."
            ) from err
        except httpx.ConnectError as err:
            raise LLMError(
                f"Cannot reach the LLM server at {self.config.base_url}. Is it running? ({err})"
            ) from err
        except httpx.HTTPError as err:
            raise LLMError(f"LLM request failed: {err}") from err

    def close(self) -> None:
        self._http.close()

    def _post(self, path: str, payload: dict[str, Any], deadline: float) -> httpx.Response:
        """POST, and give up at the deadline even if the server keeps sending something."""
        left = deadline - time.monotonic()
        if left <= 0:
            raise _OutOfTime
        with self._http.stream("POST", path, json=payload, timeout=left) as response:
            body = bytearray()
            for piece in response.iter_bytes():
                body += piece
                if time.monotonic() > deadline:
                    raise _OutOfTime
        return httpx.Response(response.status_code, content=bytes(body))

    def _check_not_cut_off(self, reason: object) -> None:
        if reason == "length":  # asking again would only be cut off at the same place
            raise LLMError(
                f"The model's answer was cut off at {self.config.max_output_tokens} tokens. "
                "Either it was writing nonsense (check the document text), or the document "
                "has a very long list: raise llm.max_output_tokens."
            )

    # ------------------------------------------------------------------ prompt size guard

    def _check_prompt_size(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        """Before sending: start this call's trace, and refuse a prompt that would not fit."""
        estimate, limit = estimate_prompt_tokens(messages), safe_prompt_limit(self.config)
        trace: dict[str, Any] = {
            "estimated_prompt_tokens": estimate,
            "safe_limit": limit,
            "sent": False,
            "prompt_eval_count": None,
            "eval_count": None,
            "problems": [],
        }
        self.calls.append(trace)
        if estimate > limit:
            problem = (
                f"Not sent: the prompt is about {estimate} tokens (estimate), over the safe "
                f"limit of {limit} (num_ctx {self.config.num_ctx} minus room for the answer). "
                "Ollama can silently cut a prompt that does not fit. Lower "
                "llm.max_chars_per_call or raise llm.num_ctx."
            )
            trace["problems"].append(problem)
            log.warning(problem)
            raise LLMPromptTooLong(problem)
        return trace

    def _check_token_counts(self, trace: dict[str, Any], body: dict[str, Any]) -> None:
        """After the reply: keep Ollama's token counts and warn if they look wrong."""
        trace["prompt_eval_count"] = body.get("prompt_eval_count")
        trace["eval_count"] = body.get("eval_count")
        log.info(
            "LLM call: estimated %s prompt tokens (safe limit %s), prompt_eval_count=%s, "
            "eval_count=%s",
            trace["estimated_prompt_tokens"],
            trace["safe_limit"],
            trace["prompt_eval_count"],
            trace["eval_count"],
        )
        for problem in count_problems(trace["prompt_eval_count"], trace["eval_count"], self.config):
            trace["problems"].append(problem)
            log.warning(problem)

    # ------------------------------------------------------------------ Ollama

    def _ollama(
        self, messages: list[dict[str, str]], schema: dict[str, Any] | None, deadline: float
    ) -> str:
        trace = self._check_prompt_size(messages)
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
            "format": schema or "json",
            "options": {
                "temperature": self.config.temperature,
                "num_ctx": self.config.num_ctx,
                "num_predict": self.config.max_output_tokens,
            },
        }
        trace["sent"] = True
        response = self._post("/api/chat", payload, deadline)
        _raise_for_status(response)
        body = response.json()
        self._check_token_counts(trace, body)
        self._check_not_cut_off(body.get("done_reason"))
        return str(body["message"]["content"])

    # ------------------------------------------------------------------ OpenAI-compatible

    def _openai(
        self, messages: list[dict[str, str]], schema: dict[str, Any] | None, deadline: float
    ) -> str:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
            "max_tokens": self.config.max_output_tokens,
        }
        if schema:
            strict = {
                "type": "json_schema",
                "json_schema": {"name": "extraction", "schema": schema},
            }
            response = self._post(
                "/chat/completions", {**payload, "response_format": strict}, deadline
            )
            if response.status_code in (400, 422):  # server does not know json_schema
                response = self._post(
                    "/chat/completions",
                    {**payload, "response_format": {"type": "json_object"}},
                    deadline,
                )
        else:
            response = self._post("/chat/completions", payload, deadline)
        _raise_for_status(response)
        choice = response.json()["choices"][0]
        self._check_not_cut_off(choice.get("finish_reason"))
        return str(choice["message"]["content"] or "")


class _OutOfTime(Exception):
    pass


def _raise_for_status(response: httpx.Response) -> None:
    if response.status_code >= 400:
        detail = response.text[:300]
        try:
            detail = json.loads(response.text).get("error", detail)
        except ValueError:
            pass
        raise LLMError(f"LLM server answered {response.status_code}: {detail}")


def list_models(config: LLMConfig, timeout: float = 3.0) -> list[str]:
    """Names of the models on the server in llm.base_url (Ollama: /api/tags, OpenAI-style: /models).

    Raises LLMError if the server cannot be reached. Used by the UI model dropdown.
    """
    headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
    path = "/api/tags" if config.provider == "ollama" else "/models"
    try:
        reply = httpx.get(
            config.base_url.rstrip("/") + path, headers=headers, timeout=timeout
        ).raise_for_status()
        body = reply.json()
    except (httpx.HTTPError, ValueError) as err:
        raise LLMError(f"Cannot list models at {config.base_url}: {err}") from err
    items = body.get("models", []) if config.provider == "ollama" else body.get("data", [])
    key = "name" if config.provider == "ollama" else "id"
    return sorted(str(item[key]) for item in items if isinstance(item, dict) and key in item)
