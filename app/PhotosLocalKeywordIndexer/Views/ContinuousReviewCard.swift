import SwiftUI
import AppKit

struct ContinuousReviewCard: View {
    let item: QueueReviewItem
    let isExpanded: Bool
    let contentWidth: CGFloat
    let queueWorkAllowed: Bool
    let manualQueueMutationAllowed: Bool
    let onToggleExpanded: () -> Void
    let onInspect: () -> Void
    let onDraftChange: ([String], String?) -> Bool
    let onSave: () -> Void
    let onDiscard: () -> Void
    let onRescan: () -> Void

    @Environment(\.colorSchemeContrast) private var contrast
    @State private var pendingKeyword = ""
    @FocusState private var focusedField: EditorField?

    private enum EditorField: Hashable {
        case keyword
        case caption
    }

    private var actions: ContinuousReviewActions {
        ContinuousReviewActions(item: item)
    }

    private var canEdit: Bool { actions.canEdit && manualQueueMutationAllowed }

    private var hasReviewValues: Bool {
        actions.canEdit || !item.draft.keywords.isEmpty || item.draft.caption != nil
    }

    private var locationContextSummary: String? {
        guard let photo = item.photo, let trace = photo.technicalTrace,
              trace.usedGPS || trace.usedAppleMaps || !trace.placeContext.isEmpty else {
            return nil
        }
        return ContinuousReviewInspectorSnapshot(item: item).locationContext
    }

    private var displayTitle: String {
        guard let photo = item.photo else { return "Foto pendiente" }
        return ReviewPhotoRowCopy.displayTitle(photo.title)
    }

    private var displayDate: String {
        guard let photo = item.photo else { return "Esperando metadatos verificados" }
        return ReviewPhotoRowCopy.displayDate(photo.date)
    }

    private var thumbnailSize: CGSize {
        isExpanded
            ? CGSize(width: contentWidth >= 640 ? 144 : 96, height: contentWidth >= 640 ? 108 : 72)
            : CGSize(width: 64, height: 48)
    }

    private var cardLayout: AnyLayout {
        isExpanded && contentWidth < 640
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 12))
            : AnyLayout(HStackLayout(alignment: .top, spacing: 14))
    }

    var body: some View {
        cardLayout {
            thumbnailColumn
            VStack(alignment: .leading, spacing: isExpanded ? 14 : 5) {
                header
                if isExpanded {
                    expandedContent
                } else {
                    compactSummary
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(isExpanded ? 16 : 12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
        .overlay {
            RoundedRectangle(cornerRadius: 12)
                .strokeBorder(
                    isExpanded ? Color.accentColor.opacity(0.75) : Color.primary.opacity(contrast == .increased ? 0.35 : 0.1),
                    lineWidth: isExpanded || contrast == .increased ? 1.5 : 1
                )
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(displayTitle). \(item.statusCopy)")
    }

    private var thumbnailColumn: some View {
        VStack(alignment: .leading, spacing: 8) {
            PhotoThumbnailView(
                photosLocalIdentifier: item.photo?.photosLocalIdentifier,
                displayTitle: displayTitle,
                presentationSize: thumbnailSize
            )
            .id(item.thumbnailIdentity)
            if isExpanded, let photo = item.photo, photo.shouldDisplayConfidence {
                Label(photo.confidenceText, systemImage: "gauge.with.dots.needle.50percent")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
                    .accessibilityLabel(photo.confidenceAccessibilityLabel)
            }
        }
        .frame(width: thumbnailSize.width, alignment: .leading)
    }

    private var header: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .top, spacing: 12) {
                photoHeading
                Spacer(minLength: 0)
                headerActions
            }
            VStack(alignment: .leading, spacing: 8) {
                photoHeading
                headerActions
            }
        }
    }

    private var photoHeading: some View {
        VStack(alignment: .leading, spacing: 3) {
            Text(displayTitle)
                .font(.headline)
                .lineLimit(isExpanded ? nil : 1)
                .fixedSize(horizontal: false, vertical: true)
            Text(displayDate)
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(isExpanded ? nil : 1)
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private var headerActions: some View {
        HStack(spacing: 8) {
            statusBadge
            Button(action: onToggleExpanded) {
                Label(isExpanded ? "Contraer" : reviewButtonTitle, systemImage: isExpanded ? "chevron.up" : "chevron.down")
            }
            .buttonStyle(.bordered)
            .controlSize(.small)
            .accessibilityLabel(isExpanded ? "Contraer revisión de \(displayTitle)" : "Revisar \(displayTitle)")
            .accessibilityValue(isExpanded ? "Ampliada" : "Contraída")
            .accessibilityHint("Muestra u oculta los valores completos; no guarda cambios.")
            secondaryMenu
        }
        .fixedSize(horizontal: true, vertical: false)
    }

    private var reviewButtonTitle: String {
        actions.canEdit ? "Revisar" : "Consultar"
    }

    private var statusBadge: some View {
        Label(statusTitle, systemImage: statusSymbol)
            .font(.caption2.weight(.medium))
            .foregroundStyle(statusColor)
            .padding(.horizontal, 7)
            .padding(.vertical, 4)
            .background(statusColor.opacity(0.09), in: RoundedRectangle(cornerRadius: 5))
            .accessibilityLabel(item.statusCopy)
    }

    private var secondaryMenu: some View {
        Menu {
            Button("Detalles de la foto", action: onInspect)
            Button("Reanalizar", action: onRescan)
                .disabled(!actions.canRescan || !queueWorkAllowed)
            Divider()
            Button("Descartar", action: onDiscard)
                .disabled(!actions.canDiscard || !manualQueueMutationAllowed)
        } label: {
            Image(systemName: "ellipsis")
        }
        .menuStyle(.borderlessButton)
        .frame(width: 18)
        .accessibilityLabel("Más acciones para \(displayTitle)")
    }

    private var compactSummary: some View {
        HStack(alignment: .center, spacing: 10) {
            if item.progressPresentation == .indeterminate {
                ProgressView()
                    .controlSize(.mini)
                    .accessibilityLabel(item.statusCopy)
            }
            Text(compactExcerpt)
                .font(.caption)
                .foregroundStyle(needsAttention ? statusColor : .secondary)
                .lineLimit(1)
                .frame(maxWidth: .infinity, alignment: .leading)
            if needsAttention {
                Button("Reanalizar", action: onRescan)
                    .buttonStyle(.bordered)
                    .controlSize(.small)
                    .disabled(!actions.canRescan || !queueWorkAllowed)
                    .accessibilityHint("Solicita una propuesta nueva y conserva las ediciones manuales.")
            }
        }
        .help(compactExcerpt)
    }

    private var compactExcerpt: String {
        if item.saveRequestStatus != nil || needsAttention || item.progressPresentation == .indeterminate {
            return item.statusCopy
        }
        if !item.draft.keywords.isEmpty {
            return "\(item.draft.keywords.count) etiquetas · " + item.draft.keywords.prefix(3).joined(separator: " · ")
        }
        return item.draft.caption ?? item.statusCopy
    }

    private var expandedContent: some View {
        VStack(alignment: .leading, spacing: 12) {
            if !actions.canEdit || item.saveRequestStatus != nil {
                statusExplanation
            }
            if let failure = item.failurePresentation {
                failureDiagnostic(failure)
            }
            if hasReviewValues {
                keywordEditor
                captionEditor
            }
            if let locationContextSummary {
                Label(locationContextSummary, systemImage: "location.circle")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                    .accessibilityLabel(locationContextSummary)
            }
            if hasReviewValues || actions.canDiscard || actions.canRescan {
                Divider()
                actionBar
            }
        }
    }

    private func failureDiagnostic(_ failure: QueueReviewFailurePresentation) -> some View {
        VStack(alignment: .leading, spacing: 5) {
            Label("Qué ocurrió", systemImage: "exclamationmark.triangle")
                .font(.caption.weight(.semibold))
                .foregroundStyle(.orange)
            Text(failure.reason)
                .font(.caption)
                .foregroundStyle(.secondary)
                .fixedSize(horizontal: false, vertical: true)
            Text("No se guardó ningún cambio en Fotos.")
                .font(.caption)
                .foregroundStyle(.secondary)
            Text("Siguiente acción: \(failure.nextAction)")
                .font(.caption)
                .fixedSize(horizontal: false, vertical: true)
        }
        .padding(10)
        .background(.orange.opacity(0.08), in: RoundedRectangle(cornerRadius: 8))
        .accessibilityElement(children: .combine)
        .accessibilityLabel("Diagnóstico seguro. Código: \(failure.code). \(failure.technicalDetail) Siguiente acción: \(failure.nextAction)")
    }

    private var statusExplanation: some View {
        HStack(alignment: .top, spacing: 8) {
            if item.progressPresentation == .indeterminate {
                ProgressView().controlSize(.small)
                    .accessibilityLabel(item.statusCopy)
            } else {
                Image(systemName: statusSymbol).foregroundStyle(statusColor)
            }
            Text(item.statusCopy)
                .font(.callout)
                .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var keywordEditor: some View {
        VStack(alignment: .leading, spacing: 7) {
            HStack {
                Text("Etiquetas").font(.callout.weight(.semibold))
                Spacer()
                Text("\(item.draft.keywords.count) de 8")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            if item.draft.keywords.isEmpty {
                Text("Sin etiquetas")
                    .font(.callout)
                    .foregroundStyle(.secondary)
            } else {
                ReviewKeywordFlow(spacing: 6) {
                    ForEach(item.draft.keywords, id: \.self) { keyword in
                        keywordChip(keyword)
                    }
                }
            }
            if actions.canEdit {
                HStack(spacing: 8) {
                    TextField("Añadir etiqueta", text: $pendingKeyword)
                        .textFieldStyle(.roundedBorder)
                        .focused($focusedField, equals: .keyword)
                        .onSubmit(addPendingKeyword)
                        .accessibilityLabel("Añadir etiqueta para \(displayTitle)")
                    Button("Agregar", action: addPendingKeyword)
                        .disabled(!canEdit || QueueReviewEditPolicy.addingKeyword(pendingKeyword, to: item.draft.keywords) == nil)
                }
                .disabled(!canEdit)
            }
        }
    }

    private func keywordChip(_ keyword: String) -> some View {
        HStack(alignment: .firstTextBaseline, spacing: 5) {
            Text(keyword)
                .font(.callout)
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            if actions.canEdit {
                Button {
                    guard canEdit else { return }
                    _ = onDraftChange(item.draft.keywords.filter { $0 != keyword }, item.draft.caption)
                } label: {
                    Image(systemName: "xmark")
                        .font(.caption2.weight(.semibold))
                }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)
                .disabled(!canEdit)
                .accessibilityLabel("Quitar etiqueta \(keyword)")
            }
        }
        .padding(.horizontal, 8)
        .padding(.vertical, 5)
        .background(Color.accentColor.opacity(contrast == .increased ? 0.17 : 0.09), in: RoundedRectangle(cornerRadius: 6))
    }

    private var captionEditor: some View {
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text("Descripción").font(.callout.weight(.semibold))
                Spacer()
                Text("\(item.draft.caption?.count ?? 0) de 240")
                    .font(.caption2)
                    .foregroundStyle(.secondary)
            }
            if actions.canEdit {
                TextField("Escribe una descripción opcional", text: captionBinding, axis: .vertical)
                    .textFieldStyle(.roundedBorder)
                    .lineLimit(2...)
                    .focused($focusedField, equals: .caption)
                    .disabled(!canEdit)
                    .accessibilityLabel("Descripción de \(displayTitle)")
            } else {
                Text(item.draft.caption ?? "Sin descripción")
                    .font(.callout)
                    .foregroundStyle(item.draft.caption == nil ? .secondary : .primary)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
        }
    }

    private var actionBar: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 10) {
                secondaryActions
                Spacer(minLength: 8)
                saveButton
            }
            VStack(alignment: .leading, spacing: 10) {
                saveButton
                secondaryActions
            }
        }
    }

    private var secondaryActions: some View {
        HStack(spacing: 10) {
            Button("Descartar", action: onDiscard)
                .buttonStyle(.bordered)
                .disabled(!actions.canDiscard || !manualQueueMutationAllowed)
                .accessibilityHint("Retira esta foto de la sesión sin modificar Apple Photos.")
            if needsAttention {
                Button("Reanalizar", action: onRescan)
                    .buttonStyle(.bordered)
                    .disabled(!actions.canRescan || !queueWorkAllowed)
            }
        }
    }

    @ViewBuilder
    private var saveButton: some View {
        if hasReviewValues {
            Button("Guardar en Fotos", action: onSave)
            .buttonStyle(.borderedProminent)
            .disabled(!actions.canSave || !queueWorkAllowed)
            .accessibilityHint(
                !queueWorkAllowed
                    ? "Resuelve el bloqueo de preparación o espera a que la revisión manual esté disponible antes de guardar."
                    : actions.canSave
                    ? "Vuelve a leer la foto, guarda solo estos valores y verifica el resultado."
                    : "Espera un resultado editable con al menos una etiqueta o descripción."
            )
        }
    }

    private var captionBinding: Binding<String> {
        Binding(
            get: { item.draft.caption ?? "" },
            set: { value in
                guard canEdit else { return }
                if value.isEmpty {
                    _ = onDraftChange(item.draft.keywords, nil)
                } else if QueueReviewEditPolicy.caption(value) != nil {
                    _ = onDraftChange(item.draft.keywords, value)
                }
            }
        )
    }

    private func addPendingKeyword() {
        guard canEdit,
              let updated = QueueReviewEditPolicy.addingKeyword(pendingKeyword, to: item.draft.keywords),
              onDraftChange(updated, item.draft.caption) else { return }
        pendingKeyword = ""
    }

    private var needsAttention: Bool {
        [.failed, .uncertain].contains(item.state) || item.saveRequestStatus == .unconfirmed
    }

    private var statusTitle: String {
        if item.saveRequestStatus == .sending { return "Enviando" }
        if item.saveRequestStatus == .unconfirmed { return "No confirmado" }
        switch item.state {
        case .discovered, .queued: return "En cola"
        case .preparing: return "Preparando"
        case .analyzing: return "Analizando"
        case .validating: return "Validando"
        case .ready: return "Lista para revisar"
        case .edited: return "Editada"
        case .saveQueued: return "Esperando guardado"
        case .saving: return "Guardando"
        case .verified: return "Verificada"
        case .discarded: return "Descartada"
        case .failed: return "Error"
        case .uncertain: return "Requiere revisión"
        }
    }

    private var statusColor: Color {
        if needsAttention { return .orange }
        if item.saveRequestStatus != nil { return .secondary }
        switch item.state {
        case .ready, .edited, .verified: return .green
        case .preparing, .analyzing, .validating, .saveQueued, .saving: return .accentColor
        default: return .secondary
        }
    }

    private var statusSymbol: String {
        if item.saveRequestStatus == .unconfirmed { return "exclamationmark.triangle.fill" }
        if item.saveRequestStatus == .sending { return "arrow.up.circle" }
        switch item.state {
        case .discovered, .queued: return "clock"
        case .preparing: return "photo.badge.arrow.down"
        case .analyzing: return "sparkles"
        case .validating: return "checkmark.shield"
        case .ready, .edited: return "checkmark.circle"
        case .saveQueued: return "tray"
        case .saving: return "square.and.arrow.down"
        case .verified: return "checkmark.circle.fill"
        case .discarded: return "xmark.circle"
        case .failed, .uncertain: return "exclamationmark.triangle.fill"
        }
    }
}

/// Wraps every complete keyword, including a keyword wider than the current editor.
private struct ReviewKeywordFlow: Layout {
    let spacing: CGFloat

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = max(1, proposal.width ?? 400)
        return arrangement(width: width, subviews: subviews).size
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        let result = arrangement(width: max(1, bounds.width), subviews: subviews)
        for (index, subview) in subviews.enumerated() {
            let frame = result.frames[index]
            subview.place(
                at: CGPoint(x: bounds.minX + frame.minX, y: bounds.minY + frame.minY),
                proposal: ProposedViewSize(width: frame.width, height: frame.height)
            )
        }
    }

    private func arrangement(width: CGFloat, subviews: Subviews) -> (size: CGSize, frames: [CGRect]) {
        var frames: [CGRect] = []
        var x: CGFloat = 0
        var y: CGFloat = 0
        var rowHeight: CGFloat = 0
        for subview in subviews {
            let ideal = subview.sizeThatFits(.unspecified)
            let size = subview.sizeThatFits(ProposedViewSize(width: min(width, ideal.width), height: nil))
            if x > 0, x + size.width > width {
                x = 0
                y += rowHeight + spacing
                rowHeight = 0
            }
            frames.append(CGRect(x: x, y: y, width: min(width, size.width), height: size.height))
            x += size.width + spacing
            rowHeight = max(rowHeight, size.height)
        }
        return (CGSize(width: width, height: y + rowHeight), frames)
    }
}
