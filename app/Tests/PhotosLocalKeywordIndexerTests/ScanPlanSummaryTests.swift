import XCTest
@testable import PhotosLocalKeywordIndexer

final class ScanPlanSummaryTests: XCTestCase {
    func testPlanSummaryMakesTheDryRunScopeVisible() {
        let summary = ScanPlanSummary(
            photoLimit: 20,
            modelText: "Adaptativa: qwen3-vl:4b y qwen3-vl:8b",
            appleMapsEnabled: true,
            captionsEnabled: false
        )

        XCTAssertEqual(summary.scopeText, "Hasta 20 fotos recientes elegibles")
        XCTAssertEqual(summary.modelText, "Adaptativa: qwen3-vl:4b y qwen3-vl:8b")
        XCTAssertEqual(summary.modelLabelText, "Modelo: Adaptativa: qwen3-vl:4b y qwen3-vl:8b")
        XCTAssertEqual(summary.contextText, "Apple Maps: activado para fotos con ubicación")
        XCTAssertEqual(summary.outputText, "Keywords: propuestas · Captions: desactivados")
        XCTAssertEqual(
            summary.accessibilityLabel,
            "Plan de ejecución. Hasta 20 fotos recientes elegibles. Modelo: Adaptativa: qwen3-vl:4b y qwen3-vl:8b. Apple Maps: activado para fotos con ubicación. Keywords: propuestas · Captions: desactivados. El análisis es dry-run y no modifica Apple Fotos."
        )
    }

    func testPlanSummaryUsesSingularAndStatesWhenOptionalContextIsOff() {
        let summary = ScanPlanSummary(
            photoLimit: 1,
            modelText: "qwen3-vl:4b",
            appleMapsEnabled: false,
            captionsEnabled: true
        )

        XCTAssertEqual(summary.scopeText, "1 foto reciente elegible")
        XCTAssertEqual(summary.contextText, "Apple Maps: desactivado")
        XCTAssertEqual(summary.outputText, "Keywords: propuestas · Captions: propuestas para revisión")
        XCTAssertFalse(summary.outputText.contains("escribir"))
    }

    func testPlanSummaryDistinguishesRecentFromRandomSelection() {
        let recent = ScanPlanSummary(
            photoLimit: 10,
            modelText: "qwen3-vl:4b",
            appleMapsEnabled: false,
            captionsEnabled: false,
            randomSelection: false
        )
        let random = ScanPlanSummary(
            photoLimit: 10,
            modelText: "qwen3-vl:4b",
            appleMapsEnabled: false,
            captionsEnabled: false,
            randomSelection: true
        )

        XCTAssertEqual(recent.scopeText, "Hasta 10 fotos recientes elegibles")
        XCTAssertEqual(random.scopeText, "Hasta 10 fotos elegibles seleccionadas al azar")
        XCTAssertTrue(random.accessibilityLabel.contains("seleccionadas al azar"))
        XCTAssertFalse(recent.accessibilityLabel.contains("azar"))
    }

    func testAccessibilityPlanStatesAdaptiveRoutingDecision() {
        let summary = ScanPlanSummary(
            photoLimit: 20,
            modelText: "Modelos adaptativos: rápido qwen3-vl:4b · detallado qwen3-vl:8b",
            modelRoutingText: "Calidad adaptativa: usa el modelo rápido sin GPS y el detallado cuando hay ubicación; ambos deben estar instalados.",
            appleMapsEnabled: true,
            captionsEnabled: true
        )

        XCTAssertTrue(summary.accessibilityLabel.contains("usa el modelo rápido sin GPS"))
        XCTAssertTrue(summary.accessibilityLabel.contains("detallado cuando hay ubicación"))
        XCTAssertTrue(summary.accessibilityLabel.contains("Apple Maps: activado para fotos con ubicación"))
        XCTAssertTrue(summary.accessibilityLabel.contains("Captions: propuestas para revisión"))
    }

    func testPlanSummaryClampsInvalidLimitToSafeDisplayValue() {
        let summary = ScanPlanSummary(
            photoLimit: 0,
            modelText: "",
            appleMapsEnabled: false,
            captionsEnabled: false
        )

        XCTAssertEqual(summary.scopeText, "Fotos recientes elegibles")
        XCTAssertEqual(summary.modelText, "Modelo local pendiente de configurar")
    }
}
