from __future__ import annotations

import tempfile
import unittest
import json
from pathlib import Path
from unittest.mock import patch

try:
    from typer.testing import CliRunner
except ModuleNotFoundError:
    CliRunner = None  # type: ignore[assignment,misc]

from photos_indexer.workflows import APP_VERSION, WorkflowResult
from tests.test_workflows import make_manifest, make_photo


@unittest.skipUnless(CliRunner is not None, "Typer CLI dependency is not installed")
class CliTests(unittest.TestCase):
    def test_cli_doctor_runs_preflight_without_invoking_scan(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        calls: list[object] = []

        def preflight(payload: dict[str, object], token: object) -> tuple[int, dict[str, object]]:
            calls.append((payload, token))
            return 0, {"models": {"qwen3-vl:4b": "0.32.1"}, "next_action": "none"}

        def scan_runner(*args: object, **kwargs: object) -> WorkflowResult:
            raise AssertionError("doctor must not invoke scan")

        app = create_app(
            scan_runner=scan_runner,
            preflight_runner=preflight,
            console=Console(width=200, color_system=None),
        )

        output = CliRunner().invoke(app, ["doctor", "--model", "qwen3-vl:4b"])

        self.assertEqual(output.exit_code, 0, output.output)
        self.assertIn("doctor:ready", output.output)
        self.assertIn("scope:ollama_photoscript_local", output.output)
        self.assertIn("model:qwen3-vl:4b=0.32.1", output.output)
        self.assertIn("tcc:not_checked", output.output)
        self.assertIn("helper:not_checked", output.output)
        self.assertEqual(len(calls), 1)

    def test_cli_doctor_reports_bounded_app_and_runtime_identity(self) -> None:
        from photos_indexer.cli import create_app
        from photos_indexer.runtime_identity import RuntimeIdentity
        from rich.console import Console

        app = create_app(
            preflight_runner=lambda payload, token: (
                0,
                {"models": {"qwen3-vl:4b": "0.32.1"}, "next_action": "none"},
            ),
            console=Console(width=200, color_system=None),
        )

        with patch(
            "photos_indexer.cli.current_runtime_identity",
            return_value=RuntimeIdentity(
                executable_hint=".venv/bin/python3.11",
                python_version="3.11.15",
                architecture="arm64",
                packaged=False,
            ),
        ):
            output = CliRunner().invoke(app, ["doctor"])

        self.assertEqual(output.exit_code, 0, output.output)
        self.assertIn(f"app_version:{APP_VERSION}", output.output)
        self.assertIn("runtime:.venv/bin/python3.11 version=3.11.15 architecture=arm64 packaged=false", output.output)
        self.assertNotIn("/private/user", output.output)

    def test_cli_doctor_json_emits_only_bounded_support_fields(self) -> None:
        from photos_indexer.cli import create_app
        from photos_indexer.runtime_identity import RuntimeIdentity
        from rich.console import Console

        app = create_app(
            preflight_runner=lambda payload, token: (
                0,
                {"models": {"qwen3-vl:4b": "0.32.1"}, "next_action": "none"},
            ),
            console=Console(width=200, color_system=None),
        )

        with patch(
            "photos_indexer.cli.current_runtime_identity",
            return_value=RuntimeIdentity(
                executable_hint="project/.venv311/bin/python3.11",
                python_version="3.11.15",
                architecture="arm64",
                packaged=False,
            ),
        ):
            output = CliRunner().invoke(app, ["doctor", "--json"])

        self.assertEqual(output.exit_code, 0, output.output)
        report = json.loads(output.output)
        self.assertEqual(report["format_version"], 1)
        self.assertEqual(report["app_version"], APP_VERSION)
        self.assertEqual(report["exit_code"], 0)
        self.assertEqual(report["runtime"], {
            "executable": "project/.venv311/bin/python3.11",
            "python_version": "3.11.15",
            "architecture": "arm64",
            "packaged": False,
        })
        self.assertEqual(report["models"], {"qwen3-vl:4b": "0.32.1"})
        self.assertEqual(report["error_codes"], [])
        self.assertEqual(report["next_action"], "none")
        self.assertTrue(report["tcc_not_checked"])
        self.assertTrue(report["helper_not_checked"])
        self.assertNotIn("/private", output.output)

    def test_cli_doctor_json_stays_one_line_in_narrow_terminal(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        app = create_app(
            preflight_runner=lambda payload, token: (0, {"next_action": "none"}),
            console=Console(width=40, color_system=None),
        )

        with patch("photos_indexer.cli.current_runtime_identity") as runtime:
            runtime.return_value.executable_hint = "project/.venv311/bin/python3.11"
            runtime.return_value.python_version = "3.11.15"
            runtime.return_value.architecture = "arm64"
            runtime.return_value.packaged = False
            output = CliRunner().invoke(app, ["doctor", "--json"])

        self.assertEqual(output.exit_code, 0, output.output)
        self.assertEqual(len(output.output.splitlines()), 1, output.output)
        self.assertEqual(json.loads(output.output)["exit_code"], 0)

    def test_cli_doctor_explains_the_surfaces_it_does_not_check(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        app = create_app(
            preflight_runner=lambda payload, token: (
                0,
                {"models": {"qwen3-vl:4b": "0.32.1"}, "next_action": "none"},
            ),
            console=Console(width=200, color_system=None),
        )

        output = CliRunner().invoke(app, ["doctor"])

        self.assertEqual(output.exit_code, 0, output.output)
        self.assertIn("scope:ollama_photoscript_local", output.output)
        self.assertIn("El dry-run comprobará TCC y el helper", output.output)
        self.assertIn("no abre Fotos", output.output)

    def test_cli_doctor_rejects_a_contradictory_successful_preflight(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        app = create_app(
            preflight_runner=lambda payload, token: (
                0,
                {"error_codes": ["OLLAMA_UNAVAILABLE"], "next_action": "retry_preflight"},
            ),
            console=Console(width=200, color_system=None),
        )

        output = CliRunner().invoke(app, ["doctor"])

        self.assertEqual(output.exit_code, 2, output.output)
        self.assertIn("doctor:blocked", output.output)
        self.assertIn("error:UNSAFE_PREFLIGHT_RESULT", output.output)
        self.assertNotIn("doctor:ready", output.output)

    def test_cli_doctor_checks_both_models_for_adaptive_policy(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        payloads: list[dict[str, object]] = []

        def preflight(payload: dict[str, object], token: object) -> tuple[int, dict[str, object]]:
            payloads.append(payload)
            return 0, {"models": {name: "0.32.1" for name in payload["models"]}, "next_action": "none"}

        output = CliRunner().invoke(
            create_app(preflight_runner=preflight, console=Console(width=200, color_system=None)),
            ["doctor", "--model-policy", "adaptive", "--fast-model", "vision:fast", "--detailed-model", "vision:detailed"],
        )

        self.assertEqual(output.exit_code, 0, output.output)
        self.assertEqual(payloads, [{"models": ["vision:fast", "vision:detailed"]}])
        self.assertIn("model:vision:fast=0.32.1", output.output)
        self.assertIn("model:vision:detailed=0.32.1", output.output)

    def test_cli_doctor_prints_scope_once_for_adaptive_policy(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        output = CliRunner().invoke(
            create_app(
                preflight_runner=lambda payload, token: (
                    0,
                    {"models": {name: "0.32.1" for name in payload["models"]}, "next_action": "none"},
                ),
                console=Console(width=200, color_system=None),
            ),
            [
                "doctor",
                "--model-policy",
                "adaptive",
                "--fast-model",
                "vision:fast",
                "--detailed-model",
                "vision:detailed",
            ],
        )

        self.assertEqual(output.exit_code, 0, output.output)
        self.assertEqual(output.output.count("scope:ollama_photoscript_local"), 1)

    def test_cli_doctor_deduplicates_equal_adaptive_models(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        payloads: list[dict[str, object]] = []

        def preflight(payload: dict[str, object], token: object) -> tuple[int, dict[str, object]]:
            payloads.append(payload)
            return 0, {"models": {"vision:same": "0.32.1"}, "next_action": "none"}

        output = CliRunner().invoke(
            create_app(preflight_runner=preflight, console=Console(width=200, color_system=None)),
            ["doctor", "--model-policy", "adaptive", "--fast-model", "vision:same", "--detailed-model", "vision:same"],
        )

        self.assertEqual(output.exit_code, 0, output.output)
        self.assertEqual(payloads, [{"models": ["vision:same"]}])

    def test_cli_doctor_accepts_successful_preflight_without_optional_next_action(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        app = create_app(
            preflight_runner=lambda payload, token: (
                0,
                {"models": {"qwen3-vl:4b": "0.32.1"}},
            ),
            console=Console(width=200, color_system=None),
        )

        output = CliRunner().invoke(app, ["doctor"])

        self.assertEqual(output.exit_code, 0, output.output)
        self.assertIn("doctor:ready", output.output)
        self.assertIn("next:none", output.output)

    def test_cli_explains_recovery_for_an_unsafe_workflow_result(self) -> None:
        from photos_indexer.cli import create_app
        from photos_indexer.runtime_identity import RuntimeIdentity
        from rich.console import Console

        result = WorkflowResult(
            exit_code=2,
            error_codes=("UNSAFE_WORKFLOW_RESULT",),
            next_action="fix_fatal_error",
        )
        app = create_app(
            scan_runner=lambda *args, **kwargs: result,
            console=Console(width=200, color_system=None),
        )

        with patch(
            "photos_indexer.cli.current_runtime_identity",
            return_value=RuntimeIdentity(
                executable_hint="project/.venv311/bin/python3.11",
                python_version="3.11.15",
                architecture="arm64",
                packaged=False,
            ),
        ):
            output = CliRunner().invoke(app, ["scan", "--limit", "1"])

        self.assertEqual(output.exit_code, 2, output.output)
        self.assertIn("error:UNSAFE_WORKFLOW_RESULT", output.output)
        self.assertIn("doctor", output.output)
        self.assertIn("mismo intérprete", output.output)
        self.assertIn("next:fix_fatal_error", output.output)
        self.assertIn(f"app_version:{APP_VERSION}", output.output)
        self.assertIn(
            "runtime:project/.venv311/bin/python3.11 version=3.11.15 architecture=arm64 packaged=false",
            output.output,
        )
        self.assertNotIn("/private/user", output.output)

    def test_cli_scan_prints_manifest_path_for_follow_up_review(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        manifest_path = Path("runs/example/manifest.json")
        result = WorkflowResult(exit_code=0, manifest_path=manifest_path)
        app = create_app(
            scan_runner=lambda *args, **kwargs: result,
            console=Console(width=200, color_system=None),
        )

        output = CliRunner().invoke(app, ["scan", "--limit", "1"])

        self.assertEqual(output.exit_code, 0)
        self.assertIn("manifest:runs/example/manifest.json", output.output)

    def test_cli_scan_keeps_an_empty_selection_as_a_warning_only_result(self) -> None:
        from photos_indexer.cli import create_app
        from photos_indexer.manifest import load_manifest, write_manifest
        from rich.console import Console

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            manifest_path = make_manifest(run_dir, [])
            manifest = load_manifest(run_dir)
            manifest.selection["requested"] = 1
            write_manifest(run_dir, manifest)
            result = WorkflowResult(
                exit_code=0,
                manifest_path=manifest_path,
                manifest=manifest,
                warning_codes=("FEWER_PHOTOS_AVAILABLE",),
                next_action="none",
            )
            app = create_app(
                scan_runner=lambda *args, **kwargs: result,
                console=Console(width=200, color_system=None),
            )

            output = CliRunner().invoke(app, ["scan", "--limit", "1"])

            self.assertEqual(output.exit_code, 0, output.output)
            self.assertIn("warning:FEWER_PHOTOS_AVAILABLE", output.output)
            self.assertNotIn("UNSAFE_WORKFLOW_RESULT", output.output)
            self.assertIn("next:none", output.output)

    def test_cli_scan_shows_scope_when_no_photos_are_eligible(self) -> None:
        from photos_indexer.cli import create_app
        from photos_indexer.manifest import load_manifest, write_manifest
        from rich.console import Console

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            manifest_path = make_manifest(run_dir, [])
            manifest = load_manifest(run_dir)
            manifest.selection["requested"] = 1
            write_manifest(run_dir, manifest)
            result = WorkflowResult(
                exit_code=0,
                manifest_path=manifest_path,
                manifest=manifest,
                warning_codes=("FEWER_PHOTOS_AVAILABLE",),
                next_action="none",
            )
            app = create_app(
                scan_runner=lambda *args, **kwargs: result,
                console=Console(width=200, color_system=None),
            )

            output = CliRunner().invoke(app, ["scan", "--limit", "1"])

            self.assertEqual(output.exit_code, 0, output.output)
            self.assertIn(
                "scope: requested=1, eligible=0, processed=0, screenshots_excluded=0",
                output.output,
            )

    def test_cli_prints_exact_pull_instruction_only_for_missing_model_result(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        missing = WorkflowResult(
            exit_code=2,
            error_codes=("OLLAMA_MODEL_MISSING",),
            safe_instruction="ollama pull vision:missing",
        )
        unavailable = WorkflowResult(exit_code=2, error_codes=("OLLAMA_PREFLIGHT_FAILED",))
        responses = [missing, unavailable]
        app = create_app(
            scan_runner=lambda root, limit, model: responses.pop(0),
            apply_runner=lambda path: unavailable,
            status_runner=lambda path: unavailable,
            rollback_runner=lambda path: unavailable,
            console=Console(width=200, color_system=None),
        )
        runner = CliRunner()

        missing_output = runner.invoke(app, ["scan", "--model", "vision:missing"])
        unavailable_output = runner.invoke(app, ["scan", "--model", "vision:broken"])

        self.assertEqual(missing_output.exit_code, 2)
        self.assertIn("ollama pull vision:missing", missing_output.output.splitlines())
        self.assertEqual(unavailable_output.exit_code, 2)
        self.assertNotIn("ollama pull", unavailable_output.output)

    def test_cli_explains_how_to_recover_when_ollama_is_unavailable(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        result = WorkflowResult(
            exit_code=2,
            error_codes=("OLLAMA_UNAVAILABLE",),
            next_action="retry_preflight",
        )
        app = create_app(
            scan_runner=lambda root, limit, model: result,
            console=Console(width=200, color_system=None),
        )

        output = CliRunner().invoke(app, ["scan", "--limit", "1"])

        self.assertEqual(output.exit_code, 2)
        self.assertIn("error:OLLAMA_UNAVAILABLE", output.output)
        self.assertIn("Inicia Ollama", output.output)
        self.assertIn("127.0.0.1:11434", output.output)
        self.assertIn("next:retry_preflight", output.output)

    def test_cli_explains_other_ollama_preflight_failures(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        cases = (
            ("OLLAMA_VERSION_OLD", "Actualiza Ollama"),
            ("OLLAMA_NO_VISION", "capacidad de visión"),
            ("OLLAMA_PREFLIGHT_FAILED", "Comprueba Ollama"),
            ("MODEL_INVALID", "modelo local válido"),
        )
        for code, expected in cases:
            with self.subTest(code=code):
                result = WorkflowResult(
                    exit_code=2,
                    error_codes=(code,),
                    next_action="fix_fatal_error" if code == "MODEL_INVALID" else "retry_preflight",
                )
                app = create_app(
                    scan_runner=lambda root, limit, model, result=result: result,
                    console=Console(width=200, color_system=None),
                )

                output = CliRunner().invoke(app, ["scan", "--limit", "1"])

                self.assertEqual(output.exit_code, 2)
                self.assertIn(f"error:{code}", output.output)
                self.assertIn(expected, output.output)

    def test_cli_rejects_an_empty_explicit_model_instead_of_using_the_default(self) -> None:
        from photos_indexer.cli import create_app

        calls: list[object] = []

        def scan_runner(*args: object, **kwargs: object) -> WorkflowResult:
            calls.append((args, kwargs))
            return WorkflowResult(exit_code=0)

        app = create_app(scan_runner=scan_runner)
        result = CliRunner().invoke(app, ["scan", "--model", "   "])

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--model no puede estar vacío", result.output)
        self.assertEqual(calls, [])

    def test_cli_rejects_adaptive_policy_with_explicit_model(self) -> None:
        from photos_indexer.cli import create_app

        called = False

        def scan_runner(*args: object, **kwargs: object) -> WorkflowResult:
            nonlocal called
            called = True
            return WorkflowResult(exit_code=0)

        result = CliRunner().invoke(
            create_app(scan_runner=scan_runner),
            ["scan", "--model-policy", "adaptive", "--model", "qwen3-vl:4b"],
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("--model no se puede combinar", result.output)
        self.assertFalse(called)

    def test_cli_scan_rejects_unknown_model_policy_before_running(self) -> None:
        from photos_indexer.cli import create_app

        called = False

        def scan_runner(*args: object, **kwargs: object) -> WorkflowResult:
            nonlocal called
            called = True
            return WorkflowResult(exit_code=0)

        result = CliRunner().invoke(
            create_app(scan_runner=scan_runner),
            ["scan", "--model-policy", "remote"],
        )

        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("single o adaptive", result.output)
        self.assertFalse(called)

    def test_cli_support_exports_only_the_sanitized_snapshot(self) -> None:
        from photos_indexer.cli import create_app
        from photos_indexer.manifest import load_manifest
        from rich.console import Console

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            photo = make_photo(1)
            photo.scan_state = "noop"
            photo.proposed_keywords = []
            photo.errors = [{"stage": "analysis", "code": "LOW_CONFIDENCE"}]
            manifest_path = make_manifest(run_dir, [photo], scan_status="ready_with_errors")
            manifest = load_manifest(run_dir)
            result = WorkflowResult(
                exit_code=1,
                error_codes=("LOW_CONFIDENCE",),
                next_action="manual_review",
                manifest=manifest,
                status_summary={"scan_status": "ready_with_errors", "processed": 1},
            )
            app = create_app(
                status_runner=lambda path: result,
                console=Console(width=200, color_system=None),
            )

            output = CliRunner().invoke(app, ["support", str(manifest_path)])

            self.assertEqual(output.exit_code, 1)
            report = json.loads(output.stdout)
            serialized = json.dumps(report, ensure_ascii=False)
            self.assertEqual(report["format_version"], 1)
            self.assertEqual(report["error_codes"], ["LOW_CONFIDENCE"])
            self.assertNotIn("Private title", serialized)
            self.assertNotIn("private-local-id", serialized)
            self.assertNotIn("playa", serialized)

    def test_cli_support_exit_code_matches_fail_closed_snapshot(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        result = WorkflowResult(exit_code=0, error_codes=("ANALYSIS_FAILED",))
        app = create_app(
            status_runner=lambda path: result,
            console=Console(width=200, color_system=None),
        )

        output = CliRunner().invoke(app, ["support", "runs/example/manifest.json"])

        self.assertEqual(output.exit_code, 2, output.output)
        report = json.loads(output.stdout)
        self.assertEqual(report["exit_code"], 2)
        self.assertEqual(report["error_codes"], ["UNSAFE_WORKFLOW_RESULT"])

    def test_cli_scan_exit_code_matches_fail_closed_snapshot(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        result = WorkflowResult(exit_code=0, error_codes=("ANALYSIS_FAILED",))
        app = create_app(
            scan_runner=lambda root, limit, model: result,
            console=Console(width=200, color_system=None),
        )

        output = CliRunner().invoke(app, ["scan", "--limit", "1"])

        self.assertEqual(output.exit_code, 2, output.output)
        self.assertIn("error:UNSAFE_WORKFLOW_RESULT", output.output)

    def test_cli_status_does_not_print_untrusted_status_fields(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        result = WorkflowResult(
            exit_code=0,
            status_summary={"caption": "texto privado", "location": "coordenadas", "processed": 1},
            next_action="none",
        )
        app = create_app(status_runner=lambda path: result, console=Console(width=200, color_system=None))

        output = CliRunner().invoke(app, ["status", "/tmp/manifest.json"])

        self.assertEqual(output.exit_code, 0)
        self.assertNotIn("texto privado", output.output)
        self.assertNotIn("coordenadas", output.output)
        self.assertIn("processed:1", output.output)

    def test_cli_status_does_not_print_untrusted_count_fields(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        result = WorkflowResult(
            exit_code=0,
            counts={"apply": {"caption": "texto privado"}},  # type: ignore[dict-item]
            next_action="none",
        )
        app = create_app(status_runner=lambda path: result, console=Console(width=200, color_system=None))

        output = CliRunner().invoke(app, ["status", "/tmp/manifest.json"])

        self.assertEqual(output.exit_code, 2)
        self.assertNotIn("texto privado", output.output)

    def test_cli_prints_tcc_guidance_and_never_apply_after_photos_denial(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        result = WorkflowResult(
            exit_code=2,
            error_codes=("PHOTOS_ACCESS_DENIED",),
            next_action="grant_photos_access",
        )
        app = create_app(
            scan_runner=lambda root, limit, model: result,
            console=Console(width=200, color_system=None),
        )

        output = CliRunner().invoke(app, ["scan", "--limit", "1"])

        self.assertEqual(output.exit_code, 2)
        self.assertIn("error:PHOTOS_ACCESS_DENIED", output.output)
        self.assertIn("hint:Concede acceso a Fotos", output.output)
        self.assertIn("next:grant_photos_access", output.output)
        self.assertNotIn("review_then_apply", output.output)

    def test_cli_reports_the_active_runtime_without_confusing_photokit_with_automation(self) -> None:
        from photos_indexer.cli import create_app
        from photos_indexer.runtime_identity import RuntimeIdentity
        from rich.console import Console

        result = WorkflowResult(
            exit_code=2,
            error_codes=("PHOTOS_ACCESS_DENIED",),
            next_action="grant_photos_access",
        )
        app = create_app(
            scan_runner=lambda root, limit, model: result,
            console=Console(width=200, color_system=None),
        )

        with patch("photos_indexer.cli.current_runtime_identity", return_value=RuntimeIdentity(
            executable_hint="project/.venv/bin/python3.11",
            python_version="3.11.13",
            architecture="arm64",
            packaged=False,
        )):
            output = CliRunner().invoke(app, ["scan", "--limit", "1"])

        self.assertEqual(output.exit_code, 2)
        self.assertIn("error:PHOTOS_ACCESS_DENIED", output.output)
        self.assertIn("runtime:Autoriza el proceso", output.output)
        self.assertIn("project/.venv/bin/python3.11", output.output)
        self.assertIn("PhotoKit", output.output)
        runtime_line = next(line for line in output.output.splitlines() if line.startswith("runtime:"))
        self.assertIn("Fotos para PhotoKit", runtime_line)
        self.assertNotIn("Automatización", runtime_line)
        self.assertNotIn("/Users/", output.output)

    def test_cli_handles_an_invalid_runtime_identity_without_leaking_details(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        result = WorkflowResult(
            exit_code=2,
            error_codes=("PHOTOS_ACCESS_DENIED",),
            next_action="grant_photos_access",
        )
        app = create_app(
            scan_runner=lambda root, limit, model: result,
            console=Console(width=200, color_system=None),
        )

        with patch("photos_indexer.cli.current_runtime_identity", side_effect=ValueError("invalid")):
            output = CliRunner().invoke(app, ["scan", "--limit", "1"])

        self.assertEqual(output.exit_code, 2)
        self.assertIn("error:PHOTOS_ACCESS_DENIED", output.output)
        self.assertNotIn("invalid", output.output)

    def test_cli_points_automation_denial_to_the_apple_events_identity(self) -> None:
        from photos_indexer.cli import create_app
        from photos_indexer.runtime_identity import RuntimeIdentity
        from rich.console import Console

        result = WorkflowResult(
            exit_code=2,
            error_codes=("PHOTOS_AUTOMATION_DENIED",),
            next_action="grant_photos_automation",
        )
        app = create_app(
            scan_runner=lambda root, limit, model: result,
            console=Console(width=200, color_system=None),
        )

        with patch("photos_indexer.cli.current_runtime_identity", return_value=RuntimeIdentity(
            executable_hint="project/.venv/bin/python3.11",
            python_version="3.11.15",
            architecture="arm64",
            packaged=False,
        )):
            output = CliRunner().invoke(app, ["scan", "--limit", "1"])

        runtime_line = next(line for line in output.output.splitlines() if line.startswith("runtime:"))
        self.assertIn("Automatización para controlar Fotos", runtime_line)
        self.assertIn("envía Apple Events", runtime_line)

    def test_cli_exposes_exact_commands_and_scan_defaults_through_injected_runner(self) -> None:
        from photos_indexer.cli import create_app

        calls: list[tuple[Path, int, str]] = []

        def scan_runner(root: Path, *, limit: int, model: str) -> WorkflowResult:
            calls.append((root, limit, model))
            return WorkflowResult(exit_code=0)

        app = create_app(
            scan_runner=scan_runner,
            apply_runner=lambda path: WorkflowResult(exit_code=0),
            status_runner=lambda path: WorkflowResult(exit_code=0),
            rollback_runner=lambda path: WorkflowResult(exit_code=0),
        )
        runner = CliRunner()

        help_result = runner.invoke(app, ["--help"])
        self.assertEqual(help_result.exit_code, 0, help_result.output)
        for command in ("scan", "apply", "status", "rollback"):
            self.assertIn(command, help_result.output)

        scan_result = runner.invoke(app, ["scan"])
        self.assertEqual(scan_result.exit_code, 0, scan_result.output)
        self.assertEqual(calls, [(Path("runs"), 20, "qwen3-vl:4b")])

        scan_help = runner.invoke(app, ["scan", "--help"])
        self.assertEqual(scan_help.exit_code, 0, scan_help.output)
        self.assertIn("--limit", scan_help.output)
        self.assertIn("--model", scan_help.output)
        self.assertIn("--random", scan_help.output)
        self.assertIn("--apple-maps", scan_help.output)

    def test_cli_forwards_explicit_scan_policies_and_capabilities(self) -> None:
        from photos_indexer.cli import create_app

        calls: list[dict[str, object]] = []

        def scan_runner(*args: object, **kwargs: object) -> WorkflowResult:
            del args
            calls.append(kwargs)
            return WorkflowResult(exit_code=0)

        app = create_app(scan_runner=scan_runner)
        runner = CliRunner()
        invocations = (
            ["scan", "--model-policy", "adaptive", "--fast-model", "qwen3-vl:4b"],
            ["scan", "--random"],
            ["scan", "--apple-maps"],
            ["scan", "--include-caption"],
            ["scan", "--random", "--apple-maps", "--include-caption"],
        )

        for arguments in invocations:
            result = runner.invoke(app, arguments)
            self.assertEqual(result.exit_code, 0, result.output)

        self.assertEqual(len(calls), len(invocations))
        self.assertEqual(calls[0]["model_policy"], "adaptive")
        self.assertEqual(calls[0]["detailed_model"], "qwen3-vl:4b")
        self.assertTrue(calls[1]["random_selection"])
        self.assertTrue(calls[2]["apple_maps"])
        self.assertTrue(calls[3]["include_caption"])
        self.assertTrue(calls[4]["random_selection"])
        self.assertTrue(calls[4]["apple_maps"])
        self.assertTrue(calls[4]["include_caption"])

    def test_cli_mutation_commands_forward_results_and_exit_codes(self) -> None:
        from photos_indexer.cli import create_app

        success = WorkflowResult(exit_code=0)
        failed = WorkflowResult(exit_code=1, error_codes=("WRITE_FAILED",), next_action="retry_failed_operation")
        calls: list[tuple[str, Path]] = []

        def apply_runner(path: Path) -> WorkflowResult:
            calls.append(("apply", path))
            return success

        def status_runner(path: Path) -> WorkflowResult:
            calls.append(("status", path))
            return success

        def rollback_runner(path: Path) -> WorkflowResult:
            calls.append(("rollback", path))
            return success

        app = create_app(
            apply_runner=apply_runner,
            status_runner=status_runner,
            rollback_runner=rollback_runner,
        )
        runner = CliRunner()
        manifest = Path("runs/example/manifest.json")
        for command in ("apply", "status", "rollback"):
            result = runner.invoke(app, [command, str(manifest)])
            self.assertEqual(result.exit_code, 0, result.output)
        self.assertEqual([name for name, _ in calls], ["apply", "status", "rollback"])

        failed_app = create_app(
            apply_runner=lambda path: failed,
            status_runner=lambda path: failed,
            rollback_runner=lambda path: failed,
        )
        for command in ("apply", "status", "rollback"):
            result = runner.invoke(failed_app, [command, str(manifest)])
            self.assertEqual(result.exit_code, 1, result.output)
            self.assertIn("error:WRITE_FAILED", result.output)

    def test_cli_main_invokes_typer_application(self) -> None:
        from unittest.mock import patch
        import photos_indexer.cli as cli_module

        with patch.object(cli_module, "app") as application:
            cli_module.main()
        application.assert_called_once_with()

    def test_cli_table_is_sanitized_and_propagates_partial_exit_code(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "runs" / "private-run-path"
            photo = make_photo(1)
            photo.title = "Título visible que debe truncarse SHOULD_NOT_PRINT"
            photo.scan_state = "noop"
            photo.proposed_keywords = []
            photo.errors = [{"stage": "analysis", "code": "LOW_CONFIDENCE"}]
            manifest_path = make_manifest(run_dir, [photo], scan_status="ready_with_errors")
            from photos_indexer.manifest import load_manifest

            result = WorkflowResult(
                exit_code=1,
                manifest_path=manifest_path,
                manifest=load_manifest(run_dir),
                error_codes=("SAFE_FATAL_CODE",),
                status_summary={
                    "scan_status": "ready_with_errors",
                    "processed": 1,
                    "keywords_proposed": 1,
                    "errors_by_code": {"LOW_CONFIDENCE": 1},
                },
                next_action="manual_review",
            )
            app = create_app(
                scan_runner=lambda root, limit, model: result,
                apply_runner=lambda path: result,
                status_runner=lambda path: result,
                rollback_runner=lambda path: result,
                console=Console(width=200, color_system=None),
            )

            output = CliRunner().invoke(app, ["status", str(manifest_path)])

            self.assertEqual(output.exit_code, 1)
            self.assertIn("00000000", output.output)
            self.assertIn("LOW_CONFIDENCE", output.output)
            self.assertIn("manual_review", output.output)
            self.assertIn("scan_status:ready_with_errors", output.output)
            self.assertIn("processed:1", output.output)
            self.assertIn("keywords_proposed:1", output.output)
            self.assertIn("errors_by_code: LOW_CONFIDENCE=1", output.output)
            self.assertNotIn("00000000-0000-4000-8000-000000000001", output.output)
            self.assertNotIn("SHOULD_NOT_PRINT", output.output)
            self.assertNotIn("private-run-path", output.output)
            self.assertNotIn("local-1", output.output)

    def test_cli_status_does_not_print_unallowlisted_status_fields(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        result = WorkflowResult(
            exit_code=1,
            error_codes=("ANALYSIS_FAILED",),
            status_summary={
                "processed": 1,
                "caption": "texto privado",
                "location": "coordenadas privadas",
            },
            next_action="fix_failed_scan",
        )
        app = create_app(
            status_runner=lambda path: result,
            console=Console(width=200, color_system=None),
        )

        output = CliRunner().invoke(app, ["status", "runs/example/manifest.json"])

        self.assertEqual(output.exit_code, 1)
        self.assertIn("processed:1", output.output)
        self.assertNotIn("caption", output.output)
        self.assertNotIn("texto privado", output.output)
        self.assertNotIn("location", output.output)
        self.assertNotIn("coordenadas privadas", output.output)

    def test_cli_does_not_print_unallowlisted_count_values(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        result = WorkflowResult(
            exit_code=1,
            error_codes=("ANALYSIS_FAILED",),
            counts={
                "scan": {"processed": 1, "caption": "texto privado"},
            },
            next_action="fix_failed_scan",
        )
        app = create_app(
            status_runner=lambda path: result,
            console=Console(width=200, color_system=None),
        )

        output = CliRunner().invoke(app, ["status", "runs/example/manifest.json"])

        self.assertEqual(output.exit_code, 2)
        self.assertIn("error:UNSAFE_WORKFLOW_RESULT", output.output)
        self.assertIn("next:fix_fatal_error", output.output)
        self.assertNotIn("scan: processed=1", output.output)
        self.assertNotIn("caption", output.output)
        self.assertNotIn("texto privado", output.output)

    def test_cli_does_not_print_raw_error_or_action_fields(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        result = WorkflowResult(
            exit_code=1,
            error_codes=("raw/private caption",),
            warning_codes=("raw warning",),
            safe_instruction="rm -rf /private",
            next_action="../private",
        )
        app = create_app(
            status_runner=lambda path: result,
            console=Console(width=200, color_system=None),
        )

        output = CliRunner().invoke(app, ["status", "runs/example/manifest.json"])

        self.assertEqual(output.exit_code, 2)
        self.assertIn("error:UNSAFE_WORKFLOW_RESULT", output.output)
        self.assertIn("next:fix_fatal_error", output.output)
        self.assertNotIn("raw/private caption", output.output)
        self.assertNotIn("raw warning", output.output)
        self.assertNotIn("rm -rf", output.output)
        self.assertNotIn("../private", output.output)

    def test_cli_review_can_approve_captions_separately_from_keywords(self) -> None:
        from photos_indexer.cli import create_app

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selections = root / "selections.json"
            captions = root / "captions.json"
            selections.write_text('{"00000000-0000-4000-8000-000000000001": ["playa"]}', encoding="utf-8")
            captions.write_text('{"00000000-0000-4000-8000-000000000001": true}', encoding="utf-8")
            calls: list[tuple[Path, dict[str, list[str]], dict[str, bool]]] = []

            def review_runner(
                manifest: Path,
                keyword_selections: dict[str, list[str]],
                caption_selections: dict[str, bool],
            ) -> Path:
                calls.append((manifest, keyword_selections, caption_selections))
                return root / "reviewed" / "manifest.json"

            app = create_app(
                review_runner=review_runner,
                apply_runner=lambda path: WorkflowResult(exit_code=0),
                status_runner=lambda path: WorkflowResult(exit_code=0),
                rollback_runner=lambda path: WorkflowResult(exit_code=0),
            )
            result = CliRunner().invoke(
                app,
                [
                    "review", str(root / "manifest.json"),
                    "--selections", str(selections),
                    "--caption-selections", str(captions),
                ],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            self.assertEqual(calls, [(
                root / "manifest.json",
                {"00000000-0000-4000-8000-000000000001": ["playa"]},
                {"00000000-0000-4000-8000-000000000001": True},
            )])
            self.assertIn("reviewed_manifest:", result.output)

    def test_cli_review_rejects_duplicate_selection_keys(self) -> None:
        from photos_indexer.cli import create_app

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selections = root / "selections.json"
            selections.write_text(
                '{"00000000-0000-4000-8000-000000000001": ["playa"], '
                '"00000000-0000-4000-8000-000000000001": []}',
                encoding="utf-8",
            )
            calls: list[object] = []

            def review_runner(*args: object, **kwargs: object) -> Path:
                calls.append((args, kwargs))
                return root / "reviewed" / "manifest.json"

            result = CliRunner().invoke(
                create_app(review_runner=review_runner),
                ["review", str(root / "manifest.json"), "--selections", str(selections)],
            )

        self.assertEqual(result.exit_code, 2)
        self.assertIn("error:REVIEW_INVALID", result.output)
        self.assertEqual(calls, [])

    def test_cli_review_rejects_an_oversized_selection_file_before_runner(self) -> None:
        from photos_indexer.cli import create_app
        from photos_indexer.manifest import MAX_MANIFEST_BYTES

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selections = root / "selections.json"
            selections.write_text(
                '{"00000000-0000-4000-8000-000000000001": ["playa"], "extra": "'
                + ("x" * MAX_MANIFEST_BYTES)
                + '"}',
                encoding="utf-8",
            )
            calls: list[object] = []

            def review_runner(*args: object, **kwargs: object) -> Path:
                calls.append((args, kwargs))
                return root / "reviewed" / "manifest.json"

            result = CliRunner().invoke(
                create_app(review_runner=review_runner),
                ["review", str(root / "manifest.json"), "--selections", str(selections)],
            )

        self.assertEqual(result.exit_code, 2)
        self.assertIn("error:REVIEW_INVALID", result.output)
        self.assertEqual(calls, [])

    def test_cli_review_uses_the_real_local_manifest_workflow(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            manifest = make_manifest(root / "source", [make_photo(1)])
            selections = root / "selections.json"
            selections.write_text(
                '{"00000000-0000-4000-8000-000000000001": ["playa"]}',
                encoding="utf-8",
            )

            result = CliRunner().invoke(
                create_app(console=Console(width=240, color_system=None)),
                ["review", str(manifest), "--selections", str(selections)],
            )

            self.assertEqual(result.exit_code, 0, result.output)
            reviewed = Path(next(line.split(":", 1)[1] for line in result.output.splitlines() if line.startswith("reviewed_manifest:")))
            self.assertTrue(reviewed.is_file())

    def test_cli_review_rejects_non_object_caption_selections(self) -> None:
        from photos_indexer.cli import create_app

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selections = root / "selections.json"
            captions = root / "captions.json"
            selections.write_text("{}", encoding="utf-8")
            captions.write_text("[]", encoding="utf-8")
            result = CliRunner().invoke(
                create_app(),
                ["review", str(root / "manifest.json"), "--selections", str(selections), "--caption-selections", str(captions)],
            )

        self.assertEqual(result.exit_code, 2)
        self.assertIn("error:REVIEW_INVALID", result.output)

    def test_module_entrypoint_delegates_to_cli_main(self) -> None:
        import runpy

        with patch("photos_indexer.cli.main") as main:
            runpy.run_module("photos_indexer.__main__", run_name="__main__")
        main.assert_called_once_with()

    def test_cli_review_never_prints_raw_exception_details(self) -> None:
        from photos_indexer.cli import create_app

        raw_details = "raw/private/path caption=hidden text"

        def failing_review(*args: object, **kwargs: object) -> Path:
            raise RuntimeError(raw_details)

        app = create_app(
            review_runner=failing_review,
            apply_runner=lambda path: WorkflowResult(exit_code=0),
            status_runner=lambda path: WorkflowResult(exit_code=0),
            rollback_runner=lambda path: WorkflowResult(exit_code=0),
        )

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selections = root / "selections.json"
            selections.write_text("{}", encoding="utf-8")
            result = CliRunner().invoke(
                app,
                ["review", str(root / "manifest.json"), "--selections", str(selections)],
            )

        self.assertEqual(result.exit_code, 2, result.output)
        self.assertIn("error:REVIEW_INVALID", result.output)
        self.assertNotIn(raw_details, result.output)
        self.assertNotIn("raw/private/path", result.output)
        self.assertNotIn("hidden text", result.output)

    def test_cli_explains_photoscript_compile_failure_without_raw_applescript(self) -> None:
        from photos_indexer.cli import create_app
        from rich.console import Console

        result = WorkflowResult(
            exit_code=2,
            error_codes=("PHOTOSCRIPT_UNAVAILABLE",),
            next_action="fix_fatal_error",
        )
        app = create_app(
            scan_runner=lambda root, limit, model: result,
            console=Console(width=200, color_system=None),
        )

        output = CliRunner().invoke(app, ["scan", "--limit", "1"])

        self.assertEqual(output.exit_code, 2)
        self.assertIn("error:PHOTOSCRIPT_UNAVAILABLE", output.output)
        self.assertIn("PhotoScript", output.output)
        self.assertIn("compatibilidad", output.output)
        self.assertIn("next:fix_fatal_error", output.output)
        self.assertNotIn("-2741", output.output)


if __name__ == "__main__":
    unittest.main()
