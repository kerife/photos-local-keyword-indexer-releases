import SwiftUI

struct AutonomyReviewBanner: View {
    let presentation: AutonomyReviewPresentation
    let includeCaptions: Bool
    let onStart: () -> Void
    let onPause: () -> Void
    let onResume: () -> Void
    let onRefresh: () -> Void
    @State private var showsScope = false
    @State private var showsActivationConfirmation = false

    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 8) {
                Toggle("Guardado autónomo", isOn: toggleBinding)
                    .disabled(!canToggle)
                Text("Piloto beta: guarda como máximo \(AutonomyPilotPolicy.maximumPhotos) resultados válidos con confianza de 85 % o más sin revisión por foto.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                status
                if let campaign = presentation.campaign {
                    Text(campaign.countersText)
                        .font(.callout)
                        .monospacedDigit()
                    Text("En curso: \(presentation.activities.count) · Analizando: \(presentation.analyzingCount) · Guardando: \(presentation.savingCount) · Registros sin identificar: \(campaign.invalidCount)")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    if let progress = presentation.progress {
                        ProgressView(value: progress)
                    }
                    controls(for: campaign)
                }
                if let error = presentation.error {
                    Text(error).foregroundStyle(.orange)
                }
                if presentation.error != nil || !presentation.statusReady {
                    Button("Comprobar estado del recorrido", action: onRefresh)
                        .disabled(presentation.controlPending)
                }
                Button("Alcance y opciones") { showsScope.toggle() }
                    .popover(isPresented: $showsScope, arrowEdge: .bottom) { scopePopover }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .accessibilityElement(children: .contain)
            .accessibilityLabel(presentation.accessibilityLabel)
            .confirmationDialog(
                "Iniciar piloto de guardado autónomo",
                isPresented: $showsActivationConfirmation,
                titleVisibility: .visible
            ) {
                Button("Iniciar piloto de \(AutonomyPilotPolicy.maximumPhotos) fotos") { onStart() }
                Button("Cancelar", role: .cancel) {}
            } message: {
                Text(activationScope)
            }
    }

    private var canToggle: Bool {
        if presentation.activationPending {
            return !presentation.pausePending
        }
        guard !presentation.controlPending else { return false }
        if presentation.isRunning { return true }
        return presentation.statusReady
    }

    private var toggleBinding: Binding<Bool> {
        Binding(
            get: { presentation.isRunning },
            set: { enabled in
                if enabled {
                    showsActivationConfirmation = true
                } else {
                    onPause()
                }
            }
        )
    }

    @ViewBuilder
    private var status: some View {
        if presentation.pausePending {
            Text("Pausando: el trabajo aceptado termina; no se admiten nuevos guardados.")
        } else if presentation.activationPending {
            Text("Preparando el recorrido y terminando el trabajo manual iniciado…")
        } else if let campaign = presentation.campaign {
            Text(campaign.statusText)
        } else if !presentation.statusReady {
            Text("Comprobando el recorrido guardado…")
        }
    }

    @ViewBuilder
    private func controls(for campaign: AutonomyCampaignSnapshot) -> some View {
        if campaign.state == .paused {
            Button("Iniciar nuevo piloto limitado", action: { showsActivationConfirmation = true })
                .disabled(!presentation.statusReady || presentation.controlPending)
        } else if campaign.state == .completed {
            Button("Iniciar nuevo piloto limitado", action: { showsActivationConfirmation = true })
                .disabled(!presentation.statusReady || presentation.controlPending)
        }
    }

    private var scopePopover: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                Text("Alcance del guardado autónomo").font(.headline)
                Text(activationScope)
                    .font(.callout)
                Text("Cada recorrido conserva la configuración con la que se inició. Los borradores manuales se conservan.")
                    .font(.caption).foregroundStyle(.secondary)
                Text("Límite fijo de beta: \(AutonomyPilotPolicy.maximumPhotos) fotos por piloto.").font(.caption.weight(.medium))
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(20)
        }
        .frame(width: 360, height: 340)
    }

    private var activationScope: String {
        let captions = includeCaptions
            ? "También puede añadir una descripción solo cuando Fotos no tenga una."
            : "No añadirá descripciones."
        return "La app puede guardar hasta \(AutonomyPilotPolicy.maximumPhotos) imágenes accesibles más recientes, incluidas capturas y fotos ya etiquetadas, sin revisión por foto. Añade keywords conservando las existentes y verifica cada escritura. \(captions)"
    }
}
