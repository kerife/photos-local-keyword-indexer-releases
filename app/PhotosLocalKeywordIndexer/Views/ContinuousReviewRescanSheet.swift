import SwiftUI

struct ContinuousReviewRescanSheet: View {
    let item: QueueReviewItem
    let modelOptions: [String]
    let onCancel: () -> Void
    let onSubmit: (QueueRescanOptions) -> Void

    @State private var draft: ContinuousReviewRescanDraft
    @State private var showsAdvancedPrompt = false
    @State private var showsPromptPreview = false

    init(
        item: QueueReviewItem,
        modelOptions: [String],
        initialModel: String,
        onCancel: @escaping () -> Void,
        onSubmit: @escaping (QueueRescanOptions) -> Void
    ) {
        self.item = item
        self.modelOptions = modelOptions
        self.onCancel = onCancel
        self.onSubmit = onSubmit
        _draft = State(initialValue: ContinuousReviewRescanDraft(model: initialModel))
    }

    var body: some View {
        GeometryReader { geometry in
        VStack(alignment: .leading, spacing: 16) {
            HStack(alignment: .firstTextBaseline) {
                VStack(alignment: .leading, spacing: 3) {
                    Label("Reanalizar foto", systemImage: "arrow.clockwise.circle")
                        .font(.title2.weight(.semibold))
                    Text(
                        item.photo.map { ReviewPhotoRowCopy.displayTitle($0.title) }
                            ?? "Foto pendiente"
                    )
                        .font(.caption)
                        .foregroundStyle(.secondary)
                }
                Spacer()
                Text("Intento nuevo")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }

            ScrollView {
                VStack(alignment: .leading, spacing: 16) {
                    modelAndProfile
                    if item.hasManualEdits {
                        manualEditsBehavior
                    }
                    analysisLayers(width: geometry.size.width - 44)
                    additionalInformation
                    DisclosureGroup("Prompt avanzado", isExpanded: $showsAdvancedPrompt) {
                        promptEditor.padding(.top, 8)
                    }
                    DisclosureGroup("Vista previa del prompt", isExpanded: $showsPromptPreview) {
                        promptPreview.padding(.top, 8)
                    }
                    Label(
                        "El contrato JSON, los límites de privacidad y la exclusión de coordenadas permanecen bloqueados.",
                        systemImage: "lock.shield"
                    )
                    .font(.caption)
                    .foregroundStyle(.secondary)
                }
            }

            Divider()
            VStack(alignment: .leading, spacing: 8) {
                Text(validationMessage)
                    .font(.caption)
                    .foregroundStyle(draft.validatedOptions == nil ? .orange : .secondary)
                HStack {
                Spacer()
                Button("Cancelar", action: onCancel)
                    .keyboardShortcut(.cancelAction)
                Button("Reanalizar") {
                    guard let options = draft.validatedOptions else { return }
                    onSubmit(options)
                }
                .buttonStyle(.borderedProminent)
                .keyboardShortcut(.defaultAction)
                .disabled(draft.validatedOptions == nil)
                .accessibilityHint(
                    "Conserva las ediciones manuales y crea un intento nuevo con estas opciones privadas."
                )
                }
            }
        }
        .padding(22)
        }
        .frame(minWidth: 560, idealWidth: 700, minHeight: 460, idealHeight: 640)
    }

    private var modelAndProfile: some View {
        GroupBox("Modelo y perfil") {
            VStack(alignment: .leading, spacing: 10) {
                if modelOptions.isEmpty {
                    Label("No hay un modelo local disponible para reanalizar.", systemImage: "exclamationmark.triangle")
                        .foregroundStyle(.orange)
                } else {
                    Picker("Modelo", selection: $draft.model) {
                        ForEach(modelOptions, id: \.self) { model in
                            Text(model).tag(model)
                        }
                    }
                }
                Picker("Perfil", selection: $draft.profile) {
                    ForEach(QueueRescanProfile.allCases, id: \.self) { profile in
                        Text(profile.displayName).tag(profile)
                    }
                }
                .pickerStyle(.segmented)
                Text(draft.profile.detail)
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private func analysisLayers(width: CGFloat) -> some View {
        GroupBox("Capas de análisis") {
            LazyVGrid(
                columns: Array(repeating: GridItem(.flexible(), alignment: .leading), count: width < 600 ? 1 : 2),
                alignment: .leading,
                spacing: 10
            ) {
                Toggle("Lugares", isOn: $draft.layers.places)
                Toggle("Documentos y texto", isOn: $draft.layers.documentsText)
                Toggle("Personas y accesorios", isOn: $draft.layers.peopleAccessories)
                Toggle("Normalización semántica", isOn: $draft.layers.semanticNormalization)
            }
        }
    }

    private var manualEditsBehavior: some View {
        GroupBox("Ediciones manuales") {
            VStack(alignment: .leading, spacing: 6) {
                Toggle("Restablecer también mis ediciones", isOn: $draft.resetManualEdits)
                Text(
                    draft.resetManualEdits
                        ? "La propuesta nueva reemplazará el borrador manual actual. El intento anterior seguirá en Historial."
                        : "Tus etiquetas y descripción editadas se conservarán cuando llegue la propuesta nueva."
                )
                .font(.caption)
                .foregroundStyle(.secondary)
            }
        }
        .accessibilityElement(children: .contain)
    }

    private var additionalInformation: some View {
        GroupBox("Información adicional") {
            VStack(alignment: .leading, spacing: 6) {
                TextEditor(text: $draft.additionalInformation)
                    .font(.body)
                    .frame(minHeight: 70, maxHeight: 100)
                    .accessibilityLabel("Información adicional para esta foto")
                Text("Opcional, máximo 1,024 caracteres. No incluyas credenciales ni coordenadas.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
        }
    }

    private var promptEditor: some View {
        GroupBox("Cuerpo analítico del prompt") {
            VStack(alignment: .leading, spacing: 8) {
                TextEditor(text: $draft.promptBody)
                    .font(.body.monospaced())
                    .frame(minHeight: 120)
                    .accessibilityLabel("Cuerpo analítico editable del prompt")
                VStack(alignment: .leading, spacing: 8) {
                    Text("Máximo 4,096 caracteres; el sobre estructural no es editable.")
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Button("Restaurar prompt predeterminado") {
                        draft.restoreDefaultPrompt()
                    }
                    .buttonStyle(.bordered)
                    .accessibilityHint("Restaura solo el cuerpo del prompt y conserva el resto de las opciones.")
                }
            }
        }
    }

    private var promptPreview: some View {
        GroupBox("Vista previa del prompt efectivo") {
            ScrollView {
                Text(draft.effectivePromptPreview)
                    .font(.caption.monospaced())
                    .textSelection(.enabled)
                    .frame(maxWidth: .infinity, alignment: .leading)
            }
            .frame(minHeight: 130, maxHeight: 190)
            .accessibilityLabel("Vista previa del prompt efectivo")
        }
    }

    private var validationMessage: String {
        draft.validatedOptions == nil
            ? "Corrige el modelo o elimina contenido privado o inválido antes de continuar."
            : "Las opciones se guardarán en un artefacto local privado; no viajarán en JSONL."
    }
}

