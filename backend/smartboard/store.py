"""
Result store.

The brain receives a `result_id`, never the rows. The browser fetches rows
directly from /api/result/{id}. Three things fall out of that:

  - prompt size is independent of result size
  - the model cannot misquote numbers it never saw
  - repeat questions are cache hits

In production, swap the dict for Redis and keep the interface.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class StoredResult:
    result_id: str
    rows: List[Dict[str, Any]]
    columns: List[Dict[str, Any]]
    label: Optional[str]
    sql: str
    params: Dict[str, Any]
    dataset: str
    elapsed_ms: int
    created_at: float = field(default_factory=time.time)


class ResultStore:
    def __init__(self, ttl_seconds: int = 3600, max_entries: int = 500):
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self._data: Dict[str, StoredResult] = {}
        self._by_fingerprint: Dict[str, str] = {}
        self._lock = threading.Lock()

    @staticmethod
    def fingerprint(sql: str, params: Dict[str, Any], scope_key: str = "") -> str:
        blob = json.dumps({"s": sql, "p": params, "k": scope_key}, sort_keys=True, default=str)
        return hashlib.sha256(blob.encode()).hexdigest()[:24]

    def get_by_fingerprint(self, fp: str) -> Optional[StoredResult]:
        with self._lock:
            rid = self._by_fingerprint.get(fp)
            if not rid:
                return None
            res = self._data.get(rid)
            if res and time.time() - res.created_at < self.ttl:
                return res
            self._by_fingerprint.pop(fp, None)
            return None

    def put(self, result: StoredResult, fingerprint: Optional[str] = None) -> StoredResult:
        with self._lock:
            self._evict()
            self._data[result.result_id] = result
            if fingerprint:
                self._by_fingerprint[fingerprint] = result.result_id
        return result

    def get(self, result_id: str) -> Optional[StoredResult]:
        with self._lock:
            res = self._data.get(result_id)
            if res and time.time() - res.created_at >= self.ttl:
                self._data.pop(result_id, None)
                return None
            return res

    def _evict(self) -> None:
        now = time.time()
        stale = [k for k, v in self._data.items() if now - v.created_at >= self.ttl]
        for k in stale:
            self._data.pop(k, None)
        if len(self._data) >= self.max_entries:
            oldest = sorted(self._data.items(), key=lambda kv: kv[1].created_at)
            for k, _ in oldest[: len(self._data) - self.max_entries + 1]:
                self._data.pop(k, None)

    @staticmethod
    def new_id() -> str:
        return "r_" + uuid.uuid4().hex[:10]
