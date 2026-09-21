"""Behavior contract for the open semantic inference policy (v3)."""

from __future__ import annotations

import unittest

from photos_indexer.taxonomy import proposed_keywords


class SemanticKeywordPolicyV3Tests(unittest.TestCase):
    def test_accepts_open_semantic_document_inferences(self) -> None:
        candidates = [
            "prescripción óptica",
            "receta de lentes",
            "graduación de lentes",
            "documento médico",
            "texto manuscrito",
            "óptica",
            "formulario",
            "oftalmología",
        ]

        self.assertEqual(proposed_keywords([], candidates), candidates)

    def test_accepts_open_semantic_categories_without_a_curated_allowlist(self) -> None:
        candidates = [
            "miopía",
            "examen visual",
            "documentación clínica",
            "instrucciones de montaje",
            "reunión de trabajo",
            "persona leyendo",
            "ropa de bebé",
            "cabello oscuro",
        ]

        self.assertEqual(proposed_keywords([], candidates), candidates)

    def test_rejects_literal_personal_and_numeric_content(self) -> None:
        candidates = [
            "Kevin Ríos",
            "Ximena",
            "kevin@example.com",
            "https://example.com/expediente",
            "+52 55 1234 5678",
            "2026-08-31",
            "expediente 123456",
            "OD -2.50",
            "Eje 90",
            "19.4326,-99.1332",
            "prescripción óptica",
        ]

        self.assertEqual(proposed_keywords([], candidates), ["prescripción óptica"])

    def test_open_inference_still_deduplicates_and_preserves_existing_keywords(self) -> None:
        self.assertEqual(
            proposed_keywords(
                ["Documento médico"],
                ["documento médico", "prescripción óptica", "PRESCRIPCIÓN ÓPTICA"],
            ),
            ["prescripción óptica"],
        )


if __name__ == "__main__":
    unittest.main()
