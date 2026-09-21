from __future__ import annotations

import json
import importlib.util
import os
import subprocess
import sys
import threading
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ENTRY_POINT = PROJECT_ROOT / "packaging" / "worker_entry.py"


def test_worker_entry_self_check_is_local_and_does_not_start_ipc() -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(PROJECT_ROOT / "src"),
            "OLLAMA_HOST": "https://must-not-be-used.invalid",
            "VIRTUAL_ENV": "/tmp/must-not-be-used",
        }
    )
    result = subprocess.run(
        [sys.executable, str(ENTRY_POINT), "--self-check"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
        cwd=PROJECT_ROOT,
    )

    assert result.returncode == 0, result.stderr
    assert result.stderr == ""
    diagnostic = json.loads(result.stdout)
    assert diagnostic == {
        "status": "ok",
        "runtime": "source",
        "architecture": diagnostic["architecture"],
        "protocol": "jsonl",
    }
    assert diagnostic["architecture"]
    assert "127.0.0.1" not in result.stdout
    assert "https://" not in result.stdout


def test_worker_entry_defers_ipc_import_until_normal_protocol_start() -> None:
    source = ENTRY_POINT.read_text(encoding="utf-8")
    self_check = source.split("def _self_check()", 1)[1].split("if __name__", 1)[0]
    assert "from photos_indexer.ipc import main" not in self_check
    assert source.index("from photos_indexer.ipc import main") > source.index("raise SystemExit(_self_check())")


def test_worker_entry_keeps_the_main_thread_available_for_mapkit_callbacks() -> None:
    spec = importlib.util.spec_from_file_location("worker_entry", ENTRY_POINT)
    assert spec is not None and spec.loader is not None
    worker_entry = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker_entry)

    main_thread = threading.get_ident()
    event_loop_started = threading.Event()
    stop_requested = threading.Event()
    observed: dict[str, int] = {}

    def serve() -> None:
        assert event_loop_started.wait(timeout=1)
        observed["protocol"] = threading.get_ident()

    def run_event_loop() -> None:
        observed["event_loop"] = threading.get_ident()
        event_loop_started.set()
        assert stop_requested.wait(timeout=1)

    def schedule_stop() -> None:
        observed["stop_scheduler"] = threading.get_ident()
        stop_requested.set()

    worker_entry._run_protocol_with_main_event_loop(
        serve=serve,
        run_event_loop=run_event_loop,
        schedule_stop=schedule_stop,
    )

    assert observed["event_loop"] == main_thread
    assert observed["protocol"] != main_thread
    assert observed["stop_scheduler"] == observed["protocol"]
