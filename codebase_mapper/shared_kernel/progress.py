"""Shared, time-throttled progress reporter for long per-item passes.

Used by the L4 enrichment passes (file_summary / concept_description /
schema_purpose), which iterate thousands of items and, for the
Ollama-backed kinds, are network-bound and slow. The reporter replaces
one-unthrottled-line-per-item output with a steady heartbeat that shows
position, percent, throughput, ETA, a cached tally, and elapsed time.

Design notes:

- **Time-throttled, not count-throttled.** After the first line it emits
  at most once per ``min_interval_s``. On a slow network-bound run that
  gives a predictable heartbeat regardless of per-item latency; on a fast
  pass it collapses to a handful of lines. The final item always emits
  when ``total`` is known, so completion is visible.
- **Clock is injected per call.** ``update`` / ``summary`` take an
  optional ``now`` (monotonic seconds); production omits it and reads
  ``time.monotonic()``, tests pass a fixed value. This keeps the emit
  decision and the rate/ETA math fully deterministic under test.
- **``tag`` is the verbatim line prefix** (e.g. ``"[L4] file_summary"``),
  so callers control the existing log grammar.
"""
from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from typing import TextIO


def format_duration(seconds: float) -> str:
    """``H:MM:SS`` when >= 1h, else ``M:SS``. Negative clamps to ``0:00``."""
    total = max(0, int(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


@dataclass
class ProgressReporter:
    """Stateful heartbeat for one per-item pass.

    ``total=None`` means the count is not known ahead of time; lines then
    show ``#N`` with no percent/ETA. Otherwise lines show ``i/total (p%)``
    plus an ETA derived from observed throughput.
    """

    tag: str
    total: int | None = None
    min_interval_s: float = 2.0
    stream: TextIO | None = None

    _count: int = field(default=0, init=False)
    _cached: int = field(default=0, init=False)
    _start: float | None = field(default=None, init=False)
    _last_emit: float | None = field(default=None, init=False)

    # ---- read-only state -------------------------------------------------

    @property
    def count(self) -> int:
        return self._count

    @property
    def cached(self) -> int:
        return self._cached

    # ---- pure helpers ----------------------------------------------------

    def rate(self, now: float) -> float | None:
        """Items per second so far, or ``None`` before the first update /
        with zero elapsed time."""
        if self._start is None:
            return None
        elapsed = now - self._start
        return (self._count / elapsed) if elapsed > 0 else None

    def eta_seconds(self, now: float) -> float | None:
        """Estimated seconds remaining, or ``None`` when total/rate are
        unavailable."""
        if not self.total:
            return None
        r = self.rate(now)
        if not r:
            return None
        return max(0, self.total - self._count) / r

    def _should_emit(self, now: float, *, first: bool, last: bool) -> bool:
        if first or last:
            return True
        if self._last_emit is None:
            return True
        return (now - self._last_emit) >= self.min_interval_s

    def format_line(self, label: str, now: float) -> str:
        if self.total:
            pct = int(100 * self._count / self.total)
            prog = f"{self._count}/{self.total} ({pct}%)"
        else:
            prog = f"#{self._count}"
        parts = [self.tag, prog]
        r = self.rate(now)
        if r is not None:
            parts.append(f"{r:.1f}/s")
        eta = self.eta_seconds(now)
        if eta is not None:
            parts.append(f"eta {format_duration(eta)}")
        if self._cached:
            parts.append(f"{self._cached} cached")
        if self._start is not None:
            parts.append(f"elapsed {format_duration(now - self._start)}")
        if label:
            parts.append(str(label))
        return "  ".join(parts)

    # ---- driver ----------------------------------------------------------

    def update(self, label: str = "", *, cached: bool = False,
               now: float | None = None) -> str | None:
        """Record one processed item. Returns the emitted line, or ``None``
        when throttled. The internal counters always advance regardless of
        whether a line was emitted."""
        if now is None:
            now = time.monotonic()
        first = self._count == 0
        if first:
            self._start = now
        self._count += 1
        if cached:
            self._cached += 1
        last = self.total is not None and self._count >= self.total
        if self._should_emit(now, first=first, last=last):
            line = self.format_line(label, now)
            self._last_emit = now
            self._emit(line)
            return line
        return None

    def summary(self, *, now: float | None = None) -> str:
        """Emit and return a closing one-line tally."""
        if now is None:
            now = time.monotonic()
        elapsed = 0.0 if self._start is None else now - self._start
        computed = self._count - self._cached
        r = self.rate(now)
        rate_txt = f", {r:.1f}/s" if r else ""
        line = (f"{self.tag}  done — {self._count} item(s) "
                f"({computed} computed, {self._cached} cached) "
                f"in {format_duration(elapsed)}{rate_txt}")
        self._emit(line)
        return line

    def _emit(self, line: str) -> None:
        out = self.stream if self.stream is not None else sys.stderr
        print(line, file=out)
