"""Run independent I/O-bound steps at the same time.

Provider calls (weather, events, maps, web, LLM) spend their time waiting on the
network, so a request that needs several of them should wait for the slowest one,
not for the sum. A bounded shared pool keeps the number of threads predictable.
"""
from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any, Callable, Iterator

_PREFIX = "geoguide-io"
_POOL = ThreadPoolExecutor(max_workers=32, thread_name_prefix=_PREFIX)


@contextmanager
def _executor(size: int) -> Iterator[ThreadPoolExecutor]:
    """The shared pool, or, when already inside it (nested fan-out), a short-lived one so waits can't deadlock."""
    if threading.current_thread().name.startswith(_PREFIX):
        with ThreadPoolExecutor(max_workers=max(1, size), thread_name_prefix=_PREFIX + "-nested") as nested:
            yield nested
    else:
        yield _POOL


def run_parallel(tasks: dict[str, Callable[[], Any]]) -> dict[str, Any]:
    """Run each callable concurrently; returns {name: result}. The first exception is re-raised."""
    if len(tasks) <= 1:
        return {name: fn() for name, fn in tasks.items()}
    with _executor(len(tasks)) as pool:
        futures = {name: pool.submit(fn) for name, fn in tasks.items()}
        return {name: future.result() for name, future in futures.items()}


def map_parallel(fn: Callable[[Any], Any], items: list[Any]) -> list[Any]:
    """``[fn(item) for item in items]`` with the calls running concurrently (order preserved)."""
    if len(items) <= 1:
        return [fn(item) for item in items]
    with _executor(len(items)) as pool:
        return list(pool.map(fn, items))
