import SwiftUI

struct AutonomyCampaignPanel: View {
    @ObservedObject var model: AppModel
    @State private var showsScope = false

    var body: some View {
        GroupBox {
            VStack(alignment: .leading, spacing: 8) {
                Toggle("Guardado autónomo", isOn: Binding(
                    get: { model.autonomyIsOn },
                    set: { $0 ? model.startAutonomy() : model.pauseAutonomy() }
                ))
                .disabled(!model.autonomyIsOn && (!model.autonomyStatusReady || model.autonomyControlPending || !model.continuousReviewPreparation.allowsQueueWork))
                Text("Guarda resultados válidos con confianza de 85% o más sin revisión por foto. Incluye capturas y fotos ya etiquetadas.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if model.autonomyPausePending {
                    Text("Pausando: el trabajo aceptado termina; no se admiten nuevos guardados.")
                } else if model.autonomyActivationPending {
                    Text("Preparando el recorrido y terminando el trabajo manual iniciado…")
                } else if let campaign = model.autonomyCampaign {
                    Text(campaign.statusText)
                } else if !model.autonomyStatusReady {
                    Text("Comprobando el recorrido guardado…")
                }
                if let campaign = model.autonomyCampaign {
                    Text(campaign.countersText).font(.callout).monospacedDigit()
                        .accessibilityLabel(campaign.countersText)
                    Text("En curso: \(campaign.inFlight) · Registros del inventario sin identificar: \(campaign.invalidCount)")
                        .font(.caption).foregroundStyle(.secondary)
                    if campaign.total > 0 { ProgressView(value: Double(campaign.total - campaign.remaining), total: Double(campaign.total)) }
                    if campaign.state == .paused {
                        Button("Reanudar recorrido", action: model.resumeAutonomy)
                            .disabled(!model.autonomyStatusReady || model.autonomyControlPending)
                    }
                }
                if let error = model.autonomyError {
                    Text(error).foregroundStyle(.orange)
                    Button("Comprobar estado del recorrido", action: model.bootstrapAutonomyStatusIfNeeded)
                        .disabled(model.autonomyControlPending)
                }
                Button("Alcance y opciones") { showsScope.toggle() }
                    .popover(isPresented: $showsScope, arrowEdge: .bottom) {
                        ScrollView {
                            VStack(alignment: .leading, spacing: 12) {
                                Text("Alcance del guardado autónomo").font(.headline)
                                Text("Recorre las imágenes accesibles más recientes, incluidas capturas y fotos ya etiquetadas. Añade keywords conservando las existentes y guarda resultados válidos con confianza de 85% o más sin revisión por foto.")
                                    .font(.callout)
                                Text(model.continuousControls.includeCaptions
                                     ? "Captions activados: escribe una descripción solo cuando Fotos no tiene una."
                                     : "Captions desactivados para el próximo recorrido. Activa las descripciones en Opciones de análisis para incluir descripciones vacías.")
                                    .font(.caption).foregroundStyle(.secondary)
                                Text("Cada recorrido conserva la configuración con la que se inició. Los cambios de modelo, captions y límite se usan en el próximo recorrido. Los borradores manuales se conservan.")
                                    .font(.caption).foregroundStyle(.secondary)
                                Text("Piloto beta fijo: máximo de \(AutonomyPilotPolicy.maximumPhotos) fotos por recorrido.")
                                    .font(.caption.weight(.medium))
                            }
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .padding(20)
                        }
                        .frame(width: 360, height: 340)
                    }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding([.horizontal, .top])
    }
}
