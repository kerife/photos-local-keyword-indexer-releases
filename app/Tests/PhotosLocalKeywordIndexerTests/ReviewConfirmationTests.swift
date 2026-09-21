import XCTest
@testable import PhotosLocalKeywordIndexer

final class ReviewConfirmationTests: XCTestCase {
    func testDestructiveConfirmationSheetsDoNotBindReturnAsTheDefaultAction() throws {
        let appRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let viewDirectory = appRoot.appendingPathComponent("PhotosLocalKeywordIndexer/Views")

        for filename in ["ApplyConfirmationSheet.swift", "RollbackConfirmationSheet.swift"] {
            let source = try String(
                contentsOf: viewDirectory.appendingPathComponent(filename),
                encoding: .utf8
            )

            XCTAssertFalse(
                source.contains(".keyboardShortcut(.defaultAction)"),
                "\(filename) must require deliberate activation for a destructive mutation."
            )
        }
    }

    func testDisabledConfirmationActionsExplainWhyTheyCannotContinue() {
        XCTAssertEqual(
            MutationConfirmationCopy.disabledApplyHint,
            "No hay cambios aprobados; vuelve a la revisión y selecciona al menos una keyword o caption."
        )
        XCTAssertEqual(
            MutationConfirmationCopy.disabledRollbackHint,
            "No hay cambios verificados para eliminar en este run; conserva el historial y revisa el estado manualmente."
        )
        XCTAssertFalse(MutationConfirmationCopy.disabledApplyHint.contains("/"))
        XCTAssertFalse(MutationConfirmationCopy.disabledRollbackHint.contains("/"))
    }

    func testApplyConfirmationSafetyCopyKeepsTheFirstWriteRecommendationVisible() {
        XCTAssertEqual(MutationConfirmationCopy.preApplySafetyTitle, "Antes de escribir en Fotos")
        XCTAssertTrue(MutationConfirmationCopy.preApplySafetyDetail.contains("biblioteca de Fotos de prueba"))
        XCTAssertTrue(MutationConfirmationCopy.preApplySafetyDetail.contains("respaldo reciente"))
        XCTAssertTrue(MutationConfirmationCopy.preApplySafetyAccessibilityLabel.contains("no valida tu biblioteca personal automáticamente"))
        XCTAssertFalse(MutationConfirmationCopy.preApplySafetyDetail.contains("/"))
        XCTAssertFalse(MutationConfirmationCopy.preApplySafetyAccessibilityLabel.contains("coordenadas"))
    }

    func testQueryOnlyApplyAccessibilityLabelDoesNotAnnounceAWrite() {
        let label = ReviewApplyButtonCopy.accessibilityLabel(
            reviewed: true,
            available: false,
            summary: "Se escribirán 1 foto afectada, 2 keywords aprobadas y 0 captions aprobados."
        )

        XCTAssertEqual(label, "Aplicación bloqueada. Este manifest queda solo para consulta.")
        XCTAssertFalse(label.contains("Se escribirán"))
        XCTAssertFalse(label.contains("caption"))
        XCTAssertFalse(label.contains("/"))
    }

    func testConfirmationDetailListsOnlyApprovedKeywordsAndCaptions() {
        let keywordPhoto = makePhoto(
            uuid: "keyword-photo",
            title: "Canal Grande",
            keywords: ["góndola", "cúpula"],
            caption: "No aprobado"
        )
        let captionPhoto = makePhoto(
            uuid: "caption-photo",
            title: "  Plaza\nSan Marcos  ",
            keywords: [],
            caption: "Una plaza visible."
        )
        var selection = ReviewSelection(photos: [keywordPhoto, captionPhoto])
        selection.setCaption(uuid: captionPhoto.uuid, selected: true)
        selection.setKeyword("góndola", for: keywordPhoto, selected: true)
        selection.setKeyword("cúpula", for: keywordPhoto, selected: false)

        let detail = ReviewConfirmationDetail(selection: selection, photos: [keywordPhoto, captionPhoto])

        XCTAssertEqual(detail.affectedPhotoCount, 2)
        XCTAssertEqual(detail.keywordCount, 1)
        XCTAssertEqual(detail.captionCount, 1)
        XCTAssertEqual(detail.keywordLines, ["Canal Grande: góndola"])
        XCTAssertEqual(detail.captionLines, ["Plaza San Marcos: Una plaza visible."])
        XCTAssertFalse(detail.summaryText.contains("No aprobado"))
    }

    func testConfirmationDetailGroupsApprovedChangesWithTheirPhotoThumbnailReference() {
        let selected = makePhoto(
            uuid: "selected-photo",
            photosLocalIdentifier: "photos-local-selected",
            title: "Canal Grande",
            keywords: ["góndola"],
            caption: "Una góndola en el canal."
        )
        let unselected = makePhoto(
            uuid: "unselected-photo",
            photosLocalIdentifier: "photos-local-unselected",
            title: "Otra foto",
            keywords: ["montaña"]
        )
        var selection = ReviewSelection(photos: [selected, unselected])
        selection.setKeyword("góndola", for: selected, selected: true)
        selection.setCaption(uuid: selected.uuid, selected: true)
        selection.setKeyword("montaña", for: unselected, selected: false)

        let detail = ReviewConfirmationDetail(
            selection: selection,
            photos: [selected, unselected]
        )

        XCTAssertEqual(
            detail.photoItems,
            [
                ConfirmationPhotoItem(
                    id: "selected-photo",
                    photosLocalIdentifier: "photos-local-selected",
                    displayTitle: "Canal Grande",
                    approvedKeywords: ["góndola"],
                    approvedCaption: "Una góndola en el canal."
                )
            ]
        )
    }

    func testThumbnailImplementationStaysOnDemandAndLocalOnly() throws {
        let appRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let service = try String(
            contentsOf: appRoot.appendingPathComponent(
                "PhotosLocalKeywordIndexer/Services/PhotoThumbnailProvider.swift"
            ),
            encoding: .utf8
        )
        let preview = try String(
            contentsOf: appRoot.appendingPathComponent(
                "PhotosLocalKeywordIndexer/Views/PreviewView.swift"
            ),
            encoding: .utf8
        )
        let confirmation = try String(
            contentsOf: appRoot.appendingPathComponent(
                "PhotosLocalKeywordIndexer/Views/ApplyConfirmationSheet.swift"
            ),
            encoding: .utf8
        )

        XCTAssertTrue(service.contains("isNetworkAccessAllowed = false"))
        XCTAssertTrue(service.contains("PHImageManager"))
        XCTAssertTrue(service.contains("requestAuthorization(for: .readWrite)"))
        XCTAssertFalse(service.contains("FileManager"))
        XCTAssertFalse(service.contains("URLSession"))
        XCTAssertTrue(preview.contains("PhotoThumbnailView("))
        XCTAssertTrue(confirmation.contains("PhotoThumbnailView("))
    }

    func testConfirmationDetailNamesTheExactMutationOnTheConfirmButton() {
        let photo = makePhoto(
            uuid: "exact-scope",
            title: "Canal",
            keywords: ["canal"],
            caption: "Una vista visible."
        )
        var selection = ReviewSelection(photos: [photo])
        selection.setCaption(uuid: photo.uuid, selected: true)
        selection.setKeyword("canal", for: photo, selected: true)

        let detail = ReviewConfirmationDetail(selection: selection, photos: [photo])

        XCTAssertEqual(detail.applyActionText, "Aplicar 1 keyword y 1 caption")
    }

    func testRetryConfirmationNamesRetryInsteadOfNewApply() {
        let photo = makePhoto(
            uuid: "retry-scope",
            title: "Canal",
            keywords: ["góndola"],
            applyState: "failed",
            errors: [ManifestPreviewError(stage: "apply", code: "APPLY_FAILED")]
        )
        let selection = ReviewSelection(photos: [photo], reviewedManifest: true)

        let detail = ReviewConfirmationDetail(
            selection: selection,
            photos: [photo],
            isRetryingFailedApply: true
        )

        XCTAssertTrue(detail.summaryText.contains("reintentar"))
        XCTAssertEqual(detail.applyActionText, "Reintentar 1 fila fallida")
    }

    func testCaptionConfirmationExplainsWriteAndPreservationConflict() {
        let photo = makePhoto(
            uuid: "caption-conflict",
            title: "Plaza",
            keywords: [],
            caption: "Una plaza visible."
        )
        var selection = ReviewSelection(photos: [photo])
        selection.setCaption(uuid: photo.uuid, selected: true)

        let detail = ReviewConfirmationDetail(selection: selection, photos: [photo])

        XCTAssertTrue(detail.captionConflictText.contains("solo si Fotos no tiene uno"))
        XCTAssertTrue(detail.captionConflictText.contains("se conserva"))
        XCTAssertTrue(detail.accessibilitySummary.contains(detail.captionConflictText))
    }

    func testCaptionConfirmationShowsTheEntireApprovedCaption() {
        let caption = "Una descripción visible del canal, la góndola, la cúpula y los edificios junto al agua que conserva todos los detalles concretos aprobados por la persona antes de escribir, incluido este remate final completo."
        let photo = makePhoto(
            uuid: "caption-complete",
            title: "Canal",
            keywords: [],
            caption: caption
        )
        var selection = ReviewSelection(photos: [photo])
        selection.setCaption(uuid: photo.uuid, selected: true)

        let detail = ReviewConfirmationDetail(selection: selection, photos: [photo])

        XCTAssertTrue(detail.captionLines.first?.hasSuffix("remate final completo.") == true)
        XCTAssertTrue(detail.accessibilitySummary.contains("remate final completo."))
    }

    func testConfirmationDetailDistinguishesKnownKeywordConflictsAndExternalChanges() {
        let photo = makePhoto(
            uuid: "conflict-photo",
            title: "Canal",
            existingKeywords: ["Viaje"],
            keywords: ["viaje", "canal"]
        )
        var selection = ReviewSelection(photos: [photo])
        selection.setAllKeywords(selected: true)

        let detail = ReviewConfirmationDetail(selection: selection, photos: [photo])

        XCTAssertEqual(detail.knownExistingKeywordCount, 1)
        XCTAssertEqual(detail.potentiallyNewKeywordCount, 1)
        XCTAssertTrue(detail.effectiveChangeText.contains("1 de 2"))
        XCTAssertTrue(detail.effectiveChangeText.contains("se conservará"))
        XCTAssertTrue(detail.externalConflictText.contains("volverá a leer Apple Fotos"))
        XCTAssertTrue(detail.externalConflictText.contains("se conservarán"))
        XCTAssertTrue(photo.reviewEffectiveChangeText?.contains("1 de 2") == true)
    }

    func testConfirmationSummaryDoesNotClaimExistingKeywordsWillBeWritten() {
        let photo = makePhoto(
            uuid: "summary-conflict",
            title: "Canal",
            existingKeywords: ["Viaje"],
            keywords: ["viaje", "canal"]
        )

        var selection = ReviewSelection(photos: [photo])
        selection.setAllKeywords(selected: true)
        let detail = ReviewConfirmationDetail(selection: selection, photos: [photo])

        XCTAssertFalse(detail.summaryText.contains("Se escribirán"))
        XCTAssertTrue(detail.summaryText.contains("Antes de escribir"))
        XCTAssertTrue(detail.summaryText.contains("se comprobarán"))
        XCTAssertTrue(detail.accessibilitySummary.contains(detail.effectiveChangeText))
    }

    func testConfirmationDetailSupportsCaptionOnlyAndUsesSafeFallbacks() {
        let photo = makePhoto(
            uuid: "caption-only",
            title: "\n  ",
            keywords: [],
            caption: "  Una escena\nvisible.  "
        )
        var selection = ReviewSelection(photos: [photo])
        selection.setCaption(uuid: photo.uuid, selected: true)

        XCTAssertTrue(selection.hasCaptionProposals)
        XCTAssertFalse(selection.canSelectAllKeywords)
        let detail = ReviewConfirmationDetail(selection: selection, photos: [photo])

        XCTAssertEqual(detail.affectedPhotoCount, 1)
        XCTAssertEqual(detail.keywordCount, 0)
        XCTAssertEqual(detail.captionCount, 1)
        XCTAssertEqual(detail.captionLines, ["Foto sin título: Una escena visible."])
        XCTAssertFalse(detail.captionLines.joined().contains(photo.uuid))
    }

    func testConfirmationDetailIsEmptyWhenNothingIsApproved() {
        let photo = makePhoto(uuid: "empty", title: "Canal", keywords: ["góndola"])
        var selection = ReviewSelection(photos: [photo])
        selection.setKeyword("góndola", for: photo, selected: false)

        let detail = ReviewConfirmationDetail(selection: selection, photos: [photo])

        XCTAssertEqual(detail.affectedPhotoCount, 0)
        XCTAssertEqual(detail.keywordLines, [])
        XCTAssertEqual(detail.captionLines, [])
        XCTAssertEqual(detail.summaryText, "No hay cambios aprobados.")
    }

    func testConfirmationDetailsKeepDuplicateLinesAddressable() {
        let first = makePhoto(
            uuid: "duplicate-first",
            title: "Foto",
            keywords: ["canal"]
        )
        let second = makePhoto(
            uuid: "duplicate-second",
            title: "Foto",
            keywords: ["canal"]
        )
        var selection = ReviewSelection(photos: [first, second])
        selection.setAllKeywords(selected: true)

        let detail = ReviewConfirmationDetail(selection: selection, photos: [first, second])

        XCTAssertEqual(detail.keywordLines, ["Foto: canal", "Foto: canal"])
        XCTAssertEqual(detail.keywordDisplayLines.map(\.id), [0, 1])
        XCTAssertEqual(detail.keywordDisplayLines.map(\.text), detail.keywordLines)
    }

    func testApplyConfirmationNamesEveryProtectedPhotosField() {
        let text = PhotoMetadataProtectionCopy.applyText
        let accessibility = PhotoMetadataProtectionCopy.applyAccessibility

        for value in ["originales", "títulos", "fechas", "ubicaciones", "álbumes", "favoritos", "caras", "Personas y mascotas"] {
            XCTAssertTrue(text.contains(value), "Missing apply boundary: \(value)")
            XCTAssertTrue(accessibility.contains(value), "Missing accessible apply boundary: \(value)")
        }
        XCTAssertFalse(text.contains("/"))
        XCTAssertFalse(accessibility.contains("coorden"))
        XCTAssertFalse(accessibility.contains("respuesta"))
    }

    func testRollbackConfirmationStatesOnlyVerifiedKeywordsAndCaptionsAreRemovable() {
        let text = PhotoMetadataProtectionCopy.rollbackText
        let accessibility = PhotoMetadataProtectionCopy.rollbackAccessibility

        XCTAssertTrue(text.contains("solo keywords y captions registrados y verificados"))
        XCTAssertTrue(accessibility.contains("solo keywords y captions registrados y verificados"))
        XCTAssertTrue(text.contains("variantes de mayúsculas"))
        XCTAssertTrue(text.contains("cambios externos posteriores"))
        XCTAssertFalse(accessibility.contains("originales"))
        XCTAssertFalse(accessibility.contains("coorden"))
        XCTAssertFalse(accessibility.contains("respuesta cruda"))
    }

    func testRollbackConfirmationDetailShowsExactVerifiedScopeAndBlockedStates() {
        let verified = makePhoto(
            uuid: "verified",
            title: "Canal Grande",
            keywords: [],
            caption: nil,
            applyState: "verified",
            appliedKeywords: ["góndola", "canal"],
            appliedCaption: "Una vista visible del canal."
        )
        let uncertain = makePhoto(
            uuid: "uncertain",
            title: "Otra foto",
            keywords: [],
            caption: nil,
            applyState: "uncertain"
        )
        let preview = RunManifestPreview(
            runID: "rollback-scope",
            createdAt: "2026-08-26T12:00:00Z",
            scanStatus: "ready",
            photos: [verified, uncertain]
        )

        let detail = RollbackConfirmationDetail(preview: preview)

        XCTAssertEqual(detail.affectedPhotoCount, 1)
        XCTAssertEqual(detail.keywordCount, 2)
        XCTAssertEqual(detail.captionCount, 1)
        XCTAssertEqual(detail.keywordLines, ["Canal Grande: góndola, canal"])
        XCTAssertEqual(detail.captionLines, ["Canal Grande: Una vista visible del canal."])
        XCTAssertEqual(detail.blockedPhotoCount, 1)
        XCTAssertEqual(
            detail.summaryText,
            "Se eliminarán únicamente 2 keywords y 1 caption verificados de 1 foto."
        )
        XCTAssertTrue(detail.blockedText.contains("no verificado"))
    }

    func testRollbackConfirmationShowsTheEntireVerifiedCaption() {
        let caption = "Una descripción visible del canal, la góndola, la cúpula y los edificios junto al agua que conserva todos los detalles registrados por este run antes de eliminar, incluido este remate final completo."
        let photo = makePhoto(
            uuid: "rollback-caption-complete",
            title: "Canal",
            keywords: [],
            applyState: "verified",
            appliedCaption: caption
        )
        let preview = RunManifestPreview(
            runID: "rollback-caption-complete",
            createdAt: "2026-08-26T12:00:00Z",
            scanStatus: "ready",
            photos: [photo]
        )

        let detail = RollbackConfirmationDetail(preview: preview)

        XCTAssertTrue(detail.captionLines.first?.hasSuffix("remate final completo.") == true)
        XCTAssertTrue(detail.accessibilitySummary.contains("remate final completo."))
    }

    func testRollbackConfirmationDetailDoesNotExposeUUIDsOrPaths() {
        let photo = makePhoto(
            uuid: "private-uuid",
            title: "\n  ",
            keywords: [],
            caption: nil,
            applyState: "verified",
            appliedKeywords: ["playa"]
        )
        let preview = RunManifestPreview(
            runID: "rollback-safe",
            createdAt: "2026-08-26T12:00:00Z",
            scanStatus: "ready",
            photos: [photo]
        )

        let detail = RollbackConfirmationDetail(preview: preview)
        let copy = [detail.summaryText, detail.blockedText, detail.keywordLines.joined()]
            .joined(separator: " ")

        XCTAssertTrue(copy.contains("Foto sin título"))
        XCTAssertFalse(copy.contains("private-uuid"))
        XCTAssertFalse(copy.contains("/"))
    }

    func testRollbackConfirmationDoesNotDoubleCountEligibleVerifiedPhotoWithScanError() {
        let verified = makePhoto(
            uuid: "verified-with-warning",
            title: "Foto verificada",
            keywords: [],
            applyState: "verified",
            appliedKeywords: ["canal"],
            errors: [ManifestPreviewError(stage: "analysis", code: "EXPORT_DELETE_FAILED")]
        )
        let detail = RollbackConfirmationDetail(
            preview: RunManifestPreview(
                runID: "rollback-warning",
                createdAt: "2026-08-26T12:00:00Z",
                scanStatus: "ready_with_errors",
                photos: [verified]
            )
        )

        XCTAssertEqual(detail.affectedPhotoCount, 1)
        XCTAssertEqual(detail.blockedPhotoCount, 0)
    }

    private func makePhoto(
        uuid: String,
        photosLocalIdentifier: String? = nil,
        title: String,
        existingKeywords: [String] = [],
        keywords: [String],
        caption: String? = nil,
        applyState: String = "not_run",
        appliedKeywords: [String] = [],
        appliedCaption: String? = nil,
        errors: [ManifestPreviewError] = []
    ) -> PreviewPhoto {
        PreviewPhoto(
            uuid: uuid,
            photosLocalIdentifier: photosLocalIdentifier,
            title: title,
            date: "2025-04-13T20:26:02",
            existingKeywords: existingKeywords,
            proposedKeywords: keywords,
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "ready",
            applyState: applyState,
            appliedKeywords: appliedKeywords,
            proposedCaption: caption,
            appliedCaption: appliedCaption,
            errors: errors
        )
    }
}
