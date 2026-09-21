from __future__ import annotations

import unittest

from photos_indexer.runtime_identity import RuntimeIdentity, current_runtime_identity


class RuntimeIdentityTests(unittest.TestCase):
    def test_development_identity_is_bounded_to_authorization_relevant_details(self) -> None:
        identity = current_runtime_identity(
            executable="/Users/example/project/.venv/bin/python3.14",
            python_version="3.14.6",
            architecture="arm64",
            packaged=False,
        )

        self.assertEqual(
            identity,
            RuntimeIdentity(
                executable_hint="project/.venv/bin/python3.14",
                python_version="3.14.6",
                architecture="arm64",
                packaged=False,
            ),
        )
        self.assertIn("project/.venv/bin/python3.14", identity.authorization_hint)
        self.assertIn("Python 3.14.6", identity.authorization_hint)
        self.assertIn("arm64", identity.authorization_hint)
        self.assertIn("host", identity.authorization_hint.casefold())
        self.assertNotIn("/Users/example", identity.authorization_hint)
        self.assertIn("PhotoKit", identity.permission_hint("photos"))
        self.assertNotIn("Automatización", identity.permission_hint("photos"))
        self.assertIn("Automatización", identity.permission_hint("automation"))

        with self.assertRaises(ValueError):
            identity.permission_hint("unknown")

    def test_packaged_identity_names_the_signed_app_helper_without_a_python_path(self) -> None:
        identity = current_runtime_identity(
            executable="/private/tmp/PhotosIndexerWorker",
            python_version="3.12.9",
            architecture="arm64",
            packaged=True,
        )

        self.assertEqual(identity.executable_hint, "PhotosIndexerWorker")
        self.assertIn("Photos Local Keyword Indexer", identity.authorization_hint)
        self.assertIn("helper incluido", identity.authorization_hint)
        self.assertIn("PhotosIndexerWorker", identity.authorization_hint)
        self.assertEqual(identity.permission_target("photos"), "Photos Local Keyword Indexer / PhotosIndexerWorker")
        self.assertEqual(identity.permission_target("automation"), "Photos Local Keyword Indexer / PhotosIndexerWorker")
        self.assertNotIn("/private/tmp", identity.authorization_hint)

    def test_invalid_inputs_fail_closed_instead_of_describing_an_unknown_runtime(self) -> None:
        with self.assertRaises(ValueError):
            current_runtime_identity(executable="", python_version="3.14.6", architecture="arm64", packaged=False)
        with self.assertRaises(ValueError):
            current_runtime_identity(executable="/usr/bin/python", python_version="", architecture="arm64", packaged=False)
        with self.assertRaises(ValueError):
            current_runtime_identity(executable="/usr/bin/python", python_version="3.14.6", architecture="", packaged=False)

    def test_runtime_hint_replaces_unsafe_executable_text_and_stays_bounded(self) -> None:
        identity = current_runtime_identity(
            executable="/" + ("x" * 400) + "/\ud800",
            python_version="3.14.6",
            architecture="arm64",
            packaged=False,
        )

        self.assertNotIn("\ud800", identity.executable_hint)
        self.assertNotIn("\ud800", identity.permission_hint("photos"))
        self.assertLessEqual(len(identity.executable_hint), 256)


if __name__ == "__main__":
    unittest.main()
