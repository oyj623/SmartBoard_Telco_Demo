"""
SmartBoard — a governed engine for AI-driven dashboards.

The model is treated as an untrusted client that happens to be good at natural
language. It is given a vocabulary, not a keyboard:

  1. It never writes SQL. It names metrics and dimensions from a manifest you
     author; every literal it supplies becomes a bound parameter.
  2. It never writes UI code. It emits typed commands from a closed vocabulary
     against a viz registry you populate.
  3. Its capability surface is derived from the manifest, so the prompt and the
     catalog cannot drift apart as the project grows.

Vendor this package into your backend, write a manifest, and mount the router:

    from smartboard import Engine, load_manifest
    from smartboard.adapters.sqlite import SQLiteAdapter
    from smartboard.brain import brain_from_env
    from smartboard.fastapi_binding import create_board_router

See docs/ARCHITECTURE.md for the full story.
"""

from .commands import COMMAND_TYPES, DashboardCommand
from .engine import CommandOutcome, Engine
from .ir import Filter, Query, ResultHandle, Sort, TimeRange
from .manifest import Dataset, Dimension, Manifest, ManifestError, Metric, load_manifest
from .security import QueryGuard, SecurityContext, no_tenancy
from .session import TurnContext, run_turn
from .store import ResultStore, StoredResult
from .tools import build_tools, to_anthropic_format, to_openai_format
from .brain.base import AssistantTurn, BrainClient, ToolCall, build_system_prompt

__version__ = "0.1.0"

__all__ = [
    "AssistantTurn",
    "BrainClient",
    "COMMAND_TYPES",
    "CommandOutcome",
    "DashboardCommand",
    "Dataset",
    "Dimension",
    "Engine",
    "Filter",
    "Manifest",
    "ManifestError",
    "Metric",
    "Query",
    "QueryGuard",
    "ResultHandle",
    "ResultStore",
    "SecurityContext",
    "Sort",
    "StoredResult",
    "TimeRange",
    "ToolCall",
    "TurnContext",
    "build_system_prompt",
    "build_tools",
    "load_manifest",
    "no_tenancy",
    "run_turn",
    "to_anthropic_format",
    "to_openai_format",
]
