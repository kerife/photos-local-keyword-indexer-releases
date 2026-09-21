import SwiftUI

struct ContinuousReviewInspector: View {
    let item: QueueReviewItem
    let onClose: () -> Void

    @State private var isShowingTechnicalTrace = false

    init(item: QueueReviewItem, onClose: @escaping () -> Void = {}) {
        self.item = item
        self.onClose = onClose
    }

    private var snapshot: ContinuousReviewInspectorSnapshot {
        ContinuousReviewInspectorSnapshot(item: item)
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Text("Detalles de la foto")
                    .font(.title2.weight(.semibold))
                Spacer()
                Button(action: onClose) {
                    Image(systemName: "xmark")
                }
                .buttonStyle(.borderless)
                .keyboardShortcut(.cancelAction)
                .accessibilityLabel("Cerrar detalles de la foto")
                .help("Cerrar detalles")
            }

            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    VStack(alignment: .leading, spacing: 12) {
                        sectionTitle("Comparación de revisión")
                        inspectorRow("Etiquetas propuestas", value: snapshot.proposalKeywords)
                        inspectorRow("Etiquetas actuales", value: snapshot.currentKeywords)
                        inspectorRow("Diferencia", value: snapshot.reviewDifference)
                    }

                    if !snapshot.errorCodes.isEmpty {
                        VStack(alignment: .leading, spacing: 8) {
                            sectionTitle("Errores y advertencias")
                            ForEach(snapshot.errorCodes, id: \.self) { code in
                                VStack(alignment: .leading, spacing: 3) {
                                    Label(code, systemImage: "exclamationmark.triangle")
                                        .font(.caption.monospaced())
                                    Text(HumanErrorCopy.message(for: code))
                                        .font(.caption)
                                        .foregroundStyle(.secondary)
                                }
                                    .foregroundStyle(.orange)
                            }
                            if let failure = item.failurePresentation {
                                Text(failure.technicalDetail)
                                    .font(.caption)
                                    .foregroundStyle(.secondary)
                                Text("Siguiente acción: \(failure.nextAction)")
                                    .font(.caption)
                            }
                        }
                    }

                    Divider()

                    VStack(alignment: .leading, spacing: 12) {
                        sectionTitle("Análisis local")
                        inspectorRow("Modelo", value: snapshot.model)
                        inspectorRow("Confianza del modelo", value: snapshot.confidence)
                        inspectorRow("Contexto de ubicación", value: snapshot.locationContext)
                    }

                    Divider()

                    DisclosureGroup("Traza técnica", isExpanded: $isShowingTechnicalTrace) {
                        VStack(alignment: .leading, spacing: 12) {
                            inspectorRow("Attempt ID", value: snapshot.attemptID, selectable: true)
                            inspectorRow("UUID", value: snapshot.uuid, selectable: true)
                            inspectorRow("Motivo de routing", value: snapshot.routingReason)
                            inspectorRow("Fuentes de contexto", value: snapshot.contextSources)
                            inspectorRow(
                                "Contexto de lugar sanitizado",
                                value: snapshot.placeContext.isEmpty ? "Ninguno" : snapshot.placeContext
                            )
                            inspectorRow("Versión de Ollama", value: snapshot.ollamaVersion)
                            inspectorRow("Versión del prompt", value: snapshot.promptVersion)
                            inspectorRow("Hash del prompt", value: snapshot.promptHash, selectable: true)
                            inspectorRow("Duraciones", value: snapshot.stageDurations)
                            inspectorRow(
                                "Prompt efectivo sanitizado",
                                value: snapshot.promptEffective,
                                selectable: true
                            )
                            Text("La traza no incluye rutas, coordenadas, OCR literal ni respuestas crudas del modelo.")
                                .font(.caption)
                                .foregroundStyle(.secondary)
                        }
                        .padding(.top, 12)
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .padding(.trailing, 4)
            }
        }
        .padding(20)
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .topLeading)
        .accessibilityElement(children: .contain)
        .accessibilityLabel("Detalles de la foto")
    }

    private func sectionTitle(_ title: String) -> some View {
        Text(title)
            .font(.headline)
    }

    @ViewBuilder
    private func inspectorRow(
        _ title: String,
        value: String,
        selectable: Bool = false
    ) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title)
                .font(.caption.weight(.medium))
                .foregroundStyle(.secondary)
            if selectable {
                Text(value)
                    .font(.caption.monospaced())
                    .textSelection(.enabled)
            } else {
                Text(value)
                    .font(.callout)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
        .accessibilityLabel("\(title): \(value)")
    }
}
