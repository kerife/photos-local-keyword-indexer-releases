import Foundation
import XCTest
@testable import PhotosLocalKeywordIndexer

final class PreviewAccessibilityTests: XCTestCase {
    func testEmptyPreviewOffersAReadOnlyEntryPointToNewRun() {
        XCTAssertEqual(
            ReviewEmptyStateActionCopy.title(preparationReady: true, preflightIsStale: false),
            "Abrir Revisión"
        )
        XCTAssertEqual(
            ReviewEmptyStateActionCopy.title(preparationReady: false, preflightIsStale: false),
            "Abrir Preparación"
        )
        XCTAssertTrue(
            ReviewEmptyStateActionCopy.hint(preparationReady: true, preflightIsStale: false)
                .contains("no modifica Fotos")
        )
    }

    func testReviewRowTitleCollapsesWhitespaceAndBoundsLongPhotoTitles() {
        let title = String(repeating: "Título ", count: 40)

        XCTAssertEqual(
            ReviewPhotoRowCopy.displayTitle("  Canal\n\nGrande  "),
            "Canal Grande"
        )
        XCTAssertEqual(
            ReviewPhotoRowCopy.displayTitle("   "),
            "Foto sin título"
        )
        XCTAssertEqual(ReviewPhotoRowCopy.displayTitle(title).count, 160)
    }

    func testReviewRowDateCollapsesWhitespaceAndBoundsLongDates() {
        let date = String(repeating: "2026-08-30 ", count: 20)

        XCTAssertEqual(
            ReviewPhotoRowCopy.displayDate("  2026-08-30\n12:00:00  "),
            "2026-08-30 12:00:00"
        )
        XCTAssertEqual(
            ReviewPhotoRowCopy.displayDate("   "),
            "Fecha no disponible"
        )
        XCTAssertEqual(ReviewPhotoRowCopy.displayDate(date).count, 64)
    }

    func testToggleAccessibilityLabelUsesOnlyTheSafePhotoTitle() {
        let photo = makePhoto(
            title: "Canal Grande",
            modelUsed: "qwen3-vl:8b",
            proposedCaption: "Caption no aprobado"
        )

        XCTAssertEqual(photo.accessibilityToggleLabel, "Seleccionar keywords de la foto: Canal Grande")
        XCTAssertFalse(photo.accessibilityToggleLabel.contains(photo.uuid))
        XCTAssertFalse(photo.accessibilityToggleLabel.contains(photo.date))
        XCTAssertFalse(photo.accessibilityToggleLabel.contains("qwen3-vl"))
        XCTAssertFalse(photo.accessibilityToggleLabel.contains("Caption no aprobado"))
    }

    func testToggleAccessibilityLabelCollapsesWhitespaceAndUsesFallback() {
        XCTAssertEqual(
            makePhoto(title: "  Canal\n\nGrande  ").accessibilityToggleLabel,
            "Seleccionar keywords de la foto: Canal Grande"
        )
        XCTAssertEqual(
            makePhoto(title: "   ").accessibilityToggleLabel,
            "Seleccionar keywords de la foto: Foto sin título"
        )
    }

    func testCaptionOnlyRowExplainsWhyPhotoToggleIsUnavailable() {
        let photo = makePhoto(state: "noop", proposedCaption: "Una escena visible.", proposedKeywords: [])

        XCTAssertEqual(
            photo.accessibilityToggleLabel,
            "Keywords no disponibles para esta foto: Canal Grande"
        )
    }

    func testBlockedRowExplainsStateBlockInsteadOfCaptionScope() {
        let photo = makePhoto(state: "analysis_failed", proposedKeywords: ["canal"])

        XCTAssertEqual(
            photo.accessibilityToggleHint,
            "Esta foto no se puede seleccionar por su estado actual; revisa el estado del run."
        )
    }

    func testBlockedRowSurfacesSafeErrorCopyInsteadOfGenericModelReason() {
        let photo = makePhoto(
            state: "analysis_failed",
            modelReason: nil,
            errors: [ManifestPreviewError(stage: "analysis", code: "ANALYSIS_FAILED")]
        )

        XCTAssertEqual(
            photo.reviewReasonText,
            "Atención: El análisis local terminó antes de producir una propuesta válida; esta versión no registró una causa más específica. Revisa esta foto antes de continuar."
        )
        XCTAssertFalse(photo.reviewReasonText.contains("ANALYSIS_FAILED"))
    }

    func testRowAccessibilityLabelCombinesSafeReviewMetadata() {
        let photo = makePhoto(
            title: "Canal Grande",
            state: "ready",
            applyState: "verified",
            modelUsed: "qwen3-vl:8b",
            confidence: 0.91,
            proposedCaption: "Caption no aprobado",
            captionState: "not_requested"
        )

        XCTAssertEqual(
            photo.accessibilityLabel,
            "Canal Grande. Fecha: 2025-04-13T20:26:02. Estado: Listo para revisar. Aplicación: Aplicado y verificado. Modelo: qwen3-vl:8b. Confianza: 91% (alta)."
        )
        XCTAssertFalse(photo.accessibilityLabel.contains(photo.uuid))
        XCTAssertFalse(photo.accessibilityLabel.contains("Caption no aprobado"))
        XCTAssertFalse(photo.accessibilityLabel.contains("coorden"))
    }

    func testRowAccessibilityLabelUsesFallbacksForMissingTitleModelAndConfidence() {
        let photo = makePhoto(
            title: "",
            state: "unknown_scan_state",
            applyState: "unknown_apply_state",
            modelUsed: "",
            confidence: nil
        )

        XCTAssertEqual(
            photo.accessibilityLabel,
            "Foto sin título. Fecha: 2025-04-13T20:26:02. Estado: Estado no disponible. Aplicación: Estado no disponible. Modelo: Modelo no disponible. Confianza: No disponible."
        )
    }

    func testRowAccessibilityLabelNamesTheUnavailableModelShownInTheRow() {
        XCTAssertTrue(
            makePhoto(state: "ready", modelUsed: nil).accessibilityLabel.contains("Modelo: Modelo no disponible.")
        )
        XCTAssertTrue(
            makePhoto(state: "analysis_failed", modelUsed: nil).accessibilityLabel.contains("Modelo: Sin inferencia.")
        )
    }

    func testModelDisplayTextDistinguishesUnanalyzedFromUnavailable() {
        XCTAssertEqual(
            makePhoto(state: "analysis_failed", modelUsed: nil).modelDisplayText,
            "Sin inferencia"
        )
        XCTAssertEqual(
            makePhoto(state: "ready", modelUsed: nil).modelDisplayText,
            "Modelo no disponible"
        )
        XCTAssertEqual(
            makePhoto(state: "ready", modelUsed: "  qwen3-vl:8b  ").modelDisplayText,
            "qwen3-vl:8b"
        )
    }

    func testModelDisplayTextBoundsLongModelNamesForRowsAndVoiceOver() {
        let longName = "vision/" + String(repeating: "x", count: 110) + ":latest"

        let displayed = makePhoto(state: "ready", modelUsed: longName).modelDisplayText

        XCTAssertEqual(displayed.count, ModelPresentationCopy.maximumLength)
        XCTAssertTrue(displayed.hasPrefix("vision/"))
        XCTAssertTrue(displayed.hasSuffix(":latest"))
        XCTAssertTrue(makePhoto(state: "ready", modelUsed: longName).accessibilityLabel.contains(displayed))
    }

    func testModelDisplayTextHidesInvalidManifestValues() {
        let photo = makePhoto(state: "ready", modelUsed: "/private/model-cache")

        XCTAssertEqual(photo.modelDisplayText, "Modelo no disponible")
        XCTAssertFalse(photo.accessibilityLabel.contains("/private/model-cache"))
    }

    func testNoopRowWithCaptionProposalNamesCaptionOnlyReview() {
        let photo = makePhoto(state: "noop", proposedCaption: "Una escena visible.", proposedKeywords: [])

        XCTAssertEqual(photo.stateLabel, "Sin cambios de keywords; caption para revisar")
    }

    func testInvalidVerifiedCaptionProposalIsAttentionOnly() {
        let photo = makePhoto(
            applyState: "verified",
            proposedCaption: "Una escena visible.",
            captionState: "proposed"
        )

        XCTAssertTrue(photo.needsReviewAttention)
        XCTAssertTrue(ReviewPhotoFilter.attention.includes(photo))
        XCTAssertFalse(ReviewPhotoFilter.captions.includes(photo))
        XCTAssertTrue(ReviewPhotoGroup.attention.includes(photo))
        XCTAssertFalse(ReviewPhotoGroup.unchanged.includes(photo))
    }

    func testCaptionOnlyRowDoesNotExposeAnEmptyKeywordProposalSection() {
        let captionOnly = makePhoto(
            state: "noop",
            proposedCaption: "Una escena visible.",
            proposedKeywords: []
        )
        let keywordRow = makePhoto(proposedKeywords: ["canal"])

        XCTAssertNil(captionOnly.keywordProposalSectionLabel)
        XCTAssertEqual(keywordRow.keywordProposalSectionLabel, "Propuestas")
    }

    func testConfidenceBandTurnsTheScoreIntoAnActionableReviewSignal() {
        XCTAssertEqual(makePhoto(confidence: 0.90).confidenceBand, .high)
        XCTAssertEqual(makePhoto(confidence: 0.60).confidenceBand, .medium)
        XCTAssertEqual(makePhoto(confidence: 0.59).confidenceBand, .low)
        XCTAssertEqual(makePhoto(confidence: nil).confidenceBand, .unavailable)

        XCTAssertEqual(makePhoto(confidence: 0.90).confidenceText, "90% · alta")
        XCTAssertEqual(makePhoto(confidence: 0.60).confidenceText, "60% · media")
        XCTAssertEqual(makePhoto(confidence: 0.59).confidenceText, "59% · baja")
        XCTAssertEqual(makePhoto(confidence: nil).confidenceText, "No disponible")
    }

    func testRowAccessibilityLabelDoesNotPresentInvalidConfidenceAsARealScore() {
        let photo = makePhoto(confidence: 1.20)

        XCTAssertTrue(photo.accessibilityLabel.contains("Confianza: No disponible."))
        XCTAssertFalse(photo.accessibilityLabel.contains("120%"))
    }

    func testReviewConfidenceSummaryExcludesLowConfidenceRowsBlockedByPolicy() {
        let photos = [
            makePhoto(uuid: "high", confidence: 0.91),
            makePhoto(uuid: "medium", confidence: 0.60),
            makePhoto(uuid: "low", confidence: 0.59),
        ]
        var selection = ReviewSelection(photos: photos)
        selection.setAllKeywords(selected: true)
        let summary = ReviewConfidenceSummary(
            photos: photos,
            approvedPhotoIDs: selection.approvedPhotoIDs
        )

        XCTAssertEqual(summary.affectedPhotoCount, 2)
        XCTAssertEqual(summary.mediumCount, 1)
        XCTAssertEqual(summary.lowCount, 0)
        XCTAssertTrue(summary.isVisible)
        XCTAssertEqual(
            summary.text,
            "Revisa antes de aplicar: 1 foto tiene confianza media."
        )
        XCTAssertTrue(summary.accessibilityLabel.contains("2 fotos aprobadas"))
        XCTAssertFalse(selection.approvedPhotoIDs.contains("low"))
    }

    func testReviewConfidenceSummaryHidesWhenApprovedPhotosAreHighOrUnavailable() {
        let photos = [
            makePhoto(uuid: "high", confidence: 0.91),
            makePhoto(uuid: "unknown", confidence: nil),
        ]
        let summary = ReviewConfidenceSummary(
            photos: photos,
            approvedPhotoIDs: Set(photos.map(\.uuid))
        )

        XCTAssertEqual(summary.affectedPhotoCount, 2)
        XCTAssertEqual(summary.mediumCount, 0)
        XCTAssertEqual(summary.lowCount, 0)
        XCTAssertFalse(summary.isVisible)
        XCTAssertEqual(summary.text, "Las fotos aprobadas tienen confianza alta o no disponible.")
        XCTAssertTrue(summary.accessibilityLabel.contains("2 fotos aprobadas"))
    }

    func testReviewConfidenceSummaryIgnoresUnapprovedLowConfidencePhotos() {
        let photos = [
            makePhoto(uuid: "high", confidence: 0.91),
            makePhoto(uuid: "low", confidence: 0.59),
        ]
        let summary = ReviewConfidenceSummary(
            photos: photos,
            approvedPhotoIDs: ["high"]
        )

        XCTAssertEqual(summary.affectedPhotoCount, 1)
        XCTAssertEqual(summary.mediumCount, 0)
        XCTAssertEqual(summary.lowCount, 0)
        XCTAssertFalse(summary.isVisible)
        XCTAssertFalse(summary.accessibilityLabel.contains("baja"))
    }

    func testReviewBlockedSummaryShowsPhotosExcludedFromApply() {
        let summary = ReviewBlockedSummary(photos: [
            makePhoto(uuid: "ready", state: "ready"),
            makePhoto(uuid: "failed", state: "analysis_failed"),
            makePhoto(uuid: "cancelled", state: "cancelled"),
        ])

        XCTAssertTrue(summary.isVisible)
        XCTAssertEqual(summary.blockedPhotoCount, 2)
        XCTAssertEqual(
            summary.text,
            "2 fotos quedan bloqueadas y no se aplicarán; las propuestas válidas siguen disponibles para revisión."
        )
        XCTAssertEqual(summary.accessibilityLabel, summary.text)
    }

    func testReviewBlockedSummaryStaysHiddenWhenEveryPhotoCanBeReviewed() {
        let summary = ReviewBlockedSummary(photos: [
            makePhoto(uuid: "ready", state: "ready"),
            makePhoto(uuid: "noop", state: "noop", proposedKeywords: []),
        ])

        XCTAssertFalse(summary.isVisible)
        XCTAssertEqual(summary.blockedPhotoCount, 0)
        XCTAssertEqual(summary.text, "Todas las fotos del run son revisables.")
    }

    func testRowAccessibilityLabelCoversAllScanStatesWithHumanLabels() {
        let expected: [(String, String)] = [
            ("ready", "Listo para revisar"),
            ("noop", "Sin cambios de keywords"),
            ("analysis_failed", "Análisis fallido"),
            ("cancelled", "Cancelado"),
            ("future_scan_state", "Estado no disponible"),
        ]

        for (state, label) in expected {
            XCTAssertTrue(
                makePhoto(state: state).accessibilityLabel.contains("Estado: \(label)."),
                "Unexpected accessibility label for scan state \(state)"
            )
        }
    }

    func testReviewSelectionIsDisabledForFailedOrCancelledPhotos() {
        XCTAssertTrue(makePhoto(state: "ready").isReviewSelectable)
        XCTAssertTrue(makePhoto(state: "noop").isReviewSelectable)
        XCTAssertFalse(makePhoto(state: "analysis_failed").isReviewSelectable)
        XCTAssertFalse(makePhoto(state: "cancelled").isReviewSelectable)
        XCTAssertFalse(makePhoto(state: "future_scan_state").isReviewSelectable)
    }

    func testReviewSelectionIsDisabledWhenAReadyPhotoHasErrors() {
        let photo = makePhoto(
            state: "ready",
            proposedCaption: "Una escena visible.",
            proposedKeywords: ["canal"],
            errors: [ManifestPreviewError(stage: "analysis", code: "ANALYSIS_FAILED")]
        )

        XCTAssertFalse(photo.isReviewSelectable)
        XCTAssertEqual(
            photo.approvalScopeText,
            "No seleccionable: la foto contiene errores que requieren atención."
        )
        XCTAssertEqual(
            photo.keywordAccessibilityLabel(for: "canal"),
            "Keyword no aprobable: canal"
        )
        XCTAssertTrue(photo.keywordAccessibilityHint.contains("contiene errores"))
        XCTAssertFalse(photo.keywordAccessibilityHint.contains("Se escribirá"))
        XCTAssertEqual(
            photo.captionAccessibilityLabel,
            "Caption no aprobable: Una escena visible."
        )
        XCTAssertTrue(photo.captionAccessibilityHint.contains("contiene errores"))
        XCTAssertFalse(photo.captionAccessibilityHint.contains("Aprueba"))
        XCTAssertFalse(ReviewPhotoFilter.changes.includes(photo))
        XCTAssertTrue(ReviewPhotoFilter.attention.includes(photo))
        var selection = ReviewSelection(photos: [photo])
        selection.setPhoto(photo, selected: true)
        selection.setKeyword("canal", for: photo, selected: true)
        XCTAssertEqual(selection.selectedChangeCount, 0)
        selection.setAllKeywords(selected: true)
        XCTAssertEqual(selection.selectedChangeCount, 0)
    }

    func testAlreadyAppliedRowWithProposalsIsNotReviewSelectable() {
        let photo = makePhoto(
            state: "ready",
            applyState: "verified",
            proposedKeywords: ["canal"]
        )

        XCTAssertFalse(photo.isReviewSelectable)
        XCTAssertFalse(photo.canSelectPhoto)
        XCTAssertEqual(
            photo.approvalScopeText,
            "No seleccionable: la foto ya tiene un resultado registrado."
        )
    }

    func testReviewSelectionBlocksProposalsBelowThePolicyConfidenceThreshold() {
        let photo = makePhoto(
            state: "ready",
            confidence: 0.59,
            proposedCaption: "Una escena visible.",
            proposedKeywords: ["canal"]
        )

        XCTAssertTrue(photo.hasConfidenceBelowReviewThreshold)
        XCTAssertFalse(photo.isReviewSelectable)
        XCTAssertTrue(photo.needsReviewAttention)
        XCTAssertTrue(ReviewPhotoFilter.attention.includes(photo))
        XCTAssertFalse(ReviewPhotoFilter.changes.includes(photo))
        XCTAssertFalse(ReviewPhotoFilter.captions.includes(photo))
        XCTAssertEqual(
            photo.approvalScopeText,
            "No seleccionable: confianza inferior al umbral de 60%."
        )

        var selection = ReviewSelection(photos: [photo])
        selection.setPhoto(photo, selected: true)
        selection.setKeyword("canal", for: photo, selected: true)
        selection.setCaption(uuid: photo.uuid, selected: true)
        XCTAssertEqual(selection.selectedChangeCount, 0)
    }

    func testReviewSelectionBlocksProposalsWithoutAValidConfidenceScore() {
        for confidence in [nil, 1.20] as [Double?] {
            let photo = makePhoto(
                state: "ready",
                confidence: confidence,
                proposedCaption: "Una escena visible.",
                proposedKeywords: ["canal"]
            )

            XCTAssertFalse(photo.isReviewSelectable)
            XCTAssertTrue(photo.needsReviewAttention)
            XCTAssertTrue(ReviewPhotoFilter.attention.includes(photo))
            XCTAssertFalse(ReviewPhotoFilter.changes.includes(photo))
            XCTAssertFalse(ReviewPhotoFilter.captions.includes(photo))
            XCTAssertEqual(
                photo.approvalScopeText,
                "No seleccionable: confianza no disponible o inválida."
            )
            XCTAssertTrue(photo.shouldDisplayConfidence)
            XCTAssertTrue(photo.accessibilityLabel.contains("Confianza: No disponible"))

            var selection = ReviewSelection(photos: [photo])
            selection.setPhoto(photo, selected: true)
            selection.setKeyword("canal", for: photo, selected: true)
            selection.setCaption(uuid: photo.uuid, selected: true)
            XCTAssertEqual(selection.selectedChangeCount, 0)
        }
    }

    func testLowConfidenceWithoutProposalsRemainsAnAuditableNoopRow() {
        let photo = makePhoto(
            state: "noop",
            confidence: 0.59,
            proposedKeywords: []
        )

        XCTAssertFalse(photo.hasConfidenceBelowReviewThreshold)
        XCTAssertTrue(photo.isReviewSelectable)
        XCTAssertFalse(photo.needsReviewAttention)
    }

    func testRowAccessibilityLabelCoversRelevantApplyStatesWithHumanLabels() {
        let expected: [(String, String)] = [
            ("not_run", "No aplicado"),
            ("writing", "Escribiendo"),
            ("verified", "Aplicado y verificado"),
            ("noop", "Sin cambios"),
            ("failed", "Aplicación fallida"),
            ("uncertain", "Requiere revisión manual"),
            ("cancelled", "Cancelado"),
            ("future_apply_state", "Estado no disponible"),
        ]

        for (applyState, label) in expected {
            XCTAssertTrue(
                makePhoto(applyState: applyState).accessibilityLabel.contains("Aplicación: \(label)."),
                "Unexpected accessibility label for apply state \(applyState)"
            )
        }
    }

    func testReviewStatusKeepsCancelledMutationVisibleAfterScanState() {
        let cancelledApply = makePhoto(applyState: "cancelled")
        XCTAssertEqual(
            cancelledApply.reviewStatusText,
            "Listo para revisar · Aplicación: Cancelado"
        )

        let cancelledRollback = makePhoto(applyState: "verified", rollbackState: "cancelled")
        XCTAssertEqual(
            cancelledRollback.reviewStatusText,
            "Listo para revisar · Aplicación: Aplicado y verificado · Rollback: Cancelado"
        )
    }

    func testKeywordToggleAccessibilityLabelExplainsApprovalAndNormalizesText() {
        let photo = makePhoto()

        XCTAssertEqual(
            photo.keywordAccessibilityLabel(for: "  góndola\n\nvisible  "),
            "Aprobar keyword propuesta: góndola visible"
        )
        XCTAssertEqual(
            photo.keywordAccessibilityHint,
            "Se escribirá únicamente después de revisar y confirmar la aplicación."
        )
    }

    func testCaptionToggleAccessibilityLabelExplainsApprovalWithoutRowMetadata() {
        let photo = makePhoto(
            proposedCaption: "  Un canal\n\nvisible.  "
        )

        XCTAssertEqual(
            photo.captionAccessibilityLabel,
            "Aprobar caption propuesto: Un canal visible."
        )
        XCTAssertEqual(
            photo.captionAccessibilityHint,
            CaptionReviewCopy.accessibilityHint
        )
        XCTAssertFalse(photo.captionAccessibilityLabel.contains(photo.uuid))
        XCTAssertFalse(photo.captionAccessibilityLabel.contains(photo.date))
    }

    func testWhitespaceOnlyCaptionIsNotRenderedAsAnActionableProposal() {
        XCTAssertNil(makePhoto(proposedCaption: "  \n\t  ").reviewCaptionText)
        XCTAssertEqual(
            makePhoto(proposedCaption: "  Una escena\nvisible.  ").reviewCaptionText,
            "Una escena visible."
        )
    }

    func testCaptionReviewCopyMakesPreservationAndSeparateApprovalExplicit() {
        XCTAssertTrue(CaptionReviewCopy.title.contains("solo si no existe uno"))
        XCTAssertTrue(CaptionReviewCopy.detail.contains("se conserva"))
        XCTAssertTrue(CaptionReviewCopy.detail.contains("por separado"))
        XCTAssertTrue(CaptionReviewCopy.accessibilityHint.contains("caption existente"))
        XCTAssertTrue(CaptionReviewCopy.accessibilityHint.contains("confirmar"))
    }

    func testReviewedManifestControlsDescribeTheFixedSelectionAsReadOnly() {
        let photo = makePhoto(
            proposedCaption: "Una escena visible.",
            proposedKeywords: ["canal"]
        )

        XCTAssertEqual(
            photo.photoSelectionAccessibilityLabel(selectionIsFixed: true),
            "Keywords registradas en manifiesto revisado: Canal Grande"
        )
        XCTAssertEqual(
            photo.keywordAccessibilityLabel(for: "canal", selectionIsFixed: true),
            "Keyword registrada en manifiesto revisado: canal"
        )
        XCTAssertEqual(
            photo.captionControlAccessibilityLabel(selectionIsFixed: true),
            "Caption registrado en manifiesto revisado: Una escena visible."
        )
        XCTAssertTrue(
            photo.reviewControlAccessibilityHint(selectionIsFixed: true)
                .contains("selección ya está fijada")
        )
        XCTAssertFalse(
            photo.photoSelectionAccessibilityLabel(selectionIsFixed: true)
                .contains("Seleccionar")
        )
        XCTAssertFalse(
            photo.captionControlAccessibilityLabel(selectionIsFixed: true)
                .contains("Aprobar")
        )
    }

    func testCaptionOutcomeStatesAreVisibleAndActionableForAccessibility() {
        let preserved = PreviewPhoto(
            uuid: "preserved-caption",
            title: "Canal",
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: ["canal"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "ready",
            applyState: "verified",
            appliedKeywords: ["canal"],
            proposedCaption: "Una vista visible.",
            captionState: "preserved"
        )
        XCTAssertEqual(preserved.captionStateLabel, "Conservado: ya existía un caption")
        XCTAssertTrue(preserved.reviewStatusText.contains("Caption: Conservado: ya existía un caption"))
        XCTAssertTrue(preserved.accessibilityLabel.contains("Caption: Conservado: ya existía un caption"))
        XCTAssertTrue(preserved.captionAccessibilityHint.contains("ya se conservó"))

        let uncertain = PreviewPhoto(
            uuid: "uncertain-caption",
            title: "Canal",
            date: "2025-04-13T20:26:02",
            existingKeywords: [],
            proposedKeywords: ["canal"],
            confidence: 0.91,
            modelUsed: "qwen3-vl:8b",
            state: "ready",
            // Keep the aggregate apply state verified so this assertion proves
            // the caption outcome itself is enough to enter attention.
            applyState: "verified",
            appliedKeywords: ["canal"],
            proposedCaption: "Una vista visible.",
            appliedCaption: "Una vista visible.",
            captionState: "uncertain"
        )
        XCTAssertEqual(uncertain.captionStateLabel, "Requiere revisión manual")
        XCTAssertTrue(uncertain.reviewStatusText.contains("Caption: Requiere revisión manual"))
        XCTAssertTrue(uncertain.accessibilityLabel.contains("Caption: Requiere revisión manual"))
        XCTAssertTrue(uncertain.captionAccessibilityHint.contains("revisión manual"))
        XCTAssertTrue(uncertain.needsReviewAttention)
    }

    func testCaptionProposalDoesNotLookPendingAfterFailedMutation() {
        let failed = makePhoto(
            applyState: "failed",
            proposedCaption: "Una vista visible.",
            captionState: "proposed",
            errors: [ManifestPreviewError(stage: "apply", code: "APPLY_FAILED")]
        )

        XCTAssertEqual(failed.captionStateLabel, "No aplicado: aplicación fallida")
        XCTAssertTrue(failed.reviewStatusText.contains("Caption: No aplicado: aplicación fallida"))
        XCTAssertTrue(failed.captionAccessibilityLabel.contains("No aplicado: aplicación fallida"))
        XCTAssertTrue(failed.captionAccessibilityHint.contains("filas fallidas"))
    }

    func testReviewControlAccessibilityLabelsUseFallbacksForEmptyText() {
        let photo = makePhoto(proposedCaption: "   ")

        XCTAssertEqual(
            photo.keywordAccessibilityLabel(for: "   "),
            "Aprobar keyword propuesta: Keyword sin texto"
        )
        XCTAssertEqual(
            photo.captionAccessibilityLabel,
            "Aprobar caption propuesto: Caption sin texto"
        )
    }

    func testApprovalScopeTextSeparatesKeywordsFromCaptionApproval() {
        XCTAssertEqual(
            makePhoto(proposedCaption: "Una escena visible.").approvalScopeText,
            "1 keyword propuesta; caption requiere aprobación separada."
        )
        XCTAssertEqual(
            makePhoto(state: "noop", proposedCaption: "Una escena visible", proposedKeywords: []).approvalScopeText,
            "Caption propuesto; requiere aprobación separada."
        )
        XCTAssertEqual(
            makePhoto(state: "analysis_failed", proposedCaption: "Una escena visible", proposedKeywords: ["canal"]).approvalScopeText,
            "No seleccionable: Análisis fallido."
        )
        XCTAssertEqual(
            makePhoto(proposedCaption: nil, proposedKeywords: []).approvalScopeText,
            "No hay keywords ni captions aprobables."
        )
        XCTAssertEqual(
            makePhoto(
                proposedCaption: "Una escena visible.",
                proposedKeywords: ["canal", "góndola"]
            ).approvalScopeText,
            "2 keywords propuestas; caption requiere aprobación separada."
        )
    }

    func testReviewContextSeparatesLocalEvidenceFromModelReasonWithoutLeakingRawText() {
        let rawReason = "Una persona aparece junto a un monumento"
        let photo = makePhoto(modelReason: rawReason)

        XCTAssertEqual(photo.reviewEvidenceText, "Evidencia local registrada: keywords existentes: Venecia.")
        XCTAssertEqual(
            photo.reviewReasonText,
            "Motivo del modelo disponible; confirma la propuesta con la evidencia visible y la confianza antes de aprobar."
        )
        XCTAssertFalse(photo.reviewReasonText.contains(rawReason))
    }

    func testLocationContextExplainsItsReadOnlyReviewBoundary() {
        let photo = makePhoto(modelReason: "location_context")

        XCTAssertEqual(
            photo.locationContextReviewText,
            "Se usó contexto de ubicación solo para orientar el análisis; no se escribirá ni guardará la ubicación."
        )
        XCTAssertFalse(photo.locationContextReviewText?.contains("coorden") == true)
        XCTAssertNil(makePhoto(modelReason: "no_location").locationContextReviewText)
    }

    private func makePhoto(
        uuid: String = "49F027C6-0000-4000-8000-000000000000",
        title: String = "Canal Grande",
        state: String = "ready",
        applyState: String = "not_run",
        rollbackState: String = "not_run",
        modelUsed: String? = "qwen3-vl:8b",
        confidence: Double? = 0.91,
        modelReason: String? = nil,
        proposedCaption: String? = nil,
        captionState: String? = nil,
        proposedKeywords: [String] = ["góndola"],
        errors: [ManifestPreviewError] = []
    ) -> PreviewPhoto {
        PreviewPhoto(
            uuid: uuid,
            title: title,
            date: "2025-04-13T20:26:02",
            existingKeywords: ["Venecia"],
            proposedKeywords: proposedKeywords,
            confidence: confidence,
            modelUsed: modelUsed,
            modelReason: modelReason,
            state: state,
            applyState: applyState,
            rollbackState: rollbackState,
            proposedCaption: proposedCaption,
            captionState: captionState,
            errors: errors
        )
    }
}
