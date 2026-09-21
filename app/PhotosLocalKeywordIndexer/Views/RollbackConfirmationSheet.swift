import SwiftUI

/// Final, explicit review surface for a rollback. The detail comes only from
/// verified apply read-backs; uncertain and external changes are not offered
/// as removable values.
struct RollbackConfirmationSheet: View {
    let detail: RollbackConfirmationDetail
    let onCancel: () -> Void
    let onConfirm: () -> Void

    var body: some View {
        GeometryReader { geometry in
            let margin: CGFloat = geometry.size.width < 640 ? 16 : 24
            VStack(alignment: .leading, spacing: 16) {
                VStack(alignment: .leading, spacing: 6) {
                    Label("Confirmar rollback en Apple Fotos", systemImage: "exclamationmark.triangle.fill")
                        .font(.title2.weight(.semibold))
                    Text(detail.summaryText)
                        .font(.headline)
                        .accessibilityLabel(detail.accessibilitySummary)
                        .fixedSize(horizontal: false, vertical: true)
                    Text(detail.blockedText)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                }

                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        changeGroup(
                            title: "Keywords que se eliminarán",
                            systemImage: "tag",
                            lines: detail.keywordDisplayLines,
                            emptyText: "No hay keywords verificadas para eliminar."
                        )
                        changeGroup(
                            title: "Captions que se eliminarán",
                            systemImage: "text.quote",
                            lines: detail.captionDisplayLines,
                            emptyText: "No hay captions verificados para eliminar."
                        )
                        GroupBox("Bloqueado por seguridad") {
                            Label(
                                PhotoMetadataProtectionCopy.rollbackText,
                                systemImage: "lock.shield"
                            )
                            .font(.callout)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .accessibilityLabel(PhotoMetadataProtectionCopy.rollbackAccessibility)
                        }
                    }
                }
                .accessibilityElement(children: .contain)
                .accessibilityLabel("Detalle exacto de los cambios verificados que se eliminarán")

                Divider()
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 10) {
                        confirmationButtons
                    }
                    .fixedSize(horizontal: true, vertical: false)
                    VStack(alignment: .leading, spacing: 10) {
                        confirmationButtons
                    }
                }
                .frame(maxWidth: .infinity, alignment: .trailing)
            }
            .padding(margin)
        }
        .frame(minWidth: 560, minHeight: 460)
    }

    @ViewBuilder
    private var confirmationButtons: some View {
        Button("Cancelar", action: onCancel)
            .keyboardShortcut(.cancelAction)
        Button(role: .destructive, action: onConfirm) {
            Text("Eliminar cambios verificados")
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
        .buttonStyle(.borderedProminent)
        .accessibilityHint(
            detail.affectedPhotoCount == 0
                ? MutationConfirmationCopy.disabledRollbackHint
                : "Confirma la eliminación únicamente de cambios registrados y verificados."
        )
        .disabled(detail.affectedPhotoCount == 0)
    }

    @ViewBuilder
    private func changeGroup(
        title: String,
        systemImage: String,
        lines: [ConfirmationDisplayLine],
        emptyText: String
    ) -> some View {
        GroupBox {
            if lines.isEmpty {
                Text(emptyText)
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                VStack(alignment: .leading, spacing: 5) {
                    ForEach(lines) { line in
                        Label(line.text, systemImage: "checkmark.circle")
                            .font(.callout)
                            .fixedSize(horizontal: false, vertical: true)
                            .textSelection(.enabled)
                            .frame(maxWidth: .infinity, alignment: .leading)
                    }
                }
            }
        } label: {
            Label(title, systemImage: systemImage)
        }
    }
}
