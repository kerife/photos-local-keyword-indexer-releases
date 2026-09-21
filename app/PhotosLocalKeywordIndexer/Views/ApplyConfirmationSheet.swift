import SwiftUI

/// Final, explicit review surface for a mutation. Keep this separate from the
/// preview list so the person can verify the exact write scope immediately
/// before PhotoScript is invoked.
struct ApplyConfirmationSheet: View {
    let detail: ReviewConfirmationDetail
    let onCancel: () -> Void
    let onConfirm: () -> Void

    var body: some View {
        GeometryReader { geometry in
            let margin: CGFloat = geometry.size.width < 640 ? 16 : 24
            VStack(alignment: .leading, spacing: 16) {
                VStack(alignment: .leading, spacing: 6) {
                    Label("Confirmar escritura en Apple Fotos", systemImage: "exclamationmark.triangle.fill")
                        .font(.title2.weight(.semibold))
                    Text(detail.summaryText)
                        .font(.headline)
                        .accessibilityLabel(detail.accessibilitySummary)
                        .fixedSize(horizontal: false, vertical: true)
                }

                ScrollView {
                    VStack(alignment: .leading, spacing: 12) {
                        confirmationPhotos
                        changeGroup(
                            title: "Keywords aprobadas",
                            systemImage: "tag",
                            lines: detail.keywordDisplayLines,
                            emptyText: "No hay keywords aprobadas."
                        )
                        changeGroup(
                            title: "Captions aprobados",
                            systemImage: "text.quote",
                            lines: detail.captionDisplayLines,
                            emptyText: "No hay captions aprobados."
                        )
                        GroupBox("Cambios efectivos y conflictos externos") {
                            VStack(alignment: .leading, spacing: 6) {
                                Label(detail.effectiveChangeText, systemImage: "arrow.triangle.2.circlepath")
                                Label(detail.captionConflictText, systemImage: "text.quote")
                                Label(detail.externalConflictText, systemImage: "person.2.wave.2")
                            }
                            .font(.callout)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        GroupBox("Límites de escritura") {
                            VStack(alignment: .leading, spacing: 6) {
                                Label("Solo se modificarán keywords y captions aprobados.", systemImage: "lock.shield")
                                Label(
                                    CaptionReviewCopy.detail,
                                    systemImage: "checkmark.shield"
                                )
                                Label(PhotoMetadataProtectionCopy.applyText, systemImage: "checkmark.shield")
                                    .accessibilityLabel(PhotoMetadataProtectionCopy.applyAccessibility)
                            }
                            .font(.callout)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        GroupBox(MutationConfirmationCopy.preApplySafetyTitle) {
                            Label(
                                MutationConfirmationCopy.preApplySafetyDetail,
                                systemImage: "checkmark.shield"
                            )
                            .font(.callout)
                            .frame(maxWidth: .infinity, alignment: .leading)
                            .accessibilityLabel(MutationConfirmationCopy.preApplySafetyAccessibilityLabel)
                        }
                    }
                }
                .accessibilityElement(children: .contain)
                .accessibilityLabel("Detalle exacto de los cambios aprobados")

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
            Text(detail.applyActionText)
                .fixedSize(horizontal: false, vertical: true)
                .multilineTextAlignment(.leading)
        }
        .buttonStyle(.borderedProminent)
        .accessibilityHint(
            detail.affectedPhotoCount == 0
                ? MutationConfirmationCopy.disabledApplyHint
                : "Confirma la escritura después de revisar el alcance exacto."
        )
        .disabled(detail.affectedPhotoCount == 0)
    }

    @ViewBuilder
    private var confirmationPhotos: some View {
        GroupBox {
            if detail.photoItems.isEmpty {
                Text("No hay fotos afectadas.")
                    .foregroundStyle(.secondary)
                    .frame(maxWidth: .infinity, alignment: .leading)
            } else {
                VStack(alignment: .leading, spacing: 10) {
                    ForEach(detail.photoItems) { item in
                        HStack(alignment: .top, spacing: 12) {
                            PhotoThumbnailView(
                                photosLocalIdentifier: item.photosLocalIdentifier,
                                displayTitle: item.displayTitle
                            )
                            VStack(alignment: .leading, spacing: 4) {
                                Text(item.displayTitle)
                                    .font(.headline)
                                if !item.approvedKeywords.isEmpty {
                                    Text("Keywords: \(item.approvedKeywords.joined(separator: ", "))")
                                        .font(.callout)
                                }
                                if let caption = item.approvedCaption {
                                    Label(caption, systemImage: "text.quote")
                                        .font(.callout)
                                }
                            }
                            .fixedSize(horizontal: false, vertical: true)
                            .frame(maxWidth: .infinity, alignment: .leading)
                        }
                        .accessibilityElement(children: .contain)
                    }
                }
            }
        } label: {
            Label("Fotos que se modificarán", systemImage: "photo.on.rectangle")
        }
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
