import SwiftUI

struct ScanView: View {
    @EnvironmentObject private var model: AppModel
    let onOpenPreparation: () -> Void
    let onOpenHistory: () -> Void

    init(
        onOpenPreparation: @escaping () -> Void,
        onOpenHistory: @escaping () -> Void = {}
    ) {
        self.onOpenPreparation = onOpenPreparation
        self.onOpenHistory = onOpenHistory
    }

    var body: some View {
        scanForm
    }

    private var scanForm: some View {
        let modelConfiguration = ScanModelConfiguration(
            policy: model.modelPolicy,
            singleModel: model.singleModel,
            fastModel: model.fastModel,
            detailedModel: model.detailedModel
        )
        let missingModels = modelConfiguration.missingModels(from: model.preparation.installedModels)
        let plan = ScanPlanSummary(
            photoLimit: model.limit,
            modelText: modelConfiguration.configuredModelsText,
            modelRoutingText: modelConfiguration.qualityText,
            appleMapsEnabled: model.appleMaps,
            captionsEnabled: model.includeCaption,
            randomSelection: model.randomSelection
        )
        return Form {
            Section("Plan de esta ejecución") {
                VStack(alignment: .leading, spacing: 8) {
                    Label(plan.scopeText, systemImage: "photo.on.rectangle.angled")
                    Label(plan.modelLabelText, systemImage: "cpu")
                    Label(plan.contextText, systemImage: plan.appleMapsEnabled ? "map" : "map.slash")
                    Label(plan.outputText, systemImage: "checklist")
                    Label(
                        "Solo se generará una vista previa; no se modificará Apple Fotos.",
                        systemImage: "lock.shield"
                    )
                    .foregroundStyle(.secondary)
                }
                .font(.callout)
                .accessibilityElement(children: .ignore)
                .accessibilityLabel(plan.accessibilityLabel)
            }
            Section("Alcance del dry-run") {
                Stepper("Fotos: \(model.limit)", value: $model.limit, in: 1...500)
                Toggle("Seleccionar fotos al azar", isOn: $model.randomSelection)
                    .accessibilityHint("Si está activado, elige fotos elegibles al azar en lugar de priorizar las más recientes.")
                Text("El análisis prepara una vista previa y no modifica Apple Fotos.")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            .disabled(!ScanControlState.optionsAreEditable(workerIsRunning: model.worker.state.isRunning))
            Section("Modelos locales") {
                Picker("Política de modelos", selection: $model.modelPolicy) {
                    Text(modelConfiguration.policyPickerLabel).tag("adaptive")
                    Text("Un solo modelo").tag("single")
                }
                if model.modelPolicy == "single" {
                    TextField("Modelo", text: $model.singleModel)
                } else {
                    TextField("Modelo rápido", text: $model.fastModel)
                    TextField("Modelo detallado", text: $model.detailedModel)
                }
                Text(modelConfiguration.qualityText)
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let validationMessage = modelConfiguration.validationMessage {
                    Label(validationMessage, systemImage: "exclamationmark.triangle.fill")
                        .font(.callout)
                        .foregroundStyle(.orange)
                        .accessibilityLabel("Configuración de modelos incompleta: \(validationMessage)")
                }
                if !missingModels.isEmpty {
                    let names = missingModels.map(ModelPresentationCopy.display).joined(separator: ", ")
                    VStack(alignment: .leading, spacing: 8) {
                        Label(
                            "Vuelve a Preparación para comprobar: \(names).",
                            systemImage: "arrow.uturn.backward.circle"
                        )
                        .font(.callout)
                        .foregroundStyle(.orange)
                        .accessibilityLabel("Modelos sin comprobar: \(names). Vuelve a Preparación antes de analizar.")
                        Button(ScanModelRecoveryCopy.buttonTitle, action: onOpenPreparation)
                            .buttonStyle(.bordered)
                            .accessibilityHint(ScanModelRecoveryCopy.buttonAccessibilityHint)
                    }
                }
                if model.preflightIsStale {
                    Label(
                        "La configuración cambió desde la última comprobación; vuelve a Preparación antes de analizar.",
                        systemImage: "arrow.triangle.2.circlepath"
                    )
                    .font(.callout)
                    .foregroundStyle(.orange)
                    .accessibilityLabel("La configuración de modelos cambió desde la última comprobación. Vuelve a Preparación antes de analizar.")
                }
            }
            .disabled(!ScanControlState.optionsAreEditable(workerIsRunning: model.worker.state.isRunning))
            Section("Opciones") {
                Toggle("Usar Apple Maps para fotos geolocalizadas", isOn: $model.appleMaps)
                    .accessibilityHint(
                        ScanPrivacyNotice(appleMapsEnabled: model.appleMaps).accessibilityHint
                    )
                if ScanPrivacyNotice(appleMapsEnabled: model.appleMaps).isVisible {
                    Label(
                        ScanPrivacyNotice(appleMapsEnabled: true).text,
                        systemImage: "location.triangle.fill"
                    )
                    .foregroundStyle(.orange)
                    .font(.callout)
                }
                Toggle(ScanCaptionCopy.toggleLabel, isOn: $model.includeCaption)
                    .accessibilityHint(ScanCaptionCopy.accessibilityHint)
                Text(ScanCaptionCopy.detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .disabled(!ScanControlState.optionsAreEditable(workerIsRunning: model.worker.state.isRunning))
            if model.worker.state.isRunning {
                Label(ScanControlState.lockedMessage, systemImage: "lock.fill")
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .accessibilityLabel(ScanControlState.lockedMessage)
            }
            if model.worker.state.isRunning
                || !model.events.isEmpty
                || model.worker.state.terminalStatusText != nil {
                let progress = ScanProgressSummary(
                    events: model.events,
                    requestedLimit: model.scanProgressLimit,
                    workerState: model.worker.state
                )
                Section("Progreso del análisis") {
                    ProgressView(value: progress.fraction) {
                        Text(progress.statusText)
                    }
                    HStack(spacing: 16) {
                        progressMetric(
                            value: progress.successfulCount,
                            label: "Completadas",
                            systemImage: "checkmark.circle.fill",
                            color: .green
                        )
                        progressMetric(
                            value: progress.failedCount,
                            label: "Con error",
                            systemImage: "exclamationmark.triangle.fill",
                            color: .orange
                        )
                        progressMetric(
                            value: progress.uncertainCount,
                            label: "Revisión manual",
                            systemImage: "questionmark.circle.fill",
                            color: .orange
                        )
                        progressMetric(
                            value: progress.cancelledCount,
                            label: "Canceladas",
                            systemImage: "pause.circle.fill",
                            color: .secondary
                        )
                        Spacer(minLength: 0)
                    }
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel(progress.accessibilityLabel)
                    if let terminalStatusText = model.worker.state.terminalStatusText {
                        Label(
                            terminalStatusText,
                            systemImage: model.worker.state == .interrupted
                                ? "pause.circle.fill"
                                : "xmark.octagon.fill"
                        )
                        .font(.callout.weight(.medium))
                        .foregroundStyle(.orange)
                        .accessibilityLabel(model.worker.state.terminalStatusAccessibilityLabel ?? terminalStatusText)
                    }
                    if ScanHistoryRecoveryCopy.shouldShow(
                        for: model.worker.state,
                        terminalOutcome: progress.terminalOutcome
                    ) {
                        Button(ScanHistoryRecoveryCopy.buttonTitle, action: onOpenHistory)
                            .buttonStyle(.bordered)
                            .accessibilityHint(ScanHistoryRecoveryCopy.accessibilityHint)
                    }
                    if let currentModel = progress.currentModelDisplayText {
                        Label("Modelo actual: \(currentModel)", systemImage: "cpu")
                            .font(.caption)
                            .foregroundStyle(.secondary)
                    }
                    let outcomeCopy = ScanOutcomeCopy(
                        state: model.worker.state,
                        terminalOutcome: progress.terminalOutcome,
                        processedCount: progress.processedCount,
                        proposedKeywordCount: progress.isTerminal
                            ? model.preview?.photos.reduce(0) { $0 + $1.proposedKeywords.count }
                            : nil,
                        proposedCaptionCount: progress.isTerminal
                            ? model.preview?.photos.filter(\.hasCaptionProposal).count
                            : nil
                    )
                    Label(outcomeCopy.text, systemImage: outcomeCopy.symbolName)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .accessibilityLabel(outcomeCopy.accessibilityLabel)
                }
            }
            Section("Iniciar") {
                HStack {
                    Button("Analizar sin modificar Fotos") { model.scan() }
                        .buttonStyle(.borderedProminent)
                        .accessibilityHint(
                            ScanActionCopy.accessibilityHint(
                                preparationReady: model.preparation.isReady,
                                modelConfigurationValid: modelConfiguration.isValid,
                                missingModelCount: missingModels.count,
                                workerIsRunning: model.worker.state.isRunning,
                                preflightIsStale: model.preflightIsStale
                            )
                        )
                        .disabled(
                            !model.preparation.isReady
                                || model.worker.state.isRunning
                                || !modelConfiguration.isValid
                                || !missingModels.isEmpty
                                || model.preflightIsStale
                        )
                    if model.worker.state.isRunning {
                        let cancellationRequested = model.worker.state.isCancellationRequested
                        Button(
                            ScanControlState.cancellationButtonTitle(
                                isCancellationRequested: cancellationRequested
                            )
                        ) { model.cancel() }
                        .disabled(
                            !ScanControlState.canRequestCancellation(
                                isCancellationRequested: cancellationRequested
                            )
                        )
                        .accessibilityHint(
                            ScanControlState.cancellationAccessibilityHint(
                                isCancellationRequested: cancellationRequested
                            )
                        )
                    }
                }
                Text(ScanActionCopy.statusMessage(base: model.message, preflightIsStale: model.preflightIsStale))
                    .foregroundStyle(.secondary)
                if !model.preparation.isReady {
                    Text("Completa Preparación para habilitar el dry-run.")
                        .foregroundStyle(.orange)
                } else if model.preflightIsStale {
                    Text("Vuelve a Preparación para comprobar la configuración actual de modelos.")
                        .foregroundStyle(.orange)
                }
            }
        }
        .formStyle(.grouped)
        .padding()
        .navigationTitle(AppRoute.scan.navigationTitle)
    }

    private func progressMetric(
        value: Int,
        label: String,
        systemImage: String,
        color: Color
    ) -> some View {
        Label {
            VStack(alignment: .leading, spacing: 1) {
                Text("\(value)").font(.headline.monospacedDigit())
                Text(label).font(.caption).foregroundStyle(.secondary)
            }
        } icon: {
            Image(systemName: systemImage).foregroundStyle(color)
        }
        .accessibilityLabel("\(value) " + label.lowercased())
    }
}
