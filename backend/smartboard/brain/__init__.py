"""
Brains — anything that can turn messages + tool schemas into an assistant turn.

Two ship with SmartBoard. `OpenAICompatBrain` speaks the OpenAI chat-completions
dialect, which covers DeepSeek, OpenAI, Together, Groq, OpenRouter and a local
vLLM. `HeuristicBrain` is a deterministic keyword matcher over the manifest's own
labels — no key, no network — kept for demos, tests, and proving that everything
below the model works.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

from .base import AssistantTurn, BrainClient, ToolCall, build_system_prompt
from .heuristic import HeuristicBrain
from .openai_compat import BrainError, OpenAICompatBrain

__all__ = [
    "AssistantTurn",
    "BrainClient",
    "BrainError",
    "HeuristicBrain",
    "OpenAICompatBrain",
    "ToolCall",
    "brain_from_env",
    "build_system_prompt",
]


def _env(name: str, default: str = "") -> str:
    """
    Read an environment variable, treating blank as absent.

    `os.getenv(name, default)` returns "" for a variable that is present but
    empty — exactly what a blanked-out .env line produces. An empty base URL
    then reaches httpx as "/chat/completions" and fails with an error that
    points at the HTTP client rather than at the .env line that caused it.
    """
    return (os.getenv(name) or "").strip() or default


def brain_from_env(manifest, logger: Optional[logging.Logger] = None):
    """
    Build a brain from the environment, falling back to the keyword brain.

    Key resolution: DEEPSEEK_API_KEY first, then OPENAI_API_KEY. Optional
    overrides SMARTBOARD_MODEL and SMARTBOARD_BASE_URL apply to either. With no
    key at all you get a HeuristicBrain over `manifest` — the board is never
    dead on arrival during a demo.
    """
    log = logger or logging.getLogger("smartboard")

    deepseek = _env("DEEPSEEK_API_KEY")
    key = deepseek or _env("OPENAI_API_KEY")
    if not key:
        log.warning("No model key found — the board is running on the heuristic brain.")
        return HeuristicBrain(manifest)

    if deepseek:
        base_url = _env("SMARTBOARD_BASE_URL", "https://api.deepseek.com/v1")
        model = _env("SMARTBOARD_MODEL", "deepseek-chat")
    else:
        base_url = _env("SMARTBOARD_BASE_URL", "https://api.openai.com/v1")
        model = _env("SMARTBOARD_MODEL", "gpt-4o")

    if not base_url.startswith(("http://", "https://")):
        raise ValueError(
            f"SMARTBOARD_BASE_URL must start with http:// or https:// (got {base_url!r}). "
            "Leave it unset to use the provider default."
        )

    log.info("SmartBoard brain: %s at %s", model, base_url)
    return OpenAICompatBrain(api_key=key, base_url=base_url, model=model)
