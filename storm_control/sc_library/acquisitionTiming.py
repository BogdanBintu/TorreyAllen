"""Opt-in wall-clock timing for acquisition startup and shutdown.

Set HAL_ACQUISITION_TIMING=1 before launching HAL. All threads use the same
perf_counter clock, so consecutive timestamps can be compared directly.
"""

from contextlib import contextmanager
import os
import time


def enabled():
    return os.environ.get("HAL_ACQUISITION_TIMING", "").strip().lower() in ("1", "true", "yes")


def event(label, **details):
    if enabled():
        fields = " ".join("{}={!r}".format(key, value) for key, value in details.items())
        print("[ACQ {:.6f}] {} {}".format(time.perf_counter(), label, fields).rstrip(), flush=True)


@contextmanager
def span(label, **details):
    if not enabled():
        yield
        return
    started = time.perf_counter()
    event(label + " begin", **details)
    try:
        yield
    finally:
        event(label + " end", elapsed_ms=round(1000.0 * (time.perf_counter() - started), 3), **details)
