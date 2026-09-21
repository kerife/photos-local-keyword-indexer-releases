import Foundation
import XCTest
@testable import PhotosLocalKeywordIndexer

final class ReviewSafetyTests: XCTestCase {
    func testMutationCancellationExplainsCooperativeWritesAndManualReview() {
        XCTAssertEqual(
            ReviewMutationCancellationCopy.buttonTitle(isCancellationRequested: false),
            "Cancelar operación"
        )
        XCTAssertTrue(
            ReviewMutationCancellationCopy.accessibilityHint(isCancellationRequested: false)
                .contains("cancelación cooperativa")
        )
        XCTAssertTrue(
            ReviewMutationCancellationCopy.accessibilityHint(isCancellationRequested: false)
                .contains("escritura ya iniciada")
        )

        XCTAssertEqual(
            ReviewMutationCancellationCopy.buttonTitle(isCancellationRequested: true),
            "Cancelación solicitada…"
        )
        XCTAssertFalse(
            ReviewMutationCancellationCopy.canRequestCancellation(isCancellationRequested: true)
        )
        XCTAssertTrue(
            ReviewMutationCancellationCopy.accessibilityHint(isCancellationRequested: true)
                .contains("revisión manual")
        )
        XCTAssertTrue(
            ReviewMutationCancellationCopy.accessibilityHint(isCancellationRequested: true)
                .contains("Historial")
        )
    }

    func testReviewedRetrySelectionIncludesOnlyFailedRows() {
        let verified = PreviewPhoto(
            uuid: "verified-row",
            title: "Ya verificada",
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: ["canal"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "ready",
            applyState: "verified",
            appliedKeywords: ["canal"]
        )
        let failed = PreviewPhoto(
            uuid: "failed-row",
            title: "Reintento pendiente",
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: ["góndola"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "ready",
            applyState: "failed",
            errors: [ManifestPreviewError(stage: "apply", code: "APPLY_FAILED")]
        )
        let pending = PreviewPhoto(
            uuid: "pending-row",
            title: "Pendiente",
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: ["puente"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "ready",
            applyState: "not_run"
        )

        let selection = ReviewSelection(
            photos: [verified, failed, pending],
            reviewedManifest: true
        )

        XCTAssertEqual(selection.payload.map(\.uuid), [failed.uuid, pending.uuid])
        XCTAssertEqual(selection.selectedPhotoCount, 2)
        XCTAssertEqual(selection.selectedKeywordCount, 2)
        XCTAssertEqual(selection.approvedPhotoIDs, Set([failed.uuid, pending.uuid]))
    }

    func testReviewedTerminalSelectionKeepsHistoricalScopeWithoutMutationPayload() {
        let applied = PreviewPhoto(
            uuid: "verified-history-scope",
            title: "Aplicada",
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: ["canal"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "ready",
            applyState: "verified",
            appliedKeywords: ["canal"]
        )

        let selection = ReviewSelection(
            photos: [applied],
            reviewedManifest: true
        )

        XCTAssertEqual(selection.selectedKeywordCount, 1)
        XCTAssertEqual(selection.approvedPhotoCount, 1)
        XCTAssertEqual(selection.approvedPhotoIDs, [applied.uuid])
        XCTAssertTrue(selection.payload.isEmpty)
        XCTAssertTrue(selection.captionSelectionPayload.isEmpty)
    }

    func testReviewedRetrySelectionRejectsVerifiedRowsAcrossAllSelectionPaths() {
        let verified = PreviewPhoto(
            uuid: "verified-retry-guard",
            title: "Ya verificada",
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: ["canal"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "ready",
            applyState: "verified",
            appliedKeywords: ["canal"],
            proposedCaption: "Un canal visible.",
            appliedCaption: "Un canal visible.",
            captionState: "verified"
        )
        let failed = PreviewPhoto(
            uuid: "failed-retry-guard",
            title: "Reintento pendiente",
            date: "2025-04-13T20:26:03",
            existingKeywords: [],
            proposedKeywords: ["góndola"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "ready",
            applyState: "failed",
            errors: [ManifestPreviewError(stage: "apply", code: "APPLY_FAILED")]
        )

        var selection = ReviewSelection(
            photos: [verified, failed],
            reviewedManifest: true
        )

        selection.setPhoto(verified, selected: true)
        selection.setKeyword("canal", for: verified, selected: true)
        selection.setCaption(uuid: verified.uuid, selected: true)
        selection.setAllKeywords(selected: true)

        XCTAssertEqual(selection.payload.map(\.uuid), [failed.uuid])
        XCTAssertEqual(selection.captionSelectionPayload, [:])
    }

    func testReviewedRetrySelectionCannotBeChangedProgrammatically() {
        let retry = PreviewPhoto(
            uuid: "reviewed-retry-immutable",
            title: "Reintento revisado",
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: ["canal"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "ready",
            applyState: "failed",
            proposedCaption: "Un canal visible.",
            captionState: "proposed",
            errors: [ManifestPreviewError(stage: "apply", code: "APPLY_FAILED")]
        )

        var selection = ReviewSelection(photos: [retry], reviewedManifest: true)
        XCTAssertEqual(selection.selectedKeywordCount, 1)
        XCTAssertEqual(selection.selectedCaptionCount, 1)

        selection.setPhoto(retry, selected: false)
        selection.setKeyword("canal", for: retry, selected: false)
        selection.setCaption(uuid: retry.uuid, selected: false)
        selection.setAllKeywords(selected: false)

        XCTAssertEqual(selection.selectedKeywordCount, 1)
        XCTAssertEqual(selection.selectedCaptionCount, 1)
        XCTAssertEqual(selection.approvedPhotoIDs, [retry.uuid])
    }

    func testReviewFilterCountsMatchVisibleRowsForRetryStates() {
        let retry = PreviewPhoto(
            uuid: "retry-filter-row",
            title: "Reintento con caption",
            date: "2026-08-26T20:00:00",
            existingKeywords: [],
            proposedKeywords: ["canal"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "ready",
            applyState: "failed",
            proposedCaption: "Un canal visible.",
            errors: [ManifestPreviewError(stage: "apply", code: "APPLY_FAILED")]
        )

        let pending = PreviewPhoto(
            uuid: "pending-filter-row",
            title: "Pendiente",
            date: "2026-08-26T20:00:01",
            existingKeywords: [],
            proposedKeywords: ["góndola"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "ready"
        )

        let photos = [retry, pending]
        let counts = ReviewFilterCounts(photos: photos)

        for filter in ReviewPhotoFilter.allCases {
            XCTAssertEqual(
                counts.count(for: filter),
                photos.filter { filter.includes($0) }.count,
                "El contador de \(filter.title) debe coincidir con las filas visibles"
            )
        }
        XCTAssertEqual(counts.changes, 1)
        XCTAssertEqual(counts.captions, 0)
        XCTAssertEqual(counts.attention, 1)
    }

    func testWorkerUsesOnlyTheNamedPackagedHelperIdentity() {
        XCTAssertEqual(WorkerProcess.packagedHelperDisplayName, "PhotosIndexerWorker")
        XCTAssertTrue(
            WorkerProcessError.helperMissing.errorDescription?.contains("no se sustituirá por un intérprete Python externo") == true
        )
    }

    func testFreshReviewSelectionStartsWithoutApprovedKeywords() {
        let photos = [
            PreviewPhoto(
                uuid: "49F027C6",
                date: "2025-04-13T20:26:02",
                existingKeywords: ["Venecia"],
                proposedKeywords: ["góndola", "Basílica de Santa María de la Salud"],
                confidence: 0.91,
                modelUsed: "qwen3-vl:8b",
                state: "ready"
            ),
        ]

        let selection = ReviewSelection(photos: photos)

        XCTAssertEqual(selection.selectedPhotoCount, 0)
        XCTAssertEqual(selection.selectedKeywordCount, 0)
        XCTAssertTrue(selection.payload.isEmpty)
    }

    func testReviewedSelectionKeepsEveryPreviouslyApprovedKeywordSelected() {
        let photos = [
            PreviewPhoto(
                uuid: "49F027C6",
                date: "2025-04-13T20:26:02",
                existingKeywords: ["Venecia"],
                proposedKeywords: ["góndola", "Basílica de Santa María de la Salud"],
                confidence: 0.91,
                modelUsed: "qwen3-vl:8b",
                state: "ready"
            ),
        ]

        let selection = ReviewSelection(photos: photos, reviewedManifest: true)

        XCTAssertEqual(selection.selectedPhotoCount, 1)
        XCTAssertEqual(selection.selectedKeywordCount, 2)
        XCTAssertEqual(selection.payload, [
            ReviewPhotoSelection(
                uuid: "49F027C6",
                keywords: ["góndola", "Basílica de Santa María de la Salud"]
            ),
        ])
    }

    func testBulkReviewControlNamesKeywordsAndKeepsCaptionsSeparate() {
        XCTAssertEqual(
            ReviewBulkKeywordCopy.buttonTitle(allKeywordsSelected: false),
            "Seleccionar todas las keywords"
        )
        XCTAssertEqual(
            ReviewBulkKeywordCopy.buttonTitle(allKeywordsSelected: true),
            "Quitar todas las keywords"
        )
        XCTAssertTrue(ReviewBulkKeywordCopy.accessibilityHint.contains("captions se aprueban por separado"))
    }

    func testApplyGateRequiresReviewedCopyAndExplicitConfirmation() {
        var gate = ApplySafetyGate()
        gate.updateSelection(keywordCount: 2)

        XCTAssertFalse(gate.requestConfirmation())
        XCTAssertNil(gate.confirmedManifest())

        gate.setReviewedManifest(URL(fileURLWithPath: "/tmp/reviewed/manifest.json"))
        XCTAssertTrue(gate.requestConfirmation())
        XCTAssertTrue(gate.isConfirmationPresented)
        XCTAssertEqual(
            gate.confirmedManifest(),
            URL(fileURLWithPath: "/tmp/reviewed/manifest.json")
        )
        XCTAssertFalse(gate.isConfirmationPresented)
    }

    func testApplyGateRejectsEmptySelectionAndRunningWorker() {
        var gate = ApplySafetyGate()
        gate.setReviewedManifest(URL(fileURLWithPath: "/tmp/reviewed/manifest.json"))
        gate.updateSelection(keywordCount: 0)
        XCTAssertFalse(gate.requestConfirmation())

        gate.updateSelection(keywordCount: 1)
        gate.isWorkerRunning = true
        XCTAssertFalse(gate.requestConfirmation())
    }

    func testApplyGateAllowsReviewedRetryWithoutNewSelection() {
        var gate = ApplySafetyGate()
        gate.setReviewedManifest(
            URL(fileURLWithPath: "/tmp/reviewed/manifest.json"),
            allowsEmptySelectionForRetry: true
        )
        gate.updateSelection(keywordCount: 0, captionCount: 0)

        XCTAssertTrue(gate.canRequestApply)
        XCTAssertTrue(gate.requestConfirmation())
    }

    func testRetryApplyIsAnActionableSelectionWithoutNewChanges() {
        XCTAssertTrue(
            ReviewApplyAvailability.hasActionableSelection(
                selectedChangeCount: 0,
                isRetryingFailedApply: true
            )
        )
        XCTAssertFalse(
            ReviewApplyAvailability.hasActionableSelection(
                selectedChangeCount: 0,
                isRetryingFailedApply: false
            )
        )
        XCTAssertTrue(
            ReviewApplyAvailability.hasActionableSelection(
                selectedChangeCount: 1,
                isRetryingFailedApply: false
            )
        )
    }

    func testReviewSelectionAllowsCaptionOnlyApproval() {
        let photo = PreviewPhoto(
            uuid: "49F027C6-0000-4000-8000-000000000000",
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "noop",
            proposedCaption: "Un canal con una góndola."
        )
        var selection = ReviewSelection(photos: [photo])

        XCTAssertEqual(selection.selectedKeywordCount, 0)
        XCTAssertEqual(selection.selectedCaptionCount, 0)
        XCTAssertEqual(selection.captionSelectionPayload, [photo.uuid: false])
        selection.setCaption(uuid: photo.uuid, selected: false)
        XCTAssertEqual(selection.selectedChangeCount, 0)
        selection.setCaption(uuid: photo.uuid, selected: true)
        XCTAssertEqual(selection.selectedCaptionCount, 1)

        var gate = ApplySafetyGate()
        gate.updateSelection(keywordCount: 0, captionCount: 1)
        gate.setReviewedManifest(URL(fileURLWithPath: "/tmp/reviewed/manifest.json"))
        XCTAssertTrue(gate.requestConfirmation())
    }

    func testConveniencePhotoWithCaptionProposalDefaultsToPendingCaption() {
        let photo = PreviewPhoto(
            uuid: "caption-default-state",
            date: "2026-08-30T00:00:00Z",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: 0.91,
            modelUsed: "qwen3-vl:4b",
            state: "noop",
            proposedCaption: "Una escena visible."
        )

        XCTAssertEqual(photo.captionState, "proposed")
        XCTAssertTrue(photo.isReviewSelectable)
        XCTAssertTrue(photo.hasCaptionProposal)
    }

    func testBulkKeywordToolbarIsHiddenOutsideFreshReview() {
        XCTAssertTrue(
            ReviewBulkKeywordCopy.shouldShowToolbar(
                isFreshReview: true,
                canSelectAllKeywords: true
            )
        )
        XCTAssertFalse(
            ReviewBulkKeywordCopy.shouldShowToolbar(
                isFreshReview: false,
                canSelectAllKeywords: true
            )
        )
        XCTAssertFalse(
            ReviewBulkKeywordCopy.shouldShowToolbar(
                isFreshReview: true,
                canSelectAllKeywords: false
            )
        )
    }

    func testCaptionDisclosureRemainsVisibleWhenReviewedBulkToolbarIsHidden() {
        XCTAssertFalse(
            ReviewBulkKeywordCopy.shouldShowCaptionDisclosure(
                isFreshReview: true,
                hasCaptionProposals: true,
                canSelectAllKeywords: true
            )
        )
        XCTAssertTrue(
            ReviewBulkKeywordCopy.shouldShowCaptionDisclosure(
                isFreshReview: false,
                hasCaptionProposals: true,
                canSelectAllKeywords: true
            )
        )
        XCTAssertFalse(
            ReviewBulkKeywordCopy.shouldShowCaptionDisclosure(
                isFreshReview: false,
                hasCaptionProposals: false,
                canSelectAllKeywords: true
            )
        )
    }

    func testPhotoToggleDoesNotReflectCaptionOnlyApproval() {
        let photo = PreviewPhoto(
            uuid: "caption-only-toggle",
            date: "2025-06-01T10:00:00",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "noop",
            proposedCaption: "Una escena visible."
        )
        var selection = ReviewSelection(photos: [photo])

        selection.setCaption(uuid: photo.uuid, selected: true)

        XCTAssertFalse(selection.isPhotoSelected(photo))
    }

    func testKeywordToggleDoesNotRevokeIndependentCaptionApproval() {
        let photo = PreviewPhoto(
            uuid: "keyword-caption-independent",
            date: "2025-06-01T10:00:00",
            existingKeywords: [],
            proposedKeywords: ["canal"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "ready",
            proposedCaption: "Una escena visible."
        )
        var selection = ReviewSelection(photos: [photo])

        selection.setCaption(uuid: photo.uuid, selected: true)
        selection.setPhoto(photo, selected: false)

        XCTAssertFalse(selection.isPhotoSelected(photo))
        XCTAssertEqual(selection.selectedKeywordCount, 0)
        XCTAssertEqual(selection.selectedCaptionCount, 1)
        XCTAssertEqual(selection.approvedPhotoIDs, [photo.uuid])
    }

    func testPhotoToggleIsNotAnActionForCaptionOnlyRows() {
        let photo = PreviewPhoto(
            uuid: "caption-only-disabled",
            date: "2025-06-01T10:00:00",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "noop",
            proposedCaption: "Una escena visible."
        )

        XCTAssertFalse(photo.canSelectPhoto)
    }

    func testCaptionOnlySetPhotoDoesNotRevokeItsIndependentCaptionApproval() {
        let photo = PreviewPhoto(
            uuid: "caption-only-independent",
            date: "2025-06-01T10:00:00",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "noop",
            proposedCaption: "Una escena visible."
        )
        var selection = ReviewSelection(photos: [photo])

        selection.setCaption(uuid: photo.uuid, selected: true)
        selection.setPhoto(photo, selected: false)

        XCTAssertEqual(selection.selectedKeywordCount, 0)
        XCTAssertEqual(selection.selectedCaptionCount, 1)
        XCTAssertEqual(selection.approvedPhotoIDs, [photo.uuid])
    }

    func testKeywordSelectionPathsSkipCaptionOnlyAndFailedRows() {
        let captionOnly = PreviewPhoto(
            uuid: "caption-only-keyword-path",
            date: "2025-06-01T10:00:00",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "noop",
            proposedCaption: "Una escena visible."
        )
        let failed = PreviewPhoto(
            uuid: "failed-keyword-path",
            date: "2025-06-01T10:00:01",
            existingKeywords: [],
            proposedKeywords: ["canal"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "analysis_failed"
        )
        var selection = ReviewSelection(photos: [captionOnly, failed])

        XCTAssertFalse(selection.canSelectAllKeywords)
        selection.setAllKeywords(selected: true)
        selection.setPhoto(captionOnly, selected: true)
        selection.setKeyword("canal", for: failed, selected: true)

        XCTAssertEqual(selection.selectedKeywordCount, 0)
        XCTAssertEqual(selection.selectedCaptionCount, 0)
        selection.setCaption(uuid: captionOnly.uuid, selected: true)
        XCTAssertEqual(selection.selectedCaptionCount, 1)
    }

    func testReviewedSelectionRetainsCaptionApprovalFromTheReviewedManifest() {
        let photo = PreviewPhoto(
            uuid: "49F027C6-0000-4000-8000-000000000003",
            date: "2025-06-01T10:00:00",
            existingKeywords: [],
            proposedKeywords: ["canal"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "ready",
            proposedCaption: "Un canal visible.",
            captionState: "proposed"
        )

        let selection = ReviewSelection(photos: [photo], reviewedManifest: true)

        XCTAssertEqual(selection.selectedKeywordCount, 1)
        XCTAssertEqual(selection.selectedCaptionCount, 1)
        XCTAssertEqual(selection.approvedPhotoCount, 1)
    }

    func testCaptionApprovalRejectsFailedPhotoStates() {
        let photo = PreviewPhoto(
            uuid: "49F027C6-0000-4000-8000-000000000002",
            date: "2025-06-01T10:00:00",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "analysis_failed",
            proposedCaption: "Una escena visible."
        )
        var selection = ReviewSelection(photos: [photo])

        selection.setCaption(uuid: photo.uuid, selected: true)

        XCTAssertEqual(selection.selectedCaptionCount, 0)
        XCTAssertTrue(selection.captionSelectionPayload.isEmpty)
    }

    func testCaptionApprovalRejectsWhitespaceOnlyCaptions() {
        let photo = PreviewPhoto(
            uuid: "49F027C6-0000-4000-8000-000000000002",
            date: "2025-04-13T20:26:04",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "noop",
            proposedCaption: "  \n  "
        )
        var selection = ReviewSelection(photos: [photo])

        selection.setCaption(uuid: photo.uuid, selected: true)

        XCTAssertEqual(selection.selectedCaptionCount, 0)
        XCTAssertTrue(selection.captionSelectionPayload.isEmpty)
    }

    func testApprovedPhotoCountIncludesCaptionOnlyAndKeywordApprovals() {
        let keywordPhoto = PreviewPhoto(
            uuid: "49F027C6-0000-4000-8000-000000000000",
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: ["canal"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "ready",
            proposedCaption: "Un canal visible."
        )
        let captionPhoto = PreviewPhoto(
            uuid: "49F027C6-0000-4000-8000-000000000001",
            date: "2025-04-13T20:26:03",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "noop",
            proposedCaption: "Una calle visible."
        )
        var selection = ReviewSelection(photos: [keywordPhoto, captionPhoto])

        selection.setPhoto(keywordPhoto, selected: true)
        XCTAssertEqual(selection.approvedPhotoCount, 1)
        selection.setCaption(uuid: captionPhoto.uuid, selected: true)
        XCTAssertEqual(selection.approvedPhotoCount, 2)
        selection.setPhoto(keywordPhoto, selected: false)
        XCTAssertEqual(selection.approvedPhotoCount, 1)
    }

    func testReviewSelectionSnapshotChangesWhenCaptionApprovalChanges() {
        let photo = PreviewPhoto(
            uuid: "49F027C6-0000-4000-8000-000000000000",
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "noop",
            proposedCaption: "Un canal con una góndola."
        )
        var before = ReviewSelection(photos: [photo])
        before.setCaption(uuid: photo.uuid, selected: true)
        var after = before
        after.setCaption(uuid: photo.uuid, selected: false)

        XCTAssertNotEqual(before, after)
        XCTAssertEqual(before.selectedCaptionCount, 1)
        XCTAssertEqual(after.selectedCaptionCount, 0)
    }

    func testReviewToolbarCanSelectOrClearAllKeywordsWithoutApprovingCaptions() {
        let keywordPhoto = PreviewPhoto(
            uuid: "49F027C6-0000-4000-8000-000000000000",
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: ["canal", "góndola"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "ready"
        )
        let captionPhoto = PreviewPhoto(
            uuid: "49F027C6-0000-4000-8000-000000000001",
            date: "2025-04-13T20:26:03",
            existingKeywords: [],
            proposedKeywords: [],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "noop",
            proposedCaption: "Un canal visible."
        )
        var selection = ReviewSelection(photos: [keywordPhoto, captionPhoto])

        XCTAssertTrue(selection.canSelectAllKeywords)
        selection.setAllKeywords(selected: true)
        XCTAssertEqual(selection.selectedKeywordCount, 2)
        XCTAssertEqual(selection.selectedCaptionCount, 0)
        XCTAssertTrue(selection.allKeywordsSelected)

        selection.setAllKeywords(selected: false)
        XCTAssertEqual(selection.selectedKeywordCount, 0)
        XCTAssertFalse(selection.allKeywordsSelected)
    }

    func testClearingKeywordSelectionKeepsItsCaptionApproval() {
        let photo = PreviewPhoto(
            uuid: "49F027C6-0000-4000-8000-000000000000",
            date: "2025-04-13T16:00:00",
            existingKeywords: [],
            proposedKeywords: ["canal"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "ready",
            proposedCaption: "Un canal visible."
        )
        var selection = ReviewSelection(photos: [photo])

        selection.setCaption(uuid: photo.uuid, selected: true)
        XCTAssertEqual(selection.selectedCaptionCount, 1)

        selection.setPhoto(photo, selected: false)

        XCTAssertEqual(selection.selectedKeywordCount, 0)
        XCTAssertEqual(selection.selectedCaptionCount, 1)
        XCTAssertEqual(selection.selectedChangeCount, 1)
    }

    func testMutationsNeverUseTheForcedTerminationFallback() {
        XCTAssertTrue(WorkerProcessState.cancellationRequested(requestID: "apply-1").isRunning)
        XCTAssertTrue(WorkerProcessState.cancellationRequested(requestID: "apply-1").isCancellationRequested)
        XCTAssertFalse(WorkerProcessState.running(requestID: "apply-1").isCancellationRequested)
        XCTAssertTrue(WorkerProcess.usesTerminationFallback(for: .scan))
        XCTAssertTrue(WorkerProcess.usesTerminationFallback(for: .preflight))
        XCTAssertFalse(WorkerProcess.usesTerminationFallback(for: .apply))
        XCTAssertFalse(WorkerProcess.usesTerminationFallback(for: .rollback))
    }

    func testCancellationCopyMatchesMutationSemantics() {
        XCTAssertTrue(WorkerProcess.cancellationMessage(for: .apply).contains("foto actual"))
        XCTAssertTrue(WorkerProcess.cancellationMessage(for: .rollback).contains("no se iniciarán fotos nuevas"))
        XCTAssertTrue(WorkerProcess.cancellationMessage(for: .scan).contains("no se escribirán cambios"))
        XCTAssertTrue(WorkerProcess.cancellationMessage(for: nil).contains("no se escribirán cambios"))
    }

    func testWorkerTerminationStatusIsHumanAndActionable() {
        XCTAssertEqual(
            WorkerProcessState.interrupted.terminalStatusText,
            "La operación se interrumpió. Abre Historial para revisar el run antes de continuar."
        )
        XCTAssertEqual(
            WorkerProcessState.failed(code: "HELPER_UNAVAILABLE").terminalStatusText,
            "No se encontró el helper local firmado. Reinstala la app o usa una build válida y vuelve a comprobar."
        )
        XCTAssertEqual(
            WorkerProcessState.interrupted.terminalStatusAccessibilityLabel,
            "Estado de la operación: La operación se interrumpió. Abre Historial para revisar el run antes de continuar."
        )
        XCTAssertNil(WorkerProcessState.ready.terminalStatusText)
        XCTAssertFalse(
            WorkerProcessState.failed(code: "HELPER_UNAVAILABLE").terminalStatusText?.contains("HELPER_UNAVAILABLE") == true
        )
    }

    func testWorkerTerminationStatusNamesRecoveryForHelperAndPhotoScriptFailures() {
        XCTAssertEqual(
            WorkerProcessState.failed(code: "HELPER_UNAVAILABLE").terminalStatusText,
            "No se encontró el helper local firmado. Reinstala la app o usa una build válida y vuelve a comprobar."
        )
        XCTAssertEqual(
            WorkerProcessState.failed(code: "PHOTOSCRIPT_UNAVAILABLE").terminalStatusText,
            "PhotoScript no pudo cargar su puente AppleScript. Comprueba la compatibilidad de Photos, PhotoScript y macOS, y vuelve a comprobar."
        )
        XCTAssertFalse(
            WorkerProcessState.failed(code: "PHOTOSCRIPT_UNAVAILABLE").terminalStatusAccessibilityLabel?.contains("/") == true
        )
    }

    func testWorkerTerminationStatusNamesHistoryAsTheRecoverySurface() {
        XCTAssertTrue(
            WorkerProcessState.interrupted.terminalStatusText?.contains("Historial") == true
        )
        XCTAssertTrue(
            WorkerProcessState.failed(code: "HELPER_UNAVAILABLE").terminalStatusText?.contains("Reinstala") == true
        )
    }

    func testWorkerTerminationStatesRefreshHistoryWithoutASecondTerminalEvent() {
        XCTAssertTrue(WorkerProcessState.interrupted.shouldRefreshHistory)
        XCTAssertTrue(WorkerProcessState.failed(code: "HELPER_UNAVAILABLE").shouldRefreshHistory)
        XCTAssertFalse(WorkerProcessState.ready.shouldRefreshHistory)
        XCTAssertFalse(WorkerProcessState.cancellationRequested(requestID: "scan-1").shouldRefreshHistory)
    }

    func testScanHistoryRecoveryActionOnlyAppearsAfterAbruptWorkerTermination() {
        XCTAssertTrue(ScanHistoryRecoveryCopy.shouldShow(for: .interrupted))
        XCTAssertTrue(ScanHistoryRecoveryCopy.shouldShow(for: .failed(code: "HELPER_UNAVAILABLE")))
        XCTAssertFalse(ScanHistoryRecoveryCopy.shouldShow(for: .ready))
        XCTAssertEqual(ScanHistoryRecoveryCopy.buttonTitle, "Abrir Historial")
        XCTAssertTrue(ScanHistoryRecoveryCopy.accessibilityHint.contains("solo lectura"))
    }

    func testScanHistoryRecoveryActionAppearsForNonCleanTerminalOutcomes() {
        XCTAssertTrue(
            ScanHistoryRecoveryCopy.shouldShow(
                for: .ready,
                terminalOutcome: .completedWithIssues
            )
        )
        XCTAssertTrue(
            ScanHistoryRecoveryCopy.shouldShow(
                for: .ready,
                terminalOutcome: .cancelled
            )
        )
        XCTAssertTrue(
            ScanHistoryRecoveryCopy.shouldShow(
                for: .ready,
                terminalOutcome: .failed
            )
        )
        XCTAssertFalse(
            ScanHistoryRecoveryCopy.shouldShow(
                for: .ready,
                terminalOutcome: .completed
            )
        )
    }

    func testWorkerOnlyFinishesForMatchingTerminalEventID() {
        XCTAssertTrue(WorkerProcess.isTerminalEvent(eventID: "request-1", activeID: "request-1", event: .error))
        XCTAssertTrue(WorkerProcess.isTerminalEvent(eventID: "request-1", activeID: "request-1", event: .completed))
        XCTAssertFalse(WorkerProcess.isTerminalEvent(eventID: "stale", activeID: "request-1", event: .error))
        XCTAssertFalse(WorkerProcess.isTerminalEvent(eventID: "stale", activeID: "request-1", event: .completed))
        XCTAssertFalse(WorkerProcess.isTerminalEvent(eventID: "request-1", activeID: "request-1", event: .photoProgress))
    }

    func testQueueEventsRemainAcceptedAfterTheStartRequestCompletes() {
        XCTAssertTrue(
            WorkerProcess.shouldAcceptEvent(
                eventID: "queue-start-1",
                activeRequestID: nil,
                event: .queueItem,
                eventSessionID: "session-1",
                activeQueueSessionID: "session-1"
            )
        )
        XCTAssertFalse(
            WorkerProcess.shouldAcceptEvent(
                eventID: "queue-start-1",
                activeRequestID: nil,
                event: .queueItem,
                eventSessionID: "stale-session",
                activeQueueSessionID: "session-1"
            )
        )
        XCTAssertFalse(
            WorkerProcess.shouldAcceptEvent(
                eventID: "stale",
                activeRequestID: nil,
                event: .completed,
                eventSessionID: nil,
                activeQueueSessionID: "session-1"
            )
        )
    }

    func testWorkerAllowsMultiplePendingQueueCommandsForTheSameSession() {
        XCTAssertFalse(
            WorkerProcess.hasSubmissionConflict(
                activeRequestID: nil,
                activeQueueSessionID: "session-1",
                requestCommand: .queuePersist,
                requestQueueSessionID: "session-1"
            )
        )
        XCTAssertFalse(
            WorkerProcess.hasSubmissionConflict(
                activeRequestID: nil,
                activeQueueSessionID: "session-1",
                requestCommand: .queuePause,
                requestQueueSessionID: "session-1"
            )
        )
        XCTAssertTrue(
            WorkerProcess.hasSubmissionConflict(
                activeRequestID: nil,
                activeQueueSessionID: "session-1",
                requestCommand: .queuePersist,
                requestQueueSessionID: "session-2"
            )
        )
        XCTAssertTrue(
            WorkerProcess.hasSubmissionConflict(
                activeRequestID: "scan-1",
                activeQueueSessionID: nil,
                requestCommand: .queuePersist,
                requestQueueSessionID: "session-1"
            )
        )
    }

    func testWorkerAcceptsTerminalEventsForAnyPendingQueueCommand() {
        let pendingQueueRequests: Set<String> = ["persist-1", "persist-2"]

        XCTAssertTrue(
            WorkerProcess.shouldAcceptEvent(
                eventID: "persist-1",
                activeRequestID: nil,
                activeQueueRequestIDs: pendingQueueRequests,
                event: .completed,
                eventSessionID: nil,
                activeQueueSessionID: "session-1"
            )
        )
        XCTAssertTrue(
            WorkerProcess.isTerminalEvent(
                eventID: "persist-2",
                activeID: nil,
                activeQueueRequestIDs: pendingQueueRequests,
                event: .error
            )
        )
        XCTAssertFalse(
            WorkerProcess.shouldAcceptEvent(
                eventID: "stale-persist",
                activeRequestID: nil,
                activeQueueRequestIDs: pendingQueueRequests,
                event: .completed,
                eventSessionID: nil,
                activeQueueSessionID: "session-1"
            )
        )
    }

    func testWorkerIgnoresTerminationFromAReplacedProcessGeneration() {
        let current = UUID()

        XCTAssertTrue(
            WorkerProcess.shouldHandleTermination(
                terminatedGeneration: current,
                activeGeneration: current
            )
        )
        XCTAssertFalse(
            WorkerProcess.shouldHandleTermination(
                terminatedGeneration: UUID(),
                activeGeneration: current
            )
        )
        XCTAssertFalse(
            WorkerProcess.shouldHandleTermination(
                terminatedGeneration: current,
                activeGeneration: nil
            )
        )
    }

    func testInvalidHelperProtocolRequiresProcessTerminationAndFailure() {
        XCTAssertEqual(
            WorkerProcess.protocolFailureDisposition(),
            .terminateAndFail(code: "INVALID_HELPER_EVENT")
        )
    }

    func testWorkerProcessAcceptsOnlyTheExactNestedHelperAppPath() throws {
        let fixture = try makeValidWorkerHelperFixture()
        defer { try? FileManager.default.removeItem(at: fixture.root) }

        XCTAssertEqual(
            WorkerProcess.validateEmbeddedHelper(
                executableURL: fixture.executable,
                bundleURL: fixture.root
            ),
            .valid
        )

        let flatHelper = fixture.root
            .appendingPathComponent(
                "Contents/Helpers/PhotosIndexerWorker/PhotosIndexerWorker",
                isDirectory: false
            )
        try FileManager.default.createDirectory(
            at: flatHelper.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try arm64MachOData().write(to: flatHelper)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o755],
            ofItemAtPath: flatHelper.path
        )

        XCTAssertNotEqual(
            WorkerProcess.validateEmbeddedHelper(
                executableURL: flatHelper,
                bundleURL: fixture.root
            ),
            .valid
        )
        XCTAssertEqual(
            WorkerProcess.validateEmbeddedHelper(
                executableURL: fixture.root.appendingPathComponent("outside-helper"),
                bundleURL: fixture.root
            ),
            .outsideBundle
        )
    }

    func testWorkerProcessRejectsMissingOrWrongNestedHelperPlistIdentity() throws {
        let fixture = try makeValidWorkerHelperFixture()
        defer { try? FileManager.default.removeItem(at: fixture.root) }

        try FileManager.default.removeItem(at: fixture.infoPlist)
        XCTAssertNotEqual(
            WorkerProcess.validateEmbeddedHelper(
                executableURL: fixture.executable,
                bundleURL: fixture.root
            ),
            .valid
        )

        try writeWorkerHelperPlist(
            to: fixture.infoPlist,
            bundleIdentifier: "com.photoslocalkeywordindexer.worker",
            packageType: "BNDL"
        )
        XCTAssertNotEqual(
            WorkerProcess.validateEmbeddedHelper(
                executableURL: fixture.executable,
                bundleURL: fixture.root
            ),
            .valid
        )

        try writeWorkerHelperPlist(
            to: fixture.infoPlist,
            bundleIdentifier: "com.photoslocalkeywordindexer.worker",
            executableName: "OtherWorker"
        )
        XCTAssertNotEqual(
            WorkerProcess.validateEmbeddedHelper(
                executableURL: fixture.executable,
                bundleURL: fixture.root
            ),
            .valid
        )

        try writeWorkerHelperPlist(
            to: fixture.infoPlist,
            bundleIdentifier: "com.example.untrusted-worker"
        )
        XCTAssertNotEqual(
            WorkerProcess.validateEmbeddedHelper(
                executableURL: fixture.executable,
                bundleURL: fixture.root
            ),
            .valid
        )
    }

    func testWorkerProcessRejectsSymlinkAndHardlinkedNestedHelperExecutables() throws {
        let fixture = try makeValidWorkerHelperFixture()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let otherExecutable = fixture.root.appendingPathComponent("other-worker")
        try arm64MachOData().write(to: otherExecutable)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o755],
            ofItemAtPath: otherExecutable.path
        )

        try FileManager.default.removeItem(at: fixture.executable)
        try FileManager.default.createSymbolicLink(
            at: fixture.executable,
            withDestinationURL: otherExecutable
        )
        XCTAssertNotEqual(
            WorkerProcess.validateEmbeddedHelper(
                executableURL: fixture.executable,
                bundleURL: fixture.root
            ),
            .valid
        )

        try FileManager.default.removeItem(at: fixture.executable)
        try FileManager.default.linkItem(at: otherExecutable, to: fixture.executable)
        XCTAssertNotEqual(
            WorkerProcess.validateEmbeddedHelper(
                executableURL: fixture.executable,
                bundleURL: fixture.root
            ),
            .valid
        )
    }

    func testWorkerProcessRejectsNonArm64OrUnsafelyWritableNestedHelper() throws {
        let fixture = try makeValidWorkerHelperFixture()
        defer { try? FileManager.default.removeItem(at: fixture.root) }

        try x86_64MachOData().write(to: fixture.executable)
        XCTAssertNotEqual(
            WorkerProcess.validateEmbeddedHelper(
                executableURL: fixture.executable,
                bundleURL: fixture.root
            ),
            .valid
        )

        try arm64MachOData().write(to: fixture.executable)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o775],
            ofItemAtPath: fixture.executable.path
        )
        XCTAssertNotEqual(
            WorkerProcess.validateEmbeddedHelper(
                executableURL: fixture.executable,
                bundleURL: fixture.root
            ),
            .valid
        )

        try FileManager.default.setAttributes(
            [.posixPermissions: 0o644],
            ofItemAtPath: fixture.executable.path
        )
        XCTAssertNotEqual(
            WorkerProcess.validateEmbeddedHelper(
                executableURL: fixture.executable,
                bundleURL: fixture.root
            ),
            .valid
        )
    }

    func testWorkerProcessRejectsUnsafeFilesDirectoriesAndLinksAcrossTheNestedPayload() throws {
        let writableFileFixture = try makeValidWorkerHelperFixture()
        defer { try? FileManager.default.removeItem(at: writableFileFixture.root) }
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o664],
            ofItemAtPath: writableFileFixture.payloadFile.path
        )
        XCTAssertNotEqual(
            WorkerProcess.validateEmbeddedHelper(
                executableURL: writableFileFixture.executable,
                bundleURL: writableFileFixture.root
            ),
            .valid
        )

        let writableDirectoryFixture = try makeValidWorkerHelperFixture()
        defer { try? FileManager.default.removeItem(at: writableDirectoryFixture.root) }
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o775],
            ofItemAtPath: writableDirectoryFixture.payloadFile.deletingLastPathComponent().path
        )
        XCTAssertNotEqual(
            WorkerProcess.validateEmbeddedHelper(
                executableURL: writableDirectoryFixture.executable,
                bundleURL: writableDirectoryFixture.root
            ),
            .valid
        )

        let hardlinkFixture = try makeValidWorkerHelperFixture()
        defer { try? FileManager.default.removeItem(at: hardlinkFixture.root) }
        let hardlinkSource = hardlinkFixture.root.appendingPathComponent("other-payload")
        try Data("payload".utf8).write(to: hardlinkSource)
        try FileManager.default.removeItem(at: hardlinkFixture.payloadFile)
        try FileManager.default.linkItem(at: hardlinkSource, to: hardlinkFixture.payloadFile)
        XCTAssertNotEqual(
            WorkerProcess.validateEmbeddedHelper(
                executableURL: hardlinkFixture.executable,
                bundleURL: hardlinkFixture.root
            ),
            .valid
        )

        let symlinkFixture = try makeValidWorkerHelperFixture()
        defer { try? FileManager.default.removeItem(at: symlinkFixture.root) }
        let externalTarget = symlinkFixture.root.appendingPathComponent("external-payload")
        try Data("payload".utf8).write(to: externalTarget)
        try FileManager.default.removeItem(at: symlinkFixture.payloadFile)
        try FileManager.default.createSymbolicLink(
            at: symlinkFixture.payloadFile,
            withDestinationURL: externalTarget
        )
        XCTAssertNotEqual(
            WorkerProcess.validateEmbeddedHelper(
                executableURL: symlinkFixture.executable,
                bundleURL: symlinkFixture.root
            ),
            .valid
        )
    }

    func testWorkerProcessRejectsUnsafeOrSymlinkedOuterBundleChain() throws {
        for relativeDirectory in ["", "Contents", "Contents/Helpers"] {
            let fixture = try makeValidWorkerHelperFixture()
            defer { try? FileManager.default.removeItem(at: fixture.root) }
            let directory = relativeDirectory.isEmpty
                ? fixture.root
                : fixture.root.appendingPathComponent(relativeDirectory, isDirectory: true)
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o775],
                ofItemAtPath: directory.path
            )
            XCTAssertNotEqual(
                WorkerProcess.validateEmbeddedHelper(
                    executableURL: fixture.executable,
                    bundleURL: fixture.root
                ),
                .valid,
                relativeDirectory
            )
        }

        let symlinkFixture = try makeValidWorkerHelperFixture()
        defer { try? FileManager.default.removeItem(at: symlinkFixture.root) }
        let helpers = symlinkFixture.root.appendingPathComponent("Contents/Helpers", isDirectory: true)
        let relocatedHelpers = symlinkFixture.root.appendingPathComponent("relocated-helpers", isDirectory: true)
        try FileManager.default.moveItem(at: helpers, to: relocatedHelpers)
        try FileManager.default.createSymbolicLink(at: helpers, withDestinationURL: relocatedHelpers)
        XCTAssertNotEqual(
            WorkerProcess.validateEmbeddedHelper(
                executableURL: symlinkFixture.executable,
                bundleURL: symlinkFixture.root
            ),
            .valid
        )
    }

    func testWorkerProcessRejectsMissingNestedHelperPrivacyDeclarations() throws {
        let fixture = try makeValidWorkerHelperFixture()
        defer { try? FileManager.default.removeItem(at: fixture.root) }
        let invalidValues: [(String, Any)] = [
            ("LSUIElement", false),
            ("NSPhotoLibraryUsageDescription", ""),
            ("NSPhotoLibraryAddUsageDescription", "   "),
            ("NSAppleEventsUsageDescription", ""),
        ]

        for (key, value) in invalidValues {
            try writeWorkerHelperPlist(
                to: fixture.infoPlist,
                bundleIdentifier: "com.photoslocalkeywordindexer.worker",
                overrides: [key: value]
            )
            XCTAssertNotEqual(
                WorkerProcess.validateEmbeddedHelper(
                    executableURL: fixture.executable,
                    bundleURL: fixture.root
                ),
                .valid,
                key
            )
        }
    }

    func testWorkerProcessRequiresExecutableMachOFileTypeAndBoundedFatSlices() throws {
        let thinFixture = try makeValidWorkerHelperFixture()
        defer { try? FileManager.default.removeItem(at: thinFixture.root) }
        try arm64MachOData(fileType: 0x6).write(to: thinFixture.executable)
        XCTAssertNotEqual(
            WorkerProcess.validateEmbeddedHelper(
                executableURL: thinFixture.executable,
                bundleURL: thinFixture.root
            ),
            .valid
        )

        let fatOffsetFixture = try makeValidWorkerHelperFixture()
        defer { try? FileManager.default.removeItem(at: fatOffsetFixture.root) }
        try fatArm64MachOData(sliceOffset: 4096, declaredSize: 32).write(
            to: fatOffsetFixture.executable
        )
        XCTAssertNotEqual(
            WorkerProcess.validateEmbeddedHelper(
                executableURL: fatOffsetFixture.executable,
                bundleURL: fatOffsetFixture.root
            ),
            .valid
        )

        let fatSizeFixture = try makeValidWorkerHelperFixture()
        defer { try? FileManager.default.removeItem(at: fatSizeFixture.root) }
        try fatArm64MachOData(sliceOffset: 28, declaredSize: 4096).write(
            to: fatSizeFixture.executable
        )
        XCTAssertNotEqual(
            WorkerProcess.validateEmbeddedHelper(
                executableURL: fatSizeFixture.executable,
                bundleURL: fatSizeFixture.root
            ),
            .valid
        )

        let validFatFixture = try makeValidWorkerHelperFixture()
        defer { try? FileManager.default.removeItem(at: validFatFixture.root) }
        try fatArm64MachOData(sliceOffset: 28, declaredSize: 32).write(
            to: validFatFixture.executable
        )
        XCTAssertEqual(
            WorkerProcess.validateEmbeddedHelper(
                executableURL: validFatFixture.executable,
                bundleURL: validFatFixture.root
            ),
            .valid
        )
    }

    private func makeValidWorkerHelperFixture() throws -> (
        root: URL,
        executable: URL,
        infoPlist: URL,
        payloadFile: URL
    ) {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(
                "photos-indexer-helper-validation-\(UUID().uuidString)",
                isDirectory: true
            )
        let helperApp = root
            .appendingPathComponent(
                "Contents/Helpers/PhotosIndexerWorker.app",
                isDirectory: true
            )
        let contents = helperApp.appendingPathComponent("Contents", isDirectory: true)
        let executable = contents
            .appendingPathComponent("MacOS", isDirectory: true)
            .appendingPathComponent("PhotosIndexerWorker", isDirectory: false)
        let infoPlist = contents.appendingPathComponent("Info.plist", isDirectory: false)
        let resources = contents.appendingPathComponent("Resources", isDirectory: true)
        let internalPayload = resources.appendingPathComponent("_internal", isDirectory: true)
        let payloadFile = internalPayload.appendingPathComponent("runtime.dat", isDirectory: false)
        let frameworks = contents.appendingPathComponent("Frameworks", isDirectory: true)
        let frameworkFile = frameworks.appendingPathComponent("runtime.dylib", isDirectory: false)

        try FileManager.default.createDirectory(
            at: executable.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try FileManager.default.createDirectory(at: internalPayload, withIntermediateDirectories: true)
        try FileManager.default.createDirectory(at: frameworks, withIntermediateDirectories: true)
        try writeWorkerHelperPlist(
            to: infoPlist,
            bundleIdentifier: "com.photoslocalkeywordindexer.worker"
        )
        try arm64MachOData().write(to: executable)
        try Data("payload".utf8).write(to: payloadFile)
        try Data("framework".utf8).write(to: frameworkFile)
        try FileManager.default.createSymbolicLink(
            atPath: executable.deletingLastPathComponent().appendingPathComponent("_internal").path,
            withDestinationPath: "../Resources/_internal"
        )
        for directory in [
            root,
            root.appendingPathComponent("Contents", isDirectory: true),
            root.appendingPathComponent("Contents/Helpers", isDirectory: true),
            helperApp,
            contents,
            executable.deletingLastPathComponent(),
            resources,
            internalPayload,
            frameworks,
        ] {
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o755],
                ofItemAtPath: directory.path
            )
        }
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o644],
            ofItemAtPath: infoPlist.path
        )
        for file in [payloadFile, frameworkFile] {
            try FileManager.default.setAttributes(
                [.posixPermissions: 0o644],
                ofItemAtPath: file.path
            )
        }
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o755],
            ofItemAtPath: executable.path
        )
        return (root, executable, infoPlist, payloadFile)
    }

    private func writeWorkerHelperPlist(
        to url: URL,
        bundleIdentifier: String,
        packageType: String = "APPL",
        executableName: String = "PhotosIndexerWorker",
        overrides: [String: Any] = [:]
    ) throws {
        var plist: [String: Any] = [
            "CFBundleExecutable": executableName,
            "CFBundleIdentifier": bundleIdentifier,
            "CFBundlePackageType": packageType,
            "LSUIElement": true,
            "NSPhotoLibraryUsageDescription": "Analiza fotos elegidas localmente.",
            "NSPhotoLibraryAddUsageDescription": "Añade metadatos aprobados.",
            "NSAppleEventsUsageDescription": "Controla Fotos para metadatos aprobados.",
        ]
        for (key, value) in overrides {
            plist[key] = value
        }
        let data = try PropertyListSerialization.data(
            fromPropertyList: plist,
            format: .xml,
            options: 0
        )
        try data.write(to: url)
    }

    private func arm64MachOData(fileType: UInt32 = 0x2) -> Data {
        var data = Data([0xcf, 0xfa, 0xed, 0xfe, 0x0c, 0x00, 0x00, 0x01])
        data.append(Data(repeating: 0, count: 4))
        data.append(littleEndianBytes(fileType))
        data.append(Data(repeating: 0, count: 16))
        return data
    }

    private func x86_64MachOData() -> Data {
        var data = Data([0xcf, 0xfa, 0xed, 0xfe, 0x07, 0x00, 0x00, 0x01])
        data.append(Data(repeating: 0, count: 4))
        data.append(littleEndianBytes(0x2))
        data.append(Data(repeating: 0, count: 16))
        return data
    }

    private func fatArm64MachOData(sliceOffset: UInt32, declaredSize: UInt32) -> Data {
        var data = Data([0xca, 0xfe, 0xba, 0xbe])
        data.append(bigEndianBytes(1))
        data.append(bigEndianBytes(0x0100000c))
        data.append(bigEndianBytes(0))
        data.append(bigEndianBytes(sliceOffset))
        data.append(bigEndianBytes(declaredSize))
        data.append(bigEndianBytes(0))
        if sliceOffset == 28 {
            data.append(arm64MachOData())
        }
        return data
    }

    private func littleEndianBytes(_ value: UInt32) -> Data {
        Data([
            UInt8(value & 0xff),
            UInt8((value >> 8) & 0xff),
            UInt8((value >> 16) & 0xff),
            UInt8((value >> 24) & 0xff),
        ])
    }

    private func bigEndianBytes(_ value: UInt32) -> Data {
        Data([
            UInt8((value >> 24) & 0xff),
            UInt8((value >> 16) & 0xff),
            UInt8((value >> 8) & 0xff),
            UInt8(value & 0xff),
        ])
    }
}
