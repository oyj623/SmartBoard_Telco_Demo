"""
OpenAI-compatible provider.

Defaults to DeepSeek. The same class serves OpenAI, Together, Groq, OpenRouter,
Moonshot and a local vLLM — only base_url, model and key change.

    export DEEPSEEK_API_KEY=sk-...
    export SMARTBOARD_MODEL=deepseek-v4-flash

Flash is the right default here: this is a high-frequency tool loop with short
outputs, which is exactly what it is tuned for. Switch to deepseek-v4-pro if you
find the model choosing weak chart types on complex asks.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

import httpx

from .base import AssistantTurn, ToolCall


class OpenAICompatBrain:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.deepseek.com/v1",
        model: str = "deepseek-v4-flash",
        temperature: float = 0.2,
        max_tokens: int = 1600,
        timeout: float = 90.0,
        extra_body: Optional[Dict[str, Any]] = None,
    ):
        self.api_key = api_key or os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("OPENAI_API_KEY", "")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.timeout = timeout
        self.extra_body = extra_body or {}
        self.name = f"{model}@{self.base_url}"

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def complete(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        system: str,
    ) -> AssistantTurn:
        payload = {
            "model": self.model,
            "messages": [{"role": "system", "content": system}, *messages],
            "tools": tools,
            "tool_choice": "auto",
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            **self.extra_body,
        }

        with httpx.Client(timeout=self.timeout) as client:
            resp = client.post(
                f"{self.base_url}/chat/completions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json=payload,
            )

        if resp.status_code >= 400:
            raise BrainError(f"{self.model} returned {resp.status_code}: {resp.text[:400]}")

        data = resp.json()
        choice = data["choices"][0]["message"]

        calls: List[ToolCall] = []
        for tc in choice.get("tool_calls") or []:
            fn = tc.get("function", {})
            calls.append(
                ToolCall(
                    id=tc.get("id", f"call_{len(calls)}"),
                    name=fn.get("name", ""),
                    arguments=_loads(fn.get("arguments")),
                )
            )

        return AssistantTurn(
            text=choice.get("content") or "",
            tool_calls=calls,
            reasoning=choice.get("reasoning_content"),
            raw=choice,
            usage=data.get("usage") or {},
        )


class BrainError(RuntimeError):
    pass


def _loads(raw: Any) -> Dict[str, Any]:
    """Tool arguments arrive as a JSON string, and occasionally as a malformed one."""
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        trimmed = str(raw).strip()
        # A truncated response often just needs its braces closed.
        for suffix in ("}", "]}", '"}]}',):
            try:
                return json.loads(trimmed + suffix)
            except json.JSONDecodeError:
                continue
        raise BrainError(f"tool arguments were not valid JSON: {trimmed[:200]}")
