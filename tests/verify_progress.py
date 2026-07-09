#!/usr/bin/env python3
"""verify_progress.py — shared ProgressReporter (L4 progress overhaul).

The L4 file_summary loop printed one unthrottled line per file with a
bare running count (`#N`) and no rate/ETA — a flood on a large repo and
uninformative on a slow, network-bound run. This pins the replacement:
a time-throttled reporter that shows `i/total (pct%)`, throughput, ETA,
a cached tally, and elapsed time.

The reporter takes an injected monotonic `now` on every call, so these
tests are fully deterministic (no wall-clock flakiness).

Run:  .venv/bin/python tests/verify_progress.py
"""
from __future__ import annotations

import io
import sys
import traceback
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from codebase_mapper.shared_kernel.progress import (
    ProgressReporter,
    format_duration,
)

PASS = 0
FAIL = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global PASS, FAIL
    if ok:
        PASS += 1
        print(f"  PASS  {name}")
    else:
        FAIL += 1
        print(f"  FAIL  {name}  {detail}")


def _rep(**kw) -> ProgressReporter:
    kw.setdefault("stream", io.StringIO())
    return ProgressReporter(**kw)


# ----------------------------------------------------------- format_duration

def test_format_duration() -> None:
    check("0s", format_duration(0) == "0:00", format_duration(0))
    check("5s", format_duration(5) == "0:05")
    check("65s", format_duration(65) == "1:05")
    check("1h", format_duration(3600) == "1:00:00")
    check("1h2m5s", format_duration(3725) == "1:02:05")
    check("negative clamps", format_duration(-9) == "0:00")


# --------------------------------------------------------------- first / format

def test_first_line_exact_format() -> None:
    r = _rep(tag="[L4] file_summary", total=100)
    line = r.update("a.c", now=0.0)
    check("first line exact",
          line == "[L4] file_summary  1/100 (1%)  elapsed 0:00  a.c",
          repr(line))


def test_unknown_total_uses_hash_and_no_eta() -> None:
    r = _rep(tag="[x]", total=None)
    line = r.update("p", now=0.0)
    check("unknown total exact",
          line == "[x]  #1  elapsed 0:00  p", repr(line))
    r.update("q", now=100.0)
    l3 = r.update("z", now=200.0)
    check("no eta when total unknown", "eta" not in (l3 or ""), repr(l3))
    check("uses #count not pct", "#3" in (l3 or ""), repr(l3))


# ----------------------------------------------------------------- throttle

def test_throttle_and_counting() -> None:
    r = _rep(tag="[t]", total=1000, min_interval_s=10.0)
    a = r.update("f1", now=0.0)       # first → emits
    b = r.update("f2", now=1.0)       # within 10s → throttled
    c = r.update("f3", now=2.0)       # still throttled
    check("first emits", a is not None)
    check("second throttled (None)", b is None, repr(b))
    check("third throttled (None)", c is None, repr(c))
    check("count still advances while throttled", r.count == 3, str(r.count))
    d = r.update("f4", now=12.0)      # >=10s since last emit → emits
    check("emits again after interval", d is not None, repr(d))


def test_last_item_always_emits() -> None:
    r = _rep(tag="[t]", total=3, min_interval_s=1e9)
    r.update("a", now=0.0)            # first
    b = r.update("b", now=0.1)        # throttled
    c = r.update("c", now=0.2)        # last → must emit despite interval
    check("mid throttled", b is None)
    check("last always emits", c is not None, repr(c))
    check("last shows 3/3 (100%)", "3/3 (100%)" in (c or ""), repr(c))


# --------------------------------------------------------------- rate / eta

def test_rate() -> None:
    r = _rep(tag="[t]", total=100, min_interval_s=0.0)
    r.update("a", now=0.0)            # start=0, count=1
    r.update("b", now=2.0)            # count=2
    check("rate = count/elapsed = 2/2 = 1.0", r.rate(2.0) == 1.0,
          str(r.rate(2.0)))
    check("rate None before any update", _rep(tag="[t]").rate(5.0) is None)


def test_eta() -> None:
    r = _rep(tag="[t]", total=10, min_interval_s=0.0)
    r.update("a", now=0.0)
    r.update("b", now=2.0)            # count=2, rate=1.0/s
    # remaining 8 at 1.0/s → 8s
    check("eta = 8.0s", r.eta_seconds(2.0) == 8.0, str(r.eta_seconds(2.0)))
    check("eta None when total unknown",
          _rep(tag="[t]").eta_seconds(1.0) is None)


def test_line_has_rate_and_eta() -> None:
    r = _rep(tag="[t]", total=10, min_interval_s=0.0)
    r.update("a", now=0.0)
    line = r.update("b", now=2.0)
    check("line shows throughput", "1.0/s" in (line or ""), repr(line))
    check("line shows eta", "eta 0:08" in (line or ""), repr(line))
    check("line shows pct", "2/10 (20%)" in (line or ""), repr(line))
    check("line shows elapsed", "elapsed 0:02" in (line or ""), repr(line))


# ------------------------------------------------------------------- cached

def test_cached_tally() -> None:
    r = _rep(tag="[t]", total=10, min_interval_s=0.0)
    r.update("a", cached=True, now=0.0)
    line = r.update("b", cached=True, now=1.0)
    check("cached count", r.cached == 2, str(r.cached))
    check("line shows cached", "2 cached" in (line or ""), repr(line))


def test_summary() -> None:
    r = _rep(tag="[L4] file_summary", total=5, min_interval_s=0.0)
    for i, now in enumerate([0.0, 1.0, 2.0, 3.0, 4.0]):
        r.update(f"f{i}", cached=(i < 2), now=now)
    s = r.summary(now=4.0)
    check("summary counts computed vs cached",
          "5 item(s) (3 computed, 2 cached)" in s, repr(s))
    check("summary shows elapsed", "in 0:04" in s, repr(s))
    check("summary says done", "done" in s, repr(s))


def test_emission_to_stream() -> None:
    buf = io.StringIO()
    r = ProgressReporter(tag="[t]", total=2, stream=buf, min_interval_s=0.0)
    r.update("a", now=0.0)
    r.update("b", now=1.0)
    out = buf.getvalue()
    check("both lines emitted to stream", out.count("\n") == 2, repr(out))
    check("stream carries the tag", "[t]" in out)


def main() -> int:
    tests = [
        test_format_duration,
        test_first_line_exact_format,
        test_unknown_total_uses_hash_and_no_eta,
        test_throttle_and_counting,
        test_last_item_always_emits,
        test_rate,
        test_eta,
        test_line_has_rate_and_eta,
        test_cached_tally,
        test_summary,
        test_emission_to_stream,
    ]
    for t in tests:
        print(f"\n{t.__name__}:")
        try:
            t()
        except Exception:
            global FAIL
            FAIL += 1
            print(f"  FAIL  {t.__name__} raised:")
            traceback.print_exc()
    print(f"\n{'=' * 50}\n  {PASS} passed, {FAIL} failed")
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
