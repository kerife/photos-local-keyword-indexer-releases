"""Bounded diagnostics for the process identity used by macOS TCC.

TCC grants are attached to the process/app that sends the Photos Apple Event;
they are not interchangeable with a permission granted to another Python
installation.  This module deliberately exposes only the last few path
components needed to distinguish a development interpreter from the bundled
helper.  It never requests, changes, or probes TCC state.
"""

from __future__ import annotations

import platform
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path


_MAX_EXECUTABLE_HINT = 256


def _safe_executable_hint(value: str) -> str:
    sanitized = "".join(
        character if unicodedata.category(character) not in {"Cc", "Cf", "Cs"} else "�"
        for character in value
    )
    return sanitized[-_MAX_EXECUTABLE_HINT:]


@dataclass(frozen=True, slots=True)
class RuntimeIdentity:
    """Safe, user-facing identity details for the current Photos caller."""

    executable_hint: str
    python_version: str
    architecture: str
    packaged: bool

    @property
    def authorization_hint(self) -> str:
        """Backward-compatible default for the Apple Events surface."""
        return self.permission_hint("automation")

    def permission_hint(self, surface: str) -> str:
        """Explain which identity receives *surface*'s permission.

        PhotoKit and Apple Events are separate macOS privacy decisions.  The
        caller must name the failed surface so a PhotoKit denial never sends
        the user to the Automation pane by accident.
        """
        if surface not in {"photos", "automation"}:
            raise ValueError("permission surface is invalid")
        target = self.permission_target(surface)
        if surface == "photos":
            permission_name = "Fotos para PhotoKit"
            action = "solicita acceso a la fototeca"
        else:
            permission_name = "Automatización para controlar Fotos"
            action = "envía Apple Events a Fotos"
        if self.packaged:
            return (
                f"Autoriza Photos Local Keyword Indexer en {permission_name}; "
                f"la operación {action} mediante el helper incluido {target.rsplit('/', 1)[-1]} "
                f"(Python {self.python_version}, {self.architecture})."
            )
        return (
            f"Autoriza el proceso que ejecuta este comando en {permission_name}; "
            f"ese proceso {action}. Intérprete activo: {self.executable_hint} "
            f"(Python {self.python_version}, {self.architecture}). Si macOS muestra un host "
            "como Terminal, Codex o tu IDE, autoriza ese host para este CLI."
        )

    def permission_target(self, surface: str) -> str:
        """Return the exact local client label to authorize for *surface*."""
        if surface not in {"photos", "automation"}:
            raise ValueError("permission surface is invalid")
        if self.packaged:
            return f"Photos Local Keyword Indexer / {self.executable_hint}"
        return self.executable_hint


def current_runtime_identity(
    *,
    executable: str | None = None,
    python_version: str | None = None,
    architecture: str | None = None,
    packaged: bool | None = None,
) -> RuntimeIdentity:
    """Return a bounded diagnostic without attempting to change TCC.

    Optional arguments make the policy testable and keep the runtime lookup
    lazy.  The full user home or checkout path is intentionally omitted from
    ``executable_hint`` so this diagnostic is safe to include in CLI output.
    """
    raw_executable = sys.executable if executable is None else executable
    raw_version = platform.python_version() if python_version is None else python_version
    raw_architecture = platform.machine() if architecture is None else architecture
    raw_packaged = bool(getattr(sys, "frozen", False)) if packaged is None else packaged
    if type(raw_executable) is not str or not raw_executable:
        raise ValueError("runtime executable is unavailable")
    if type(raw_version) is not str or not raw_version:
        raise ValueError("runtime Python version is unavailable")
    if type(raw_architecture) is not str or not raw_architecture:
        raise ValueError("runtime architecture is unavailable")
    if type(raw_packaged) is not bool:
        raise ValueError("runtime packaged flag is invalid")

    path_parts = [part for part in Path(raw_executable).parts if part not in {"", "/"}]
    # A packaged helper is identified by its signed executable name.  For a
    # development interpreter retain just enough context to distinguish a
    # project venv from another Python installation, never the user's home.
    executable_hint = path_parts[-1] if raw_packaged else "/".join(path_parts[-4:])
    executable_hint = _safe_executable_hint(executable_hint)
    if not executable_hint:
        raise ValueError("runtime executable is unavailable")
    return RuntimeIdentity(
        executable_hint=executable_hint,
        python_version=raw_version,
        architecture=raw_architecture,
        packaged=raw_packaged,
    )
