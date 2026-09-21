from __future__ import annotations

import ast
import tomllib
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = PROJECT_ROOT / "src" / "photos_indexer"


class ProjectPackagingTests(unittest.TestCase):
    def test_development_tooling_and_console_entrypoint_are_declared(self) -> None:
        configuration = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(
            configuration["project"]["scripts"]["photos-indexer"],
            "photos_indexer.cli:main",
        )
        development = "\n".join(configuration["project"]["optional-dependencies"]["dev"])
        for dependency in ("pytest", "pytest-cov", "ruff"):
            self.assertIn(dependency, development)
        self.assertEqual(configuration["tool"]["ruff"]["target-version"], "py311")
        self.assertEqual(configuration["tool"]["pytest"]["ini_options"]["testpaths"], ["tests"])
        self.assertFalse(configuration["tool"]["coverage"]["run"]["branch"])

    def test_python_and_coverage_scope_match_the_supported_contract(self) -> None:
        configuration = tomllib.loads((PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8"))

        self.assertEqual(configuration["project"]["requires-python"], ">=3.11,<4")
        self.assertEqual(
            configuration["tool"]["coverage"]["run"]["source"],
            [
                "photos_indexer.taxonomy",
                "photos_indexer.models",
                "photos_indexer.manifest",
                "photos_indexer.workflows",
            ],
        )


class StaticPrivacyBoundaryTests(unittest.TestCase):
    def _modules(self) -> list[tuple[Path, ast.Module]]:
        return [
            (path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path)))
            for path in sorted(SOURCE_ROOT.rglob("*.py"))
        ]

    def test_source_does_not_import_photos_database_readers(self) -> None:
        prohibited_roots = {"sqlite", "sqlite3", "osxphotos"}
        violations: list[str] = []

        for path, module in self._modules():
            for node in ast.walk(module):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [alias.name for alias in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    if name.split(".", 1)[0].casefold() in prohibited_roots:
                        violations.append(f"{path.name}:{node.lineno}:{name}")

        self.assertEqual(violations, [])

    def test_source_does_not_reference_photos_library_internals(self) -> None:
        prohibited_fragments = (
            "photos.sqlite",
            ".photoslibrary",
            "/system/volumes/data/media/photodata",
            "/library/containers/com.apple.photos",
        )
        violations: list[str] = []

        for path, module in self._modules():
            for node in ast.walk(module):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    lowered = node.value.casefold()
                    if any(fragment in lowered for fragment in prohibited_fragments):
                        violations.append(f"{path.name}:{node.lineno}")

        self.assertEqual(violations, [])

    def test_source_contains_no_forbidden_ollama_mutation_endpoint(self) -> None:
        prohibited_endpoints = ("/pull", "/create", "/delete")
        allowed_request_paths = {"/version", "/tags", "/show", "/chat"}
        violations: list[str] = []

        for path, module in self._modules():
            for node in ast.walk(module):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    lowered = node.value.casefold()
                    if any(endpoint in lowered for endpoint in prohibited_endpoints):
                        violations.append(f"{path.name}:{node.lineno}")
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "_request"
                    and len(node.args) >= 2
                ):
                    endpoint = node.args[1]
                    if not isinstance(endpoint, ast.Constant) or endpoint.value not in allowed_request_paths:
                        violations.append(f"{path.name}:{node.lineno}:non-allowlisted request")

        self.assertEqual(violations, [])

    def test_source_http_urls_are_fixed_to_the_ollama_loopback(self) -> None:
        violations: list[str] = []

        for path, module in self._modules():
            for node in ast.walk(module):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    value = node.value.casefold()
                    if value.startswith(("http://", "https://")) and value != "http://127.0.0.1:11434/api":
                        violations.append(f"{path.name}:{node.lineno}:{node.value}")

        self.assertEqual(violations, [])


if __name__ == "__main__":
    unittest.main()
