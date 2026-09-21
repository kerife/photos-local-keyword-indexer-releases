import Foundation
import Darwin
import XCTest
@testable import PhotosLocalKeywordIndexer

final class HistoryReviewTests: XCTestCase {
    func testMalformedAutonomyLinkCannotBecomeAnOrdinaryManualHistoryRun() throws {
        let root = try makeTemporaryDirectory()
        defer { try? FileManager.default.removeItem(at: root) }
        let run = root.appendingPathComponent("run")
        try FileManager.default.createDirectory(at: run, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        let manifest = run.appendingPathComponent("manifest.json")
        try Data(#"{"run_id":"run-1","created_at":"2026-08-25T16:00:00Z","scan_status":"ready","photos":[]}"#.utf8).write(to: manifest)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: manifest.path)
        let link = run.appendingPathComponent("autonomy.json")
        try Data(#"{"version":1,"campaign_id":"../../elsewhere"}"#.utf8).write(to: link)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: link.path)
        let result = HistoryRunStore.loadResult(from: root, expectedRoot: root)
        XCTAssertTrue(result.runs.isEmpty)
        XCTAssertEqual(result.rejectedRunCount, 1)
    }

    func testHistoryModelSummaryHidesInvalidModelValues() {
        let pathModel = makePhoto(modelUsed: "/private/model-cache", state: "ready")
        let cloudModel = makePhoto(modelUsed: "vision:cloud", state: "ready")
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/models/manifest.json"),
            preview: makeManifest(photos: [pathModel, cloudModel])
        )

        XCTAssertEqual(run.modelText, "Modelo no registrado")
        XCTAssertFalse(run.modelText.contains("/private/model-cache"))
        XCTAssertFalse(run.modelText.contains("cloud"))
    }

    func testHistoryRowTitleCollapsesWhitespaceAndBoundsLongValues() {
        let title = String(repeating: "Viaje ", count: 50)

        XCTAssertEqual(
            HistoryRunRowCopy.displayTitle("  Canal\nGrande  "),
            "Canal Grande"
        )
        XCTAssertEqual(
            HistoryRunRowCopy.displayTitle("   "),
            "Ejecución local"
        )
        XCTAssertEqual(HistoryRunRowCopy.displayTitle(title).count, 160)
    }

    func testHistoryRowDateCollapsesWhitespaceAndBoundsLongValues() {
        let date = String(repeating: "2026-08-30 ", count: 20)

        XCTAssertEqual(
            HistoryRunRowCopy.displayDate("  2026-08-30\n12:00:00  "),
            "2026-08-30 12:00:00"
        )
        XCTAssertEqual(
            HistoryRunRowCopy.displayDate("   "),
            "Fecha no disponible"
        )
        XCTAssertEqual(HistoryRunRowCopy.displayDate(date).count, 64)
    }

    func testReviewedHistoryActionNamesOpeningReviewInsteadOfImmediateApply() {
        XCTAssertEqual(
            HistoryReviewActionCopy.title(reviewed: true, available: true),
            "Abrir aplicación revisada"
        )
        XCTAssertEqual(
            HistoryReviewActionCopy.title(
                reviewed: true,
                available: true,
                hasWarnings: true
            ),
            "Abrir revisión de filas válidas"
        )
    }


    func testFreshUnreviewableRunNextActionDoesNotAskForSelection() {
        let preview = RunManifestPreview(
            runID: "fresh-failed",
            createdAt: "2026-08-29T00:00:00Z",
            scanStatus: "failed",
            photos: []
        )

        XCTAssertEqual(
            ReviewFreshNextActionCopy.text(for: preview, fallback: "Selecciona al menos una keyword o caption"),
            "El análisis falló; ejecuta un dry-run nuevo antes de aplicar"
        )
    }
    func testCaptionRequestCopyDistinguishesEnabledRunWithoutReviewableProposals() {
        let preview = RunManifestPreview(
            runID: "run-1",
            createdAt: "2026-08-26T00:00:00Z",
            scanStatus: "ready",
            captionsRequested: true,
            photos: [],
            runErrors: []
        )

        XCTAssertEqual(
            preview.captionRequestText,
            "Captions activados: no hubo propuestas revisables."
        )
        XCTAssertNil(
            RunManifestPreview(
                runID: "run-2",
                createdAt: "2026-08-26T00:00:00Z",
                scanStatus: "ready",
                photos: [],
                runErrors: []
        ).captionRequestText
        )
    }

    func testCaptionRequestCopyUsesProposalCountWhenCaptionsAreAvailable() {
        let photo = PreviewPhoto(
            uuid: "caption-1",
            date: "2026-08-26T00:00:00Z",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: 0.9,
            modelUsed: "local",
            state: "noop",
            proposedCaption: "Una escena visible."
        )
        let preview = RunManifestPreview(
            runID: "run-3",
            createdAt: "2026-08-26T00:00:00Z",
            scanStatus: "ready",
            captionsRequested: true,
            photos: [photo],
            runErrors: []
        )

        XCTAssertEqual(preview.captionRequestText, "Captions activados: 1 caption revisable.")
    }

    func testCaptionRequestCopyExcludesBlockedRowsFromReviewableCount() {
        let blocked = PreviewPhoto(
            uuid: "caption-blocked",
            date: "2026-08-26T00:00:00Z",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: 0.9,
            modelUsed: "local",
            state: "analysis_failed",
            proposedCaption: "No revisable."
        )
        let preview = RunManifestPreview(
            runID: "run-4",
            createdAt: "2026-08-26T00:00:00Z",
            scanStatus: "ready_with_errors",
            captionsRequested: true,
            photos: [blocked],
            runErrors: []
        )

        XCTAssertEqual(preview.captionRequestText, "Captions activados: no hubo propuestas revisables.")
    }

    func testCaptionRequestCopyDoesNotCallVerifiedCaptionsReviewable() {
        let applied = PreviewPhoto(
            uuid: "caption-applied",
            date: "2026-08-26T00:00:00Z",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: 0.9,
            modelUsed: "local",
            state: "noop",
            applyState: "verified",
            proposedCaption: "Una escena visible.",
            appliedCaption: "Una escena visible.",
            captionState: "verified"
        )
        let preview = RunManifestPreview(
            runID: "run-5",
            createdAt: "2026-08-26T00:00:00Z",
            scanStatus: "ready",
            captionsRequested: true,
            photos: [applied],
            runErrors: []
        )

        XCTAssertEqual(preview.captionRequestText, "Captions activados: no hubo propuestas revisables.")
    }

    func testHistoryEmptyStateExplainsSafeEntryPointsWithoutPromisingMutation() {
        XCTAssertEqual(HistoryEmptyStateCopy.title, "Sin ejecuciones locales")
        XCTAssertTrue(HistoryEmptyStateCopy.detail.contains("dry-run"))
        XCTAssertTrue(HistoryEmptyStateCopy.detail.contains("confirmación explícita"))
        XCTAssertFalse(HistoryEmptyStateCopy.accessibilityLabel.contains("/"))
    }

    func testHistoryEmptyStateOffersSafeNavigationToNewRun() {
        XCTAssertEqual(
            HistoryEmptyStateCopy.actionTitle(preparationReady: true),
            "Abrir Revisión"
        )
        XCTAssertEqual(
            HistoryEmptyStateCopy.actionTitle(preparationReady: false),
            "Abrir Preparación"
        )
        XCTAssertTrue(
            HistoryEmptyStateCopy.actionAccessibilityHint(preparationReady: false)
                .contains("primero")
        )
        XCTAssertTrue(
            HistoryEmptyStateCopy.actionAccessibilityHint(preparationReady: true)
                .contains("análisis local")
        )
        XCTAssertTrue(
            HistoryEmptyStateCopy.actionAccessibilityHint(preparationReady: true)
                .contains("no modifica Fotos")
        )
    }

    func testHistoryEmptyStateTreatsStalePreparationAsNotReady() {
        XCTAssertFalse(
            HistoryEmptyStateCopy.canOpenNewRun(preparationReady: true, preflightIsStale: true)
        )
        XCTAssertEqual(
            HistoryEmptyStateCopy.actionTitle(preparationReady: true, preflightIsStale: true),
            "Abrir Preparación"
        )
        XCTAssertTrue(
            HistoryEmptyStateCopy.actionAccessibilityHint(preparationReady: true, preflightIsStale: true)
                .contains("resolver")
        )
    }

    func testPreviewEmptyStateExplainsDryRunIsReadOnly() {
        XCTAssertEqual(ReviewEmptyStateCopy.title, "Sin manifiesto")
        XCTAssertTrue(ReviewEmptyStateCopy.detail.contains("dry-run"))
        XCTAssertTrue(ReviewEmptyStateCopy.detail.contains("no modifica Apple Fotos"))
        XCTAssertEqual(ReviewEmptyStateCopy.accessibilityLabel, "Sin manifiesto. \(ReviewEmptyStateCopy.detail)")
    }

    func testQueryOnlyRecoveryTakesPriorityOverManualMutationWarning() {
        let preview = RunManifestPreview(
            runID: "query-only-recovery",
            createdAt: "2026-08-26T00:00:00Z",
            scanStatus: "ready",
            reviewedFromRunID: "source-run",
            photos: [],
            runErrors: []
        )
        let summary = ReviewRecoverySummary(
            preview: preview,
            mutationRequiresManualReview: true,
            consultationOnly: true
        )

        XCTAssertEqual(summary.title, "Este manifest es solo para consulta")
        XCTAssertTrue(summary.detail.contains("fuente local"))
        XCTAssertFalse(summary.detail.contains("helper terminó"))
    }

    func testHistoryArtifactAvailabilityAcceptsPrivateRegularFileInRun() {
        let availability = HistoryArtifactAvailability.classify(
            metadata: HistoryArtifactMetadata(
                type: .regular,
                ownerID: UInt64(getuid()),
                mode: 0o600,
                linkCount: 1
            ),
            artifactURL: URL(fileURLWithPath: "/safe/runs/one/preview.csv"),
            runDirectory: URL(fileURLWithPath: "/safe/runs/one", isDirectory: true)
        )

        XCTAssertEqual(availability, .available)
        XCTAssertTrue(availability.isOpenable)
    }

    func testHistoryArtifactAvailabilityRejectsMissingFile() {
        let availability = HistoryArtifactAvailability.classify(
            metadata: nil,
            artifactURL: URL(fileURLWithPath: "/safe/runs/one/preview.csv"),
            runDirectory: URL(fileURLWithPath: "/safe/runs/one", isDirectory: true)
        )

        XCTAssertEqual(availability, .missing)
        XCTAssertFalse(availability.isOpenable)
    }

    func testHistoryArtifactAvailabilityRejectsDirectory() {
        let availability = HistoryArtifactAvailability.classify(
            metadata: HistoryArtifactMetadata(
                type: .directory,
                ownerID: UInt64(getuid()),
                mode: 0o700,
                linkCount: 1
            ),
            artifactURL: URL(fileURLWithPath: "/safe/runs/one/preview.csv"),
            runDirectory: URL(fileURLWithPath: "/safe/runs/one", isDirectory: true)
        )

        XCTAssertEqual(availability, .notRegular)
        XCTAssertFalse(availability.isOpenable)
    }

    func testHistoryArtifactAvailabilityRejectsSymlink() {
        let availability = HistoryArtifactAvailability.classify(
            metadata: HistoryArtifactMetadata(
                type: .symlink,
                ownerID: UInt64(getuid()),
                mode: 0o600,
                linkCount: 1
            ),
            artifactURL: URL(fileURLWithPath: "/safe/runs/one/preview.csv"),
            runDirectory: URL(fileURLWithPath: "/safe/runs/one", isDirectory: true)
        )

        XCTAssertEqual(availability, .symlink)
        XCTAssertFalse(availability.isOpenable)
    }

    func testHistoryArtifactAvailabilityRejectsInsecureOwnershipOrPermissions() {
        let wrongPermissions = HistoryArtifactAvailability.classify(
            metadata: HistoryArtifactMetadata(
                type: .regular,
                ownerID: UInt64(getuid()),
                mode: 0o644,
                linkCount: 1
            ),
            artifactURL: URL(fileURLWithPath: "/safe/runs/one/preview.csv"),
            runDirectory: URL(fileURLWithPath: "/safe/runs/one", isDirectory: true)
        )
        let wrongOwner = HistoryArtifactAvailability.classify(
            metadata: HistoryArtifactMetadata(
                type: .regular,
                ownerID: UInt64(getuid()) + 1,
                mode: 0o600,
                linkCount: 1
            ),
            artifactURL: URL(fileURLWithPath: "/safe/runs/one/preview.csv"),
            runDirectory: URL(fileURLWithPath: "/safe/runs/one", isDirectory: true)
        )

        XCTAssertEqual(wrongPermissions, .insecure)
        XCTAssertEqual(wrongOwner, .insecure)
        XCTAssertFalse(wrongPermissions.isOpenable)
        XCTAssertFalse(wrongOwner.isOpenable)
    }

    func testHistoryArtifactAvailabilityRejectsArtifactOutsideRunDirectory() {
        let availability = HistoryArtifactAvailability.classify(
            metadata: HistoryArtifactMetadata(
                type: .regular,
                ownerID: UInt64(getuid()),
                mode: 0o600,
                linkCount: 1
            ),
            artifactURL: URL(fileURLWithPath: "/safe/other/preview.csv"),
            runDirectory: URL(fileURLWithPath: "/safe/runs/one", isDirectory: true)
        )

        XCTAssertEqual(availability, .outsideRun)
        XCTAssertFalse(availability.isOpenable)
    }

    func testUnavailableArtifactProvidesVisibleSafeRecoveryCopy() {
        XCTAssertNil(HistoryArtifactAvailability.available.noticeText)
        XCTAssertEqual(
            HistoryArtifactAvailability.missing.noticeText,
            "Este artefacto local no está disponible; actualiza Historial o conserva el run."
        )
        XCTAssertTrue(
            HistoryArtifactAvailability.insecure.noticeText?
                .contains("no se abrirá") == true
        )
        XCTAssertFalse(
            HistoryArtifactAvailability.outsideRun.noticeText?.contains("/") == true
        )
    }

    func testHistoryDecodesLegacyPhotoWithoutCaptionOrPerPhotoModelFields() throws {
        let data = Data(#"""
        {
          "uuid":"49F027C6-0000-4000-8000-000000000000",
          "title":"Canal Grande",
          "date":"2025-04-13T20:26:02",
          "existing_keywords":["Venecia"],
          "proposed_keywords":["góndola"],
          "confidence":0.91,
          "scan_state":"ready",
          "apply_state":"not_run",
          "rollback_state":"not_run",
          "errors":[]
        }
        """#.utf8)

        let photo = try JSONDecoder().decode(PreviewPhoto.self, from: data)

        XCTAssertNil(photo.modelUsed)
        XCTAssertNil(photo.proposedCaption)
        XCTAssertEqual(photo.captionState, "not_requested")
    }

    func testPreviewPhotoUsesHumanLabelsForWorkflowStates() {
        let photo = PreviewPhoto(
            uuid: "49F027C6-0000-4000-8000-000000000000",
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: ["playa"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:4b",
            state: "ready",
            applyState: "uncertain"
        )

        XCTAssertEqual(photo.stateLabel, "Listo para revisar")
        XCTAssertEqual(photo.applyStateLabel, "Requiere revisión manual")
    }

    func testRunManifestUsesClosedHumanLabelsForScanStatuses() {
        let expected: [(String, String)] = [
            ("ready", "Listo para revisar"),
            ("ready_with_errors", "Listo con advertencias"),
            ("failed", "Análisis fallido"),
            ("interrupted", "Análisis interrumpido"),
        ]

        for (rawStatus, label) in expected {
            let preview = makeManifest(scanStatus: rawStatus, photos: [])
            XCTAssertEqual(preview.scanStatusLabel, label, "Unexpected label for \(rawStatus)")
            XCTAssertEqual(preview.scanStatusAccessibilityLabel, "Estado de la ejecución: \(label)")
        }

        let unknown = makeManifest(scanStatus: "future_status", photos: [])
        XCTAssertEqual(unknown.scanStatusLabel, "Estado no disponible")
        XCTAssertEqual(unknown.scanStatusAccessibilityLabel, "Estado de la ejecución: Estado no disponible")
    }

    func testHistoryStatusUsesDistinctNativeSymbolsForQuickScanning() {
        let expected: [(HistoryRunStatus, String)] = [
            (.readyToReview, "checklist"),
            (.noChanges, "minus.circle"),
            (.applied, "checkmark.circle.fill"),
            (.rolledBack, "arrow.uturn.backward.circle.fill"),
            (.needsAttention, "exclamationmark.triangle.fill"),
        ]

        for (status, symbolName) in expected {
            XCTAssertEqual(status.systemImageName, symbolName)
        }
    }

    func testReviewScreenTitleMatchesTheRunLifecycle() {
        let fresh = makeManifest(photos: [makePhoto()])
        XCTAssertEqual(ReviewScreenCopy.title(for: fresh), "Revisión antes de aplicar")

        let applied = makeManifest(
            photos: [makePhoto(applyState: "verified", appliedKeywords: ["canal"])]
        )
        XCTAssertEqual(ReviewScreenCopy.title(for: applied), "Resultado de la aplicación")

        let rolledBack = makeManifest(
            photos: [
                makePhoto(
                    applyState: "verified",
                    rollbackState: "verified_removed",
                    appliedKeywords: ["canal"]
                ),
            ]
        )
        XCTAssertEqual(ReviewScreenCopy.title(for: rolledBack), "Resultado del rollback")

        let failed = makeManifest(scanStatus: "failed", photos: [])
        XCTAssertEqual(ReviewScreenCopy.title(for: failed), "Resultado del dry-run")
    }

    func testHistoryNextSafeActionPrioritizesReviewBeforeApply() {
        let ready = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/ready/manifest.json"),
            preview: makeManifest(photos: [makePhoto()])
        )

        XCTAssertEqual(ready.nextSafeActionText, "Revisar propuestas antes de aplicar")
    }

    func testEmptyReadyRunDoesNotOfferReviewOrApply() {
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/empty/manifest.json"),
            preview: makeManifest(photos: [])
        )

        XCTAssertEqual(run.status, .noChanges)
        XCTAssertFalse(run.preview.canPrepareReview)
        XCTAssertEqual(
            run.nextSafeActionText,
            "No hubo cambios nuevos; conserva el manifiesto como auditoría"
        )
    }

    func testEmptyWarningRunExplainsThatNothingCanBeApplied() {
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/empty-warning/manifest.json"),
            preview: makeManifest(scanStatus: "ready_with_errors", photos: [])
        )

        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertEqual(
            run.attentionBannerText,
            "No hay fotos elegibles; no hay nada que aplicar."
        )
        XCTAssertEqual(
            run.nextSafeActionText,
            "No hay fotos elegibles; conserva el manifiesto y revisa las advertencias restantes"
        )
    }

    func testEmptyReadyPreviewDoesNotClaimItWasAlreadyMutated() {
        let preview = makeManifest(photos: [])
        let recovery = ReviewRecoverySummary(preview: preview)

        XCTAssertFalse(preview.canPrepareReview)
        XCTAssertEqual(preview.reviewReadOnlyMessage, "No hay fotos elegibles para revisar o aplicar.")
        XCTAssertTrue(recovery.isVisible)
        XCTAssertEqual(recovery.title, "No hay fotos elegibles")
        XCTAssertFalse(recovery.detail.contains("Ya fue revisado, aplicado o revertido"))
        XCTAssertTrue(recovery.detail.contains("Revisión"))
    }

    func testFreshRecoveryOffersNewDryRunWithoutRemovingHistoryPath() {
        let freshRuns = [
            makeManifest(photos: []),
            makeManifest(scanStatus: "failed", photos: []),
            makeManifest(scanStatus: "cancelled", photos: [makePhoto(state: "cancelled")]),
            makeManifest(photos: [makePhoto(state: "noop", proposedKeywords: [])])
        ]

        for preview in freshRuns {
            let recovery = ReviewRecoverySummary(preview: preview)
            XCTAssertTrue(recovery.canStartNewDryRun)
            XCTAssertEqual(recovery.newDryRunAccessibilityHint, "Abre Revisión para continuar el análisis local; no modifica Fotos hasta que guardes una foto explícitamente.")
            XCTAssertEqual(recovery.actionTitle, "Abrir Historial")
        }

        let reviewed = ReviewRecoverySummary(
            preview: makeManifest(reviewedFromRunID: "source-run", photos: []),
            consultationOnly: true
        )
        XCTAssertFalse(reviewed.canStartNewDryRun)
    }

    func testFailedEmptyRunDoesNotLookLikeAnEligiblePhotoWarning() {
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/failed-empty/manifest.json"),
            preview: makeManifest(scanStatus: "failed", photos: [])
        )

        XCTAssertEqual(
            run.attentionBannerText,
            "El análisis falló; ejecuta un dry-run nuevo antes de aplicar"
        )
        XCTAssertEqual(
            run.nextSafeActionText,
            "El análisis falló; ejecuta un dry-run nuevo antes de aplicar"
        )
    }

    func testCancelledEmptyRunDoesNotLookLikeAnEligiblePhotoWarning() {
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/cancelled-empty/manifest.json"),
            preview: makeManifest(scanStatus: "cancelled", photos: [])
        )

        XCTAssertEqual(
            run.attentionBannerText,
            "El dry-run se canceló; ejecuta un dry-run nuevo antes de aplicar"
        )
        XCTAssertEqual(
            run.nextSafeActionText,
            "El dry-run se canceló; ejecuta un dry-run nuevo antes de aplicar"
        )
    }

    func testFreshNoopRunDoesNotOfferReviewOrApply() {
        let noop = makePhoto(state: "noop", proposedKeywords: [])
        let preview = makeManifest(photos: [noop])
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/noop-fresh/manifest.json"),
            preview: preview
        )
        let recovery = ReviewRecoverySummary(preview: preview)

        XCTAssertEqual(run.status, .noChanges)
        XCTAssertFalse(preview.canPrepareReview)
        XCTAssertEqual(run.nextSafeActionText, "No hubo cambios nuevos; conserva el manifiesto como auditoría")
        XCTAssertEqual(preview.reviewReadOnlyMessage, "No hay keywords ni captions nuevas para revisar o aplicar.")
        XCTAssertEqual(recovery.title, "Sin cambios nuevos")
        XCTAssertFalse(recovery.detail.contains("Ya fue revisado, aplicado o revertido"))
    }

    func testFreshBlockedRunDoesNotClaimItWasAlreadyReviewed() {
        let blocked = makePhoto(
            proposedKeywords: ["góndola"],
            errors: [ManifestPreviewError(stage: "analysis", code: "LOW_CONFIDENCE")]
        )
        let preview = makeManifest(photos: [blocked])
        let recovery = ReviewRecoverySummary(preview: preview)

        XCTAssertTrue(recovery.isVisible)
        XCTAssertEqual(recovery.title, "No hay propuestas revisables")
        XCTAssertTrue(recovery.detail.contains("dry-run nuevo"))
        XCTAssertFalse(recovery.detail.contains("Ya fue revisado, aplicado o revertido"))
        XCTAssertEqual(recovery.actionTitle, "Abrir Historial")
    }

    func testHistoryFreshBlockedRunExplainsThatANewDryRunIsRequired() {
        let blocked = makePhoto(
            proposedKeywords: ["góndola"],
            errors: [ManifestPreviewError(stage: "analysis", code: "LOW_CONFIDENCE")]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/blocked-only/manifest.json"),
            preview: makeManifest(photos: [blocked])
        )

        XCTAssertEqual(run.attentionBannerText, "No hay propuestas revisables; ejecuta un dry-run nuevo antes de aplicar")
        XCTAssertEqual(run.nextSafeActionText, "No hay propuestas revisables; conserva el run y ejecuta un dry-run nuevo antes de aplicar")
    }

    func testFreshNoopRunWithWarningsKeepsWarningsSeparateFromMutationScope() {
        let noop = makePhoto(state: "noop", proposedKeywords: [])
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/noop-warning/manifest.json"),
            preview: makeManifest(scanStatus: "ready_with_errors", photos: [noop])
        )

        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertEqual(
            run.attentionBannerText,
            "Sin cambios nuevos; no hay nada que aplicar. Revisa las advertencias restantes."
        )
        XCTAssertEqual(
            run.nextSafeActionText,
            "Sin cambios nuevos; conserva el manifiesto y revisa las advertencias restantes"
        )
    }

    func testFreshNoopPhotoWithErrorsIsNotReportedAsNoChanges() {
        let noopWithError = makePhoto(
            state: "noop",
            proposedKeywords: [],
            errors: [ManifestPreviewError(stage: "analysis", code: "LOW_CONFIDENCE")]
        )
        let preview = makeManifest(photos: [noopWithError])

        XCTAssertFalse(preview.isNoopOnlyRun)
        XCTAssertEqual(
            preview.reviewReadOnlyMessage,
            "Solo lectura: ejecuta un dry-run nuevo antes de revisar o aplicar."
        )
    }

    func testWarningOnlyRunDoesNotClaimRowsAreBlocked() {
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/warning-only/manifest.json"),
            preview: makeManifest(scanStatus: "ready_with_errors", photos: [makePhoto()])
        )

        XCTAssertFalse(run.hasBlockedRows)
        XCTAssertEqual(run.statusLabel, "Listo con advertencias")
        XCTAssertEqual(
            run.attentionBannerText,
            "Revisión disponible; revisa las advertencias del scan antes de aplicar."
        )
        XCTAssertEqual(
            run.nextSafeActionText,
            "Revisa las propuestas y las advertencias del scan antes de aplicar"
        )
    }

    func testReviewedWarningOnlyRunKeepsWarningsSeparateFromBlockedRows() {
        let preview = makeManifest(
            scanStatus: "ready_with_errors",
            reviewedFromRunID: "source-run",
            photos: [makePhoto()]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/reviewed-warning-only/manifest.json"),
            preview: preview
        )

        XCTAssertTrue(run.canContinueReviewedApply)
        XCTAssertFalse(run.hasBlockedRows)
        XCTAssertEqual(
            run.attentionBannerText,
            "Revisión disponible; revisa las advertencias del scan antes de aplicar."
        )
        XCTAssertEqual(
            run.reviewedManifestMessage,
            "Manifiesto revisado listo; revisa las advertencias del scan y confirma explícitamente."
        )
        XCTAssertEqual(
            run.reviewedApplyAccessibilityLabel,
            "Confirmar aplicación tras revisar las advertencias del scan"
        )
        XCTAssertEqual(
            run.nextSafeActionText,
            "Confirma la aplicación tras revisar las advertencias del scan"
        )
    }

    func testReviewedNoopRunWithWarningsDoesNotOfferApply() {
        let noop = makePhoto(applyState: "noop", proposedKeywords: [])
        let preview = makeManifest(
            scanStatus: "ready_with_errors",
            reviewedFromRunID: "source-run",
            photos: [noop]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/reviewed-noop-warning/manifest.json"),
            preview: preview
        )

        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertTrue(run.isNoopOutcome)
        XCTAssertEqual(
            run.attentionBannerText,
            "Sin cambios nuevos; no hay nada que aplicar. Revisa las advertencias restantes."
        )
        XCTAssertEqual(
            run.nextSafeActionText,
            "Sin cambios nuevos; conserva el manifiesto y revisa las advertencias restantes"
        )
        XCTAssertEqual(
            run.reviewedManifestMessage,
            "Sin cambios nuevos; conserva el manifiesto como auditoría."
        )
    }

    func testInterruptedRunBlocksApplyAndNamesTheFullRecoverySequence() {
        let interrupted = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/interrupted/manifest.json"),
            preview: makeManifest(scanStatus: "interrupted", photos: [makePhoto(state: "cancelled")])
        )

        XCTAssertEqual(interrupted.status, .needsAttention)
        XCTAssertEqual(
            interrupted.nextSafeActionText,
            "Abre manifest y CSV, verifica Fotos, ejecuta un dry-run nuevo y revisa sus propuestas antes de aplicar"
        )
    }

    func testPartialReadyWithErrorsRunExplainsThatValidPhotosRemainReviewable() {
        let valid = makePhoto()
        let failed = makePhoto(state: "analysis_failed")
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/partial/manifest.json"),
            preview: makeManifest(scanStatus: "ready_with_errors", photos: [valid, failed])
        )

        XCTAssertTrue(run.preview.canPrepareReview)
        XCTAssertEqual(
            run.attentionBannerText,
            "Revisión disponible; las fotos con errores quedan bloqueadas para aplicar"
        )
        XCTAssertEqual(
            run.nextSafeActionText,
            "Revisa las filas válidas; las filas con errores quedan bloqueadas"
        )
    }

    func testFreshReviewBlocksWhenAnyPhotoHasAnUnknownScanState() {
        let unknown = PreviewPhoto(
            uuid: "future-scan-row",
            title: "Estado futuro",
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: nil,
            modelUsed: nil,
            state: "future_scan_state",
            errors: []
        )
        let preview = makeManifest(
            scanStatus: "ready",
            photos: [makePhoto(), unknown]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/future-state/manifest.json"),
            preview: preview
        )

        XCTAssertFalse(preview.canPrepareReview)
        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertTrue(run.nextSafeActionText.contains("dry-run nuevo"))
        XCTAssertTrue(run.attentionBannerText.contains("dry-run nuevo"))
    }

    func testInterruptedRunKeepsFreshReviewAsTheRequiredRecovery() {
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/interrupted/manifest.json"),
            preview: makeManifest(scanStatus: "interrupted", photos: [makePhoto(state: "cancelled")])
        )

        XCTAssertFalse(run.preview.canPrepareReview)
        XCTAssertEqual(
            run.attentionBannerText,
            "Dry-run interrumpido; ejecuta un dry-run nuevo antes de aplicar"
        )
    }

    func testCancelledDryRunPointsToNewDryRunInsteadOfPhotoMutationReview() {
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/cancelled-scan/manifest.json"),
            preview: makeManifest(scanStatus: "cancelled", photos: [makePhoto(state: "cancelled")])
        )

        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertEqual(
            run.attentionBannerText,
            "Dry-run cancelado; ejecuta un dry-run nuevo antes de aplicar"
        )
        XCTAssertEqual(
            run.nextSafeActionText,
            "Abre manifest y CSV, verifica Fotos, ejecuta un dry-run nuevo y revisa sus propuestas antes de aplicar"
        )
    }

    func testHistoryNextSafeActionCallsOutManualReviewForUncertainState() {
        let uncertain = makePhoto(applyState: "uncertain")
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/uncertain/manifest.json"),
            preview: makeManifest(photos: [uncertain])
        )

        XCTAssertEqual(run.nextSafeActionText, "Este run no se puede revisar de forma segura; ejecuta un dry-run nuevo antes de aplicar")
    }

    func testHistoryNextSafeActionExposesVerifiedRollbackWithoutOverpromising() {
        let applied = makePhoto(
            applyState: "verified",
            rollbackState: "not_run",
            appliedKeywords: ["canal"]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/applied/manifest.json"),
            preview: makeManifest(photos: [applied])
        )

        XCTAssertEqual(run.nextSafeActionText, "Este run no se puede revisar de forma segura; ejecuta un dry-run nuevo antes de aplicar")
    }

    func testPartialRunExposesTheExactVerifiedRollbackScope() {
        let applied = makePhoto(
            uuid: "history-applied-photo",
            applyState: "verified",
            rollbackState: "not_run",
            appliedKeywords: ["canal"]
        )
        let failed = makePhoto(uuid: "history-failed-photo", applyState: "failed")
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/partial/manifest.json"),
            preview: makeManifest(photos: [applied, failed])
        )

        XCTAssertEqual(run.rollbackActionText, "Rollback disponible para 1 foto verificada")
        XCTAssertEqual(run.nextSafeActionText, "Este run no se puede revisar de forma segura; ejecuta un dry-run nuevo antes de aplicar")
    }

    func testRetryableFailureTakesPriorityOverRollbackInNextSafeAction() {
        let verified = makePhoto(
            uuid: "history-verified-photo",
            applyState: "verified",
            rollbackState: "not_run",
            appliedKeywords: ["canal"]
        )
        let failed = makePhoto(
            uuid: "history-retryable-photo",
            applyState: "failed",
            errors: [ManifestPreviewError(stage: "apply", code: "APPLY_FAILED")]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/retry/manifest.json"),
            preview: makeManifest(
                reviewedFromRunID: "source-run",
                photos: [verified, failed]
            )
        )

        XCTAssertTrue(run.isRetryingFailedApply)
        XCTAssertEqual(run.statusLabel, "Listo para reintentar solo las filas fallidas")
        XCTAssertEqual(
            run.attentionBannerText,
            "Reintento listo; las filas verificadas permanecen sin cambios"
        )
        XCTAssertEqual(
            run.nextSafeActionText,
            "Confirma el reintento de las filas fallidas; las filas verificadas no se modificarán"
        )
    }

    func testRetryApplyActionTakesPriorityOverEmptySelection() {
        let retryHint = "La siguiente pantalla permitirá reintentar solo las filas fallidas; las filas aplicadas y verificadas no se modificarán y requerirá confirmación explícita."
        XCTAssertEqual(
            ReviewApplyButtonCopy.accessibilityHint(
                reviewed: true,
                available: true,
                requiresManualReview: false,
                hasSelectedChanges: false,
                isRetryingFailedApply: true
            ),
            retryHint
        )
        XCTAssertEqual(
            ReviewNextActionCopy.text(
                sourceAvailable: true,
                canContinue: true,
                hasSelectedChanges: false,
                isRetryingFailedApply: true
            ),
            "Confirma el reintento de las filas fallidas; las filas verificadas no se modificarán"
        )
    }

    func testPartialRunSummarizesVerifiedKeywordCaptionScopeAndManualPhotos() {
        let verified = makePhoto(
            uuid: "history-verified-scope-photo",
            applyState: "verified",
            appliedKeywords: ["góndola", "canal"],
            appliedCaption: "Un canal visible."
        )
        let failed = makePhoto(uuid: "history-manual-scope-photo", applyState: "failed")
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/partial-scope/manifest.json"),
            preview: makeManifest(photos: [verified, failed])
        )

        XCTAssertEqual(
            run.mutationScopeText,
            "Verificado: 1 foto · 2 keywords · 1 caption. Atención manual: 1 foto."
        )
    }

    func testMutationScopeReportsRolledBackKeywordAndCaptionCounts() {
        let rolledBack = makePhoto(
            applyState: "verified",
            rollbackState: "verified_removed",
            appliedKeywords: ["góndola", "canal"],
            appliedCaption: "Un canal visible.",
            rolledBackKeywords: ["góndola", "canal"]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/rolled-back/manifest.json"),
            preview: makeManifest(photos: [rolledBack])
        )

        XCTAssertEqual(
            run.mutationScopeText,
            "Rollback verificado: 1 foto · 2 keywords · 1 caption."
        )
    }

    func testMutationScopeCountsOnlyKeywordsActuallyRemovedAfterPartialRollback() {
        let rolledBack = makePhoto(
            applyState: "verified",
            rollbackState: "verified_removed",
            appliedKeywords: ["góndola", "canal"],
            rolledBackKeywords: ["góndola"]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/partial-rollback/manifest.json"),
            preview: makeManifest(photos: [rolledBack])
        )

        XCTAssertEqual(
            run.mutationScopeText,
            "Rollback verificado: 1 foto · 1 keyword · 0 captions."
        )
    }

    func testAlreadyAbsentRollbackIsReportedAsCompletedAndScoped() {
        let alreadyAbsent = makePhoto(
            applyState: "verified",
            rollbackState: "already_absent",
            appliedKeywords: ["canal"]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/already-absent/manifest.json"),
            preview: makeManifest(photos: [alreadyAbsent])
        )

        XCTAssertEqual(run.status, .rolledBack)
        XCTAssertEqual(run.statusLabel, "Rollback verificado")
        XCTAssertFalse(run.canRollback)
        XCTAssertTrue(run.mutationScopeText?.contains("ya estaban ausentes") == true)
    }

    func testAlreadyAbsentAndPendingRollbackAreShownAsPartial() {
        let alreadyAbsent = makePhoto(
            applyState: "verified",
            rollbackState: "already_absent",
            appliedKeywords: ["canal"]
        )
        let stillApplied = makePhoto(
            applyState: "verified",
            rollbackState: "not_run",
            appliedKeywords: ["góndola"]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/partial-already-absent/manifest.json"),
            preview: makeManifest(reviewedFromRunID: "source-run", photos: [alreadyAbsent, stillApplied])
        )

        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertEqual(
            run.attentionBannerText,
            "Rollback parcial: solo los cambios verificados siguen disponibles"
        )
    }

    func testCancelledApplyRequiresManualAttentionInsteadOfLookingReadyToReview() {
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/cancelled-apply/manifest.json"),
            preview: makeManifest(photos: [makePhoto(applyState: "cancelled")])
        )

        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertEqual(run.nextSafeActionText, "Este run no se puede revisar de forma segura; ejecuta un dry-run nuevo antes de aplicar")
    }

    func testCancelledRollbackDoesNotLookFullyRolledBack() {
        let verifiedRemoved = makePhoto(applyState: "verified", rollbackState: "verified_removed")
        let cancelled = makePhoto(applyState: "verified", rollbackState: "cancelled")
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/cancelled-rollback/manifest.json"),
            preview: makeManifest(photos: [verifiedRemoved, cancelled])
        )

        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertEqual(run.nextSafeActionText, "Este run no se puede revisar de forma segura; ejecuta un dry-run nuevo antes de aplicar")
    }

    func testCancelledRollbackStateIsAnAttentionState() {
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/cancelled-rollback/manifest.json"),
            preview: makeManifest(
                photos: [makePhoto(applyState: "verified", rollbackState: "cancelled")]
            )
        )

        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertEqual(run.statusLabel, "Requiere atención")
        XCTAssertTrue(run.nextSafeActionText.contains("dry-run nuevo"))
    }

    func testReviewedAppliedRunKeepsVerifiedRollbackAsTheNextSafeAction() {
        let applied = makePhoto(
            applyState: "verified",
            appliedKeywords: ["canal"]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/reviewed-applied/manifest.json"),
            preview: makeManifest(
                reviewedFromRunID: "source-run",
                photos: [applied]
            )
        )

        XCTAssertEqual(run.status, .applied)
        XCTAssertTrue(run.canRollback)
        XCTAssertEqual(run.nextSafeActionText, "Puedes revertir solo los cambios verificados")
    }

    func testReviewedAlreadyAbsentRollbackShowsNoPendingChanges() {
        let removed = makePhoto(
            applyState: "verified",
            rollbackState: "already_absent",
            appliedKeywords: ["canal"]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/reviewed-already-absent/manifest.json"),
            preview: makeManifest(
                reviewedFromRunID: "source-run",
                photos: [removed]
            )
        )

        XCTAssertEqual(run.status, .rolledBack)
        XCTAssertFalse(run.canRollback)
        XCTAssertEqual(run.nextSafeActionText, "No hay cambios verificados pendientes")
    }

    func testReviewedPartialApplyKeepsRollbackActionWhenScanWarningsRemain() {
        let verified = makePhoto(
            applyState: "verified",
            appliedKeywords: ["canal"]
        )
        let failed = makePhoto(state: "analysis_failed")
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/reviewed-partial/manifest.json"),
            preview: makeManifest(
                scanStatus: "ready_with_errors",
                reviewedFromRunID: "source-run",
                photos: [verified, failed]
            )
        )

        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertTrue(run.canRollback)
        XCTAssertEqual(
            run.nextSafeActionText,
            "Revisar manualmente el estado incierto; puedes revertir solo los cambios verificados"
        )
    }

    func testUncertainRunNamesManualReviewAndSafeVerifiedRollback() {
        let verified = makePhoto(applyState: "verified", appliedKeywords: ["canal"])
        let uncertain = makePhoto(applyState: "uncertain")
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/partial-uncertain/manifest.json"),
            preview: makeManifest(photos: [verified, uncertain])
        )

        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertEqual(run.rollbackActionText, "Rollback disponible para 1 foto verificada")
        XCTAssertTrue(run.nextSafeActionText.contains("dry-run nuevo"))
    }

    func testPartialRollbackDoesNotLookFullyRolledBack() {
        let verifiedRemoved = makePhoto(
            applyState: "verified",
            rollbackState: "verified_removed",
            appliedKeywords: ["canal"]
        )
        let stillApplied = makePhoto(
            applyState: "verified",
            rollbackState: "not_run",
            appliedKeywords: ["góndola"]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/partial-rollback/manifest.json"),
            preview: makeManifest(reviewedFromRunID: "source-run", photos: [verifiedRemoved, stillApplied])
        )

        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertEqual(run.statusLabel, "Requiere atención")
        XCTAssertEqual(run.nextSafeActionText, "Revisar manualmente el estado incierto; puedes revertir solo los cambios verificados")
        XCTAssertEqual(
            run.attentionBannerText,
            "Rollback parcial: solo los cambios verificados siguen disponibles"
        )
    }

    func testPartialRollbackStatusRemainsAccessibleAndScoped() {
        let verifiedRemoved = makePhoto(
            applyState: "verified",
            rollbackState: "verified_removed",
            appliedKeywords: ["canal"]
        )
        let stillApplied = makePhoto(
            applyState: "verified",
            rollbackState: "not_run",
            appliedKeywords: ["góndola"]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/partial-rollback/manifest.json"),
            preview: makeManifest(reviewedFromRunID: "source-run", photos: [verifiedRemoved, stillApplied])
        )

        XCTAssertEqual(run.statusLabel, "Requiere atención")
        XCTAssertTrue(run.attentionBannerText.contains("Rollback parcial"))
        XCTAssertEqual(run.rollbackActionText, "Rollback disponible para 1 foto verificada")
    }

    func testAppliedRunWithScanWarningsKeepsRollbackActionVisibleInAttentionBanner() {
        let applied = makePhoto(
            applyState: "verified",
            appliedKeywords: ["canal"]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/applied-warning/manifest.json"),
            preview: makeManifest(
                scanStatus: "ready_with_errors",
                reviewedFromRunID: "source-run",
                photos: [applied]
            )
        )

        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertTrue(run.canRollback)
        XCTAssertTrue(run.nextSafeActionText.contains("revertir solo los cambios verificados"))
        XCTAssertEqual(
            run.attentionBannerText,
            "Aplicación verificada; puedes revertir solo los cambios verificados. Revisa las advertencias restantes."
        )
    }

    func testRollbackCompletionRemainsVisibleWhenOriginalScanHasWarnings() {
        let removed = makePhoto(
            applyState: "verified",
            rollbackState: "verified_removed",
            appliedKeywords: ["canal"]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/rollback-warning/manifest.json"),
            preview: makeManifest(
                scanStatus: "ready_with_errors",
                photos: [removed]
            )
        )

        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertTrue(run.isRollbackComplete)
        XCTAssertEqual(
            run.attentionBannerText,
            "Rollback verificado; no hay cambios verificados pendientes. Revisa las advertencias restantes."
        )
        XCTAssertEqual(
            run.nextSafeActionText,
            "Rollback verificado; no hay cambios verificados pendientes; revisa las advertencias restantes"
        )
    }

    func testReviewedNextActionDoesNotPromiseApplyWhenSafetyGuardBlocksIt() {
        XCTAssertEqual(
            ReviewNextActionCopy.text(sourceAvailable: false, canContinue: false),
            "La fuente local no está disponible o no es verificable; usa «Importar manifest» para importar también el dry-run fuente o ejecuta un dry-run nuevo antes de aplicar"
        )
        XCTAssertEqual(
            ReviewNextActionCopy.text(sourceAvailable: true, canContinue: true),
            "Confirma la aplicación del manifiesto revisado"
        )
        XCTAssertEqual(
            ReviewNextActionCopy.text(sourceAvailable: true, canContinue: true, hasWarnings: true),
            "Confirma la aplicación de las filas válidas; las filas con errores quedan bloqueadas"
        )
        XCTAssertEqual(
            ReviewNextActionCopy.text(sourceAvailable: true, canContinue: false),
            "Revisa manualmente el manifiesto y ejecuta un dry-run nuevo antes de aplicar"
        )
    }

    func testReviewedNextActionRequiresSelectionBeforeConfirming() {
        XCTAssertEqual(
            ReviewNextActionCopy.text(
                sourceAvailable: true,
                canContinue: true,
                hasSelectedChanges: false
            ),
            "Selecciona al menos una keyword o caption para preparar la aplicación"
        )
    }

    func testBlockedApplyActionNamesItsSafeRecoveryWithoutPromisingConfirmation() {
        XCTAssertEqual(
            ReviewApplyButtonCopy.title(reviewed: true, available: false),
            "Aplicación bloqueada"
        )
        XCTAssertEqual(
            ReviewApplyButtonCopy.accessibilityHint(
                reviewed: true,
                available: false,
                requiresManualReview: false
            ),
            "Este manifest queda solo para consulta. La fuente local no está disponible o no es verificable; usa «Importar manifest» para importar también el dry-run fuente o ejecuta un dry-run nuevo antes de aplicar."
        )
        XCTAssertEqual(
            ReviewApplyButtonCopy.accessibilityHint(
                reviewed: true,
                available: false,
                requiresManualReview: true
            ),
            "Revisa el run en Historial antes de intentar otra mutación."
        )
        XCTAssertEqual(
            ReviewApplyButtonCopy.title(reviewed: false, available: true),
            "Preparar aplicación"
        )
    }

    func testOnlyTheReviewedApplyActionUsesDestructiveTreatment() {
        XCTAssertFalse(
            ReviewApplyButtonCopy.requiresDestructiveTreatment(
                reviewed: false,
                available: true
            )
        )
        XCTAssertTrue(
            ReviewApplyButtonCopy.requiresDestructiveTreatment(
                reviewed: true,
                available: true
            )
        )
        XCTAssertFalse(
            ReviewApplyButtonCopy.requiresDestructiveTreatment(
                reviewed: true,
                available: false
            )
        )
    }

    func testBlockedApplyLabelExplainsManualReviewInsteadOfClaimingConsultationOnly() {
        XCTAssertEqual(
            ReviewApplyButtonCopy.accessibilityLabel(
                reviewed: true,
                available: false,
                summary: "Resumen seguro",
                requiresManualReview: true
            ),
            "Aplicación bloqueada. Revisa el run en Historial antes de intentar otra mutación."
        )
    }

    func testBlockedReviewedApplyDoesNotClaimSourceIsMissingWhenItIsAvailable() {
        let expected = "Este manifest queda solo para consulta por su estado actual. Revisa Historial y ejecuta un dry-run nuevo antes de aplicar."

        XCTAssertEqual(
            ReviewApplyButtonCopy.accessibilityHint(
                reviewed: true,
                available: false,
                requiresManualReview: false,
                sourceAvailable: true
            ),
            expected
        )
        XCTAssertEqual(
            HistoryReviewActionCopy.accessibilityHint(
                reviewed: true,
                available: false,
                requiresManualReview: false,
                sourceAvailable: true
            ),
            expected
        )
        XCTAssertFalse(expected.contains("fuente local no está disponible"))
    }

    func testApplyButtonExplainsWhenNoChangeIsSelected() {
        XCTAssertEqual(
            ReviewApplyButtonCopy.accessibilityHint(
                reviewed: false,
                available: true,
                requiresManualReview: false,
                hasSelectedChanges: false
            ),
            "Selecciona al menos una keyword o caption para preparar la aplicación."
        )
    }

    func testPartialApplyButtonHintKeepsValidRowsAndBlockedRowsExplicit() {
        let expected = "La siguiente pantalla aplicará solo las filas válidas; las filas con errores están bloqueadas y requerirá confirmación explícita."
        XCTAssertEqual(
            ReviewApplyButtonCopy.accessibilityHint(
                reviewed: true,
                available: true,
                requiresManualReview: false,
                hasWarnings: true
            ),
            expected
        )
        XCTAssertEqual(
            HistoryReviewActionCopy.accessibilityHint(
                reviewed: true,
                available: true,
                requiresManualReview: false,
                hasWarnings: true
            ),
            expected
        )
    }

    func testPartialApplyButtonTitleKeepsMutationScopeVisible() {
        XCTAssertEqual(
            ReviewApplyButtonCopy.title(reviewed: true, available: true, hasWarnings: true),
            "Aplicar filas válidas"
        )
        XCTAssertEqual(
            HistoryReviewActionCopy.title(reviewed: true, available: true, hasWarnings: true),
            "Abrir revisión de filas válidas"
        )
        XCTAssertEqual(
            ReviewApplyButtonCopy.title(reviewed: true, available: true),
            "Aplicar manifiesto revisado"
        )
    }

    func testPartialApplyAccessibilityLabelMatchesVisibleButtonTitle() {
        XCTAssertEqual(
            HistoryReviewActionCopy.accessibilityLabel(
                reviewed: true,
                available: true,
                hasWarnings: true
            ),
            "Abrir revisión de filas válidas"
        )
        XCTAssertEqual(
            HistoryReviewActionCopy.accessibilityLabel(
                reviewed: true,
                available: true
            ),
            "Abrir aplicación revisada"
        )
        XCTAssertEqual(
            HistoryReviewActionCopy.accessibilityLabel(
                reviewed: false,
                available: true
            ),
            "Revisar propuestas"
        )
    }

    func testFreshPartialReviewCTADistinguishesPreparationFromApplication() {
        XCTAssertEqual(
            ReviewApplyButtonCopy.title(reviewed: false, available: true, hasWarnings: true),
            "Preparar revisión con filas válidas"
        )
        XCTAssertEqual(
            HistoryReviewActionCopy.title(reviewed: false, available: true, hasWarnings: true),
            "Revisar filas válidas"
        )
        XCTAssertEqual(
            HistoryReviewActionCopy.accessibilityLabel(
                reviewed: false,
                available: true,
                hasWarnings: true
            ),
            "Revisar filas válidas"
        )
    }

    func testFreshPartialReviewHintDoesNotPromiseAnApply() {
        let hint = ReviewApplyButtonCopy.accessibilityHint(
            reviewed: false,
            available: true,
            requiresManualReview: false,
            hasWarnings: true
        )
        XCTAssertTrue(hint.contains("preparará una copia revisada"))
        XCTAssertTrue(hint.contains("solo las filas válidas"))
        XCTAssertTrue(hint.contains("no se aplicarán"))
        XCTAssertFalse(hint.contains("aplicará solo"))

        let historyHint = HistoryReviewActionCopy.accessibilityHint(
            reviewed: false,
            available: true,
            requiresManualReview: false,
            hasWarnings: true
        )
        XCTAssertTrue(historyHint.contains("filas con errores"))
        XCTAssertTrue(historyHint.contains("no se aplicarán"))
    }

    func testWarningOnlyReviewActionsDoNotClaimRowsAreBlocked() {
        XCTAssertEqual(
            ReviewApplyButtonCopy.title(
                reviewed: false,
                available: true,
                hasWarnings: true,
                hasBlockedRows: false
            ),
            "Preparar revisión"
        )
        XCTAssertEqual(
            ReviewApplyButtonCopy.accessibilityHint(
                reviewed: false,
                available: true,
                requiresManualReview: false,
                hasWarnings: true,
                hasBlockedRows: false
            ),
            "Abre las propuestas y revisa las advertencias del scan; no se aplicará nada sin confirmación explícita."
        )
        XCTAssertEqual(
            HistoryReviewActionCopy.title(
                reviewed: false,
                available: true,
                hasWarnings: true,
                hasBlockedRows: false
            ),
            "Revisar propuestas"
        )
        XCTAssertEqual(
            HistoryReviewActionCopy.accessibilityLabel(
                reviewed: true,
                available: true,
                hasWarnings: true,
                hasBlockedRows: false
            ),
            "Abrir aplicación revisada"
        )
        XCTAssertEqual(
            ReviewNextActionCopy.text(
                sourceAvailable: true,
                canContinue: true,
                hasWarnings: true,
                hasBlockedRows: false
            ),
            "Confirma la aplicación tras revisar las advertencias del scan"
        )
    }

    func testWarningOnlyApprovalSummaryDoesNotClaimRowsAreBlocked() {
        XCTAssertEqual(
            ReviewApprovalSummary(
                keywordCount: 2,
                captionCount: 1,
                hasWarnings: true,
                hasBlockedRows: false
            ).nextSafeAction,
            "Prepara una revisión tras revisar las advertencias del scan"
        )
    }

    func testQueryOnlyApplyHintPrioritizesMissingSourceOverEmptySelection() {
        XCTAssertEqual(
            ReviewApplyButtonCopy.accessibilityHint(
                reviewed: true,
                available: false,
                requiresManualReview: false,
                hasSelectedChanges: false
            ),
            "Este manifest queda solo para consulta. La fuente local no está disponible o no es verificable; usa «Importar manifest» para importar también el dry-run fuente o ejecuta un dry-run nuevo antes de aplicar."
        )
    }

    func testUnavailableFreshRunApplyHintPrioritizesNewDryRunOverEmptySelection() {
        XCTAssertEqual(
            ReviewApplyButtonCopy.accessibilityHint(
                reviewed: false,
                available: false,
                requiresManualReview: false,
                hasSelectedChanges: false
            ),
            "Ejecuta un dry-run nuevo y revisa sus propuestas antes de aplicar."
        )
    }

    func testUnavailableFreshRunApplyLabelDoesNotDescribeAWriteAsPending() {
        XCTAssertEqual(
            ReviewApplyButtonCopy.accessibilityLabel(
                reviewed: false,
                available: false,
                summary: "Antes de escribir, se comprobarán propuestas.",
                requiresManualReview: false
            ),
            "Aplicación bloqueada. Ejecuta un dry-run nuevo y revisa sus propuestas antes de aplicar."
        )
    }

    func testManualReviewTakesPriorityOverEmptySelectionInApplyHint() {
        XCTAssertEqual(
            ReviewApplyButtonCopy.accessibilityHint(
                reviewed: false,
                available: false,
                requiresManualReview: true,
                hasSelectedChanges: false
            ),
            "Revisa el run en Historial antes de intentar otra mutación."
        )
    }

    func testHistoryQueryOnlyActionDoesNotLookLikeAnApplyConfirmation() {
        XCTAssertEqual(
            HistoryReviewActionCopy.title(reviewed: true, available: false),
            "Aplicación bloqueada"
        )
        XCTAssertEqual(
            HistoryReviewActionCopy.title(reviewed: false, available: false),
            "Revisión bloqueada"
        )
        XCTAssertEqual(
            HistoryReviewActionCopy.title(reviewed: true, available: true),
            "Abrir aplicación revisada"
        )
        XCTAssertTrue(
            HistoryReviewActionCopy.accessibilityHint(
                reviewed: true,
                available: false,
                requiresManualReview: false
            ).contains("solo para consulta")
        )
    }

    func testReviewedNoopApplyIsReportedAsNoChangesNotReadyToReview() {
        let noop = makePhoto(applyState: "noop", proposedKeywords: [])
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/noop/manifest.json"),
            preview: makeManifest(
                reviewedFromRunID: "source-run",
                photos: [noop]
            )
        )

        XCTAssertEqual(run.status, .noChanges)
        XCTAssertEqual(run.statusLabel, "Sin cambios nuevos")
        XCTAssertFalse(run.canContinueReviewedApply)
        XCTAssertFalse(run.canRollback)
        XCTAssertEqual(
            run.nextSafeActionText,
            "No hubo cambios nuevos; conserva el manifiesto como auditoría"
        )
    }

    func testMutationScopeReportsNoopRowsAlongsideVerifiedResults() {
        let noop = makePhoto(applyState: "noop", proposedKeywords: [])
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/mixed-results/manifest.json"),
            preview: makeManifest(
                reviewedFromRunID: "source-run",
                photos: [
                    makePhoto(
                        applyState: "verified",
                        appliedKeywords: ["canal"]
                    ),
                    noop,
                ]
            )
        )

        XCTAssertEqual(
            run.mutationScopeText,
            "Verificado: 1 foto · 1 keyword · 0 captions. Sin cambios nuevos: 1 foto."
        )
    }

    func testRollbackActionIsUnavailableWhileBusyOrManualReviewIsRequired() {
        XCTAssertTrue(HistoryActionAvailability.canStartRollback(workerIsRunning: false, requiresManualReview: false))
        XCTAssertFalse(HistoryActionAvailability.canStartRollback(workerIsRunning: true, requiresManualReview: false))
        XCTAssertFalse(HistoryActionAvailability.canStartRollback(workerIsRunning: false, requiresManualReview: true))
    }

    func testHistoricalRollbackIsBlockedOnlyWhileWorkerIsBusy() {
        XCTAssertTrue(HistoryActionAvailability.canStartHistoricalRollback(workerIsRunning: false))
        XCTAssertFalse(HistoryActionAvailability.canStartHistoricalRollback(workerIsRunning: true))
        XCTAssertEqual(
            HistoryActionAvailability.historicalRollbackAccessibilityHint(workerIsRunning: false),
            "Solo revierte las fotos con cambios verificados; las demás permanecen sin cambios."
        )
    }

    func testHistoricalReviewIsPausedWhileAnotherWorkerOperationIsRunning() {
        XCTAssertEqual(
            HistoryActionAvailability.activeOperationNotice,
            "Hay una operación en curso; importar otro manifiesto, abrir otra revisión o iniciar un rollback queda pausado hasta que termine."
        )
        XCTAssertTrue(
            HistoryActionAvailability.canOpenHistoricalReview(
                reviewAvailable: true,
                workerIsRunning: false
            )
        )
        XCTAssertFalse(
            HistoryActionAvailability.canOpenHistoricalReview(
                reviewAvailable: true,
                workerIsRunning: true
            )
        )
        XCTAssertFalse(
            HistoryActionAvailability.canOpenHistoricalReview(
                reviewAvailable: false,
                workerIsRunning: false
            )
        )
        XCTAssertEqual(
            HistoryActionAvailability.historicalReviewAccessibilityHint(
                workerIsRunning: true,
                fallback: "Abre las propuestas para revisar."
            ),
            "Hay otra operación en curso; espera a que termine antes de abrir otra revisión."
        )
        XCTAssertEqual(
            HistoryActionAvailability.historicalReviewAccessibilityHint(
                workerIsRunning: false,
                fallback: "Abre las propuestas para revisar."
            ),
            "Abre las propuestas para revisar."
        )
    }

    func testReviewedHistoryRemainsOpenForLocalInspectionWhenTCCBlocksMutation() {
        let permissionMessage = "Falta el permiso de Automatización para controlar Fotos."
        let access = HistoryReviewAccess(
            reviewed: true,
            reviewedApplyAvailable: true,
            freshReviewAvailable: false,
            mutationBlockMessage: permissionMessage
        )

        XCTAssertTrue(access.canOpen)
        XCTAssertTrue(access.isReadOnlyBecauseMutationIsBlocked)
        XCTAssertEqual(
            HistoryReviewActionCopy.title(
                reviewed: true,
                available: access.canOpen,
                mutationBlockMessage: access.mutationBlockMessage
            ),
            "Abrir revisión en solo lectura"
        )
        let hint = HistoryReviewActionCopy.accessibilityHint(
            reviewed: true,
            available: access.canOpen,
            requiresManualReview: false,
            sourceAvailable: true,
            mutationBlockMessage: access.mutationBlockMessage
        )
        XCTAssertTrue(hint.contains("sin consultar ni modificar Fotos"))
        XCTAssertTrue(hint.contains("permiso de Automatización"))
    }

    func testCompletedReviewedHistoryRemainsOpenForReadOnlyInspection() {
        let access = HistoryReviewAccess(
            reviewed: true,
            reviewedApplyAvailable: false,
            freshReviewAvailable: false,
            mutationBlockMessage: nil,
            reviewedReadOnlyAvailable: true
        )

        XCTAssertTrue(access.canOpen)
        XCTAssertTrue(access.isReadOnlyBecauseMutationIsBlocked)
        XCTAssertEqual(
            HistoryReviewActionCopy.title(
                reviewed: true,
                available: access.canOpen,
                mutationBlockMessage: access.mutationBlockMessage
            ),
            "Abrir revisión en solo lectura"
        )
    }

    func testQueryOnlyReviewedHistoryStaysUnavailableWhenTCCAlsoBlocksMutation() {
        let access = HistoryReviewAccess(
            reviewed: true,
            reviewedApplyAvailable: false,
            freshReviewAvailable: false,
            mutationBlockMessage: "Falta el permiso de Automatización."
        )

        XCTAssertFalse(access.canOpen)
        XCTAssertFalse(access.isReadOnlyBecauseMutationIsBlocked)
        XCTAssertNil(access.mutationBlockMessage)
    }

    func testManifestImportIsPausedWhileAnotherWorkerOperationIsRunning() {
        XCTAssertTrue(
            HistoryActionAvailability.canImportManifest(workerIsRunning: false)
        )
        XCTAssertFalse(
            HistoryActionAvailability.canImportManifest(workerIsRunning: true)
        )
        XCTAssertEqual(
            HistoryActionAvailability.manifestImportAccessibilityHint(workerIsRunning: true),
            "Hay otra operación en curso; espera a que termine antes de importar otro manifiesto."
        )
        XCTAssertEqual(
            HistoryActionAvailability.manifestImportAccessibilityHint(workerIsRunning: false),
            "Importa una copia local para consulta; aplicar o revertir seguirá requiriendo validación y confirmación."
        )
    }

    func testRollbackAccessibilityHintExplainsWhyDisabledBeforeMutationScope() {
        XCTAssertEqual(
            HistoryActionAvailability.accessibilityHint(workerIsRunning: true, requiresManualReview: false),
            "Hay otra operación en curso; espera a que termine antes de iniciar el rollback."
        )
        XCTAssertEqual(
            HistoryActionAvailability.accessibilityHint(workerIsRunning: false, requiresManualReview: true),
            "El rollback está pausado por revisión manual; revisa el run antes de intentar otra mutación."
        )
        XCTAssertEqual(
            HistoryActionAvailability.accessibilityHint(workerIsRunning: false, requiresManualReview: false),
            "Solo revierte las fotos con cambios verificados; las demás permanecen sin cambios."
        )
    }

    func testUnknownMutationStateRequiresManualReviewInsteadOfOptimisticHistoryAction() {
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/future-state/manifest.json"),
            preview: makeManifest(photos: [makePhoto(applyState: "future_apply_state")])
        )

        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertEqual(run.nextSafeActionText, "Este run no se puede revisar de forma segura; ejecuta un dry-run nuevo antes de aplicar")
    }

    func testRollbackEligibilityComesFromLoadedManifestStates() {
        let applied = makePhoto(
            applyState: "verified",
            rollbackState: "not_run",
            appliedKeywords: ["canal"]
        )
        let removed = makePhoto(applyState: "verified", rollbackState: "verified_removed")

        XCTAssertTrue(makeManifest(photos: [applied]).canRollback)
        XCTAssertFalse(makeManifest(photos: [removed]).canRollback)
    }

    func testRollbackEligibilityRequiresRecordedVerifiedChanges() {
        let verifiedWithoutEvidence = makePhoto(applyState: "verified", rollbackState: "not_run")
        let verifiedKeyword = makePhoto(
            applyState: "verified",
            rollbackState: "not_run",
            appliedKeywords: ["canal"]
        )
        let verifiedCaption = makePhoto(
            applyState: "verified",
            rollbackState: "not_run",
            appliedCaption: "Una vista visible.",
            captionState: "verified"
        )

        XCTAssertFalse(makeManifest(photos: [verifiedWithoutEvidence]).canRollback)
        XCTAssertTrue(makeManifest(photos: [verifiedKeyword]).canRollback)
        XCTAssertTrue(makeManifest(photos: [verifiedCaption]).canRollback)
    }

    func testRollbackRequiresMutationReceiptForSchemaThreeManifest() {
        let applied = makePhoto(
            applyState: "verified",
            rollbackState: "not_run",
            appliedKeywords: ["góndola"]
        )
        let preview = makeManifest(
            reviewedFromRunID: "source-run",
            schemaVersion: 3,
            photos: [applied]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/missing-receipt/manifest.json"),
            preview: preview
        )

        XCTAssertFalse(preview.hasValidMutationEvidence)
        XCTAssertFalse(run.canRollback)
        XCTAssertFalse(run.canContinueReviewedApply)
        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertTrue(run.attentionBannerText.contains("Evidencia de mutación"))
        XCTAssertTrue(run.nextSafeActionText.contains("dry-run nuevo"))
    }

    func testRollbackRejectsStaleMutationReceiptForSchemaThreeManifest() {
        let applied = makePhoto(
            applyState: "verified",
            rollbackState: "not_run",
            appliedKeywords: ["góndola"],
            mutationDigest: String(repeating: "0", count: 64)
        )
        let preview = makeManifest(
            reviewedFromRunID: "source-run",
            schemaVersion: 3,
            photos: [applied]
        )

        XCTAssertFalse(preview.hasValidMutationEvidence)
        XCTAssertFalse(preview.canRollback)
    }

    func testSchemaFourMutationReceiptMatchesPythonCanonicalJSONWithPhotoKitIdentifier() {
        let applied = makePhoto(
            uuid: "49F027C6-0000-4000-8000-000000000000",
            photosLocalIdentifier: "49F027C6-0000-4000-8000-000000000000/L0/001",
            applyState: "verified",
            rollbackState: "not_run",
            appliedKeywords: ["canal"],
            appliedCaption: "Un canal.",
            mutationDigest: "8fdd5a0fd7544987c24cf927807774f469fc3450e3eff5c92181a9c4ccc93145",
            captionState: "verified"
        )
        let preview = makeManifest(
            runID: "review-run",
            reviewedFromRunID: "source-run",
            schemaVersion: 4,
            scanDigest: "scan-digest",
            sourceScanDigest: "source-digest",
            photos: [applied]
        )

        XCTAssertTrue(preview.hasValidMutationEvidence)
        XCTAssertTrue(preview.canRollback)
    }

    func testHistoryDoesNotClaimRollbackVerifiedWithoutRollbackReceipt() {
        let removed = makePhoto(
            applyState: "verified",
            rollbackState: "verified_removed",
            appliedKeywords: ["góndola"],
            mutationDigest: "d93b1f8f9c54e0461ad4e5b0bbc4d905a796aa54e3c3814981ccd7634a82a18a"
        )
        let preview = makeManifest(
            reviewedFromRunID: "source-run",
            schemaVersion: 3,
            photos: [removed]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/missing-rollback-receipt/manifest.json"),
            preview: preview
        )

        XCTAssertFalse(preview.hasValidMutationEvidence)
        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertNotEqual(run.statusLabel, "Rollback verificado")
    }

    func testLegacyMutatedManifestDoesNotOfferRollbackOrClaimApplied() {
        let applied = makePhoto(
            applyState: "verified",
            rollbackState: "not_run",
            appliedKeywords: ["canal"]
        )
        let preview = makeManifest(schemaVersion: 2, photos: [applied])
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/legacy-mutated/manifest.json"),
            preview: preview
        )

        XCTAssertFalse(preview.canRollback)
        XCTAssertFalse(run.canRollback)
        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertTrue(run.nextSafeActionText.contains("dry-run nuevo"))
    }

    func testLegacyReceiptOnlyStateDoesNotReenterReview() {
        let receiptOnly = makePhoto(
            mutationDigest: String(repeating: "a", count: 64),
            proposedKeywords: []
        )
        let preview = makeManifest(schemaVersion: 2, photos: [receiptOnly])
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/legacy-receipt/manifest.json"),
            preview: preview
        )

        XCTAssertTrue(preview.hasLegacyMutationState)
        XCTAssertFalse(preview.canPrepareReview)
        XCTAssertEqual(run.status, .needsAttention)
    }

    func testMutationScopeOmitsVerifiedRowsWithoutRecordedChanges() {
        let verifiedWithoutEvidence = makePhoto(applyState: "verified", rollbackState: "not_run")
        let verifiedKeyword = makePhoto(
            applyState: "verified",
            rollbackState: "not_run",
            appliedKeywords: ["canal"]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/evidence/manifest.json"),
            preview: makeManifest(photos: [verifiedWithoutEvidence, verifiedKeyword])
        )

        XCTAssertEqual(run.mutationScopeText, "Verificado: 1 foto · 1 keyword · 0 captions.")
    }

    func testReviewIsUnavailableForMutatedRuns() {
        let applied = makePhoto(applyState: "verified")
        let rolledBack = makePhoto(applyState: "verified", rollbackState: "verified_removed")

        XCTAssertFalse(makeManifest(photos: [applied]).canPrepareReview)
        XCTAssertFalse(makeManifest(photos: [rolledBack]).canPrepareReview)
        XCTAssertEqual(
            makeManifest(photos: [applied]).reviewReadOnlyMessage,
            "Solo lectura: ejecuta un dry-run nuevo antes de revisar o aplicar."
        )
    }

    func testReviewIsUnavailableWhenAFreshRunContainsRollbackEvidence() {
        let receiptOnly = makePhoto(
            rolledBackKeywords: ["góndola"],
            rollbackDigest: String(repeating: "a", count: 64)
        )

        let preview = makeManifest(photos: [receiptOnly])

        XCTAssertFalse(preview.canPrepareReview)
        XCTAssertTrue(preview.hasLegacyMutationState == false)
    }

    func testReviewIsUnavailableForAReviewedCopy() {
        let reviewed = makeManifest(
            reviewedFromRunID: "49F027C6-0000-4000-8000-000000000001",
            photos: [makePhoto()]
        )

        XCTAssertFalse(reviewed.canPrepareReview)
    }

    func testReviewedCopyWithoutLocalSourceIsConsultationOnly() {
        let reviewed = makeManifest(
            reviewedFromRunID: "49F027C6-0000-4000-8000-000000000001",
            photos: [makePhoto()]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/reviewed/manifest.json"),
            preview: reviewed,
            localReviewSourceAvailable: false
        )

        XCTAssertFalse(run.canContinueReviewedApply)
        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertEqual(
            run.attentionBannerText,
            "Solo consulta: la fuente local no está disponible o no es verificable"
        )
        XCTAssertTrue(run.nextSafeActionText.contains("solo para consulta"))
    }

    func testQueryOnlyReviewedRunUsesUnambiguousHistoryStatus() {
        let reviewed = makeManifest(
            reviewedFromRunID: "49F027C6-0000-4000-8000-000000000001",
            photos: [makePhoto()]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/reviewed/manifest.json"),
            preview: reviewed,
            localReviewSourceAvailable: false
        )

        XCTAssertTrue(run.isConsultationOnly)
        XCTAssertEqual(run.statusLabel, "Solo consulta")
        XCTAssertTrue(run.reviewedApplyAccessibilityLabel.contains("solo para consulta"))
    }

    func testQueryOnlyReviewedRunNeverOffersRollbackWithoutVerifiedProvenance() {
        let reviewedApplied = makeManifest(
            reviewedFromRunID: "49F027C6-0000-4000-8000-000000000001",
            photos: [makePhoto(applyState: "verified", appliedKeywords: ["canal"])]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/reviewed/manifest.json"),
            preview: reviewedApplied,
            localReviewSourceAvailable: false
        )

        XCTAssertFalse(run.canRollback)
        XCTAssertEqual(run.rollbackActionText, "Rollback no disponible")
        XCTAssertTrue(run.nextSafeActionText.contains("solo para consulta"))
    }

    func testQueryOnlyReviewRecoveryExplainsHowToRestoreSafeProvenance() {
        let preview = makeManifest(
            reviewedFromRunID: "source-run",
            photos: [makePhoto()]
        )
        let summary = ReviewRecoverySummary(
            preview: preview,
            consultationOnly: true
        )

        XCTAssertTrue(summary.isVisible)
        XCTAssertEqual(summary.title, "Este manifest es solo para consulta")
        XCTAssertTrue(summary.detail.contains("dry-run fuente"))
        XCTAssertTrue(summary.detail.contains("dry-run nuevo"))
        XCTAssertEqual(summary.actionTitle, "Abrir Historial")
        XCTAssertTrue(summary.accessibilityLabel.contains("solo para consulta"))
    }

    func testReviewedApplyIsBlockedForFailedOrPartialRuns() {
        let cases: [(String, [PreviewPhoto])] = [
            ("failed", [makePhoto()]),
            ("ready_with_errors", [makePhoto(errors: [ManifestPreviewError(stage: "analysis", code: "ANALYSIS_FAILED")])]),
            ("ready", [makePhoto(applyState: "uncertain")])
        ]

        for (scanStatus, photos) in cases {
            let run = HistoryRunSummary(
                manifestURL: URL(fileURLWithPath: "/runs/reviewed/manifest.json"),
                preview: makeManifest(
                    scanStatus: scanStatus,
                    reviewedFromRunID: "source-run",
                    photos: photos
                ),
                localReviewSourceAvailable: true
            )

            XCTAssertFalse(run.canContinueReviewedApply, "Unexpected apply eligibility for \(scanStatus)")
            XCTAssertEqual(run.statusLabel, "Requiere atención")
            XCTAssertTrue(run.nextSafeActionText.contains("dry-run nuevo"))
            XCTAssertTrue(run.reviewedApplyAccessibilityLabel.contains("solo para consulta"))
        }
    }

    func testReviewedApplyAllowsValidRowsWhenReadyWithErrorsKeepsFailedRowsBlocked() {
        let failed = PreviewPhoto(
            uuid: "failed-row",
            title: "Foto con error",
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: nil,
            modelUsed: nil,
            state: "analysis_failed",
            errors: [ManifestPreviewError(stage: "analysis", code: "ANALYSIS_FAILED")]
        )
        let valid = makePhoto()
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/reviewed-partial/manifest.json"),
            preview: makeManifest(
                scanStatus: "ready_with_errors",
                reviewedFromRunID: "source-run",
                photos: [valid, failed]
            ),
            localReviewSourceAvailable: true
        )

        XCTAssertTrue(run.canContinueReviewedApply)
        XCTAssertEqual(run.statusLabel, "Listo para confirmar aplicación con advertencias")
        XCTAssertEqual(
            run.reviewedApplyAccessibilityLabel,
            "Confirmar aplicación de filas válidas; las filas con errores están bloqueadas"
        )
        XCTAssertEqual(run.attentionBannerText, "Revisión lista; las filas con errores quedan bloqueadas")
        XCTAssertEqual(
            run.nextSafeActionText,
            "Confirma la aplicación de las filas válidas; las filas con errores quedan bloqueadas"
        )
        XCTAssertFalse(failed.isReviewSelectable)
        XCTAssertTrue(ReviewApplyAvailability.canEnable(preview: run.preview, reviewedApplyAllowed: true))
    }

    func testReviewedApplyStaysBlockedWhenReadyWithErrorsHasNoApplicableRows() {
        let failed = PreviewPhoto(
            uuid: "failed-only-row",
            title: "Foto con error",
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: nil,
            modelUsed: nil,
            state: "analysis_failed",
            errors: [ManifestPreviewError(stage: "analysis", code: "ANALYSIS_FAILED")]
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/reviewed-failed-only/manifest.json"),
            preview: makeManifest(
                scanStatus: "ready_with_errors",
                reviewedFromRunID: "source-run",
                photos: [failed]
            ),
            localReviewSourceAvailable: true
        )

        XCTAssertFalse(run.canContinueReviewedApply)
        XCTAssertEqual(run.statusLabel, "Requiere atención")
    }

    func testReviewedApplyStaysBlockedWithLocalSourceAfterVerifiedMutation() {
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/reviewed/manifest.json"),
            preview: makeManifest(
                reviewedFromRunID: "source-run",
                photos: [makePhoto(applyState: "verified")]
            ),
            localReviewSourceAvailable: true
        )

        XCTAssertFalse(run.canContinueReviewedApply)
        XCTAssertFalse(
            ReviewApplyAvailability.canEnable(
                preview: run.preview,
                reviewedApplyAllowed: run.canContinueReviewedApply
            )
        )
        XCTAssertTrue(
            ReviewApplyAvailability.canEnable(
                preview: makeManifest(photos: [makePhoto()]),
                reviewedApplyAllowed: false
            )
        )
        XCTAssertEqual(run.reviewedApplyAccessibilityLabel, "Manifest revisado solo para consulta; requiere revisión manual y un dry-run nuevo")
    }

    func testReviewedApplyRemainsBlockedAfterRollbackMutation() {
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/reviewed/manifest.json"),
            preview: makeManifest(
                reviewedFromRunID: "source-run",
                photos: [makePhoto(applyState: "verified", rollbackState: "verified_removed")]
            ),
            localReviewSourceAvailable: true
        )

        XCTAssertFalse(run.canContinueReviewedApply)
        XCTAssertEqual(run.statusLabel, "Rollback verificado")
    }

    func testPreviewDecodesReviewedCopyProvenanceAndAppliedKeywords() throws {
        let data = Data(#"""
        {
          "run_id":"49F027C6-0000-4000-8000-000000000000",
          "created_at":"2026-08-25T16:00:00Z",
          "scan_status":"ready",
          "reviewed_from_run_id":"49F027C6-0000-4000-8000-000000000001",
          "photos":[{
            "uuid":"49F027C6-0000-4000-8000-000000000002",
            "title":"Canal",
            "date":"2026-08-25T16:00:00",
            "existing_keywords":[],
            "proposed_keywords":["canal"],
            "confidence":0.9,
            "scan_state":"ready",
            "apply_state":"verified",
            "applied_keywords":["canal"],
            "rollback_state":"not_run",
            "errors":[]
          }]
        }
        """#.utf8)

        let preview = try JSONDecoder().decode(RunManifestPreview.self, from: data)

        XCTAssertEqual(preview.reviewedFromRunID, "49F027C6-0000-4000-8000-000000000001")
        XCTAssertEqual(preview.photos.first?.appliedKeywords, ["canal"])
        XCTAssertFalse(preview.canPrepareReview)
    }

    func testPreviewRejectsDuplicatePhotoUUIDsBeforeReviewSelectionCanCrash() {
        let data = Data(#"""
        {
          "run_id":"duplicate-photos",
          "created_at":"2026-08-25T16:00:00Z",
          "scan_status":"ready",
          "photos":[
            {"uuid":"same-photo","title":"Primera","date":"2026-08-25T16:00:00","existing_keywords":[],"proposed_keywords":["canal"],"confidence":0.9,"scan_state":"ready","apply_state":"not_run","rollback_state":"not_run","errors":[]},
            {"uuid":"same-photo","title":"Segunda","date":"2026-08-25T16:01:00","existing_keywords":[],"proposed_keywords":["puente"],"confidence":0.9,"scan_state":"ready","apply_state":"not_run","rollback_state":"not_run","errors":[]}
          ]
        }
        """#.utf8)

        XCTAssertThrowsError(try JSONDecoder().decode(RunManifestPreview.self, from: data))
    }

    func testHistoryStatusUsesScanApplyRollbackAndSanitizedErrors() {
        let ready = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/ready/manifest.json"),
            preview: makeManifest(photos: [makePhoto()])
        )
        let applied = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/applied/manifest.json"),
            preview: makeManifest(photos: [makePhoto(applyState: "verified")])
        )
        let failed = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/failed/manifest.json"),
            preview: makeManifest(
                scanStatus: "ready_with_errors",
                photos: [makePhoto(errors: [ManifestPreviewError(stage: "analysis", code: "ANALYSIS_FAILED")])]
            )
        )
        let tainted = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/tainted/manifest.json"),
            preview: makeManifest(
                photos: [makePhoto(errors: [
                    ManifestPreviewError(stage: "analysis", code: "http://127.0.0.1/image-base64")
                ])]
            )
        )

        XCTAssertEqual(ready.status, .readyToReview)
        XCTAssertEqual(applied.status, .applied)
        XCTAssertEqual(failed.status, .needsAttention)
        XCTAssertEqual(failed.errorCodes, ["ANALYSIS_FAILED"])
        XCTAssertEqual(tainted.errorCodes, [])
        XCTAssertEqual(tainted.status, .needsAttention)
    }

    func testHistoryShowsHumanSafeErrorSummaryInsteadOfInternalCodes() {
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/missing-model/manifest.json"),
            preview: makeManifest(photos: [makePhoto(errors: [
                ManifestPreviewError(stage: "preflight", code: "OLLAMA_MODEL_MISSING")
            ])])
        )

        XCTAssertEqual(run.errorSummaryText, "Falta un modelo local; instala el modelo indicado.")
        XCTAssertFalse(run.errorSummaryText?.contains("OLLAMA_MODEL_MISSING") == true)
    }

    func testHistoryHidesUnknownErrorDetailsAndUsesSafeFallback() {
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/unknown-error/manifest.json"),
            preview: makeManifest(photos: [makePhoto(errors: [
                ManifestPreviewError(stage: "analysis", code: "http://127.0.0.1/private-image-base64")
            ])])
        )

        XCTAssertEqual(run.errorSummaryText, "La ejecución necesita revisión manual.")
        XCTAssertFalse(run.errorSummaryText?.contains("127.0.0.1") == true)
    }

    func testHistoryExplainsMutationValidationErrorsWithoutInternalCodes() {
        let codes = [
            "SCAN_NOT_READY",
            "REVIEW_PROVENANCE_INVALID",
            "MUTATION_EVIDENCE_INVALID",
            "REVIEW_NOT_PRISTINE",
            "ROLLBACK_ALREADY_STARTED",
            "PLATFORM_UNSUPPORTED",
            "LIMIT_INVALID",
            "MODEL_INVALID",
            "SCAN_SETUP_FAILED",
            "MANIFEST_WRITE_FAILED",
            "MANIFEST_INVALID",
            "LOCK_OR_MANIFEST_FAILED",
            "MANIFEST_PATH_INVALID"
        ]

        for code in codes {
            let run = HistoryRunSummary(
                manifestURL: URL(fileURLWithPath: "/runs/validation/manifest.json"),
                preview: makeManifest(photos: [makePhoto(errors: [
                    ManifestPreviewError(stage: "validation", code: code)
                ])])
            )

            XCTAssertNotEqual(run.errorSummaryText, "La ejecución necesita revisión manual.", code)
            XCTAssertFalse(run.errorSummaryText?.contains(code) == true, code)
            XCTAssertFalse(run.errorSummaryText?.contains("/") == true, code)
        }
    }

    func testHistorySurfacesSanitizedTopLevelRunErrors() throws {
        let data = Data(#"""
        {
          "run_id":"failed-run",
          "created_at":"2026-08-25T16:00:00Z",
          "scan_status":"failed",
          "run_errors":[{"stage":"preflight","code":"SCAN_NOT_READY"}],
          "photos":[]
        }
        """#.utf8)

        let preview = try JSONDecoder().decode(RunManifestPreview.self, from: data)
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/failed/manifest.json"),
            preview: preview
        )

        XCTAssertEqual(run.errorCodes, ["SCAN_NOT_READY"])
        XCTAssertEqual(
            run.errorSummaryText,
            "El análisis aún no está listo; ejecuta un dry-run nuevo antes de continuar."
        )
        XCTAssertFalse(run.errorSummaryText?.contains("preflight") == true)
    }

    func testHistorySurfacesUnsafeWorkflowResultWithSafeRecoveryCopy() throws {
        let data = Data(#"{"run_id":"unsafe-run","created_at":"2026-08-25T16:00:00Z","scan_status":"failed","run_errors":[{"stage":"workflow","code":"UNSAFE_WORKFLOW_RESULT"}],"photos":[]}"#.utf8)
        let preview = try JSONDecoder().decode(RunManifestPreview.self, from: data)
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/failed/manifest.json"),
            preview: preview
        )

        XCTAssertEqual(run.errorCodes, ["UNSAFE_WORKFLOW_RESULT"])
        XCTAssertEqual(
            run.errorSummaryText,
            "El helper local devolvió una respuesta incompatible; reinstala la app o usa una build válida y vuelve a comprobar."
        )
        XCTAssertFalse(run.errorSummaryText?.contains("workflow") == true)
    }

    func testHistorySurfacesUnsafeReviewResultWithSafeRecoveryCopy() throws {
        let data = Data(#"{"run_id":"unsafe-review","created_at":"2026-08-25T16:00:00Z","scan_status":"ready","run_errors":[{"stage":"review","code":"UNSAFE_REVIEW_RESULT"}],"photos":[]}"#.utf8)
        let preview = try JSONDecoder().decode(RunManifestPreview.self, from: data)
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/unsafe-review/manifest.json"),
            preview: preview
        )

        XCTAssertEqual(run.errorCodes, ["UNSAFE_REVIEW_RESULT"])
        XCTAssertEqual(
            run.errorSummaryText,
            "La revisión local no pudo validarse; crea una revisión nueva desde el dry-run."
        )
        XCTAssertEqual(
            run.nextSafeActionText,
            "Crea una revisión nueva desde el dry-run"
        )
        XCTAssertFalse(run.errorSummaryText?.contains("review") == true)
    }

    func testHistorySurfacesInvalidScanOptionsWithSafeRecoveryCopy() {
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/invalid-options/manifest.json"),
            preview: makeManifest(
                scanStatus: "failed",
                photos: [makePhoto(errors: [
                    ManifestPreviewError(stage: "scan", code: "SCAN_OPTIONS_INVALID")
                ])]
            )
        )

        XCTAssertEqual(run.errorCodes, ["SCAN_OPTIONS_INVALID"])
        XCTAssertEqual(
            run.errorSummaryText,
            "La configuración de la ejecución no es válida; revisa las opciones y ejecuta un dry-run nuevo."
        )
        XCTAssertFalse(run.errorSummaryText?.contains("SCAN_OPTIONS_INVALID") == true)
    }

    func testHistorySurfacesPermissionAndOllamaPreflightErrors() {
        let codes = [
            "PHOTOS_ACCESS_DENIED",
            "PHOTOS_AUTOMATION_DENIED",
            "PHOTOS_ACCESS_LIMITED",
            "OLLAMA_PREFLIGHT_FAILED"
        ]

        for code in codes {
            let run = HistoryRunSummary(
                manifestURL: URL(fileURLWithPath: "/runs/preflight/manifest.json"),
                preview: makeManifest(
                    scanStatus: "failed",
                    photos: [makePhoto(errors: [
                        ManifestPreviewError(stage: "preflight", code: code)
                    ])]
                )
            )

            XCTAssertEqual(run.errorCodes, [code], code)
            XCTAssertNotEqual(run.errorSummaryText, "La ejecución necesita revisión manual.", code)
            XCTAssertFalse(run.errorSummaryText?.contains(code) == true, code)
        }
    }

    func testHistoryPermissionErrorsNameTheRuntimeSurfaceAndExactRecoveryTarget() {
        let photoKitRun = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/photos-permission/manifest.json"),
            preview: makeManifest(
                scanStatus: "failed",
                photos: [makePhoto(errors: [
                    ManifestPreviewError(stage: "selection", code: "PHOTOS_ACCESS_DENIED")
                ])]
            )
        )

        XCTAssertTrue(photoKitRun.errorSummaryText?.contains("PhotoKit") == true)
        XCTAssertTrue(photoKitRun.errorSummaryText?.contains("Photos Local Keyword Indexer") == true)
        XCTAssertTrue(photoKitRun.errorSummaryText?.contains("PhotosIndexerWorker") == true)
        XCTAssertTrue(photoKitRun.nextSafeActionText.contains("PhotoKit"))
        XCTAssertTrue(photoKitRun.nextSafeActionText.contains("Photos Local Keyword Indexer"))
        XCTAssertFalse(photoKitRun.nextSafeActionText.contains("/Users/"))

        let automationRun = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/automation-permission/manifest.json"),
            preview: makeManifest(
                scanStatus: "failed",
                photos: [makePhoto(errors: [
                    ManifestPreviewError(stage: "preflight", code: "PHOTOS_AUTOMATION_DENIED")
                ])]
            )
        )

        XCTAssertTrue(automationRun.errorSummaryText?.contains("Apple Events") == true)
        XCTAssertTrue(automationRun.errorSummaryText?.contains("PhotosIndexerWorker") == true)
        XCTAssertFalse(automationRun.errorSummaryText?.contains("PhotoKit") == true)
        XCTAssertTrue(automationRun.nextSafeActionText.contains("PhotosIndexerWorker"))
        XCTAssertTrue(automationRun.nextSafeActionText.contains("Automatización"))
        XCTAssertFalse(automationRun.nextSafeActionText.contains("/"))
    }

    func testHistorySurfacesPhotoScriptUnavailableWithSafeRecoveryAction() {
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/photoscript/manifest.json"),
            preview: makeManifest(
                scanStatus: "failed",
                photos: [makePhoto(errors: [
                    ManifestPreviewError(stage: "export", code: "PHOTOSCRIPT_UNAVAILABLE")
                ])]
            )
        )

        XCTAssertEqual(run.errorCodes, ["PHOTOSCRIPT_UNAVAILABLE"])
        XCTAssertEqual(
            run.errorSummaryText,
            "PhotoScript no pudo cargar su puente AppleScript; comprueba la compatibilidad de Photos, PhotoScript y macOS, y ejecuta un dry-run nuevo."
        )
        XCTAssertEqual(
            run.nextSafeActionText,
            "Comprueba la compatibilidad local de Photos y PhotoScript, y ejecuta un dry-run nuevo"
        )
        XCTAssertFalse(run.errorSummaryText?.contains("PHOTOSCRIPT_UNAVAILABLE") == true)
        XCTAssertFalse(run.errorSummaryText?.contains("/AppleScript") == true)
        XCTAssertFalse(run.errorSummaryText?.contains("http") == true)
    }

    func testHistorySurfacesHelperUnavailableWithSafeRecoveryAction() {
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/helper/manifest.json"),
            preview: makeManifest(
                scanStatus: "failed",
                photos: [makePhoto(errors: [
                    ManifestPreviewError(stage: "preflight", code: "HELPER_UNAVAILABLE")
                ])]
            )
        )

        XCTAssertEqual(run.errorCodes, ["HELPER_UNAVAILABLE"])
        XCTAssertEqual(
            run.nextSafeActionText,
            "Reinstala la app o usa una build válida y vuelve a comprobar"
        )
        XCTAssertFalse(run.errorSummaryText?.contains("HELPER_UNAVAILABLE") == true)
        XCTAssertFalse(run.nextSafeActionText.contains("/"))
    }

    func testHistorySortsNewestManifestFirst() {
        let older = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/older/manifest.json"),
            preview: makeManifest(runID: "older", createdAt: "2025-04-13T20:00:00Z", photos: [makePhoto()])
        )
        let newer = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/newer/manifest.json"),
            preview: makeManifest(runID: "newer", createdAt: "2026-08-25T16:00:00Z", photos: [makePhoto()])
        )

        XCTAssertEqual(HistoryRunSummary.newestFirst([older, newer]).map(\.runID), ["newer", "older"])
        XCTAssertEqual(newer.displayDate, "2026-08-25T16:00:00Z")
        XCTAssertEqual(newer.abbreviatedUUID, "49F027C6")
    }

    func testHistoryRunStoreRejectsAnUnexpectedRunsRoot() {
        let expected = URL(fileURLWithPath: "/Users/example/Library/Application Support/Photos Local Keyword Indexer/runs")
        let unexpected = URL(fileURLWithPath: "/tmp/external-runs")

        XCTAssertFalse(HistoryRunStore.isTrustedRunsRoot(unexpected, expected: expected))
        XCTAssertTrue(HistoryRunStore.isTrustedRunsRoot(expected, expected: expected))
    }

    func testHistoryRunStoreRejectsASymlinkedParent() throws {
        let temporary = try makeTemporaryDirectory()
        let external = temporary.appendingPathComponent("external", isDirectory: true)
        try FileManager.default.createDirectory(at: external, withIntermediateDirectories: true)
        let support = temporary.appendingPathComponent("Support", isDirectory: true)
        try FileManager.default.createSymbolicLink(at: support, withDestinationURL: external)
        let expected = support.appendingPathComponent("Photos Local Keyword Indexer/runs", isDirectory: true)

        XCTAssertFalse(HistoryRunStore.isTrustedRunsRoot(expected, expected: expected))
        try? FileManager.default.removeItem(at: temporary)
    }

    func testHistoryRunStoreEnumeratesValidRunsAndSkipsSymlinkedRuns() throws {
        let temporary = try makeTemporaryDirectory()
        let run = temporary.appendingPathComponent("run-1", isDirectory: true)
        try FileManager.default.createDirectory(at: run, withIntermediateDirectories: true)
        let manifest = run.appendingPathComponent("manifest.json")
        try Data(#"{"run_id":"run-1","created_at":"2026-08-25T16:00:00Z","scan_status":"ready","photos":[{"uuid":"49F027C6-0000-4000-8000-000000000000","title":"","date":"2026-08-25T16:00:00","existing_keywords":[],"proposed_keywords":[],"confidence":0.9,"scan_state":"noop","apply_state":"not_run","rollback_state":"not_run","errors":[]}]}"#.utf8).write(to: manifest)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: manifest.path)
        let symlink = temporary.appendingPathComponent("run-link", isDirectory: true)
        try FileManager.default.createSymbolicLink(at: symlink, withDestinationURL: run)

        let runs = HistoryRunStore.load(from: temporary, expectedRoot: temporary)

        XCTAssertEqual(runs.count, 1)
        XCTAssertEqual(runs.first?.runID, "run-1")
        try? FileManager.default.removeItem(at: temporary)
    }

    func testHistoryLoadResultExplainsRejectedManifestWithoutRawDetails() {
        let result = HistoryLoadResult(runs: [], rejectedRunCount: 2)

        XCTAssertEqual(result.runs, [])
        XCTAssertTrue(result.rejectedRunNotice?.contains("2 ejecuciones locales") == true)
        XCTAssertTrue(result.rejectedRunNotice?.contains("dry-run nuevo") == true)
        XCTAssertFalse(result.rejectedRunNotice?.contains("/") == true)
        XCTAssertFalse(result.rejectedRunNotice?.contains("JSON") == true)
    }

    func testHistoryLoadResultCountsUnreadableManifestInsteadOfDroppingItSilently() throws {
        let temporary = try makeTemporaryDirectory()
        let run = temporary.appendingPathComponent("invalid-run", isDirectory: true)
        try FileManager.default.createDirectory(at: run, withIntermediateDirectories: true)
        try Data("not a manifest".utf8).write(to: run.appendingPathComponent("manifest.json"))
        defer { try? FileManager.default.removeItem(at: temporary) }

        let result = HistoryRunStore.loadResult(from: temporary, expectedRoot: temporary)

        XCTAssertEqual(result.runs, [])
        XCTAssertEqual(result.rejectedRunCount, 1)
        XCTAssertNotNil(result.rejectedRunNotice)
    }

    func testHistoryRunStoreCopiesManifestAndCompanionCSVWithoutMovingSource() throws {
        let temporary = try makeTemporaryDirectory()
        let sourceRun = temporary.appendingPathComponent("source-run", isDirectory: true)
        let destinationRoot = temporary.appendingPathComponent("app-runs", isDirectory: true)
        try FileManager.default.createDirectory(at: sourceRun, withIntermediateDirectories: true)
        let sourceManifest = sourceRun.appendingPathComponent("manifest.json")
        try Data(#"{"run_id":"imported-run","created_at":"2026-08-25T16:00:00Z","scan_status":"ready","photos":[]}"#.utf8)
            .write(to: sourceManifest)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: sourceManifest.path)
        let sourceCSV = sourceRun.appendingPathComponent("preview.csv")
        try Data("uuid,estado\n".utf8).write(to: sourceCSV)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: sourceCSV.path)
        defer { try? FileManager.default.removeItem(at: temporary) }

        let importedManifest = try HistoryRunStore.importManifest(from: sourceManifest, to: destinationRoot)

        XCTAssertTrue(FileManager.default.fileExists(atPath: sourceManifest.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: importedManifest.path))
        XCTAssertTrue(FileManager.default.fileExists(atPath: importedManifest.deletingLastPathComponent().appendingPathComponent("preview.csv").path))
        XCTAssertEqual(try RunManifestPreview.load(from: importedManifest).runID, "imported-run")
        XCTAssertEqual(HistoryRunStore.load(from: destinationRoot, expectedRoot: destinationRoot).map(\.runID), ["imported-run"])
    }

    func testImportCopyMessageMakesReviewedQueryOnlyStateExplicit() {
        let reviewed = makeManifest(
            reviewedFromRunID: "source-run",
            photos: [makePhoto()]
        )

        XCTAssertEqual(
            HistoryImportCopy.message(preview: reviewed, sourceAvailable: false),
            "Manifest revisado importado como copia local, pero queda solo para consulta porque no se encontró una fuente local verificable. Usa «Importar manifest» para importar también el dry-run fuente o ejecuta un dry-run nuevo antes de aplicar."
        )
        XCTAssertTrue(
            HistoryImportCopy.message(preview: reviewed, sourceAvailable: true)
                .contains("fuente local está disponible")
        )
    }

    func testImportingReviewedManifestCopiesCoherentSourceAndKeepsApplyAvailable() throws {
        let temporary = try makeTemporaryDirectory()
        let sourceRoot = temporary.appendingPathComponent("runs", isDirectory: true)
        let sourceRun = sourceRoot.appendingPathComponent("source", isDirectory: true)
        let reviewedRun = sourceRoot.appendingPathComponent("reviewed", isDirectory: true)
        let destinationRoot = temporary.appendingPathComponent("app-runs", isDirectory: true)
        try FileManager.default.createDirectory(at: sourceRun, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: reviewedRun, withIntermediateDirectories: true)
        let digest = String(repeating: "a", count: 64)
        let model = "\"model\":{\"policy\":\"single\",\"fast_name\":\"qwen3-vl:4b\",\"detailed_name\":\"qwen3-vl:8b\",\"ollama_version\":\"0.12.7\",\"endpoint\":\"http://127.0.0.1:11434\"},\"policy\":{\"id\":\"es-visible-v2\",\"taxonomy_sha256\":\"" + String(repeating: "d", count: 64) + "\",\"max_keywords\":8,\"confidence_threshold\":0.6}"
        let photo = #"{"uuid":"49F027C6-0000-4000-8000-000000000010","title":"Canal","date":"2026-08-25T16:00:00","existing_keywords":[],"proposed_keywords":["canal"],"confidence":0.9,"scan_state":"ready","apply_state":"not_run","rollback_state":"not_run","errors":[]}"#
        let sourceJSON = "{\"run_id\":\"49F027C6-0000-4000-8000-000000000010\",\"created_at\":\"2026-08-25T16:00:00Z\",\"scan_status\":\"ready\",\"scan_digest\":\"" + digest + "\"," + model + ",\"photos\":[" + photo + "]}"
        let reviewedJSON = "{\"run_id\":\"49F027C6-0000-4000-8000-000000000011\",\"created_at\":\"2026-08-25T16:01:00Z\",\"scan_status\":\"ready\",\"reviewed_from_run_id\":\"49F027C6-0000-4000-8000-000000000010\",\"source_scan_digest\":\"" + digest + "\"," + model + ",\"photos\":[" + photo + "]}"
        let sourceData = Data(sourceJSON.utf8)
        let reviewedData = Data(reviewedJSON.utf8)
        let sourceManifest = sourceRun.appendingPathComponent("manifest.json")
        let reviewedManifest = reviewedRun.appendingPathComponent("manifest.json")
        try sourceData.write(to: sourceManifest)
        try reviewedData.write(to: reviewedManifest)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: sourceManifest.path)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: reviewedManifest.path)
        defer { try? FileManager.default.removeItem(at: temporary) }

        _ = try HistoryRunStore.importManifest(from: reviewedManifest, to: destinationRoot)
        let runs = HistoryRunStore.load(from: destinationRoot, expectedRoot: destinationRoot)
        let importedReviewed = try XCTUnwrap(runs.first(where: { $0.runID == "49F027C6-0000-4000-8000-000000000011" }))

        XCTAssertTrue(importedReviewed.canContinueReviewedApply)
        XCTAssertTrue(importedReviewed.localReviewSourceAvailable)
        XCTAssertEqual(importedReviewed.statusLabel, "Listo para confirmar aplicación")
    }

    func testVersionlessRoutedManifestRemainsImportableForLegacyHistory() throws {
        let data = Data(#"{"run_id":"legacy-routed","created_at":"2026-08-25T16:00:00Z","scan_status":"ready","model":{"policy":"single","fast_name":"qwen3-vl:4b","detailed_name":"qwen3-vl:8b"},"photos":[]}"#.utf8)

        XCTAssertNoThrow(try RunManifestPreview.decodeStrict(data))
    }

    func testManifestWithAnUnsupportedSchemaVersionIsRejectedBeforeHistoryGates() {
        let data = Data(#"{"run_id":"future-schema","created_at":"2026-08-25T16:00:00Z","scan_status":"ready","schema_version":5,"photos":[]}"#.utf8)

        XCTAssertThrowsError(try RunManifestPreview.decodeStrict(data))
    }

    func testImportingReviewedManifestWithChangedImmutablePhotoIsQueryOnly() throws {
        let temporary = try makeTemporaryDirectory()
        let sourceRoot = temporary.appendingPathComponent("runs", isDirectory: true)
        let sourceRun = sourceRoot.appendingPathComponent("source", isDirectory: true)
        let reviewedRun = sourceRoot.appendingPathComponent("reviewed", isDirectory: true)
        let destinationRoot = temporary.appendingPathComponent("app-runs", isDirectory: true)
        try FileManager.default.createDirectory(at: sourceRun, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: reviewedRun, withIntermediateDirectories: true)
        let digest = String(repeating: "b", count: 64)
        let photo = #"{"uuid":"49F027C6-0000-4000-8000-000000000012","title":"Canal","date":"2026-08-25T16:00:00","existing_keywords":[],"proposed_keywords":["canal"],"confidence":0.9,"scan_state":"ready","apply_state":"not_run","rollback_state":"not_run","errors":[]}"#
        let changedPhoto = photo.replacingOccurrences(of: "Canal", with: "Otro edificio")
        let sourceJSON = "{\"run_id\":\"49F027C6-0000-4000-8000-000000000010\",\"created_at\":\"2026-08-25T16:00:00Z\",\"scan_status\":\"ready\",\"scan_digest\":\"" + digest + "\",\"photos\":[" + photo + "]}"
        let reviewedJSON = "{\"run_id\":\"49F027C6-0000-4000-8000-000000000011\",\"created_at\":\"2026-08-25T16:01:00Z\",\"scan_status\":\"ready\",\"reviewed_from_run_id\":\"49F027C6-0000-4000-8000-000000000010\",\"source_scan_digest\":\"" + digest + "\",\"photos\":[" + changedPhoto + "]}"
        let sourceManifest = sourceRun.appendingPathComponent("manifest.json")
        let reviewedManifest = reviewedRun.appendingPathComponent("manifest.json")
        try Data(sourceJSON.utf8).write(to: sourceManifest)
        try Data(reviewedJSON.utf8).write(to: reviewedManifest)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: sourceManifest.path)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: reviewedManifest.path)
        defer { try? FileManager.default.removeItem(at: temporary) }

        _ = try HistoryRunStore.importManifest(from: reviewedManifest, to: destinationRoot)
        let runs = HistoryRunStore.load(from: destinationRoot, expectedRoot: destinationRoot)
        let importedReviewed = try XCTUnwrap(runs.first(where: { $0.runID == "49F027C6-0000-4000-8000-000000000011" }))

        XCTAssertEqual(runs.map(\.runID), ["49F027C6-0000-4000-8000-000000000011"])
        XCTAssertFalse(importedReviewed.localReviewSourceAvailable)
        XCTAssertFalse(importedReviewed.canContinueReviewedApply)
        XCTAssertEqual(importedReviewed.statusLabel, "Solo consulta")
    }

    func testImportingReviewedManifestWithChangedModelPlanIsQueryOnly() throws {
        let temporary = try makeTemporaryDirectory()
        let sourceRoot = temporary.appendingPathComponent("runs", isDirectory: true)
        let sourceRun = sourceRoot.appendingPathComponent("source", isDirectory: true)
        let reviewedRun = sourceRoot.appendingPathComponent("reviewed", isDirectory: true)
        let destinationRoot = temporary.appendingPathComponent("app-runs", isDirectory: true)
        try FileManager.default.createDirectory(at: sourceRun, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: reviewedRun, withIntermediateDirectories: true)
        let digest = String(repeating: "c", count: 64)
        let sourceJSON = "{\"run_id\":\"49F027C6-0000-4000-8000-000000000020\",\"created_at\":\"2026-08-25T16:00:00Z\",\"scan_status\":\"ready\",\"scan_digest\":\"" + digest + "\",\"model\":{\"policy\":\"adaptive\",\"fast_name\":\"qwen3-vl:4b\",\"detailed_name\":\"qwen3-vl:8b\"},\"policy\":{\"id\":\"es-visible-v2\",\"max_keywords\":8},\"photos\":[]}"
        let reviewedJSON = "{\"run_id\":\"49F027C6-0000-4000-8000-000000000021\",\"created_at\":\"2026-08-25T16:01:00Z\",\"scan_status\":\"ready\",\"reviewed_from_run_id\":\"49F027C6-0000-4000-8000-000000000020\",\"source_scan_digest\":\"" + digest + "\",\"model\":{\"policy\":\"single\",\"fast_name\":\"qwen3-vl:4b\",\"detailed_name\":\"qwen3-vl:8b\"},\"policy\":{\"id\":\"es-visible-v2\",\"max_keywords\":8},\"photos\":[]}"
        let sourceManifest = sourceRun.appendingPathComponent("manifest.json")
        let reviewedManifest = reviewedRun.appendingPathComponent("manifest.json")
        try Data(sourceJSON.utf8).write(to: sourceManifest)
        try Data(reviewedJSON.utf8).write(to: reviewedManifest)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: sourceManifest.path)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: reviewedManifest.path)
        defer { try? FileManager.default.removeItem(at: temporary) }

        _ = try HistoryRunStore.importManifest(from: reviewedManifest, to: destinationRoot)
        let runs = HistoryRunStore.load(from: destinationRoot, expectedRoot: destinationRoot)
        let importedReviewed = try XCTUnwrap(runs.first(where: { $0.runID == "49F027C6-0000-4000-8000-000000000021" }))

        XCTAssertEqual(runs.map(\.runID), ["49F027C6-0000-4000-8000-000000000021"])
        XCTAssertFalse(importedReviewed.localReviewSourceAvailable)
        XCTAssertFalse(importedReviewed.canContinueReviewedApply)
    }

    func testImportingReviewedManifestWithFailedSourceDoesNotCopySource() throws {
        let temporary = try makeTemporaryDirectory()
        let sourceRoot = temporary.appendingPathComponent("runs", isDirectory: true)
        let sourceRun = sourceRoot.appendingPathComponent("source", isDirectory: true)
        let reviewedRun = sourceRoot.appendingPathComponent("reviewed", isDirectory: true)
        let destinationRoot = temporary.appendingPathComponent("app-runs", isDirectory: true)
        try FileManager.default.createDirectory(at: sourceRun, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: reviewedRun, withIntermediateDirectories: true)
        let digest = String(repeating: "e", count: 64)
        let model = "\"model\":{\"policy\":\"single\",\"fast_name\":\"qwen3-vl:4b\",\"detailed_name\":\"qwen3-vl:8b\",\"ollama_version\":\"0.12.7\",\"endpoint\":\"http://127.0.0.1:11434\"},\"policy\":{\"id\":\"es-visible-v2\",\"taxonomy_sha256\":\"" + String(repeating: "f", count: 64) + "\",\"max_keywords\":8,\"confidence_threshold\":0.6}"
        let sourceJSON = "{\"run_id\":\"49F027C6-0000-4000-8000-000000000030\",\"created_at\":\"2026-08-25T16:00:00Z\",\"scan_status\":\"failed\",\"scan_digest\":\"" + digest + "\"," + model + ",\"photos\":[]}"
        let reviewedJSON = "{\"run_id\":\"49F027C6-0000-4000-8000-000000000031\",\"created_at\":\"2026-08-25T16:01:00Z\",\"scan_status\":\"ready\",\"reviewed_from_run_id\":\"49F027C6-0000-4000-8000-000000000030\",\"source_scan_digest\":\"" + digest + "\"," + model + ",\"photos\":[]}"
        let sourceManifest = sourceRun.appendingPathComponent("manifest.json")
        let reviewedManifest = reviewedRun.appendingPathComponent("manifest.json")
        try Data(sourceJSON.utf8).write(to: sourceManifest)
        try Data(reviewedJSON.utf8).write(to: reviewedManifest)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: sourceManifest.path)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: reviewedManifest.path)
        defer { try? FileManager.default.removeItem(at: temporary) }

        _ = try HistoryRunStore.importManifest(from: reviewedManifest, to: destinationRoot)
        let runs = HistoryRunStore.load(from: destinationRoot, expectedRoot: destinationRoot)
        let importedReviewed = try XCTUnwrap(runs.first(where: { $0.runID == "49F027C6-0000-4000-8000-000000000031" }))

        XCTAssertEqual(runs.map(\.runID), ["49F027C6-0000-4000-8000-000000000031"])
        XCTAssertFalse(importedReviewed.localReviewSourceAvailable)
        XCTAssertFalse(importedReviewed.canContinueReviewedApply)
        XCTAssertEqual(importedReviewed.statusLabel, "Solo consulta")
    }

    func testHistoryRunStoreRejectsSymlinkedRunsRootWithAnExternalManifest() throws {
        let temporary = try makeTemporaryDirectory()
        let external = temporary.appendingPathComponent("external-runs", isDirectory: true)
        let externalRun = external.appendingPathComponent("run-external", isDirectory: true)
        try FileManager.default.createDirectory(at: externalRun, withIntermediateDirectories: true)
        let manifest = externalRun.appendingPathComponent("manifest.json")
        try Data(#"{"run_id":"run-external","created_at":"2026-08-25T16:00:00Z","scan_status":"ready","photos":[{"uuid":"49F027C6-0000-4000-8000-000000000000","title":"Externo","date":"2026-08-25T16:00:00","existing_keywords":[],"proposed_keywords":[],"confidence":0.9,"scan_state":"noop","apply_state":"not_run","rollback_state":"not_run","errors":[]}]}"#.utf8).write(to: manifest)

        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: manifest.path)
        let runsRoot = temporary.appendingPathComponent("runs", isDirectory: true)
        try FileManager.default.createSymbolicLink(at: runsRoot, withDestinationURL: external)

        let runs = HistoryRunStore.load(from: runsRoot, expectedRoot: runsRoot)

        XCTAssertTrue(runs.isEmpty)
        try? FileManager.default.removeItem(at: temporary)
    }

    func testRunManifestRejectsAWorldReadableManifest() throws {
        let temporary = try makeManifestFile(permissions: 0o644)
        defer { try? FileManager.default.removeItem(at: temporary.deletingLastPathComponent()) }

        XCTAssertThrowsError(try RunManifestPreview.load(from: temporary)) { error in
            XCTAssertEqual(error as? ManifestPreviewLoadError, .insecurePermissions)
        }
    }

    func testRunManifestAcceptsTrustedSystemTemporaryAlias() throws {
        let directory = URL(fileURLWithPath: "/tmp", isDirectory: true)
            .appendingPathComponent("manifest-alias-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: directory) }
        let manifest = directory.appendingPathComponent("manifest.json")
        try Data(#"{"run_id":"run-1","created_at":"2026-08-25T16:00:00Z","scan_status":"ready","photos":[]}"#.utf8)
            .write(to: manifest)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: manifest.path)

        XCTAssertNoThrow(try RunManifestPreview.load(from: manifest))
    }

    func testRunManifestRejectsAManifestWithAnUnexpectedOwner() {
        let currentUID = UInt64(getuid())
        let metadata = ManifestFileMetadata(
            ownerID: currentUID + 1,
            mode: 0o600,
            linkCount: 1,
            size: 128,
            device: 1,
            inode: 2,
            modificationSeconds: 3,
            modificationNanoseconds: 4
        )

        XCTAssertThrowsError(try ManifestFileSecurity.validate(metadata: metadata)) { error in
            XCTAssertEqual(error as? ManifestPreviewLoadError, .unexpectedOwner)
        }
    }

    func testRunManifestRejectsHardLinks() throws {
        let manifest = try makeManifestFile()
        defer { try? FileManager.default.removeItem(at: manifest.deletingLastPathComponent()) }
        let hardLink = manifest.deletingLastPathComponent().appendingPathComponent("manifest-copy.json")
        try FileManager.default.linkItem(at: manifest, to: hardLink)

        XCTAssertThrowsError(try RunManifestPreview.load(from: hardLink)) { error in
            XCTAssertEqual(error as? ManifestPreviewLoadError, .hardLinked)
        }
    }

    func testRunManifestRejectsFilesLargerThanFourMiBBeforeDecoding() throws {
        let manifest = try makeManifestFile(data: Data(repeating: 0x20, count: 4 * 1024 * 1024 + 1))
        defer { try? FileManager.default.removeItem(at: manifest.deletingLastPathComponent()) }

        XCTAssertThrowsError(try RunManifestPreview.load(from: manifest)) { error in
            XCTAssertEqual(error as? ManifestPreviewLoadError, .tooLarge)
        }
    }

    func testRunManifestRejectsDuplicateJSONKeysBeforeReview() throws {
        let duplicated = Data(
            #"{"run_id":"run-1","created_at":"2026-08-25T16:00:00Z","scan_status":"ready","scan_\u0073tatus":"failed","photos":[]}"#.utf8
        )
        let manifest = try makeManifestFile(data: duplicated)
        defer { try? FileManager.default.removeItem(at: manifest.deletingLastPathComponent()) }

        XCTAssertThrowsError(try RunManifestPreview.load(from: manifest)) { error in
            XCTAssertEqual(error as? ManifestPreviewLoadError, .invalidJSON)
        }
    }

    func testRunManifestDoesNotFollowFinalSymlink() throws {
        let manifest = try makeManifestFile()
        defer { try? FileManager.default.removeItem(at: manifest.deletingLastPathComponent()) }
        let symlink = manifest.deletingLastPathComponent().appendingPathComponent("manifest-link.json")
        try FileManager.default.createSymbolicLink(at: symlink, withDestinationURL: manifest)

        XCTAssertThrowsError(try RunManifestPreview.load(from: symlink)) { error in
            XCTAssertEqual(error as? ManifestPreviewLoadError, .notRegularFile)
        }
    }

    func testRunManifestRejectsMetadataChangedBetweenReadAndFinalFstat() {
        let before = ManifestFileMetadata(
            ownerID: UInt64(getuid()), mode: 0o600, linkCount: 1, size: 128,
            device: 1, inode: 2, modificationSeconds: 3, modificationNanoseconds: 4
        )
        let after = ManifestFileMetadata(
            ownerID: UInt64(getuid()), mode: 0o600, linkCount: 1, size: 256,
            device: 1, inode: 2, modificationSeconds: 3, modificationNanoseconds: 4
        )

        XCTAssertThrowsError(try ManifestFileSecurity.validateStable(before: before, after: after)) { error in
            XCTAssertEqual(error as? ManifestPreviewLoadError, .changedDuringRead)
        }
    }

    func testSuccessfulHistoryReviewNavigatesToReviewRoute() {
        var navigation = AppNavigationState()

        XCTAssertTrue(navigation.openHistoryReview(loadSucceeded: true))
        XCTAssertEqual(navigation.route, .preview)

        _ = navigation.navigate(to: .history, preparation: PreparationState())
        XCTAssertFalse(navigation.openHistoryReview(loadSucceeded: false))
        XCTAssertEqual(navigation.route, .history)
    }

    func testReviewSummaryKeepsKeywordAndCaptionApprovalsSeparate() {
        let summary = ReviewApprovalSummary(photoCount: 2, keywordCount: 4, captionCount: 1)

        XCTAssertEqual(summary.photoText, "2 fotos afectadas")
        XCTAssertEqual(summary.keywordText, "4 keywords aprobadas")
        XCTAssertEqual(summary.captionText, "1 caption aprobado")
        XCTAssertEqual(summary.nextSafeAction, "Preparar un manifiesto revisado antes de aplicar")

        let reviewed = ReviewApprovalSummary(photoCount: 2, keywordCount: 4, captionCount: 1, hasReviewedManifest: true)
        XCTAssertEqual(reviewed.nextSafeAction, "Confirma la aplicación del manifiesto revisado")
    }

    func testHistoryConfidenceSummaryIgnoresScoresOutsideTheModelContract() {
        let preview = makeManifest(photos: [
            makePhoto(uuid: "valid-confidence", confidence: 0.80),
            makePhoto(uuid: "invalid-confidence", confidence: 1.20),
        ])
        let summary = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/confidence/manifest.json"),
            preview: preview
        )

        XCTAssertEqual(summary.confidenceText, "Confianza media 80%")
        XCTAssertFalse(summary.confidenceText.contains("120%"))

        let invalidOnly = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/invalid-confidence/manifest.json"),
            preview: makeManifest(photos: [makePhoto(confidence: -0.10)])
        )
        XCTAssertEqual(invalidOnly.confidenceText, "Confianza no disponible")
    }

    func testHistoryConfidenceWarningCountsOnlyValidLowScores() {
        let preview = makeManifest(photos: [
            makePhoto(uuid: "low-confidence", confidence: 0.59),
            makePhoto(uuid: "threshold-confidence", confidence: 0.60),
            makePhoto(uuid: "invalid-confidence", confidence: 1.20),
            makePhoto(uuid: "missing-confidence", confidence: nil),
        ])
        let summary = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/confidence-warning/manifest.json"),
            preview: preview
        )

        XCTAssertEqual(summary.lowConfidenceText, "1 foto tiene confianza baja; no se propusieron cambios aplicables.")

        let failedLowConfidence = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/confidence-failed/manifest.json"),
            preview: makeManifest(photos: [
                makePhoto(
                    confidence: 0.59,
                    state: "analysis_failed",
                    proposedKeywords: [],
                    errors: [ManifestPreviewError(stage: "analysis", code: "ANALYSIS_FAILED")]
                )
            ])
        )
        XCTAssertEqual(
            failedLowConfidence.lowConfidenceText,
            "1 foto tiene confianza baja; no se propusieron cambios aplicables."
        )

        let clear = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/confidence-clear/manifest.json"),
            preview: makeManifest(photos: [makePhoto(confidence: 0.60)])
        )
        XCTAssertNil(clear.lowConfidenceText)
    }

    func testHistoryDisplayTitleSkipsBlankTitlesAndCompactsWhitespace() {
        let preview = makeManifest(photos: [
            makePhoto(uuid: "blank-title", title: "  \n\t  "),
            makePhoto(uuid: "visible-title", title: "  Canal\n\nGrande  "),
        ])
        let summary = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/display-title/manifest.json"),
            preview: preview
        )

        XCTAssertEqual(summary.displayTitle, "Canal Grande")
    }

    func testReviewSummaryNextActionNamesBlockedRowsForFreshPartialRuns() {
        let summary = ReviewApprovalSummary(
            photoCount: 1,
            keywordCount: 2,
            captionCount: 0,
            hasWarnings: true
        )
        XCTAssertEqual(
            summary.nextSafeAction,
            "Prepara una revisión solo con las filas válidas; las filas con errores quedan bloqueadas"
        )
    }

    func testReviewSummaryExplainsThatASelectionIsRequiredBeforePreparation() {
        let summary = ReviewApprovalSummary(keywordCount: 0, captionCount: 0)

        XCTAssertEqual(
            summary.nextSafeAction,
            "Selecciona al menos una keyword o caption para preparar la aplicación"
        )
    }

    func testRetrySummaryTakesPriorityOverEmptySelection() {
        let summary = ReviewApprovalSummary(
            keywordCount: 0,
            captionCount: 0,
            hasReviewedManifest: true,
            isRetryingFailedApply: true
        )

        XCTAssertEqual(
            summary.nextSafeAction,
            "Confirma el reintento de las filas fallidas; las filas verificadas no se modificarán"
        )
    }

    func testRetrySummaryNamesRetryScopeWithoutPretendingNoChangesSelected() {
        let summary = ReviewApprovalSummary(
            keywordCount: 0,
            captionCount: 0,
            hasReviewedManifest: true,
            isRetryingFailedApply: true
        )

        XCTAssertEqual(
            summary.actionScopeText,
            "Solo filas fallidas · cambios verificados sin modificar"
        )
    }

    func testFailedApplyWithoutAProposedChangeIsNotRetryable() {
        let photo = makePhoto(
            applyState: "failed",
            proposedKeywords: [],
            errors: [ManifestPreviewError(stage: "apply", code: "APPLY_FAILED")]
        )

        XCTAssertFalse(photo.isRetryableApplyRow)
    }

    func testReviewSummaryNamesTheExactMutationInTheConfirmationAction() {
        XCTAssertEqual(
            ReviewApprovalSummary(keywordCount: 2, captionCount: 1).applyActionText,
            "Aplicar 2 keywords y 1 caption"
        )
        XCTAssertEqual(
            ReviewApprovalSummary(keywordCount: 1, captionCount: 0).applyActionText,
            "Aplicar 1 keyword"
        )
        XCTAssertEqual(
            ReviewApprovalSummary(keywordCount: 0, captionCount: 2).applyActionText,
            "Aplicar 2 captions"
        )
        XCTAssertEqual(
            ReviewApprovalSummary(keywordCount: 0, captionCount: 0).applyActionText,
            "Sin cambios seleccionados"
        )
    }

    func testReviewSummaryRepeatsTheExactScopeOnTheActionButton() {
        let summary = ReviewApprovalSummary(photoCount: 2, keywordCount: 4, captionCount: 1)

        XCTAssertEqual(
            summary.actionScopeText,
            "2 fotos afectadas · 4 keywords aprobadas · 1 caption aprobado"
        )
        XCTAssertEqual(
            ReviewApprovalSummary(keywordCount: 0, captionCount: 0).actionScopeText,
            "Sin cambios seleccionados"
        )
    }

    func testReviewSummaryUsesSingularScopeWithoutTemplatePlaceholders() {
        let summary = ReviewApprovalSummary(photoCount: 1, keywordCount: 1, captionCount: 1)

        XCTAssertEqual(
            summary.actionScopeText,
            "1 foto afectada · 1 keyword aprobada · 1 caption aprobado"
        )
        XCTAssertFalse(summary.actionScopeText.contains("photoText"))
        XCTAssertFalse(summary.actionScopeText.contains("keywordText"))
        XCTAssertFalse(summary.actionScopeText.contains("captionText"))
    }

    func testReviewConfirmationSummaryIncludesAffectedPhotosAndBothMutationTypes() {
        let summary = ReviewApprovalSummary(
            photoCount: 1,
            keywordCount: 0,
            captionCount: 1,
            hasReviewedManifest: true
        )

        XCTAssertEqual(
            summary.confirmationSummaryText,
            "Antes de escribir, se comprobarán 1 foto afectada, 0 keywords aprobadas y 1 caption aprobado."
        )
    }

    func testReviewApprovalSummaryDoesNotPromiseDirectWrite() {
        let summary = ReviewApprovalSummary(
            photoCount: 1,
            keywordCount: 2,
            captionCount: 0,
            hasReviewedManifest: true
        )

        XCTAssertFalse(summary.confirmationSummaryText.contains("Se escribirán"))
        XCTAssertTrue(summary.confirmationSummaryText.contains("Antes de escribir"))
        XCTAssertTrue(summary.confirmationSummaryText.contains("se comprobarán"))
    }

    func testFreshReviewSummaryDoesNotDescribeAWriteAsPending() {
        let summary = ReviewApprovalSummary(
            photoCount: 1,
            keywordCount: 2,
            captionCount: 0,
            hasReviewedManifest: false
        )

        XCTAssertTrue(summary.confirmationSummaryText.contains("Antes de preparar la revisión"))
        XCTAssertFalse(summary.confirmationSummaryText.contains("Antes de escribir"))
    }

    func testReviewSummaryUsesSafeEmptyPhotoCopy() {
        let summary = ReviewApprovalSummary(keywordCount: 0, captionCount: 0)

        XCTAssertEqual(summary.photoText, "Ninguna foto afectada")
    }

    func testScanOutcomeSummaryExplainsPhotosWithoutProposals() {
        let summary = ReviewScanOutcomeSummary(photos: [
            makePhoto(state: "ready", proposedKeywords: ["cocina"]),
            makePhoto(uuid: "no-change", state: "noop", proposedKeywords: []),
            makePhoto(uuid: "no-change-2", state: "noop", proposedKeywords: []),
        ])

        XCTAssertEqual(summary.text, "Analizadas: 3 fotos · 2 sin cambios propuestos")
        XCTAssertEqual(summary.accessibilityLabel, summary.text)
    }

    func testScanOutcomeSummaryDoesNotCountFailedRowsAsAnalyzed() {
        let summary = ReviewScanOutcomeSummary(photos: [
            makePhoto(state: "ready", proposedKeywords: ["cocina"]),
            makePhoto(
                uuid: "failed",
                state: "analysis_failed",
                proposedKeywords: [],
                errors: [ManifestPreviewError(stage: "analysis", code: "ANALYSIS_FAILED")]
            ),
        ])

        XCTAssertEqual(summary.text, "Analizadas: 1 foto · 1 foto requiere atención")
        XCTAssertEqual(summary.analyzedCount, 1)
        XCTAssertEqual(summary.failedCount, 1)
    }

    func testScanOutcomeSummaryRemainsVisibleWhenAllRowsNeedAttention() {
        let summary = ReviewScanOutcomeSummary(photos: [
            makePhoto(
                state: "analysis_failed",
                proposedKeywords: [],
                errors: [ManifestPreviewError(stage: "analysis", code: "ANALYSIS_FAILED")]
            ),
        ])

        XCTAssertTrue(summary.isVisible)
        XCTAssertEqual(summary.text, "Analizadas: ninguna · 1 foto requiere atención")
    }

    func testReviewRecoverySummaryPointsInterruptedRunsToHistory() {
        let summary = ReviewRecoverySummary(
            preview: makeManifest(scanStatus: "interrupted", photos: [makePhoto(state: "cancelled")])
        )

        XCTAssertTrue(summary.isVisible)
        XCTAssertEqual(summary.title, "Este dry-run se interrumpió")
        XCTAssertEqual(summary.actionTitle, "Abrir Historial")
        XCTAssertTrue(summary.accessibilityLabel.contains("Historial"))
        XCTAssertFalse(summary.accessibilityLabel.contains("manifest.json"))
    }

    func testReviewRecoverySummaryDistinguishesCooperativeCancellation() {
        let summary = ReviewRecoverySummary(
            preview: makeManifest(scanStatus: "cancelled", photos: [makePhoto(state: "cancelled")])
        )

        XCTAssertTrue(summary.isVisible)
        XCTAssertEqual(summary.title, "Este dry-run se canceló")
        XCTAssertTrue(summary.detail.contains("sin aplicar cambios en Fotos"))
        XCTAssertTrue(summary.detail.contains("dry-run nuevo"))
        XCTAssertEqual(summary.actionTitle, "Abrir Historial")
    }

    func testReviewRecoverySummaryUsesSanitizedRootErrorForFailedRun() {
        let preview = RunManifestPreview(
            runID: "failed-run",
            createdAt: "2026-08-25T16:00:00Z",
            scanStatus: "failed",
            photos: [],
            runErrors: [ManifestPreviewError(stage: "preflight", code: "SCAN_NOT_READY")]
        )

        let summary = ReviewRecoverySummary(preview: preview)

        XCTAssertTrue(summary.detail.contains("El análisis aún no está listo"))
        XCTAssertTrue(summary.detail.contains("dry-run nuevo"))
        XCTAssertFalse(summary.detail.contains("SCAN_NOT_READY"))
        XCTAssertFalse(summary.detail.contains("preflight"))
    }

    func testReviewRecoverySummaryExplainsManualReviewWithoutOfferingRetry() {
        let summary = ReviewRecoverySummary(
            preview: makeManifest(photos: [makePhoto(applyState: "uncertain")]),
            mutationRequiresManualReview: true
        )

        XCTAssertTrue(summary.isVisible)
        XCTAssertEqual(summary.title, "Aplicación pausada por revisión manual")
        XCTAssertTrue(summary.detail.contains("no se reintentará"))
        XCTAssertTrue(summary.accessibilityLabel.contains("Fotos"))
    }

    func testReviewRecoverySummaryStaysHiddenForPristineReadyRun() {
        let summary = ReviewRecoverySummary(
            preview: makeManifest(photos: [makePhoto()])
        )

        XCTAssertFalse(summary.isVisible)
        XCTAssertEqual(summary.accessibilityLabel, "")
    }

    func testHistorySummaryIncludesCaptionProposalsWhenPresent() {
        let photo = PreviewPhoto(
            uuid: "49F027C6-0000-4000-8000-000000000000",
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: ["canal"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "ready",
            proposedCaption: "Un canal visible."
        )
        let summary = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/caption/manifest.json"),
            preview: makeManifest(photos: [photo])
        )

        XCTAssertEqual(summary.summaryText, "1 foto · 1 keyword propuesta · 1 caption propuesto")
    }

    func testBlockedScopeDoesNotPromiseReviewWhenEveryPhotoIsBlocked() {
        let blocked = makePhoto(
            state: "analysis_failed",
            proposedKeywords: ["canal"]
        )

        XCTAssertEqual(
            ReviewBlockedSummary(photos: [blocked]).text,
            "1 foto queda bloqueada y no se aplicará; no quedan propuestas válidas para revisión."
        )

        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/blocked/manifest.json"),
            preview: makeManifest(
                scanStatus: "ready_with_errors",
                photos: [blocked]
            )
        )

        XCTAssertEqual(
            run.blockedScopeText,
            "1 foto bloqueada; no se incluirá en una aplicación. No quedan filas revisables para una nueva aplicación en este run."
        )
    }

    func testHistorySummaryDoesNotCountWhitespaceOnlyCaptionAsReviewable() {
        let photo = PreviewPhoto(
            uuid: "49F027C6-0000-4000-8000-000000000000",
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: ["canal"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "ready",
            proposedCaption: "  \n\t  "
        )
        let summary = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/caption-empty/manifest.json"),
            preview: makeManifest(photos: [photo])
        )

        XCTAssertEqual(summary.summaryText, "1 foto · 1 keyword propuesta · 0 captions propuestos")
    }

    func testHistoryCaptionOutcomeSummaryMakesPersistedStatesTraceableWithoutCaptionText() {
        let photos = [
            PreviewPhoto(
                uuid: "caption-pending",
                title: "Canal",
                date: "2025-04-13T20:26:02",
                existingKeywords: [],
                proposedKeywords: [],
                confidence: 0.91,
                modelUsed: "qwen3-vl:8b",
                state: "noop",
                proposedCaption: "Una escena pendiente.",
                captionState: "proposed"
            ),
            PreviewPhoto(
                uuid: "caption-preserved",
                title: "Plaza",
                date: "2025-04-13T20:26:02",
                existingKeywords: [],
                proposedKeywords: [],
                confidence: 0.91,
                modelUsed: "qwen3-vl:8b",
                state: "noop",
                applyState: "noop",
                proposedCaption: "Una plaza visible.",
                captionState: "preserved"
            ),
            PreviewPhoto(
                uuid: "caption-verified",
                title: "Puente",
                date: "2025-04-13T20:26:02",
                existingKeywords: [],
                proposedKeywords: [],
                confidence: 0.91,
                modelUsed: "qwen3-vl:8b",
                state: "noop",
                applyState: "verified",
                appliedKeywords: ["puente"],
                proposedCaption: "Un puente visible.",
                appliedCaption: "Un puente visible.",
                captionState: "verified"
            ),
            PreviewPhoto(
                uuid: "caption-uncertain",
                title: "Noche",
                date: "2025-04-13T20:26:02",
                existingKeywords: [],
                proposedKeywords: [],
                confidence: 0.91,
                modelUsed: "qwen3-vl:8b",
                state: "noop",
                applyState: "verified",
                appliedKeywords: ["noche"],
                proposedCaption: "Una noche visible.",
                appliedCaption: "Una noche visible.",
                captionState: "uncertain"
            )
        ]
        let summary = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/captions/manifest.json"),
            preview: makeManifest(photos: photos)
        )

        XCTAssertEqual(
            summary.captionOutcomeText,
            "Estado de captions: 1 pendiente · 1 conservado · 1 aplicado y verificado · 1 requiere revisión manual."
        )
        XCTAssertFalse(summary.captionOutcomeText?.contains("Una escena pendiente") == true)
        XCTAssertFalse(summary.captionOutcomeText?.contains("caption-pending") == true)
    }

    func testHistoryCaptionOutcomeSummaryDoesNotCallFailedCaptionPending() {
        let failed = PreviewPhoto(
            uuid: "caption-failed",
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "noop",
            applyState: "failed",
            proposedCaption: "Una escena pendiente.",
            captionState: "proposed",
            errors: [ManifestPreviewError(stage: "apply", code: "APPLY_FAILED")]
        )
        let summary = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/caption-failed/manifest.json"),
            preview: makeManifest(photos: [failed])
        )

        XCTAssertEqual(summary.captionOutcomeText, "Estado de captions: 1 fallido.")
    }

    func testReviewedInvalidCaptionStateDoesNotOfferApply() {
        let invalid = PreviewPhoto(
            uuid: "caption-invalid-noop",
            date: "2026-08-26T00:00:00Z",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: 0.91,
            modelUsed: "qwen3-vl:4b",
            state: "noop",
            applyState: "noop",
            proposedCaption: "Una escena visible.",
            captionState: "proposed"
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/invalid-caption/manifest.json"),
            preview: makeManifest(
                reviewedFromRunID: "source-run",
                schemaVersion: 3,
                photos: [invalid]
            )
        )

        XCTAssertFalse(run.canContinueReviewedApply)
        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertTrue(run.nextSafeActionText.contains("dry-run nuevo"))
    }

    func testHistoryCaptionOutcomeMarksInvalidTransitionForManualReview() {
        let invalid = PreviewPhoto(
            uuid: "caption-invalid-outcome",
            date: "2026-08-26T00:00:00Z",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: 0.91,
            modelUsed: "qwen3-vl:4b",
            state: "noop",
            applyState: "noop",
            proposedCaption: "Una escena visible.",
            captionState: "proposed"
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/invalid-caption-outcome/manifest.json"),
            preview: makeManifest(
                reviewedFromRunID: "source-run",
                schemaVersion: 3,
                photos: [invalid]
            )
        )

        XCTAssertTrue(invalid.hasInvalidCaptionState)
        XCTAssertEqual(run.captionOutcomeText, "Estado de captions: 1 requiere revisión manual.")
    }

    func testReviewedCaptionProposalWithoutProposedStateDoesNotOfferApply() {
        let invalid = PreviewPhoto(
            uuid: "caption-invalid-not-requested",
            date: "2026-08-26T00:00:00Z",
            existingKeywords: [],
            proposedKeywords: ["canal"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:4b",
            state: "ready",
            proposedCaption: "Una escena visible.",
            captionState: "not_requested"
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/invalid-caption-state/manifest.json"),
            preview: makeManifest(
                reviewedFromRunID: "source-run",
                schemaVersion: 3,
                photos: [invalid]
            )
        )

        XCTAssertTrue(invalid.needsReviewAttention)
        XCTAssertFalse(run.canContinueReviewedApply)
        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertTrue(run.nextSafeActionText.contains("dry-run nuevo"))
    }

    func testExplicitNotRequestedCaptionWithProposalIsNotSelectable() {
        let invalid = PreviewPhoto(
            uuid: "caption-invalid-explicit-not-requested",
            date: "2026-08-26T00:00:00Z",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: 0.91,
            modelUsed: "qwen3-vl:4b",
            state: "noop",
            proposedCaption: "Una escena visible.",
            captionState: "not_requested"
        )

        XCTAssertTrue(invalid.hasInvalidFreshReviewCaptionState)
        XCTAssertFalse(invalid.isReviewSelectable)
        XCTAssertTrue(invalid.needsReviewAttention)
        XCTAssertTrue(invalid.reviewSelectionBlockReason?.contains("caption") == true)
    }

    func testReviewedAppliedCaptionWithoutCompletedStateCannotOfferRollback() {
        let invalid = PreviewPhoto(
            uuid: "caption-invalid-applied-state",
            date: "2026-08-26T00:00:00Z",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: 0.91,
            modelUsed: "qwen3-vl:4b",
            state: "ready",
            applyState: "verified",
            appliedCaption: "Una escena visible.",
            captionState: "not_requested"
        )
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/invalid-applied-caption/manifest.json"),
            preview: makeManifest(
                reviewedFromRunID: "source-run",
                photos: [invalid]
            )
        )

        XCTAssertTrue(invalid.needsReviewAttention)
        XCTAssertFalse(run.canRollback)
        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertTrue(run.nextSafeActionText.contains("dry-run nuevo"))
    }

    func testFreshInvalidCaptionStateDoesNotOfferReview() {
        let invalid = PreviewPhoto(
            uuid: "caption-invalid-fresh",
            date: "2026-08-26T00:00:00Z",
            existingKeywords: [],
            proposedKeywords: ["canal"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:4b",
            state: "ready",
            proposedCaption: "Una escena visible.",
            captionState: "preserved"
        )
        let preview = makeManifest(photos: [invalid])
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/invalid-fresh-caption/manifest.json"),
            preview: preview
        )

        XCTAssertTrue(invalid.hasInvalidCaptionState)
        XCTAssertFalse(preview.canPrepareReview)
        XCTAssertEqual(run.status, .needsAttention)
        XCTAssertTrue(run.nextSafeActionText.contains("dry-run nuevo"))
    }

    func testFreshInvalidCaptionStateIsNotCountedAsCaptionReviewable() {
        let invalid = PreviewPhoto(
            uuid: "caption-invalid-filter",
            date: "2026-08-26T00:00:00Z",
            existingKeywords: [],
            proposedKeywords: ["canal"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:4b",
            state: "ready",
            proposedCaption: "Una escena visible.",
            captionState: "verified"
        )
        let preview = RunManifestPreview(
            runID: "caption-invalid-filter-run",
            createdAt: "2026-08-26T00:00:00Z",
            scanStatus: "ready",
            captionsRequested: true,
            photos: [invalid]
        )

        XCTAssertTrue(invalid.hasInvalidFreshReviewCaptionState)
        XCTAssertFalse(invalid.isReviewSelectable)
        XCTAssertTrue(invalid.needsReviewAttention)
        XCTAssertTrue(invalid.reviewSelectionBlockReason?.contains("caption") == true)
        XCTAssertFalse(ReviewPhotoFilter.captions.includes(invalid))
        let run = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/caption-invalid-filter/manifest.json"),
            preview: preview
        )
        XCTAssertEqual(run.captionOutcomeText, "Estado de captions: 1 requiere revisión manual.")
        XCTAssertEqual(
            preview.captionRequestText,
            "Captions activados: no hubo propuestas revisables."
        )
    }

    func testHistorySummaryExposesBlockedRowsWithoutExpandingMutationScope() {
        let failed = makePhoto(state: "analysis_failed")
        let valid = PreviewPhoto(
            uuid: "50F027C6-0000-4000-8000-000000000000",
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: ["canal"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "ready"
        )
        let summary = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/partial/manifest.json"),
            preview: makeManifest(
                scanStatus: "ready_with_errors",
                photos: [failed, valid]
            )
        )

        XCTAssertEqual(
            summary.blockedScopeText,
            "1 foto bloqueada; no se incluirá en una aplicación. Las filas válidas siguen disponibles para revisión."
        )
        XCTAssertTrue(summary.blockedScopeText?.contains("no se incluirá") == true)
    }

    func testLoadedReviewedManifestCopyShowsSafePostApplyAction() {
        let message = ReviewedManifestCopy.message(
            status: .applied,
            canContinueReviewedApply: false,
            canRollback: true,
            isConsultationOnly: false,
            hasWarnings: false
        )

        XCTAssertTrue(message.contains("revertir solo los cambios verificados"))
        XCTAssertFalse(message.contains("confirma la aplicación"))
    }

    func testLoadedReviewedManifestCopyShowsRollbackTerminalState() {
        let message = ReviewedManifestCopy.message(
            status: .rolledBack,
            canContinueReviewedApply: false,
            canRollback: false,
            isConsultationOnly: false,
            hasWarnings: false
        )

        XCTAssertEqual(message, "Rollback verificado; no hay cambios verificados pendientes.")
        XCTAssertFalse(message.contains("aplicar"))
    }

    func testLoadedReviewedManifestCopyNamesVerifiedSubsetWhenRunNeedsAttention() {
        let message = ReviewedManifestCopy.message(
            status: .needsAttention,
            canContinueReviewedApply: false,
            canRollback: true,
            isConsultationOnly: false,
            hasWarnings: false
        )

        XCTAssertTrue(message.contains("revertir solo los cambios verificados"))
        XCTAssertFalse(message.contains("confirma la aplicación"))
    }

    func testLoadedReviewedManifestCopyNamesPartialRollbackSeparately() {
        let message = ReviewedManifestCopy.message(
            status: .needsAttention,
            canContinueReviewedApply: false,
            canRollback: true,
            isConsultationOnly: false,
            hasWarnings: false,
            isPartialRollback: true
        )

        XCTAssertEqual(message, "Rollback parcial: solo los cambios verificados restantes pueden revertirse.")
    }

    func testLoadedReviewedManifestCopyShowsNoChangesAsAuditOnly() {
        let message = ReviewedManifestCopy.message(
            status: .noChanges,
            canContinueReviewedApply: false,
            canRollback: false,
            isConsultationOnly: false,
            hasWarnings: false
        )

        XCTAssertEqual(message, "No hubo cambios nuevos; conserva el manifiesto como auditoría.")
        XCTAssertFalse(message.contains("confirma"))
    }

    func testLoadedReviewedManifestCopyKeepsReviewConfirmationExplicit() {
        let message = ReviewedManifestCopy.message(
            status: .readyToReview,
            canContinueReviewedApply: true,
            canRollback: false,
            isConsultationOnly: false,
            hasWarnings: true
        )

        XCTAssertEqual(
            message,
            "Manifiesto revisado listo: se confirmarán solo las filas válidas; las demás quedan bloqueadas."
        )
    }

    func testLoadedReviewedManifestCopyKeepsQueryOnlySafe() {
        let message = ReviewedManifestCopy.message(
            status: .needsAttention,
            canContinueReviewedApply: false,
            canRollback: false,
            isConsultationOnly: true,
            hasWarnings: false
        )

        XCTAssertTrue(message.contains("solo consulta"))
        XCTAssertFalse(message.contains("aplicar"))
    }

    func testReviewedHistoryExposesApprovedKeywordAndCaptionScope() {
        let keywordPhoto = makePhoto(proposedKeywords: ["góndola"])
        let withCaption = PreviewPhoto(
            uuid: "50F027C6-0000-4000-8000-000000000000",
            photosLocalIdentifier: nil,
            title: "Basílica",
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: [],
            containsPeople: nil,
            containsText: nil,
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            modelReason: nil,
            state: "ready",
            applyState: "not_run",
            appliedKeywords: [],
            rollbackState: "not_run",
            rolledBackKeywords: [],
            proposedCaption: "Basílica junto al canal",
            appliedCaption: nil,
            captionState: "proposed",
            errors: []
        )
        let preview = makeManifest(
            reviewedFromRunID: "source-run",
            photos: [keywordPhoto, withCaption]
        )
        let summary = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/review/manifest.json"),
            preview: preview
        )

        XCTAssertEqual(summary.approvedScopeText, "Alcance aprobado: 2 fotos · 1 keyword · 1 caption.")
        XCTAssertFalse(summary.approvedScopeText?.contains("Basílica junto al canal") == true)
        XCTAssertFalse(summary.approvedScopeText?.contains("/") == true)
    }

    func testReviewedHistoryKeepsApprovedScopeAfterApply() {
        let applied = makePhoto(
            applyState: "verified",
            appliedKeywords: ["góndola"]
        )
        let preview = makeManifest(
            reviewedFromRunID: "source-run",
            photos: [applied]
        )
        let summary = HistoryRunSummary(
            manifestURL: URL(fileURLWithPath: "/runs/review/manifest.json"),
            preview: preview
        )

        XCTAssertEqual(
            summary.approvedScopeText,
            "Alcance aprobado: 1 foto · 1 keyword · 0 captions."
        )
    }

    func testHistoricalPreviewScopeShowsApprovedChangesWithoutReenablingApply() {
        let applied = makePhoto(
            applyState: "verified",
            appliedKeywords: ["góndola"]
        )
        let preview = makeManifest(
            reviewedFromRunID: "source-run",
            photos: [applied]
        )

        let scope = ReviewHistoricalScope(preview: preview)

        XCTAssertEqual(scope.text, "Alcance aprobado: 1 foto · 1 keyword · 0 captions.")
        XCTAssertTrue(scope.isHistorical)
        XCTAssertFalse(preview.canPrepareReview)
    }

    func testHistoricalPreviewShowsEffectiveMutationOutcomeSeparately() {
        let applied = makePhoto(
            applyState: "verified",
            appliedKeywords: ["góndola"]
        )
        let preview = makeManifest(
            reviewedFromRunID: "source-run",
            photos: [applied]
        )

        let outcome = ReviewMutationOutcome(preview: preview)

        XCTAssertEqual(outcome.text, "Verificado: 1 foto · 1 keyword · 0 captions.")
        XCTAssertFalse(preview.canPrepareReview)
    }

    private func makeManifest(
        runID: String = "run-1",
        createdAt: String = "2026-08-25T16:00:00Z",
        scanStatus: String = "ready",
        reviewedFromRunID: String? = nil,
        schemaVersion: Int? = nil,
        scanDigest: String? = nil,
        sourceScanDigest: String? = nil,
        photos: [PreviewPhoto]
    ) -> RunManifestPreview {
        RunManifestPreview(
            runID: runID,
            createdAt: createdAt,
            scanStatus: scanStatus,
            reviewedFromRunID: reviewedFromRunID,
            schemaVersion: schemaVersion,
            scanDigest: scanDigest,
            sourceScanDigest: sourceScanDigest,
            photos: photos
        )
    }

    private func makeManifestFile(
        data: Data? = nil,
        permissions: UInt16 = 0o600
    ) throws -> URL {
        let directory = try makeTemporaryDirectory()
        let manifest = directory.appendingPathComponent("manifest.json")
        let valid = Data(#"{"run_id":"run-1","created_at":"2026-08-25T16:00:00Z","scan_status":"ready","photos":[]}"#.utf8)
        try (data ?? valid).write(to: manifest, options: [.atomic])
        try FileManager.default.setAttributes([.posixPermissions: permissions], ofItemAtPath: manifest.path)
        return manifest
    }

    private func makeTemporaryDirectory() throws -> URL {
        let directory = URL(fileURLWithPath: "/private/tmp", isDirectory: true)
            .appendingPathComponent("history-test-runs", isDirectory: true)
            .appendingPathComponent("photos-local-history-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        return directory
    }

    private func makePhoto(
        uuid: String = "49F027C6-0000-4000-8000-000000000000",
        photosLocalIdentifier: String? = nil,
        title: String = "Canal Grande",
        date: String = "2025-04-13T20:26:02",
        existingKeywords: [String] = ["Venecia"],
        confidence: Double? = 0.91,
        modelUsed: String? = "qwen3-vl:8b",
        state: String = "ready",
        applyState: String = "not_run",
        rollbackState: String = "not_run",
        appliedKeywords: [String] = [],
        appliedCaption: String? = nil,
        mutationDigest: String? = nil,
        rolledBackKeywords: [String] = [],
        rollbackDigest: String? = nil,
        proposedKeywords: [String] = ["góndola"],
        proposedCaption: String? = nil,
        captionState: String? = nil,
        containsPeople: Bool? = nil,
        containsText: Bool? = nil,
        modelReason: String? = nil,
        errors: [ManifestPreviewError] = []
    ) -> PreviewPhoto {
        PreviewPhoto(
            uuid: uuid,
            photosLocalIdentifier: photosLocalIdentifier,
            title: title,
            date: date,
            existingKeywords: existingKeywords,
            proposedKeywords: proposedKeywords,
            containsPeople: containsPeople,
            containsText: containsText,
            confidence: confidence,
            modelUsed: modelUsed,
            modelReason: modelReason,
            state: state,
            applyState: applyState,
            appliedKeywords: appliedKeywords,
            mutationDigest: mutationDigest,
            rollbackState: rollbackState,
            rolledBackKeywords: rolledBackKeywords,
            rollbackDigest: rollbackDigest,
            proposedCaption: proposedCaption,
            appliedCaption: appliedCaption,
            captionState: captionState,
            errors: errors
        )
    }
}
