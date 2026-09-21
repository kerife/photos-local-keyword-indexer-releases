"""PyInstaller entry point for the private stdin/stdout worker protocol."""

from __future__ import annotations

import json
import platform
import sys
import threading
from collections.abc import Callable


def _self_check() -> int:
    """Report only frozen-runtime facts; never open Photos or Ollama.

    This is intentionally a separate entry path so release diagnostics can
    prove that the executable inside the app starts with its embedded Python
    runtime without triggering TCC, Apple Events, PhotoKit, or network I/O.
    """
    print(json.dumps({
        "status": "ok",
        "runtime": "embedded" if getattr(sys, "frozen", False) else "source",
        "architecture": platform.machine(),
        "protocol": "jsonl",
    }, ensure_ascii=False, separators=(",", ":")))
    return 0


def _run_protocol_with_main_event_loop(
    *,
    serve: Callable[[], None],
    run_event_loop: Callable[[], None],
    schedule_stop: Callable[[], None],
) -> None:
    """Keep AppKit's main run loop available while JSONL blocks on stdin."""
    failures: list[BaseException] = []

    def run_protocol() -> None:
        try:
            serve()
        except BaseException as error:
            failures.append(error)
        finally:
            schedule_stop()

    protocol_thread = threading.Thread(
        target=run_protocol,
        name="PhotosIndexerWorkerProtocol",
    )
    protocol_thread.start()
    run_event_loop()
    protocol_thread.join()
    if failures:
        raise failures[0]


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--self-check":
        raise SystemExit(_self_check())
    from PyObjCTools.AppHelper import callAfter, runConsoleEventLoop, stopEventLoop
    from photos_indexer.ipc import main

    _run_protocol_with_main_event_loop(
        serve=main,
        run_event_loop=runConsoleEventLoop,
        schedule_stop=lambda: callAfter(stopEventLoop),
    )
