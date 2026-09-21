from __future__ import annotations

from collections.abc import Callable, Mapping
import json
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .manifest import MAX_MANIFEST_BYTES, ScanManifest, decode_strict_json
from .ipc import CancellationToken, _default_preflight, _safe_preflight_details
from .runtime_identity import current_runtime_identity
from .service import review_manifest, support_snapshot
from .workflows import (
    APP_VERSION,
    DEFAULT_DETAILED_MODEL,
    DEFAULT_LIMIT,
    DEFAULT_MODEL,
    WorkflowResult,
    run_apply,
    run_rollback,
    run_scan,
    run_status,
    sanitized_rows,
)


_PERMISSION_HINTS = {
    "PHOTOS_ACCESS_DENIED": "Concede acceso a Fotos en Configuración del Sistema y vuelve a ejecutar. PhotoKit no tiene acceso a la fototeca; esto es independiente de Automatización.",
    "PHOTOS_AUTOMATION_DENIED": "Concede Automatización a Fotos en Configuración del Sistema y vuelve a ejecutar. PhotoScript no puede controlar Fotos; esto es independiente del acceso PhotoKit.",
}
_SETUP_HINTS = {
    "PHOTOSCRIPT_UNAVAILABLE": "PhotoScript no pudo cargar su puente AppleScript; comprueba la compatibilidad de Photos, PhotoScript y la versión de macOS, y vuelve a ejecutar un dry-run.",
    "OLLAMA_UNAVAILABLE": "Inicia Ollama y comprueba que escuche solo en 127.0.0.1:11434; después vuelve a ejecutar el preflight.",
    "OLLAMA_VERSION_OLD": "Actualiza Ollama manualmente y vuelve a ejecutar el preflight; no se descargará nada automáticamente.",
    "OLLAMA_NO_VISION": "Comprueba que el modelo local instalado tenga capacidad de visión y vuelve a ejecutar el preflight.",
    "OLLAMA_PREFLIGHT_FAILED": "Comprueba Ollama y sus modelos locales en 127.0.0.1:11434; después vuelve a ejecutar el preflight.",
    "MODEL_INVALID": "Usa un nombre de modelo local válido, sin proveedores cloud, y vuelve a ejecutar el preflight.",
    "SCAN_OPTIONS_INVALID": "Revisa las opciones de la ejecución y vuelve a ejecutar un dry-run nuevo.",
    "UNSAFE_WORKFLOW_RESULT": "Ejecuta doctor y repite el dry-run con el mismo intérprete; si persiste, comparte el diagnóstico sanitizado.",
}


def _read_bounded_json(path: Path) -> object:
    with path.open("rb") as handle:
        payload = handle.read(MAX_MANIFEST_BYTES + 1)
    if len(payload) > MAX_MANIFEST_BYTES:
        raise ValueError("JSON input exceeds the safety limit")
    return decode_strict_json(payload.decode("utf-8"))


def _render_manifest(console: Console, manifest: ScanManifest) -> None:
    table = Table(title="Resumen local")
    for heading in ("UUID", "Título", "Fecha", "Existentes", "Propuestas", "Caption", "Conf.", "Estado", "Códigos"):
        table.add_column(heading)
    for row in sanitized_rows(manifest):
        table.add_row(
            row["uuid"], row["title"], row["date"], row["existing"], row["proposed"], row["caption_status"],
            row["confidence"], row["status"], row["error_codes"],
        )
    console.print(table)


def _render_scan_scope(console: Console, manifest: ScanManifest) -> None:
    """Make an empty or partial scan explicit without exposing photo data."""
    selection = manifest.selection
    requested = selection.get("requested")
    eligible = selection.get("eligible")
    screenshots = selection.get("screenshots_excluded")
    photos = manifest.photos
    if (
        type(requested) is not int or not 0 <= requested <= 500
        or type(eligible) is not int or not 0 <= eligible <= 500
        or type(screenshots) is not int or not 0 <= screenshots <= 500
        or not isinstance(photos, list) or len(photos) > 500
        or (eligible >= requested and photos)
    ):
        return
    console.print(
        "scope: "
        f"requested={requested}, eligible={eligible}, "
        f"processed={len(photos)}, screenshots_excluded={screenshots}"
    )


def _emit_result(
    console: Console,
    result: WorkflowResult,
    *,
    show_manifest_path: bool = False,
    show_scan_scope: bool = False,
) -> int:
    if result.manifest is not None:
        _render_manifest(console, result.manifest)
        if show_scan_scope and isinstance(result.manifest, ScanManifest):
            _render_scan_scope(console, result.manifest)
    if show_manifest_path and result.manifest_path is not None:
        # Give CLI users the exact durable artifact to review next.  The path
        # is metadata only; image exports and model responses remain private.
        console.print(f"manifest:{result.manifest_path}")
    safe_snapshot = support_snapshot(result)
    safe_counts = safe_snapshot.get("counts", {})
    if isinstance(safe_counts, Mapping):
        for stage in ("scan", "apply", "rollback"):
            values = safe_counts.get(stage, {})
            if isinstance(values, Mapping) and values:
                console.print(f"{stage}: " + ", ".join(f"{name}={count}" for name, count in sorted(values.items())))
    # Reuse the bounded support projection so direct CLI output cannot expose
    # captions, coordinates, or other fields injected into status_summary.
    safe_status = safe_snapshot.get("status", {})
    if isinstance(safe_status, Mapping):
        for name, value in safe_status.items():
            if name == "errors_by_code":
                errors = value if isinstance(value, dict) else {}
                console.print("errors_by_code: " + ", ".join(f"{code}={count}" for code, count in sorted(errors.items())))
            else:
                console.print(f"{name}:{value}")
    for code in safe_snapshot.get("warning_codes", []):
        console.print(f"warning:{code}")
    for code in safe_snapshot.get("error_codes", []):
        console.print(f"error:{code}")
        if code in _PERMISSION_HINTS:
            console.print(f"hint:{_PERMISSION_HINTS[code]}")
            try:
                surface = "photos" if code == "PHOTOS_ACCESS_DENIED" else "automation"
                runtime_hint = current_runtime_identity().permission_hint(surface)
            except ValueError:
                runtime_hint = None
            if runtime_hint is not None:
                console.print(f"runtime:{runtime_hint}")
        elif code in _SETUP_HINTS:
            console.print(f"hint:{_SETUP_HINTS[code]}")
        if code == "UNSAFE_WORKFLOW_RESULT":
            # A contradictory runner result is most often caused by invoking
            # an older installed copy or a different interpreter.  Expose
            # only the same bounded identity used by ``doctor`` so support
            # can distinguish that case without leaking a home path.
            console.print(f"app_version:{APP_VERSION}")
            try:
                runtime_identity = current_runtime_identity()
            except (OSError, TypeError, ValueError):
                runtime_identity = None
            if runtime_identity is not None:
                console.print(
                    "runtime:"
                    f"{runtime_identity.executable_hint} version={runtime_identity.python_version} "
                    f"architecture={runtime_identity.architecture} packaged={str(runtime_identity.packaged).lower()}"
                )
    safe_instruction = safe_snapshot.get("safe_instruction")
    if isinstance(safe_instruction, str):
        console.print(safe_instruction)
    next_action = safe_snapshot.get("next_action", "none")
    console.print(f"next:{next_action}")
    exit_code = safe_snapshot.get("exit_code")
    return exit_code if type(exit_code) is int and exit_code in {0, 1, 2} else 2


def create_app(
    *,
    scan_runner: Callable[..., WorkflowResult] = run_scan,
    apply_runner: Callable[[Path], WorkflowResult] = run_apply,
    review_runner: Callable[..., Path] = review_manifest,
    status_runner: Callable[[Path], WorkflowResult] = run_status,
    rollback_runner: Callable[[Path], WorkflowResult] = run_rollback,
    preflight_runner: Callable[[dict[str, object], CancellationToken], tuple[int, dict[str, object]]] = _default_preflight,
    console: Console | None = None,
) -> typer.Typer:
    console = console or Console()
    application = typer.Typer(add_completion=False, no_args_is_help=True)

    @application.command("scan")
    def scan_command(
        limit: int = typer.Option(DEFAULT_LIMIT, "--limit", min=1, max=500),
        model: str | None = typer.Option(None, "--model"),
        model_policy: str = typer.Option("single", "--model-policy", help="single o adaptive según GPS."),
        fast_model: str = typer.Option(DEFAULT_MODEL, "--fast-model"),
        detailed_model: str = typer.Option(DEFAULT_DETAILED_MODEL, "--detailed-model"),
        random_selection: bool = typer.Option(False, "--random", help="Elegir uniformemente de toda la fototeca."),
        apple_maps: bool = typer.Option(False, "--apple-maps", help="Enviar coordenadas con GPS a Apple Maps para contexto de lugares."),
        include_caption: bool = typer.Option(False, "--include-caption", help="Proponer captions breves y escribirlos solo tras revisión."),
    ) -> None:
        if model_policy not in {"single", "adaptive"}:
            raise typer.BadParameter("--model-policy debe ser single o adaptive")
        if model_policy == "adaptive" and model is not None:
            raise typer.BadParameter("--model no se puede combinar con --model-policy adaptive")
        if model is not None and not model.strip():
            raise typer.BadParameter("--model no puede estar vacío")
        effective_model = model or DEFAULT_MODEL
        advanced_model_policy = (
            model_policy != "single" or fast_model != DEFAULT_MODEL or detailed_model != DEFAULT_DETAILED_MODEL
        )
        if advanced_model_policy:
            result = scan_runner(
                Path("runs"), limit=limit,
                model=None if model_policy == "adaptive" else effective_model,
                random_selection=random_selection,
                apple_maps=apple_maps,
                model_policy=model_policy,
                fast_model=fast_model,
                detailed_model=detailed_model,
                include_caption=include_caption,
            )
        elif random_selection and apple_maps:
            result = scan_runner(Path("runs"), limit=limit, model=effective_model, random_selection=True, apple_maps=True, **({"include_caption": True} if include_caption else {}))
        elif random_selection:
            result = scan_runner(Path("runs"), limit=limit, model=effective_model, random_selection=True, **({"include_caption": True} if include_caption else {}))
        elif apple_maps:
            result = scan_runner(Path("runs"), limit=limit, model=effective_model, apple_maps=True, **({"include_caption": True} if include_caption else {}))
        elif include_caption:
            result = scan_runner(Path("runs"), limit=limit, model=effective_model, include_caption=True)
        else:
            result = scan_runner(Path("runs"), limit=limit, model=effective_model)
        exit_code = _emit_result(console, result, show_manifest_path=True, show_scan_scope=True)
        if exit_code:
            raise typer.Exit(exit_code)

    @application.command("doctor")
    def doctor_command(
        model: str | None = typer.Option(None, "--model", help="Modelo local que se comprobará en política single."),
        model_policy: str = typer.Option("single", "--model-policy", help="single o adaptive."),
        fast_model: str = typer.Option(DEFAULT_MODEL, "--fast-model"),
        detailed_model: str = typer.Option(DEFAULT_DETAILED_MODEL, "--detailed-model"),
        json_output: bool = typer.Option(False, "--json", help="Emitir únicamente un diagnóstico JSON acotado."),
    ) -> None:
        """Comprobar dependencias locales sin abrir Fotos ni crear un run."""
        if model_policy not in {"single", "adaptive"}:
            raise typer.BadParameter("--model-policy debe ser single o adaptive")
        if model_policy == "adaptive" and model is not None:
            raise typer.BadParameter("--model no se puede combinar con --model-policy adaptive")
        models = [model or DEFAULT_MODEL] if model_policy == "single" else [fast_model, detailed_model]
        if any(type(value) is not str or not value.strip() for value in models):
            raise typer.BadParameter("--model no puede estar vacío")
        models = list(dict.fromkeys(value.strip() for value in models))
        try:
            raw_exit_code, raw_details = preflight_runner(
                {"models": models},
                CancellationToken(),
            )
            details = _safe_preflight_details(raw_details)
        except Exception:
            raw_exit_code, details = 2, {
                "error_codes": ["UNSAFE_PREFLIGHT_RESULT"],
                "next_action": "fix_fatal_error",
            }
        exit_code = raw_exit_code if type(raw_exit_code) is int and raw_exit_code in {0, 1, 2} else 2
        error_values = details.get("error_codes", [])
        next_action = details.get("next_action", "none")
        contradictory = (
            (exit_code == 0 and (error_values or details.get("safe_instruction") is not None or next_action != "none"))
            or (exit_code != 0 and not error_values)
        )
        if contradictory:
            exit_code = 2
            details = {"error_codes": ["UNSAFE_PREFLIGHT_RESULT"], "next_action": "fix_fatal_error"}
        runtime_identity = current_runtime_identity()
        if json_output:
            report = {
                "format_version": 1,
                "app_version": APP_VERSION,
                "exit_code": exit_code,
                "runtime": {
                    "executable": runtime_identity.executable_hint,
                    "python_version": runtime_identity.python_version,
                    "architecture": runtime_identity.architecture,
                    "packaged": runtime_identity.packaged,
                },
                "models": details.get("models", {}) if isinstance(details.get("models", {}), Mapping) else {},
                "error_codes": details.get("error_codes", []),
                "next_action": details.get("next_action", "none"),
                "tcc_not_checked": True,
                "helper_not_checked": True,
            }
            console.file.write(json.dumps(report, ensure_ascii=False, sort_keys=True) + "\n")
            console.file.flush()
            if exit_code:
                raise typer.Exit(exit_code)
            return
        console.print("doctor:ready" if exit_code == 0 else "doctor:blocked")
        console.print(f"app_version:{APP_VERSION}")
        console.print(
            "runtime:"
            f"{runtime_identity.executable_hint} version={runtime_identity.python_version} "
            f"architecture={runtime_identity.architecture} packaged={str(runtime_identity.packaged).lower()}"
        )
        console.print("scope:ollama_photoscript_local")
        models = details.get("models", {})
        if isinstance(models, Mapping):
            for name, version in sorted(models.items()):
                console.print(f"model:{name}={version}")
        for code in details.get("error_codes", []):
            console.print(f"error:{code}")
            if code in _SETUP_HINTS:
                console.print(f"hint:{_SETUP_HINTS[code]}")
        instruction = details.get("safe_instruction")
        if isinstance(instruction, str):
            console.print(instruction)
        console.print("tcc:not_checked")
        console.print("helper:not_checked")
        console.print("note:El dry-run comprobará TCC y el helper; doctor no abre Fotos.")
        console.print(f"next:{details.get('next_action', 'none')}")
        if exit_code:
            raise typer.Exit(exit_code)

    @application.command("apply")
    def apply_command(manifest: Path) -> None:
        result = apply_runner(manifest)
        exit_code = _emit_result(console, result)
        if exit_code:
            raise typer.Exit(exit_code)

    @application.command("review")
    def review_command(
        manifest: Path,
        selections: Path = typer.Option(..., "--selections", help="JSON local con UUID completo y keywords aprobadas."),
        caption_selections: Path | None = typer.Option(
            None,
            "--caption-selections",
            help="JSON local con UUID completo y true/false para aprobar captions.",
        ),
    ) -> None:
        try:
            payload = _read_bounded_json(selections)
            if not isinstance(payload, Mapping):
                raise ValueError("selections debe ser un objeto JSON")
            caption_payload: Mapping[str, bool] = {}
            if caption_selections is not None:
                decoded_captions = _read_bounded_json(caption_selections)
                if not isinstance(decoded_captions, Mapping):
                    raise ValueError("caption_selections debe ser un objeto JSON")
                caption_payload = decoded_captions
            reviewed_path = review_runner(manifest, payload, caption_payload)
        except Exception as error:
            console.print("error:REVIEW_INVALID")
            raise typer.Exit(2) from error
        console.print(f"reviewed_manifest:{reviewed_path}")
        console.print("next:apply_reviewed_manifest")

    @application.command("status")
    def status_command(manifest: Path) -> None:
        result = status_runner(manifest)
        exit_code = _emit_result(console, result)
        if exit_code:
            raise typer.Exit(exit_code)

    @application.command("support")
    def support_command(manifest: Path) -> None:
        """Emit a shareable, metadata-only support report for a run."""
        result = status_runner(manifest)
        snapshot = support_snapshot(result)
        console.print(json.dumps(snapshot, ensure_ascii=False, sort_keys=True))
        snapshot_exit_code = snapshot["exit_code"]
        if type(snapshot_exit_code) is int and snapshot_exit_code:
            raise typer.Exit(snapshot_exit_code)

    @application.command("rollback")
    def rollback_command(manifest: Path) -> None:
        result = rollback_runner(manifest)
        exit_code = _emit_result(console, result)
        if exit_code:
            raise typer.Exit(exit_code)

    return application


app = create_app()


def main() -> None:
    app()
