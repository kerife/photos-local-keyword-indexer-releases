import Foundation

enum QueueReviewItemState: String, CaseIterable, Equatable, Sendable {
    case discovered
    case queued
    case preparing
    case analyzing
    case validating
    case ready
    case edited
    case saveQueued = "save_queued"
    case saving
    case verified
    case discarded
    case failed
    case uncertain

    var statusMessage: String {
        switch self {
        case .discovered: return "Foto descubierta."
        case .queued: return "Foto añadida a la cola."
        case .preparing: return "Preparando una copia para el análisis local."
        case .analyzing: return "Analizando una foto en este Mac."
        case .validating: return "Validando una propuesta local."
        case .ready: return "Una foto ya está lista para revisar."
        case .edited: return "La edición manual está lista para guardar."
        case .saveQueued: return "En cola para guardar en Apple Fotos."
        case .saving: return "Guardando y verificando en Apple Fotos."
        case .verified: return "La escritura fue verificada."
        case .discarded: return "La foto fue descartada sin modificar Fotos."
        case .failed: return "No se pudo completar la operación."
        case .uncertain: return "Una escritura requiere revisión manual antes de continuar."
        }
    }
}

enum QueueReviewProgressPresentation: Equatable, Sendable {
    case hidden
    case indeterminate
    case complete
}

struct ContinuousReviewAnnouncement: Equatable, Sendable {
    let sequence: Int
    let text: String
}

enum ContinuousReviewAnnouncementCopy {
    static func text(for state: QueueReviewItemState) -> String? {
        switch state {
        case .preparing: return "Preparando una copia para el análisis local."
        case .analyzing: return "Analizando una foto en este Mac."
        case .validating: return "Validando una propuesta local."
        case .ready, .edited: return "Una foto está lista para revisar."
        case .saveQueued: return "Una foto entró en la cola de guardado."
        case .saving: return "Una foto se está guardando y verificando."
        case .verified: return "Una escritura en Fotos fue verificada."
        case .failed: return "Una foto requiere atención."
        case .uncertain: return "Una escritura requiere revisión manual."
        case .discovered, .queued, .discarded: return nil
        }
    }
}

struct QueueReviewProposal: Equatable, Sendable {
    let keywords: [String]
    let caption: String?

    init(keywords: [String], caption: String?) {
        self.keywords = keywords
        self.caption = caption
    }

    static let empty = QueueReviewProposal(keywords: [], caption: nil)
}

/// A bounded, user-facing diagnosis for a failed analysis.  The source
/// manifest can contain diagnostic data that must remain local, so this model
/// deliberately exposes only a recognized cause code and fixed copy.
struct QueueReviewFailurePresentation: Equatable, Sendable {
    let code: String
    let reason: String
    let technicalDetail: String
    let nextAction: String

    static func analysisFailure(for photo: PreviewPhoto) -> Self? {
        guard photo.state == "analysis_failed", photo.applyState == "not_run" else { return nil }
        let knownCodes = photo.errors.lazy
            .map(\.code)
            .filter { HumanErrorCopy.message(for: $0) != "La ejecución necesita revisión manual." }
        guard let code = knownCodes.first else {
            return Self(
                code: "No disponible",
                reason: "No se pudo completar el análisis local.",
                technicalDetail: "No quedó un código de causa seguro en el resultado local. Apple Fotos no se modificó.",
                nextAction: "Reanaliza esta foto. Si vuelve a ocurrir, comprueba la preparación local."
            )
        }
        return Self(
            code: code,
            reason: HumanErrorCopy.message(for: code),
            technicalDetail: technicalDetail(for: code),
            nextAction: nextAction(for: code)
        )
    }

    private static func technicalDetail(for code: String) -> String {
        switch code {
        case "ANALYSIS_FAILED":
            return "Etapa técnica: análisis local. Esta ejecución anterior no registró una causa más específica. Apple Fotos no se modificó."
        case "READ_FAILED", "PHOTOS_ACCESS_DENIED", "PHOTOS_ACCESS_LIMITED":
            return "Etapa técnica: lectura de Fotos. La app no pudo obtener la información necesaria; Apple Fotos no se modificó."
        case "EXPORT_FAILED", "WORKSPACE_FAILED":
            return "Etapa técnica: preparación de una copia temporal. No se inició una escritura en Apple Fotos."
        case "OLLAMA_UNAVAILABLE", "OLLAMA_MODEL_MISSING", "OLLAMA_NO_VISION", "OLLAMA_VERSION_OLD", "OLLAMA_PREFLIGHT_FAILED", "OLLAMA_RESPONSE_INVALID", "OLLAMA_REQUEST_FAILED":
            return "Etapa técnica: motor de análisis local. No se generó una propuesta ni se modificó Apple Fotos."
        case "VISION_IMAGE_INVALID":
            return "Etapa técnica: preparación de la imagen para el modelo local. No se inició una escritura en Apple Fotos."
        case "PHOTOS_AUTOMATION_DENIED", "PHOTOSCRIPT_UNAVAILABLE", "HELPER_UNAVAILABLE":
            return "Etapa técnica: conexión local con Fotos. La operación se detuvo antes de cualquier guardado."
        case "IDENTITY_MISMATCH":
            return "Etapa técnica: verificación de identidad. La foto cambió durante el proceso y se descartó el resultado para evitar asociarlo a otra foto."
        default:
            return "Etapa técnica registrada en el resultado local. El detalle bruto se omitió para proteger tus datos; Apple Fotos no se modificó."
        }
    }

    private static func nextAction(for code: String) -> String {
        switch code {
        case "PHOTOS_ACCESS_DENIED", "PHOTOS_ACCESS_LIMITED", "PHOTOS_AUTOMATION_DENIED", "PHOTOSCRIPT_UNAVAILABLE":
            return "Comprueba los permisos de Fotos en Preparación y luego reanaliza esta foto."
        case "OLLAMA_UNAVAILABLE", "OLLAMA_MODEL_MISSING", "OLLAMA_NO_VISION", "OLLAMA_VERSION_OLD", "OLLAMA_PREFLIGHT_FAILED", "OLLAMA_RESPONSE_INVALID", "OLLAMA_REQUEST_FAILED":
            return "Corrige la preparación de Ollama y vuelve a analizar esta foto."
        case "VISION_IMAGE_INVALID":
            return "Vuelve a analizar esta foto; si se repite, comprueba la preparación local y el formato de la imagen."
        case "IDENTITY_MISMATCH":
            return "Ejecuta un análisis nuevo para leer la versión actual de la foto."
        default:
            return "Reanaliza esta foto o descártala; no se guardará ningún cambio automáticamente."
        }
    }
}

struct QueueReviewDraft: Equatable, Sendable {
    var keywords: [String]
    var caption: String?
}

enum QueueSaveRequestStatus: Equatable, Sendable {
    case sending
    case unconfirmed
}

struct QueueReviewItem: Identifiable, Equatable, Sendable {
    let id: String
    let ordinal: Int
    private(set) var revision: Int
    private(set) var state: QueueReviewItemState
    private(set) var proposal: QueueReviewProposal
    private(set) var draft: QueueReviewDraft
    private(set) var hasManualEdits: Bool
    private(set) var manifestURL: URL?
    private(set) var photo: PreviewPhoto?
    var saveRequestStatus: QueueSaveRequestStatus?

    init(id: String, ordinal: Int, revision: Int = 0) {
        self.id = id
        self.ordinal = ordinal
        self.revision = revision
        state = .queued
        proposal = .empty
        draft = QueueReviewDraft(keywords: [], caption: nil)
        hasManualEdits = false
        manifestURL = nil
        photo = nil
    }

    var progressPresentation: QueueReviewProgressPresentation {
        if saveRequestStatus == .sending { return .indeterminate }
        if saveRequestStatus == .unconfirmed { return .complete }
        switch state {
        case .discovered, .queued:
            return .hidden
        case .preparing, .analyzing, .validating, .saveQueued, .saving:
            return .indeterminate
        case .ready, .edited, .verified, .discarded, .failed, .uncertain:
            return .complete
        }
    }

    /// Fixed copy intentionally excludes the item identifier, proposal, draft,
    /// model output, and any local file information.
    var statusCopy: String {
        if saveRequestStatus == .sending { return "Enviando solicitud…" }
        if saveRequestStatus == .unconfirmed {
            return "Guardado no confirmado. Requiere revisión antes de continuar."
        }
        switch state {
        case .discovered: return "Foto descubierta."
        case .queued: return "Pendiente de análisis."
        case .preparing: return "Preparando una copia para el análisis local."
        case .analyzing:
            return revision > 1
                ? "Analizando de nuevo; el resultado aún no está disponible."
                : "Analizando en este Mac."
        case .validating: return "Validando el resultado local."
        case .ready:
            return proposal.keywords.isEmpty && proposal.caption == nil
                ? "El modelo no generó propuestas. Puedes editar o reanalizar."
                : "Lista para revisar."
        case .edited: return "Edición manual lista para guardar."
        case .saveQueued: return "En cola para guardar."
        case .saving: return "Guardando y verificando."
        case .verified: return "Cambios guardados y verificados."
        case .discarded: return "Descartada; no se guardarán cambios."
        case .failed:
            guard let photo,
                  let failure = QueueReviewFailurePresentation.analysisFailure(for: photo)
            else {
                return "No se pudo completar la operación."
            }
            return "\(failure.reason) No se guardó ningún cambio en Fotos. \(failure.nextAction)"
        case .uncertain: return "Requiere revisión antes de continuar."
        }
    }

    var failurePresentation: QueueReviewFailurePresentation? {
        guard state == .failed, let photo else { return nil }
        return QueueReviewFailurePresentation.analysisFailure(for: photo)
    }

    var thumbnailIdentity: String {
        guard let identifier = photo?.photosLocalIdentifier?
            .trimmingCharacters(in: .whitespacesAndNewlines),
              !identifier.isEmpty,
              identifier != "unknown",
              identifier.count <= 1_024,
              identifier.unicodeScalars.allSatisfy({
                  !CharacterSet.controlCharacters.contains($0)
              }) else {
            return "pending-\(id)"
        }
        return "photo-\(identifier)"
    }

    @discardableResult
    mutating func transition(to newState: QueueReviewItemState) -> Bool {
        guard Self.canTransition(from: state, to: newState) else { return false }
        state = newState
        return true
    }

    @discardableResult
    mutating func completeAnalysis(proposal newProposal: QueueReviewProposal) -> Bool {
        guard state == .analyzing else { return false }

        proposal = newProposal
        if !hasManualEdits {
            draft = QueueReviewDraft(
                keywords: newProposal.keywords,
                caption: newProposal.caption
            )
        }
        state = .ready
        return true
    }

    @discardableResult
    mutating func editDraft(keywords: [String], caption: String?) -> Bool {
        guard saveRequestStatus == nil, state == .ready || state == .edited else { return false }

        draft = QueueReviewDraft(keywords: keywords, caption: caption)
        hasManualEdits = keywords != proposal.keywords || caption != proposal.caption
        state = hasManualEdits ? .edited : .ready
        return true
    }

    mutating func attach(manifestURL: URL, photo: PreviewPhoto) {
        self.manifestURL = manifestURL
        self.photo = photo
        proposal = QueueReviewProposal(
            keywords: photo.proposedKeywords,
            caption: photo.proposedCaption
        )
        if !hasManualEdits {
            draft = QueueReviewDraft(
                keywords: photo.proposedKeywords,
                caption: photo.proposedCaption
            )
        }
    }

    mutating func resetDraftForRescan() {
        proposal = .empty
        draft = QueueReviewDraft(keywords: [], caption: nil)
        hasManualEdits = false
    }

    mutating func applyRemoteState(_ newState: QueueReviewItemState, revision: Int? = nil) {
        if let revision {
            if revision > self.revision {
                saveRequestStatus = nil
                manifestURL = nil
                photo = nil
                proposal = .empty
                if !hasManualEdits {
                    draft = QueueReviewDraft(keywords: [], caption: nil)
                }
            }
            self.revision = revision
        }
        state = newState
    }

    private static func canTransition(
        from currentState: QueueReviewItemState,
        to newState: QueueReviewItemState
    ) -> Bool {
        switch (currentState, newState) {
        case (.discovered, .queued),
             (.queued, .preparing),
             (.queued, .analyzing),
             (.queued, .discarded),
             (.preparing, .analyzing),
             (.preparing, .failed),
             (.analyzing, .validating),
             (.validating, .ready),
             (.analyzing, .ready),
             (.analyzing, .failed),
             (.analyzing, .uncertain),
             (.analyzing, .discarded),
             (.ready, .queued),
             (.ready, .saveQueued),
             (.ready, .discarded),
             (.edited, .queued),
             (.edited, .saveQueued),
             (.saveQueued, .saving),
             (.edited, .discarded),
             (.saving, .verified),
             (.saving, .failed),
             (.saving, .uncertain),
             (.failed, .queued),
             (.failed, .discarded),
             (.uncertain, .queued),
             (.uncertain, .discarded):
            return true
        default:
            return false
        }
    }
}

struct ReviewSessionStore: Equatable, Sendable {
    private(set) var items: [QueueReviewItem]
    private(set) var focusedItemID: String?

    init(items: [QueueReviewItem]) {
        var seenIDs = Set<String>()
        self.items = items.filter { seenIDs.insert($0.id).inserted }
        focusedItemID = self.items.first?.id
    }

    var focusedItem: QueueReviewItem? {
        guard let focusedItemID else { return nil }
        return item(id: focusedItemID)
    }

    func item(id: String) -> QueueReviewItem? {
        items.first { $0.id == id }
    }

    @discardableResult
    mutating func focus(id: String) -> Bool {
        guard item(id: id) != nil else { return false }
        focusedItemID = id
        return true
    }

    @discardableResult
    mutating func beginAnalysis(id: String) -> Bool {
        updateItem(id: id) { $0.transition(to: .analyzing) }
    }

    @discardableResult
    mutating func completeAnalysis(
        id: String,
        proposal: QueueReviewProposal
    ) -> Bool {
        updateItem(id: id) { $0.completeAnalysis(proposal: proposal) }
    }

    @discardableResult
    mutating func editDraft(
        id: String,
        keywords: [String],
        caption: String?
    ) -> Bool {
        updateItem(id: id) {
            $0.editDraft(keywords: keywords, caption: caption)
        }
    }

    @discardableResult
    mutating func queueRescan(id: String, resetManualEdits: Bool = false) -> Bool {
        updateItem(id: id) { item in
            guard item.transition(to: .queued) else { return false }
            if resetManualEdits {
                item.resetDraftForRescan()
            }
            return true
        }
    }

    @discardableResult
    mutating func queuePersistence(id: String) -> Bool {
        updateItem(id: id) { $0.transition(to: .saveQueued) }
    }

    mutating func setSaveRequestStatus(id: String, status: QueueSaveRequestStatus?) {
        _ = updateItem(id: id) {
            $0.saveRequestStatus = status
            return true
        }
    }

    @discardableResult
    mutating func beginPersistence(id: String) -> Bool {
        updateItem(id: id) { $0.transition(to: .saving) }
    }

    @discardableResult
    mutating func completePersistence(id: String) -> Bool {
        transitionAndAdvanceFocus(id: id, to: .verified)
    }

    @discardableResult
    mutating func discard(id: String) -> Bool {
        transitionAndAdvanceFocus(id: id, to: .discarded)
    }

    @discardableResult
    mutating func markFailed(id: String) -> Bool {
        updateItem(id: id) { $0.transition(to: .failed) }
    }

    @discardableResult
    mutating func markBlocked(id: String) -> Bool {
        updateItem(id: id) { $0.transition(to: .uncertain) }
    }

    mutating func applyQueueEvent(
        id: String,
        revision: Int,
        state: QueueReviewItemState,
        manifestURL: URL? = nil,
        photo: PreviewPhoto? = nil
    ) {
        if let index = items.firstIndex(where: { $0.id == id }) {
            guard revision >= items[index].revision else { return }
            items[index].applyRemoteState(state, revision: revision)
            if let manifestURL, let photo {
                items[index].attach(manifestURL: manifestURL, photo: photo)
            }
            advanceFocusPastTerminalItem(id: id)
            return
        }
        var item = QueueReviewItem(id: id, ordinal: items.count, revision: revision)
        item.applyRemoteState(state, revision: revision)
        if let manifestURL, let photo {
            item.attach(manifestURL: manifestURL, photo: photo)
        }
        items.append(item)
        if focusedItemID == nil {
            focusedItemID = id
        }
        advanceFocusPastTerminalItem(id: id)
    }

    mutating func remove(id: String) {
        let removedIndex = items.firstIndex { $0.id == id }
        items.removeAll { $0.id == id }
        guard focusedItemID == id else { return }
        if items.isEmpty {
            focusedItemID = nil
        } else {
            focusedItemID = items[min(removedIndex ?? 0, items.count - 1)].id
        }
    }

    mutating func restoreDiscardedItem(_ previous: QueueReviewItem) {
        _ = updateItem(id: previous.id) { current in
            guard current.state == .discarded, current.revision == previous.revision else { return false }
            current = previous
            return true
        }
    }

    private mutating func updateItem(
        id: String,
        operation: (inout QueueReviewItem) -> Bool
    ) -> Bool {
        guard let index = items.firstIndex(where: { $0.id == id }) else {
            return false
        }
        return operation(&items[index])
    }

    private mutating func transitionAndAdvanceFocus(
        id: String,
        to state: QueueReviewItemState
    ) -> Bool {
        let changed = updateItem(id: id) { $0.transition(to: state) }
        guard changed else { return false }
        advanceFocusPastTerminalItem(id: id)
        return true
    }

    /// Keep the next usable card selected when an action removes the current
    /// card from the mesa. The UI can therefore rotate immediately without
    /// waiting for discovery or a Photos response.
    private mutating func advanceFocusPastTerminalItem(id: String) {
        guard focusedItemID == id,
              let currentIndex = items.firstIndex(where: { $0.id == id }),
              items[currentIndex].state == .verified || items[currentIndex].state == .discarded
        else { return }

        let next = (1..<items.count)
            .map { items[(currentIndex + $0) % items.count] }
            .first { $0.state != .verified && $0.state != .discarded }
        if let next {
            focusedItemID = next.id
        } else {
            focusedItemID = nil
        }
    }
}
