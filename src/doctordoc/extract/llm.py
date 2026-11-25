"""A tiny client for a local or remote LLM. Two protocols:

  ollama  Ollama's own /api/chat. It lets us set the context window (num_ctx), so a long
          prompt is not silently cut, and pass the JSON schema as `format` (structured output).
  openai  Any OpenAI-compatible /chat/completions server (vLLM, LM Studio, OpenAI ...).
          We ask for a JSON schema first and fall back to plain JSON mode if it is refused.

We use httpx directly: one small dependency we already have, nothing hidden.

Two limits keep one bad document from running for ever. The answer has a maximum length
(`max_output_tokens`), because a model reading junk text can keep writing for many minutes.
And every call has a time limit measured on the clock: a plain HTTP timeout only limits the
silence between two pieces of an answer, so a server that sends a little now and then would
never reach it.
"""

from __future__ import annotations

import json
import time
from typing import Any

import httpx

from doctordoc.config import LLMConfig


class LLMError(Exception):
    """The LLM server could not be reached, or answered with an error."""


class LLMTimeout(LLMError):
    """The model did not finish its answer in the time allowed."""


class LLMClient:
    def __init__(self, config: LLMConfig, transport: httpx.BaseTransport | None = None) -> None:
        self.config = config
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

    # ------------------------------------------------------------------ Ollama

    def _ollama(
        self, messages: list[dict[str, str]], schema: dict[str, Any] | None, deadline: float
    ) -> str:
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
        response = self._post("/api/chat", payload, deadline)
        _raise_for_status(response)
        body = response.json()
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
    """Names of the models the local server has (Ollama: /api/tags, OpenAI-style: /models).

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
