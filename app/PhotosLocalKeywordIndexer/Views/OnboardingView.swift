import SwiftUI
import AppKit

struct OnboardingView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var updates: UpdateService
    let onContinue: () -> Void
    @State private var copiedInstruction: String?

    var body: some View {
        ResponsivePage { width in
            ScrollView {
                VStack(alignment: .leading, spacing: 20) {
                    header
                    readinessCard
                    nextActionCard
                    actions
                    preflightProgressCard
                    statusGrid(width: width)
                    privacyNotice
                    DisclosureGroup("Distribución, permisos y soporte") {
                        VStack(alignment: .leading, spacing: 12) {
                            buildChannelCard
                            releaseDiagnosticCard
                            smokeTestSafetyCard
                            permissionBoundaryCard
                        }
                        .padding(.top, 8)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
            }
        }
        .navigationTitle(AppRoute.setup.navigationTitle)
    }

    private var buildChannelCard: some View {
        let channel = BuildChannelCopy.current()
        return GroupBox {
            Text(channel.detail)
                .font(.callout)
                .foregroundStyle(.secondary)
        } label: {
            Label(channel.label, systemImage: channel.symbolName)
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel(channel.accessibilityLabel)
    }

    private var header: some View {
        VStack(alignment: .leading, spacing: 8) {
            Text("Preparación local")
                .font(.largeTitle.weight(.semibold))
            Text("Comprueba los componentes necesarios antes de configurar un análisis dry-run.")
                .font(.title3)
                .foregroundStyle(.secondary)
            Label(globalStatusText, systemImage: globalStatusSymbol)
                .font(.callout)
                .foregroundStyle(globalStatusColor)
                .accessibilityLabel("Estado global: \(globalStatusText)")
            Label(
                "Siguiente acción segura: \(globalNextActionText)",
                systemImage: "arrow.forward.circle"
            )
            .font(.callout.weight(.medium))
            .foregroundStyle(.secondary)
            .accessibilityLabel("Siguiente acción segura: \(globalNextActionText)")
        }
    }

    private func statusGrid(width: CGFloat) -> some View {
        LazyVGrid(
            columns: Array(repeating: GridItem(.flexible(), spacing: 12), count: width < 600 ? 1 : 2),
            alignment: .leading,
            spacing: 12
        ) {
            statusCard("Fotos", state: model.preparation.photos, detail: photosDetail)
            statusCard(
                "Automatización",
                state: model.preparation.automation,
                detail: automationDetail
            )
            statusCard(
                "Ollama",
                state: model.preparation.ollama,
                detail: "Servicio local en 127.0.0.1:11434."
            )
            statusCard(
                "Modelos",
                state: model.preparation.models,
                detail: model.preparation.modelStatusText
            )
        }
    }

    @ViewBuilder
    private var releaseDiagnosticCard: some View {
        if !updates.isConfigured {
            let status = SettingsUpdateStatus.unconfigured
            GroupBox {
                Label(
                    "El análisis local no depende de las actualizaciones de la app.",
                    systemImage: "checkmark.shield"
                )
                .font(.callout)
                Text(status.detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            } label: {
                Label(SettingsUpdateCopy.localBuildTitle, systemImage: status.symbolName)
            }
            .accessibilityElement(children: .combine)
            .accessibilityLabel(
                "\(status.accessibilityLabel) El análisis local puede continuar sin Sparkle."
            )
        }
    }

    private var smokeTestSafetyCard: some View {
        GroupBox {
            Text(SmokeTestSafetyCopy.detail)
                .font(.callout)
                .foregroundStyle(.secondary)
                .accessibilityLabel(SmokeTestSafetyCopy.accessibilityLabel)
        } label: {
            Label(SmokeTestSafetyCopy.sectionTitle, systemImage: "checkmark.shield")
        }
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(SmokeTestSafetyCopy.sectionTitle). \(SmokeTestSafetyCopy.accessibilityLabel)")
    }

    private var readinessCard: some View {
        let summary = PreparationReadinessSummary(
            state: model.preparation,
            preflightIsStale: model.preflightIsStale
        )

        return GroupBox {
            VStack(alignment: .leading, spacing: 8) {
                HStack {
                    Label(summary.title, systemImage: summary.symbolName)
                        .font(.headline)
                    Spacer()
                    Text(summary.progressLabel)
                        .font(.headline.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
                ProgressView(value: summary.fraction)
                    .accessibilityHidden(true)
                Text(summary.detail)
                    .font(.callout)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(summary.accessibilityLabel)
        } label: {
            Label(PreparationReadinessSummary.sectionTitle, systemImage: "checklist")
        }
    }

    @ViewBuilder
    private var preflightProgressCard: some View {
        if model.preparation.hasPreflightProgress {
            GroupBox {
                VStack(alignment: .leading, spacing: 6) {
                    Label(
                        model.preparation.preflightProgressText,
                        systemImage: preflightProgressSymbol
                    )
                    .font(.callout.weight(.medium))
                    Text(PreparationPreflightCopy.detail)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityElement(children: .combine)
                .accessibilityLabel(model.preparation.preflightProgressAccessibilityLabel)
            } label: {
                Label("Progreso de preparación", systemImage: "checklist")
            }
        }
    }

    private var privacyNotice: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 6) {
                Label(
                    "Las imágenes se analizan localmente y no se suben a servicios cloud.",
                    systemImage: LocalAnalysisPrivacyCopy.symbolName
                )
                Label(
                    AppleMapsPrivacyCopy.text(enabled: model.appleMaps),
                    systemImage: AppleMapsPrivacyCopy.symbolName(enabled: model.appleMaps)
                )
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .font(.callout)
        } label: {
            Text("Privacidad")
        }
    }

    private var permissionBoundaryCard: some View {
        let guide = AutomationTCCGuide.packagedHelper

        return GroupBox {
            VStack(alignment: .leading, spacing: 8) {
                Label(PermissionBoundaryGuide.title, systemImage: "checkmark.shield")
                    .font(.headline)
                Text(PermissionBoundaryGuide.distinction)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                VStack(alignment: .leading, spacing: 6) {
                    Label(PermissionBoundaryGuide.photos, systemImage: "photo")
                    Label(PermissionBoundaryGuide.automation, systemImage: "gear.badge")
                }
                .font(.callout)
                .foregroundStyle(.secondary)
                Text(guide.shortLabel)
                    .font(.caption.weight(.medium))
                Text("La primera operación PhotoScript es la comprobación real de Automatización.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityElement(children: .combine)
            .accessibilityLabel(PermissionBoundaryGuide.accessibilityLabel)
        } label: {
            Label(PermissionBoundaryGuide.sectionTitle, systemImage: "person.badge.key.fill")
        }
    }

    private var nextActionCard: some View {
        let summary = PreparationActionSummary(
            state: model.preparation,
            preflightIsStale: model.preflightIsStale
        )

        return GroupBox {
            VStack(alignment: .leading, spacing: 10) {
                HStack(alignment: .top, spacing: 10) {
                    Image(systemName: summary.symbolName)
                        .foregroundStyle(summary.isBlocking ? .orange : .green)
                        .font(.title3)
                        .accessibilityHidden(true)
                    VStack(alignment: .leading, spacing: 4) {
                        Text(summary.title)
                            .font(.headline)
                        Text(summary.detail)
                            .font(.callout)
                            .foregroundStyle(.secondary)
                    }
                    Spacer(minLength: 0)
                }
                if let instruction = summary.safeInstruction {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(instruction)
                            .font(.system(.callout, design: .monospaced))
                            .textSelection(.enabled)
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        Button(copiedInstruction == instruction ? "Copiado" : "Copiar") {
                            copyInstruction(instruction)
                        }
                        .buttonStyle(.bordered)
                        .accessibilityLabel(summary.commandCopyAccessibilityLabel(copied: copiedInstruction == instruction))
                        .accessibilityHint(summary.commandCopyAccessibilityHint)
                    }
                    .padding(.horizontal, 8)
                    .padding(.vertical, 6)
                    .background(.quaternary, in: RoundedRectangle(cornerRadius: 6))
                    .accessibilityElement(children: .contain)
                    .accessibilityLabel("Comando informativo: \(instruction)")
                }
                Button(summary.buttonTitle, action: performSafeAction)
                    .buttonStyle(.borderedProminent)
                    .accessibilityHint(summary.buttonAccessibilityHint)
                    .disabled(model.worker.state.isRunning || summary.action == "wait_for_preflight")
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .accessibilityElement(children: .contain)
            .accessibilityLabel(summary.accessibilityLabel)
        } label: {
            Label("Siguiente acción segura", systemImage: "arrow.forward.circle")
        }
    }

    private var actions: some View {
        VStack(alignment: .leading, spacing: 10) {
            if shouldRequestPhotosAccess {
                Button(photosAccessButtonTitle, action: requestOrOpenPhotosAccess)
                    .accessibilityHint(PhotosAccessActionCopy.accessibilityHint(for: model.preparation.photos))
                    .disabled(model.worker.state.isRunning)
            }
            if model.preparation.photos == .actionRequired {
                Button("Ya concedí el permiso · comprobar de nuevo", action: model.refreshPreparation)
                    .buttonStyle(.bordered)
                    .disabled(model.worker.state.isRunning)
                    .accessibilityHint(
                        "Actualiza el estado de Fotos y vuelve a comprobar Ollama si es necesario; no se abrirá ni modificará ninguna foto."
                    )
            }
            if model.preparation.canPrepareAutomationRetry {
                Button(AutomationRecoveryCopy.buttonTitle) {
                    model.prepareAutomationRetry()
                    if model.preparation.isReady {
                        onContinue()
                    }
                }
                .buttonStyle(.bordered)
                .disabled(model.worker.state.isRunning)
                .accessibilityHint(AutomationRecoveryCopy.buttonAccessibilityHint)
            }
            Text(model.message)
                .font(.callout)
                .foregroundStyle(.secondary)
        }
    }

    private func performSafeAction() {
        let action = PreparationActionSummary(
            state: model.preparation,
            preflightIsStale: model.preflightIsStale
        ).action
        switch action {
        case "grant_photos_access":
            requestOrOpenPhotosAccess()
        case "grant_photos_automation":
            _ = model.permissions.openAutomationSettings()
        case "none" where model.preparation.isReady:
            onContinue()
        case "wait_for_preflight":
            break
        default:
            model.preflight()
        }
    }

    private func copyInstruction(_ instruction: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(instruction, forType: .string)
        copiedInstruction = instruction
    }

    @ViewBuilder
    private func statusCard(_ title: String, state: PreparationCheckState, detail: String) -> some View {
        GroupBox {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: symbol(for: state))
                    .foregroundStyle(color(for: state))
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 4) {
                    Text(title).font(.headline)
                    Text(state.humanLabel).font(.subheadline.weight(.medium))
                    Text(detail).font(.caption).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
            }
            .frame(maxWidth: .infinity, minHeight: 76, alignment: .topLeading)
            .accessibilityElement(children: .ignore)
            .accessibilityLabel("\(title): \(state.humanLabel). \(detail)")
        }
    }

    private var photosDetail: String {
        PhotosStatusCopy.detail(
            state: model.preparation.photos,
            accessIsLimited: model.permissions.photos == .limited
        )
    }

    private var preflightProgressSymbol: String {
        if model.preparation.errorCodes.contains("PHOTOSCRIPT_UNAVAILABLE") {
            return "exclamationmark.triangle.fill"
        }
        switch model.preparation.ollama {
        case .checking: return "clock.arrow.circlepath"
        case .failed: return "exclamationmark.triangle.fill"
        case .actionRequired: return "exclamationmark.triangle.fill"
        case .ready: return "checkmark.circle.fill"
        case .pending: return "circle.dashed"
        }
    }

    private var automationDetail: String {
        AutomationStatusCopy.detail(
            state: model.preparation.automation,
            errorCodes: model.preparation.errorCodes
        )
    }

    private var photosAccessButtonTitle: String {
        model.preparation.photos == .actionRequired
            ? "Abrir configuración de Fotos"
            : "Solicitar acceso de Fotos"
    }

    private func requestOrOpenPhotosAccess() {
        switch PhotosAccessActionCopy.resolution(for: model.permissions.photos) {
        case .openSettings:
            _ = model.permissions.openPhotosSettings()
        case .requestAuthorization:
            Task { await model.requestPhotosAccess() }
        case .none:
            break
        }
    }

    private var shouldRequestPhotosAccess: Bool {
        PhotosAccessActionCopy.shouldShowAuxiliaryAction(
            permission: model.permissions.photos,
            preparation: model.preparation.photos,
            primaryAction: model.preparation.nextAction
        )
    }

    private var globalStatusSymbol: String {
        if model.preflightIsStale { return "arrow.triangle.2.circlepath" }
        if model.preparation.isReady && model.preparation.hasWarnings {
            return "exclamationmark.triangle.fill"
        }
        if model.preparation.isReady { return "checkmark.shield.fill" }
        if model.preparation.requiresAttention { return "exclamationmark.triangle.fill" }
        return "checkmark.shield"
    }

    private var globalStatusText: String {
        model.preflightIsStale ? PreparationStaleCopy.text : model.preparation.statusMessage
    }

    private var globalNextActionText: String {
        model.preflightIsStale ? "Comprobar de nuevo la preparación local" : model.preparation.nextActionText
    }

    private var globalStatusColor: Color {
        if model.preflightIsStale { return .orange }
        if model.preparation.isReady && model.preparation.hasWarnings {
            return .orange
        }
        if model.preparation.isReady { return .green }
        if model.preparation.requiresAttention { return .orange }
        return .secondary
    }

    private func symbol(for state: PreparationCheckState) -> String {
        switch state {
        case .ready: return "checkmark.circle.fill"
        case .checking: return "clock.arrow.circlepath"
        case .actionRequired: return "exclamationmark.circle.fill"
        case .failed: return "xmark.circle.fill"
        case .pending: return "circle.dashed"
        }
    }

    private func color(for state: PreparationCheckState) -> Color {
        switch state {
        case .ready: return .green
        case .actionRequired, .failed: return .orange
        case .checking, .pending: return .secondary
        }
    }
}
