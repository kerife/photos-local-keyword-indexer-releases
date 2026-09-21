from __future__ import annotations

import re
import base64
import json
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DocumentationContractTests(unittest.TestCase):
    def test_unsigned_beta_notes_preserve_the_safe_installation_contract(self) -> None:
        notes = (
            PROJECT_ROOT / "docs" / "release" / "unsigned-beta-0.1.0-1.md"
        ).read_text(encoding="utf-8")
        normalized = " ".join(notes.split())

        for phrase in (
            "Unsigned public beta",
            "Apple Silicon beta for macOS 14 or later",
            "not signed with Apple Developer ID",
            "notarized by Apple",
            "Do not disable Gatekeeper, SIP, or AMFI",
            "Open Anyway",
            "ollama pull qwen3-vl:4b",
            "A dry-run does not modify Photos",
            "Compare the local thumbnail",
            "No automatic Sparkle update feed",
        ):
            self.assertIn(phrase, normalized)
        self.assertNotIn("xattr -dr", notes)
        self.assertNotIn("spctl --master-disable", notes)

        public_readme = (
            PROJECT_ROOT / "docs" / "release" / "public-downloads-readme.md"
        ).read_text(encoding="utf-8")
        public_normalized = " ".join(public_readme.split())
        for phrase in (
            "application source under the MIT license",
            "unsigned, non-notarized public beta",
            "Do not disable Gatekeeper, SIP, or AMFI",
            "ollama pull qwen3-vl:4b",
            "against the local thumbnail",
        ):
            self.assertIn(phrase, public_normalized)

    def test_public_beta_docs_link_privacy_support_and_project_license(self) -> None:
        privacy = (PROJECT_ROOT / "docs" / "privacy.md").read_text(encoding="utf-8")
        support = (PROJECT_ROOT / "docs" / "support.md").read_text(encoding="utf-8")
        notices = (PROJECT_ROOT / "docs" / "release" / "third-party-notices.md").read_text(encoding="utf-8")
        license_text = (PROJECT_ROOT / "LICENSE").read_text(encoding="utf-8")

        self.assertIn("Automatización → Fotos para leer metadatos y exportar", privacy)
        self.assertIn("solo usa setters", privacy)
        self.assertIn("posibilidad de\nejecutar rollback", privacy)
        self.assertIn("doctor --json", support)
        self.assertIn("No adjuntes fotos", support)
        self.assertIn("MIT", license_text)
        self.assertIn("no relicencia", notices)
        self.assertIn("terceros", notices)

    def test_unpublished_candidate_has_no_fabricated_artifact_evidence(self) -> None:
        candidate = (
            PROJECT_ROOT / "docs" / "release" / "unsigned-beta-0.1.1-2.md"
        ).read_text(encoding="utf-8")

        self.assertIn("UNPUBLISHED", candidate)
        self.assertIn("no identifica un DMG público", candidate)
        self.assertIn("READY_FOR_ACCEPTANCE", candidate)
        self.assertIn("siguen pendientes", candidate)
        self.assertIn("unsigned, non-notarized public beta", candidate)
        self.assertIn("Las actualizaciones serán\nmanuales", candidate)
        self.assertIn("Sparkle seguirá deshabilitado", candidate)
        self.assertNotRegex(candidate, r"(?i)SHA-256:\\s*[0-9a-f]{64}")

    def test_user_facing_docs_present_the_public_beta_without_claiming_an_official_release(self) -> None:
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        guide = (PROJECT_ROOT / "docs" / "user-guide.md").read_text(encoding="utf-8")
        release = (PROJECT_ROOT / "docs" / "release.md").read_text(encoding="utf-8")

        release_url = (
            "https://github.com/kerife/photos-local-keyword-indexer-releases/"
            "releases/tag/v0.1.0-beta.1"
        )
        for document in (readme, guide, release):
            normalized = " ".join(document.split())
            self.assertIn(release_url, document)
            self.assertRegex(normalized, r"(?i)sin firma Developer ID|no está firmada con\s+Developer ID")
            self.assertRegex(normalized, r"(?i)(?:sin|ni) notarización|no está.*notarizada")
        self.assertIn("beta pública", release)
        self.assertNotIn("Descarga el DMG oficial", readme)
        self.assertNotIn("Instálala desde el DMG oficial", guide)
        self.assertIn("dmg_layout:READY_DEV", release)
        self.assertIn("run_verify_public_beta_before_any_authorized_publication", release)
        self.assertIn("notarize:FAIL:development_dmg_not_releasable", release)

    def test_release_guide_distinguishes_static_gates_from_real_release_evidence(self) -> None:
        release = (PROJECT_ROOT / "docs" / "release.md").read_text(encoding="utf-8")

        for phrase in (
            "tests/test_release_hardening.py",
            "packaging/verify_release.sh",
            "packaging/notarize.sh",
            "smoke:passed",
            "smoke:disabled",
            "Automatización → Fotos",
            "Acceso total al disco",
        ):
            self.assertIn(phrase, release)
        self.assertIn("No se debe interpretar un build local", release)
        self.assertIn("release_preflight:HINT:", release)
        self.assertIn("release_preflight:NEXT:", release)
        self.assertRegex(
            release,
            re.compile(r"APP_VERSION='<versión semver>' BUILD_NUMBER='<build positivo>' \\\n+\s+DEVELOPER_ID_APPLICATION='<identidad de firma>' \\\n+\s+APPLE_NOTARY_PROFILE='<perfil local>' \\\n+\s+SPARKLE_FEED_URL='<feed HTTPS real>' \\\n+\s+SPARKLE_PUBLIC_ED_KEY='<clave-publica-ed25519>' \\\n+\s+packaging/release_preflight\.sh"),
        )
        self.assertIn("release_metadata_missing", release)

    def test_release_guide_documents_the_explicit_local_dev_dmg_command(self) -> None:
        release = (PROJECT_ROOT / "docs" / "release.md").read_text(encoding="utf-8")

        command_pattern = re.compile(
            r'RELEASE_BUILD=0 \\\n\s+packaging/build_dmg\.sh "\$PWD/build/app/PhotosLocalKeywordIndexer\.app"'
        )
        match = command_pattern.search(release)
        self.assertIsNotNone(match)
        assert match is not None
        command_position = match.start()
        nearby = release[command_position : command_position + 700]
        self.assertIn("solo para pruebas locales", nearby)
        self.assertIn("no se debe compartir", nearby)
        self.assertIn("release_dmg:READY_DEV", nearby)

    def test_release_guide_distinguishes_the_provisioned_sparkle_key_from_the_feed_template(self) -> None:
        release = (PROJECT_ROOT / "docs" / "release.md").read_text(encoding="utf-8")
        config = json.loads((PROJECT_ROOT / "packaging" / "sparkle-config.json").read_text(encoding="utf-8"))

        self.assertIn("feed sigue siendo una plantilla", release)
        self.assertIn("SPARKLE_PUBLIC_ED_KEY", release)
        self.assertNotIn("REPLACE_WITH", config["public_ed25519_key"])
        self.assertEqual(len(base64.b64decode(config["public_ed25519_key"], validate=True)), 32)
        self.assertRegex(config["keychain_account"], r"^[A-Za-z0-9._-]{1,64}$")
        self.assertIn('generate_appcast --account "$SPARKLE_KEY_ACCOUNT"', release)

    def test_readme_documents_external_ollama_model_storage_boundary(self) -> None:
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("OLLAMA_MODELS", readme)
        self.assertIn("proceso que inicia Ollama", readme)
        self.assertIn("La app no crea, mueve ni elimina modelos", readme)

    def test_user_guide_documents_external_ollama_model_storage_boundary(self) -> None:
        guide = (PROJECT_ROOT / "docs" / "user-guide.md").read_text(encoding="utf-8")

        self.assertIn("OLLAMA_MODELS", guide)
        self.assertIn("proceso que inicia Ollama", guide)
        self.assertIn("La app no configura", guide)

    def test_external_ollama_storage_documents_finder_launch_boundary(self) -> None:
        documents = [
            (PROJECT_ROOT / "README.md").read_text(encoding="utf-8"),
            (PROJECT_ROOT / "docs" / "user-guide.md").read_text(encoding="utf-8"),
            (PROJECT_ROOT / "docs" / "release.md").read_text(encoding="utf-8"),
        ]

        for document in documents:
            self.assertIn("ollama serve", document)
            self.assertIn("ollama list", document)
            self.assertIn("OLLAMA_MODELS", document)
            self.assertIn("Finder", document)
            self.assertIn("launchctl", document)
            self.assertIn("OLLAMA_NO_CLOUD", document)
            self.assertIn("blobs", document)
            self.assertIn("manifests", document)
            self.assertLess(
                document.index("open -a Ollama"),
                document.index("launchctl unsetenv OLLAMA_MODELS"),
            )

    def test_release_guide_documents_external_ollama_storage_and_release_preflight_boundary(self) -> None:
        release = (PROJECT_ROOT / "docs" / "release.md").read_text(encoding="utf-8")
        preflight = (PROJECT_ROOT / "packaging" / "release_preflight.sh").read_text(encoding="utf-8")

        self.assertIn("OLLAMA_MODELS", release)
        self.assertIn("OLLAMA_NO_CLOUD", release)
        self.assertIn("ollama list", release)
        self.assertIn("disco debe estar montado", release)
        self.assertIn("no inicia Ollama", release)
        self.assertNotIn("OLLAMA_MODELS", preflight)

    def test_readme_documents_random_selection_privacy_boundary(self) -> None:
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("--random", readme)
        self.assertIn("strategy: random", readme)
        self.assertIn("UUID abreviados", readme)
        self.assertIn("manifiesto `0600`", readme)

    def test_user_guide_documents_random_cli_selection(self) -> None:
        guide = (PROJECT_ROOT / "docs" / "user-guide.md").read_text(encoding="utf-8")

        self.assertIn("--random", guide)
        self.assertIn("strategy: random", guide)

    def test_release_guide_is_honest_about_photoscript_on_macos26(self) -> None:
        release = (PROJECT_ROOT / "docs" / "release.md").read_text(encoding="utf-8")
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

        self.assertIn("PhotoScript 0.5.3", release)
        self.assertRegex(release, r"compatibilidad acotada por versión y hash")
        self.assertRegex(release, r"no modifica la\s+dependencia instalada")
        self.assertIn("se conserva mientras falle cualquier preflight", release)
        self.assertIn("previous_helper_preserved_not_release_ready", release)
        self.assertIn("check_photoscript_and_macos_compatibility_before_retry", release)
        self.assertIn("script exacto de PhotoScript 0.5.3", readme)
        self.assertIn("cualquier fuente distinta del artefacto 0.5.3 auditado queda", readme)
        self.assertIn("fail-closed", readme)

    def test_release_guide_states_swiftpm_offline_test_boundary(self) -> None:
        release = (PROJECT_ROOT / "docs" / "release.md").read_text(encoding="utf-8")

        self.assertIn("swift test --disable-automatic-resolution", release)
        self.assertIn("falla cerrado", release)
        self.assertRegex(release, r"No se usa un\s+stub")

    def test_release_guide_reuses_the_configured_python_for_the_offline_contract(self) -> None:
        release = (PROJECT_ROOT / "docs" / "release.md").read_text(encoding="utf-8")

        command = '"${PYTHON_BIN:-python3.12}" packaging/verify_offline_release_contract.py --json'
        self.assertEqual(release.count(command), 2)
        self.assertNotIn("python3.11 packaging/verify_offline_release_contract.py", release)

    def test_release_guide_uses_absolute_dmg_paths(self) -> None:
        release = (PROJECT_ROOT / "docs" / "release.md").read_text(encoding="utf-8")

        self.assertNotRegex(release, r"verify_dmg_layout\.sh\s+dist/")
        self.assertNotRegex(release, r"notarize\.sh\s+dist/")
        self.assertNotRegex(release, r"stapler validate\s+dist/")

    def test_release_guide_leaves_release_signing_to_build_dmg_after_helper_freshness_check(self) -> None:
        """A separately signed helper no longer matches the unsigned build artifact."""
        release = (PROJECT_ROOT / "docs" / "release.md").read_text(encoding="utf-8")

        self.assertIn("`build_dmg.sh` firma la app", release)
        self.assertIn("No ejecutes `sign_app.sh` por separado", release)
        self.assertNotIn("packaging/sign_app.sh /ruta/PhotosLocalKeywordIndexer.app", release)

    def test_user_guide_documents_caption_rollback_safety(self) -> None:
        guide = (PROJECT_ROOT / "docs" / "user-guide.md").read_text(encoding="utf-8")
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        release = (PROJECT_ROOT / "docs" / "release.md").read_text(encoding="utf-8")
        for document in (guide, readme, release):
            self.assertIn("caption aplicado por ese run", document)
            self.assertIn("coincide exactamente", document)
            self.assertIn("marca `uncertain`", document)

    def test_reviewed_selection_is_documented_as_fixed(self) -> None:
        documents = [
            (PROJECT_ROOT / "README.md").read_text(encoding="utf-8"),
            (PROJECT_ROOT / "docs" / "user-guide.md").read_text(encoding="utf-8"),
        ]

        for document in documents:
            normalized = " ".join(document.split())
            self.assertIn("las selecciones aprobadas quedan fijadas", normalized)
            self.assertIn("Para cambiar la selección, ejecuta un dry-run nuevo", normalized)
            self.assertNotIn("para que puedas continuar o ajustarlas", normalized)


if __name__ == "__main__":
    unittest.main()
