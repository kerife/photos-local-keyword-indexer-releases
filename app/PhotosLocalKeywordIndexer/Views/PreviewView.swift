import SwiftUI

struct PreviewView: View {
    @EnvironmentObject private var model: AppModel
    @State private var filter: ReviewPhotoFilter = .all
    let onOpenHistory: () -> Void
    let onStartScan: () -> Void

    init(
        onOpenHistory: @escaping () -> Void = {},
        onStartScan: @escaping () -> Void = {}
    ) {
        self.onOpenHistory = onOpenHistory
        self.onStartScan = onStartScan
    }

    private var filteredPhotos: [PreviewPhoto] {
        guard let preview = model.preview else { return [] }
        return preview.photos.filter { filter.includes($0) }
    }

    var body: some View {
        ResponsivePage { width in
            VStack(alignment: .leading, spacing: 12) {
                if let preview = model.preview {
                    Text(ReviewScreenCopy.title(for: preview)).font(.title2)
                    let recovery = ReviewRecoverySummary(
                        preview: preview,
                        mutationRequiresManualReview: model.mutationRequiresManualReview,
                        consultationOnly: preview.reviewedFromRunID != nil && !model.reviewSourceAvailable
                    )
                    let approval = ReviewApprovalSummary(
                        photoCount: model.review.approvedPhotoCount,
                        keywordCount: model.review.selectedKeywordCount,
                        captionCount: model.review.selectedCaptionCount,
                        hasReviewedManifest: model.safetyGate.reviewedManifest != nil,
                        hasWarnings: preview.scanStatus == "ready_with_errors",
                        hasBlockedRows: preview.photos.contains { !$0.isReviewSelectable },
                        isRetryingFailedApply: model.isRetryingFailedApply
                    )
                    let historicalScope = ReviewHistoricalScope(preview: preview)
                    let historicalOutcome = ReviewMutationOutcome(preview: preview)
                    let confidence = ReviewConfidenceSummary(
                        photos: preview.photos,
                        approvedPhotoIDs: model.review.approvedPhotoIDs
                    )
                    ScrollView {
                        LazyVStack(alignment: .leading, spacing: 12) {
                            GroupBox("Resumen aprobado") {
                                if historicalScope.isHistorical, let historicalText = historicalScope.text {
                                    VStack(alignment: .leading, spacing: 4) {
                                        Label(historicalText, systemImage: "checklist")
                                        Text("Alcance histórico; la selección de apply está cerrada.")
                                            .font(.caption)
                                            .foregroundStyle(.secondary)
                                        if let outcomeText = historicalOutcome.text {
                                            Label("Resultado efectivo", systemImage: "checkmark.circle")
                                                .font(.callout.weight(.medium))
                                            Text(outcomeText)
                                                .font(.callout)
                                                .foregroundStyle(.secondary)
                                        }
                                        Text(reviewNextAction(for: preview, fallback: approval.nextSafeAction))
                                            .font(.callout)
                                            .foregroundStyle(.secondary)
                                    }
                                    .accessibilityElement(children: .combine)
                                    .accessibilityLabel(
                                        historicalOutcome.text.map {
                                            "\(historicalText) Alcance histórico; la selección de apply está cerrada. Resultado efectivo: \($0)"
                                        } ?? "\(historicalText) Alcance histórico; la selección de apply está cerrada."
                                    )
                                    .padding(.vertical, 4)
                                } else {
                                    VStack(alignment: .leading, spacing: 8) {
                            let summaryLayout = width < 620
                                            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 6))
                                            : AnyLayout(HStackLayout(alignment: .top, spacing: 12))
                                        summaryLayout {
                                            Label(approval.photoText, systemImage: "photo.on.rectangle")
                                            Label(approval.keywordText, systemImage: "tag")
                                            Label(approval.captionText, systemImage: "text.quote")
                                        }
                                        Text(reviewNextAction(for: preview, fallback: approval.nextSafeAction))
                                            .foregroundStyle(.secondary)
                                    }
                                    .padding(.vertical, 4)
                                }
                            }
                            if confidence.isVisible {
                                Label(confidence.text, systemImage: "exclamationmark.triangle")
                                    .font(.callout)
                                    .foregroundStyle(.orange)
                                    .accessibilityLabel(confidence.accessibilityLabel)
                            }
                            let blocked = ReviewBlockedSummary(photos: preview.photos)
                            if blocked.isVisible {
                                Label(blocked.text, systemImage: "lock.shield")
                                    .font(.callout)
                                    .foregroundStyle(.orange)
                                    .accessibilityLabel(blocked.accessibilityLabel)
                            }
                            DisclosureGroup("Contexto del análisis") {
                                VStack(alignment: .leading, spacing: 8) {
                                    Text("Run \(String(preview.runID.prefix(8))) · \(preview.scanStatusLabel)")
                                        .foregroundStyle(.secondary)
                                        .accessibilityLabel(preview.scanStatusAccessibilityLabel)
                                    let scanOutcome = ReviewScanOutcomeSummary(photos: preview.photos)
                                    if preview.reviewedFromRunID == nil, scanOutcome.isVisible {
                                        Label(scanOutcome.text, systemImage: "photo.stack")
                                            .font(.callout)
                                            .foregroundStyle(.secondary)
                                            .accessibilityLabel(scanOutcome.accessibilityLabel)
                                    }
                                    Label(preview.selectionStrategyText, systemImage: preview.selectionStrategySystemImage)
                                        .font(.callout)
                                        .foregroundStyle(.secondary)
                                        .accessibilityLabel(preview.selectionStrategyText)
                                    if let photosAccessText = preview.photosAccessText {
                                        Label(photosAccessText, systemImage: preview.photosAccessSystemImage)
                                            .font(.callout)
                                            .foregroundStyle(preview.photosAccess == "limited" ? .orange : .secondary)
                                            .accessibilityLabel(preview.photosAccessAccessibilityLabel ?? photosAccessText)
                                    }
                                    if let captionRequestText = preview.captionRequestText {
                                        Label(captionRequestText, systemImage: "text.quote")
                                            .font(.callout)
                                            .foregroundStyle(.secondary)
                                            .accessibilityLabel(captionRequestText)
                                    }
                                }
                                .frame(maxWidth: .infinity, alignment: .leading)
                                .padding(.top, 6)
                            }
                            if preview.reviewedFromRunID != nil {
                                let reviewedCanApply = model.canContinueReviewedApply
                    let reviewedMessage = model.reviewedManifestStatusMessage
                                    ?? "Manifiesto revisado en solo consulta; ejecuta un dry-run nuevo antes de aplicar."
                                Label(
                                    reviewedMessage,
                                    systemImage: reviewedCanApply ? "checkmark.shield" : "lock.shield"
                                )
                                .font(.callout)
                                .foregroundStyle(reviewedCanApply ? Color.secondary : Color.orange)
                                .accessibilityLabel(
                                    reviewedMessage
                                )
                            } else if let readOnlyMessage = preview.reviewReadOnlyMessage {
                                Label(readOnlyMessage, systemImage: "lock.shield")
                                    .font(.callout)
                                    .foregroundStyle(.orange)
                                    .accessibilityLabel(readOnlyMessage)
                            }
                            if model.mutationRequiresManualReview {
                                Label(
                                    "Solo lectura: revisa el run en Historial antes de intentar otra mutación.",
                                    systemImage: "exclamationmark.triangle"
                                )
                                .font(.callout)
                                .foregroundStyle(.orange)
                            }
                            if recovery.isVisible {
                                GroupBox {
                                    HStack(alignment: .top, spacing: 10) {
                                        Image(systemName: recovery.symbolName)
                                            .foregroundStyle(.orange)
                                            .font(.title3)
                                            .accessibilityHidden(true)
                                        VStack(alignment: .leading, spacing: 5) {
                                            Text(recovery.title)
                                                .font(.headline)
                                            Text(recovery.detail)
                                                .font(.callout)
                                                .foregroundStyle(.secondary)
                                let recoveryLayout = width < 620
                                                ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
                                                : AnyLayout(HStackLayout(alignment: .top, spacing: 8))
                                            recoveryLayout {
                                                if recovery.canStartNewDryRun {
                                                    let canOpenNewRun = model.preparation.isReady && !model.preflightIsStale
                                                    Button(
                                                        canOpenNewRun ? "Revisión" : "Abrir Preparación",
                                                        action: onStartScan
                                                    )
                                                    .buttonStyle(.borderedProminent)
                                                    .accessibilityHint(
                                                        canOpenNewRun
                                                            ? recovery.newDryRunAccessibilityHint
                                                            : "Abre Preparación para resolver los componentes locales antes de iniciar otro dry-run; no modifica Fotos."
                                                    )
                                                }
                                                Button(recovery.actionTitle, action: onOpenHistory)
                                                    .buttonStyle(.bordered)
                                                    .accessibilityHint("Abre el historial local sin consultar ni modificar Fotos.")
                                            }
                                        }
                                        Spacer(minLength: 0)
                                    }
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                } label: {
                                    Label("Siguiente paso seguro", systemImage: "arrow.forward.circle")
                                }
                                .accessibilityElement(children: .contain)
                                .accessibilityLabel(recovery.accessibilityLabel)
                            }
                            if ReviewBulkKeywordCopy.shouldShowToolbar(
                                isFreshReview: preview.canPrepareReview,
                                canSelectAllKeywords: model.review.canSelectAllKeywords
                            ) {
                    let toolbarLayout = width < 620
                                    ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
                                    : AnyLayout(HStackLayout(alignment: .top, spacing: 10))
                                toolbarLayout {
                                    Label(
                                        approval.actionScopeText,
                                        systemImage: "checklist"
                                    )
                                    .font(.callout)
                                    .foregroundStyle(.secondary)
                                    .frame(maxWidth: .infinity, alignment: .leading)
                                    Button(
                                        ReviewBulkKeywordCopy.buttonTitle(
                                            allKeywordsSelected: model.review.allKeywordsSelected
                                        )
                                    ) {
                                        model.setAllKeywords(selected: !model.review.allKeywordsSelected)
                                    }
                                    .accessibilityHint(ReviewBulkKeywordCopy.accessibilityHint)
                                    .disabled(model.worker.state.isRunning || !preview.canPrepareReview || model.mutationRequiresManualReview)
                                }
                                Text("Las captions se aprueban por separado dentro de cada foto.")
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                            }
                            if ReviewBulkKeywordCopy.shouldShowCaptionDisclosure(
                                isFreshReview: preview.canPrepareReview,
                                hasCaptionProposals: model.review.hasCaptionProposals,
                                canSelectAllKeywords: model.review.canSelectAllKeywords
                            ) {
                                Label(
                                    "Las captions se aprueban por separado dentro de cada foto.",
                                    systemImage: "text.quote"
                                )
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .accessibilityLabel("Las captions se aprueban por separado dentro de cada foto.")
                            }
                            reviewFilters(for: preview, width: width)
                            LazyVStack(alignment: .leading, spacing: 12, pinnedViews: []) {
                                if filteredPhotos.isEmpty {
                                    ContentUnavailableView(
                                        filter.emptyMessage(for: preview.photos.count),
                                        systemImage: filter.systemImage,
                                        description: Text(filter.emptyDescription(for: preview.photos.count))
                                    )
                                    .listRowSeparator(.hidden)
                                } else if filter == .all {
                                    ForEach(ReviewPhotoGroups(photos: filteredPhotos).sections) { section in
                                        Section {
                                            ForEach(section.photos) { photo in
                                                photoRow(photo, width: width)
                                                Divider()
                                            }
                                        } header: {
                                            Label(
                                                "\(section.group.title) (\(section.photos.count))",
                                                systemImage: section.group.systemImage
                                            )
                                            .accessibilityLabel("\(section.group.title): \(section.photos.count) fotos")
                                        }
                                    }
                                } else {
                                    ForEach(filteredPhotos) { photo in
                                        photoRow(photo, width: width)
                                        Divider()
                                    }
                                }
                            }
                            Text(model.message)
                                .font(.callout)
                                .foregroundStyle(.secondary)
                        }
                        .frame(maxWidth: .infinity, alignment: .leading)
                    }
                    Divider()
                let footerLayout = width < 760
                        ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
                        : AnyLayout(HStackLayout(alignment: .center, spacing: 12))
                    footerLayout {
                        Text("Siguiente paso seguro: \(reviewNextAction(for: preview, fallback: approval.nextSafeAction)).")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        if model.worker.state.isRunning {
                            let cancellationRequested = model.worker.state.isCancellationRequested
                            Button(
                                ReviewMutationCancellationCopy.buttonTitle(
                                    isCancellationRequested: cancellationRequested
                                )
                            ) { model.cancel() }
                            .buttonStyle(.bordered)
                            .disabled(
                                !ReviewMutationCancellationCopy.canRequestCancellation(
                                    isCancellationRequested: cancellationRequested
                                )
                            )
                            .accessibilityHint(
                                ReviewMutationCancellationCopy.accessibilityHint(
                                    isCancellationRequested: cancellationRequested
                                )
                            )
                        } else {
                            let reviewed = preview.reviewedFromRunID != nil
                            let mutationBlockMessage = PhotosMutationAccess.blockingMessage(
                                for: model.preparation,
                                localReviewOnly: preview.canPrepareReview
                            )
                        let applyActionAvailable = ReviewApplyAvailability.canEnable(
                                    preview: preview,
                                    reviewedApplyAllowed: model.canContinueReviewedApply
                                ) && !model.mutationRequiresManualReview
                                && mutationBlockMessage == nil
                            let actionTitle = ReviewApplyButtonCopy.title(
                                reviewed: reviewed,
                                available: applyActionAvailable,
                                hasWarnings: preview.scanStatus == "ready_with_errors",
                                hasBlockedRows: preview.photos.contains { !$0.isReviewSelectable },
                                isRetryingFailedApply: model.isRetryingFailedApply
                            )
                        let actionRole: ButtonRole? = ReviewApplyButtonCopy.requiresDestructiveTreatment(
                                    reviewed: reviewed,
                                    available: applyActionAvailable
                                ) ? .destructive : nil
                        Button(role: actionRole) { model.requestApply() } label: {
                                Text(actionTitle)
                                    .fixedSize(horizontal: false, vertical: true)
                                    .multilineTextAlignment(.leading)
                            }
                            .buttonStyle(.borderedProminent)
                            .accessibilityLabel(
                                ReviewApplyButtonCopy.accessibilityLabel(
                                    reviewed: reviewed,
                                    available: applyActionAvailable,
                                    summary: approval.confirmationSummaryText,
                                    hasWarnings: preview.scanStatus == "ready_with_errors",
                                    hasBlockedRows: preview.photos.contains { !$0.isReviewSelectable },
                                    isRetryingFailedApply: model.isRetryingFailedApply,
                                    requiresManualReview: model.mutationRequiresManualReview
                                )
                            )
                            .accessibilityHint(
                                mutationBlockMessage
                                    ?? ReviewApplyButtonCopy.accessibilityHint(
                                        reviewed: reviewed,
                                        available: applyActionAvailable,
                                        requiresManualReview: model.mutationRequiresManualReview,
                                        hasSelectedChanges: model.review.selectedChangeCount > 0,
                                        hasWarnings: preview.scanStatus == "ready_with_errors",
                                        hasBlockedRows: preview.photos.contains { !$0.isReviewSelectable },
                                        sourceAvailable: model.reviewSourceAvailable,
                                        isRetryingFailedApply: model.isRetryingFailedApply
                                    )
                            )
                            .disabled(
                                !ReviewApplyAvailability.hasActionableSelection(
                                    selectedChangeCount: model.review.selectedChangeCount,
                                    isRetryingFailedApply: model.isRetryingFailedApply
                                )
                                    || !ReviewApplyAvailability.canEnable(
                                        preview: preview,
                                        reviewedApplyAllowed: model.canContinueReviewedApply
                                    )
                                    || model.mutationRequiresManualReview
                                    || mutationBlockMessage != nil
                            )
                        }
                    }
                } else {
                    VStack(spacing: 12) {
                        ContentUnavailableView(
                            ReviewEmptyStateCopy.title,
                            systemImage: "doc.text.magnifyingglass",
                            description: Text(ReviewEmptyStateCopy.detail)
                        )
                        Button(
                            ReviewEmptyStateActionCopy.title(
                                preparationReady: model.preparation.isReady,
                                preflightIsStale: model.preflightIsStale
                            ),
                            action: onStartScan
                        )
                        .buttonStyle(.borderedProminent)
                        .accessibilityHint(
                            ReviewEmptyStateActionCopy.hint(
                                preparationReady: model.preparation.isReady,
                                preflightIsStale: model.preflightIsStale
                            )
                        )
                    }
                    .accessibilityElement(children: .contain)
                    .accessibilityLabel(ReviewEmptyStateCopy.accessibilityLabel)
                }
            }
        }
        .sheet(
            isPresented: $model.showApplyConfirmation,
            onDismiss: { model.safetyGate.cancelConfirmation() }
        ) {
            ApplyConfirmationSheet(
                detail: confirmationDetail,
                onCancel: {
                    model.safetyGate.cancelConfirmation()
                    model.showApplyConfirmation = false
                },
                onConfirm: { model.applyReviewedManifest() }
            )
        }
        .onChange(of: model.preview?.runID) { _, _ in
            filter = .all
        }
        .navigationTitle(AppRoute.preview.navigationTitle)
    }

    private var confirmationDetail: ReviewConfirmationDetail {
        ReviewConfirmationDetail(
            selection: model.review,
            photos: model.preview?.photos ?? [],
            isRetryingFailedApply: model.isRetryingFailedApply
        )
    }

    private func reviewNextAction(for preview: RunManifestPreview, fallback: String) -> String {
        guard preview.reviewedFromRunID != nil else {
            return ReviewFreshNextActionCopy.text(for: preview, fallback: fallback)
        }
        return ReviewNextActionCopy.text(
            sourceAvailable: model.reviewSourceAvailable,
            canContinue: model.canContinueReviewedApply,
            hasSelectedChanges: model.review.selectedChangeCount > 0,
            hasWarnings: preview.scanStatus == "ready_with_errors",
            hasBlockedRows: preview.photos.contains { !$0.isReviewSelectable },
            isRetryingFailedApply: model.isRetryingFailedApply
        )
    }

    private func reviewFilters(for preview: RunManifestPreview, width: CGFloat) -> some View {
        let counts = ReviewFilterCounts(photos: preview.photos)
        let scope = ReviewScopeSummary(
            filter: filter,
            visiblePhotoCount: filteredPhotos.count,
            totalPhotoCount: preview.photos.count,
            approvedPhotoCount: model.review.approvedPhotoCount,
            approvedKeywordCount: model.review.selectedKeywordCount,
            approvedCaptionCount: model.review.selectedCaptionCount
        )
        return VStack(alignment: .leading, spacing: 6) {
            ViewThatFits(in: .horizontal) {
                Picker("Filtrar fotos", selection: $filter) {
                    ForEach(ReviewPhotoFilter.allCases) { option in
                        Label("\(option.title) (\(counts.count(for: option)))", systemImage: option.systemImage)
                            .tag(option)
                    }
                }
                .pickerStyle(.segmented)
                .fixedSize(horizontal: true, vertical: false)
                .accessibilityLabel("Filtrar fotos de la revisión")
                .accessibilityValue(scope.visibleScopeText)
                .accessibilityHint("Muestra todas las fotos, los cambios, los captions o los elementos que requieren atención.")
                Picker("Filtrar fotos", selection: $filter) {
                    ForEach(ReviewPhotoFilter.allCases) { option in
                        Label("\(option.title) (\(counts.count(for: option)))", systemImage: option.systemImage)
                            .tag(option)
                    }
                }
                .pickerStyle(.menu)
                .accessibilityLabel("Filtrar fotos de la revisión")
                .accessibilityValue(scope.visibleScopeText)
                .accessibilityHint("Muestra todas las fotos, los cambios, los captions o los elementos que requieren atención.")
            }
            VStack(alignment: .leading, spacing: 3) {
                let scopeLayout = width < 620
                    ? AnyLayout(VStackLayout(alignment: .leading, spacing: 6))
                    : AnyLayout(HStackLayout(alignment: .top, spacing: 12))
                scopeLayout {
                    Label(scope.visibleScopeText, systemImage: filter.systemImage)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Text(scope.approvedScopeText)
                }
                .font(.callout)
                Text("El filtro solo cambia lo visible; no cambia la selección aprobada.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(scope.accessibilityLabel)
        }
    }

    @ViewBuilder
    private func photoRow(_ photo: PreviewPhoto, width: CGFloat) -> some View {
        let selectionIsFixed = model.preview?.reviewedFromRunID != nil
        VStack(alignment: .leading, spacing: 8) {
            let headerLayout = width < 640
                ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
                : AnyLayout(HStackLayout(alignment: .top, spacing: 12))
            headerLayout {
                PhotoThumbnailView(
                    photosLocalIdentifier: photo.photosLocalIdentifier,
                    displayTitle: ReviewPhotoRowCopy.displayTitle(photo.title)
                )
                if photo.canSelectPhoto {
                    Toggle(isOn: Binding(
                            get: { model.review.isPhotoSelected(photo) },
                            set: { model.setPhoto(photo, selected: $0) }
                    )) {
                        VStack(alignment: .leading) {
                            Text(ReviewPhotoRowCopy.displayTitle(photo.title))
                                .font(.headline)
                                .lineLimit(1)
                                .truncationMode(.tail)
                                .accessibilityHidden(true)
                            Text(ReviewPhotoRowCopy.displayDate(photo.date))
                                .font(.caption)
                                .foregroundStyle(.secondary)
                                .accessibilityHidden(true)
                        }
                    }
                    .accessibilityLabel(
                        photo.photoSelectionAccessibilityLabel(selectionIsFixed: selectionIsFixed)
                    )
                    .accessibilityHint(
                        selectionIsFixed
                            ? photo.reviewControlAccessibilityHint(selectionIsFixed: true)
                            : photo.accessibilityToggleHint
                    )
                    .disabled(model.worker.state.isRunning || model.preview?.canPrepareReview != true || model.mutationRequiresManualReview)
                } else {
                    VStack(alignment: .leading) {
                        Text(ReviewPhotoRowCopy.displayTitle(photo.title))
                            .font(.headline)
                            .lineLimit(1)
                            .truncationMode(.tail)
                            .accessibilityHidden(true)
                        Text(ReviewPhotoRowCopy.displayDate(photo.date))
                            .font(.caption)
                            .foregroundStyle(.secondary)
                            .accessibilityHidden(true)
                    }
                }
                VStack(alignment: width < 640 ? .leading : .trailing) {
                    Text(photo.reviewStatusText)
                        .font(.caption)
                        .accessibilityHidden(true)
                    Text(photo.modelDisplayText)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                        .accessibilityHidden(true)
                    if photo.shouldDisplayConfidence {
                        Label(photo.confidenceText, systemImage: photo.confidenceBand.systemImage)
                            .font(.caption)
                            .foregroundStyle(confidenceColor(photo.confidenceBand))
                            .accessibilityLabel(photo.confidenceAccessibilityLabel)
                            .accessibilityHidden(true)
                    }
                }
                .frame(maxWidth: .infinity, alignment: width < 640 ? .leading : .trailing)
            }
            Text("UUID \(String(photo.uuid.prefix(8)))")
                .font(.caption2)
                .foregroundStyle(.tertiary)
                .accessibilityHidden(true)
            Text(photo.reviewEvidenceText)
                .font(.caption)
                .foregroundStyle(.secondary)
            if let locationContextReviewText = photo.locationContextReviewText {
                Label(locationContextReviewText, systemImage: "location.circle")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .accessibilityLabel(locationContextReviewText)
            }
            Label(
                photo.approvalScopeText,
                systemImage: photo.isReviewSelectable && photo.hasReviewableChanges
                    ? "checklist"
                    : "info.circle"
            )
            .font(.caption)
            .foregroundStyle(.secondary)
            .accessibilityLabel(photo.approvalScopeText)
            if let effectiveChangeText = photo.reviewEffectiveChangeText {
                Label(effectiveChangeText, systemImage: "arrow.triangle.2.circlepath")
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .accessibilityLabel(effectiveChangeText)
            }
            if !photo.errors.isEmpty {
                Label(photo.reviewReasonText, systemImage: "exclamationmark.triangle.fill")
                    .font(.caption)
                    .foregroundStyle(.orange)
                    .accessibilityLabel(photo.reviewReasonText)
            } else {
                Text(photo.reviewReasonText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            if let caption = photo.reviewCaptionText {
                VStack(alignment: .leading, spacing: 4) {
                    Label(CaptionReviewCopy.title, systemImage: "text.quote")
                        .font(.caption.weight(.medium))
                    Text(CaptionReviewCopy.detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Toggle("Caption propuesto: \(caption)", isOn: Binding(
                            get: { model.review.isCaptionSelected(photo) },
                            set: { model.setCaption(photo, selected: $0) }
                    ))
                    .toggleStyle(.checkbox)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityLabel(
                        photo.captionControlAccessibilityLabel(selectionIsFixed: selectionIsFixed)
                    )
                    .accessibilityHint(
                        selectionIsFixed
                            ? photo.reviewControlAccessibilityHint(selectionIsFixed: true)
                            : photo.captionAccessibilityHint
                    )
                }
                .disabled(model.worker.state.isRunning || !photo.isReviewSelectable || model.preview?.canPrepareReview != true || model.mutationRequiresManualReview)
            }
            if let keywordProposalSectionLabel = photo.keywordProposalSectionLabel {
                Text(keywordProposalSectionLabel)
                    .font(.caption).foregroundStyle(.secondary)
                ForEach(photo.proposedKeywords, id: \.self) { keyword in
                    Toggle(keyword, isOn: Binding(
                            get: { model.review.isKeywordSelected(keyword, for: photo) },
                            set: { model.setKeyword(keyword, photo: photo, selected: $0) }
                    ))
                    .toggleStyle(.checkbox)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityLabel(
                        photo.keywordAccessibilityLabel(
                            for: keyword,
                            selectionIsFixed: selectionIsFixed
                        )
                    )
                    .accessibilityHint(
                        selectionIsFixed
                            ? photo.reviewControlAccessibilityHint(selectionIsFixed: true)
                            : photo.keywordAccessibilityHint
                    )
                    .disabled(model.worker.state.isRunning || !photo.isReviewSelectable || model.preview?.canPrepareReview != true || model.mutationRequiresManualReview)
                }
            }
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(photo.accessibilityLabel)
    }

    private func confidenceColor(_ band: ReviewConfidenceBand) -> Color {
        switch band {
        case .high: return .green
        case .medium: return .orange
        case .low: return .red
        case .unavailable: return .secondary
        }
    }
}

/// Bounds untrusted photo titles before they enter the dense review table.
/// Keep this presentation-only projection aligned with the bounded VoiceOver
/// title without changing the title persisted in the manifest.
enum ReviewPhotoRowCopy {
    static func displayTitle(_ value: String) -> String {
        let compact = value
            .split(whereSeparator: \.isWhitespace)
            .joined(separator: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !compact.isEmpty else { return "Foto sin título" }
        return String(compact.prefix(160))
    }

    static func displayDate(_ value: String) -> String {
        let compact = value
            .split(whereSeparator: \.isWhitespace)
            .joined(separator: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !compact.isEmpty else { return "Fecha no disponible" }
        return String(compact.prefix(64))
    }
}

enum ReviewEmptyStateActionCopy {
    static func title(preparationReady: Bool, preflightIsStale: Bool) -> String {
        HistoryEmptyStateCopy.actionTitle(
            preparationReady: preparationReady,
            preflightIsStale: preflightIsStale
        )
    }

    static func hint(preparationReady: Bool, preflightIsStale: Bool) -> String {
        HistoryEmptyStateCopy.actionAccessibilityHint(
            preparationReady: preparationReady,
            preflightIsStale: preflightIsStale
        )
    }
}
