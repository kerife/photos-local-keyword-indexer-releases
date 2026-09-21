import XCTest
@testable import PhotosLocalKeywordIndexer

final class QueueReviewModelsTests: XCTestCase {
    func testNewRevisionDetachesPreviousSourceAndPreservesManualDraft() throws {
        var store = ReviewSessionStore(items: [])
        let source = URL(fileURLWithPath: "/private/run/manifest.json")
        let photo = PreviewPhoto(uuid: "photo-a", date: "2026-09-09", existingKeywords: [],
                                 proposedKeywords: ["playa"], confidence: 0.9, modelUsed: "qwen3-vl:4b", state: "ready")
        store.applyQueueEvent(id: "photo-a", revision: 1, state: .ready, manifestURL: source, photo: photo)
        XCTAssertTrue(store.editDraft(id: "photo-a", keywords: ["mar"], caption: "Mi edición."))
        store.applyQueueEvent(id: "photo-a", revision: 2, state: .analyzing)
        for _ in 0..<2 {
            store.applyQueueEvent(id: "photo-a", revision: 1, state: .ready, manifestURL: source, photo: photo)
        }
        store.applyQueueEvent(id: "photo-a", revision: 2, state: .analyzing)
        let item = try XCTUnwrap(store.item(id: "photo-a"))
        XCTAssertNil(item.manifestURL)
        XCTAssertNil(item.photo)
        XCTAssertEqual(item.proposal, .empty)
        XCTAssertEqual(item.draft.keywords, ["mar"])
        XCTAssertEqual(item.draft.caption, "Mi edición.")
        XCTAssertTrue(item.hasManualEdits)
        XCTAssertEqual(item.state, .analyzing)
        XCTAssertEqual(item.progressPresentation, .indeterminate)
        XCTAssertEqual(item.statusCopy, "Analizando de nuevo; el resultado aún no está disponible.")
        XCTAssertEqual(store.focusedItemID, "photo-a")
    }

    func testFailedAnalysisAndValidEmptyResultHaveDistinctPresentationAndActions() throws {
        var store = ReviewSessionStore(items: [])
        let source = URL(fileURLWithPath: "/private/run/manifest.json")
        let failed = PreviewPhoto(uuid: "photo-a", date: "2026-09-09", existingKeywords: [],
                                  proposedKeywords: [], confidence: nil, modelUsed: nil, state: "analysis_failed",
                                  errors: [ManifestPreviewError(stage: "analysis", code: "ANALYSIS_FAILED")])
        store.applyQueueEvent(id: "photo-a", revision: 1, state: .failed, manifestURL: source, photo: failed)
        let item = try XCTUnwrap(store.item(id: "photo-a"))
        XCTAssertEqual(item.state, .failed)
        XCTAssertEqual(
            item.statusCopy,
            "El análisis local terminó antes de producir una propuesta válida; esta versión no registró una causa más específica. No se guardó ningún cambio en Fotos. Reanaliza esta foto o descártala; no se guardará ningún cambio automáticamente."
        )
        XCTAssertFalse(item.statusCopy.contains("ANALYSIS_FAILED"))
        let failure = try XCTUnwrap(item.failurePresentation)
        XCTAssertEqual(failure.code, "ANALYSIS_FAILED")
        XCTAssertEqual(failure.reason, "El análisis local terminó antes de producir una propuesta válida; esta versión no registró una causa más específica.")
        XCTAssertEqual(failure.technicalDetail, "Etapa técnica: análisis local. Esta ejecución anterior no registró una causa más específica. Apple Fotos no se modificó.")
        XCTAssertFalse(failure.technicalDetail.contains("photo-a"))
        XCTAssertFalse(ContinuousReviewActions(item: item).canSave)
        XCTAssertFalse(ContinuousReviewActions(item: item).canEdit)
        XCTAssertTrue(ContinuousReviewActions(item: item).canRescan)
        XCTAssertTrue(ContinuousReviewActions(item: item).canDiscard)
        XCTAssertEqual(ContinuousReviewSummary(items: store.items).attention, 1)
        XCTAssertEqual(ContinuousReviewSummary(items: store.items).ready, 0)
        let empty = PreviewPhoto(uuid: "photo-b", date: "2026-09-09", existingKeywords: [],
                                 proposedKeywords: [], confidence: 0.5, modelUsed: "qwen3-vl:4b", state: "noop")
        store.applyQueueEvent(id: "photo-b", revision: 1, state: .ready, manifestURL: source, photo: empty)
        let valid = try XCTUnwrap(store.item(id: "photo-b"))
        XCTAssertEqual(valid.statusCopy, "El modelo no generó propuestas. Puedes editar o reanalizar.")
        XCTAssertTrue(ContinuousReviewActions(item: valid).canEdit)
        XCTAssertEqual(ContinuousReviewSummary(items: store.items).ready, 1)
        store.applyQueueEvent(id: "photo-b", revision: 1, state: .failed)
        XCTAssertEqual(store.item(id: "photo-b")?.statusCopy, "No se pudo completar la operación.")
        XCTAssertFalse(ContinuousReviewActions(item: try XCTUnwrap(store.item(id: "photo-b"))).canRescan)
    }

    func testFailurePresentationShowsRecognizedCauseButNeverRawDiagnosticInput() throws {
        let source = URL(fileURLWithPath: "/private/run/manifest.json")
        var store = ReviewSessionStore(items: [])
        let failed = PreviewPhoto(
            uuid: "photo-a", date: "2026-09-09", existingKeywords: [], proposedKeywords: [],
            confidence: nil, modelUsed: nil, state: "analysis_failed",
            errors: [ManifestPreviewError(stage: "analysis", code: "untrusted diagnostic /private/photo.jpg")]
        )
        store.applyQueueEvent(id: "photo-a", revision: 1, state: .failed, manifestURL: source, photo: failed)

        let failure = try XCTUnwrap(store.item(id: "photo-a")?.failurePresentation)
        XCTAssertEqual(failure.code, "No disponible")
        XCTAssertFalse(failure.reason.contains("/private/photo.jpg"))
        XCTAssertFalse(failure.technicalDetail.contains("/private/photo.jpg"))
        XCTAssertFalse(store.item(id: "photo-a")?.statusCopy.contains("/private/photo.jpg") ?? true)
    }

    func testExplicitRescanResetClearsDraftAndPreviousSource() throws {
        var store = ReviewSessionStore(items: [])
        let photo = PreviewPhoto(uuid: "photo-a", date: "2026-09-09", existingKeywords: [],
                                 proposedKeywords: ["playa"], confidence: 0.9, modelUsed: "qwen3-vl:4b", state: "ready")
        store.applyQueueEvent(id: "photo-a", revision: 1, state: .ready,
                              manifestURL: URL(fileURLWithPath: "/private/run/manifest.json"), photo: photo)
        XCTAssertTrue(store.editDraft(id: "photo-a", keywords: ["mar"], caption: nil))
        XCTAssertTrue(store.queueRescan(id: "photo-a", resetManualEdits: true))
        store.applyQueueEvent(id: "photo-a", revision: 2, state: .preparing)
        let item = try XCTUnwrap(store.item(id: "photo-a"))
        XCTAssertNil(item.photo)
        XCTAssertNil(item.manifestURL)
        XCTAssertEqual(item.draft.keywords, [])
        XCTAssertFalse(item.hasManualEdits)
        XCTAssertEqual(item.statusCopy, "Preparando una copia para el análisis local.")
    }

    func testItemLifecycleRejectsInvalidAndPostTerminalTransitions() {
        var item = QueueReviewItem(id: "photo-a", ordinal: 0)

        XCTAssertFalse(item.transition(to: .saving))
        XCTAssertEqual(item.state, .queued)

        XCTAssertTrue(item.transition(to: .analyzing))
        XCTAssertEqual(item.progressPresentation, .indeterminate)

        XCTAssertTrue(
            item.completeAnalysis(
                proposal: QueueReviewProposal(
                    keywords: ["playa"],
                    caption: "Una playa al atardecer."
                )
            )
        )
        XCTAssertEqual(item.state, .ready)
        XCTAssertEqual(item.progressPresentation, .complete)

        XCTAssertTrue(item.transition(to: .saveQueued))
        XCTAssertEqual(item.statusCopy, "En cola para guardar.")
        XCTAssertTrue(item.transition(to: .saving))
        XCTAssertEqual(item.statusCopy, "Guardando y verificando.")
        XCTAssertEqual(item.progressPresentation, .indeterminate)
        XCTAssertTrue(item.transition(to: .verified))

        XCTAssertFalse(item.transition(to: .discarded))
        XCTAssertEqual(item.state, .verified)
    }

    func testManualDraftSurvivesACompletedRescan() {
        var store = ReviewSessionStore(
            items: [QueueReviewItem(id: "photo-a", ordinal: 0)]
        )

        XCTAssertTrue(store.beginAnalysis(id: "photo-a"))
        XCTAssertTrue(
            store.completeAnalysis(
                id: "photo-a",
                proposal: QueueReviewProposal(
                    keywords: ["playa"],
                    caption: "Una playa."
                )
            )
        )
        XCTAssertTrue(
            store.editDraft(
                id: "photo-a",
                keywords: ["mar", "atardecer"],
                caption: "Atardecer junto al mar."
            )
        )
        XCTAssertTrue(store.queueRescan(id: "photo-a"))
        XCTAssertTrue(store.beginAnalysis(id: "photo-a"))
        XCTAssertTrue(
            store.completeAnalysis(
                id: "photo-a",
                proposal: QueueReviewProposal(
                    keywords: ["océano"],
                    caption: "Vista del océano."
                )
            )
        )

        let item = try! XCTUnwrap(store.item(id: "photo-a"))
        XCTAssertEqual(item.proposal.keywords, ["océano"])
        XCTAssertEqual(item.proposal.caption, "Vista del océano.")
        XCTAssertEqual(item.draft.keywords, ["mar", "atardecer"])
        XCTAssertEqual(item.draft.caption, "Atardecer junto al mar.")
        XCTAssertTrue(item.hasManualEdits)
    }

    func testUneditedDraftTracksACompletedRescanProposal() {
        var store = ReviewSessionStore(
            items: [QueueReviewItem(id: "photo-a", ordinal: 0)]
        )

        XCTAssertTrue(store.beginAnalysis(id: "photo-a"))
        XCTAssertTrue(
            store.completeAnalysis(
                id: "photo-a",
                proposal: QueueReviewProposal(keywords: ["playa"], caption: nil)
            )
        )
        XCTAssertTrue(store.queueRescan(id: "photo-a"))
        XCTAssertTrue(store.beginAnalysis(id: "photo-a"))
        XCTAssertTrue(
            store.completeAnalysis(
                id: "photo-a",
                proposal: QueueReviewProposal(
                    keywords: ["mar"],
                    caption: "Vista del mar."
                )
            )
        )

        let item = try! XCTUnwrap(store.item(id: "photo-a"))
        XCTAssertEqual(item.draft.keywords, ["mar"])
        XCTAssertEqual(item.draft.caption, "Vista del mar.")
        XCTAssertFalse(item.hasManualEdits)
    }

    func testUpdatesPreserveItemOrderAndFocusedItem() {
        var store = ReviewSessionStore(
            items: [
                QueueReviewItem(id: "photo-a", ordinal: 0),
                QueueReviewItem(id: "photo-b", ordinal: 1),
                QueueReviewItem(id: "photo-c", ordinal: 2),
            ]
        )

        XCTAssertTrue(store.focus(id: "photo-b"))
        XCTAssertTrue(store.beginAnalysis(id: "photo-a"))
        XCTAssertTrue(
            store.completeAnalysis(
                id: "photo-a",
                proposal: QueueReviewProposal(keywords: ["arquitectura"], caption: nil)
            )
        )
        XCTAssertTrue(store.discard(id: "photo-c"))

        XCTAssertEqual(store.items.map(\.id), ["photo-a", "photo-b", "photo-c"])
        XCTAssertEqual(store.items.map(\.ordinal), [0, 1, 2])
        XCTAssertEqual(store.focusedItemID, "photo-b")
        XCTAssertEqual(store.focusedItem?.id, "photo-b")
    }

    func testTerminalCurrentCardAdvancesFocusToTheNextReviewableCard() {
        var store = ReviewSessionStore(items: [])
        let source = URL(fileURLWithPath: "/private/run/manifest.json")
        let photo = PreviewPhoto(uuid: "photo", date: "2026-09-09", existingKeywords: [],
                                 proposedKeywords: ["playa"], confidence: 0.9,
                                 modelUsed: "qwen3-vl:4b", state: "ready")
        store.applyQueueEvent(id: "photo-a", revision: 1, state: .ready, manifestURL: source, photo: photo)
        store.applyQueueEvent(id: "photo-b", revision: 1, state: .ready, manifestURL: source, photo: photo)
        store.applyQueueEvent(id: "photo-c", revision: 1, state: .ready, manifestURL: source, photo: photo)

        XCTAssertTrue(store.focus(id: "photo-b"))
        XCTAssertTrue(store.discard(id: "photo-b"))
        XCTAssertEqual(store.focusedItemID, "photo-c")

        store.applyQueueEvent(id: "photo-c", revision: 1, state: .verified)
        XCTAssertEqual(store.focusedItemID, "photo-a")

        let table = ContinuousReviewTable(session: store)
        XCTAssertEqual(table.focusedItem?.id, "photo-a")
        XCTAssertEqual(table.visibleItems.map(\.id), ["photo-a"])
    }

    func testQueuedWorkHidesProgressAndActiveWorkIsIndeterminate() {
        var item = QueueReviewItem(id: "photo-a", ordinal: 0)
        XCTAssertEqual(item.progressPresentation, .hidden)

        XCTAssertTrue(item.transition(to: .analyzing))
        XCTAssertEqual(item.progressPresentation, .indeterminate)

        XCTAssertTrue(
            item.completeAnalysis(
                proposal: QueueReviewProposal(keywords: [], caption: nil)
            )
        )
        XCTAssertEqual(item.progressPresentation, .complete)

        XCTAssertTrue(item.transition(to: .queued))
        XCTAssertEqual(item.progressPresentation, .hidden)
    }

    func testStatusCopyNeverIncludesIdentityOrEditableMetadata() {
        var item = QueueReviewItem(id: "private-photo-reference", ordinal: 0)
        XCTAssertTrue(item.transition(to: .analyzing))
        XCTAssertTrue(
            item.completeAnalysis(
                proposal: QueueReviewProposal(
                    keywords: ["interior"],
                    caption: "Texto privado de ejemplo."
                )
            )
        )

        let copy = item.statusCopy

        XCTAssertEqual(copy, "Lista para revisar.")
        XCTAssertFalse(copy.contains(item.id))
        XCTAssertFalse(copy.contains("interior"))
        XCTAssertFalse(copy.contains("Texto privado de ejemplo."))
    }

    func testEditingUnknownOrNonReadyItemDoesNotMutateTheStore() {
        var store = ReviewSessionStore(
            items: [QueueReviewItem(id: "photo-a", ordinal: 0)]
        )
        let original = store

        XCTAssertFalse(
            store.editDraft(id: "missing", keywords: ["mar"], caption: nil)
        )
        XCTAssertFalse(
            store.editDraft(id: "photo-a", keywords: ["mar"], caption: nil)
        )
        XCTAssertEqual(store, original)
    }

    func testVoiceOverAnnouncementsDescribeStagesWithoutPhotoContent() {
        XCTAssertEqual(
            ContinuousReviewAnnouncementCopy.text(for: .analyzing),
            "Analizando una foto en este Mac."
        )
        XCTAssertEqual(
            ContinuousReviewAnnouncementCopy.text(for: .ready),
            "Una foto está lista para revisar."
        )
        XCTAssertEqual(
            ContinuousReviewAnnouncementCopy.text(for: .saveQueued),
            "Una foto entró en la cola de guardado."
        )
        XCTAssertEqual(
            ContinuousReviewAnnouncementCopy.text(for: .saving),
            "Una foto se está guardando y verificando."
        )
        XCTAssertNil(ContinuousReviewAnnouncementCopy.text(for: .queued))
    }
}
