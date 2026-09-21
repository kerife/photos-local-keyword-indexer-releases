import XCTest
@testable import PhotosLocalKeywordIndexer

final class ContinuousReviewPresentationTests: XCTestCase {
    func testRescanInspectorDoesNotAttributePreviousModelOrConfidenceToNewAttempt() throws {
        var session = ReviewSessionStore(items: [])
        session.applyQueueEvent(
            id: "photo-a", revision: 1, state: .ready,
            manifestURL: URL(fileURLWithPath: "/private/run/manifest.json"),
            photo: PreviewPhoto(uuid: "photo-a", date: "2026-09-09", existingKeywords: [],
                                proposedKeywords: ["playa"], confidence: 0.91,
                                modelUsed: "qwen3-vl:4b", modelReason: "location_context", state: "ready")
        )
        session.applyQueueEvent(id: "photo-a", revision: 2, state: .analyzing)
        let item = try XCTUnwrap(session.item(id: "photo-a"))
        let inspector = ContinuousReviewInspectorSnapshot(item: item)
        XCTAssertEqual(inspector.model, "No disponible")
        XCTAssertEqual(inspector.confidence, "No disponible")
        XCTAssertEqual(inspector.routingReason, "No disponible")
        XCTAssertFalse(inspector.locationContext.contains("GPS local y Apple Maps"))
        XCTAssertEqual(item.progressPresentation, .indeterminate)
        XCTAssertFalse(ContinuousReviewActions(item: item).canSave)
        XCTAssertFalse(ContinuousReviewActions(item: item).canEdit)
    }

    func testThumbnailIdentityChangesWhenTheReadyPhotoProvidesALocalIdentifier() {
        let itemID = UUID().uuidString.lowercased()
        var session = ReviewSessionStore(items: [])
        session.applyQueueEvent(
            id: itemID,
            revision: 1,
            state: .discovered
        )
        let discoveredIdentity = session.item(id: itemID)?.thumbnailIdentity

        session.applyQueueEvent(
            id: itemID,
            revision: 2,
            state: .ready,
            manifestURL: URL(fileURLWithPath: "/private/run/manifest.json"),
            photo: PreviewPhoto(
                uuid: UUID().uuidString.lowercased(),
                photosLocalIdentifier: "photo-kit-local-identifier",
                date: "2026-09-01T12:00:00",
                existingKeywords: [],
                proposedKeywords: ["mascota"],
                confidence: 0.91,
                modelUsed: "qwen3-vl:4b",
                state: "ready"
            )
        )

        XCTAssertEqual(discoveredIdentity, "pending-\(itemID)")
        XCTAssertEqual(
            session.item(id: itemID)?.thumbnailIdentity,
            "photo-photo-kit-local-identifier"
        )
        XCTAssertNotEqual(discoveredIdentity, session.item(id: itemID)?.thumbnailIdentity)
    }

    func testContinuousReviewSurfaceRoutesEditsThroughAppModel() throws {
        let appRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let reviewView = try String(
            contentsOf: appRoot.appendingPathComponent(
                "PhotosLocalKeywordIndexer/Views/ContinuousReviewView.swift"
            ),
            encoding: .utf8
        )
        let app = try String(
            contentsOf: appRoot.appendingPathComponent(
                "PhotosLocalKeywordIndexer/PhotosLocalKeywordIndexerApp.swift"
            ),
            encoding: .utf8
        )
        let reviewCard = try String(
            contentsOf: appRoot.appendingPathComponent(
                "PhotosLocalKeywordIndexer/Views/ContinuousReviewCard.swift"
            ),
            encoding: .utf8
        )

        XCTAssertFalse(reviewView.contains("_ = session.editDraft("))
        XCTAssertFalse(reviewCard.contains("session.editDraft("))
        XCTAssertTrue(reviewView.contains("private let onDraftChange:"))
        XCTAssertTrue(reviewView.contains("guard let current = currentItem(id: item.id)"))
        XCTAssertTrue(reviewCard.contains(".id(item.thumbnailIdentity)"))
        XCTAssertTrue(reviewCard.contains("locationContextSummary"))
        XCTAssertTrue(reviewCard.contains("location.circle"))
        XCTAssertTrue(reviewView.contains("private let onStop:"))
        XCTAssertTrue(reviewView.contains("Button(\"Finalizar sesión\", action: onStop)"))
        XCTAssertTrue(reviewView.contains("ScrollViewReader"))
        XCTAssertTrue(reviewView.contains("proxy.scrollTo(focusedID, anchor: .top)"))
        XCTAssertTrue(reviewView.contains(".id(item.id)"))
        XCTAssertTrue(app.contains("model.editContinuousItem("))
        XCTAssertTrue(app.contains("onStop: model.stopContinuousReview"))
    }

    func testControlsKeepOperationalLimitsAndExposeTheSelectedModel() {
        var controls = ContinuousReviewControls.defaults

        XCTAssertEqual(controls.photoCount, 10)
        XCTAssertEqual(controls.analysisConcurrency, 2)
        XCTAssertEqual(controls.modelSelection, "adaptive")

        XCTAssertFalse(controls.setPhotoCount(0))
        XCTAssertFalse(controls.setPhotoCount(51))
        XCTAssertTrue(controls.setPhotoCount(50))
        XCTAssertEqual(controls.photoCount, 50)

        XCTAssertFalse(controls.setAnalysisConcurrency(0))
        XCTAssertFalse(controls.setAnalysisConcurrency(5))
        XCTAssertTrue(controls.setAnalysisConcurrency(4))
        XCTAssertEqual(controls.analysisConcurrency, 4)

        XCTAssertFalse(controls.setModelSelection("  "))
        XCTAssertTrue(controls.setModelSelection("qwen3-vl:4b"))
        XCTAssertEqual(controls.modelSelection, "qwen3-vl:4b")
    }

    func testSummarySeparatesQueuedActiveReadySavedAndAttentionItems() {
        let queued = QueueReviewItem(id: "queued", ordinal: 0)
        var analyzing = QueueReviewItem(id: "analyzing", ordinal: 1)
        var ready = QueueReviewItem(id: "ready", ordinal: 2)
        var saved = QueueReviewItem(id: "saved", ordinal: 3)
        var saveQueued = QueueReviewItem(id: "saveQueued", ordinal: 4)
        var saving = QueueReviewItem(id: "saving", ordinal: 5)
        var attention = QueueReviewItem(id: "attention", ordinal: 6)

        XCTAssertTrue(analyzing.transition(to: .analyzing))
        XCTAssertTrue(ready.transition(to: .analyzing))
        XCTAssertTrue(
            ready.completeAnalysis(
                proposal: QueueReviewProposal(keywords: ["paisaje"], caption: nil)
            )
        )
        XCTAssertTrue(saved.transition(to: .analyzing))
        XCTAssertTrue(
            saved.completeAnalysis(
                proposal: QueueReviewProposal(keywords: ["interior"], caption: nil)
            )
        )
        XCTAssertTrue(saved.transition(to: .saveQueued))
        XCTAssertTrue(saved.transition(to: .saving))
        XCTAssertTrue(saved.transition(to: .verified))
        XCTAssertTrue(saveQueued.transition(to: .analyzing))
        XCTAssertTrue(
            saveQueued.completeAnalysis(
                proposal: QueueReviewProposal(keywords: ["interior"], caption: nil)
            )
        )
        XCTAssertTrue(saveQueued.transition(to: .saveQueued))
        XCTAssertTrue(saving.transition(to: .analyzing))
        XCTAssertTrue(
            saving.completeAnalysis(
                proposal: QueueReviewProposal(keywords: ["interior"], caption: nil)
            )
        )
        XCTAssertTrue(saving.transition(to: .saveQueued))
        XCTAssertTrue(saving.transition(to: .saving))
        XCTAssertTrue(attention.transition(to: .analyzing))
        XCTAssertTrue(attention.transition(to: .uncertain))

        let summary = ContinuousReviewSummary(
            items: [queued, analyzing, ready, saved, saveQueued, saving, attention]
        )

        XCTAssertEqual(summary.queued, 1)
        XCTAssertEqual(summary.analyzing, 1)
        XCTAssertEqual(summary.ready, 1)
        XCTAssertEqual(summary.saveQueued, 1)
        XCTAssertEqual(summary.saving, 1)
        XCTAssertEqual(summary.saved, 1)
        XCTAssertEqual(summary.discarded, 0)
        XCTAssertEqual(summary.attention, 1)
        XCTAssertEqual(
            summary.accessibilityLabel,
            "En cola: 1. Analizando: 1. Listas: 1. Esperando guardado: 1. Guardando: 1. Guardadas: 1. Descartadas: 0. Atención: 1."
        )
    }

    func testTableGroupsAutonomousAndManualWorkByVisiblePriority() {
        var session = ReviewSessionStore(items: [])
        let queued = QueueReviewItem(id: "queued", ordinal: 4)
        let ready = analyzedItem(id: "ready", ordinal: 3)
        var saving = analyzedItem(id: "saving", ordinal: 2)
        var sending = analyzedItem(id: "sending", ordinal: 0)
        var attention = QueueReviewItem(id: "attention", ordinal: 1)
        XCTAssertTrue(saving.transition(to: .saveQueued))
        XCTAssertTrue(saving.transition(to: .saving))
        sending.saveRequestStatus = .sending
        XCTAssertTrue(attention.transition(to: .analyzing))
        XCTAssertTrue(attention.transition(to: .failed))
        session = ReviewSessionStore(items: [queued, ready, saving, sending, attention])
        let autonomous = try! JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"campaign","event":"autonomy_activity","campaign_id":"campaign","revision":1,"position":8,"state":"analyzing","photos_local_identifier":"local-id"}"#.utf8)
        ).autonomyActivity!

        let table = ContinuousReviewTable(session: session, autonomyActivities: [autonomous])

        XCTAssertEqual(table.sections.map(\.kind), [.active, .attention, .review, .queued])
        XCTAssertEqual(table.autonomyActivities.map(\.position), [8])
        XCTAssertEqual(table.sections[0].items.map(\.id), ["sending", "saving"])
        XCTAssertEqual(table.sections[1].items.map(\.id), ["attention"])
        XCTAssertEqual(table.sections[2].items.map(\.id), ["ready"])
        XCTAssertEqual(table.sections[3].items.map(\.id), ["queued"])
    }

    func testActionsFollowItemStateAndRequireVisibleApprovedValuesToSave() {
        var emptyReady = QueueReviewItem(id: "empty", ordinal: 0)
        XCTAssertTrue(emptyReady.transition(to: .analyzing))
        XCTAssertTrue(emptyReady.completeAnalysis(proposal: .empty))

        var ready = QueueReviewItem(id: "ready", ordinal: 1)
        XCTAssertTrue(ready.transition(to: .analyzing))
        XCTAssertTrue(
            ready.completeAnalysis(
                proposal: QueueReviewProposal(
                    keywords: ["arquitectura"],
                    caption: "Fachada de un edificio."
                )
            )
        )

        var uncertain = ready
        XCTAssertTrue(uncertain.transition(to: .saveQueued))
        XCTAssertTrue(uncertain.transition(to: .saving))
        XCTAssertTrue(uncertain.transition(to: .uncertain))
        var waitingSave = ready
        XCTAssertTrue(waitingSave.transition(to: .saveQueued))

        XCTAssertFalse(ContinuousReviewActions(item: emptyReady).canSave)
        XCTAssertTrue(ContinuousReviewActions(item: ready).canSave)
        XCTAssertTrue(ContinuousReviewActions(item: ready).canEdit)
        XCTAssertTrue(ContinuousReviewActions(item: ready).canDiscard)
        XCTAssertTrue(ContinuousReviewActions(item: ready).canRescan)
        XCTAssertFalse(ContinuousReviewActions(item: uncertain).canSave)
        XCTAssertFalse(ContinuousReviewActions(item: uncertain).canEdit)
        XCTAssertTrue(ContinuousReviewActions(item: uncertain).canDiscard)
        XCTAssertTrue(ContinuousReviewActions(item: uncertain).canRescan)
        XCTAssertFalse(ContinuousReviewActions(item: waitingSave).canSave)
        XCTAssertFalse(ContinuousReviewActions(item: waitingSave).canEdit)
        XCTAssertFalse(ContinuousReviewActions(item: waitingSave).canDiscard)
        XCTAssertFalse(ContinuousReviewActions(item: waitingSave).canRescan)
    }

    func testInFlightItemsAllowDiscardButNotRescanBeforeAStableResult() {
        var analyzing = QueueReviewItem(id: "analyzing", ordinal: 0)
        XCTAssertTrue(analyzing.transition(to: .preparing))
        XCTAssertTrue(analyzing.transition(to: .analyzing))

        let actions = ContinuousReviewActions(item: analyzing)
        XCTAssertTrue(actions.canDiscard)
        XCTAssertFalse(actions.canRescan)
    }

    func testKeywordAndCaptionEditingStayWithinThePersistedContract() {
        XCTAssertEqual(
            QueueReviewEditPolicy.addingKeyword("  Gato adulto  ", to: ["casa"]),
            ["casa", "Gato adulto"]
        )
        XCTAssertNil(
            QueueReviewEditPolicy.addingKeyword("casa", to: ["casa"])
        )
        XCTAssertNil(
            QueueReviewEditPolicy.addingKeyword(
                "novena",
                to: ["uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho"]
            )
        )
        XCTAssertNil(
            QueueReviewEditPolicy.addingKeyword(String(repeating: "a", count: 129), to: [])
        )

        XCTAssertEqual(QueueReviewEditPolicy.caption("  Un gato en casa.  "), "Un gato en casa.")
        XCTAssertNil(QueueReviewEditPolicy.caption(String(repeating: "a", count: 241)))
        XCTAssertNil(QueueReviewEditPolicy.caption("línea\u{0000}inválida"))
        XCTAssertNil(QueueReviewEditPolicy.caption("   "))
    }

    func testTerminalItemsLeaveTheVisibleTableWithoutLosingAuditCounters() {
        var verified = analyzedItem(id: "verified", ordinal: 0)
        XCTAssertTrue(verified.transition(to: .saveQueued))
        XCTAssertTrue(verified.transition(to: .saving))
        XCTAssertTrue(verified.transition(to: .verified))
        var discarded = analyzedItem(id: "discarded", ordinal: 1)
        XCTAssertTrue(discarded.transition(to: .discarded))
        let ready = analyzedItem(id: "ready", ordinal: 2)
        var session = ReviewSessionStore(items: [verified, discarded, ready])
        XCTAssertTrue(session.focus(id: "verified"))

        let table = ContinuousReviewTable(session: session)
        let summary = ContinuousReviewSummary(items: session.items)

        XCTAssertEqual(table.visibleItems.map(\.id), ["ready"])
        XCTAssertEqual(table.focusedItem?.id, "ready")
        XCTAssertEqual(summary.saved, 1)
        XCTAssertEqual(summary.discarded, 1)
    }

    func testInspectorUsesOnlyAvailableSanitizedTechnicalFields() {
        var item = QueueReviewItem(id: "attempt-17", ordinal: 0)
        XCTAssertTrue(item.transition(to: .analyzing))
        XCTAssertTrue(
            item.completeAnalysis(
                proposal: QueueReviewProposal(
                    keywords: ["secreto-visible-en-tarjeta"],
                    caption: "Caption visible solo en el editor."
                )
            )
        )
        item.attach(
            manifestURL: URL(fileURLWithPath: "/private/run/manifest.json"),
            photo: PreviewPhoto(
                uuid: "12345678-1234-1234-1234-1234567890ab",
                date: "2026-09-01T12:00:00",
                existingKeywords: [],
                proposedKeywords: ["secreto-visible-en-tarjeta"],
                confidence: 0.87,
                modelUsed: "qwen3-vl:4b",
                modelReason: "location_context",
                state: "ready",
                proposedCaption: "Caption visible solo en el editor.",
                errors: [ManifestPreviewError(stage: "analysis", code: "LOW_CONFIDENCE")]
            )
        )

        let inspector = ContinuousReviewInspectorSnapshot(item: item)

        XCTAssertEqual(inspector.attemptID, "attempt-17")
        XCTAssertEqual(inspector.uuid, "12345678-1234-1234-1234-1234567890ab")
        XCTAssertEqual(inspector.model, "qwen3-vl:4b")
        XCTAssertEqual(inspector.routingReason, "Contexto de ubicación")
        XCTAssertEqual(inspector.confidence, "87%")
        XCTAssertEqual(inspector.locationContext, "Usado para orientar el análisis; no se guardaron coordenadas.")
        XCTAssertEqual(inspector.errorCodes, ["LOW_CONFIDENCE"])
        XCTAssertFalse(inspector.accessibilityLabel.contains("manifest.json"))
        XCTAssertFalse(inspector.accessibilityLabel.contains("secreto-visible-en-tarjeta"))
        XCTAssertFalse(inspector.accessibilityLabel.contains("Caption visible"))
    }

    func testInspectorDistinguishesGpsFromAppleMapsUsage() {
        var item = QueueReviewItem(id: "attempt-gps", ordinal: 0)
        item.attach(
            manifestURL: URL(fileURLWithPath: "/private/run/manifest.json"),
            photo: PreviewPhoto(
                uuid: "12345678-1234-1234-1234-1234567890ab",
                date: "2026-09-01T12:00:00",
                existingKeywords: [],
                proposedKeywords: ["plaza"],
                confidence: 0.87,
                modelUsed: "qwen3-vl:4b",
                modelReason: "location_context",
                state: "ready",
                technicalTrace: PreviewTechnicalTrace(
                    promptEffective: "Analiza evidencia visual.",
                    promptVersion: "vision-prompt-v1",
                    promptSHA256: String(repeating: "a", count: 64),
                    ollamaVersion: "0.12.7",
                    usedGPS: true,
                    usedAppleMaps: false,
                    usedLandmark: false,
                    placeContext: [],
                    durationsMilliseconds: ["total": 1]
                )
            )
        )

        let inspector = ContinuousReviewInspectorSnapshot(item: item)

        XCTAssertEqual(
            inspector.locationContext,
            "GPS local usado para orientar el análisis; Apple Maps no se consultó y no se guardaron coordenadas."
        )
        XCTAssertEqual(inspector.contextSources, "GPS")
    }

    func testInspectorComparesTheModelProposalWithTheManualDraft() {
        var item = QueueReviewItem(id: "attempt-diff", ordinal: 0)
        XCTAssertTrue(item.transition(to: .analyzing))
        XCTAssertTrue(item.completeAnalysis(
            proposal: QueueReviewProposal(
                keywords: ["gato", "casa"],
                caption: "Un gato descansa en casa."
            )
        ))
        XCTAssertTrue(item.editDraft(
            keywords: ["gato", "lentes"],
            caption: "Una persona con lentes junto a un gato."
        ))

        let inspector = ContinuousReviewInspectorSnapshot(item: item)

        XCTAssertEqual(inspector.proposalKeywords, "gato, casa")
        XCTAssertEqual(inspector.currentKeywords, "gato, lentes")
        XCTAssertEqual(
            inspector.reviewDifference,
            "Agregadas: lentes · eliminadas: casa · caption modificado."
        )
        XCTAssertTrue(inspector.accessibilityLabel.contains("1 keyword agregada"))
        XCTAssertTrue(inspector.accessibilityLabel.contains("1 keyword eliminada"))
        XCTAssertFalse(inspector.accessibilityLabel.contains("lentes"))
    }

    func testInspectorReportsWhenAppleMapsReturnsNoPlaceContext() {
        var item = QueueReviewItem(id: "attempt-map-empty", ordinal: 0)
        item.attach(
            manifestURL: URL(fileURLWithPath: "/private/run/manifest.json"),
            photo: PreviewPhoto(
                uuid: "12345678-1234-1234-1234-1234567890ab",
                date: "2026-09-01T12:00:00",
                existingKeywords: [],
                proposedKeywords: ["estatua"],
                confidence: 0.87,
                modelUsed: "qwen3-vl:4b",
                modelReason: "location_context",
                state: "ready",
                technicalTrace: PreviewTechnicalTrace(
                    promptEffective: "Analiza evidencia visual.",
                    promptVersion: "vision-prompt-v1",
                    promptSHA256: String(repeating: "a", count: 64),
                    ollamaVersion: "0.12.7",
                    usedGPS: true,
                    usedAppleMaps: true,
                    usedLandmark: false,
                    placeContext: [],
                    durationsMilliseconds: ["total": 1]
                )
            )
        )

        let inspector = ContinuousReviewInspectorSnapshot(item: item)

        XCTAssertEqual(
            inspector.locationContext,
            "GPS local usado; se intentó consultar Apple Maps pero no devolvió lugares; no se guardaron coordenadas."
        )
        XCTAssertEqual(inspector.placeContext, "Apple Maps no devolvió lugares")
    }

    func testInspectorDistinguishesAppleMapsLookupFailures() {
        let cases = [
            (
                "timeout",
                "GPS local usado; Apple Maps agotó el tiempo de espera sin contexto sanitizado; no se guardaron coordenadas.",
                "Apple Maps agotó el tiempo de espera"
            ),
            (
                "error",
                "GPS local usado; Apple Maps falló o no pudo completar la consulta; no se guardaron coordenadas.",
                "Apple Maps falló o no pudo completar la consulta"
            ),
            (
                "results_filtered",
                "GPS local usado; Apple Maps devolvió lugares descartados por sanitización; no se guardaron coordenadas.",
                "Apple Maps devolvió lugares descartados por sanitización"
            )
        ]

        for itemCase in cases {
            var item = QueueReviewItem(id: "attempt-map-\(itemCase.0)", ordinal: 0)
            item.attach(
                manifestURL: URL(fileURLWithPath: "/private/run/manifest.json"),
                photo: PreviewPhoto(
                    uuid: "12345678-1234-1234-1234-1234567890ab",
                    date: "2026-09-01T12:00:00",
                    existingKeywords: [],
                    proposedKeywords: ["plaza"],
                    confidence: 0.87,
                    modelUsed: "qwen3-vl:4b",
                    modelReason: "location_context",
                    state: "ready",
                    technicalTrace: PreviewTechnicalTrace(
                        promptEffective: "Analiza evidencia visual.",
                        promptVersion: "vision-prompt-v1",
                        promptSHA256: String(repeating: "a", count: 64),
                        ollamaVersion: "0.12.7",
                        usedGPS: true,
                        usedAppleMaps: true,
                        usedLandmark: false,
                        placeContext: [],
                        placeLookupState: itemCase.0,
                        durationsMilliseconds: ["total": 1]
                    )
                )
            )

            let inspector = ContinuousReviewInspectorSnapshot(item: item)

            XCTAssertEqual(inspector.locationContext, itemCase.1)
            XCTAssertEqual(inspector.placeContext, itemCase.2)
        }
    }

    func testInspectorReportsPlaceContextDiscardedByVisualEvidence() {
        var item = QueueReviewItem(id: "attempt-map-discarded", ordinal: 0)
        item.attach(
            manifestURL: URL(fileURLWithPath: "/private/run/manifest.json"),
            photo: PreviewPhoto(
                uuid: "12345678-1234-1234-1234-1234567890ab",
                date: "2026-09-01T12:00:00",
                existingKeywords: [],
                proposedKeywords: ["edificio"],
                confidence: 0.87,
                modelUsed: "qwen3-vl:4b",
                modelReason: "location_context",
                state: "ready",
                technicalTrace: PreviewTechnicalTrace(
                    promptEffective: "Analiza evidencia visual.",
                    promptVersion: "vision-prompt-v1",
                    promptSHA256: String(repeating: "a", count: 64),
                    ollamaVersion: "0.12.7",
                    usedGPS: true,
                    usedAppleMaps: true,
                    usedLandmark: false,
                    placeContext: ["Basílica de Guadalupe"],
                    placeLookupState: "results",
                    placeEvidenceState: "discarded_by_visual_evidence",
                    durationsMilliseconds: ["total": 1]
                )
            )
        )

        let inspector = ContinuousReviewInspectorSnapshot(item: item)

        XCTAssertEqual(
            inspector.locationContext,
            "GPS local y Apple Maps devolvieron lugares, pero se descartaron nombres específicos por falta de evidencia visual; no se guardaron coordenadas."
        )
        XCTAssertEqual(inspector.placeContext, "Basílica de Guadalupe")
        XCTAssertEqual(inspector.contextSources, "GPS, búsqueda Apple Maps")
    }

    func testPreviewTechnicalTraceDecodesLocationFlagsFromManifestKeys() throws {
        let hash = String(repeating: "a", count: 64)
        let data = Data(
            "{\"prompt_effective\":\"Analiza evidencia visual.\",\"prompt_version\":\"vision-prompt-v1\",\"prompt_sha256\":\"\(hash)\",\"ollama_version\":\"0.33.2\",\"used_gps\":true,\"used_apple_maps\":true,\"used_landmark\":false,\"place_context\":[],\"durations_ms\":{}}".utf8
        )
        let trace = try JSONDecoder().decode(PreviewTechnicalTrace.self, from: data)
        XCTAssertTrue(trace.usedGPS)
        XCTAssertTrue(trace.usedAppleMaps)
        XCTAssertTrue(trace.placeContext.isEmpty)
        XCTAssertNil(trace.placeLookupState)
        XCTAssertNil(trace.placeEvidenceState)
    }

    func testPreviewTechnicalTraceDecodesPlaceLookupStatesFromManifestKeys() throws {
        let hash = String(repeating: "a", count: 64)
        let data = Data(
            "{\"prompt_effective\":\"Analiza evidencia visual.\",\"prompt_version\":\"vision-prompt-v1\",\"prompt_sha256\":\"\(hash)\",\"used_gps\":true,\"used_apple_maps\":true,\"used_landmark\":false,\"place_context\":[\"Canal Grande\"],\"place_lookup_state\":\"results\",\"place_evidence_state\":\"visually_confirmed\",\"durations_ms\":{\"total\":1}}".utf8
        )

        let trace = try JSONDecoder().decode(PreviewTechnicalTrace.self, from: data)

        XCTAssertEqual(trace.placeLookupState, "results")
        XCTAssertEqual(trace.placeEvidenceState, "visually_confirmed")
    }

    func testPreparationPresentationKeepsTheCorrectiveActionOnReview() {
        XCTAssertEqual(ContinuousReviewPreparation.checking.phase, .checking)
        XCTAssertFalse(ContinuousReviewPreparation.checking.allowsQueueWork)

        let blocked = ContinuousReviewPreparation.blocked(.photosAccess)
        XCTAssertEqual(blocked.phase, .blocked)
        XCTAssertEqual(blocked.title, "Falta acceso a Fotos")
        XCTAssertEqual(blocked.actionTitle, "Abrir configuración de Fotos")
        XCTAssertFalse(blocked.allowsQueueWork)
        XCTAssertFalse(blocked.detail.contains("/"))

        XCTAssertTrue(ContinuousReviewPreparation.ready.allowsQueueWork)
    }

    func testAdvancedRescanDraftDefaultsToFreeLocalAndAllAnalysisLayers() throws {
        let draft = ContinuousReviewRescanDraft(model: "qwen3-vl:4b")

        XCTAssertEqual(draft.profile, .freeLocal)
        XCTAssertEqual(draft.layers, .all)
        XCTAssertEqual(draft.promptBody, ContinuousReviewRescanDraft.defaultPromptBody)
        XCTAssertTrue(draft.effectivePromptPreview.contains("Libre local"))

        let options = try XCTUnwrap(draft.validatedOptions)
        XCTAssertEqual(options.model, "qwen3-vl:4b")
        XCTAssertEqual(options.profile, .freeLocal)
        XCTAssertEqual(options.layers, .all)
        XCTAssertNil(options.additionalInformation)
        XCTAssertNil(options.analysisPrompt)
        XCTAssertFalse(options.resetPrompt)
        XCTAssertFalse(options.resetEdits)
    }

    func testAdvancedRescanDraftMapsCustomPrivateInputsWithoutChangingTheEnvelope() throws {
        var draft = ContinuousReviewRescanDraft(model: "qwen3-vl:8b")
        draft.profile = .balanced
        draft.layers.documentsText = false
        draft.layers.semanticNormalization = false
        draft.additionalInformation = "La imagen forma parte de una colección deportiva."
        draft.promptBody = "Prioriza equipos y mascotas visibles."

        let options = try XCTUnwrap(draft.validatedOptions)

        XCTAssertEqual(options.profile, .balanced)
        XCTAssertFalse(options.layers.documentsText)
        XCTAssertFalse(options.layers.semanticNormalization)
        XCTAssertEqual(
            options.additionalInformation,
            "La imagen forma parte de una colección deportiva."
        )
        XCTAssertEqual(options.analysisPrompt, "Prioriza equipos y mascotas visibles.")
        XCTAssertFalse(options.resetPrompt)
        XCTAssertFalse(options.resetEdits)
        XCTAssertTrue(draft.effectivePromptPreview.contains("Equilibrado"))
        XCTAssertTrue(draft.effectivePromptPreview.contains("documentos y texto: desactivada"))
        XCTAssertTrue(draft.effectivePromptPreview.contains("La estructura de salida y los límites de privacidad permanecen fijos."))
    }

    func testRestoringAdvancedRescanPromptKeepsModelProfileLayersAndAdditionalInformation() throws {
        var draft = ContinuousReviewRescanDraft(model: "qwen3-vl:8b")
        draft.profile = .conservative
        draft.layers.places = false
        draft.additionalInformation = "La escena pertenece a una colección deportiva."
        draft.promptBody = "Una instrucción personalizada."

        draft.restoreDefaultPrompt()

        XCTAssertEqual(draft.model, "qwen3-vl:8b")
        XCTAssertEqual(draft.profile, .conservative)
        XCTAssertFalse(draft.layers.places)
        XCTAssertEqual(
            draft.additionalInformation,
            "La escena pertenece a una colección deportiva."
        )
        XCTAssertEqual(draft.promptBody, ContinuousReviewRescanDraft.defaultPromptBody)
        let options = try XCTUnwrap(draft.validatedOptions)
        XCTAssertNil(options.analysisPrompt)
        XCTAssertTrue(options.resetPrompt)
    }

    func testAdvancedRescanCanExplicitlyResetManualEdits() throws {
        var draft = ContinuousReviewRescanDraft(model: "qwen3-vl:4b")
        draft.resetManualEdits = true

        let options = try XCTUnwrap(draft.validatedOptions)

        XCTAssertTrue(options.resetEdits)
        XCTAssertTrue(draft.effectivePromptPreview.contains("restablecer"))
    }

    func testAdvancedRescanDraftRejectsUnsafePrivateInputsBeforeSubmission() {
        var draft = ContinuousReviewRescanDraft(model: "qwen3-vl:4b")
        draft.additionalInformation = "API token: valor privado"
        XCTAssertNil(draft.validatedOptions)

        draft.additionalInformation = "Contexto visual permitido."
        draft.promptBody = "Usa 19.4326,-99.1332 como referencia."
        XCTAssertNil(draft.validatedOptions)
    }

    func testAdvancedRescanProfilesHaveExplicitUserFacingCopy() {
        XCTAssertEqual(QueueRescanProfile.freeLocal.displayName, "Libre local")
        XCTAssertEqual(QueueRescanProfile.balanced.displayName, "Equilibrado")
        XCTAssertEqual(QueueRescanProfile.conservative.displayName, "Conservador")
        XCTAssertTrue(QueueRescanProfile.allCases.allSatisfy { !$0.detail.isEmpty })
    }

    private func analyzedItem(id: String, ordinal: Int) -> QueueReviewItem {
        var item = QueueReviewItem(id: id, ordinal: ordinal)
        XCTAssertTrue(item.transition(to: .analyzing))
        XCTAssertTrue(
            item.completeAnalysis(
                proposal: QueueReviewProposal(keywords: ["paisaje"], caption: nil)
            )
        )
        return item
    }
}
