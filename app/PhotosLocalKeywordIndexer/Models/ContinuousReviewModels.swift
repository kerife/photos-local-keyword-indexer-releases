import Foundation

struct ContinuousReviewControls: Equatable, Sendable {
    static let defaults = Self(
        photoCount: 10,
        modelSelection: "adaptive",
        autoAnalyze: true,
        analysisConcurrency: 2,
        includeCaptions: true,
        appleMaps: false,
        isPaused: false
    )

    private(set) var photoCount: Int
    private(set) var modelSelection: String
    var autoAnalyze: Bool
    private(set) var analysisConcurrency: Int
    var includeCaptions: Bool
    var appleMaps: Bool
    var isPaused: Bool

    init(
        photoCount: Int,
        modelSelection: String,
        autoAnalyze: Bool,
        analysisConcurrency: Int,
        includeCaptions: Bool,
        appleMaps: Bool,
        isPaused: Bool
    ) {
        self.photoCount = (1 ... 50).contains(photoCount) ? photoCount : 10
        self.modelSelection = Self.normalizedModel(modelSelection) ?? "adaptive"
        self.autoAnalyze = autoAnalyze
        self.analysisConcurrency = (1 ... 4).contains(analysisConcurrency)
            ? analysisConcurrency
            : 2
        self.includeCaptions = includeCaptions
        self.appleMaps = appleMaps
        self.isPaused = isPaused
    }

    @discardableResult
    mutating func setPhotoCount(_ value: Int) -> Bool {
        guard (1 ... 50).contains(value) else { return false }
        photoCount = value
        return true
    }

    @discardableResult
    mutating func setAnalysisConcurrency(_ value: Int) -> Bool {
        guard (1 ... 4).contains(value) else { return false }
        analysisConcurrency = value
        return true
    }

    @discardableResult
    mutating func setModelSelection(_ value: String) -> Bool {
        guard let value = Self.normalizedModel(value) else { return false }
        modelSelection = value
        return true
    }

    private static func normalizedModel(_ value: String) -> String? {
        let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty,
              normalized.count <= 128,
              normalized.unicodeScalars.allSatisfy({ scalar in
                  switch scalar.properties.generalCategory {
                  case .control, .format, .surrogate:
                      return false
                  default:
                      return true
                  }
              }) else {
            return nil
        }
        return normalized
    }
}

extension QueueRescanProfile {
    var displayName: String {
        switch self {
        case .freeLocal: return "Libre local"
        case .balanced: return "Equilibrado"
        case .conservative: return "Conservador"
        }
    }

    var detail: String {
        switch self {
        case .freeLocal:
            return "Permite inferencias locales amplias y las deja visibles para revisión."
        case .balanced:
            return "Prioriza conceptos útiles con evidencia visual o contextual suficiente."
        case .conservative:
            return "Limita las propuestas a observaciones de alta confianza."
        }
    }
}

struct ContinuousReviewRescanDraft: Equatable, Sendable {
    static let defaultPromptBody = """
    Describe el contenido útil para catalogación fotográfica. Propón keywords concretas y un caption natural basados en evidencia visible y contexto local permitido. No inventes identidades ni hechos que no puedas sostener.
    """

    var model: String
    var profile: QueueRescanProfile
    var layers: QueueRescanLayers
    var additionalInformation: String
    var promptBody: String {
        didSet {
            if promptBody != Self.defaultPromptBody {
                resetPromptRequested = false
            }
        }
    }
    var resetManualEdits: Bool
    private(set) var resetPromptRequested: Bool

    init(model: String) {
        self.model = model
        profile = .freeLocal
        layers = .all
        additionalInformation = ""
        promptBody = Self.defaultPromptBody
        resetManualEdits = false
        resetPromptRequested = false
    }

    mutating func restoreDefaultPrompt() {
        promptBody = Self.defaultPromptBody
        resetPromptRequested = true
    }

    var validatedOptions: QueueRescanOptions? {
        let normalizedModel = model.trimmingCharacters(in: .whitespacesAndNewlines)
        let normalizedInformation = normalizedOptional(additionalInformation)
        let normalizedPrompt = promptBody.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalizedPrompt.isEmpty else { return nil }
        let usesDefaultPrompt = normalizedPrompt == Self.defaultPromptBody
        let options = QueueRescanOptions(
            model: normalizedModel,
            profile: profile,
            layers: layers,
            additionalInformation: normalizedInformation,
            analysisPrompt: usesDefaultPrompt ? nil : normalizedPrompt,
            resetPrompt: usesDefaultPrompt && resetPromptRequested,
            resetEdits: resetManualEdits
        )
        return options.isValid ? options : nil
    }

    var effectivePromptPreview: String {
        let information = normalizedOptional(additionalInformation) ?? "Sin información adicional."
        let layerLines = [
            "lugares: \(activation(layers.places))",
            "documentos y texto: \(activation(layers.documentsText))",
            "personas y accesorios: \(activation(layers.peopleAccessories))",
            "normalización semántica: \(activation(layers.semanticNormalization))",
        ].joined(separator: " · ")
        return """
        Perfil: \(profile.displayName)
        Capas: \(layerLines)
        Información adicional: \(information)
        Ediciones manuales: \(resetManualEdits ? "restablecer al recibir la propuesta nueva" : "conservar")
        Cuerpo analítico:
        \(promptBody)

        La estructura de salida y los límites de privacidad permanecen fijos.
        """
    }

    private func normalizedOptional(_ value: String) -> String? {
        let normalized = value.trimmingCharacters(in: .whitespacesAndNewlines)
        return normalized.isEmpty ? nil : normalized
    }

    private func activation(_ enabled: Bool) -> String {
        enabled ? "activada" : "desactivada"
    }
}

struct ContinuousReviewSummary: Equatable, Sendable {
    let queued: Int
    let analyzing: Int
    let ready: Int
    let saveQueued: Int
    let saving: Int
    let saved: Int
    let discarded: Int
    let attention: Int

    init(items: [QueueReviewItem]) {
        var queued = 0
        var analyzing = 0
        var ready = 0
        var saveQueued = 0
        var saving = 0
        var saved = 0
        var discarded = 0
        var attention = 0
        for item in items {
            switch item.state {
            case .discovered, .queued:
                queued += 1
            case .preparing, .analyzing, .validating:
                analyzing += 1
            case .saveQueued:
                saveQueued += 1
            case .saving:
                saving += 1
            case .ready, .edited:
                ready += 1
            case .verified:
                saved += 1
            case .failed, .uncertain:
                attention += 1
            case .discarded:
                discarded += 1
            }
        }
        self.queued = queued
        self.analyzing = analyzing
        self.ready = ready
        self.saveQueued = saveQueued
        self.saving = saving
        self.saved = saved
        self.discarded = discarded
        self.attention = attention
    }

    var accessibilityLabel: String {
        "En cola: \(queued). Analizando: \(analyzing). Listas: \(ready). Esperando guardado: \(saveQueued). Guardando: \(saving). "
            + "Guardadas: \(saved). Descartadas: \(discarded). Atención: \(attention)."
    }
}

enum ContinuousReviewSectionKind: String, CaseIterable, Equatable, Sendable {
    case active, attention, review, queued

    var title: String {
        switch self {
        case .active: return "En curso"
        case .attention: return "Requieren atención"
        case .review: return "Listas para revisar"
        case .queued: return "En cola"
        }
    }
}

struct ContinuousReviewSection: Identifiable, Equatable, Sendable {
    let kind: ContinuousReviewSectionKind
    let items: [QueueReviewItem]

    var id: String { kind.rawValue }
}

struct ContinuousReviewTable: Equatable, Sendable {
    let autonomyActivities: [AutonomyActivitySnapshot]
    let sections: [ContinuousReviewSection]
    let visibleItems: [QueueReviewItem]
    let focusedItem: QueueReviewItem?

    init(session: ReviewSessionStore, autonomyActivities: [AutonomyActivitySnapshot] = []) {
        self.autonomyActivities = autonomyActivities
            .filter { $0.state.isVisible }
            .sorted { lhs, rhs in
                let left = Self.activityPriority(lhs.state)
                let right = Self.activityPriority(rhs.state)
                return left == right ? lhs.position < rhs.position : left < right
            }
        let nonterminal = session.items.filter {
            $0.state != .verified && $0.state != .discarded
        }
        sections = ContinuousReviewSectionKind.allCases.compactMap { kind in
            let items = nonterminal.filter { Self.section(for: $0) == kind }
                .sorted { lhs, rhs in
                    let left = Self.manualPriority(lhs)
                    let right = Self.manualPriority(rhs)
                    return left == right ? lhs.ordinal < rhs.ordinal : left < right
                }
            return items.isEmpty ? nil : ContinuousReviewSection(kind: kind, items: items)
        }
        visibleItems = sections.flatMap(\.items)
        if let focusedItemID = session.focusedItemID,
           let selected = visibleItems.first(where: { $0.id == focusedItemID }) {
            focusedItem = selected
        } else {
            focusedItem = visibleItems.first
        }
    }

    private static func section(for item: QueueReviewItem) -> ContinuousReviewSectionKind {
        if item.saveRequestStatus == .sending { return .active }
        if item.saveRequestStatus == .unconfirmed || [.failed, .uncertain].contains(item.state) { return .attention }
        switch item.state {
        case .preparing, .analyzing, .validating, .saveQueued, .saving: return .active
        case .edited, .ready: return .review
        case .discovered, .queued: return .queued
        case .verified, .discarded, .failed, .uncertain: return .attention
        }
    }

    private static func manualPriority(_ item: QueueReviewItem) -> Int {
        if item.saveRequestStatus == .sending { return 0 }
        if item.saveRequestStatus == .unconfirmed { return 0 }
        switch item.state {
        case .saving: return 1
        case .saveQueued: return 2
        case .preparing: return 3
        case .analyzing: return 4
        case .validating: return 5
        case .failed, .uncertain: return 6
        case .edited: return 7
        case .ready: return 8
        case .discovered: return 9
        case .queued: return 10
        case .verified, .discarded: return 11
        }
    }

    private static func activityPriority(_ state: AutonomyActivityState) -> Int {
        switch state {
        case .saving: return 0
        case .saveQueued: return 1
        case .preparing: return 2
        case .analyzing: return 3
        case .validating: return 4
        case .settled: return 5
        }
    }
}

struct ContinuousReviewInspectorSnapshot: Equatable, Sendable {
    let attemptID: String
    let uuid: String
    let model: String
    let routingReason: String
    let confidence: String
    let locationContext: String
    let ollamaVersion: String
    let promptVersion: String
    let promptHash: String
    let promptEffective: String
    let placeContext: String
    let stageDurations: String
    let contextSources: String
    let proposalKeywords: String
    let currentKeywords: String
    let reviewDifference: String
    let reviewDifferenceAccessibilityLabel: String
    let errorCodes: [String]

    init(item: QueueReviewItem) {
        let photo = item.photo
        attemptID = Self.bounded(item.id, maximum: 64, fallback: "No disponible")
        uuid = Self.bounded(photo?.uuid, maximum: 64, fallback: "No disponible")
        model = Self.bounded(photo?.modelUsed, maximum: 128, fallback: "No disponible")
        switch photo?.modelReason {
        case "single_policy": routingReason = "Política de modelo único"
        case "no_location": routingReason = "Sin contexto de ubicación"
        case "location_context": routingReason = "Contexto de ubicación"
        default: routingReason = "No disponible"
        }
        if let score = photo?.confidence, score.isFinite, (0 ... 1).contains(score) {
            confidence = "\(Int((score * 100).rounded()))%"
        } else {
            confidence = "No disponible"
        }
        let trace = photo?.technicalTrace
        let lookupState = trace?.placeLookupState
        let evidenceState = trace?.placeEvidenceState
        let placeNames = (trace?.placeContext ?? [])
            .compactMap { Self.boundedOptional($0, maximum: 160) }
        // Derive the human-facing statement from every location signal in the
        // same trace. This keeps the inspector truthful even when an older
        // helper omitted the GPS flag but still recorded an Apple Maps query
        // or sanitized place context.
        let locationWasUsed = trace?.usedGPS == true
            || trace?.usedAppleMaps == true
            || !(trace?.placeContext.isEmpty ?? true)
            || photo?.modelReason == "location_context"
        if locationWasUsed {
            if trace?.usedGPS == true, trace?.usedAppleMaps == true {
                if evidenceState == "discarded_by_visual_evidence" {
                    locationContext = "GPS local y Apple Maps devolvieron lugares, pero se descartaron nombres específicos por falta de evidencia visual; no se guardaron coordenadas."
                } else {
                    switch lookupState {
                    case "timeout":
                        locationContext = placeNames.isEmpty
                            ? "GPS local usado; Apple Maps agotó el tiempo de espera sin contexto sanitizado; no se guardaron coordenadas."
                            : "GPS local usado; Apple Maps agotó el tiempo de espera y se usó contexto parcial sanitizado; no se guardaron coordenadas."
                    case "error":
                        locationContext = "GPS local usado; Apple Maps falló o no pudo completar la consulta; no se guardaron coordenadas."
                    case "results_filtered":
                        locationContext = "GPS local usado; Apple Maps devolvió lugares descartados por sanitización; no se guardaron coordenadas."
                    case "cancelled":
                        locationContext = "GPS local usado; la consulta de Apple Maps fue cancelada; no se guardaron coordenadas."
                    case "no_results":
                        locationContext = "GPS local usado; se intentó consultar Apple Maps pero no devolvió lugares; no se guardaron coordenadas."
                    default:
                        locationContext = placeNames.isEmpty
                            ? "GPS local usado; se intentó consultar Apple Maps pero no devolvió lugares; no se guardaron coordenadas."
                            : "GPS local y Apple Maps usados para orientar el análisis; no se guardaron coordenadas."
                    }
                }
            } else if trace?.usedGPS == true {
                locationContext = "GPS local usado para orientar el análisis; Apple Maps no se consultó y no se guardaron coordenadas."
            } else if trace?.usedAppleMaps == true || !(trace?.placeContext.isEmpty ?? true) {
                locationContext = "Se consultó Apple Maps para orientar el análisis; no se guardaron coordenadas."
            } else {
                locationContext = "Usado para orientar el análisis; no se guardaron coordenadas."
            }
        } else {
            locationContext = "No usado en este análisis."
        }
        ollamaVersion = Self.bounded(trace?.ollamaVersion, maximum: 64, fallback: "No disponible")
        promptVersion = Self.bounded(trace?.promptVersion, maximum: 64, fallback: "No disponible")
        promptHash = Self.bounded(trace?.promptSHA256, maximum: 64, fallback: "No disponible")
        promptEffective = Self.bounded(trace?.promptEffective, maximum: 16_384, fallback: "No disponible")
        if !placeNames.isEmpty {
            placeContext = placeNames.joined(separator: ", ")
        } else if trace?.usedAppleMaps == true {
            switch lookupState {
            case "timeout":
                placeContext = "Apple Maps agotó el tiempo de espera"
            case "error":
                placeContext = "Apple Maps falló o no pudo completar la consulta"
            case "results_filtered":
                placeContext = "Apple Maps devolvió lugares descartados por sanitización"
            case "cancelled":
                placeContext = "Consulta de Apple Maps cancelada"
            default:
                placeContext = "Apple Maps no devolvió lugares"
            }
        } else {
            placeContext = ""
        }
        let durations = trace?.durationsMilliseconds ?? [:]
        let durationOrder = [
            ("metadata", "Metadata"), ("export", "Export"), ("context", "Contexto"),
            ("inference", "Inferencia"), ("postprocess", "Postproceso"), ("total", "Total"),
        ]
        let durationParts = durationOrder.compactMap { key, label -> String? in
            guard let value = durations[key], value >= 0 else { return nil }
            return "\(label) \(value) ms"
        }
        stageDurations = durationParts.isEmpty ? "No disponible" : durationParts.joined(separator: " · ")
        if let trace {
            var sources: [String] = []
            if trace.usedGPS { sources.append("GPS") }
            if trace.usedAppleMaps { sources.append("búsqueda Apple Maps") }
            if trace.usedLandmark { sources.append("landmark local") }
            contextSources = sources.isEmpty ? "Ninguna" : sources.joined(separator: ", ")
        } else {
            contextSources = "No disponible"
        }
        let proposed = item.proposal.keywords.compactMap {
            Self.boundedOptional($0, maximum: 128)
        }
        let current = item.draft.keywords.compactMap {
            Self.boundedOptional($0, maximum: 128)
        }
        proposalKeywords = proposed.isEmpty ? "Ninguna" : proposed.joined(separator: ", ")
        currentKeywords = current.isEmpty ? "Ninguna" : current.joined(separator: ", ")
        let proposedKeys = Set(proposed.map(Self.reviewKey))
        let currentKeys = Set(current.map(Self.reviewKey))
        let added = current.filter { !proposedKeys.contains(Self.reviewKey($0)) }
        let removed = proposed.filter { !currentKeys.contains(Self.reviewKey($0)) }
        let captionChanged = item.draft.caption != item.proposal.caption
        var visibleDifference: [String] = []
        var accessibleDifference: [String] = []
        if !added.isEmpty {
            visibleDifference.append("Agregadas: \(added.joined(separator: ", "))")
            accessibleDifference.append("\(added.count) keyword\(added.count == 1 ? "" : "s") agregada\(added.count == 1 ? "" : "s")")
        }
        if !removed.isEmpty {
            visibleDifference.append("eliminadas: \(removed.joined(separator: ", "))")
            accessibleDifference.append("\(removed.count) keyword\(removed.count == 1 ? "" : "s") eliminada\(removed.count == 1 ? "" : "s")")
        }
        if captionChanged {
            visibleDifference.append("caption modificado")
            accessibleDifference.append("caption modificado")
        }
        reviewDifference = visibleDifference.isEmpty
            ? "Sin ediciones manuales."
            : visibleDifference.joined(separator: " · ") + "."
        reviewDifferenceAccessibilityLabel = accessibleDifference.isEmpty
            ? "Sin ediciones manuales."
            : accessibleDifference.joined(separator: ". ") + "."
        errorCodes = Array(Set((photo?.errors ?? []).map(\.code))).sorted()
    }

    var accessibilityLabel: String {
        let errors = errorCodes.isEmpty ? "Ninguno" : errorCodes.joined(separator: ", ")
        return "Inspector técnico. Intento: \(attemptID). UUID: \(uuid). Modelo: \(model). "
            + "Routing: \(routingReason). Confianza: \(confidence). "
            + "Contexto de ubicación: \(locationContext). Versión de Ollama: \(ollamaVersion). "
            + "Versión del prompt: \(promptVersion). Duraciones: \(stageDurations). "
            + "Diferencia de revisión: \(reviewDifferenceAccessibilityLabel) Códigos: \(errors)."
    }

    private static func bounded(
        _ value: String?,
        maximum: Int,
        fallback: String
    ) -> String {
        guard let value else { return fallback }
        let compact = value
            .split(whereSeparator: \Character.isWhitespace)
            .joined(separator: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !compact.isEmpty,
              compact.unicodeScalars.allSatisfy({ scalar in
                  switch scalar.properties.generalCategory {
                  case .control, .format, .surrogate:
                      return false
                  default:
                      return true
                  }
              }) else {
            return fallback
        }
        return String(compact.prefix(maximum))
    }

    private static func boundedOptional(_ value: String, maximum: Int) -> String? {
        let bounded = bounded(value, maximum: maximum, fallback: "")
        return bounded.isEmpty ? nil : bounded
    }

    private static func reviewKey(_ value: String) -> String {
        value.folding(
            options: [.caseInsensitive, .diacriticInsensitive],
            locale: Locale(identifier: "es_MX")
        )
    }
}

enum ContinuousReviewPreparationPhase: Equatable, Sendable {
    case ready
    case checking
    case blocked
}

enum ContinuousReviewPreparationIssue: Equatable, Sendable {
    case photosAccess
    case photosAutomation
    case ollama
    case model
    case photoScript
    case helper
}

struct ContinuousReviewPreparation: Equatable, Sendable {
    let phase: ContinuousReviewPreparationPhase
    let title: String
    let detail: String
    let actionTitle: String
    let actionAccessibilityHint: String
    let symbolName: String

    static let ready = Self(
        phase: .ready,
        title: "Preparación lista",
        detail: "Ollama, el modelo y el acceso de lectura están listos.",
        actionTitle: "",
        actionAccessibilityHint: "",
        symbolName: "checkmark.shield"
    )

    static let checking = Self(
        phase: .checking,
        title: "Comprobando preparación local",
        detail: "Espera a que terminen las comprobaciones antes de iniciar trabajo nuevo.",
        actionTitle: "Comprobando…",
        actionAccessibilityHint: "La comprobación local sigue en curso.",
        symbolName: "clock.arrow.circlepath"
    )

    static func blocked(_ issue: ContinuousReviewPreparationIssue) -> Self {
        switch issue {
        case .photosAccess:
            return Self(
                phase: .blocked,
                title: "Falta acceso a Fotos",
                detail: "Concede acceso de lectura a Fotos para cargar y volver a validar imágenes.",
                actionTitle: "Abrir configuración de Fotos",
                actionAccessibilityHint: "Abre el panel local de privacidad de Fotos.",
                symbolName: "photo.badge.exclamationmark"
            )
        case .photosAutomation:
            return Self(
                phase: .blocked,
                title: "Falta Automatización de Fotos",
                detail: "Autoriza al helper firmado para guardar únicamente cambios confirmados.",
                actionTitle: "Abrir configuración de Automatización",
                actionAccessibilityHint: "Abre el panel local de Automatización.",
                symbolName: "gear.badge"
            )
        case .ollama:
            return Self(
                phase: .blocked,
                title: "Ollama no está disponible",
                detail: "Inicia el servicio local de Ollama y vuelve a comprobar.",
                actionTitle: "Comprobar de nuevo",
                actionAccessibilityHint: "Comprueba Ollama localmente sin abrir Fotos.",
                symbolName: "network.slash"
            )
        case .model:
            return Self(
                phase: .blocked,
                title: "Falta un modelo local",
                detail: "Instala o selecciona un modelo de visión local compatible.",
                actionTitle: "Comprobar modelos",
                actionAccessibilityHint: "Comprueba los modelos instalados sin descargar ninguno.",
                symbolName: "shippingbox"
            )
        case .photoScript:
            return Self(
                phase: .blocked,
                title: "PhotoScript no está disponible",
                detail: "Comprueba la compatibilidad local de Photos, PhotoScript y macOS.",
                actionTitle: "Comprobar de nuevo",
                actionAccessibilityHint: "Comprueba PhotoScript localmente sin modificar Fotos.",
                symbolName: "applescript"
            )
        case .helper:
            return Self(
                phase: .blocked,
                title: "Helper local no disponible",
                detail: "Reinstala la app o usa una build válida antes de continuar.",
                actionTitle: "Comprobar de nuevo",
                actionAccessibilityHint: "Comprueba el helper firmado sin usar un intérprete externo.",
                symbolName: "exclamationmark.shield"
            )
        }
    }

    var allowsQueueWork: Bool { phase == .ready }
}

struct ContinuousReviewActions: Equatable, Sendable {
    let canEdit: Bool
    let canSave: Bool
    let canDiscard: Bool
    let canRescan: Bool

    init(item: QueueReviewItem) {
        canEdit = item.saveRequestStatus == nil && [.ready, .edited].contains(item.state)
        canSave = canEdit && (
            !item.draft.keywords.isEmpty
                || item.draft.caption?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
        )
        canDiscard = item.saveRequestStatus == nil && ![.saveQueued, .saving, .verified, .discarded].contains(item.state)
        canRescan = item.saveRequestStatus == nil && (
            [.ready, .edited, .uncertain].contains(item.state) || item.failurePresentation != nil
        )
    }
}

enum QueueReviewEditPolicy {
    static func addingKeyword(_ rawValue: String, to current: [String]) -> [String]? {
        guard current.count < 8 else { return nil }
        let value = rawValue
            .split(whereSeparator: \Character.isWhitespace)
            .joined(separator: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard isSafe(value, maximumCharacters: 128), !value.isEmpty else { return nil }
        let key = canonicalKey(value)
        guard !current.contains(where: { canonicalKey($0) == key }) else { return nil }
        return current + [value]
    }

    static func caption(_ rawValue: String) -> String? {
        let value = rawValue.trimmingCharacters(in: .whitespacesAndNewlines)
        guard isSafe(value, maximumCharacters: 240), !value.isEmpty else { return nil }
        return value
    }

    private static func canonicalKey(_ value: String) -> String {
        value.folding(
            options: [.caseInsensitive, .diacriticInsensitive],
            locale: Locale(identifier: "es_MX")
        )
    }

    private static func isSafe(_ value: String, maximumCharacters: Int) -> Bool {
        guard value.count <= maximumCharacters else { return false }
        return value.unicodeScalars.allSatisfy { scalar in
            switch scalar.properties.generalCategory {
            case .control, .format, .surrogate:
                return false
            default:
                return true
            }
        }
    }
}
