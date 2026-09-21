import XCTest
@testable import PhotosLocalKeywordIndexer

final class SettingsModelsTests: XCTestCase {
    func testBuildChannelAcceptsOnlyTheBoundedInfoPlistValues() {
        XCTAssertEqual(BuildChannel.from(infoValue: "development"), .development)
        XCTAssertEqual(BuildChannel.from(infoValue: " release "), .release)
        XCTAssertEqual(BuildChannel.from(infoValue: "READY_DEV"), .unknown)
        XCTAssertEqual(BuildChannel.from(infoValue: ["development"]), .unknown)
        XCTAssertTrue(BuildChannel.development.detail.contains("Beta pública sin firma Developer ID ni notarización"))
        XCTAssertTrue(BuildChannel.release.detail.contains("verificarse por separado"))
        XCTAssertEqual(BuildChannelCopy.infoPlistKey, "PhotosLocalKeywordIndexerBuildChannel")
        XCTAssertTrue(BuildChannel.development.accessibilityLabel.hasPrefix("Build de desarrollo."))
        XCTAssertEqual(BuildChannel.unknown.symbolName, "questionmark.diamond")
    }

    func testSettingsPathCopyUsesHomeAbbreviationAndBoundsLongValues() {
        XCTAssertEqual(
            SettingsPathCopy.display(
                "/Users/test/Library/Application Support/Photos Local Keyword Indexer/runs",
                homePath: "/Users/test"
            ),
            "~/Library/Application Support/Photos Local Keyword Indexer/runs"
        )
        let longPath = "/Users/test/" + String(repeating: "nested/", count: 30) + "manifest.json"
        let compact = SettingsPathCopy.display(longPath, homePath: "/Users/test")
        XCTAssertEqual(compact.count, 96)
        XCTAssertTrue(compact.hasPrefix("…/"))
        XCTAssertFalse(compact.contains("/Users/test"))
    }

    func testSettingsScopeCopySeparatesReadOnlyEvidenceFromEditableUpdatePreference() {
        XCTAssertTrue(SettingsScopeCopy.detail.contains("Rutas, modelos y privacidad"))
        XCTAssertTrue(SettingsScopeCopy.detail.contains("solo lectura"))
        XCTAssertTrue(SettingsScopeCopy.detail.contains("actualizaciones puede cambiarse"))
        XCTAssertFalse(SettingsScopeCopy.detail.contains("Configuración es un resumen de solo lectura"))
        XCTAssertTrue(SettingsScopeCopy.accessibilityLabel.contains("preferencia editable"))
        XCTAssertFalse(SettingsScopeCopy.accessibilityLabel.contains("/"))
    }

    func testSmokeTestCopyDoesNotImplyRealLibraryValidation() {
        XCTAssertEqual(SmokeTestSafetyCopy.sectionTitle, "Prueba segura antes de aplicar")
        XCTAssertTrue(SmokeTestSafetyCopy.detail.contains("no ejecuta smoke tests automáticamente"))
        XCTAssertTrue(SmokeTestSafetyCopy.detail.contains("biblioteca de Fotos de prueba"))
        XCTAssertTrue(SmokeTestSafetyCopy.accessibilityLabel.lowercased().contains("no se ha validado automáticamente"))
        XCTAssertFalse(SmokeTestSafetyCopy.detail.contains("/"))
        XCTAssertFalse(SmokeTestSafetyCopy.detail.contains("coordenadas"))
    }

    func testSnapshotShowsLocalPathsAndReadOnlyRuntimeDefaults() {
        let snapshot = SettingsSnapshot(
            paths: SettingsPaths(
                applicationSupport: "/Users/test/Library/Application Support/Photos Local Keyword Indexer",
                runs: "/Users/test/Library/Application Support/Photos Local Keyword Indexer/runs",
                helper: "/Applications/Photos Local Keyword Indexer.app/Contents/Helpers/PhotosIndexerWorker"
            ),
            modelPolicy: "adaptive",
            singleModel: "qwen3-vl:4b",
            fastModel: "qwen3-vl:4b",
            detailedModel: "qwen3-vl:8b",
            appleMapsEnabled: false,
            includeCaptionEnabled: true,
            updateStatus: .configured,
            automaticChecksEnabled: false
        )

        XCTAssertEqual(snapshot.paths.runs, "/Users/test/Library/Application Support/Photos Local Keyword Indexer/runs")
        XCTAssertEqual(snapshot.modelDefaults, ["qwen3-vl:4b", "qwen3-vl:8b"])
        XCTAssertEqual(snapshot.activeModelDefaults, ["qwen3-vl:4b", "qwen3-vl:8b"])
        XCTAssertEqual(snapshot.privacy.appleMapsLabel, "Desactivado por defecto; se decide por ejecución.")
        XCTAssertEqual(snapshot.privacy.captionLabel, "Activado para proponer captions breves.")
        XCTAssertEqual(snapshot.updateStatus.label, "Actualizaciones configuradas")
        XCTAssertFalse(snapshot.automaticChecksEnabled)
    }

    func testSinglePolicyExposesOnlyTheModelThatWillRun() {
        let snapshot = SettingsSnapshot(
            paths: SettingsPaths(applicationSupport: "hidden", runs: "hidden", helper: "hidden"),
            modelPolicy: "single",
            singleModel: "qwen3-vl:4b",
            fastModel: "qwen3-vl:4b",
            detailedModel: "qwen3-vl:8b",
            appleMapsEnabled: false,
            includeCaptionEnabled: false,
            updateStatus: .unconfigured
        )

        XCTAssertEqual(snapshot.activeModelDefaults, ["qwen3-vl:4b"])
        XCTAssertEqual(snapshot.modelPolicyDetail, "Un solo modelo local; se usará en todas las fotos.")
    }

    func testAdaptivePolicyWithOneModelExplainsThatItRunsEverywhere() {
        let snapshot = SettingsSnapshot(
            paths: SettingsPaths(applicationSupport: "hidden", runs: "hidden", helper: "hidden"),
            modelPolicy: "adaptive",
            singleModel: "qwen3-vl:4b",
            fastModel: "qwen3-vl:4b",
            detailedModel: "qwen3-vl:4b",
            appleMapsEnabled: false,
            includeCaptionEnabled: false,
            updateStatus: .unconfigured
        )

        XCTAssertEqual(
            snapshot.modelPolicyDetail,
            "Adaptativa con un modelo local; se usará en todas las fotos."
        )
    }

    func testUnknownPolicyIsNotPresentedAsAdaptive() {
        let snapshot = SettingsSnapshot(
            paths: SettingsPaths(applicationSupport: "hidden", runs: "hidden", helper: "hidden"),
            modelPolicy: "future-policy",
            singleModel: "qwen3-vl:4b",
            fastModel: "qwen3-vl:4b",
            detailedModel: "qwen3-vl:8b",
            appleMapsEnabled: false,
            includeCaptionEnabled: false,
            updateStatus: .unconfigured
        )

        XCTAssertEqual(snapshot.modelPolicyLabel, "Política no válida")
        XCTAssertTrue(snapshot.modelPolicyDetail.contains("no es válida"))
        XCTAssertEqual(snapshot.activeModelDefaults, [])
    }

    func testSupportDiagnosticNamesTheLoopbackOnlyModelRuntime() {
        let snapshot = SettingsSnapshot(
            paths: SettingsPaths(applicationSupport: "hidden", runs: "hidden", helper: "hidden"),
            modelPolicy: "adaptive",
            singleModel: "qwen3-vl:4b",
            fastModel: "qwen3-vl:4b",
            detailedModel: "qwen3-vl:4b",
            appleMapsEnabled: false,
            includeCaptionEnabled: false,
            updateStatus: .unconfigured
        )

        let diagnostic = SettingsSupportDiagnostic(snapshot: snapshot)

        XCTAssertTrue(diagnostic.report.contains("Ollama: solo loopback local; sin servicios cloud."))
        XCTAssertTrue(diagnostic.report.contains("Helper local: No comprobado en esta etapa."))
    }

    func testPrivacySnapshotNeverIncludesCoordinatesOrLocationValues() {
        let privacy = SettingsPrivacySnapshot(appleMapsEnabled: true, includeCaptionEnabled: false)

        XCTAssertEqual(privacy.appleMapsLabel, "Opt-in por ejecución; si se activa, Apple Maps recibe coordenadas.")
        XCTAssertFalse(privacy.appleMapsLabel.contains("latitude"))
        XCTAssertFalse(privacy.appleMapsLabel.contains("longitude"))
        XCTAssertFalse(privacy.appleMapsLabel.contains("19."))
    }

    func testUnconfiguredUpdatesHaveSafeActionLabel() {
        XCTAssertEqual(SettingsUpdateStatus.unconfigured.label, "Actualizaciones no configuradas en esta build")
        XCTAssertFalse(SettingsUpdateStatus.unconfigured.canCheck)
        XCTAssertEqual(SettingsUpdateStatus.unconfigured.symbolName, "exclamationmark.triangle.fill")
        XCTAssertTrue(SettingsUpdateStatus.unconfigured.accessibilityLabel.contains("feed firmado"))
        XCTAssertTrue(SettingsUpdateStatus.unconfigured.manualCheckDetail.contains("build release configurada"))
        XCTAssertEqual(SettingsUpdateStatus.unconfigured.automaticChecksLabel, "No disponible en esta build")
        XCTAssertFalse(SettingsUpdateStatus.unconfigured.manualCheckDetail.contains("Puedes buscar"))
        XCTAssertEqual(SettingsUpdateStatus.configured.symbolName, "checkmark.seal.fill")
        XCTAssertEqual(SettingsUpdateStatus.configured.automaticChecksLabel, "Disponible")
        XCTAssertTrue(SettingsUpdateStatus.configured.manualCheckDetail.contains("Puedes buscar"))
    }

    func testLocalBuildNoticeUsesSpanishProductCopy() {
        XCTAssertEqual(SettingsUpdateCopy.localBuildTitle, "Versión local sin actualizaciones")
        XCTAssertFalse(SettingsUpdateCopy.localBuildTitle.contains("Build"))
    }

    func testConfiguredUpdatesExplainTheirNarrowNetworkScope() {
        let detail = SettingsUpdateStatus.configured.privacyDetail

        XCTAssertTrue(detail.contains("feed HTTPS"))
        XCTAssertTrue(detail.contains("no consultan Fotos"))
        XCTAssertTrue(detail.contains("no descargan modelos"))
        XCTAssertTrue(detail.contains("ni modifican keywords"))
        XCTAssertTrue(detail.contains("ni captions"))
        XCTAssertFalse(detail.contains("/"))
        XCTAssertFalse(detail.contains("coordenadas"))
    }

    func testSparklePublicKeyRequiresCanonicalEd25519Base64() {
        let canonical = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="

        XCTAssertTrue(UpdateService.isValidPublicEdKey(canonical))
        XCTAssertFalse(UpdateService.isValidPublicEdKey("not-a-key"))
        XCTAssertFalse(UpdateService.isValidPublicEdKey("AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=="))
    }

    func testSupportDiagnosticIsUsefulWithoutExposingLocalData() {
        let snapshot = SettingsSnapshot(
            paths: SettingsPaths(
                applicationSupport: "/Users/test/Library/Application Support/Photos Local Keyword Indexer",
                runs: "/Users/test/Library/Application Support/Photos Local Keyword Indexer/runs",
                helper: "/Applications/Photos Local Keyword Indexer.app/Contents/Helpers/PhotosIndexerWorker"
            ),
            modelPolicy: "adaptive",
            singleModel: "qwen3-vl:4b",
            fastModel: "qwen3-vl:4b",
            detailedModel: "qwen3-vl:8b",
            appleMapsEnabled: true,
            includeCaptionEnabled: true,
            updateStatus: .unconfigured
        )
        let diagnostic = SettingsSupportDiagnostic(
            snapshot: snapshot,
            appVersion: "0.1.0",
            operatingSystem: "macOS 26.5"
        )

        XCTAssertTrue(diagnostic.report.contains("Política de modelos: Adaptativa"))
        XCTAssertTrue(diagnostic.report.contains("Apple Maps: activado por ejecución"))
        XCTAssertTrue(diagnostic.report.contains("Captions: activados"))
        XCTAssertTrue(diagnostic.report.contains("Preparación — Fotos: Pendiente; Automatización: Pendiente; Ollama: Pendiente; Modelos: Pendiente"))
        XCTAssertTrue(diagnostic.report.contains("sin rutas ni datos de fotos"))
        XCTAssertFalse(diagnostic.report.contains("/Users/test"))
        XCTAssertFalse(diagnostic.report.contains("PhotosIndexerWorker"))
        XCTAssertFalse(diagnostic.report.contains("caption propuesto"))
    }

    func testSupportDiagnosticRedactsPathLikeMetadata() {
        let snapshot = SettingsSnapshot(
            paths: SettingsPaths(applicationSupport: "hidden", runs: "hidden", helper: "hidden"),
            modelPolicy: "single",
            singleModel: "qwen3-vl:4b",
            fastModel: "/Users/private/model",
            detailedModel: "qwen3-vl:8b",
            appleMapsEnabled: false,
            includeCaptionEnabled: false,
            updateStatus: .configured
        )
        let diagnostic = SettingsSupportDiagnostic(
            snapshot: snapshot,
            appVersion: "/private/build",
            operatingSystem: "macOS\n26.5"
        )

        XCTAssertTrue(diagnostic.report.contains("Modelos configurados: qwen3-vl:4b"))
        XCTAssertFalse(diagnostic.report.contains("qwen3-vl:8b"))
        XCTAssertTrue(diagnostic.report.contains("Versión: desconocida"))
        XCTAssertTrue(diagnostic.report.contains("macOS: macOS 26.5"))
        XCTAssertFalse(diagnostic.report.contains("/private/build"))
    }
}
