"""A tiny client for a local or remote LLM. Two protocols:

  ollama  Ollama's own /api/chat. It lets us set the context window (num_ctx), so a long
          prompt is not silently cut, and pass the JSON schema as `format` (structured output).
  openai  Any OpenAI-compatible /chat/completions server (vLLM, LM Studio, OpenAI ...).
          We ask for a JSON schema first and fall back to plain JSON mode if it is refused.

We use httpx directly: one small dependency we already have, nothing hidden.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from doctordoc.config import LLMConfig


class LLMError(Exception):
    """The LLM server could not be reached, or answered with an error."""


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
        self, messages: list[dict[str, str]], json_schema: dict[str, Any] | None = None
    ) -> str:
        """Send the conversation, return the model's text (which should be JSON)."""
        try:
            if self.config.provider == "ollama":
                return self._ollama(messages, json_schema)
            return self._openai(messages, json_schema)
        except httpx.ConnectError as err:
            raise LLMError(
                f"Cannot reach the LLM server at {self.config.base_url}. Is it running? ({err})"
            ) from err
        except httpx.HTTPError as err:
            raise LLMError(f"LLM request failed: {err}") from err

    def close(self) -> None:
        self._http.close()

    # ------------------------------------------------------------------ Ollama

    def _ollama(self, messages: list[dict[str, str]], schema: dict[str, Any] | None) -> str:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": False,
            "format": schema or "json",
            "options": {"temperature": self.config.temperature, "num_ctx": self.config.num_ctx},
        }
        response = self._http.post("/api/chat", json=payload)
        _raise_for_status(response)
        return str(response.json()["message"]["content"])

    # ------------------------------------------------------------------ OpenAI-compatible

    def _openai(self, messages: list[dict[str, str]], schema: dict[str, Any] | None) -> str:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "temperature": self.config.temperature,
        }
        if schema:
            strict = {
                "type": "json_schema",
                "json_schema": {"name": "extraction", "schema": schema},
            }
            response = self._http.post(
                "/chat/completions", json={**payload, "response_format": strict}
            )
            if response.status_code in (400, 422):  # server does not know json_schema
                response = self._http.post(
                    "/chat/completions",
                    json={**payload, "response_format": {"type": "json_object"}},
                )
        else:
            response = self._http.post("/chat/completions", json=payload)
        _raise_for_status(response)
        return str(response.json()["choices"][0]["message"]["content"] or "")


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
