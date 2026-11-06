"""Fan a query out to every provider in parallel and merge the results."""

from __future__ import annotations

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from itertools import zip_longest
from typing import Dict, Iterable, List, Optional

from .models import Video
from .providers import Provider

log = logging.getLogger(__name__)


class SearchAggregator:
    def __init__(
        self,
        providers: Dict[str, Provider],
        deadline: float = 15.0,
        cache_ttl: float = 300.0,
        cache_size: int = 128,
    ):
        self.providers = providers
        self.deadline = deadline
        self.cache_ttl = cache_ttl
        self.cache_size = cache_size
        self._cache: Dict[tuple, tuple] = {}
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(max_workers=max(4, len(providers) * 2))

    def search(
        self, query: str, limit: int = 8, sources: Optional[Iterable[str]] = None
    ) -> dict:
        keys = [k for k in (sources or self.providers) if k in self.providers]
        cache_key = (query.lower(), limit, tuple(sorted(keys)))
        cached = self._cache_get(cache_key)
        if cached is not None:
            return cached

        futures = {
            self._pool.submit(self.providers[k].search, query, limit): k for k in keys
        }
        done, pending = wait(futures, timeout=self.deadline)

        per_source: Dict[str, List[Video]] = {}
        status: Dict[str, dict] = {}
        for fut, key in futures.items():
            if fut in pending:
                fut.cancel()
                status[key] = {"ok": False, "count": 0, "error": "timed out"}
                continue
            try:
                videos = fut.result()
                per_source[key] = videos
                status[key] = {"ok": True, "count": len(videos), "error": None}
            except Exception as exc:  # one broken source must not break the page
                log.warning("provider %s failed: %s", key, exc)
                status[key] = {"ok": False, "count": 0, "error": str(exc)[:200]}

        merged = interleave(per_source[k] for k in keys if k in per_source)
        result = {
            "query": query,
            "results": [v.to_dict() for v in merged],
            "sources": status,
        }
        # Only cache if every source answered, so transient failures retry.
        if all(s["ok"] for s in status.values()):
            self._cache_put(cache_key, result)
        return result

    def _cache_get(self, key):
        with self._lock:
            hit = self._cache.get(key)
            if hit and time.monotonic() - hit[0] < self.cache_ttl:
                return hit[1]
            self._cache.pop(key, None)
            return None

    def _cache_put(self, key, value):
        with self._lock:
            if len(self._cache) >= self.cache_size:
                oldest = min(self._cache, key=lambda k: self._cache[k][0])
                del self._cache[oldest]
            self._cache[key] = (time.monotonic(), value)


def interleave(lists: Iterable[List[Video]]) -> List[Video]:
    """Round-robin across sources so no single site dominates the top."""
    out, seen = [], set()
    for row in zip_longest(*lists):
        for v in row:
            if v is None:
                continue
            ident = (v.source, v.id)
            if ident not in seen:
                seen.add(ident)
                out.append(v)
    return out
