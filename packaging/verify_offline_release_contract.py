#!/usr/bin/env python3
"""Validate the source-side distribution contract without network or credentials."""

from __future__ import annotations

import argparse
import ast
import json
import plistlib
import re
import stat
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # macOS system Python can still be 3.9.
    tomllib = None


ROOT = Path(__file__).resolve().parents[1]
RELEASE_GATES = [
    {"id": "developer_id", "state": "not_checked", "code": "DEVELOPER_ID_RUNTIME_REQUIRED"},
    {"id": "notarization", "state": "not_checked", "code": "NOTARIZATION_RUNTIME_REQUIRED"},
    {"id": "tcc", "state": "not_checked", "code": "TCC_RUNTIME_REQUIRED"},
]


def check(checks: list[dict[str, str]], code: str, passed: bool) -> None:
    checks.append({"state": "PASS" if passed else "FAIL", "code": code})


def read_plist(path: Path) -> dict[str, object] | None:
    try:
        with path.open("rb") as stream:
            value = plistlib.load(stream)
    except (OSError, plistlib.InvalidFileException, ValueError):
        return None
    return value if isinstance(value, dict) else None


def has_active_runtime_provenance_check(source: str) -> bool:
    """Require the pinned-version comparison to be executable Python code."""
    blocks = re.finditer(r'\$python_bin"\s+-c\s+\'(?P<code>.*?)\'\s+>/dev/null', source, re.DOTALL)
    for block in blocks:
        code = block.group("code")
        if "expected_versions" not in code or "importlib.metadata" not in code:
            continue
        try:
            tree = ast.parse(code)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.For) or not isinstance(node.target, ast.Tuple):
                continue
            names = [element.id for element in node.target.elts if isinstance(element, ast.Name)]
            if names != ["package", "expected"]:
                continue
            for child in node.body:
                if not isinstance(child, ast.If) or not isinstance(child.test, ast.Compare):
                    continue
                comparison = child.test
                if len(comparison.ops) != 1 or not isinstance(comparison.ops[0], ast.NotEq):
                    continue
                if len(comparison.comparators) != 1:
                    continue
                left = comparison.left
                right = comparison.comparators[0]
                if (
                    isinstance(left, ast.Call)
                    and isinstance(left.func, ast.Name)
                    and left.func.id == "version"
                    and len(left.args) == 1
                    and isinstance(left.args[0], ast.Name)
                    and left.args[0].id == "package"
                    and isinstance(right, ast.Name)
                    and right.id == "expected"
                ):
                    return True
    return False


def validate(root: Path) -> tuple[list[dict[str, str]], bool]:
    checks: list[dict[str, str]] = []

    check(checks, "OFFLINE_PYTHON_RUNTIME", tomllib is not None)
    if tomllib is None:
        return checks, False

    pyproject_path = root / "pyproject.toml"
    try:
        with pyproject_path.open("rb") as stream:
            pyproject = tomllib.load(stream)
    except (OSError, tomllib.TOMLDecodeError):
        pyproject = None
    check(checks, "PYPROJECT_VALID", pyproject is not None)
    project_value = pyproject.get("project", {}) if isinstance(pyproject, dict) else {}
    project = project_value if isinstance(project_value, dict) else {}
    if pyproject is not None:
        check(checks, "PYTHON_CONSTRAINT", project.get("requires-python") == ">=3.11,<4")
        raw_dependencies = project.get("dependencies", [])
        dependencies = (
            set(raw_dependencies)
            if isinstance(raw_dependencies, list) and all(isinstance(item, str) for item in raw_dependencies)
            else set()
        )
        expected_dependencies = {
            "photoscript==0.5.3",
            "pyobjc-framework-Photos==12.2.2",
            "pyobjc-framework-MapKit==12.2.2",
            "pyobjc-framework-CoreLocation==12.2.2",
            "pyobjc-framework-Quartz==12.2.2",
        }
        check(checks, "PYTHON_RUNTIME_DEPENDENCIES_PINNED", expected_dependencies <= dependencies)

    app_info = read_plist(root / "packaging" / "AppInfo.plist")
    check(checks, "APP_INFO_VALID", app_info is not None)
    if app_info is not None:
        check(checks, "APP_BUNDLE_ID", app_info.get("CFBundleIdentifier") == "com.photoslocalkeywordindexer.app")
        check(checks, "APP_MINIMUM_MACOS", app_info.get("LSMinimumSystemVersion") == "14.0")
        check(checks, "APP_EXECUTABLE", app_info.get("CFBundleExecutable") == "PhotosLocalKeywordIndexer")
        check(checks, "APP_ICON_METADATA", app_info.get("CFBundleIconFile") == "AppIcon.icns")
    usage_descriptions = (
        "NSAppleEventsUsageDescription",
        "NSPhotoLibraryUsageDescription",
    )
    check(
        checks,
        "APP_PRIVACY_USAGE_DESCRIPTIONS",
        isinstance(app_info, dict)
        and all(isinstance(app_info.get(key), str) and app_info[key].strip() for key in usage_descriptions),
    )
    icon_builder_source = (
        (root / "packaging" / "build_app_icon.sh").read_text(encoding="utf-8")
        if (root / "packaging" / "build_app_icon.sh").is_file()
        else ""
    )
    check(
        checks,
        "APP_ICON_ATOMIC_OUTPUT",
        'icon_output_tmp="$icon_tmp_root/AppIcon.icns"' in icon_builder_source
        and '--out "$icon_output_tmp"' in icon_builder_source
        and 'mv "$icon_output_tmp" "$output_path"' in icon_builder_source
        and 'rm -f -- "$icon_output_tmp"' in icon_builder_source,
    )
    icon_path_guard = 'validate_build_path "$output_path" "$project_root/build" "icon output"'
    check(
        checks,
        "APP_ICON_PATH_GUARD",
        'source "$project_root/packaging/build_path_guard.zsh"' in icon_builder_source
        and icon_path_guard in icon_builder_source
        and icon_builder_source.index(icon_path_guard)
        < icon_builder_source.index('mktemp -d "${output_path:h}/.photos-indexer-icon.XXXXXX"'),
    )
    helper_version_source = (root / "src" / "photos_indexer" / "workflows.py").read_text(encoding="utf-8") if (root / "src" / "photos_indexer" / "workflows.py").is_file() else ""
    helper_version_match = re.search(r'^APP_VERSION\s*=\s*["\']([^"\']+)["\']', helper_version_source, re.MULTILINE)
    project_version = project.get("version")
    check(
        checks,
        "APP_HELPER_VERSION_TRACEABLE",
        isinstance(project_version, str)
        and isinstance(app_info, dict)
        and app_info.get("CFBundleShortVersionString") == project_version
        and helper_version_match is not None
        and helper_version_match.group(1) == project_version,
    )
    swift_build_source = (
        (root / "packaging" / "build_swift_app.sh").read_text(encoding="utf-8")
        if (root / "packaging" / "build_swift_app.sh").is_file()
        else ""
    )
    check(
        checks,
        "RELEASE_HELPER_VERIFICATION_REQUIRED",
        'if [[ "$release_build" == "1" && "$verify_embedded_helper" != "1" ]]; then' in swift_build_source
        and "build_swift_app:FAIL:embedded_helper_verification_required" in swift_build_source,
    )
    helper_copy_call = 'ditto "$helper" "$app_bundle/Contents/Helpers/PhotosIndexerWorker.app"'
    helper_runtime_call = '"$project_root/packaging/verify_embedded_helper.sh" "$app_bundle"'
    check(
        checks,
        "APP_POSTCOPY_HELPER_RUNTIME_GATE",
        helper_copy_call in swift_build_source
        and helper_runtime_call in swift_build_source
        and "build_succeeded=1" in swift_build_source
        and swift_build_source.index(helper_copy_call) < swift_build_source.index(helper_runtime_call)
        and swift_build_source.index(helper_runtime_call) < swift_build_source.index("build_succeeded=1"),
    )
    check(
        checks,
        "APP_NESTED_HELPER_COPY",
        'helper="$project_root/build/python-helper/dist/PhotosIndexerWorker.app"' in swift_build_source
        and helper_copy_call in swift_build_source
        and 'Contents/Helpers/PhotosIndexerWorker/PhotosIndexerWorker' not in swift_build_source,
    )
    check(
        checks,
        "APP_NESTED_HELPER_PATHS",
        'helper_binary="$helper/Contents/MacOS/PhotosIndexerWorker"' in swift_build_source
        and 'helper_source_marker="$helper_resources/.photos-indexer-source-fingerprint"'
        in swift_build_source
        and 'bundled_helper_root="$app_bundle/Contents/Helpers/PhotosIndexerWorker.app"'
        in swift_build_source
        and 'bundled_helper="$bundled_helper_root/Contents/MacOS/PhotosIndexerWorker"'
        in swift_build_source,
    )
    helper_sign = (
        'development_sign_bundle_tree "$bundled_helper_root" '
        '"$project_root/packaging/helper-entitlements.plist"'
    )
    sparkle_sign = 'development_sign_bundle_tree "$embedded_sparkle"'
    sign_tool = "code" + "sign"
    main_sign = (
        f'{sign_tool} --force --sign "$development_sign_identity" '
        '"$app_bundle/Contents/MacOS/PhotosLocalKeywordIndexer"'
    )
    outer_sign = (
        f'{sign_tool} --force --sign "$development_sign_identity" --entitlements '
        '"$project_root/packaging/entitlements.plist" "$app_bundle"'
    )
    local_identity_query = "security find-identity -v -p " + "code" + "signing"
    local_signing_contract = (
        'local_code_sign_identity="${LOCAL_CODE_SIGN_IDENTITY:-}"' in swift_build_source
        and 'development_sign_identity="${local_code_sign_identity:--}"' in swift_build_source
        and local_identity_query in swift_build_source
        and "local_code_sign_identity_not_allowed_for_release" in swift_build_source
        and "local_code_sign_identity_unavailable" in swift_build_source
    )
    check(
        checks,
        "APP_DEVELOPMENT_ADHOC_SIGNING_ORDER",
        'if [[ "$release_build" == "0" ]]; then' in swift_build_source
        and 'find "$bundle_root" -type f -print0' in swift_build_source
        and "payload_kind" in swift_build_source
        and 'find "$bundle_root" -depth -type d' in swift_build_source
        and local_signing_contract
        and all(marker in swift_build_source for marker in (helper_sign, sparkle_sign, main_sign, outer_sign))
        and swift_build_source.index(helper_sign)
        < swift_build_source.index(sparkle_sign)
        < swift_build_source.index(main_sign)
        < swift_build_source.index(outer_sign)
        and f"{sign_tool} --force --deep" not in swift_build_source
        and f"{sign_tool} --deep --force" not in swift_build_source
        and f'{sign_tool} --verify --deep --strict --verbose=2 "$app_bundle"'
        in swift_build_source,
    )
    check(
        checks,
        "APP_SPARKLE_PLACEHOLDER_GUARD",
        'placeholder_domains = ("example.invalid", "example.org", "example.com", "example.net")'
        in swift_build_source,
    )
    check(
        checks,
        "APP_SPARKLE_PUBLIC_KEY_PREFLIGHT",
        "base64.b64decode" in swift_build_source
        and "len(decoded_key) != 32" in swift_build_source
        and "build_swift_app:FAIL:sparkle_update_configuration_invalid" in swift_build_source
        and swift_build_source.index("base64.b64decode") < swift_build_source.index('"$swift_bin" build --package-path'),
    )
    update_service_source = (
        (root / "app" / "PhotosLocalKeywordIndexer" / "Services" / "UpdateService.swift").read_text(encoding="utf-8")
        if (root / "app" / "PhotosLocalKeywordIndexer" / "Services" / "UpdateService.swift").is_file()
        else ""
    )
    app_model_source = (
        (root / "app" / "PhotosLocalKeywordIndexer" / "AppModel.swift").read_text(encoding="utf-8")
        if (root / "app" / "PhotosLocalKeywordIndexer" / "AppModel.swift").is_file()
        else ""
    )
    app_entry_source = (
        (root / "app" / "PhotosLocalKeywordIndexer" / "PhotosLocalKeywordIndexerApp.swift").read_text(
            encoding="utf-8"
        )
        if (root / "app" / "PhotosLocalKeywordIndexer" / "PhotosLocalKeywordIndexerApp.swift").is_file()
        else ""
    )
    app_settings_store_source = (
        (root / "app" / "PhotosLocalKeywordIndexer" / "Services" / "AppSettingsStore.swift").read_text(
            encoding="utf-8"
        )
        if (root / "app" / "PhotosLocalKeywordIndexer" / "Services" / "AppSettingsStore.swift").is_file()
        else ""
    )
    safe_feature_defaults = (
        '@Published var modelPolicy = "adaptive"',
        '@Published var singleModel = "qwen3-vl:4b"',
        '@Published var fastModel = "qwen3-vl:4b"',
        '@Published var detailedModel = "qwen3-vl:4b"',
        "@Published var appleMaps = false",
        "@Published var includeCaption = false",
    )
    check(
        checks,
        "APP_SAFE_FEATURE_DEFAULTS",
        all(default in app_model_source for default in safe_feature_defaults)
        and all(
            setting not in swift_build_source
            for setting in ("modelPolicy", "singleModel", "fastModel", "detailedModel", "appleMaps", "includeCaption")
        ),
    )
    private_settings_markers = (
        ".applicationSupportDirectory",
        '.appendingPathComponent("settings.json")',
        "destinationOfSymbolicLink(atPath: fileURL.path)",
        ".posixPermissions: 0o700",
        ".posixPermissions: 0o600",
        "data.write(to: temporaryURL, options: .atomic)",
        "fileManager.replaceItemAt(",
        "fileManager.removeItem(at: temporaryURL)",
    )
    check(
        checks,
        "APP_SETTINGS_PRIVATE_PERSISTENCE",
        all(marker in app_settings_store_source for marker in private_settings_markers)
        and "AppSettingsStore.loadDefault()" in app_entry_source
        and "persistedSettings:" in app_entry_source
        and re.search(
            r"private func persistCurrentSettings\(\)\s*\{\s*"
            r"guard let settingsStore else \{ return \}\s*"
            r"try\? settingsStore\.save\(currentSettings\)\s*\}",
            app_model_source,
        ) is not None
        and re.search(
            r"private var currentSettings:\s*AppSettings\s*\{\s*AppSettings\(",
            app_model_source,
        ) is not None,
    )
    runtime_host_binding = "let host = feedURL.host"
    runtime_host_nonempty = "!host.isEmpty"
    runtime_placeholder_guard = "!Self.isPlaceholderHost(host)"
    runtime_host_markers = (
        runtime_host_binding,
        runtime_host_nonempty,
        runtime_placeholder_guard,
    )
    check(
        checks,
        "APP_RUNTIME_SPARKLE_PLACEHOLDER_GUARD",
        '"example.invalid", "example.org", "example.com", "example.net"' in update_service_source
        and all(marker in update_service_source for marker in runtime_host_markers)
        and update_service_source.index(runtime_host_binding)
        < update_service_source.index(runtime_host_nonempty)
        < update_service_source.index(runtime_placeholder_guard),
    )
    helper_source_gate = 'if [[ -L "$helper" || ! -d "$helper" \\'
    helper_source_copy = 'ditto "$helper" "$app_bundle/Contents/Helpers/PhotosIndexerWorker.app"'
    helper_source_path_guard = 'validate_build_path "$helper" "$project_root/build" "helper source"'
    check(
        checks,
        "APP_SOURCE_HELPER_PATH_GUARD",
        helper_source_path_guard in swift_build_source
        and helper_source_copy in swift_build_source
        and swift_build_source.index(helper_source_path_guard) < swift_build_source.index(helper_source_copy)
        and helper_source_gate in swift_build_source
        and "build_swift_app:FAIL:helper_path_invalid" in swift_build_source
        and swift_build_source.index(helper_source_gate) < swift_build_source.index(helper_source_copy),
    )
    embedded_helper_source = (
        (root / "packaging" / "verify_embedded_helper.sh").read_text(encoding="utf-8")
        if (root / "packaging" / "verify_embedded_helper.sh").is_file()
        else ""
    )
    worker_entry_source = (
        (root / "packaging" / "worker_entry.py").read_text(encoding="utf-8")
        if (root / "packaging" / "worker_entry.py").is_file()
        else ""
    )
    ipc_source = (
        (root / "src" / "photos_indexer" / "ipc.py").read_text(encoding="utf-8")
        if (root / "src" / "photos_indexer" / "ipc.py").is_file()
        else ""
    )
    swift_ipc_source = (
        (root / "app" / "PhotosLocalKeywordIndexer" / "Models" / "IPCModels.swift").read_text(encoding="utf-8")
        if (root / "app" / "PhotosLocalKeywordIndexer" / "Models" / "IPCModels.swift").is_file()
        else ""
    )
    worker_process_source = (
        (root / "app" / "PhotosLocalKeywordIndexer" / "Services" / "WorkerProcess.swift").read_text(encoding="utf-8")
        if (root / "app" / "PhotosLocalKeywordIndexer" / "Services" / "WorkerProcess.swift").is_file()
        else ""
    )
    preview_models_source = (
        (root / "app" / "PhotosLocalKeywordIndexer" / "Models" / "PreviewModels.swift").read_text(encoding="utf-8")
        if (root / "app" / "PhotosLocalKeywordIndexer" / "Models" / "PreviewModels.swift").is_file()
        else ""
    )
    helper_spec_source = (
        (root / "packaging" / "PhotosIndexerWorker.spec").read_text(encoding="utf-8")
        if (root / "packaging" / "PhotosIndexerWorker.spec").is_file()
        else ""
    )
    protocol_commands = ("preflight", "scan", "review", "apply", "rollback", "cancel")
    protocol_events = ("started", "photo_progress", "completed", "error")
    check(
        checks,
        "APP_HELPER_IPC_PROTOCOL_CONTRACT",
        'entry_point = project_root / "packaging" / "worker_entry.py"' in helper_spec_source
        and '"protocol": "jsonl"' in worker_entry_source
        and "_COMMANDS = frozenset({\"preflight\", \"scan\", \"review\", \"apply\", \"rollback\", \"cancel\"})" in ipc_source
        and all(f"case {command}" in swift_ipc_source for command in protocol_commands)
        and all(
            (f'case {event}' in swift_ipc_source if event != "photo_progress" else 'case photoProgress = "photo_progress"' in swift_ipc_source)
            for event in protocol_events
        )
        and all(f'"{event}"' in ipc_source for event in protocol_events),
    )
    check(
        checks,
        "APP_HELPER_IPC_ERROR_REDACTION",
        "stderr is deliberately not retained or displayed" in worker_process_source
        and "self?.stderrReceived = true" in worker_process_source
        and "_safe_service_event" in ipc_source
        and '"UNSAFE_WORKFLOW_RESULT"' in ipc_source,
    )
    check(
        checks,
        "APP_IMPORT_PARTIAL_COPY_CLEANUP",
        "var createdImportDirectories: [URL] = []" in preview_models_source
        and "createdImportDirectories.append(destination)" in preview_models_source
        and "createdImportDirectories.append(sourceDestination)" in preview_models_source
        and "for directory in createdImportDirectories.reversed()" in preview_models_source
        and "fileManager.removeItem(at: directory)" in preview_models_source,
    )
    embedded_helper_core_paths = (
        '"$app_path/Contents"',
        '"$app_path/Contents/Helpers"',
        '"$app_path/Contents/Helpers/PhotosIndexerWorker.app"',
        '"$app_path/Contents/Helpers/PhotosIndexerWorker.app/Contents"',
        '"$app_path/Contents/Helpers/PhotosIndexerWorker.app/Contents/MacOS"',
        '"$app_path/Contents/Helpers/PhotosIndexerWorker.app/Contents/Frameworks"',
        '"$app_path/Contents/Helpers/PhotosIndexerWorker.app/Contents/Resources"',
    )
    check(
        checks,
        "EMBEDDED_HELPER_BUNDLE_PATH_GUARD",
        "for bundle_path in" in embedded_helper_source
        and all(path in embedded_helper_source for path in embedded_helper_core_paths)
        and 'embedded_helper:FAIL:bundle_path_invalid' in embedded_helper_source
        and embedded_helper_source.index('bundle_path_invalid') < embedded_helper_source.index('helper_root='),
    )
    check(
        checks,
        "EMBEDDED_HELPER_NATIVE_BUNDLE_IDENTITY",
        'helper_root="$app_path/Contents/Helpers/PhotosIndexerWorker.app"' in embedded_helper_source
        and 'helper="$helper_root/Contents/MacOS/PhotosIndexerWorker"' in embedded_helper_source
        and 'helper_info="$helper_root/Contents/Info.plist"' in embedded_helper_source
        and 'helper_source_marker="$helper_root/Contents/Resources/.photos-indexer-source-fingerprint"'
        in embedded_helper_source
        and '"CFBundleIdentifier": "com.photoslocalkeywordindexer.worker"'
        in embedded_helper_source
        and '"CFBundlePackageType": "APPL"' in embedded_helper_source
        and '"CFBundleExecutable": "PhotosIndexerWorker"' in embedded_helper_source
        and '"LSUIElement": True' in embedded_helper_source
        and 'legacy_helper="$app_path/Contents/Helpers/PhotosIndexerWorker/PhotosIndexerWorker"'
        in embedded_helper_source,
    )
    check(
        checks,
        "EMBEDDED_HELPER_INTEGRITY_GUARD",
        "metadata.st_uid != os.getuid()" in embedded_helper_source
        and "metadata.st_mode & 0o022" in embedded_helper_source
        and "metadata.st_nlink != 1" in embedded_helper_source
        and 'embedded_helper:FAIL:payload_ownership' in embedded_helper_source
        and 'embedded_helper:FAIL:payload_permissions' in embedded_helper_source
        and 'embedded_helper:FAIL:payload_hardlink' in embedded_helper_source
        and 'stat.S_IMODE(os.lstat(marker_path).st_mode) != 0o600' in embedded_helper_source,
    )
    check(
        checks,
        "EMBEDDED_HELPER_PAYLOAD_SYMLINK_GUARD",
        'find "$helper_root" -type l -print' in embedded_helper_source
        and 'embedded_helper:FAIL:payload_symlink' in embedded_helper_source
        and 'helper_root_real="${helper_root:A}"' in embedded_helper_source,
    )
    check(
        checks,
        "EMBEDDED_HELPER_PAYLOAD_SCAN_GUARD",
        'payload_paths="$(find "$helper_root" -type l -print 2>/dev/null)"' in embedded_helper_source
        and 'embedded_helper:FAIL:payload_scan_failed' in embedded_helper_source,
    )
    embedded_cleanup = "\ncleanup_diagnostic\n"
    embedded_cleanup_guard = (
        'if (( cleanup_failed == 1 )) || [[ -e "$diagnostic_root" || -L "$diagnostic_root" ]]; then'
    )
    embedded_ready = 'print -- "embedded_helper:READY"'
    check(
        checks,
        "EMBEDDED_HELPER_FINAL_CLEANUP_BEFORE_READY",
        "cleanup_failed=0" in embedded_helper_source
        and embedded_cleanup in embedded_helper_source
        and embedded_cleanup_guard in embedded_helper_source
        and "embedded_helper:FAIL:final_cleanup_failed" in embedded_helper_source
        and "trap - EXIT INT TERM" in embedded_helper_source
        and embedded_ready in embedded_helper_source
        and embedded_helper_source.index(embedded_cleanup)
        < embedded_helper_source.index(embedded_cleanup_guard)
        and embedded_helper_source.index(embedded_cleanup_guard)
        < embedded_helper_source.index("trap - EXIT INT TERM")
        and embedded_helper_source.index("trap - EXIT INT TERM")
        < embedded_helper_source.index(embedded_ready),
    )
    check(
        checks,
        "APP_POSTBUILD_CLEANUP",
        "cleanup_app_bundle()" in swift_build_source
        and 'app_bundle_created_identity=""' in swift_build_source
        and "record_app_bundle_identity()" in swift_build_source
        and "app_bundle_is_owned()" in swift_build_source
        and "if (( build_succeeded == 0 )) && app_bundle_is_owned; then" in swift_build_source
        and "trap cleanup_app_bundle EXIT" in swift_build_source
        and swift_build_source.index("trap cleanup_app_bundle EXIT") < swift_build_source.index('"$swift_bin" build --package-path')
        and "trap - EXIT INT TERM" in swift_build_source,
    )
    swift_build_invocation = '"$swift_bin" build --package-path'
    check(
        checks,
        "APP_SWIFT_LOCKED_BUILD",
        swift_build_invocation in swift_build_source
        and "--disable-automatic-resolution" in swift_build_source,
    )
    check(
        checks,
        "APP_SWIFT_SELECTED_TOOLCHAIN",
        "command -v xcrun" in swift_build_source
        and "xcrun --find swift" in swift_build_source
        and "swift_bin=" in swift_build_source
        and swift_build_invocation in swift_build_source
        and swift_build_source.index("xcrun --find swift") < swift_build_source.index(swift_build_invocation),
    )
    check(
        checks,
        "APP_NO_BARE_SWIFT_SHIM_DEPENDENCY",
        'if ! command -v swift >/dev/null 2>&1; then' not in swift_build_source
        and 'xcrun --find swift' in swift_build_source,
    )
    app_xcode_preflight = 'if ! command -v xcodebuild >/dev/null 2>&1; then'
    check(
        checks,
        "APP_XCODE_PREFLIGHT",
        app_xcode_preflight in swift_build_source
        and "xcodebuild -version" in swift_build_source
        and "xcode-select -p" in swift_build_source
        and swift_build_source.index(app_xcode_preflight) < swift_build_source.index('rm -rf -- "$app_bundle"'),
    )
    check(
        checks,
        "APP_BUILD_ROOT_ERROR_REDACTION",
        "build_swift_app:FAIL:build_root_invalid" in swift_build_source
        and "BUILD_ROOT must stay below $project_root/build" not in swift_build_source,
    )
    app_plistbuddy_preflight = 'if [[ "$release_build" == "1" && ! -x /usr/libexec/PlistBuddy ]]; then'
    check(
        checks,
        "APP_PLISTBUDDY_PREFLIGHT",
        app_plistbuddy_preflight in swift_build_source
        and "build_swift_app:FAIL:plistbuddy_missing" in swift_build_source
        and swift_build_source.index(app_plistbuddy_preflight) < swift_build_source.index(swift_build_invocation),
    )
    binary_architecture_gate = 'binary_architectures="$(lipo -archs "$binary"'
    app_bundle_creation = 'mkdir -p "$app_bundle/Contents/MacOS"'
    check(
        checks,
        "APP_MAIN_BINARY_ARCHITECTURE",
        'if ! command -v lipo >/dev/null 2>&1; then' in swift_build_source
        and binary_architecture_gate in swift_build_source
        and "build_swift_app:FAIL:binary_architecture_not_arm64" in swift_build_source
        and "build_swift_app:FAIL:binary_path_invalid" in swift_build_source
        and swift_build_source.index(binary_architecture_gate) < swift_build_source.index(app_bundle_creation),
    )
    package_lock_guard = 'package_lock="$package_root/Package.resolved"'
    check(
        checks,
        "APP_PACKAGE_LOCK_GUARD",
        package_lock_guard in swift_build_source
        and '[[ -L "$package_lock" || ! -f "$package_lock" ]]' in swift_build_source
        and "build_swift_app:FAIL:package_lock_invalid" in swift_build_source
        and swift_build_source.index(package_lock_guard)
        < swift_build_source.index('"$swift_bin" build --package-path'),
    )
    sparkle_lock_prebuild = 'if ! validate_sparkle_lock "$package_lock" "2.9.2"'
    check(
        checks,
        "APP_SPARKLE_LOCK_PREBUILD",
        sparkle_lock_prebuild in swift_build_source
        and "build_swift_app:FAIL:sparkle_lock_invalid" in swift_build_source
        and swift_build_source.index(sparkle_lock_prebuild)
        < swift_build_source.index('"$swift_bin" build --package-path'),
    )
    release_preflight_source = (
        (root / "packaging" / "release_preflight.sh").read_text(encoding="utf-8")
        if (root / "packaging" / "release_preflight.sh").is_file()
        else ""
    )
    check(
        checks,
        "PREFLIGHT_HELPER_RUNTIME_ISOLATION",
        'helper_check_root="$(mktemp -d "${TMPDIR:-/tmp}/photos-indexer-preflight.' in release_preflight_source
        and 'HOME="$helper_check_root"' in release_preflight_source
        and 'TMPDIR="$helper_check_root"' in release_preflight_source
        and "cleanup_helper_check" in release_preflight_source
        and 'HOME="/tmp"' not in release_preflight_source
        and 'TMPDIR="/tmp"' not in release_preflight_source,
    )
    preflight_cleanup = "\ncleanup_helper_check\n"
    preflight_cleanup_guard = 'if (( cleanup_failed == 1 )) || { [[ -n "$helper_check_root" ]] && [[ -e "$helper_check_root" || -L "$helper_check_root" ]]; }; then'
    preflight_cleanup_terminal = (
        f"{preflight_cleanup_guard}\n"
        "  report FAIL helper_check_cleanup_failed\n"
        "else\n"
        "  trap - EXIT INT TERM\n"
        "fi"
    )
    preflight_readiness = "if (( failed == 0 )); then"
    preflight_disable_traps = "trap - EXIT INT TERM"
    check(
        checks,
        "PREFLIGHT_HELPER_FINAL_CLEANUP_BEFORE_READY",
        "cleanup_failed=0" in release_preflight_source
        and preflight_cleanup in release_preflight_source
        and preflight_cleanup_guard in release_preflight_source
        and preflight_cleanup_terminal in release_preflight_source
        and preflight_disable_traps in release_preflight_source
        and preflight_readiness in release_preflight_source
        and release_preflight_source.index(preflight_cleanup)
        < release_preflight_source.index(preflight_cleanup_guard)
        and release_preflight_source.index(preflight_cleanup_guard)
        < release_preflight_source.index(preflight_readiness)
        and release_preflight_source.index(preflight_disable_traps)
        < release_preflight_source.index(preflight_readiness),
    )
    check(
        checks,
        "PREFLIGHT_HELPER_CLEANUP_DIAGNOSTIC_SCOPED",
        release_preflight_source.count('"HELPER_CHECK_CLEANUP_FAILED"') == 2,
    )
    preflight_helper_fingerprint_codes = (
        "HELPER_SOURCE_FINGERPRINT_MISSING",
        "HELPER_SOURCE_FINGERPRINT_STALE",
        "HELPER_SOURCE_FINGERPRINT_UNAVAILABLE",
    )
    check(
        checks,
        "PREFLIGHT_HELPER_FINGERPRINT_DIAGNOSTICS_SCOPED",
        all(
            release_preflight_source.count(f'"{code}"') == 2
            for code in preflight_helper_fingerprint_codes
        ),
    )
    check(
        checks,
        "PREFLIGHT_SWIFT_SELECTED_TOOLCHAIN",
        "xcrun --find swift" in release_preflight_source
        and "swift_toolchain_unavailable" in release_preflight_source
        and 'DEVELOPER_DIR="$selected_developer_dir" xcrun --find swift' in release_preflight_source,
    )
    helper_build_source = (
        (root / "packaging" / "build_python_helper.sh").read_text(encoding="utf-8")
        if (root / "packaging" / "build_python_helper.sh").is_file()
        else ""
    )
    helper_fingerprint_path = root / "packaging" / "helper_source_fingerprint.py"
    sign_app_source = (
        (root / "packaging" / "sign_app.sh").read_text(encoding="utf-8")
        if (root / "packaging" / "sign_app.sh").is_file()
        else ""
    )
    notarize_fingerprint_source = (
        (root / "packaging" / "notarize.sh").read_text(encoding="utf-8")
        if (root / "packaging" / "notarize.sh").is_file()
        else ""
    )
    check(
        checks,
        "HELPER_SOURCE_FINGERPRINT_CHAIN",
        helper_fingerprint_path.is_file()
        and not helper_fingerprint_path.is_symlink()
        and "helper_source_fingerprint" in helper_build_source
        and "helper_source_fingerprint_stale" in swift_build_source
        and "helper_source_fingerprint_stale" in release_preflight_source
        and "helper_source_fingerprint_stale" in sign_app_source
        and "helper_source_fingerprint_stale" in notarize_fingerprint_source,
    )
    lipo_precheck = 'if ! command -v lipo >/dev/null 2>&1; then'
    pyinstaller_invocation = '"$python_bin" -m PyInstaller'
    check(
        checks,
        "HELPER_LIPO_PRECHECK",
        lipo_precheck in helper_build_source
        and pyinstaller_invocation in helper_build_source
        and helper_build_source.index(lipo_precheck) < helper_build_source.index(pyinstaller_invocation)
        and "build_python_helper:FAIL:lipo_missing" in helper_build_source,
    )
    python_path_resolution = 'python_bin="$(command -v "$python_bin")"'
    check(
        checks,
        "HELPER_PYTHON_PATH_STABLE",
        python_path_resolution in helper_build_source
        and 'cd "$project_root"' in helper_build_source
        and helper_build_source.index(python_path_resolution) < helper_build_source.index('cd "$project_root"')
        and python_path_resolution in helper_build_source[: helper_build_source.index('cd "$project_root"')],
    )
    runtime_import_preflight = (
        'PYTHONPATH="$project_root/src" "$python_bin" -c',
        "import AppKit, CoreLocation, Foundation, MapKit, Photos, Quartz, objc, " + "ht" + "tp" + "x",
        "from photos_indexer.adapters import PhotoScriptBridge",
        "PhotoScriptBridge.preflight_compatibility()",
    )
    check(
        checks,
        "HELPER_RUNTIME_IMPORT_PREFLIGHT",
        all(marker in helper_build_source for marker in runtime_import_preflight)
        and pyinstaller_invocation in helper_build_source
        and max(helper_build_source.index(marker) for marker in runtime_import_preflight)
        < helper_build_source.index(pyinstaller_invocation),
    )
    runtime_provenance_markers = (
        "from importlib.metadata import version",
        "if version(package) != expected:",
        '"photoscript": "0.5.3"',
        '"pyobjc-framework-Photos": "12.2.2"',
        '"pyobjc-framework-MapKit": "12.2.2"',
        '"pyobjc-framework-CoreLocation": "12.2.2"',
        '"pyobjc-framework-Quartz": "12.2.2"',
        "build_python_helper:FAIL:python_runtime_provenance_invalid",
    )
    check(
        checks,
        "HELPER_RUNTIME_PROVENANCE_PREFLIGHT",
        all(marker in helper_build_source for marker in runtime_provenance_markers)
        and has_active_runtime_provenance_check(helper_build_source)
        and helper_build_source.index("from importlib.metadata import version") < helper_build_source.index(pyinstaller_invocation)
        and helper_build_source.index("python_runtime_provenance_invalid") < helper_build_source.index(pyinstaller_invocation),
    )
    check(
        checks,
        "HELPER_ARCHITECTURE_ERROR_REDACTION",
        "Expected arm64 Python 3.12; found $python_architecture." in helper_build_source
        and "Expected arm64 Python 3.12 at $python_bin" not in helper_build_source,
    )
    check(
        checks,
        "HELPER_DEPENDENCY_ERROR_REDACTION",
        "Install build dependencies first with Python 3.12" in helper_build_source
        and "Install build dependencies first: $python_bin" not in helper_build_source,
    )
    isolation_markers = (
        'isolation_root="$(mktemp -d',
        'cd "$isolation_root"',
        "env -i",
        'PYTHONPATH',
        'PYTHONHOME',
        'VIRTUAL_ENV',
        'VERIFY_HELPER',
    )
    check(
        checks,
        "HELPER_RUNTIME_ISOLATION",
        all(marker in helper_build_source for marker in isolation_markers),
    )
    helper_output_cleanup = 'rm -rf -- "$build_root/dist/PhotosIndexerWorker"'
    check(
        checks,
        "HELPER_APPLESCRIPT_ERROR_PREFLIGHT",
        '[[ "$runtime_dependency_error" == *"-2741"* ]]' in helper_build_source
        and "build_python_helper:FAIL:photoscript_applescript_unavailable" in helper_build_source
        and "build_python_helper:HINT:photoscript_applescript_unavailable:check_photoscript_and_macos_compatibility_before_retry" in helper_build_source
        and "build_python_helper:INFO:previous_helper_preserved_not_release_ready" in helper_build_source
        and helper_build_source.index('[[ "$runtime_dependency_error" == *"-2741"* ]]') < helper_build_source.index(helper_output_cleanup),
    )
    check(
        checks,
        "HELPER_LIPO_CLEANUP",
        helper_output_cleanup in helper_build_source
        and lipo_precheck in helper_build_source
        and pyinstaller_invocation in helper_build_source
        and helper_build_source.index(lipo_precheck) < helper_build_source.index(helper_output_cleanup)
        and helper_build_source.index(helper_output_cleanup) < helper_build_source.index(pyinstaller_invocation),
    )
    check(
        checks,
        "HELPER_STALE_CLEANUP",
        helper_output_cleanup in helper_build_source
        and pyinstaller_invocation in helper_build_source
        and helper_build_source.index(helper_output_cleanup) < helper_build_source.index(pyinstaller_invocation),
    )
    helper_spec = (
        (root / "packaging" / "PhotosIndexerWorker.spec").read_text(encoding="utf-8")
        if (root / "packaging" / "PhotosIndexerWorker.spec").is_file()
        else ""
    )
    check(
        checks,
        "PYINSTALLER_SPEC_ARM64",
        bool(re.search(r"target_arch\s*=\s*[\"']arm64[\"']", helper_spec)),
    )
    check(
        checks,
        "HELPER_POSTBUILD_CLEANUP",
        "cleanup_helper_output()" in helper_build_source
        and "trap cleanup_helper_output EXIT" in helper_build_source
        and helper_build_source.index("trap cleanup_helper_output EXIT") < helper_build_source.index(pyinstaller_invocation)
        and "trap - EXIT" in helper_build_source,
    )
    helper_final_cleanup = "\n  cleanup_helper_check\n"
    helper_final_cleanup_guard = 'if [[ -e "$isolation_root" || -L "$isolation_root" ]]; then'
    helper_ready_marker = 'print -- "release_helper:READY_FOR_APP_BUNDLE"'
    check(
        checks,
        "HELPER_FINAL_CLEANUP_BEFORE_READY",
        helper_final_cleanup in helper_build_source
        and helper_final_cleanup_guard in helper_build_source
        and helper_ready_marker in helper_build_source
        and "build_python_helper:FAIL:final_cleanup_failed" in helper_build_source
        and helper_build_source.index(helper_final_cleanup)
        < helper_build_source.index(helper_final_cleanup_guard)
        and helper_build_source.index(helper_final_cleanup_guard)
        < helper_build_source.index("trap - EXIT INT TERM", helper_build_source.index(helper_final_cleanup_guard))
        and helper_build_source.index(
            "trap - EXIT INT TERM", helper_build_source.index(helper_final_cleanup_guard)
        )
        < helper_build_source.index(helper_ready_marker),
    )
    helper_unverified_trap_teardown = "else\n  trap - EXIT INT TERM\nfi"
    check(
        checks,
        "HELPER_SIGNAL_TRAPS_CLEARED_BEFORE_READY",
        helper_unverified_trap_teardown in helper_build_source
        and helper_ready_marker in helper_build_source
        and helper_build_source.index(helper_unverified_trap_teardown)
        < helper_build_source.index(helper_ready_marker),
    )
    helper_output_path_gate = 'helper="$helper_app/Contents/MacOS/PhotosIndexerWorker"'
    check(
        checks,
        "HELPER_OUTPUT_SYMLINK_GUARD",
        helper_output_path_gate in helper_build_source
        and helper_build_source.index(helper_output_path_gate) > helper_build_source.index(pyinstaller_invocation)
        and '|| ! -x "$helper" \\' in helper_build_source
        and '|| -L "$helper" ]]; then' in helper_build_source
        and helper_build_source.index(helper_output_path_gate) < helper_build_source.index('lipo -archs "$helper"'),
    )
    check(
        checks,
        "HELPER_DIST_PATH_GUARD",
        'if [[ -L "$helper_dist" ]]; then' in helper_build_source
        and "build_python_helper:FAIL:dist_symlink" in helper_build_source
        and helper_build_source.index('if [[ -L "$helper_dist" ]]; then') < helper_build_source.index(helper_output_cleanup),
    )
    build_directories_guard = 'for build_directory in "$build_root/work" "$build_root/spec"; do'
    check(
        checks,
        "HELPER_BUILD_DIRECTORIES_GUARD",
        build_directories_guard in helper_build_source
        and "build_python_helper:FAIL:build_directory_symlink" in helper_build_source
        and "build_python_helper:FAIL:build_directory_not_directory" in helper_build_source
        and helper_build_source.index(build_directories_guard)
        < helper_build_source.index('mkdir -p "$build_root/dist" "$build_root/work" "$build_root/spec"'),
    )
    build_directories_create = 'mkdir -p "$build_root/dist" "$build_root/work" "$build_root/spec"'
    check(
        checks,
        "HELPER_BUILD_DIRECTORIES_AFTER_PREFLIGHT",
        build_directories_create in helper_build_source
        and helper_build_source.index('command -v "$python_bin"') < helper_build_source.index(build_directories_create)
        and helper_build_source.index('command -v lipo') < helper_build_source.index(build_directories_create),
    )
    check(
        checks,
        "HELPER_BUILD_DIRECTORY_SETUP_CLEANUP",
        "cleanup_build_directories()" in helper_build_source
        and "trap cleanup_build_directories EXIT" in helper_build_source
        and "build_directories_ready=1" in helper_build_source
        and helper_build_source.index("trap cleanup_build_directories EXIT") < helper_build_source.index(build_directories_create)
        and helper_build_source.index("build_directories_ready=1") > helper_build_source.index(build_directories_create),
    )
    helper_build_path_guard = 'validate_build_path "$build_root" "$project_root/build" "BUILD_ROOT"'
    check(
        checks,
        "HELPER_BUILD_PATH_GUARD",
        'source "$project_root/packaging/build_path_guard.zsh"' in helper_build_source
        and helper_build_path_guard in helper_build_source
        and helper_build_source.index(helper_build_path_guard) < helper_build_source.index(build_directories_create),
    )
    build_path_guard_source = (
        (root / "packaging" / "build_path_guard.zsh").read_text(encoding="utf-8")
        if (root / "packaging" / "build_path_guard.zsh").is_file()
        else ""
    )
    check(
        checks,
        "BUILD_PATH_ERROR_REDACTION",
        'contains a symlinked component."' in build_path_guard_source
        and 'contains a symlinked component: $cursor' not in build_path_guard_source,
    )
    signal_cleanup_requirements = {
        root / "packaging" / "build_python_helper.sh": "trap 'cleanup_helper_output; exit 130' INT TERM",
        root / "packaging" / "build_swift_app.sh": "trap 'cleanup_app_bundle; exit 130' INT TERM",
        root / "packaging" / "build_dmg.sh": "trap 'cleanup_build_artifacts; exit 130' INT TERM",
        root / "packaging" / "build_app_icon.sh": "trap 'cleanup; exit 130' INT TERM",
        root / "packaging" / "notarize.sh": "trap 'cleanup; exit 130' INT TERM",
        root / "packaging" / "verify_dmg_layout.sh": "trap 'cleanup; exit 130' INT TERM",
        root / "packaging" / "verify_release.sh": "trap 'cleanup_entitlements; exit 130' INT TERM",
    }
    check(
        checks,
        "PACKAGING_SIGNAL_CLEANUP",
        all(path.is_file() and marker in path.read_text(encoding="utf-8") for path, marker in signal_cleanup_requirements.items()),
    )
    evidence_source = (
        (root / "packaging" / "write_release_evidence.sh").read_text(encoding="utf-8")
        if (root / "packaging" / "write_release_evidence.sh").is_file()
        else ""
    )
    check(
        checks,
        "RELEASE_EVIDENCE_SIGNAL_CLEANUP",
        "signal.signal(signal.SIGINT" in evidence_source
        and "signal.signal(signal.SIGTERM" in evidence_source
        and "cleanup_temporary_file()" in evidence_source,
    )
    check(
        checks,
        "RELEASE_EVIDENCE_OUTPUT_PATH_GUARD",
        'output_parent="${output_path:h}"' in evidence_source
        and '[[ -L "$output_parent" ]]' in evidence_source
        and evidence_source.index('[[ -L "$output_parent" ]]')
        < evidence_source.index("/usr/bin/python3 -"),
    )
    check(
        checks,
        "RELEASE_EVIDENCE_INPUT_PATH_GUARD",
        "reject_symlink_components()" in evidence_source
        and 'reject_symlink_components "$dmg_path"' in evidence_source
        and 'reject_symlink_components "$notary_result"' in evidence_source
        and 'reject_symlink_components "$output_path"' in evidence_source
        and evidence_source.index("reject_symlink_components()")
        < evidence_source.index("/usr/bin/python3 -"),
    )
    check(
        checks,
        "RELEASE_EVIDENCE_APP_PATH_GUARD",
        'reject_symlink_components "$app_path"' in evidence_source
        and evidence_source.index('reject_symlink_components "$app_path"')
        < evidence_source.index("/usr/bin/python3 -"),
    )
    check(
        checks,
        "RELEASE_EVIDENCE_ERROR_REDACTION",
        'print("Release evidence input is invalid.", file=sys.stderr)' in evidence_source
        and 'f"Release evidence input is invalid: {exc}"' not in evidence_source
        and 'f"Release bundle fingerprint failed: {exc}"' not in evidence_source,
    )
    check(
        checks,
        "RELEASE_EVIDENCE_PUBLICATION_ERROR_REDACTION",
        "except BaseException as error:" in evidence_source
        and 'print("Release evidence publication failed.", file=sys.stderr)' in evidence_source
        and "raise SystemExit(1) from None" in evidence_source
        and "raise\nPY" not in evidence_source,
    )
    check(
        checks,
        "RELEASE_EVIDENCE_VERSION_GATE",
        '"CFBundleShortVersionString"' in evidence_source
        and '"CFBundleVersion"' in evidence_source
        and "Release DMG filename does not match bundle version and build." in evidence_source,
    )
    dmg_build_source = (
        (root / "packaging" / "build_dmg.sh").read_text(encoding="utf-8")
        if (root / "packaging" / "build_dmg.sh").is_file()
        else ""
    )
    notarize_ready_marker = 'print -- "notarize:READY_FOR_DISTRIBUTION"'
    finalized_dmg_record = "if ! record_finalized_dmg_fingerprint; then"
    finalized_dmg_recheck = "if ! dmg_matches_finalized_fingerprint; then"
    release_evidence_record = "if ! record_release_evidence_fingerprint; then"
    release_evidence_recheck = "if ! evidence_matches_release_fingerprint; then"
    integrated_evidence_call = '"$notary_result" "$evidence_path" >/dev/null'
    integrated_evidence_marker = 'print -- "release_evidence:RECORDED"'
    notarize_completion_marker = "notarize_completed=1"
    notarize_marker_scoped = (
        notarize_ready_marker in notarize_fingerprint_source
        and '"$script_root/write_release_evidence.sh"' in notarize_fingerprint_source
        and 'hdiutil detach "$mount_dir"' in notarize_fingerprint_source
        and finalized_dmg_record in notarize_fingerprint_source
        and finalized_dmg_recheck in notarize_fingerprint_source
        and release_evidence_record in notarize_fingerprint_source
        and release_evidence_recheck in notarize_fingerprint_source
        and integrated_evidence_call in notarize_fingerprint_source
        and integrated_evidence_marker in notarize_fingerprint_source
        and notarize_completion_marker in notarize_fingerprint_source
        and 'xcrun stapler validate "$dmg_path"' in notarize_fingerprint_source
        and notarize_fingerprint_source.index('xcrun stapler validate "$dmg_path"')
        < notarize_fingerprint_source.index(finalized_dmg_record)
        and notarize_fingerprint_source.index(finalized_dmg_record)
        < notarize_fingerprint_source.index('"$script_root/write_release_evidence.sh"')
        and notarize_fingerprint_source.index('"$script_root/write_release_evidence.sh"')
        < notarize_fingerprint_source.index(release_evidence_record)
        and notarize_fingerprint_source.index(release_evidence_record)
        < notarize_fingerprint_source.index(finalized_dmg_recheck)
        and notarize_fingerprint_source.rindex('hdiutil detach "$mount_dir"')
        < notarize_fingerprint_source.index(finalized_dmg_recheck)
        and notarize_fingerprint_source.index(finalized_dmg_recheck)
        < notarize_fingerprint_source.index(release_evidence_recheck)
        and notarize_fingerprint_source.index(release_evidence_recheck)
        < notarize_fingerprint_source.index(notarize_completion_marker)
        and notarize_fingerprint_source.index(notarize_completion_marker)
        < notarize_fingerprint_source.index(integrated_evidence_marker)
        and notarize_fingerprint_source.index(integrated_evidence_marker)
        < notarize_fingerprint_source.index(notarize_ready_marker)
    )
    scoped_release_markers = (
        'print -- "release_helper:READY_FOR_APP_BUNDLE"' in helper_build_source
        and 'print -- "release_app:READY_FOR_SIGNING_AND_DMG"' in swift_build_source
        and 'print -- "release_signature:READY_FOR_DMG"' in sign_app_source
        and 'print -- "release_dmg:READY_FOR_NOTARIZATION"' in dmg_build_source
        and 'print -- "release_preflight:READY:release_gates_available_runtime_not_checked"'
        in release_preflight_source
        and 'print -- "release_evidence:RECORDED"' in evidence_source
        and notarize_marker_scoped
    )
    generic_release_markers = (
        'print -- "release_helper:READY"',
        'print -- "release_app:READY"',
        'print -- "release_signature:READY"',
        'print -- "release_dmg:READY"',
        'print -- "release_evidence:READY"',
        'print -- "notarize:READY"',
        'print -- "release_preflight:READY"',
        'print -- "release_preflight:READY:all_gates_available"',
    )
    release_stage_sources = "\n".join(
        (
            helper_build_source,
            swift_build_source,
            sign_app_source,
            dmg_build_source,
            release_preflight_source,
            evidence_source,
            notarize_fingerprint_source,
        )
    )
    ready_marker_pattern = re.compile(
        r"(?<![A-Za-z0-9_])"
        r"((?:release_(?:helper|app|signature|dmg|preflight|evidence)|notarize):READY[A-Za-z0-9_:]*)"
        r"(?![A-Za-z0-9_])"
    )
    stage_ready_markers_scoped = (
        set(ready_marker_pattern.findall(helper_build_source))
        == {"release_helper:READY_FOR_APP_BUNDLE"}
        and set(ready_marker_pattern.findall(swift_build_source))
        == {"release_app:READY_FOR_SIGNING_AND_DMG", "release_app:READY_DEV"}
        and set(ready_marker_pattern.findall(sign_app_source))
        == {"release_signature:READY_FOR_DMG"}
        and set(ready_marker_pattern.findall(dmg_build_source))
        == {"release_dmg:READY_FOR_NOTARIZATION", "release_dmg:READY_DEV"}
        and set(ready_marker_pattern.findall(release_preflight_source))
        == {"release_preflight:READY:release_gates_available_runtime_not_checked"}
        and not ready_marker_pattern.findall(evidence_source)
        and set(ready_marker_pattern.findall(notarize_fingerprint_source))
        == {"notarize:READY_FOR_DISTRIBUTION"}
    )
    check(
        checks,
        "RELEASE_STAGE_MARKERS_SCOPED",
        scoped_release_markers
        and stage_ready_markers_scoped
        and not any(marker in release_stage_sources for marker in generic_release_markers),
    )
    release_layout_gate = 'if [[ "$release_build" == "1" && "$verify_dmg_layout" != "1" ]]; then'
    check(
        checks,
        "RELEASE_DMG_LAYOUT_REQUIRED",
        release_layout_gate in dmg_build_source
        and "build_dmg:FAIL:dmg_layout_verification_required" in dmg_build_source
        and dmg_build_source.index(release_layout_gate) < dmg_build_source.index("hdiutil create"),
    )
    hdiutil_preflight = "if ! command -v hdiutil >/dev/null 2>&1; then"
    check(
        checks,
        "BUILD_DMG_HDIUTIL_PREFLIGHT",
        hdiutil_preflight in dmg_build_source
        and "build_dmg:FAIL:hdiutil_missing" in dmg_build_source
        and dmg_build_source.index(hdiutil_preflight) < dmg_build_source.index('"$project_root/packaging/sign_app.sh" "$app_path"'),
    )
    dmg_copy_tools_preflight = "for command_name in ditto ln; do"
    check(
        checks,
        "BUILD_DMG_COPY_TOOL_PREFLIGHT",
        dmg_copy_tools_preflight in dmg_build_source
        and "build_dmg:FAIL:${command_name}_missing" in dmg_build_source
        and dmg_build_source.index(dmg_copy_tools_preflight)
        < dmg_build_source.index('"$project_root/packaging/sign_app.sh" "$app_path"'),
    )
    helper_freshness_check = 'cmp -s "$source_helper" "$app_helper"'
    check(
        checks,
        "DMG_EMBEDDED_HELPER_FRESHNESS_GATE",
        helper_freshness_check in dmg_build_source
        and "build_dmg:FAIL:embedded_helper_stale" in dmg_build_source
        and "build_dmg:HINT:embedded_helper_stale:rebuild_the_app_with_the_current_helper" in dmg_build_source
        and dmg_build_source.index("embedded_helper_stale") < dmg_build_source.index(
            '"$project_root/packaging/sign_app.sh" "$app_path"'
        ),
    )
    check(
        checks,
        "DMG_HELPER_SOURCE_FINGERPRINT_GATE",
        "helper_source_fingerprint_stale" in dmg_build_source
        and "helper_source_fingerprint_missing" in dmg_build_source
        and dmg_build_source.index("helper_source_fingerprint_stale") < dmg_build_source.index(
            '"$project_root/packaging/sign_app.sh" "$app_path"'
        ),
    )
    check(
        checks,
        "DMG_EMBEDDED_HELPER_PAYLOAD_FRESHNESS_GATE",
        "helper_payload_check" in dmg_build_source
        and "os.walk" in dmg_build_source
        and "build_dmg:FAIL:embedded_helper_payload_stale" in dmg_build_source
        and "build_dmg:FAIL:embedded_helper_payload_uncheckable" in dmg_build_source
        and dmg_build_source.index("embedded_helper_payload_stale") < dmg_build_source.index(
            '"$project_root/packaging/sign_app.sh" "$app_path"'
        ),
    )
    check(
        checks,
        "BUILD_DMG_ERROR_REDACTION",
        dmg_build_source.count('print -u2 -- "Refusing to overwrite an existing DMG."') == 2
        and 'Refusing to overwrite an existing DMG: $dmg_path' not in dmg_build_source
        and all(
            "$" not in line
            for line in dmg_build_source.splitlines()
            if 'print -u2 -- "build_dmg:FAIL:output_directory' in line
        ),
    )
    identity_guards = (
        '[[ -n "${DEVELOPER_ID_APPLICATION:-}" ]] ||',
        '[[ "$release_build" == "1" && -z "${DEVELOPER_ID_APPLICATION:-}" ]]',
    )
    identity_guard_positions = [
        dmg_build_source.index(guard) for guard in identity_guards if guard in dmg_build_source
    ]
    check(
        checks,
        "DMG_IDENTITY_PREFLIGHT",
        bool(identity_guard_positions)
        and min(identity_guard_positions) < dmg_build_source.index('mkdir -p "$output_root"')
        and min(identity_guard_positions)
        < dmg_build_source.index('if ! mkdir "$reservation_path" 2>/dev/null; then'),
    )
    sign_app_source = (
        (root / "packaging" / "sign_app.sh").read_text(encoding="utf-8")
        if (root / "packaging" / "sign_app.sh").is_file()
        else ""
    )
    sign_integrity_gate = 'bundle_hardlink_check="$(/usr/bin/python3 - "$app_path"'
    check(
        checks,
        "SIGN_APP_BUNDLE_INTEGRITY_GUARD",
        sign_integrity_gate in sign_app_source
        and "os.stat(candidate, follow_symlinks=False)" in sign_app_source
        and "metadata.st_nlink != 1" in sign_app_source
        and "metadata.st_uid != os.getuid()" in sign_app_source
        and "metadata.st_mode & 0o022" in sign_app_source
        and 'sign_app:FAIL:bundle_hardlink' in sign_app_source
        and 'sign_app:FAIL:bundle_ownership' in sign_app_source
        and 'sign_app:FAIL:bundle_permissions' in sign_app_source
        and sign_app_source.index(sign_integrity_gate) < sign_app_source.index("sign_tree_inner_first"),
    )
    sign_verify_tool_preflight = "for command_name in plutil lipo spctl; do"
    check(
        checks,
        "SIGN_APP_VERIFY_TOOL_PREFLIGHT",
        sign_verify_tool_preflight in sign_app_source
        and 'Required release verification tool is unavailable: $command_name' in sign_app_source
        and sign_app_source.index(sign_verify_tool_preflight) < sign_app_source.index("sign_tree_inner_first"),
    )
    check(
        checks,
        "SIGN_APP_SYMLINK_ERROR_REDACTION",
        'sign_app:FAIL:bundle_symlink' in sign_app_source
        and 'sign_app:HINT:bundle_symlink:rebuild_bundle_without_symlinked_core_nodes_then_retry' in sign_app_source
        and 'Release signing refuses symlinked bundle nodes: $path' not in sign_app_source,
    )
    reservation_path = 'reservation_path="$dmg_path.reservation"'
    reservation_acquire = 'if ! mkdir "$reservation_path" 2>/dev/null; then'
    reservation_identity = 'reservation_created_identity=""'
    reservation_ownership = 'reservation_is_owned()'
    reservation_cleanup = 'if (( reservation_acquired == 1 )) && reservation_is_owned; then'
    check(
        checks,
        "DMG_ARTIFACT_RESERVATION",
        reservation_path in dmg_build_source
        and 'reservation_acquired=0' in dmg_build_source
        and reservation_identity in dmg_build_source
        and reservation_ownership in dmg_build_source
        and reservation_acquire in dmg_build_source
        and reservation_cleanup in dmg_build_source
        and 'record_reservation_identity' in dmg_build_source
        and dmg_build_source.index(reservation_acquire) < dmg_build_source.index('"$project_root/packaging/sign_app.sh" "$app_path"'),
    )
    check(
        checks,
        "DMG_OUTPUT_DIRECTORY_CLEANUP",
        "output_root_preexisting=0" in dmg_build_source
        and "rmdir -- \"$output_root\"" in dmg_build_source
        and "output_root_preexisting" in dmg_build_source[dmg_build_source.index("cleanup_build_artifacts()") :]
        and dmg_build_source.index("output_root_preexisting=0") < dmg_build_source.index('mkdir -p "$output_root"'),
    )
    dmg_final_cleanup = "\ncleanup_build_artifacts\n"
    dmg_final_cleanup_guard = "if (( cleanup_failed == 1 )); then"
    check(
        checks,
        "BUILD_DMG_FINAL_CLEANUP_BEFORE_READY",
        "cleanup_failed=0" in dmg_build_source
        and dmg_final_cleanup in dmg_build_source
        and dmg_final_cleanup_guard in dmg_build_source
        and "build_dmg:FAIL:final_cleanup_failed" in dmg_build_source
        and "trap - EXIT INT TERM" in dmg_build_source
        and dmg_build_source.index("preserve_dmg=1")
        < dmg_build_source.index(dmg_final_cleanup, dmg_build_source.index("preserve_dmg=1"))
        and dmg_build_source.index(dmg_final_cleanup, dmg_build_source.index("preserve_dmg=1"))
        < dmg_build_source.index(dmg_final_cleanup_guard)
        and dmg_build_source.index(dmg_final_cleanup_guard)
        < dmg_build_source.index("trap - EXIT INT TERM")
        and dmg_build_source.index("trap - EXIT INT TERM")
        < dmg_build_source.index('print -- "release_dmg:READY_FOR_NOTARIZATION"'),
    )

    sparkle_path = root / "app" / "Package.resolved"
    try:
        if sparkle_path.is_symlink() or not sparkle_path.is_file() or sparkle_path.stat().st_size > 1024 * 1024:
            raise ValueError("Package.resolved must be a regular bounded file")
        sparkle = json.loads(sparkle_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        sparkle = None
    pins = sparkle.get("pins") if isinstance(sparkle, dict) else None
    if pins is None and isinstance(sparkle, dict):
        legacy = sparkle.get("object")
        pins = legacy.get("pins") if isinstance(legacy, dict) else None
    if not isinstance(pins, list):
        pins = []
    sparkle_matches = [
        pin
        for pin in pins
        if isinstance(pin, dict)
        and str(pin.get("identity", "")).casefold() == "sparkle"
        and str(pin.get("location", "")).casefold().rstrip("/")
        == "https://github.com/sparkle-project/sparkle.git"
        and isinstance(pin.get("state"), dict)
        and pin["state"].get("version") == "2.9.2"
        and pin["state"].get("revision") == "6276ba2b404829d139c45ff98427cf90e2efc59b"
    ]
    check(checks, "SPARKLE_LOCK_EXACT", len(sparkle_matches) == 1)
    sparkle_locator_source = (
        (root / "packaging" / "sparkle_framework.zsh").read_text(encoding="utf-8")
        if (root / "packaging" / "sparkle_framework.zsh").is_file()
        else ""
    )
    size_check = "oversized = os.path.getsize(path) > 1024 * 1024"
    check(
        checks,
        "SPARKLE_LOCK_SIZE_BOUND",
        size_check in sparkle_locator_source
        and "Package.resolved must be a regular bounded file" in sparkle_locator_source
        and sparkle_locator_source.index(size_check)
        < sparkle_locator_source.index('with open(path, "rb")'),
    )
    check(
        checks,
        "SPARKLE_LOCK_ERROR_REDACTION",
        'except (OSError, ValueError):' in sparkle_locator_source
        and 'print("Package.resolved cannot be read.", file=sys.stderr)' in sparkle_locator_source
        and 'Cannot read Package.resolved: {exc}' not in sparkle_locator_source,
    )
    check(
        checks,
        "SPARKLE_LOCK_SHAPE_GUARD",
        'if isinstance(document, dict):' in sparkle_locator_source
        and 'legacy = document.get("object")' in sparkle_locator_source
        and 'pins = legacy.get("pins") if isinstance(legacy, dict) else None' in sparkle_locator_source,
    )
    check(
        checks,
        "SPARKLE_FRAMEWORK_PLIST_SHAPE",
        'isinstance(plist, dict) and str(plist.get("CFBundleShortVersionString", "")) == expected'
        in sparkle_locator_source,
    )
    package_swift = (root / "app" / "Package.swift").read_text(encoding="utf-8") if (root / "app" / "Package.swift").is_file() else ""
    check(
        checks,
        "PACKAGE_SWIFT_SPARKLE_EXACT",
        bool(
            re.search(
                r'\.package\(url:\s*"https://github\.com/sparkle-project/Sparkle\.git",\s*exact:\s*"2\.9\.2"\)',
                package_swift,
            )
        ),
    )
    sparkle_config_path = root / "packaging" / "sparkle-config.json"
    try:
        if sparkle_config_path.is_symlink() or not sparkle_config_path.is_file() or sparkle_config_path.stat().st_size > 64 * 1024:
            raise ValueError("Sparkle config must be a regular bounded file")
        sparkle_config = json.loads(sparkle_config_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        sparkle_config = None
    check(
        checks,
        "SPARKLE_CONFIG_PRIVACY_SHAPE",
        isinstance(sparkle_config, dict)
        and set(sparkle_config)
        == {"feed_url", "automatically_checks_for_updates", "public_ed25519_key", "keychain_account"}
        and isinstance(sparkle_config.get("feed_url"), str)
        and isinstance(sparkle_config.get("automatically_checks_for_updates"), bool)
        and isinstance(sparkle_config.get("public_ed25519_key"), str)
        and isinstance(sparkle_config.get("keychain_account"), str)
        and bool(re.fullmatch(r"[A-Za-z0-9._-]{1,64}", sparkle_config["keychain_account"])),
    )

    allowed_entitlements = {
        "com.apple.security.automation.apple-events",
        "com.apple.security.personal-information.photos-library",
    }
    entitlement_ok = True
    for relative_path in ("packaging/entitlements.plist", "packaging/helper-entitlements.plist"):
        entitlements = read_plist(root / relative_path)
        entitlement_ok = entitlement_ok and entitlements is not None and set(entitlements) == allowed_entitlements
    check(checks, "ENTITLEMENTS_MINIMAL", entitlement_ok)

    required_scripts = (
        "build_python_helper.sh",
        "build_swift_app.sh",
        "build_app_icon.sh",
        "build_dmg.sh",
        "verify_embedded_helper.sh",
        "verify_dmg_layout.sh",
        "verify_release.sh",
        "sign_app.sh",
        "notarize.sh",
        "write_release_evidence.sh",
        "release_preflight.sh",
    )
    scripts_ok = all(
        (path := root / "packaging" / name).is_file()
        and not path.is_symlink()
        and bool(path.stat().st_mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH))
        for name in required_scripts
    )
    required_support_files = ("build_path_guard.zsh", "sparkle_framework.zsh")
    support_files_ok = all(
        (path := root / "packaging" / name).is_file() and not path.is_symlink()
        for name in required_support_files
    )
    check(checks, "PACKAGING_SCRIPTS_PRESENT", scripts_ok and support_files_ok)

    release_verifier_source = (
        (root / "packaging" / "verify_release.sh").read_text(encoding="utf-8")
        if (root / "packaging" / "verify_release.sh").is_file()
        else ""
    )
    check(
        checks,
        "RELEASE_PLIST_ROOT_SHAPE",
        release_verifier_source.count("if not isinstance(values, dict):") >= 5
        and 'raise SystemExit("plist root must be a dictionary")' in release_verifier_source,
    )
    check(
        checks,
        "RELEASE_PLIST_LINT_ERROR_REDACTION",
        'if ! plutil -lint "$info" >/dev/null 2>&1; then' in release_verifier_source
        and 'verify_release:FAIL:info_plist_invalid' in release_verifier_source
        and 'if ! plutil -lint "$entitlements" >/dev/null 2>&1; then' in release_verifier_source
        and 'verify_release:FAIL:entitlements_plist_invalid' in release_verifier_source
        and 'plutil -lint "$info" >/dev/null\n' not in release_verifier_source,
    )
    check(
        checks,
        "RELEASE_BUNDLE_ERROR_REDACTION",
        "verify_release:FAIL:bundle_symlink" in release_verifier_source
        and "verify_release:FAIL:nested_architecture" in release_verifier_source
        and "Bundle symlink escapes its containing bundle: $candidate" not in release_verifier_source
        and "Nested release executable must be arm64-only: $candidate" not in release_verifier_source,
    )
    check(
        checks,
        "RELEASE_BUNDLE_INTEGRITY_GUARD",
        "bundle_integrity_check" in release_verifier_source
        and "verify_release:FAIL:bundle_hardlink" in release_verifier_source
        and "verify_release:FAIL:bundle_ownership" in release_verifier_source
        and release_verifier_source.index("bundle_integrity_check") < release_verifier_source.index("verify_bundle_permissions"),
    )
    check(
        checks,
        "RELEASE_DEBUG_ENTITLEMENT_ERROR_REDACTION",
        "verify_release:FAIL:debug_entitlement" in release_verifier_source
        and "Release code contains the get-task-allow entitlement: $target" not in release_verifier_source,
    )
    check(
        checks,
        "RELEASE_CODE_SIGNATURE_ERROR_REDACTION",
        ("code" + "sign") + ' --verify --strict --verbose=2 "$target" >/dev/null 2>&1' in release_verifier_source
        and "verify_release:FAIL:code_signature_invalid" in release_verifier_source
        and (("code" + "sign") + ' --verify --strict --verbose=2 "$target" >/dev/null\n') not in release_verifier_source,
    )
    verifier_cleanup = "\ncleanup_entitlements\n"
    verifier_cleanup_guard = (
        'if (( cleanup_failed == 1 )) || [[ -e "$entitlement_tmp" || -L "$entitlement_tmp" ]]; then'
    )
    verifier_pass_terminal = 'print -- "Release verification passed."'
    check(
        checks,
        "RELEASE_VERIFIER_FINAL_CLEANUP_BEFORE_PASS",
        "cleanup_failed=0" in release_verifier_source
        and verifier_cleanup in release_verifier_source
        and verifier_cleanup_guard in release_verifier_source
        and "verify_release:FAIL:final_cleanup_failed" in release_verifier_source
        and "trap - EXIT INT TERM" in release_verifier_source
        and verifier_pass_terminal in release_verifier_source
        and release_verifier_source.index(verifier_cleanup)
        < release_verifier_source.index(verifier_cleanup_guard)
        and release_verifier_source.index(verifier_cleanup_guard)
        < release_verifier_source.index("trap - EXIT INT TERM")
        and release_verifier_source.index("trap - EXIT INT TERM")
        < release_verifier_source.index(verifier_pass_terminal),
    )

    source_files = list((root / "src").rglob("*.py")) + list((root / "app").rglob("*.swift"))
    source_files += [path for path in (root / "packaging").glob("*.sh") if path.is_file()]
    source_text = "\n".join(path.read_text(encoding="utf-8") for path in source_files)
    source_lower = source_text.casefold()
    check(checks, "IMPLEMENTATION_NO_PRIVATE_DB", not any(term in source_lower for term in ("sqlite3", "osxphotos", "photos.sqlite", ".photoslibrary")))
    packaging_text = "\n".join(path.read_text(encoding="utf-8") for path in (root / "packaging").glob("*.sh") if path.is_file())
    check(checks, "DMG_LAYOUT_GATE_PRESENT", "verify_dmg_layout.sh" in packaging_text)
    dmg_layout_source = (
        (root / "packaging" / "verify_dmg_layout.sh").read_text(encoding="utf-8")
        if (root / "packaging" / "verify_dmg_layout.sh").is_file()
        else ""
    )
    helper_verifier_call = '"$project_root/packaging/verify_embedded_helper.sh" "$app_path"'
    check(
        checks,
        "DMG_HELPER_RUNTIME_GATE",
        helper_verifier_call in dmg_layout_source
        and "dmg_helper_runtime" in dmg_layout_source
        and dmg_layout_source.index(helper_verifier_call) < dmg_layout_source.index("emit_json READY none"),
    )
    check(
        checks,
        "DMG_NESTED_HELPER_PATH",
        'helper_app="$app_path/Contents/Helpers/PhotosIndexerWorker.app"' in dmg_layout_source
        and 'helper_info="$helper_app/Contents/Info.plist"' in dmg_layout_source
        and 'helper="$helper_app/Contents/MacOS/PhotosIndexerWorker"' in dmg_layout_source
        and 'Contents/Helpers/PhotosIndexerWorker/PhotosIndexerWorker' not in dmg_layout_source,
    )
    dmg_layout_cleanup = "\ncleanup\n"
    dmg_layout_cleanup_guard = (
        'if (( cleanup_failed == 1 || mounted == 1 )) || [[ -e "$mount_point" || -L "$mount_point" ]]; then'
    )
    dmg_layout_ready_branch = "if (( json_mode == 1 )); then"
    check(
        checks,
        "DMG_LAYOUT_FINAL_CLEANUP_BEFORE_READY",
        "cleanup_failed=0" in dmg_layout_source
        and dmg_layout_cleanup in dmg_layout_source
        and dmg_layout_cleanup_guard in dmg_layout_source
        and "dmg_layout:FAIL:mount_cleanup_failed" in dmg_layout_source
        and "trap - EXIT INT TERM" in dmg_layout_source
        and dmg_layout_source.index(dmg_layout_cleanup)
        < dmg_layout_source.index(dmg_layout_cleanup_guard)
        and dmg_layout_source.index(dmg_layout_cleanup_guard)
        < dmg_layout_source.index("trap - EXIT INT TERM")
        and dmg_layout_source.index("trap - EXIT INT TERM")
        < dmg_layout_source.index(dmg_layout_ready_branch, dmg_layout_source.index("trap - EXIT INT TERM")),
    )
    notarize_source = (
        (root / "packaging" / "notarize.sh").read_text(encoding="utf-8")
        if (root / "packaging" / "notarize.sh").is_file()
        else ""
    )
    layout_call = '"$script_root/verify_dmg_layout.sh" "$dmg_path" >/dev/null'
    notary_submission = "xcrun " + "notary" + "tool submit"
    check(
        checks,
        "NOTARIZE_LAYOUT_PRE_SUBMIT",
        layout_call in notarize_source
        and notary_submission in notarize_source
        and notarize_source.index(layout_call) < notarize_source.index(notary_submission),
    )
    mounted_helper_call = '"$script_root/verify_embedded_helper.sh" "$mounted_app" >/dev/null'
    mounted_release_call = '"$script_root/verify_release.sh" "$mounted_app" >/dev/null'
    check(
        checks,
        "NOTARIZE_INTERNAL_SUCCESS_OUTPUT_SCOPED",
        mounted_helper_call in notarize_source
        and mounted_release_call in notarize_source
        and notarize_source.index(mounted_helper_call) < notarize_source.index(notary_submission)
        and notarize_source.index(mounted_release_call) < notarize_source.index(notary_submission),
    )
    check(
        checks,
        "NOTARIZE_TOOL_PREFLIGHT",
        (tool_preflight := "for command_name in hdiutil " + "code" + "sign spctl xcrun; do") in notarize_source
        and notarize_source.index(tool_preflight)
        < notarize_source.index('hdiutil imageinfo "$dmg_path"'),
    )
    hardlink_gate = 'if ! hardlink_count="$(/usr/bin/python3 - "$dmg_path"'
    check(
        checks,
        "NOTARIZE_DMG_HARDLINK_GUARD",
        hardlink_gate in notarize_source
        and "os.stat(sys.argv[1], follow_symlinks=False).st_nlink" in notarize_source
        and 'notarize:FAIL:dmg_hardlink' in notarize_source
        and notarize_source.index(hardlink_gate) < notarize_source.index('hdiutil imageinfo "$dmg_path"'),
    )
    check(
        checks,
        "NOTARIZE_FILE_FINGERPRINT_FD_BOUND",
        notarize_source.count(
            "descriptor = os.open(sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW)"
        )
        == 2
        and notarize_source.count("metadata_before = os.fstat(descriptor)") == 2
        and notarize_source.count("metadata_after = os.fstat(descriptor)") == 2
        and notarize_source.count("path_metadata = os.stat(sys.argv[1], follow_symlinks=False)")
        == 2
        and notarize_source.count(
            "stable_metadata(metadata_before) != stable_metadata(metadata_after)"
        )
        == 2
        and notarize_source.count(
            "stable_metadata(metadata_after) != stable_metadata(path_metadata)"
        )
        == 2,
    )
    final_cleanup_call = "\ncleanup\n"
    final_cleanup_guard = "if (( cleanup_failed == 1 )); then"
    check(
        checks,
        "NOTARIZE_FINAL_CLEANUP_BEFORE_READY",
        "cleanup_failed=0" in notarize_source
        and final_cleanup_call in notarize_source
        and final_cleanup_guard in notarize_source
        and "notarize:FAIL:final_cleanup_failed" in notarize_source
        and "trap - EXIT" in notarize_source
        and notarize_source.index("notarize_completed=1")
        < notarize_source.index(final_cleanup_call, notarize_source.index("notarize_completed=1"))
        and notarize_source.index(final_cleanup_call, notarize_source.index("notarize_completed=1"))
        < notarize_source.index(final_cleanup_guard)
        and notarize_source.index(final_cleanup_guard) < notarize_source.index("trap - EXIT")
        and notarize_source.index("trap - EXIT")
        < notarize_source.index('print -- "release_evidence:RECORDED"'),
    )
    notarize_lock = 'notarize_lock="${dmg_path}.notarize.lock"'
    check(
        checks,
        "NOTARIZE_MUTATION_LOCK",
        notarize_lock in notarize_source
        and 'if ! mkdir "$notarize_lock" 2>/dev/null; then' in notarize_source
        and "notarize:FAIL:already_running" in notarize_source
        and "notarize_lock_acquired=1" in notarize_source
        and 'notarize_lock_created_identity=""' in notarize_source
        and "record_notarize_lock_identity()" in notarize_source
        and "notarize_lock_is_owned()" in notarize_source
        and 'stat -f \'%d:%i\' -- "$notarize_lock"' in notarize_source
        and 'if (( notarize_lock_acquired == 1 )) && notarize_lock_is_owned; then' in notarize_source
        and 'rmdir "$notarize_lock"' in notarize_source
        and notarize_source.index('if ! mkdir "$notarize_lock" 2>/dev/null; then')
        < notarize_source.index('xcrun stapler staple'),
    )
    check(
        checks,
        "NOTARIZE_STALE_LOCK_DIAGNOSTIC",
        'notarize_lock_owner="$notarize_lock/owner"' in notarize_source
        and 'print -r -- "$$" > "$notarize_lock_owner"' in notarize_source
        and 'kill -0 "$owner_pid"' in notarize_source
        and "notarize:FAIL:stale_lock" in notarize_source
        and "notarize:HINT:stale_lock:verify_no_notarization_then_remove_lock" in notarize_source
        and 'rm -f -- "$notarize_lock_owner"' in notarize_source,
    )
    check(
        checks,
        "NOTARIZE_LOCK_OWNER_ATOMIC",
        "set -o noclobber" in notarize_source
        and 'print -r -- "$$" > "$notarize_lock_owner"' in notarize_source
        and "notarize:FAIL:lock_owner_write" in notarize_source
        and notarize_source.index("set -o noclobber") < notarize_source.index('print -r -- "$$" > "$notarize_lock_owner"'),
    )
    check(
        checks,
        "NOTARIZE_EVIDENCE_EXISTS_REDACTION",
        'notarize:FAIL:evidence_exists' in notarize_source
        and 'notarize:HINT:evidence_exists:review_or_remove_existing_evidence_then_retry' in notarize_source
        and "Refusing to overwrite existing release evidence:" not in notarize_source
        and notarize_source.index('notarize:FAIL:evidence_exists')
        < notarize_source.index('hdiutil imageinfo "$dmg_path"'),
    )
    evidence_cleanup = (
        'if (( evidence_written == 1 )) && evidence_is_owned; then\n'
        '    rm -f -- "$evidence_path" >/dev/null 2>&1 || cleanup_failed=1\n'
        '  fi'
    )
    check(
        checks,
        "NOTARIZE_EVIDENCE_CLEANUP_ON_UNMOUNT",
        "evidence_written=0" in notarize_source
        and "evidence_written=1" in notarize_source
        and evidence_cleanup in notarize_source
        and notarize_source.index("evidence_written=1")
        < notarize_source.index(evidence_cleanup, notarize_source.index("evidence_written=1"))
        and notarize_source.index(evidence_cleanup, notarize_source.index("evidence_written=1"))
        < notarize_source.rindex("notarize:FAIL:dmg_unmount_failed"),
    )
    interrupt_cleanup = (
        'if (( evidence_written == 1 && notarize_completed == 0 )) && evidence_is_owned; then\n'
        '    rm -f -- "$evidence_path" >/dev/null 2>&1 || cleanup_failed=1\n'
        '  fi'
    )
    check(
        checks,
        "NOTARIZE_EVIDENCE_CLEANUP_ON_INTERRUPT",
        "notarize_completed=0" in notarize_source
        and "notarize_completed=1" in notarize_source
        and 'evidence_created_identity=""' in notarize_source
        and "record_evidence_identity()" in notarize_source
        and "evidence_is_owned()" in notarize_source
        and interrupt_cleanup in notarize_source
        and notarize_source.index(interrupt_cleanup) < notarize_source.index("notarize_completed=1"),
    )

    passed = all(item["state"] == "PASS" for item in checks)
    return checks, passed


def make_report(checks: list[dict[str, str]], passed: bool) -> dict[str, object]:
    python_runtime_missing = any(
        item == {"state": "FAIL", "code": "OFFLINE_PYTHON_RUNTIME"} for item in checks
    )
    return {
        "schema_version": 1,
        "tool": "photos-local-keyword-indexer",
        "status": "ready" if passed else "blocked",
        "checks": checks,
        "release_gates": RELEASE_GATES,
        "next_action": (
            "run_signed_release_preflight"
            if passed
            else "use_python_3_11_or_newer"
            if python_runtime_missing
            else "review_offline_contract"
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args()
    checks, passed = validate(ROOT)
    report = make_report(checks, passed)
    if args.as_json:
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":")))
    else:
        for item in checks:
            print(f"offline_contract:{item['state']}:{item['code']}")
        print(f"offline_contract:{report['status'].upper()}:source_contract")
        print(f"offline_contract:NEXT:{report['next_action']}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
