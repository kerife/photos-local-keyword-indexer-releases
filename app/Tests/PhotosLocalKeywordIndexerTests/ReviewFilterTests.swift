import XCTest
@testable import PhotosLocalKeywordIndexer

final class ReviewFilterTests: XCTestCase {
    func testFilterCountsSeparateChangesCaptionsAndAttention() {
        let photos = [
            makePhoto(uuid: "ready", state: "ready", keywords: ["canal"]),
            makePhoto(uuid: "caption", state: "noop", caption: "Un canal visible.", captionState: "proposed"),
            makePhoto(uuid: "failed", state: "analysis_failed", keywords: [])
        ]

        let counts = ReviewFilterCounts(photos: photos)

        XCTAssertEqual(counts.all, 3)
        XCTAssertEqual(counts.changes, 2)
        XCTAssertEqual(counts.captions, 1)
        XCTAssertEqual(counts.attention, 1)
        XCTAssertEqual(counts.count(for: .changes), 2)
    }

    func testAllFilterEmptyStateExplainsThatThereAreNoEligiblePhotos() {
        XCTAssertEqual(
            ReviewPhotoFilter.all.emptyDescription(for: 0),
            "No hay otra vista que mostrar; conserva este run como auditoría y ejecuta un dry-run nuevo si esperabas encontrar fotos."
        )
        XCTAssertEqual(
            ReviewPhotoFilter.changes.emptyDescription(for: 3),
            "Cambia el filtro para revisar otra parte del run."
        )
    }

    func testFiltersNeverChangeTheUnderlyingPhotoSelection() {
        let photos = [
            makePhoto(uuid: "ready", state: "ready", keywords: ["canal"]),
            makePhoto(uuid: "noop", state: "noop", keywords: [])
        ]

        XCTAssertEqual(photos.filter(ReviewPhotoFilter.changes.includes).map(\.uuid), ["ready"])
        XCTAssertEqual(photos.filter(ReviewPhotoFilter.all.includes).map(\.uuid), ["ready", "noop"])
    }

    func testUnknownWorkflowStateIsVisibleInAttentionFilter() {
        let photo = makePhoto(uuid: "future", state: "future_state", keywords: [])

        XCTAssertTrue(ReviewPhotoFilter.attention.includes(photo))
        XCTAssertEqual(
            ReviewPhotoFilter.attention.emptyMessage(for: 1),
            "No hay fotos con errores o estados que requieran atención."
        )
    }

    func testChangesFilterExcludesFailedPhotosEvenWhenTheyContainProposals() {
        let failed = makePhoto(uuid: "failed-proposal", state: "analysis_failed", keywords: ["canal"])

        XCTAssertFalse(ReviewPhotoFilter.changes.includes(failed))
        XCTAssertTrue(ReviewPhotoFilter.attention.includes(failed))
        XCTAssertEqual(ReviewFilterCounts(photos: [failed]).changes, 0)
    }

    func testCaptionsFilterExcludesFailedPhotosEvenWhenTheyContainProposals() {
        let failed = makePhoto(
            uuid: "failed-caption",
            state: "analysis_failed",
            caption: "Un canal visible."
        )

        XCTAssertFalse(ReviewPhotoFilter.captions.includes(failed))
        XCTAssertTrue(ReviewPhotoFilter.attention.includes(failed))
        XCTAssertEqual(ReviewFilterCounts(photos: [failed]).captions, 0)
        XCTAssertEqual(
            ReviewPhotoFilter.captions.emptyMessage(for: 1),
            "No hay captions revisables en este run."
        )
    }

    func testCaptionProposalAvailabilityExcludesFailedPhotos() {
        let failed = makePhoto(
            uuid: "failed-caption-availability",
            state: "analysis_failed",
            caption: "Un canal visible."
        )

        XCTAssertFalse(ReviewSelection(photos: [failed]).hasCaptionProposals)
    }

    func testCaptionFilterTreatsWhitespaceOnlyCaptionAsAbsent() {
        let photo = makePhoto(uuid: "blank", state: "noop", caption: "  \n  ")

        XCTAssertFalse(photo.hasCaptionProposal)
        XCTAssertFalse(ReviewPhotoFilter.captions.includes(photo))
        XCTAssertFalse(ReviewPhotoFilter.changes.includes(photo))
    }

    func testReviewGroupsUseStableDecisionPriorityAndPreservePhotoOrder() {
        let photos = [
            makePhoto(uuid: "attention", state: "analysis_failed", keywords: ["canal"]),
            makePhoto(uuid: "caption", state: "noop", caption: "Un canal visible.", captionState: "proposed"),
            makePhoto(uuid: "keywords", state: "ready", keywords: ["canal"]),
            makePhoto(uuid: "quiet", state: "noop", keywords: [])
        ]

        let groups = ReviewPhotoGroups(photos: photos)

        XCTAssertEqual(groups.sections.map(\.group), [.attention, .captions, .changes, .unchanged])
        XCTAssertEqual(groups.sections.map { $0.photos.map(\.uuid) }, [
            ["attention"], ["caption"], ["keywords"], ["quiet"]
        ])
    }

    func testReviewGroupsPutMixedCaptionAndKeywordPhotoInCaptionGroup() {
        let mixed = makePhoto(
            uuid: "mixed",
            state: "ready",
            keywords: ["canal"],
            caption: "Una escena visible.",
            captionState: "proposed"
        )

        let groups = ReviewPhotoGroups(photos: [mixed])

        XCTAssertEqual(groups.sections.map(\.group), [.captions])
        XCTAssertEqual(groups.sections.first?.photos.map(\.uuid), ["mixed"])
    }

    func testReviewGroupsDoNotCallVerifiedChangesPendingReview() {
        let applied = makePhoto(
            uuid: "applied",
            state: "ready",
            keywords: ["canal"],
            applyState: "verified",
            appliedCaption: "Una escena visible.",
            captionState: "verified"
        )

        let groups = ReviewPhotoGroups(photos: [applied])

        XCTAssertEqual(groups.sections.map(\.group), [.unchanged])
        XCTAssertEqual(groups.sections.first?.photos.map(\.uuid), ["applied"])
    }

    func testPendingFiltersExcludeAlreadyAppliedProposals() {
        let applied = makePhoto(
            uuid: "applied-filter",
            state: "ready",
            keywords: ["canal"],
            applyState: "verified",
            appliedCaption: "Una escena visible.",
            captionState: "verified"
        )

        XCTAssertFalse(ReviewPhotoFilter.changes.includes(applied))
        XCTAssertFalse(ReviewPhotoFilter.captions.includes(applied))
        let counts = ReviewFilterCounts(photos: [applied])
        XCTAssertEqual(counts.changes, 0)
        XCTAssertEqual(counts.captions, 0)
    }

    func testReviewGroupCopyDistinguishesNoPendingChangesFromHistoricalChanges() {
        XCTAssertEqual(ReviewPhotoGroup.unchanged.title, "Sin cambios pendientes")
    }

    func testReviewScopeSummarySeparatesVisibleFilterFromApprovedWriteScope() {
        let summary = ReviewScopeSummary(
            filter: .changes,
            visiblePhotoCount: 2,
            totalPhotoCount: 5,
            approvedPhotoCount: 3
        )

        XCTAssertEqual(summary.visibleScopeText, "Filtro Cambios: 2 de 5 fotos visibles")
        XCTAssertEqual(summary.approvedScopeText, "3 fotos afectadas por la selección aprobada")
        XCTAssertEqual(
            summary.accessibilityLabel,
            "Filtro Cambios: 2 de 5 fotos visibles. 3 fotos afectadas por la selección aprobada. El filtro no cambia la selección aprobada."
        )
    }

    func testReviewScopeSummaryUsesSingularCountsWithoutChangingTheirMeaning() {
        let summary = ReviewScopeSummary(
            filter: .attention,
            visiblePhotoCount: 1,
            totalPhotoCount: 1,
            approvedPhotoCount: 1
        )

        XCTAssertEqual(summary.visibleScopeText, "Filtro Atención: 1 de 1 foto visible")
        XCTAssertEqual(summary.approvedScopeText, "1 foto afectada por la selección aprobada")
    }

    func testReviewScopeSummaryShowsTheFullApprovedMutationScopeBesideTheFilter() {
        let summary = ReviewScopeSummary(
            filter: .captions,
            visiblePhotoCount: 2,
            totalPhotoCount: 5,
            approvedPhotoCount: 3,
            approvedKeywordCount: 4,
            approvedCaptionCount: 1
        )

        XCTAssertEqual(
            summary.approvedScopeText,
            "3 fotos afectadas por la selección aprobada · 4 keywords aprobadas · 1 caption aprobado"
        )
        XCTAssertTrue(summary.accessibilityLabel.contains("4 keywords aprobadas"))
        XCTAssertTrue(summary.accessibilityLabel.contains("1 caption aprobado"))
    }

    private func makePhoto(
        uuid: String,
        state: String,
        keywords: [String] = [],
        caption: String? = nil,
        applyState: String = "not_run",
        appliedCaption: String? = nil,
        captionState: String? = nil
    ) -> PreviewPhoto {
        PreviewPhoto(
            uuid: uuid,
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: keywords,
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: state,
            applyState: applyState,
            proposedCaption: caption,
            appliedCaption: appliedCaption,
            captionState: captionState
        )
    }
}
