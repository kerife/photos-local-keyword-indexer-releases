import SwiftUI
import UniformTypeIdentifiers

struct RunDetailView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.openURL) private var openURL
    @State private var historyRows: [HistoryDisplayRow] = []
    @State private var rejectedRunNotice: String?
    @State private var artifactToOpen: URL?
    @State private var rollbackCandidate: HistoryRunSummary?
    @State private var isShowingImporter = false
    @State private var importMessage: String?
    private let runsRoot: URL?
    let onReview: (URL) -> Void
    let onStartScan: () -> Void

    init(
        runsRoot: URL? = HistoryRunStore.applicationSupportRunsRoot(),
        onReview: @escaping (URL) -> Void = { _ in },
        onStartScan: @escaping () -> Void = {}
    ) {
        self.runsRoot = runsRoot
        self.onReview = onReview
        self.onStartScan = onStartScan
    }

    var body: some View {
        ResponsivePage { width in
            VStack(alignment: .leading, spacing: 12) {
                ViewThatFits(in: .horizontal) {
                    HStack {
                        Text("Actividad local").font(.title2)
                        Spacer()
                        historyActions
                    }
                    .fixedSize(horizontal: true, vertical: false)
                    VStack(alignment: .leading, spacing: 8) {
                        Text("Actividad local").font(.title2)
                        historyActions
                    }
                }
                if model.worker.state.isRunning {
                    Label(HistoryActionAvailability.activeOperationNotice, systemImage: "lock.fill")
                        .font(.callout)
                        .foregroundStyle(.orange)
                        .accessibilityLabel(HistoryActionAvailability.activeOperationNotice)
                }
                if historyRows.isEmpty {
                    VStack(spacing: 12) {
                        ContentUnavailableView(
                            HistoryEmptyStateCopy.title,
                            systemImage: "clock",
                            description: Text(HistoryEmptyStateCopy.detail)
                        )
                        Button(
                            HistoryEmptyStateCopy.actionTitle(
                                preparationReady: model.preparation.isReady,
                                preflightIsStale: model.preflightIsStale
                            ),
                            action: onStartScan
                        )
                        .buttonStyle(.borderedProminent)
                        .accessibilityHint(
                            HistoryEmptyStateCopy.actionAccessibilityHint(
                                preparationReady: model.preparation.isReady,
                                preflightIsStale: model.preflightIsStale
                            )
                        )
                    }
                    .frame(maxWidth: .infinity, maxHeight: .infinity)
                    .accessibilityElement(children: .contain)
                    .accessibilityLabel(HistoryEmptyStateCopy.accessibilityLabel)
                } else {
                    List {
                        ForEach(historyRows) { row in
                            switch row {
                            case .session(let session):
                                HistorySessionGroupCard(session: session, width: width)
                                    .listRowSeparator(.hidden)
                                    .listRowInsets(EdgeInsets(top: 12, leading: 8, bottom: 2, trailing: 8))
                            case .run(let run):
                                runCard(run, width: width)
                            }
                        }
                    }
                }

                if let rejectedRunNotice {
                    Label(rejectedRunNotice, systemImage: "exclamationmark.triangle")
                        .font(.callout)
                        .foregroundStyle(.orange)
                        .accessibilityLabel(rejectedRunNotice)
                        .padding(.horizontal)
                }

                if !model.events.isEmpty {
                    DisclosureGroup("Eventos de la sesión (\(model.events.count))") {
                        ScrollView {
                            LazyVStack(alignment: .leading, spacing: 8) {
                                ForEach(model.events, id: \.id) { event in
                                    VStack(alignment: .leading, spacing: 3) {
                                        Text(event.event.humanLabel)
                                        Text(event.humanDetailText ?? "")
                                            .foregroundStyle(.secondary)
                                    }
                                    .accessibilityElement(children: .combine)
                                    .accessibilityLabel(event.humanAccessibilityLabel)
                                }
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .frame(maxHeight: 140)
                    }
                }
            }
        }
        .onAppear(perform: reloadRuns)
        .fileImporter(
            isPresented: $isShowingImporter,
            allowedContentTypes: [.json],
            allowsMultipleSelection: false,
            onCompletion: handleImport
        )
        .onChange(of: model.events.last?.id) { _, _ in
            guard let event = model.events.last,
                  event.event == .completed || event.event == .error else { return }
            // Refresh only after a terminal event. Reading a manifest while a
            // mutation is still being persisted could briefly hide a valid run.
            reloadRuns()
        }
        .onChange(of: model.worker.state) { _, state in
            guard state.shouldRefreshHistory else { return }
            // An interrupted helper may persist a partial manifest without
            // emitting a terminal event; surface it so its next safe action
            // is available in Historial without requiring manual refresh.
            reloadRuns()
        }
        .navigationTitle(AppRoute.history.navigationTitle)
        .alert("Abrir artefacto local revisable", isPresented: artifactAlertBinding) {
            Button("Cancelar", role: .cancel) { artifactToOpen = nil }
            Button("Abrir") {
                if let artifactToOpen {
                    let runDirectory = artifactToOpen.deletingLastPathComponent()
                    if HistoryArtifactAvailability.inspect(path: artifactToOpen, runDirectory: runDirectory).isOpenable {
                        openURL(artifactToOpen)
                    } else {
                        importMessage = "El artefacto local cambió y no se abrirá. Actualiza Historial y conserva el run para auditoría."
                    }
                }
                artifactToOpen = nil
            }
        } message: {
            Text("No edites manifest.json ni preview.csv durante apply o rollback. Abrirlos no consulta Fotos ni Ollama.")
        }
        .alert("Importación local", isPresented: importAlertBinding) {
            Button("Aceptar", role: .cancel) { importMessage = nil }
        } message: {
            Text(importMessage ?? "")
        }
        .sheet(item: $rollbackCandidate) { run in
            RollbackConfirmationSheet(
                detail: RollbackConfirmationDetail(preview: run.preview),
                onCancel: { rollbackCandidate = nil },
                onConfirm: {
                    model.rollback(manifest: run.manifestURL)
                    rollbackCandidate = nil
                }
            )
        }
    }

    private var historyActions: some View {
        HStack(spacing: 8) {
            Button("Importar manifest", systemImage: "square.and.arrow.down") {
                isShowingImporter = true
            }
            .disabled(
                !HistoryActionAvailability.canImportManifest(
                    workerIsRunning: model.worker.state.isRunning
                )
            )
            .accessibilityHint(
                HistoryActionAvailability.manifestImportAccessibilityHint(
                    workerIsRunning: model.worker.state.isRunning
                )
            )
            Button("Actualizar", systemImage: "arrow.clockwise", action: reloadRuns)
        }
    }

    @ViewBuilder
    private func runCard(_ run: HistoryRunSummary, width: CGFloat) -> some View {
        let manifest = run.manifestURL
        let preview = manifest.deletingLastPathComponent().appendingPathComponent("preview.csv")
        GroupBox {
            VStack(alignment: .leading, spacing: 6) {
                        let headerLayout = width < 620
                    ? AnyLayout(VStackLayout(alignment: .leading, spacing: 6))
                    : AnyLayout(HStackLayout(alignment: .top, spacing: 12))
                headerLayout {
                    VStack(alignment: .leading) {
                        Text(HistoryRunRowCopy.displayTitle(run.displayTitle)).font(.headline)
                        Text(HistoryRunRowCopy.displayDate(run.displayDate))
                            .foregroundStyle(.secondary)
                    }
                    .frame(maxWidth: .infinity, alignment: .leading)
                    Label(run.statusLabel, systemImage: run.status.systemImageName)
                        .foregroundStyle(run.status == .needsAttention ? .orange : .secondary)
                        .symbolRenderingMode(.hierarchical)
                    if run.isAutonomous {
                        Text("Guardado autónomo")
                            .font(.caption)
                            .accessibilityLabel("Guardado autónomo con autorización del recorrido verificada; sin revisión individual")
                    }
                }
                Text(run.summaryText)
                if let captionRequestText = run.preview.captionRequestText {
                    Label(captionRequestText, systemImage: "text.quote")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .accessibilityLabel(captionRequestText)
                }
                if let captionOutcomeText = run.captionOutcomeText {
                    Label(captionOutcomeText, systemImage: "checkmark.bubble")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .accessibilityLabel(captionOutcomeText)
                }
                Label(run.preview.selectionStrategyText, systemImage: run.preview.selectionStrategySystemImage)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .accessibilityLabel(run.preview.selectionStrategyText)
                if let photosAccessText = run.preview.photosAccessText {
                    Label(photosAccessText, systemImage: run.preview.photosAccessSystemImage)
                        .font(.callout)
                        .foregroundStyle(run.preview.photosAccess == "limited" ? .orange : .secondary)
                        .accessibilityLabel(run.preview.photosAccessAccessibilityLabel ?? photosAccessText)
                }
                if let blockedScopeText = run.blockedScopeText {
                    Label(blockedScopeText, systemImage: "lock.shield")
                        .font(.callout)
                        .foregroundStyle(.orange)
                        .accessibilityLabel(blockedScopeText)
                }
                if let approvedScopeText = run.approvedScopeText {
                    Label(approvedScopeText, systemImage: "checklist")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .accessibilityLabel(approvedScopeText)
                }
                if let mutationScopeText = run.mutationScopeText {
                    Text(mutationScopeText)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .accessibilityLabel("Alcance persistido: \(mutationScopeText)")
                }
                if run.status == .needsAttention {
                    GroupBox {
                        Text("Siguiente paso seguro: \(run.nextSafeActionText).")
                            .font(.callout)
                            .foregroundStyle(.secondary)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    } label: {
                        Label(
                            run.attentionBannerText,
                            systemImage: run.preview.canPrepareReview || run.canContinueReviewedApply
                                ? "checklist"
                                : "lock.shield"
                        )
                        .foregroundStyle(.orange)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel(
                        "\(run.attentionBannerText). Siguiente paso seguro: \(run.nextSafeActionText)."
                    )
                } else {
                    Label(
                        "Siguiente acción segura: \(run.nextSafeActionText)",
                        systemImage: "arrow.forward.circle"
                    )
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .accessibilityLabel("Siguiente acción segura: \(run.nextSafeActionText)")
                }
                Text("\(run.confidenceText) · \(run.modelText)")
                    .font(.caption).foregroundStyle(.secondary)
                if let lowConfidenceText = run.lowConfidenceText {
                    Label(lowConfidenceText, systemImage: "exclamationmark.triangle.fill")
                        .font(.caption)
                        .foregroundStyle(.orange)
                        .accessibilityLabel("Aviso de confianza: \(lowConfidenceText)")
                }
                Text("UUID \(run.abbreviatedUUID) · run \(String(run.runID.prefix(8)))")
                    .font(.caption2).foregroundStyle(.tertiary)
                if let errorSummary = run.errorSummaryText {
                    Text(errorSummary)
                        .font(.caption).foregroundStyle(.orange)
                        .accessibilityLabel("Aviso: \(errorSummary)")
                }
                let runDirectory = manifest.deletingLastPathComponent()
                let manifestAvailability = HistoryArtifactAvailability.inspect(
                    path: manifest,
                    runDirectory: runDirectory
                )
                let csvAvailability = HistoryArtifactAvailability.inspect(
                    path: preview,
                    runDirectory: runDirectory
                )
                        let actionLayout = width < 620
                    ? AnyLayout(VStackLayout(alignment: .leading, spacing: 8))
                    : AnyLayout(HStackLayout(alignment: .top, spacing: 10))
                actionLayout {
                    let reviewed = run.preview.reviewedFromRunID != nil
                    let applyMutationBlockMessage = PhotosMutationAccess.blockingMessage(
                        for: model.preparation,
                        operation: .apply
                    )
                    let rollbackMutationBlockMessage = PhotosMutationAccess.blockingMessage(
                        for: model.preparation,
                        operation: .rollback
                    )
                    let reviewAccess = HistoryReviewAccess(
                        reviewed: reviewed,
                        reviewedApplyAvailable: run.canContinueReviewedApply
                            && !model.mutationRequiresManualReview,
                        freshReviewAvailable: run.preview.canPrepareReview,
                        mutationBlockMessage: applyMutationBlockMessage,
                        reviewedReadOnlyAvailable: reviewed && run.localReviewSourceAvailable
                    )
                    let reviewActionAvailable = reviewAccess.canOpen
                    let reviewAccessibilityHint = HistoryReviewActionCopy.accessibilityHint(
                        reviewed: reviewed,
                        available: reviewActionAvailable,
                        requiresManualReview: model.mutationRequiresManualReview,
                        hasWarnings: run.preview.scanStatus == "ready_with_errors",
                        hasBlockedRows: run.hasBlockedRows,
                        sourceAvailable: run.localReviewSourceAvailable,
                        isRetryingFailedApply: run.isRetryingFailedApply,
                        mutationBlockMessage: reviewAccess.mutationBlockMessage
                    )
                    Button(
                        HistoryReviewActionCopy.title(
                            reviewed: reviewed,
                            available: reviewActionAvailable,
                            hasWarnings: run.preview.scanStatus == "ready_with_errors",
                            hasBlockedRows: run.hasBlockedRows,
                            isRetryingFailedApply: run.isRetryingFailedApply,
                            mutationBlockMessage: reviewAccess.mutationBlockMessage
                        )
                    ) { onReview(manifest) }
                    .disabled(
                        !HistoryActionAvailability.canOpenHistoricalReview(
                            reviewAvailable: reviewActionAvailable,
                            workerIsRunning: model.worker.state.isRunning
                        )
                    )
                    .accessibilityLabel(
                        HistoryReviewActionCopy.accessibilityLabel(
                            reviewed: reviewed,
                            available: reviewActionAvailable,
                            hasWarnings: run.preview.scanStatus == "ready_with_errors",
                            hasBlockedRows: run.hasBlockedRows,
                            isRetryingFailedApply: run.isRetryingFailedApply,
                            mutationBlockMessage: reviewAccess.mutationBlockMessage
                        )
                    )
                    .accessibilityHint(
                        HistoryActionAvailability.historicalReviewAccessibilityHint(
                            workerIsRunning: model.worker.state.isRunning,
                            fallback: reviewAccessibilityHint
                        )
                    )
                    if run.canRollback {
                        Button(run.rollbackActionText, role: .destructive) { rollbackCandidate = run }
                                    .disabled(!HistoryActionAvailability.canStartHistoricalRollback(
                                    workerIsRunning: model.worker.state.isRunning
                                    ) || rollbackMutationBlockMessage != nil)
                            .accessibilityLabel(run.rollbackActionText)
                            .accessibilityHint(
                                rollbackMutationBlockMessage
                                    ?? HistoryActionAvailability.historicalRollbackAccessibilityHint(
                                        workerIsRunning: model.worker.state.isRunning
                                    )
                            )
                    }
                    Menu("Archivos", systemImage: "doc") {
                        Button("Abrir manifest") {
                            openArtifact(manifest, runDirectory: runDirectory)
                        }
                        .disabled(!manifestAvailability.isOpenable)
                        .accessibilityHint(manifestAvailability.accessibilityHint)
                        Button("Abrir CSV") {
                            openArtifact(preview, runDirectory: runDirectory)
                        }
                        .disabled(!csvAvailability.isOpenable)
                        .accessibilityHint(csvAvailability.accessibilityHint)
                    }
                }
                .font(.caption)
                if let artifactNotice = [manifestAvailability.noticeText, csvAvailability.noticeText]
                    .compactMap({ $0 })
                            .first {
                    Label(artifactNotice, systemImage: "lock.shield")
                        .font(.caption)
                        .foregroundStyle(.orange)
                        .accessibilityLabel(artifactNotice)
                }
            }
        }
        .listRowSeparator(.hidden)
        .listRowInsets(EdgeInsets(top: 6, leading: 8, bottom: 6, trailing: 8))
    }

    private func reloadRuns() {
        guard let base = runsRoot else {
            historyRows = []
            return
        }
        let result = HistoryRunStore.loadResult(from: base, expectedRoot: base)
        historyRows = result.displayRows
        rejectedRunNotice = result.rejectedRunNotice
    }

    private var artifactAlertBinding: Binding<Bool> {
        Binding(get: { artifactToOpen != nil }, set: { if !$0 { artifactToOpen = nil } })
    }

    private var importAlertBinding: Binding<Bool> {
        Binding(get: { importMessage != nil }, set: { if !$0 { importMessage = nil } })
    }

    private func openArtifact(_ url: URL, runDirectory: URL) {
        guard HistoryArtifactAvailability.inspect(path: url, runDirectory: runDirectory).isOpenable else {
            importMessage = "El artefacto local cambió y no se abrirá. Actualiza Historial y conserva el run para auditoría."
            return
        }
        artifactToOpen = url
    }

    private func handleImport(_ result: Result<[URL], Error>) {
        guard HistoryActionAvailability.canImportManifest(
                workerIsRunning: model.worker.state.isRunning
        ) else {
            importMessage = HistoryActionAvailability.manifestImportBlockedNotice
            return
        }
        do {
            guard let source = try result.get().first else { return }
            let hasSecurityScope = source.startAccessingSecurityScopedResource()
            defer {
                if hasSecurityScope {
                    source.stopAccessingSecurityScopedResource()
                }
            }
            guard let root = runsRoot else {
                importMessage = "No se pudo resolver el almacenamiento local privado."
                return
            }
            let importedManifest = try HistoryRunStore.importManifest(from: source, to: root)
            reloadRuns()
            let importedPreview = try RunManifestPreview.load(from: importedManifest)
            let sourceAvailable = HistoryRunStore.reviewSourceAvailable(
                for: importedPreview,
                manifestURL: importedManifest
            )
            importMessage = HistoryImportCopy.message(
                preview: importedPreview,
                sourceAvailable: sourceAvailable
            )
        } catch {
            importMessage = "No se pudo importar el manifest. Selecciona un archivo regular llamado manifest.json con permisos privados."
        }
    }

}

private struct HistorySessionGroupCard: View {
    let session: HistorySessionSummary
    let width: CGFloat

    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 8) {
                let headerLayout = width < 620
                    ? AnyLayout(VStackLayout(alignment: .leading, spacing: 6))
                    : AnyLayout(HStackLayout(alignment: .top, spacing: 12))
                headerLayout {
                    Text(session.displayTitle)
                        .font(.headline)
                        .frame(maxWidth: .infinity, alignment: .leading)
                    Label(session.stateLabel, systemImage: stateSystemImage)
                        .foregroundStyle(session.state == .attention ? .orange : .secondary)
                }
                Text(session.summaryText)
                    .font(.callout)
                    .foregroundStyle(.secondary)

                ForEach(Array(session.attempts.enumerated()), id: \.element.id) { index, attempt in
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Elemento \(index + 1) · intento \(attempt.revision): \(attempt.stateLabel)")
                            .font(.callout.weight(.semibold))
                        Text(
                            attempt.decisionLabels.isEmpty
                                ? "Sin decisión registrada."
                                : "Decisiones: \(attempt.decisionLabels.joined(separator: ", "))."
                        )
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    }
                    .accessibilityElement(children: .combine)
                    .accessibilityLabel(attempt.presentationText)
                }

                Label(
                    session.rollbackAvailabilityText,
                    systemImage: session.canRollback ? "arrow.uturn.backward.circle" : "lock.shield"
                )
                .font(.callout)
                .foregroundStyle(session.canRollback ? .secondary : .tertiary)
            }
        } label: {
            Label("Actividad de la sesión", systemImage: "rectangle.stack")
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(session.presentationText)
    }

    private var stateSystemImage: String {
        switch session.state {
        case .running: return "play.circle"
        case .paused: return "pause.circle"
        case .stopped: return "stop.circle"
        case .attention: return "exclamationmark.triangle"
        }
    }
}

/// Bounds manifest dates before they enter the compact history card. This is
/// presentation-only and leaves the persisted run metadata untouched.
enum HistoryRunRowCopy {
    static func displayTitle(_ value: String) -> String {
        let compact = value
            .split(whereSeparator: \.isWhitespace)
            .joined(separator: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !compact.isEmpty else { return "Ejecución local" }
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
