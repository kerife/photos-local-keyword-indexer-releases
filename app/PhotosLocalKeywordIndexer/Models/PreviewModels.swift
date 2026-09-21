import Foundation
import Darwin
import CryptoKit

struct ManifestPreviewError: Codable, Hashable, Sendable {
    let stage: String
    let code: String
}

struct PreviewTechnicalTrace: Decodable, Hashable, Sendable {
    let promptEffective: String
    let promptVersion: String
    let promptSHA256: String
    let ollamaVersion: String?
    let usedGPS: Bool
    let usedAppleMaps: Bool
    let usedLandmark: Bool
    let placeContext: [String]
    let placeLookupState: String?
    let placeEvidenceState: String?
    let durationsMilliseconds: [String: Int]

    private enum CodingKeys: String, CodingKey {
        case promptEffective = "prompt_effective"
        case promptVersion = "prompt_version"
        case promptSHA256 = "prompt_sha256"
        case ollamaVersion = "ollama_version"
        case usedGPS = "used_gps"
        case usedAppleMaps = "used_apple_maps"
        case usedLandmark = "used_landmark"
        case placeContext = "place_context"
        case placeLookupState = "place_lookup_state"
        case placeEvidenceState = "place_evidence_state"
        case durationsMilliseconds = "durations_ms"
    }

    init(
        promptEffective: String,
        promptVersion: String,
        promptSHA256: String,
        ollamaVersion: String? = nil,
        usedGPS: Bool,
        usedAppleMaps: Bool,
        usedLandmark: Bool,
        placeContext: [String],
        placeLookupState: String? = nil,
        placeEvidenceState: String? = nil,
        durationsMilliseconds: [String: Int]
    ) {
        self.promptEffective = promptEffective
        self.promptVersion = promptVersion
        self.promptSHA256 = promptSHA256
        self.ollamaVersion = ollamaVersion
        self.usedGPS = usedGPS
        self.usedAppleMaps = usedAppleMaps
        self.usedLandmark = usedLandmark
        self.placeContext = placeContext
        self.placeLookupState = placeLookupState
        self.placeEvidenceState = placeEvidenceState
        self.durationsMilliseconds = durationsMilliseconds
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        promptEffective = try container.decode(String.self, forKey: .promptEffective)
        promptVersion = try container.decode(String.self, forKey: .promptVersion)
        promptSHA256 = try container.decode(String.self, forKey: .promptSHA256)
        ollamaVersion = try container.decodeIfPresent(String.self, forKey: .ollamaVersion)
        usedGPS = try container.decode(Bool.self, forKey: .usedGPS)
        usedAppleMaps = try container.decode(Bool.self, forKey: .usedAppleMaps)
        usedLandmark = try container.decode(Bool.self, forKey: .usedLandmark)
        placeContext = try container.decode([String].self, forKey: .placeContext)
        placeLookupState = try container.decodeIfPresent(String.self, forKey: .placeLookupState)
        placeEvidenceState = try container.decodeIfPresent(String.self, forKey: .placeEvidenceState)
        durationsMilliseconds = try container.decode([String: Int].self, forKey: .durationsMilliseconds)
    }
}

struct PreviewPhoto: Decodable, Hashable, Identifiable, Sendable {
    let uuid: String
    let photosLocalIdentifier: String?
    let title: String
    let date: String
    let existingKeywords: [String]
    let proposedKeywords: [String]
    let containsPeople: Bool?
    let containsText: Bool?
    let confidence: Double?
    let modelUsed: String?
    let modelReason: String?
    let state: String
    let applyState: String
    let appliedKeywords: [String]
    let mutationDigest: String?
    let rollbackState: String
    let rolledBackKeywords: [String]
    let rollbackDigest: String?
    let proposedCaption: String?
    let appliedCaption: String?
    let captionState: String
    let errors: [ManifestPreviewError]
    let technicalTrace: PreviewTechnicalTrace?

    var id: String { uuid }

    var stateLabel: String {
        switch state {
        case "ready": return "Listo para revisar"
        case "noop":
            return hasCaptionProposal
                ? "Sin cambios de keywords; caption para revisar"
                : "Sin cambios de keywords"
        case "analysis_failed": return "Análisis fallido"
        case "cancelled": return "Cancelado"
        default: return "Estado no disponible"
        }
    }

    var applyStateLabel: String {
        switch applyState {
        case "not_run": return "No aplicado"
        case "writing": return "Escribiendo"
        case "verified": return "Aplicado y verificado"
        case "noop": return "Sin cambios"
        case "failed": return "Aplicación fallida"
        case "uncertain": return "Requiere revisión manual"
        case "cancelled": return "Cancelado"
        default: return "Estado no disponible"
        }
    }

    /// Distinguish a photo that never reached inference from a row whose
    /// model metadata is unavailable. This keeps the visible model column
    /// accurate after an analysis/export failure.
    var modelDisplayText: String {
        if let model = modelUsed {
            let normalized = model.trimmingCharacters(in: .whitespacesAndNewlines)
            if OllamaModelPresentationPolicy.isValidModelName(normalized) {
                return ModelPresentationCopy.display(normalized)
            }
        }
        return ["analysis_failed", "cancelled"].contains(state)
            ? "Sin inferencia"
            : "Modelo no disponible"
    }

    var reviewStatusText: String {
        var statuses = [stateLabel]
        if applyState != "not_run" {
            statuses.append("Aplicación: \(applyStateLabel)")
        }
        if let captionStateLabel {
            statuses.append("Caption: \(captionStateLabel)")
        }
        if rollbackState != "not_run" {
            statuses.append("Rollback: \(rollbackStateLabel)")
        }
        return statuses.joined(separator: " · ")
    }

    var filteredScanErrors: [ManifestPreviewError] {
        errors.filter { $0.stage != "apply" && $0.stage != "rollback" }
    }

    private var rollbackStateLabel: String {
        switch rollbackState {
        case "not_run": return "No ejecutado"
        case "removing": return "Revirtiendo"
        case "verified_removed": return "Revertido y verificado"
        case "failed": return "Reversión fallida"
        case "uncertain": return "Requiere revisión manual"
        case "casing_conflict": return "Conflicto de mayúsculas"
        case "already_absent": return "Ya no estaba presente"
        case "cancelled": return "Cancelado"
        default: return "Estado no disponible"
        }
    }

    /// Label for the primary photo-selection toggle.
    ///
    /// Keep this intentionally limited to the title. Date, workflow state,
    /// model and confidence are exposed once by the containing row; including
    /// them here makes VoiceOver repeat the same context before the control.
    var accessibilityToggleLabel: String {
        if !isReviewSelectable {
            return "Foto no seleccionable: \(accessibilityDisplayTitle)"
        }
        if proposedKeywords.isEmpty {
            return "Keywords no disponibles para esta foto: \(accessibilityDisplayTitle)"
        }
        return "Seleccionar keywords de la foto: \(accessibilityDisplayTitle)"
    }

    /// A reviewed manifest has already fixed its mutation scope. Disabled
    /// controls remain visible for audit, but VoiceOver must not announce
    /// them as actions that can still change the selection.
    func photoSelectionAccessibilityLabel(selectionIsFixed: Bool) -> String {
        selectionIsFixed
            ? "Keywords registradas en manifiesto revisado: \(accessibilityDisplayTitle)"
            : accessibilityToggleLabel
    }

    var accessibilityToggleHint: String {
        if !isReviewSelectable {
            return "Esta foto no se puede seleccionar por su estado actual; revisa el estado del run."
        }
        if proposedKeywords.isEmpty {
            return "Esta foto solo tiene un caption; apruébalo con su control independiente."
        }
        return "Selecciona o quita únicamente las keywords propuestas; el caption se aprueba por separado."
    }

    /// Label for a proposed keyword approval toggle.
    ///
    /// The Toggle announces its selected state separately; this label names
    /// the approval action without repeating the photo row metadata.
    func keywordAccessibilityLabel(for keyword: String) -> String {
        let displayKeyword = accessibilityDisplayText(keyword, fallback: "Keyword sin texto")
        if reviewSelectionBlockReason != nil {
            return "Keyword no aprobable: \(displayKeyword)"
        }
        return "Aprobar keyword propuesta: \(displayKeyword)"
    }

    func keywordAccessibilityLabel(for keyword: String, selectionIsFixed: Bool) -> String {
        guard selectionIsFixed else { return keywordAccessibilityLabel(for: keyword) }
        let displayKeyword = accessibilityDisplayText(keyword, fallback: "Keyword sin texto")
        return "Keyword registrada en manifiesto revisado: \(displayKeyword)"
    }

    var keywordAccessibilityHint: String {
        if let reviewSelectionBlockReason {
            return "Esta keyword queda en solo lectura: \(reviewSelectionBlockReason). Revisa el estado del run."
        }
        return "Se escribirá únicamente después de revisar y confirmar la aplicación."
    }

    /// Label for the explicit caption approval toggle.
    var captionAccessibilityLabel: String {
        let displayCaption = accessibilityDisplayText(proposedCaption, fallback: "Caption sin texto")
        if captionState == "proposed" {
            switch applyState {
            case "failed":
                return "Caption: No aplicado: aplicación fallida: \(displayCaption)"
            case "uncertain":
                return "Caption requiere revisión manual: \(displayCaption)"
            case "cancelled":
                return "Caption no aplicado; operación cancelada: \(displayCaption)"
            case "writing":
                return "Caption con escritura incompleta: \(displayCaption)"
            default:
                break
            }
        }
        if reviewSelectionBlockReason != nil {
            return "Caption no aprobable: \(displayCaption)"
        }
        switch captionState {
        case "preserved": return "Caption conservado: \(displayCaption)"
        case "verified": return "Caption aplicado y verificado: \(displayCaption)"
        case "removed": return "Caption retirado y verificado: \(displayCaption)"
        case "failed": return "Caption con aplicación fallida: \(displayCaption)"
        case "uncertain": return "Caption requiere revisión manual: \(displayCaption)"
        default: return "Aprobar caption propuesto: \(displayCaption)"
        }
    }

    func captionControlAccessibilityLabel(selectionIsFixed: Bool) -> String {
        guard selectionIsFixed else { return captionAccessibilityLabel }
        let displayCaption = accessibilityDisplayText(proposedCaption, fallback: "Caption sin texto")
        return "Caption registrado en manifiesto revisado: \(displayCaption)"
    }

    func reviewControlAccessibilityHint(selectionIsFixed: Bool) -> String {
        selectionIsFixed
            ? "La selección ya está fijada en este manifiesto revisado; consulta el alcance y usa la acción principal solo si el run permite continuar."
            : keywordAccessibilityHint
    }

    var captionAccessibilityHint: String {
        if captionState == "proposed" {
            switch applyState {
            case "failed":
                return "El caption no se aplicó porque la foto falló; el reintento procesará solo las filas fallidas."
            case "uncertain":
                return "El caption no pudo verificarse; requiere revisión manual y no se reintentará automáticamente."
            case "cancelled":
                return "El caption no se aplicó porque la operación se canceló; revisa el run antes de reintentar."
            case "writing":
                return "La escritura del caption quedó incompleta; revisa el run antes de otra mutación."
            default:
                break
            }
        }
        switch captionState {
        case "preserved":
            return "Este caption ya se conservó porque Fotos tenía uno; no se escribirá de nuevo."
        case "verified":
            return "Este caption ya se aplicó y verificó; no se volverá a escribir desde esta revisión."
        case "removed":
            return "Este caption ya se retiró y verificó; revisa el historial para consultar el resultado."
        case "failed":
            return "La aplicación de este caption falló; revisa el run y ejecuta un dry-run nuevo."
        case "uncertain":
            return "La aplicación de este caption requiere revisión manual; no se reintentará automáticamente."
        default:
            if let reviewSelectionBlockReason {
                return "Este caption queda en solo lectura: \(reviewSelectionBlockReason). Revisa el estado del run."
            }
            return CaptionReviewCopy.accessibilityHint
        }
    }

    /// Caption outcomes are independent from the aggregate apply state. Keep
    /// them visible in the row so a preserved or uncertain caption cannot
    /// look like a fully verified mutation.
    var captionStateLabel: String? {
        switch captionState {
        case "not_requested": return nil
        case "proposed":
            switch applyState {
            case "failed": return "No aplicado: aplicación fallida"
            case "uncertain": return "Requiere revisión manual"
            case "cancelled": return "No aplicado: operación cancelada"
            case "writing": return "Escritura incompleta"
            default: return "Pendiente de aprobación"
            }
        case "preserved": return "Conservado: ya existía un caption"
        case "verified": return "Aplicado y verificado"
        case "removed": return "Retirado y verificado"
        case "failed": return "Aplicación fallida"
        case "uncertain": return "Requiere revisión manual"
        default: return "Estado no disponible"
        }
    }

    /// Mirror the Python manifest gate for caption transitions that cannot
    /// safely be treated as a pending or verified mutation. Keeping this
    /// explicit prevents History from offering Apply for a malformed
    /// reviewed row that the helper will reject before touching Photos.
    var hasInvalidCaptionState: Bool {
        (captionState == "not_requested" && (proposedCaption != nil || appliedCaption != nil))
            || (captionState == "preserved" && applyState == "not_run")
            || (captionState == "proposed" && applyState != "not_run")
            || (captionState == "failed" && ["not_run", "noop"].contains(applyState))
            || (captionState != "not_requested"
                && applyState == "not_run"
                && rollbackState == "not_run"
                && hasInvalidFreshReviewCaptionState)
    }

    /// A fresh scan can only enter review with no prior caption outcome. The
    /// Python manifest validator enforces this, but the Swift history decoder
    /// intentionally remains permissive for audit display; mirror the gate
    /// here so malformed terminal caption states cannot reach approval.
    var hasInvalidFreshReviewCaptionState: Bool {
        switch captionState {
        case "not_requested":
            return proposedCaption != nil || appliedCaption != nil
        case "proposed":
            return proposedCaption == nil || appliedCaption != nil
        default:
            return true
        }
    }

    /// Consolidated, deterministic metadata for VoiceOver on the photo row.
    ///
    /// Deliberately excludes UUIDs, proposed captions and location data. The
    /// individual controls remain available to VoiceOver as children of the
    /// row, while this label provides the row-level context first.
    var accessibilityLabel: String {
        let displayTitle = accessibilityDisplayTitle
        let displayDate = date.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
            ? "Fecha no disponible"
            : date.trimmingCharacters(in: .whitespacesAndNewlines)
        var components = [
            displayTitle,
            "Fecha: \(displayDate)",
            "Estado: \(stateLabel)",
            "Aplicación: \(applyStateLabel)",
        ]
        components.append("Modelo: \(modelDisplayText)")
        if let captionStateLabel {
            components.append("Caption: \(captionStateLabel)")
        }
        if shouldDisplayConfidence {
            let confidence = confidence
            if confidenceBand == .unavailable {
                components.append("Confianza: No disponible")
            } else if let confidence {
                components.append("Confianza: \(Int((confidence * 100).rounded()))% (\(confidenceBand.label))")
            }
        }
        return components.joined(separator: ". ") + "."
    }

    private var accessibilityDisplayTitle: String {
        accessibilityDisplayText(title, fallback: "Foto sin título")
    }

    private func accessibilityDisplayText(_ value: String?, fallback: String) -> String {
        let compactValue = (value ?? "")
            .split(whereSeparator: \.isWhitespace)
            .joined(separator: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !compactValue.isEmpty else { return fallback }
        return String(compactValue.prefix(160))
    }

    private enum CodingKeys: String, CodingKey {
        case uuid
        case photosLocalIdentifier = "photos_local_identifier"
        case title
        case date
        case existingKeywords = "existing_keywords"
        case proposedKeywords = "proposed_keywords"
        case containsPeople = "contains_people"
        case containsText = "contains_text"
        case confidence
        case modelUsed = "model_used"
        case modelReason = "model_reason"
        case state = "scan_state"
        case applyState = "apply_state"
        case appliedKeywords = "applied_keywords"
        case mutationDigest = "mutation_digest"
        case rollbackState = "rollback_state"
        case rolledBackKeywords = "rolled_back_keywords"
        case rollbackDigest = "rollback_digest"
        case proposedCaption = "proposed_caption"
        case appliedCaption = "applied_caption"
        case captionState = "caption_state"
        case errors
        case technicalTrace = "technical_trace"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let localIdentifier = try container.decodeIfPresent(String.self, forKey: .photosLocalIdentifier) ?? "unknown"
        uuid = try container.decodeIfPresent(String.self, forKey: .uuid)
            ?? "sin-uuid-\(String(localIdentifier.suffix(8)))"
        photosLocalIdentifier = try container.decodeIfPresent(String.self, forKey: .photosLocalIdentifier)
        title = try container.decodeIfPresent(String.self, forKey: .title) ?? ""
        date = try container.decode(String.self, forKey: .date)
        existingKeywords = try container.decode([String].self, forKey: .existingKeywords)
        proposedKeywords = try container.decode([String].self, forKey: .proposedKeywords)
        containsPeople = try container.decodeIfPresent(Bool.self, forKey: .containsPeople)
        containsText = try container.decodeIfPresent(Bool.self, forKey: .containsText)
        confidence = try container.decodeIfPresent(Double.self, forKey: .confidence)
        modelUsed = try container.decodeIfPresent(String.self, forKey: .modelUsed)
        modelReason = try container.decodeIfPresent(String.self, forKey: .modelReason)
        state = try container.decode(String.self, forKey: .state)
        applyState = try container.decode(String.self, forKey: .applyState)
        appliedKeywords = try container.decodeIfPresent([String].self, forKey: .appliedKeywords) ?? []
        mutationDigest = try container.decodeIfPresent(String.self, forKey: .mutationDigest)
        rollbackState = try container.decode(String.self, forKey: .rollbackState)
        rolledBackKeywords = try container.decodeIfPresent([String].self, forKey: .rolledBackKeywords) ?? []
        rollbackDigest = try container.decodeIfPresent(String.self, forKey: .rollbackDigest)
        proposedCaption = try container.decodeIfPresent(String.self, forKey: .proposedCaption)
        appliedCaption = try container.decodeIfPresent(String.self, forKey: .appliedCaption)
        captionState = try container.decodeIfPresent(String.self, forKey: .captionState) ?? "not_requested"
        errors = try container.decodeIfPresent([ManifestPreviewError].self, forKey: .errors) ?? []
        technicalTrace = try container.decodeIfPresent(PreviewTechnicalTrace.self, forKey: .technicalTrace)
    }

    init(
        uuid: String,
        photosLocalIdentifier: String? = nil,
        title: String = "",
        date: String,
        existingKeywords: [String],
        proposedKeywords: [String],
        containsPeople: Bool? = nil,
        containsText: Bool? = nil,
        confidence: Double?,
        modelUsed: String?,
        modelReason: String? = nil,
        state: String,
        applyState: String = "not_run",
        appliedKeywords: [String] = [],
        mutationDigest: String? = nil,
        rollbackState: String = "not_run",
        rolledBackKeywords: [String] = [],
        rollbackDigest: String? = nil,
        proposedCaption: String? = nil,
        appliedCaption: String? = nil,
        captionState: String? = nil,
        errors: [ManifestPreviewError] = [],
        technicalTrace: PreviewTechnicalTrace? = nil
    ) {
        self.uuid = uuid
        self.photosLocalIdentifier = photosLocalIdentifier
        self.title = title
        self.date = date
        self.existingKeywords = existingKeywords
        self.proposedKeywords = proposedKeywords
        self.containsPeople = containsPeople
        self.containsText = containsText
        self.confidence = confidence
        self.modelUsed = modelUsed
        self.modelReason = modelReason
        self.state = state
        self.applyState = applyState
        self.appliedKeywords = appliedKeywords
        self.mutationDigest = mutationDigest
        self.rollbackState = rollbackState
        self.rolledBackKeywords = rolledBackKeywords
        self.rollbackDigest = rollbackDigest
        self.proposedCaption = proposedCaption
        self.appliedCaption = appliedCaption
        // Convenience-created rows should reflect the same invariant as the
        // manifest writer: a supplied proposal is pending approval. Keep an
        // explicitly supplied state untouched so malformed/test data remains
        // fail-closed in the review gates.
        self.captionState = captionState ?? (proposedCaption != nil ? "proposed" : "not_requested")
        self.errors = errors
        self.technicalTrace = technicalTrace
    }
}

/// Copy shown next to each proposed caption. Captions have a different
/// write rule from keywords: they are independently approved and are
/// preserved when Photos already has a description.
enum CaptionReviewCopy {
    static let title = "Caption propuesto (solo si no existe uno)"
    static let detail = "Se aprueba por separado y se escribe únicamente si la foto no tiene caption; un caption existente se conserva."
    static let accessibilityHint = "Aprueba este caption de forma independiente; solo se escribirá después de confirmar y si la foto no tiene un caption existente. Los captions existentes se conservan."
}

/// Boundaries shown immediately before a mutating action. Keep this copy
/// centralized so the visual and accessibility descriptions cannot drift.
enum PhotoMetadataProtectionCopy {
    static let applyText = "No se tocarán originales, títulos, fechas, ubicaciones, álbumes, favoritos, caras ni identidades de Personas y mascotas."
    static let applyAccessibility = "Protección de Apple Fotos: no se modificarán originales, títulos, fechas, ubicaciones, álbumes, favoritos, caras ni identidades de Personas y mascotas."
    static let rollbackText = "Rollback: solo keywords y captions registrados y verificados por este run. No se eliminarán variantes de mayúsculas ni cambios externos posteriores."
    static let rollbackAccessibility = "Alcance del rollback: solo keywords y captions registrados y verificados por este run. No se eliminarán variantes de mayúsculas ni cambios externos posteriores."
}

/// Disabled confirmation buttons still need to explain the safe next step to
/// VoiceOver users. The copy is centralized so Apply and Rollback do not drift
/// from the corresponding empty states.
enum MutationConfirmationCopy {
    static let disabledApplyHint = "No hay cambios aprobados; vuelve a la revisión y selecciona al menos una keyword o caption."
    static let disabledRollbackHint = "No hay cambios verificados para eliminar en este run; conserva el historial y revisa el estado manualmente."
    static let preApplySafetyTitle = "Antes de escribir en Fotos"
    static let preApplySafetyDetail = "Para la primera escritura, usa una biblioteca de Fotos de prueba y conserva un respaldo reciente. La app no valida tu biblioteca personal automáticamente."
    static let preApplySafetyAccessibilityLabel = "Antes de escribir en Fotos. Para la primera escritura, usa una biblioteca de Fotos de prueba y conserva un respaldo reciente. La app no valida tu biblioteca personal automáticamente."
}

/// Human-readable confidence bands make the review decision legible without
/// changing the numeric confidence persisted by the helper. The thresholds
/// deliberately mirror the policy boundary at 0.60 and reserve the top band
/// for proposals that need the least manual scrutiny.
enum ReviewConfidenceBand: String, Equatable, Sendable {
    case high
    case medium
    case low
    case unavailable

    var label: String {
        switch self {
        case .high: return "alta"
        case .medium: return "media"
        case .low: return "baja"
        case .unavailable: return "no disponible"
        }
    }

    var systemImage: String {
        switch self {
        case .high: return "checkmark.seal.fill"
        case .medium: return "exclamationmark.circle"
        case .low: return "exclamationmark.triangle.fill"
        case .unavailable: return "questionmark.circle"
        }
    }
}

struct RunManifestPreview: Decodable, Equatable, Sendable {
    let runID: String
    let createdAt: String
    /// The schema is intentionally retained at the UI boundary. Legacy
    /// schema-1/2 scans cannot authorize mutation, even if an imported file
    /// happens to contain post-apply-looking fields.
    let schemaVersion: Int?
    let scanStatus: String
    /// Reviewed copies are already scoped for mutation and must not enter the
    /// review workflow a second time.
    let reviewedFromRunID: String?
    let scanDigest: String?
    let sourceScanDigest: String?
    let selectionStrategy: String
    /// PhotoKit access is captured in the scan selection so a later review
    /// cannot imply full-library coverage after a limited authorization.
    let photosAccess: String
    let captionsRequested: Bool
    let photos: [PreviewPhoto]
    /// Run-level failures are kept as stage/code pairs so History can explain
    /// fatal preflight and validation outcomes without exposing raw messages.
    let runErrors: [ManifestPreviewError]

    var selectionStrategyText: String {
        switch selectionStrategy {
        case "random": return "Selección: fotos elegibles seleccionadas al azar."
        case "targeted": return "Selección: fotos elegibles dirigidas."
        case "recent": return "Selección: fotos recientes elegibles."
        default: return "Selección: alcance no disponible."
        }
    }

    var selectionStrategySystemImage: String {
        switch selectionStrategy {
        case "random": return "shuffle"
        case "targeted": return "scope"
        case "recent": return "clock"
        default: return "questionmark.circle"
        }
    }

    /// Keep the authorization scope visible after leaving Preparación. The
    /// value is fixed policy copy, never a path, coordinate or photo detail.
    var photosAccessText: String? {
        switch photosAccess {
        case "authorized": return "Acceso a Fotos: biblioteca disponible."
        case "limited": return "Acceso a Fotos limitado: solo se analizaron las fotos visibles."
        default: return nil
        }
    }

    var photosAccessSystemImage: String {
        photosAccess == "limited"
            ? "person.crop.circle.badge.exclamationmark"
            : "checkmark.shield"
    }

    var photosAccessAccessibilityLabel: String? {
        photosAccessText
    }

    /// Explain an explicitly requested caption pass without exposing model
    /// output.  A missing value remains nil for legacy runs that did not
    /// record the option.
    var captionRequestText: String? {
        guard captionsRequested else { return nil }
        let count = photos.filter {
            $0.isReviewSelectable
                && $0.hasCaptionProposal
                && ($0.applyState == "not_run" || $0.isRetryableApplyRow)
        }.count
        guard count > 0 else {
            return "Captions activados: no hubo propuestas revisables."
        }
        let label = count == 1 ? "caption revisable" : "captions revisables"
        return "Captions activados: \(count) \(label)."
    }

    /// Human-readable status for the review screen. Keep the raw manifest
    /// value private to the UI so an unknown future status is never exposed
    /// as an implementation detail to users or assistive technologies.
    var scanStatusLabel: String {
        switch scanStatus {
        case "ready": return "Listo para revisar"
        case "ready_with_errors": return "Listo con advertencias"
        case "failed": return "Análisis fallido"
        case "interrupted": return "Análisis interrumpido"
        case "cancelled": return "Análisis cancelado"
        default: return "Estado no disponible"
        }
    }

    var scanStatusAccessibilityLabel: String {
        "Estado de la ejecución: \(scanStatusLabel)"
    }

    /// A run can enter review only after a completed, unmutated dry-run.
    /// Reviewed copies and manifests with mutation state are already in a
    /// later workflow phase and cannot be reviewed again.
    var canPrepareReview: Bool {
        guard reviewedFromRunID == nil,
              scanStatus == "ready" || scanStatus == "ready_with_errors",
              !photos.isEmpty else {
            return false
        }
        // An older app may decode a future scan row so it can still show the
        // run in History, but it must not create a reviewed manifest from a
        // state the Python helper cannot validate. Keep this fail-closed at
        // the review boundary rather than failing after the user approves.
        let knownScanStates = Set(["ready", "noop", "analysis_failed", "cancelled"])
        guard photos.allSatisfy({ knownScanStates.contains($0.state) }) else {
            return false
        }
        guard photos.contains(where: { $0.isReviewSelectable && $0.hasReviewableChanges }) else {
            return false
        }
        return photos.allSatisfy { photo in
            photo.applyState == "not_run"
                && photo.rollbackState == "not_run"
                && photo.appliedCaption == nil
                && photo.appliedKeywords.isEmpty
                && photo.mutationDigest == nil
                && photo.rollbackDigest == nil
                && photo.rolledBackKeywords.isEmpty
        } && !photos.contains(where: \.hasInvalidFreshReviewCaptionState)
    }

    var reviewReadOnlyMessage: String? {
        if reviewedFromRunID != nil {
            return "Manifiesto revisado: la selección ya está fijada; confirma la aplicación solo si su dry-run fuente está disponible."
        }
        if photos.isEmpty && (scanStatus == "ready" || scanStatus == "ready_with_errors") {
            return "No hay fotos elegibles para revisar o aplicar."
        }
        if isNoopOnlyRun {
            return "No hay keywords ni captions nuevas para revisar o aplicar."
        }
        return canPrepareReview ? nil : "Solo lectura: ejecuta un dry-run nuevo antes de revisar o aplicar."
    }

    var isNoopOnlyRun: Bool {
        !photos.isEmpty && photos.allSatisfy { photo in
            photo.state == "noop"
                && !photo.hasReviewableChanges
                && photo.errors.isEmpty
                && photo.applyState == "not_run"
                && photo.rollbackState == "not_run"
        }
    }

    /// A fresh scan can contain only failed, cancelled, or otherwise
    /// non-selectable rows. Keep that state distinct from a reviewed or
    /// mutated manifest so recovery copy points to a new dry-run rather than
    /// claiming that an existing review is available.
    var isFreshRunWithoutReviewableRows: Bool {
        reviewedFromRunID == nil
            && !photos.isEmpty
            && photos.allSatisfy { $0.applyState == "not_run" && $0.rollbackState == "not_run" }
            && !photos.contains { $0.isReviewSelectable && $0.hasReviewableChanges }
    }

    var canRollback: Bool {
        guard !hasLegacyMutationState,
              !photos.contains(where: \.hasInvalidCaptionState) else { return false }
        return hasValidMutationEvidence && photos.contains(where: \.hasRollbackEvidence)
    }

    /// Schema 3 and 4 mutation rows carry receipts produced only after a successful
    /// read-back. History must not present a row as rollback-eligible when an
    /// editor or a truncated copy removed that evidence; Python repeats the
    /// authoritative check immediately before any write.
    var hasValidMutationEvidence: Bool {
        guard schemaVersion == 3 || schemaVersion == 4 else { return true }
        return photos.allSatisfy { photo in
            let hasAppliedData = !photo.appliedKeywords.isEmpty || photo.appliedCaption != nil
            if hasAppliedData {
                guard photo.applyState == "verified" || photo.applyState == "uncertain",
                      let digest = photo.mutationDigest,
                      Self.isValidMutationDigest(digest),
                      Self.mutationDigest(for: self, photo: photo) == digest else {
                    return false
                }
            } else if photo.mutationDigest != nil {
                return false
            }

            if photo.rollbackState == "not_run" {
                return photo.rollbackDigest == nil && photo.rolledBackKeywords.isEmpty
            }
            guard photo.applyState == "verified",
                  let digest = photo.rollbackDigest,
                  Self.isValidMutationDigest(digest),
                  Self.rollbackDigest(for: self, photo: photo) == digest else {
                return false
            }
            return true
        }
    }

    /// Python rejects mutation state in schema 1/2 before opening Photos.
    /// Mirror that boundary here so History does not offer a rollback button
    /// that will inevitably fail when the user activates it.
    var hasLegacyMutationState: Bool {
        guard let schemaVersion, schemaVersion < 3 else { return false }
        return photos.contains { photo in
            photo.applyState == "writing"
                || photo.applyState == "verified"
                || photo.applyState == "failed"
                || photo.applyState == "uncertain"
                || photo.applyState == "cancelled"
                || photo.rollbackState != "not_run"
                || !photo.appliedKeywords.isEmpty
                || !photo.rolledBackKeywords.isEmpty
                || photo.appliedCaption != nil
                || photo.mutationDigest != nil
                || photo.rollbackDigest != nil
        }
    }

    private enum CodingKeys: String, CodingKey {
        case runID = "run_id"
        case createdAt = "created_at"
        case schemaVersion = "schema_version"
        case scanStatus = "scan_status"
        case reviewedFromRunID = "reviewed_from_run_id"
        case scanDigest = "scan_digest"
        case sourceScanDigest = "source_scan_digest"
        case selection
        case photos
        case runErrors = "run_errors"
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        runID = try container.decode(String.self, forKey: .runID)
        createdAt = try container.decode(String.self, forKey: .createdAt)
        schemaVersion = try container.decodeIfPresent(Int.self, forKey: .schemaVersion)
        scanStatus = try container.decode(String.self, forKey: .scanStatus)
        reviewedFromRunID = try container.decodeIfPresent(String.self, forKey: .reviewedFromRunID)
        scanDigest = try container.decodeIfPresent(String.self, forKey: .scanDigest)
        sourceScanDigest = try container.decodeIfPresent(String.self, forKey: .sourceScanDigest)
        let selection = try container.decodeIfPresent(SelectionMetadata.self, forKey: .selection)
        selectionStrategy = Self.normalizeSelectionStrategy(selection?.strategy)
        photosAccess = Self.normalizePhotosAccess(selection?.access)
        captionsRequested = selection?.captionsRequested ?? false
        photos = try container.decode([PreviewPhoto].self, forKey: .photos)
        guard Set(photos.map(\.uuid)).count == photos.count else {
            throw DecodingError.dataCorruptedError(
                forKey: .photos,
                in: container,
                debugDescription: "photo UUIDs must be unique"
            )
        }
        runErrors = try container.decodeIfPresent([ManifestPreviewError].self, forKey: .runErrors) ?? []
    }

    init(
        runID: String,
        createdAt: String,
        scanStatus: String,
        reviewedFromRunID: String? = nil,
        schemaVersion: Int? = nil,
        scanDigest: String? = nil,
        sourceScanDigest: String? = nil,
        selectionStrategy: String = "recent",
        photosAccess: String = "unknown",
        captionsRequested: Bool = false,
        photos: [PreviewPhoto],
        runErrors: [ManifestPreviewError] = []
    ) {
        self.runID = runID
        self.createdAt = createdAt
        self.schemaVersion = schemaVersion
        self.scanStatus = scanStatus
        self.reviewedFromRunID = reviewedFromRunID
        self.scanDigest = scanDigest
        self.sourceScanDigest = sourceScanDigest
        self.selectionStrategy = Self.normalizeSelectionStrategy(selectionStrategy)
        self.photosAccess = Self.normalizePhotosAccess(photosAccess)
        self.captionsRequested = captionsRequested
        self.photos = photos
        self.runErrors = runErrors
    }

    private struct SelectionMetadata: Decodable {
        let strategy: String?
        let access: String?
        let captionsRequested: Bool?

        private enum CodingKeys: String, CodingKey {
            case strategy
            case access
            case captionsRequested = "captions_requested"
        }
    }

    private static func normalizeSelectionStrategy(_ value: String?) -> String {
        guard let value else { return "recent" }
        switch value {
        case "random", "targeted", "recent": return value
        default: return "unknown"
        }
    }

    private static func normalizePhotosAccess(_ value: String?) -> String {
        guard let value else { return "unknown" }
        switch value {
        case "authorized", "limited": return value
        default: return "unknown"
        }
    }

    private static func isValidMutationDigest(_ value: String) -> Bool {
        value.utf8.count == 64
            && value.unicodeScalars.allSatisfy {
                "0123456789abcdefABCDEF".unicodeScalars.contains($0)
            }
    }

    private static func mutationDigest(for manifest: Self, photo: PreviewPhoto) -> String? {
        let payload: [String: Any] = [
            "run_id": manifest.runID,
            "scan_digest": manifest.scanDigest ?? NSNull(),
            "reviewed_from_run_id": manifest.reviewedFromRunID ?? NSNull(),
            "source_scan_digest": manifest.sourceScanDigest ?? NSNull(),
            "uuid": photo.uuid,
            "photos_local_identifier": photo.photosLocalIdentifier ?? NSNull(),
            "applied_keywords": photo.appliedKeywords,
            "applied_caption": photo.appliedCaption ?? NSNull(),
        ]
        return sha256JSON(payload)
    }

    private static func rollbackDigest(for manifest: Self, photo: PreviewPhoto) -> String? {
        let payload: [String: Any] = [
            "run_id": manifest.runID,
            "mutation_digest": photo.mutationDigest ?? NSNull(),
            "uuid": photo.uuid,
            "photos_local_identifier": photo.photosLocalIdentifier ?? NSNull(),
            "rollback_state": photo.rollbackState,
            "rolled_back_keywords": photo.rolledBackKeywords,
            "caption_state": photo.captionState,
        ]
        return sha256JSON(payload)
    }

    private static func sha256JSON(_ object: [String: Any]) -> String? {
        guard JSONSerialization.isValidJSONObject(object),
              let data = try? JSONSerialization.data(
                  withJSONObject: object,
                  options: [.sortedKeys, .withoutEscapingSlashes]
              ) else {
            return nil
        }
        return SHA256.hash(data: data)
            .map { String(format: "%02x", $0) }
            .joined()
    }

    static func load(from url: URL) throws -> Self {
        let data = try ManifestFileSecurity.read(from: url)
        return try decodeStrict(data)
    }

    static func decodeStrict(_ data: Data) throws -> Self {
        do {
            try StrictJSONManifestValidator.validate(data)
            return try JSONDecoder().decode(Self.self, from: data)
        } catch {
            throw ManifestPreviewLoadError.invalidJSON
        }
    }
}

/// Stable, user-owned manifest file metadata captured with `fstat(2)`.
/// Keeping this value type internal makes the security checks independently
/// testable without opening Photos or relying on a race-prone pathname check.
struct ManifestFileMetadata: Equatable {
    let ownerID: UInt64
    let mode: UInt64
    let linkCount: UInt64
    let size: Int64
    let device: UInt64
    let inode: UInt64
    let modificationSeconds: Int64
    let modificationNanoseconds: Int64
}

enum ManifestPreviewLoadError: Error, Equatable {
    case notRegularFile
    case unreadable
    case invalidJSON
    case unexpectedOwner
    case insecurePermissions
    case hardLinked
    case tooLarge
    case changedDuringRead
}

/// `JSONDecoder` accepts duplicate object keys, while the Python manifest
/// boundary rejects them. Validate the object structure first so History and
/// Review cannot present an ambiguous manifest that Apply will reject later.
struct StrictJSONManifestValidator {
    private let bytes: [UInt8]
    private var integerRootKeys: Set<String> = []
    private var index = 0
    private var depth = 0
    private static let maximumDepth = 128

    static func validate(_ data: Data) throws {
        try validateSyntax(data)
        try Self.validateSchemaShape(data)
    }

    static func validateSyntax(_ data: Data, integerRootKeys: Set<String> = []) throws {
        var validator = Self(bytes: Array(data), integerRootKeys: integerRootKeys)
        try validator.parseValue()
        validator.skipWhitespace()
        guard validator.index == validator.bytes.count else { throw ValidationError.invalid }
    }

    private enum ValidationError: Error {
        case invalid
        case duplicateKey
    }

    /// JSONDecoder ignores unknown keys by design, while the Python manifest
    /// boundary rejects them. Keep the UI and mutation boundary aligned so a
    /// malformed manifest cannot look reviewable and fail only after Apply.
    /// Required-field validation remains the decoder's responsibility here;
    /// this gate only rejects fields outside the versioned manifest schema.
    private static func validateSchemaShape(_ data: Data) throws {
        guard let root = try JSONSerialization.jsonObject(with: data) as? [String: Any] else {
            throw ValidationError.invalid
        }
        // Early app/CLI manifests omitted schema_version while already using
        // the routed model shape. Infer that legacy shape only when the
        // discriminator fields are present; explicit versions remain
        // authoritative. This keeps strict unknown-key validation without
        // rejecting versionless historical exports.
        let schemaVersion: Int
        if let rawSchemaVersion = root["schema_version"] {
            schemaVersion = (rawSchemaVersion as? NSNumber)?.intValue ?? 0
        } else {
            let model = root["model"] as? [String: Any]
            let hasRoutedModel = model?.keys.contains("fast_name") == true
                || model?.keys.contains("detailed_name") == true
            let hasReviewProvenance = root["reviewed_from_run_id"] != nil
                || root["source_scan_digest"] != nil
            schemaVersion = hasReviewProvenance ? 3 : (hasRoutedModel ? 2 : 1)
        }
        guard [1, 2, 3, 4].contains(schemaVersion) else { throw ValidationError.invalid }
        let baseKeys: Set<String> = [
            "schema_version", "app", "run_id", "created_at", "dry_run", "scan_status",
            "model", "selection", "policy", "photos", "summary", "run_errors", "scan_digest",
        ]
        let reviewedRootKeys: Set<String> = ["reviewed_from_run_id", "source_scan_digest"]
        let rootKeys = schemaVersion == 4
            ? baseKeys.union(reviewedRootKeys).union(["review_decision_digest"])
            : (schemaVersion == 3 ? baseKeys.union(reviewedRootKeys) : baseKeys)
        guard Set(root.keys).isSubset(of: rootKeys) else { throw ValidationError.invalid }

        func validateObjectKeys(_ value: Any?, allowed: Set<String>) throws {
            guard let object = value as? [String: Any] else { return }
            guard Set(object.keys).isSubset(of: allowed) else { throw ValidationError.invalid }
        }

        try validateObjectKeys(root["app"], allowed: ["name", "version"])
        let modelKeys = schemaVersion >= 2
            ? ["policy", "fast_name", "detailed_name", "ollama_version", "endpoint"]
            : ["name", "ollama_version", "endpoint"]
        try validateObjectKeys(root["model"], allowed: Set(modelKeys))
        try validateObjectKeys(
            root["selection"],
            allowed: ["requested", "eligible", "screenshots_excluded", "access", "strategy", "captions_requested"]
        )
        try validateObjectKeys(
            root["policy"],
            allowed: ["id", "taxonomy_sha256", "max_keywords", "confidence_threshold"]
        )
        try validateObjectKeys(root["summary"], allowed: ["ready", "noop", "analysis_failed"])

        if let photos = root["photos"] as? [Any] {
            let basePhotoKeys: Set<String> = [
                "uuid", "photos_local_identifier", "title", "date", "date_timezone",
                "existing_keywords", "proposed_keywords", "contains_people", "contains_text",
                "confidence", "scan_state", "apply_state", "applied_keywords", "rollback_state",
                "rolled_back_keywords", "errors", "proposed_caption", "applied_caption",
                "caption_state", "mutation_digest", "rollback_digest", "technical_trace",
            ]
            let photoKeys = schemaVersion >= 2
                ? basePhotoKeys.union(["model_used", "model_reason"])
                : basePhotoKeys
            let schemaFourPhotoKeys: Set<String> = [
                "model_proposed_keywords", "approved_keywords", "keyword_origins",
                "model_proposed_caption", "approved_caption", "caption_origin",
            ]
            let allowedPhotoKeys = schemaVersion == 4
                ? photoKeys.union(schemaFourPhotoKeys)
                : photoKeys
            for photo in photos {
                try validateObjectKeys(photo, allowed: allowedPhotoKeys)
                guard let photoObject = photo as? [String: Any] else { continue }
                try validateObjectKeys(
                    photoObject["technical_trace"],
                    allowed: [
                        "prompt_effective", "prompt_version", "prompt_sha256", "ollama_version",
                        "used_gps", "used_apple_maps", "used_landmark", "place_context",
                        "place_lookup_state", "place_evidence_state", "durations_ms",
                    ]
                )
                if let errors = photoObject["errors"] as? [Any] {
                    for error in errors {
                        try validateObjectKeys(error, allowed: ["stage", "code"])
                    }
                }
            }
        }
        if let errors = root["run_errors"] as? [Any] {
            for error in errors {
                try validateObjectKeys(error, allowed: ["stage", "code"])
            }
        }
    }

    private mutating func parseValue() throws {
        skipWhitespace()
        guard index < bytes.count else { throw ValidationError.invalid }
        switch bytes[index] {
        case 0x7B: try parseObject() // {
        case 0x5B: try parseArray() // [
        case 0x22: _ = try parseString(decode: false) // "
        default: try parsePrimitive()
        }
    }

    private mutating func parseObject() throws {
        try enterContainer(opening: 0x7B)
        defer { depth -= 1 }
        skipWhitespace()
        if consume(0x7D) { return }

        var keys = Set<String>()
        while true {
            skipWhitespace()
            let key = try parseString(decode: true)
            guard keys.insert(key).inserted else { throw ValidationError.duplicateKey }
            skipWhitespace()
            guard consume(0x3A) else { throw ValidationError.invalid } // :
            skipWhitespace()
            let valueStart = index
            try parseValue()
            if depth == 1, integerRootKeys.contains(key) {
                let literal = bytes[valueStart..<index]
                let digits = literal.first == 0x2D ? literal.dropFirst() : literal
                guard !digits.isEmpty, digits.allSatisfy({ (0x30...0x39).contains($0) }) else {
                    throw ValidationError.invalid
                }
            }
            skipWhitespace()
            if consume(0x7D) { return }
            guard consume(0x2C) else { throw ValidationError.invalid } // ,
        }
    }

    private mutating func parseArray() throws {
        try enterContainer(opening: 0x5B)
        defer { depth -= 1 }
        skipWhitespace()
        if consume(0x5D) { return }

        while true {
            try parseValue()
            skipWhitespace()
            if consume(0x5D) { return }
            guard consume(0x2C) else { throw ValidationError.invalid }
        }
    }

    private mutating func enterContainer(opening: UInt8) throws {
        guard consume(opening), depth < Self.maximumDepth else { throw ValidationError.invalid }
        depth += 1
    }

    private mutating func parseString(decode: Bool) throws -> String {
        let start = index
        guard consume(0x22) else { throw ValidationError.invalid }
        while index < bytes.count {
            let byte = bytes[index]
            index += 1
            if byte == 0x22 {
                guard decode else { return "" }
                let slice = Data(bytes[start ..< index])
                return try JSONDecoder().decode(String.self, from: slice)
            }
            guard byte >= 0x20 else { throw ValidationError.invalid }
            if byte == 0x5C {
                guard index < bytes.count else { throw ValidationError.invalid }
                let escaped = bytes[index]
                index += 1
                if escaped == 0x75 {
                    guard index + 4 <= bytes.count,
                          bytes[index ..< index + 4].allSatisfy(Self.isHexDigit) else {
                        throw ValidationError.invalid
                    }
                    index += 4
                } else if ![0x22, 0x5C, 0x2F, 0x62, 0x66, 0x6E, 0x72, 0x74].contains(escaped) {
                    throw ValidationError.invalid
                }
            }
        }
        throw ValidationError.invalid
    }

    private mutating func parsePrimitive() throws {
        let start = index
        while index < bytes.count,
              ![0x20, 0x09, 0x0A, 0x0D, 0x2C, 0x5D, 0x7D].contains(bytes[index]) {
            index += 1
        }
        guard index > start else { throw ValidationError.invalid }
    }

    private mutating func skipWhitespace() {
        while index < bytes.count, [0x20, 0x09, 0x0A, 0x0D].contains(bytes[index]) {
            index += 1
        }
    }

    private mutating func consume(_ byte: UInt8) -> Bool {
        guard index < bytes.count, bytes[index] == byte else { return false }
        index += 1
        return true
    }

    private static func isHexDigit(_ byte: UInt8) -> Bool {
        (0x30 ... 0x39).contains(byte)
            || (0x41 ... 0x46).contains(byte)
            || (0x61 ... 0x66).contains(byte)
    }
}

enum HistoryManifestImportError: Error, Equatable {
    case invalidFilename
    case invalidManifest
    case destinationUnavailable
}

enum ManifestFileSecurity {
    static let maximumBytes = 4 * 1024 * 1024

    static func read(from url: URL, maximumBytes: Int = Self.maximumBytes) throws -> Data {
        let descriptor = try openManifestDescriptor(at: url)
        defer { Darwin.close(descriptor) }

        let before = try metadata(for: descriptor)
        try validate(metadata: before)
        guard before.size <= maximumBytes else { throw ManifestPreviewLoadError.tooLarge }

        var data = Data()
        data.reserveCapacity(min(Int(before.size), maximumBytes))
        var buffer = [UInt8](repeating: 0, count: 64 * 1024)

        while true {
            let count = buffer.withUnsafeMutableBytes { bytes -> Int in
                guard let baseAddress = bytes.baseAddress else { return 0 }
                return Darwin.read(descriptor, baseAddress, bytes.count)
            }
            guard count >= 0 else { throw ManifestPreviewLoadError.unreadable }
            if count == 0 { break }
            guard data.count <= maximumBytes - count else {
                throw ManifestPreviewLoadError.tooLarge
            }
            data.append(contentsOf: buffer.prefix(count))
        }

        // The pathname was never trusted after open. Re-check the descriptor
        // after reading so truncation, replacement, permission, owner, or
        // hard-link changes cannot be silently decoded as a stable manifest.
        let after = try metadata(for: descriptor)
        try validateStable(before: before, after: after)
        return data
    }

    static func validate(metadata: ManifestFileMetadata) throws {
        guard metadata.ownerID == UInt64(getuid()) else {
            throw ManifestPreviewLoadError.unexpectedOwner
        }
        guard metadata.mode == 0o600 else {
            throw ManifestPreviewLoadError.insecurePermissions
        }
        guard metadata.linkCount == 1 else {
            throw ManifestPreviewLoadError.hardLinked
        }
        guard metadata.size >= 0, metadata.size <= Int64(maximumBytes) else {
            throw ManifestPreviewLoadError.tooLarge
        }
    }

    static func validateStable(
        before: ManifestFileMetadata,
        after: ManifestFileMetadata
    ) throws {
        guard before == after else {
            throw ManifestPreviewLoadError.changedDuringRead
        }
        try validate(metadata: after)
    }

    private static func openManifestDescriptor(at url: URL) throws -> Int32 {
        let path = TrustedSystemPath.canonicalPath(for: url)
        guard path.hasPrefix("/") else {
            throw ManifestPreviewLoadError.notRegularFile
        }
        let components = path.split(separator: "/", omittingEmptySubsequences: true)
        guard let finalComponent = components.last, !finalComponent.isEmpty else {
            throw ManifestPreviewLoadError.notRegularFile
        }

        let directoryFlags = O_RDONLY | O_CLOEXEC | O_NOFOLLOW | O_DIRECTORY
        let rootDescriptor = Darwin.open("/", directoryFlags)
        guard rootDescriptor >= 0 else { throw openError() }
        var parentDescriptor = rootDescriptor

        for component in components.dropLast() {
            let childDescriptor = component.withCString { name in
                Darwin.openat(parentDescriptor, name, directoryFlags)
            }
            Darwin.close(parentDescriptor)
            guard childDescriptor >= 0 else { throw openError() }
            parentDescriptor = childDescriptor
        }

        let fileFlags = O_RDONLY | O_CLOEXEC | O_NOFOLLOW
        let descriptor = finalComponent.withCString { name in
            Darwin.openat(parentDescriptor, name, fileFlags)
        }
        Darwin.close(parentDescriptor)
        guard descriptor >= 0 else { throw openError() }

        var fileStatus = stat()
        guard Darwin.fstat(descriptor, &fileStatus) == 0 else {
            Darwin.close(descriptor)
            throw ManifestPreviewLoadError.unreadable
        }
        guard (fileStatus.st_mode & S_IFMT) == S_IFREG else {
            Darwin.close(descriptor)
            throw ManifestPreviewLoadError.notRegularFile
        }
        return descriptor
    }

    private static func metadata(for descriptor: Int32) throws -> ManifestFileMetadata {
        var fileStatus = stat()
        guard Darwin.fstat(descriptor, &fileStatus) == 0 else {
            throw ManifestPreviewLoadError.unreadable
        }
        guard (fileStatus.st_mode & S_IFMT) == S_IFREG else {
            throw ManifestPreviewLoadError.notRegularFile
        }
        return ManifestFileMetadata(
            ownerID: UInt64(fileStatus.st_uid),
            mode: UInt64(fileStatus.st_mode) & 0o7777,
            linkCount: UInt64(fileStatus.st_nlink),
            size: Int64(fileStatus.st_size),
            device: UInt64(fileStatus.st_dev),
            inode: UInt64(fileStatus.st_ino),
            modificationSeconds: Int64(fileStatus.st_mtimespec.tv_sec),
            modificationNanoseconds: Int64(fileStatus.st_mtimespec.tv_nsec)
        )
    }

    private static func openError() -> ManifestPreviewLoadError {
        return errno == ELOOP ? .notRegularFile : .unreadable
    }
}

enum HistoryActionAvailability {
    static let activeOperationNotice = "Hay una operación en curso; importar otro manifiesto, abrir otra revisión o iniciar un rollback queda pausado hasta que termine."
    static let manifestImportBlockedNotice = "Hay otra operación en curso; espera a que termine antes de importar otro manifiesto."

    /// Import writes a new private run into the same Application Support
    /// collection that the active workflow is persisting. Pause it alongside
    /// review navigation and rollback so History cannot change underneath an
    /// in-flight operation. The handler rechecks this after the file picker
    /// returns to close the race where an operation starts while it is open.
    static func canImportManifest(workerIsRunning: Bool) -> Bool {
        !workerIsRunning
    }

    static func manifestImportAccessibilityHint(workerIsRunning: Bool) -> String {
        workerIsRunning
            ? manifestImportBlockedNotice
            : "Importa una copia local para consulta; aplicar o revertir seguirá requiriendo validación y confirmación."
    }

    /// Loading a different manifest while the worker is still emitting events
    /// would replace the visible review context mid-operation. Keep history
    /// inspection available, but pause navigation into another review until
    /// the active request reaches a terminal state.
    static func canOpenHistoricalReview(
        reviewAvailable: Bool,
        workerIsRunning: Bool
    ) -> Bool {
        reviewAvailable && !workerIsRunning
    }

    static func historicalReviewAccessibilityHint(
        workerIsRunning: Bool,
        fallback: String
    ) -> String {
        workerIsRunning
            ? "Hay otra operación en curso; espera a que termine antes de abrir otra revisión."
            : fallback
    }

    /// A historical run carries its own provenance and mutation evidence. A
    /// failed operation in the currently visible run must not disable a
    /// verified rollback belonging to a different historical run; only an
    /// active worker is a process-wide conflict.
    static func canStartHistoricalRollback(workerIsRunning: Bool) -> Bool {
        !workerIsRunning
    }

    static func historicalRollbackAccessibilityHint(workerIsRunning: Bool) -> String {
        if workerIsRunning {
            return "Hay otra operación en curso; espera a que termine antes de iniciar el rollback."
        }
        return "Solo revierte las fotos con cambios verificados; las demás permanecen sin cambios."
    }

    static func canStartRollback(workerIsRunning: Bool, requiresManualReview: Bool) -> Bool {
        !workerIsRunning && !requiresManualReview
    }

    static func accessibilityHint(workerIsRunning: Bool, requiresManualReview: Bool) -> String {
        if workerIsRunning {
            return "Hay otra operación en curso; espera a que termine antes de iniciar el rollback."
        }
        if requiresManualReview {
            return "El rollback está pausado por revisión manual; revisa el run antes de intentar otra mutación."
        }
        return "Solo revierte las fotos con cambios verificados; las demás permanecen sin cambios."
    }
}

/// Opening a persisted review is a local, read-only action. A current Photos
/// or Automation failure must block the later mutation without hiding the
/// approved scope the person needs to inspect. Provenance/manual-review gates
/// remain authoritative and can still make the review itself unavailable.
struct HistoryReviewAccess: Equatable, Sendable {
    let canOpen: Bool
    let mutationBlockMessage: String?

    init(
        reviewed: Bool,
        reviewedApplyAvailable: Bool,
        freshReviewAvailable: Bool,
        mutationBlockMessage: String?,
        reviewedReadOnlyAvailable: Bool = false
    ) {
        canOpen = reviewed
            ? reviewedApplyAvailable || reviewedReadOnlyAvailable
            : freshReviewAvailable
        if reviewed && canOpen {
            self.mutationBlockMessage = mutationBlockMessage
                ?? (reviewedReadOnlyAvailable && !reviewedApplyAvailable
                    ? "Este run ya tiene un resultado registrado; la revisión es solo lectura."
                    : nil)
        } else {
            self.mutationBlockMessage = nil
        }
    }

    var isReadOnlyBecauseMutationIsBlocked: Bool {
        canOpen && mutationBlockMessage != nil
    }
}

/// Copy for an empty history that points to a safe, read-only entry point.
/// Keeping this in a value-only model makes the privacy and mutation boundary
/// testable without launching SwiftUI or touching Photos.
enum HistoryEmptyStateCopy {
    static let title = "Sin ejecuciones locales"
    static let detail = "Ejecuta un dry-run o importa un manifiesto local para verlo aquí. Revisar es de solo lectura; Apply y Rollback requieren revisión y confirmación explícitas."
    static let accessibilityLabel = "\(title). \(detail)"
    static func actionTitle(preparationReady: Bool) -> String {
        actionTitle(preparationReady: preparationReady, preflightIsStale: false)
    }

    static func actionTitle(preparationReady: Bool, preflightIsStale: Bool) -> String {
        canOpenNewRun(preparationReady: preparationReady, preflightIsStale: preflightIsStale)
            ? "Abrir Revisión" : "Abrir Preparación"
    }

    static func actionAccessibilityHint(preparationReady: Bool) -> String {
        actionAccessibilityHint(preparationReady: preparationReady, preflightIsStale: false)
    }

    static func actionAccessibilityHint(preparationReady: Bool, preflightIsStale: Bool) -> String {
        canOpenNewRun(preparationReady: preparationReady, preflightIsStale: preflightIsStale)
            ? "Abre la mesa continua de Revisión; el análisis local no modifica Fotos hasta que guardes una foto explícitamente."
            : "Abre Preparación primero para resolver los componentes locales; no inicia el análisis; no modifica Fotos."
    }

    static func canOpenNewRun(preparationReady: Bool, preflightIsStale: Bool) -> Bool {
        preparationReady && !preflightIsStale
    }
}

/// Copy for Preview before a dry-run exists. Keep the read-only boundary
/// explicit here as well as in Historial, since this is the first empty state
/// many users see after opening the app.
enum ReviewEmptyStateCopy {
    static let title = "Sin manifiesto"
    static let detail = "Ejecuta un dry-run para revisar propuestas. El dry-run no modifica Apple Fotos."
    static let accessibilityLabel = "\(title). \(detail)"
}

enum HistoryArtifactFileType: Equatable, Sendable {
    case regular
    case directory
    case symlink
    case other
}

struct HistoryArtifactMetadata: Equatable, Sendable {
    let type: HistoryArtifactFileType
    let ownerID: UInt64
    let mode: UInt16
    let linkCount: UInt64
}

/// A history artifact is openable only when it is a private regular file in
/// the run directory. This is deliberately a value-level policy so the UI can
/// test and explain every blocked state without exposing filesystem details.
enum HistoryArtifactAvailability: String, Equatable, Sendable {
    case available
    case missing
    case notRegular
    case symlink
    case insecure
    case outsideRun

    var isOpenable: Bool { self == .available }

    var accessibilityHint: String {
        switch self {
        case .available:
            return "Abre el artefacto local privado de esta ejecución."
        case .missing:
            return "El artefacto local no está disponible; actualiza Historial o conserva el manifest."
        case .notRegular:
            return "El artefacto no es un archivo regular y no se abrirá."
        case .symlink:
            return "El artefacto es un enlace y no se abrirá fuera de esta ejecución."
        case .insecure:
            return "El artefacto no tiene propiedad o permisos privados; no se abrirá."
        case .outsideRun:
            return "El artefacto no pertenece a esta ejecución y no se abrirá."
        }
    }

    /// Disabled artifact buttons need a visible explanation as well as an
    /// accessibility hint; some macOS controls do not announce hints while
    /// disabled. Keep this copy path-free and safe to share.
    var noticeText: String? {
        switch self {
        case .available:
            return nil
        case .missing:
            return "Este artefacto local no está disponible; actualiza Historial o conserva el run."
        case .notRegular:
            return "Este artefacto no es un archivo regular; no se abrirá."
        case .symlink:
            return "Este artefacto es un enlace; no se abrirá fuera del run."
        case .insecure:
            return "Este artefacto no tiene permisos privados; no se abrirá."
        case .outsideRun:
            return "Este artefacto no pertenece a este run; no se abrirá."
        }
    }

    static func classify(
        metadata: HistoryArtifactMetadata?,
        artifactURL: URL,
        runDirectory: URL
    ) -> Self {
        let artifactParent = artifactURL.standardizedFileURL.deletingLastPathComponent().path
        let expectedParent = runDirectory.standardizedFileURL.path
        guard artifactParent == expectedParent else { return .outsideRun }
        guard let metadata else { return .missing }
        switch metadata.type {
        case .symlink:
            return .symlink
        case .directory, .other:
            return .notRegular
        case .regular:
            guard metadata.ownerID == UInt64(getuid()),
                  metadata.mode == 0o600,
                  metadata.linkCount == 1 else {
                return .insecure
            }
            return .available
        }
    }

    static func inspect(path: URL, runDirectory: URL) -> Self {
        let artifactURL = path.standardizedFileURL
        let runDirectoryURL = runDirectory.standardizedFileURL
        guard runDirectoryURL.path == runDirectoryURL.resolvingSymlinksInPath().path else {
            return .outsideRun
        }
        var fileStatus = stat()
        let result = artifactURL.path.withCString { pointer in
            Darwin.lstat(pointer, &fileStatus)
        }
        guard result == 0 else {
            return classify(metadata: nil, artifactURL: artifactURL, runDirectory: runDirectoryURL)
        }

        let fileType: HistoryArtifactFileType
        switch fileStatus.st_mode & S_IFMT {
        case S_IFLNK:
            fileType = .symlink
        case S_IFREG:
            fileType = .regular
        case S_IFDIR:
            fileType = .directory
        default:
            fileType = .other
        }
        let metadata = HistoryArtifactMetadata(
            type: fileType,
            ownerID: UInt64(fileStatus.st_uid),
            mode: UInt16(fileStatus.st_mode & 0o7777),
            linkCount: UInt64(fileStatus.st_nlink)
        )
        return classify(metadata: metadata, artifactURL: artifactURL, runDirectory: runDirectoryURL)
    }
}

enum HistoryRunStatus: String, Equatable, Sendable {
    case readyToReview
    case noChanges
    case applied
    case rolledBack
    case needsAttention

    var humanLabel: String {
        switch self {
        case .readyToReview: return "Listo para revisar"
        case .noChanges: return "Sin cambios nuevos"
        case .applied: return "Aplicado y verificado"
        case .rolledBack: return "Rollback verificado"
        case .needsAttention: return "Requiere atención"
        }
    }

    /// Native SF Symbols make the lifecycle state scannable before reading
    /// the longer, human-safe status copy in Historial.
    var systemImageName: String {
        switch self {
        case .readyToReview: return "checklist"
        case .noChanges: return "minus.circle"
        case .applied: return "checkmark.circle.fill"
        case .rolledBack: return "arrow.uturn.backward.circle.fill"
        case .needsAttention: return "exclamationmark.triangle.fill"
        }
    }
}

/// Copy for a reviewed manifest loaded back into the app. Keep the terminal
/// mutation states explicit so a completed apply or rollback never looks like
/// another apply confirmation.
enum ReviewedManifestCopy {
    static func message(
        status: HistoryRunStatus,
        canContinueReviewedApply: Bool,
        canRollback: Bool,
        isConsultationOnly: Bool,
        hasWarnings: Bool,
        isPartialRollback: Bool = false,
        hasNoChanges: Bool = false,
        hasBlockedRows: Bool = true,
        isRetryingFailedApply: Bool = false
    ) -> String {
        if isConsultationOnly {
            return "Manifiesto revisado en solo consulta: la fuente local no está disponible o no es verificable."
        }
        if canContinueReviewedApply {
            if isRetryingFailedApply {
                return "Manifiesto revisado listo para reintentar solo las filas fallidas; las filas verificadas no se modificarán."
            }
            return hasWarnings
                ? (hasBlockedRows
                    ? "Manifiesto revisado listo: se confirmarán solo las filas válidas; las demás quedan bloqueadas."
                    : "Manifiesto revisado listo; revisa las advertencias del scan y confirma explícitamente.")
                : "Manifiesto revisado coherente; confirma la aplicación explícitamente."
        }
        if hasNoChanges {
            return "Sin cambios nuevos; conserva el manifiesto como auditoría."
        }
        switch status {
        case .applied:
            return canRollback
                ? "Aplicación verificada; puedes revertir solo los cambios verificados."
                : "Aplicación verificada; conserva el manifiesto como auditoría."
        case .rolledBack:
            return "Rollback verificado; no hay cambios verificados pendientes."
        case .noChanges:
            return "No hubo cambios nuevos; conserva el manifiesto como auditoría."
        case .needsAttention:
            if isPartialRollback {
                return "Rollback parcial: solo los cambios verificados restantes pueden revertirse."
            }
            if canRollback {
                return "Aplicación parcial o incierta; puedes revertir solo los cambios verificados."
            }
            return "Este manifiesto requiere revisión manual; ejecuta un dry-run nuevo antes de aplicar."
        case .readyToReview:
            return "Revisa las propuestas antes de aplicar."
        }
    }
}

struct HistoryRunSummary: Identifiable, Equatable, Sendable {
    private static let displayableErrorCodes: Set<String> = [
        "READ_FAILED", "IDENTITY_MISMATCH", "EXPORT_FAILED", "ANALYSIS_FAILED",
        "LOW_CONFIDENCE", "EXPORT_DELETE_FAILED", "WORKSPACE_FAILED", "CANCELLED",
        "INTERRUPTED_WRITE", "APPLY_FAILED", "APPLY_READ_FAILED", "WRITE_UNCERTAIN",
        "KEYWORD_WRITE_UNCERTAIN", "CAPTION_WRITE_UNCERTAIN", "INTERRUPTED_REMOVAL",
        "ROLLBACK_FAILED", "CASING_CONFLICT", "APPLIED_KEYWORD_MISSING", "REMOVAL_UNCERTAIN",
        "OLLAMA_VERSION_OLD", "OLLAMA_MODEL_MISSING", "OLLAMA_UNAVAILABLE", "OLLAMA_NO_VISION",
        "OLLAMA_RESPONSE_INVALID", "OLLAMA_REQUEST_FAILED", "VISION_IMAGE_INVALID",
        "OLLAMA_PREFLIGHT_FAILED", "PHOTOS_ACCESS_DENIED", "PHOTOS_AUTOMATION_DENIED", "PHOTOS_ACCESS_LIMITED",
        "FEWER_PHOTOS_AVAILABLE",
        "MODEL_POLICY_UNSUPPORTED", "RUNS_ROOT_INVALID", "WORKER_OPERATION_FAILED",
        "INVALID_HELPER_EVENT", "HELPER_UNAVAILABLE", "PHOTOSCRIPT_UNAVAILABLE", "MANIFEST_NOT_REVIEWED",
        "UNSAFE_PREFLIGHT_RESULT", "UNSAFE_WORKFLOW_RESULT", "UNSAFE_REVIEW_RESULT",
        "SCAN_NOT_READY", "REVIEW_PROVENANCE_INVALID", "MUTATION_EVIDENCE_INVALID",
        "REVIEW_NOT_PRISTINE", "ROLLBACK_ALREADY_STARTED", "PLATFORM_UNSUPPORTED",
        "LIMIT_INVALID", "SCAN_OPTIONS_INVALID", "MODEL_INVALID", "SCAN_SETUP_FAILED", "MANIFEST_WRITE_FAILED",
        "MANIFEST_INVALID", "LOCK_OR_MANIFEST_FAILED", "MANIFEST_PATH_INVALID"
    ]

    private static let humanErrorMessages: [String: String] = [
        "READ_FAILED": "No se pudo leer la foto.",
        "IDENTITY_MISMATCH": "La foto cambió; se requiere una revisión manual.",
        "EXPORT_FAILED": "No se pudo preparar una copia temporal para el análisis.",
        "ANALYSIS_FAILED": "El análisis local terminó antes de producir una propuesta válida; esta versión no registró una causa más específica.",
        "LOW_CONFIDENCE": "La confianza fue baja; revisa las propuestas antes de aplicar.",
        "EXPORT_DELETE_FAILED": "Quedó un temporal pendiente de limpieza.",
        "WORKSPACE_FAILED": "No se pudo preparar el espacio local de trabajo.",
        "CANCELLED": "La ejecución fue cancelada.",
        "INTERRUPTED_WRITE": "La escritura se interrumpió; revisa Fotos manualmente.",
        "APPLY_FAILED": "No se pudo aplicar el cambio a esta foto.",
        "APPLY_READ_FAILED": "No se pudo revalidar la foto antes de escribir.",
        "WRITE_UNCERTAIN": "La escritura no pudo verificarse; revisa Fotos manualmente.",
        "KEYWORD_WRITE_UNCERTAIN": "Las keywords no pudieron verificarse; revisa Fotos manualmente.",
        "CAPTION_WRITE_UNCERTAIN": "El caption no pudo verificarse; revisa Fotos manualmente.",
        "INTERRUPTED_REMOVAL": "La eliminación se interrumpió; revisa Fotos manualmente.",
        "ROLLBACK_FAILED": "No se pudo revertir este cambio.",
        "CASING_CONFLICT": "Hay una variante de mayúsculas; no se eliminó automáticamente.",
        "APPLIED_KEYWORD_MISSING": "La keyword aplicada no apareció en la verificación.",
        "REMOVAL_UNCERTAIN": "La reversión no pudo verificarse; revisa Fotos manualmente.",
        "OLLAMA_VERSION_OLD": "Ollama necesita una versión más reciente.",
        "OLLAMA_MODEL_MISSING": "Falta un modelo local; instala el modelo indicado.",
        "OLLAMA_UNAVAILABLE": "Ollama no está disponible en este Mac.",
        "OLLAMA_NO_VISION": "El modelo local no tiene capacidad de visión.",
        "OLLAMA_RESPONSE_INVALID": "Ollama respondió sin el formato esperado para el análisis.",
        "OLLAMA_REQUEST_FAILED": "Ollama rechazó o no completó la solicitud de análisis.",
        "VISION_IMAGE_INVALID": "No se pudo preparar una copia compatible de la foto para el modelo local.",
        "OLLAMA_PREFLIGHT_FAILED": "No se pudo comprobar Ollama o los modelos locales; corrige el servicio local y vuelve a intentarlo.",
        "PHOTOS_ACCESS_DENIED": PhotosTCCGuide.packagedApplication.errorMessage,
        "PHOTOS_AUTOMATION_DENIED": AutomationTCCGuide.packagedHelper.errorMessage,
        "PHOTOS_ACCESS_LIMITED": "El acceso a Fotos es limitado; solo se analizarán las fotos visibles.",
        "FEWER_PHOTOS_AVAILABLE": "Hay menos fotos elegibles de las solicitadas; revisa únicamente las fotos disponibles.",
        "MODEL_POLICY_UNSUPPORTED": "La política de modelos no está disponible en esta versión.",
        "RUNS_ROOT_INVALID": "La carpeta local de ejecuciones no es segura.",
        "WORKER_OPERATION_FAILED": "El helper local no pudo completar la operación.",
        "INVALID_HELPER_EVENT": "El helper devolvió un evento no válido.",
        "UNSAFE_PREFLIGHT_RESULT": "El helper local devolvió una respuesta incompatible; reinstala la app o usa una build válida y vuelve a comprobar.",
        "UNSAFE_WORKFLOW_RESULT": "El helper local devolvió una respuesta incompatible; reinstala la app o usa una build válida y vuelve a comprobar.",
        "UNSAFE_REVIEW_RESULT": "La revisión local no pudo validarse; crea una revisión nueva desde el dry-run.",
        "HELPER_UNAVAILABLE": "No se pudo iniciar el helper local.",
        "PHOTOSCRIPT_UNAVAILABLE": "PhotoScript no pudo cargar su puente AppleScript; comprueba la compatibilidad de Photos, PhotoScript y macOS, y ejecuta un dry-run nuevo.",
        "MANIFEST_NOT_REVIEWED": "Revisa las propuestas antes de aplicar cambios.",
        "SCAN_NOT_READY": "El análisis aún no está listo; ejecuta un dry-run nuevo antes de continuar.",
        "REVIEW_PROVENANCE_INVALID": "El manifiesto revisado no conserva una fuente local verificable; ejecuta un dry-run nuevo antes de aplicar.",
        "MUTATION_EVIDENCE_INVALID": "No se pudieron verificar los cambios registrados; revisa Fotos manualmente y ejecuta un dry-run nuevo.",
        "REVIEW_NOT_PRISTINE": "El manifiesto revisado ya fue modificado; ejecuta un dry-run nuevo antes de aplicar.",
        "ROLLBACK_ALREADY_STARTED": "La reversión ya comenzó; revisa Fotos manualmente antes de intentarlo de nuevo.",
        "PLATFORM_UNSUPPORTED": "Esta app necesita macOS 14 o posterior; ejecuta el análisis en un sistema compatible.",
        "LIMIT_INVALID": "El límite de fotos no es válido; elige un valor entre 1 y 500.",
        "SCAN_OPTIONS_INVALID": "La configuración de la ejecución no es válida; revisa las opciones y ejecuta un dry-run nuevo.",
        "MODEL_INVALID": "El modelo local no es válido; comprueba el nombre instalado y vuelve a intentarlo.",
        "SCAN_SETUP_FAILED": "No se pudo preparar el análisis local; revisa permisos y vuelve a ejecutar un dry-run.",
        "MANIFEST_WRITE_FAILED": "No se pudo guardar el resultado local; comprueba el espacio y permisos de la carpeta de ejecuciones.",
        "MANIFEST_INVALID": "El manifiesto no es válido o no coincide con el esquema; importa un dry-run nuevo.",
        "LOCK_OR_MANIFEST_FAILED": "No se pudo bloquear o leer el run de forma segura; cierra otra ejecución y vuelve a intentarlo.",
        "MANIFEST_PATH_INVALID": "La ubicación del manifiesto no es segura; selecciona un manifest.json local válido."
    ]

    let manifestURL: URL
    let preview: RunManifestPreview
    let localReviewSourceAvailable: Bool
    let isAutonomous: Bool

    init(manifestURL: URL, preview: RunManifestPreview, localReviewSourceAvailable: Bool = true, isAutonomous: Bool = false) {
        self.manifestURL = manifestURL
        self.preview = preview
        self.localReviewSourceAvailable = localReviewSourceAvailable
        self.isAutonomous = isAutonomous
    }

    var id: String { preview.runID }
    var runID: String { preview.runID }
    var createdAt: String { preview.createdAt }
    var canRollback: Bool { preview.canRollback && !isConsultationOnly }

    var reviewedManifestMessage: String {
        ReviewedManifestCopy.message(
            status: status,
            canContinueReviewedApply: canContinueReviewedApply,
            canRollback: canRollback,
            isConsultationOnly: isConsultationOnly,
            hasWarnings: preview.scanStatus == "ready_with_errors",
            isPartialRollback: isPartialRollback,
            hasNoChanges: isNoopOutcome,
            hasBlockedRows: hasBlockedRows,
            isRetryingFailedApply: isRetryingFailedApply
        )
    }

    /// An imported reviewed manifest without its matching local dry-run is
    /// consultation-only. Keep this explicit in the history status so the
    /// UI cannot make it look like an ordinary recoverable run.
    var isConsultationOnly: Bool {
        preview.reviewedFromRunID != nil && !localReviewSourceAvailable
    }

    /// A partial rollback has both verified removals and verified changes
    /// still present. Keep this state distinct from a fully rolled-back run.
    var isPartialRollback: Bool {
        preview.photos.contains { ["verified_removed", "already_absent"].contains($0.rollbackState) }
            && preview.photos.contains {
                $0.hasRollbackEvidence
            }
    }

    /// A completed rollback can coexist with scan warnings. Treat only
    /// verified removals/absence as terminal; failed, uncertain, in-flight or
    /// still-applied rows keep the run in manual review.
    var isRollbackComplete: Bool {
        let hasRollbackOutcome = preview.photos.contains {
            ["verified_removed", "already_absent"].contains($0.rollbackState)
        }
        guard hasRollbackOutcome, !isPartialRollback else { return false }
        return preview.photos.allSatisfy { photo in
            let applyIsUnresolved = !["not_run", "verified", "noop"].contains(photo.applyState)
            let rollbackIsUnresolved = !["not_run", "verified_removed", "already_absent"].contains(photo.rollbackState)
            return !photo.hasRollbackEvidence && !applyIsUnresolved && !rollbackIsUnresolved
        }
    }

    var isNoopOutcome: Bool {
        preview.isNoopOnlyRun
            || (preview.reviewedFromRunID != nil
                && !preview.photos.isEmpty
                && preview.photos.allSatisfy {
                    $0.applyState == "noop"
                        && $0.rollbackState == "not_run"
                        && $0.appliedKeywords.isEmpty
                        && $0.appliedCaption == nil
                })
    }

    /// A warning-only scan has no blocked photo rows. Keep its copy distinct
    /// from a partial scan so the UI does not imply that a row is disabled
    /// when every selected photo remains reviewable.
    var hasBlockedRows: Bool {
        preview.photos.contains { !$0.isReviewSelectable }
    }

    var canContinueReviewedApply: Bool {
        guard preview.reviewedFromRunID != nil,
              localReviewSourceAvailable,
              preview.scanStatus == "ready" || preview.scanStatus == "ready_with_errors",
              preview.hasValidMutationEvidence,
              !preview.photos.contains(where: \.hasInvalidCaptionState),
              (!preview.canRollback || hasRetryableApplyRows) else { return false }
        let hasApplicableRow = preview.photos.contains { photo in
            (photo.isReviewSelectable && photo.hasReviewableChanges)
                || photo.isRetryableApplyRow
        }
        guard hasApplicableRow else { return false }
        return preview.photos.allSatisfy { photo in
            guard photo.rollbackState == "not_run" else { return false }
            switch photo.applyState {
            case "not_run":
                guard photo.appliedKeywords.isEmpty && photo.appliedCaption == nil else { return false }
                switch photo.state {
                case "ready", "noop":
                    return !photo.needsReviewAttention && photo.errors.isEmpty
                case "analysis_failed", "cancelled":
                    return !photo.errors.isEmpty && photo.proposedKeywords.isEmpty && !photo.hasCaptionProposal
                default:
                    return false
                }
            case "noop":
                return photo.appliedKeywords.isEmpty && photo.appliedCaption == nil
            case "verified":
                return photo.hasRollbackEvidence
            case "failed", "cancelled":
                return photo.isRetryableApplyRow
            default:
                return false
            }
        }
    }

    /// A failed apply can be retried only when its row has no mutation
    /// evidence and retains an apply-stage error. Uncertain/writing and any
    /// rollback state remain fail-closed for manual review.
    var hasRetryableApplyRows: Bool {
        preview.photos.contains(where: \.isRetryableApplyRow)
    }

    var isRetryingFailedApply: Bool {
        canContinueReviewedApply && hasRetryableApplyRows
    }

    var statusLabel: String {
        if isConsultationOnly { return "Solo consulta" }
        if isRetryingFailedApply {
            return "Listo para reintentar solo las filas fallidas"
        }
        if canContinueReviewedApply && preview.scanStatus == "ready_with_errors" {
            return "Listo para confirmar aplicación con advertencias"
        }
        if preview.reviewedFromRunID == nil,
           preview.scanStatus == "ready_with_errors",
           !preview.photos.isEmpty,
           !hasBlockedRows {
            return "Listo con advertencias"
        }
        return canContinueReviewedApply ? "Listo para confirmar aplicación" : status.humanLabel
    }

    var reviewedApplyAccessibilityLabel: String {
        if canContinueReviewedApply {
            if isRetryingFailedApply {
                return "Reintentar solo las filas fallidas; las filas aplicadas y verificadas no se modificarán"
            }
            if preview.scanStatus == "ready_with_errors" {
                return hasBlockedRows
                    ? "Confirmar aplicación de filas válidas; las filas con errores están bloqueadas"
                    : "Confirmar aplicación tras revisar las advertencias del scan"
            }
            return "Confirmar aplicación del manifiesto revisado"
        }
        return localReviewSourceAvailable
            ? "Manifest revisado solo para consulta; requiere revisión manual y un dry-run nuevo"
            : "Manifest revisado solo para consulta; la fuente local no está disponible o no es verificable"
    }

    /// Make the scope of a rollback explicit for partial runs. A verified
    /// subset can be reverted safely even when another photo needs attention.
    var rollbackActionText: String {
        guard canRollback else { return "Rollback no disponible" }
        let count = preview.photos.filter { photo in
            photo.hasRollbackEvidence
        }.count
        guard count > 0 else { return "Rollback no disponible" }
        return count == 1
            ? "Rollback disponible para 1 foto verificada"
            : "Rollback disponible para \(count) fotos verificadas"
    }

    /// Safe, human-facing action for the history row. This deliberately
    /// prioritizes uncertainty over convenience and never implies that a
    /// failed or partial mutation is safe to retry automatically.
    var nextSafeActionText: String {
        if preview.reviewedFromRunID != nil && !localReviewSourceAvailable {
            return "Usa «Importar manifest» para importar también el dry-run fuente o ejecuta un dry-run nuevo; este manifest queda solo para consulta"
        }
        if !preview.hasValidMutationEvidence {
            return "La evidencia de mutación no es verificable; revisa Fotos y ejecuta un dry-run nuevo"
        }
        if preview.photos.contains(where: \.hasInvalidCaptionState) {
            return "El estado del caption no es verificable; ejecuta un dry-run nuevo antes de aplicar"
        }
        if errorCodes.contains("PHOTOS_ACCESS_DENIED") {
            return PhotosTCCGuide.packagedApplication.recoveryActionText
        }
        if errorCodes.contains("PHOTOS_AUTOMATION_DENIED") {
            return AutomationTCCGuide.packagedHelper.recoveryActionText
        }
        if errorCodes.contains("HELPER_UNAVAILABLE") {
            return "Reinstala la app o usa una build válida y vuelve a comprobar"
        }
        if errorCodes.contains("PHOTOSCRIPT_UNAVAILABLE") {
            return "Comprueba la compatibilidad local de Photos y PhotoScript, y ejecuta un dry-run nuevo"
        }
        if errorCodes.contains("UNSAFE_REVIEW_RESULT") {
            return "Crea una revisión nueva desde el dry-run"
        }
        if status == .noChanges {
            return "No hubo cambios nuevos; conserva el manifiesto como auditoría"
        }
        if isRollbackComplete && status == .needsAttention {
            return "Rollback verificado; no hay cambios verificados pendientes; revisa las advertencias restantes"
        }
        if status == .rolledBack {
            return "No hay cambios verificados pendientes"
        }
        if preview.photos.isEmpty && preview.scanStatus == "failed" {
            return "El análisis falló; ejecuta un dry-run nuevo antes de aplicar"
        }
        if preview.photos.isEmpty && preview.scanStatus == "cancelled" {
            return "El dry-run se canceló; ejecuta un dry-run nuevo antes de aplicar"
        }
        if preview.photos.isEmpty {
            return "No hay fotos elegibles; conserva el manifiesto y revisa las advertencias restantes"
        }
        // Keep an interrupted or cancelled scan recognizable even when it
        // contains rows that are no longer reviewable. Otherwise the generic
        // fresh-run message hides the terminal outcome and its recovery.
        if preview.scanStatus == "interrupted" {
            return "Abre manifest y CSV, verifica Fotos, ejecuta un dry-run nuevo y revisa sus propuestas antes de aplicar"
        }
        if preview.scanStatus == "cancelled" {
            return "Abre manifest y CSV, verifica Fotos, ejecuta un dry-run nuevo y revisa sus propuestas antes de aplicar"
        }
        if isNoopOutcome {
            return "Sin cambios nuevos; conserva el manifiesto y revisa las advertencias restantes"
        }
        if preview.isFreshRunWithoutReviewableRows {
            return "No hay propuestas revisables; conserva el run y ejecuta un dry-run nuevo antes de aplicar"
        }
        if preview.reviewedFromRunID == nil && !preview.canPrepareReview {
            return "Este run no se puede revisar de forma segura; ejecuta un dry-run nuevo antes de aplicar"
        }
        // A reviewed retry can legitimately coexist with verified rows that
        // remain rollback-eligible. The retry CTA is the immediate safe
        // action; do not let the rollback shortcut obscure that scope.
        if isRetryingFailedApply {
            return "Confirma el reintento de las filas fallidas; las filas verificadas no se modificarán"
        }
        if preview.scanStatus == "ready_with_errors",
           preview.reviewedFromRunID == nil,
           preview.photos.contains(where: { $0.isReviewSelectable && $0.hasReviewableChanges }) {
            return hasBlockedRows
                ? "Revisa las filas válidas; las filas con errores quedan bloqueadas"
                : "Revisa las propuestas y las advertencias del scan antes de aplicar"
        }
        if preview.reviewedFromRunID != nil && localReviewSourceAvailable {
            if canRollback {
                return status == .needsAttention
                    ? "Revisar manualmente el estado incierto; puedes revertir solo los cambios verificados"
                    : "Puedes revertir solo los cambios verificados"
            }
            if status == .applied {
                return "Conservar el manifiesto como auditoría"
            }
            if isRetryingFailedApply {
                return "Confirma el reintento de las filas fallidas; las filas verificadas no se modificarán"
            }
            if canContinueReviewedApply && preview.scanStatus == "ready_with_errors" {
                return hasBlockedRows
                    ? "Confirma la aplicación de las filas válidas; las filas con errores quedan bloqueadas"
                    : "Confirma la aplicación tras revisar las advertencias del scan"
            }
            return canContinueReviewedApply
                ? "Confirma la aplicación del manifiesto revisado"
                : "Revisa manualmente el manifiesto y ejecuta un dry-run nuevo antes de aplicar"
        }
        if preview.scanStatus == "interrupted" {
            return "Abre manifest y CSV, verifica Fotos, ejecuta un dry-run nuevo y revisa sus propuestas antes de aplicar"
        }
        if preview.scanStatus == "cancelled" {
            return "Abre manifest y CSV, verifica Fotos, ejecuta un dry-run nuevo y revisa sus propuestas antes de aplicar"
        }
        let needsManualReview = preview.photos.contains { photo in
            ["uncertain", "writing", "cancelled"].contains(photo.applyState)
                || ["uncertain", "removing", "cancelled"].contains(photo.rollbackState)
                || !["ready", "noop", "analysis_failed", "cancelled"].contains(photo.state)
                || !["not_run", "writing", "verified", "noop", "failed", "uncertain", "cancelled"].contains(photo.applyState)
                || !["not_run", "removing", "verified_removed", "failed", "uncertain", "casing_conflict", "already_absent", "cancelled"].contains(photo.rollbackState)
        }
        if needsManualReview {
            if canRollback {
                return "Revisar manualmente el estado incierto; puedes revertir solo los cambios verificados"
            }
            return "Revisar manualmente el estado de la foto"
        }
        if canRollback {
            return "Puedes revertir solo los cambios verificados"
        }
        if preview.canPrepareReview {
            return "Revisar propuestas antes de aplicar"
        }
        switch status {
        case .rolledBack:
            return "No hay cambios verificados pendientes"
        case .applied:
            return "Conservar el manifiesto como auditoría"
        case .noChanges:
            return "No hubo cambios nuevos; conserva el manifiesto como auditoría"
        case .needsAttention:
            return "Revisar errores y ejecutar un nuevo dry-run"
        case .readyToReview:
            return "Revisar propuestas antes de aplicar"
        }
    }

    var status: HistoryRunStatus {
        if preview.hasLegacyMutationState
            || preview.scanStatus != "ready"
            || preview.photos.contains(where: \.needsReviewAttention)
            || hasAnyErrors
            || !preview.hasValidMutationEvidence
            || (preview.reviewedFromRunID != nil && !localReviewSourceAvailable) {
            return .needsAttention
        }
        if preview.photos.isEmpty {
            return .noChanges
        }
        if preview.reviewedFromRunID == nil, preview.isNoopOnlyRun {
            return .noChanges
        }
        if preview.photos.contains(where: { ["verified_removed", "already_absent"].contains($0.rollbackState) }) {
            let hasPendingVerifiedRollback = preview.photos.contains {
                $0.hasRollbackEvidence
            }
            if hasPendingVerifiedRollback {
                return .needsAttention
            }
            return .rolledBack
        }
        if preview.photos.contains(where: { $0.applyState == "verified" }) {
            return .applied
        }
        if preview.reviewedFromRunID != nil,
           preview.photos.contains(where: { $0.applyState == "noop" }),
           preview.photos.allSatisfy({ $0.applyState == "noop" }) {
            return .noChanges
        }
        return .readyToReview
    }

    var displayTitle: String {
        for photo in preview.photos {
            let compact = photo.title
                .split(whereSeparator: \.isWhitespace)
                .joined(separator: " ")
                .trimmingCharacters(in: .whitespacesAndNewlines)
            if !compact.isEmpty {
                return compact
            }
        }
        return "Ejecución local"
    }

    var displayDate: String {
        preview.createdAt
    }

    var summaryText: String {
        let proposed = preview.photos.reduce(0) { $0 + $1.proposedKeywords.count }
        let captions = preview.photos.filter(\.hasCaptionProposal).count
        let photoLabel = preview.photos.count == 1 ? "foto" : "fotos"
        let keywordLabel = proposed == 1 ? "keyword propuesta" : "keywords propuestas"
        let captionLabel = captions == 1 ? "caption propuesto" : "captions propuestos"
        var parts = ["\(preview.photos.count) \(photoLabel)", "\(proposed) \(keywordLabel)"]
        parts.append("\(captions) \(captionLabel)")
        return parts.joined(separator: " · ")
    }

    /// Persisted caption outcomes make a run auditable without exposing the
    /// generated caption text. Keep this separate from `captionRequestText`,
    /// which only describes the dry-run request and pending proposals.
    var captionOutcomeText: String? {
        let counts = preview.photos.reduce(into: [String: Int]()) { counts, photo in
            if photo.hasInvalidCaptionState {
                let knownProposedOutcome = photo.captionState == "proposed"
                    && ["failed", "uncertain", "cancelled", "writing"].contains(photo.applyState)
                if !knownProposedOutcome {
                    counts["requiere revisión manual", default: 0] += 1
                    return
                }
            }
            switch photo.captionState {
            case "proposed":
                guard photo.hasCaptionProposal else { return }
                switch photo.applyState {
                case "failed", "cancelled":
                    counts["fallido", default: 0] += 1
                case "uncertain", "writing":
                    counts["requiere revisión manual", default: 0] += 1
                default:
                    counts["pendiente", default: 0] += 1
                }
            case "preserved":
                counts["conservado", default: 0] += 1
            case "verified":
                counts["aplicado y verificado", default: 0] += 1
            case "removed":
                counts["retirado y verificado", default: 0] += 1
            case "failed":
                counts["fallido", default: 0] += 1
            case "uncertain":
                counts["requiere revisión manual", default: 0] += 1
            default:
                break
            }
        }
        let order = [
            "pendiente", "conservado", "aplicado y verificado", "retirado y verificado",
            "fallido", "requiere revisión manual"
        ]
        let parts = order.compactMap { label -> String? in
            guard let count = counts[label], count > 0 else { return nil }
            return "\(count) \(label)"
        }
        guard !parts.isEmpty else { return nil }
        return "Estado de captions: \(parts.joined(separator: " · "))."
    }

    /// Surface blocked rows beside the aggregate history summary. The main
    /// counts include the full run for auditability, while this separate copy
    /// prevents users from mistaking failed rows for mutation-ready scope.
    var blockedScopeText: String? {
        let blocked = preview.photos.filter { !$0.isReviewSelectable }.count
        guard blocked > 0 else { return nil }
        let photoLabel = blocked == 1 ? "foto bloqueada" : "fotos bloqueadas"
        let verb = blocked == 1 ? "incluirá" : "incluirán"
        let hasReviewableRows = preview.photos.contains { $0.isReviewSelectable && $0.hasReviewableChanges }
        if hasReviewableRows {
            return "\(blocked) \(photoLabel); no se \(verb) en una aplicación. Las filas válidas siguen disponibles para revisión."
        }
        if canRollback {
            return "\(blocked) \(photoLabel); no se \(verb) en una nueva aplicación. Los cambios verificados siguen disponibles para rollback."
        }
        return "\(blocked) \(photoLabel); no se \(verb) en una aplicación. No quedan filas revisables para una nueva aplicación en este run."
    }

    /// A reviewed manifest materializes the user's selection as its remaining
    /// proposals. Keep that approved scope distinct from the original scan
    /// summary so History never describes a mutation-ready copy as merely
    /// speculative. Counts only are exposed; caption text stays in Review.
    var approvedScopeText: String? {
        guard preview.reviewedFromRunID != nil else { return nil }
        // The reviewed manifest is the durable record of what the user
        // approved. After apply/rollback, rows are intentionally no longer
        // selectable, but their proposals must remain visible as historical
        // scope; mutationScopeText reports the separate effective outcome.
        let approvedRows = preview.photos.filter { photo in
            guard ["ready", "noop"].contains(photo.state),
                  photo.filteredScanErrors.isEmpty,
                  photo.hasReviewableChanges else { return false }
            return photo.isReviewSelectable
                || photo.applyState != "not_run"
                || photo.rollbackState != "not_run"
        }
        let affectedPhotos = approvedRows.count
        let keywords = approvedRows.reduce(0) { $0 + $1.proposedKeywords.count }
        let captions = approvedRows.filter(\.hasCaptionProposal).count
        guard affectedPhotos > 0 else { return "Alcance aprobado: sin keywords ni captions." }
        let photoLabel = affectedPhotos == 1 ? "foto" : "fotos"
        let keywordLabel = keywords == 1 ? "keyword" : "keywords"
        let captionLabel = captions == 1 ? "caption" : "captions"
        return "Alcance aprobado: \(affectedPhotos) \(photoLabel) · \(keywords) \(keywordLabel) · \(captions) \(captionLabel)."
    }

    /// Outcome scope is separate from proposed scope so a partial mutation
    /// cannot be mistaken for an untouched dry-run in Historial.
    var mutationScopeText: String? {
        ReviewMutationOutcome(preview: preview).text
    }

    /// Distinguish a recoverable partial dry-run from a mutation state that
    /// must remain blocked until a fresh run. A ready-with-errors manifest can
    /// still review and apply only its valid photo entries.
    var attentionBannerText: String {
        if preview.reviewedFromRunID != nil && !localReviewSourceAvailable {
            return "Solo consulta: la fuente local no está disponible o no es verificable"
        }
        if !preview.hasValidMutationEvidence {
            return "Evidencia de mutación no verificable; Apply y Rollback permanecen bloqueados"
        }
        if isRollbackComplete {
            return "Rollback verificado; no hay cambios verificados pendientes. Revisa las advertencias restantes."
        }
        if preview.photos.isEmpty && preview.scanStatus == "failed" {
            return "El análisis falló; ejecuta un dry-run nuevo antes de aplicar"
        }
        if preview.photos.isEmpty && preview.scanStatus == "cancelled" {
            return "El dry-run se canceló; ejecuta un dry-run nuevo antes de aplicar"
        }
        if preview.photos.isEmpty {
            return "No hay fotos elegibles; no hay nada que aplicar."
        }
        // Preserve the terminal scan outcome even when the partial manifest
        // also has no reviewable rows. The interruption is the actionable
        // reason; the generic empty-review copy would hide it.
        if preview.scanStatus == "interrupted" {
            return "Dry-run interrumpido; ejecuta un dry-run nuevo antes de aplicar"
        }
        if preview.scanStatus == "cancelled" {
            return "Dry-run cancelado; ejecuta un dry-run nuevo antes de aplicar"
        }
        if isNoopOutcome {
            return "Sin cambios nuevos; no hay nada que aplicar. Revisa las advertencias restantes."
        }
        if preview.isFreshRunWithoutReviewableRows {
            return "No hay propuestas revisables; ejecuta un dry-run nuevo antes de aplicar"
        }
        if preview.reviewedFromRunID == nil && !preview.canPrepareReview {
            return "Este run no se puede revisar de forma segura; ejecuta un dry-run nuevo antes de aplicar"
        }
        if isRetryingFailedApply {
            return "Reintento listo; las filas verificadas permanecen sin cambios"
        }
        if canContinueReviewedApply {
            return hasBlockedRows
                ? "Revisión lista; las filas con errores quedan bloqueadas"
                : "Revisión disponible; revisa las advertencias del scan antes de aplicar."
        }
        if isPartialRollback {
            return "Rollback parcial: solo los cambios verificados siguen disponibles"
        }
        if canRollback {
            return "Aplicación verificada; puedes revertir solo los cambios verificados. Revisa las advertencias restantes."
        }
        if preview.scanStatus == "cancelled" {
            return "Dry-run cancelado; ejecuta un dry-run nuevo antes de aplicar"
        }
        if preview.canPrepareReview {
            return hasBlockedRows
                ? "Revisión disponible; las fotos con errores quedan bloqueadas para aplicar"
                : "Revisión disponible; revisa las advertencias del scan antes de aplicar."
        }
        return "Aplicación bloqueada hasta una revisión nueva"
    }

    var confidenceText: String {
        let values = preview.photos.compactMap(\.confidence).filter {
            $0.isFinite && (0 ... 1).contains($0)
        }
        guard !values.isEmpty else { return "Confianza no disponible" }
        let average = values.reduce(0, +) / Double(values.count)
        return "Confianza media \(Int((average * 100).rounded()))%"
    }

    /// Keep low-confidence photos visible from Historial, where the user may
    /// decide whether to open the detailed review before taking any action.
    /// Invalid or missing scores are intentionally excluded.
    var lowConfidenceText: String? {
        let count = preview.photos.filter { $0.confidenceBand == .low }.count
        guard count > 0 else { return nil }
        let photoLabel = count == 1 ? "foto tiene" : "fotos tienen"
        return "\(count) \(photoLabel) confianza baja; no se propusieron cambios aplicables."
    }

    var modelText: String {
        let values = Array(Set(preview.photos.compactMap(\.modelUsed)))
            .filter { model in
                OllamaModelPresentationPolicy.isValidModelName(
                    model.trimmingCharacters(in: .whitespacesAndNewlines)
                )
            }
            .sorted()
            .map(ModelPresentationCopy.display)
        return values.isEmpty ? "Modelo no registrado" : values.joined(separator: " · ")
    }

    var abbreviatedUUID: String {
        guard let uuid = preview.photos.first?.uuid else { return "Sin UUID" }
        return String(uuid.prefix(8))
    }

    var errorCodes: [String] {
        let photoCodes = preview.photos.flatMap { $0.errors.map(\.code) }
        let runCodes = preview.runErrors.map(\.code)
        return Array(Set((photoCodes + runCodes).filter(Self.displayableErrorCodes.contains))).sorted()
    }

    /// Safe copy for the history UI. Internal error codes remain available to
    /// workflow logic but are never rendered to users or exposed as metadata.
    var errorSummaryText: String? {
        guard hasAnyErrors else { return nil }
        let messages = errorCodes.compactMap { Self.humanErrorMessages[$0] }
        if messages.isEmpty { return "La ejecución necesita revisión manual." }
        var seen = Set<String>()
        return messages.filter { seen.insert($0).inserted }.joined(separator: " ")
    }

    private var hasAnyErrors: Bool {
        !preview.runErrors.isEmpty || preview.photos.contains { !$0.errors.isEmpty }
    }

    static func newestFirst(_ values: [Self]) -> [Self] {
        values.sorted {
            if $0.createdAt == $1.createdAt { return $0.runID > $1.runID }
            return $0.createdAt > $1.createdAt
        }
    }
}

struct HistoryLoadResult: Equatable, Sendable {
    let runs: [HistoryRunSummary]
    let rejectedRunCount: Int
    let sessions: [HistorySessionSummary]
    let standaloneRuns: [HistoryRunSummary]

    var displayRows: [HistoryDisplayRow] {
        sessions.flatMap { session in
            [.session(session)] + session.runs.map(HistoryDisplayRow.run)
        } + standaloneRuns.map(HistoryDisplayRow.run)
    }

    init(
        runs: [HistoryRunSummary],
        rejectedRunCount: Int,
        sessions: [HistorySessionSummary] = [],
        standaloneRuns: [HistoryRunSummary]? = nil
    ) {
        self.runs = runs
        self.rejectedRunCount = rejectedRunCount
        self.sessions = sessions
        self.standaloneRuns = standaloneRuns ?? runs
    }

    var rejectedRunNotice: String? {
        guard rejectedRunCount > 0 else { return nil }
        let label = rejectedRunCount == 1 ? "1 ejecución local" : "\(rejectedRunCount) ejecuciones locales"
        return "\(label) no se pudo leer y queda fuera del historial. Conserva el archivo y ejecuta un dry-run nuevo; no se abrirá ni aplicará automáticamente."
    }
}

struct HistoryRunStore {
    private enum CanonicalJSONField: Equatable {
        case absent
        case value(Data)
        case malformed
    }

    static func applicationSupportRunsRoot(fileManager: FileManager = .default) -> URL? {
        fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
            .appendingPathComponent("Photos Local Keyword Indexer", isDirectory: true)
            .appendingPathComponent("runs", isDirectory: true)
    }

    static func isTrustedRunsRoot(_ root: URL, expected: URL) -> Bool {
        let candidatePath = TrustedSystemPath.canonicalPath(for: root)
        let expectedPath = TrustedSystemPath.canonicalPath(for: expected)
        guard candidatePath == expectedPath else { return false }

        var current = URL(fileURLWithPath: "/", isDirectory: true)
        for component in TrustedSystemPath.pathComponents(for: expected).dropFirst() {
            current.appendPathComponent(component, isDirectory: true)
            if let values = try? current.resourceValues(forKeys: [.isSymbolicLinkKey]), values.isSymbolicLink == true {
                return false
            }
        }
        return true
    }

    /// Compare the immutable portion of a source/review pair before showing
    /// an imported review as mutation-ready. Python repeats this check before
    /// writing; keeping the UI gate aligned avoids offering a confirmation
    /// that is guaranteed to fail later in the helper.
    private static func structuralReviewMatchesSource(
        sourceURL: URL,
        source: RunManifestPreview,
        reviewedURL: URL,
        reviewed: RunManifestPreview
    ) -> Bool {
        guard source.photos.count == reviewed.photos.count else { return false }

        for key in ["model", "policy"] {
            let sourceValue = canonicalJSONField(key, from: sourceURL)
            let reviewedValue = canonicalJSONField(key, from: reviewedURL)
            guard case .value = sourceValue,
                  case .value = reviewedValue,
                  sourceValue == reviewedValue else {
                return false
            }
        }

        func identity(_ photo: PreviewPhoto) -> String {
            photo.photosLocalIdentifier ?? photo.uuid
        }
        let sourceIDs = source.photos.map(identity)
        let reviewedIDs = reviewed.photos.map(identity)
        guard Set(sourceIDs).count == sourceIDs.count,
              Set(reviewedIDs).count == reviewedIDs.count else { return false }
        let sourceByID = Dictionary(uniqueKeysWithValues: zip(sourceIDs, source.photos))

        for photo in reviewed.photos {
            guard let original = sourceByID[identity(photo)],
                  photo.uuid == original.uuid,
                  photo.photosLocalIdentifier == original.photosLocalIdentifier,
                  photo.title == original.title,
                  photo.date == original.date,
                  photo.existingKeywords == original.existingKeywords,
                  photo.containsPeople == original.containsPeople,
                  photo.containsText == original.containsText,
                  photo.confidence == original.confidence,
                  photo.modelUsed == original.modelUsed,
                  photo.modelReason == original.modelReason,
                  photo.filteredScanErrors == original.filteredScanErrors else {
                return false
            }
            var sourceKeywordCounts: [String: Int] = [:]
            for keyword in original.proposedKeywords {
                sourceKeywordCounts[keyword, default: 0] += 1
            }
            var reviewedKeywordCounts: [String: Int] = [:]
            for keyword in photo.proposedKeywords {
                reviewedKeywordCounts[keyword, default: 0] += 1
            }
            guard reviewedKeywordCounts.allSatisfy({ $0.value <= (sourceKeywordCounts[$0.key] ?? 0) }) else {
                return false
            }
            if let caption = photo.proposedCaption, caption != original.proposedCaption {
                return false
            }
            if original.state == "ready" {
                guard photo.state == "ready" || photo.state == "noop" else { return false }
            } else if photo.state != original.state {
                return false
            }
        }
        return true
    }

    private static func canonicalJSONField(_ key: String, from url: URL) -> CanonicalJSONField {
        guard let data = try? ManifestFileSecurity.read(from: url),
              let object = try? JSONSerialization.jsonObject(with: data),
              let dictionary = object as? [String: Any] else { return .malformed }
        guard let value = dictionary[key] else { return .absent }
        guard value is [String: Any],
              JSONSerialization.isValidJSONObject(value),
              let canonical = try? JSONSerialization.data(withJSONObject: value, options: [.sortedKeys]) else {
            return .malformed
        }
        return .value(canonical)
    }

    /// A reviewed manifest is safe to hand to the mutating helper only when
    /// its source dry-run is present beside it and the recorded digest agrees.
    /// Python performs the authoritative structural provenance check again;
    /// this value only controls the visible UI gate.
    static func reviewSourceAvailable(
        for preview: RunManifestPreview,
        manifestURL: URL,
        fileManager: FileManager = .default
    ) -> Bool {
        guard let sourceID = preview.reviewedFromRunID,
              let sourceDigest = preview.sourceScanDigest else {
            return preview.reviewedFromRunID == nil
        }
        let root = manifestURL.deletingLastPathComponent().deletingLastPathComponent()
        guard isTrustedRunsRoot(root, expected: root),
              let directories = try? fileManager.contentsOfDirectory(
                  at: root,
                  includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey],
                  options: [.skipsHiddenFiles]
              ) else { return false }
        for directory in directories {
            let values = try? directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
            guard values?.isDirectory == true, values?.isSymbolicLink != true else { continue }
            let candidate = directory.appendingPathComponent("manifest.json", isDirectory: false)
            guard let source = try? RunManifestPreview.load(from: candidate) else { continue }
            if source.runID == sourceID,
               source.reviewedFromRunID == nil,
               source.scanStatus == "ready" || source.scanStatus == "ready_with_errors",
               source.scanDigest == sourceDigest,
               structuralReviewMatchesSource(
                   sourceURL: candidate,
                   source: source,
                   reviewedURL: manifestURL,
                   reviewed: preview
               ) {
                return true
            }
        }
        return false
    }

    static func load(from root: URL, fileManager: FileManager = .default) -> [HistoryRunSummary] {
        loadResult(from: root, fileManager: fileManager).runs
    }

    static func loadResult(from root: URL, fileManager: FileManager = .default) -> HistoryLoadResult {
        guard let expected = applicationSupportRunsRoot(fileManager: fileManager) else {
            return HistoryLoadResult(runs: [], rejectedRunCount: 0)
        }
        return loadResult(from: root, expectedRoot: expected, fileManager: fileManager)
    }

    static func load(from root: URL, expectedRoot: URL, fileManager: FileManager = .default) -> [HistoryRunSummary] {
        loadResult(from: root, expectedRoot: expectedRoot, fileManager: fileManager).runs
    }

    static func loadResult(from root: URL, expectedRoot: URL, fileManager: FileManager = .default) -> HistoryLoadResult {
        guard isTrustedRunsRoot(root, expected: expectedRoot) else {
            return HistoryLoadResult(runs: [], rejectedRunCount: 0)
        }
        let runDirectories = (try? fileManager.contentsOfDirectory(
            at: root,
            includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey],
            options: [.skipsHiddenFiles]
        )) ?? []
        var rejectedRunCount = 0
        let previews = runDirectories.compactMap { directory -> (URL, RunManifestPreview)? in
            let values = try? directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
            guard values?.isDirectory == true, values?.isSymbolicLink != true else { return nil }
            let manifest = directory.appendingPathComponent("manifest.json", isDirectory: false)
            guard let preview = try? RunManifestPreview.load(from: manifest) else {
                rejectedRunCount += 1
                return nil
            }
            return (manifest, preview)
        }
        let summaries = previews.compactMap { item -> HistoryRunSummary? in
            let manifest = item.0
            let preview = item.1
            let isAutonomous: Bool
            do { isAutonomous = try HistoryAutonomyEvidenceStore.isAutonomous(manifestURL: manifest) } catch {
                rejectedRunCount += 1
                return nil
            }
            return HistoryRunSummary(
                manifestURL: manifest,
                preview: preview,
                localReviewSourceAvailable: reviewSourceAvailable(for: preview, manifestURL: manifest, fileManager: fileManager),
                isAutonomous: isAutonomous
            )
        }
        let runs = HistoryRunSummary.newestFirst(summaries)
        let grouping = HistorySessionStore.group(runsRoot: root, runs: runs, fileManager: fileManager)
        return HistoryLoadResult(
            runs: runs,
            rejectedRunCount: rejectedRunCount,
            sessions: grouping.sessions,
            standaloneRuns: grouping.standaloneRuns
        )
    }

    /// Copies a user-selected manifest into the app-owned history root. The
    /// source is read through the same descriptor validation as local history;
    /// it is never moved, followed through a symlink, or trusted by pathname.
    /// A valid sibling preview.csv is copied as a convenience for History.
    @discardableResult
    static func importManifest(
        from source: URL,
        to root: URL,
        fileManager: FileManager = .default
    ) throws -> URL {
        guard source.lastPathComponent == "manifest.json" else {
            throw HistoryManifestImportError.invalidFilename
        }
        guard let data = try? ManifestFileSecurity.read(from: source),
              let preview = try? RunManifestPreview.decodeStrict(data) else {
            throw HistoryManifestImportError.invalidManifest
        }
        guard isTrustedRunsRoot(root, expected: root) else {
            throw HistoryManifestImportError.destinationUnavailable
        }

        var createdImportDirectories: [URL] = []
        do {
            try fileManager.createDirectory(at: root, withIntermediateDirectories: true)
            try fileManager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: root.path)
            let destination = root.appendingPathComponent("imported-\(UUID().uuidString)", isDirectory: true)
            try fileManager.createDirectory(at: destination, withIntermediateDirectories: false)
            createdImportDirectories.append(destination)
            try fileManager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: destination.path)

            let manifest = destination.appendingPathComponent("manifest.json", isDirectory: false)
            try data.write(to: manifest, options: .atomic)
            try fileManager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: manifest.path)

            let sourceCSV = source.deletingLastPathComponent().appendingPathComponent("preview.csv", isDirectory: false)
            if let csv = try? ManifestFileSecurity.read(from: sourceCSV) {
                let destinationCSV = destination.appendingPathComponent("preview.csv", isDirectory: false)
                try csv.write(to: destinationCSV, options: .atomic)
                try fileManager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: destinationCSV.path)
            }

            // Preserve a reviewed package's provenance when the source
            // dry-run is available beside the selected manifest. A standalone
            // reviewed file remains importable but is intentionally
            // consultation-only until its source is imported too.
            if let sourceID = preview.reviewedFromRunID,
               let sourceDigest = preview.sourceScanDigest {
                let sourceRoot = source.deletingLastPathComponent().deletingLastPathComponent()
                let directories = (try? fileManager.contentsOfDirectory(
                    at: sourceRoot,
                    includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey],
                    options: [.skipsHiddenFiles]
                )) ?? []
                for directory in directories {
                    let values = try? directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
                    guard values?.isDirectory == true, values?.isSymbolicLink != true else { continue }
                    let candidate = directory.appendingPathComponent("manifest.json", isDirectory: false)
                    guard let sourceData = try? ManifestFileSecurity.read(from: candidate),
                          let sourcePreview = try? RunManifestPreview.decodeStrict(sourceData),
                          sourcePreview.runID == sourceID,
                          sourcePreview.reviewedFromRunID == nil,
                          sourcePreview.scanStatus == "ready" || sourcePreview.scanStatus == "ready_with_errors",
                          sourcePreview.scanDigest == sourceDigest,
                          structuralReviewMatchesSource(
                              sourceURL: candidate,
                              source: sourcePreview,
                              reviewedURL: source,
                              reviewed: preview
                          ) else { continue }
                    let sourceDestination = root.appendingPathComponent("imported-source-\(UUID().uuidString)", isDirectory: true)
                    try fileManager.createDirectory(at: sourceDestination, withIntermediateDirectories: false)
                    createdImportDirectories.append(sourceDestination)
                    try fileManager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: sourceDestination.path)
                    let copiedSource = sourceDestination.appendingPathComponent("manifest.json", isDirectory: false)
                    try sourceData.write(to: copiedSource, options: .atomic)
                    try fileManager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: copiedSource.path)
                    let sourceCSV = directory.appendingPathComponent("preview.csv", isDirectory: false)
                    if let csv = try? ManifestFileSecurity.read(from: sourceCSV) {
                        let copiedCSV = sourceDestination.appendingPathComponent("preview.csv", isDirectory: false)
                        try csv.write(to: copiedCSV, options: .atomic)
                        try fileManager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: copiedCSV.path)
                    }
                    break
                }
            }
            return manifest
        } catch {
            for directory in createdImportDirectories.reversed() {
                let values = try? directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey])
                if values?.isDirectory == true, values?.isSymbolicLink != true {
                    try? fileManager.removeItem(at: directory)
                }
            }
            throw HistoryManifestImportError.destinationUnavailable
        }
    }
}

/// Copy shown after an import makes provenance visible without exposing the
/// selected file path or any photo data. A reviewed manifest without its
/// source is intentionally useful only for consultation.
enum HistoryImportCopy {
    static let queryOnlyNextAction = "La fuente local no está disponible o no es verificable; usa «Importar manifest» para importar también el dry-run fuente o ejecuta un dry-run nuevo antes de aplicar"

    static func message(preview: RunManifestPreview, sourceAvailable: Bool) -> String {
        guard preview.reviewedFromRunID != nil else {
            return "Manifest importado como copia local. Ya aparece en Historial; el archivo original no se movió."
        }
        if sourceAvailable {
            return "Manifest revisado importado como copia local. La fuente local está disponible; cualquier aplicación seguirá requiriendo revisión y confirmación explícita."
        }
        return "Manifest revisado importado como copia local, pero queda solo para consulta porque no se encontró una fuente local verificable. Usa «Importar manifest» para importar también el dry-run fuente o ejecuta un dry-run nuevo antes de aplicar."
    }
}

/// Centralizes the distinction between a fresh dry-run and a reviewed
/// manifest. A reviewed run must satisfy its stricter provenance/pristine
/// guard before the Preview action can be enabled.
enum ReviewApplyAvailability {
    static func canEnable(
        preview: RunManifestPreview,
        reviewedApplyAllowed: Bool
    ) -> Bool {
        preview.reviewedFromRunID != nil
            ? reviewedApplyAllowed
            : preview.canPrepareReview
    }

    static func hasActionableSelection(
        selectedChangeCount: Int,
        isRetryingFailedApply: Bool
    ) -> Bool {
        selectedChangeCount > 0 || isRetryingFailedApply
    }
}

/// Summarizes the scan itself separately from the approved mutation scope.
/// This keeps a run with no proposals from looking as if no photos were
/// analyzed at all.
struct ReviewScanOutcomeSummary: Equatable, Sendable {
    let analyzedCount: Int
    let noChangeCount: Int
    let failedCount: Int

    init(photos: [PreviewPhoto]) {
        failedCount = photos.filter {
            !$0.errors.isEmpty || !["ready", "noop"].contains($0.state)
        }.count
        analyzedCount = photos.count - failedCount
        noChangeCount = photos.filter {
            $0.state == "noop" && $0.errors.isEmpty && !$0.hasReviewableChanges
        }.count
    }

    var isVisible: Bool { analyzedCount > 0 || failedCount > 0 }

    var text: String {
        let photoLabel = analyzedCount == 1 ? "foto" : "fotos"
        var parts = [
            analyzedCount == 0 ? "Analizadas: ninguna" : "Analizadas: \(analyzedCount) \(photoLabel)"
        ]
        if failedCount > 0 {
            let failedLabel = failedCount == 1 ? "foto" : "fotos"
            let verb = failedCount == 1 ? "requiere" : "requieren"
            parts.append("\(failedCount) \(failedLabel) \(verb) atención")
        }
        if noChangeCount > 0 {
            parts.append("\(noChangeCount) sin cambios propuestos")
        }
        return parts.joined(separator: " · ")
    }

    var accessibilityLabel: String { text }
}

struct ReviewApprovalSummary: Equatable, Sendable {
    let photoCount: Int
    let keywordCount: Int
    let captionCount: Int
    let hasReviewedManifest: Bool
    let hasWarnings: Bool
    let hasBlockedRows: Bool
    let isRetryingFailedApply: Bool

    init(
        photoCount: Int = 0,
        keywordCount: Int,
        captionCount: Int,
        hasReviewedManifest: Bool = false,
        hasWarnings: Bool = false,
        hasBlockedRows: Bool = true,
        isRetryingFailedApply: Bool = false
    ) {
        self.photoCount = max(0, photoCount)
        self.keywordCount = keywordCount
        self.captionCount = captionCount
        self.hasReviewedManifest = hasReviewedManifest
        self.hasWarnings = hasWarnings
        self.hasBlockedRows = hasBlockedRows
        self.isRetryingFailedApply = isRetryingFailedApply
    }

    var photoText: String {
        guard photoCount > 0 else { return "Ninguna foto afectada" }
        return "\(photoCount) " + (photoCount == 1 ? "foto afectada" : "fotos afectadas")
    }

    var keywordText: String {
        "\(keywordCount) " + (keywordCount == 1 ? "keyword aprobada" : "keywords aprobadas")
    }

    var captionText: String {
        "\(captionCount) " + (captionCount == 1 ? "caption aprobado" : "captions aprobados")
    }

    var confirmationSummaryText: String {
        let action = hasReviewedManifest ? "escribir" : "preparar la revisión"
        return "Antes de \(action), se comprobarán \(photoText), \(keywordText) y \(captionText)."
    }

    var selectedChangeCount: Int {
        keywordCount + captionCount
    }

    /// Names the exact mutation represented by the confirmation button. A
    /// mixed or empty selection should never be summarized with an ambiguous
    /// generic action label.
    var applyActionText: String {
        switch (keywordCount, captionCount) {
        case (0, 0):
            return "Sin cambios seleccionados"
        case (_, 0):
            return "Aplicar \(keywordCount) " + (keywordCount == 1 ? "keyword" : "keywords")
        case (0, _):
            return "Aplicar \(captionCount) " + (captionCount == 1 ? "caption" : "captions")
        default:
            let keywords = "\(keywordCount) " + (keywordCount == 1 ? "keyword" : "keywords")
            let captions = "\(captionCount) " + (captionCount == 1 ? "caption" : "captions")
            return "Aplicar \(keywords) y \(captions)"
        }
    }

    /// Compact scope for the destructive CTA. The full confirmation sheet
    /// remains authoritative, but repeating the counts on the button prevents
    /// a long review list from hiding what the next click will write.
    var actionScopeText: String {
        if selectedChangeCount == 0 && isRetryingFailedApply {
            return "Solo filas fallidas · cambios verificados sin modificar"
        }
        guard selectedChangeCount > 0 else { return "Sin cambios seleccionados" }
        return "\(photoText) · \(keywordText) · \(captionText)"
    }

    var nextSafeAction: String {
        if hasReviewedManifest && isRetryingFailedApply {
            return "Confirma el reintento de las filas fallidas; las filas verificadas no se modificarán"
        }
        guard selectedChangeCount > 0 else {
            return "Selecciona al menos una keyword o caption para preparar la aplicación"
        }
        if hasWarnings {
            if hasBlockedRows {
                return hasReviewedManifest
                    ? "Confirma la aplicación de las filas válidas; las filas con errores quedan bloqueadas"
                    : "Prepara una revisión solo con las filas válidas; las filas con errores quedan bloqueadas"
            }
            return hasReviewedManifest
                ? "Confirma la aplicación tras revisar las advertencias del scan"
                : "Prepara una revisión tras revisar las advertencias del scan"
        }
        return hasReviewedManifest
            ? "Confirma la aplicación del manifiesto revisado"
            : "Preparar un manifiesto revisado antes de aplicar"
    }
}

/// The bulk control changes keywords only. Captions remain an explicit
/// per-photo decision, so the label and VoiceOver hint must say so plainly.
enum ReviewBulkKeywordCopy {
    static func shouldShowToolbar(
        isFreshReview: Bool,
        canSelectAllKeywords: Bool
    ) -> Bool {
        isFreshReview && canSelectAllKeywords
    }

    static func shouldShowCaptionDisclosure(
        isFreshReview: Bool,
        hasCaptionProposals: Bool,
        canSelectAllKeywords: Bool
    ) -> Bool {
        hasCaptionProposals && !shouldShowToolbar(
            isFreshReview: isFreshReview,
            canSelectAllKeywords: canSelectAllKeywords
        )
    }

    static func buttonTitle(allKeywordsSelected: Bool) -> String {
        allKeywordsSelected ? "Quitar todas las keywords" : "Seleccionar todas las keywords"
    }

    static let accessibilityHint = "Solo cambia la selección de keywords; las captions se aprueban por separado en cada foto."
}

/// Read-only projection of the effective mutation outcome. It is shared by
/// Preview and Historial so aggregate result counts cannot drift from the
/// per-run history projection.
struct ReviewMutationOutcome: Equatable, Sendable {
    let text: String?

    init(preview: RunManifestPreview) {
        let currentlyVerified = preview.photos.filter {
            $0.hasRollbackEvidence && $0.rollbackState != "verified_removed"
        }
        let verifiedKeywords = currentlyVerified.reduce(0) { $0 + $1.appliedKeywords.count }
        let verifiedCaptions = currentlyVerified.filter {
            $0.appliedCaption?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
        }.count
        let rolledBackPhotos = preview.photos.filter { $0.rollbackState == "verified_removed" }
        let rolledBackKeywords = rolledBackPhotos.reduce(0) { $0 + $1.rolledBackKeywords.count }
        let rolledBackCaptions = rolledBackPhotos.filter {
            $0.appliedCaption?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
        }.count
        let alreadyAbsent = preview.photos.filter { $0.rollbackState == "already_absent" }.count
        let noOp = preview.photos.filter {
            $0.applyState == "noop" && $0.rollbackState == "not_run"
        }.count
        let manual = preview.photos.filter { photo in
            ["failed", "uncertain", "writing", "cancelled"].contains(photo.applyState)
                || ["failed", "uncertain", "removing", "casing_conflict", "cancelled"].contains(photo.rollbackState)
        }.count

        guard !currentlyVerified.isEmpty || !rolledBackPhotos.isEmpty || alreadyAbsent > 0 || noOp > 0 || manual > 0 else {
            text = nil
            return
        }
        var parts: [String] = []
        if !currentlyVerified.isEmpty {
            let photoLabel = currentlyVerified.count == 1 ? "foto" : "fotos"
            let keywordLabel = verifiedKeywords == 1 ? "keyword" : "keywords"
            let captionLabel = verifiedCaptions == 1 ? "caption" : "captions"
            parts.append("Verificado: \(currentlyVerified.count) \(photoLabel) · \(verifiedKeywords) \(keywordLabel) · \(verifiedCaptions) \(captionLabel).")
        }
        if noOp > 0 {
            let photoLabel = noOp == 1 ? "foto" : "fotos"
            parts.append("Sin cambios nuevos: \(noOp) \(photoLabel).")
        }
        if !rolledBackPhotos.isEmpty {
            let photoLabel = rolledBackPhotos.count == 1 ? "foto" : "fotos"
            let keywordLabel = rolledBackKeywords == 1 ? "keyword" : "keywords"
            let captionLabel = rolledBackCaptions == 1 ? "caption" : "captions"
            parts.append("Rollback verificado: \(rolledBackPhotos.count) \(photoLabel) · \(rolledBackKeywords) \(keywordLabel) · \(rolledBackCaptions) \(captionLabel).")
        }
        if alreadyAbsent > 0 {
            let photoLabel = alreadyAbsent == 1 ? "foto" : "fotos"
            parts.append("Sin cambios que eliminar: \(alreadyAbsent) \(photoLabel) ya estaban ausentes.")
        }
        if manual > 0 {
            let photoLabel = manual == 1 ? "foto" : "fotos"
            parts.append("Atención manual: \(manual) \(photoLabel).")
        }
        text = parts.joined(separator: " ")
    }
}

/// Read-only projection of the scope approved by a reviewed manifest after a
/// mutation has started. It is intentionally separate from `ReviewSelection`:
/// historical counts must remain visible without making Apply selectable again.
struct ReviewHistoricalScope: Equatable, Sendable {
    let isHistorical: Bool
    let text: String?

    init(preview: RunManifestPreview) {
        guard preview.reviewedFromRunID != nil else {
            isHistorical = false
            text = nil
            return
        }

        let hasMutation = preview.photos.contains {
            $0.applyState != "not_run" || $0.rollbackState != "not_run"
        }
        guard hasMutation else {
            isHistorical = false
            text = nil
            return
        }

        isHistorical = true
        let approvedRows = preview.photos.filter { photo in
            guard ["ready", "noop"].contains(photo.state),
                  photo.filteredScanErrors.isEmpty,
                  photo.hasReviewableChanges else { return false }
            return photo.isReviewSelectable
                || photo.applyState != "not_run"
                || photo.rollbackState != "not_run"
        }
        let affectedPhotos = approvedRows.count
        let keywords = approvedRows.reduce(0) { $0 + $1.proposedKeywords.count }
        let captions = approvedRows.filter(\.hasCaptionProposal).count
        guard affectedPhotos > 0 else {
            text = "Alcance aprobado: sin keywords ni captions."
            return
        }
        let photoLabel = affectedPhotos == 1 ? "foto" : "fotos"
        let keywordLabel = keywords == 1 ? "keyword" : "keywords"
        let captionLabel = captions == 1 ? "caption" : "captions"
        text = "Alcance aprobado: \(affectedPhotos) \(photoLabel) · \(keywords) \(keywordLabel) · \(captions) \(captionLabel)."
    }
}

/// Review-time confidence signal scoped to the changes currently approved.
/// It keeps a long photo list from hiding medium-confidence decisions before
/// the person opens the final confirmation sheet.
struct ReviewConfidenceSummary: Equatable, Sendable {
    let affectedPhotoCount: Int
    let mediumCount: Int
    let lowCount: Int

    init(photos: [PreviewPhoto], approvedPhotoIDs: Set<String>) {
        let affected = photos.filter { approvedPhotoIDs.contains($0.uuid) }
        affectedPhotoCount = affected.count
        mediumCount = affected.filter { $0.confidenceBand == .medium }.count
        lowCount = affected.filter { $0.confidenceBand == .low }.count
    }

    var isVisible: Bool {
        mediumCount > 0 || lowCount > 0
    }

    var text: String {
        guard isVisible else {
            return "Las fotos aprobadas tienen confianza alta o no disponible."
        }
        var parts: [String] = []
        if mediumCount > 0 {
            parts.append("\(mediumCount) " + (mediumCount == 1 ? "foto tiene" : "fotos tienen") + " confianza media")
        }
        if lowCount > 0 {
            parts.append("\(lowCount) " + (lowCount == 1 ? "foto tiene" : "fotos tienen") + " confianza baja")
        }
        return "Revisa antes de aplicar: " + parts.joined(separator: " y ") + "."
    }

    var accessibilityLabel: String {
        "\(text) \(affectedPhotoCount) " + (affectedPhotoCount == 1 ? "foto aprobada." : "fotos aprobadas.")
    }
}

/// Keeps partial dry-runs honest at the point of approval. Failed or
/// cancelled photos remain visible for audit but cannot enter the mutation
/// selection.
struct ReviewBlockedSummary: Equatable, Sendable {
    let blockedPhotoCount: Int

    var isVisible: Bool { blockedPhotoCount > 0 }

    /// Keep the all-blocked state distinct from a partial scan: there is no
    /// valid review path to offer when every row is excluded.
    var text: String {
        guard isVisible else { return "Todas las fotos del run son revisables." }
        let blockedText = blockedPhotoCount == 1
            ? "1 foto queda bloqueada y no se aplicará"
            : "\(blockedPhotoCount) fotos quedan bloqueadas y no se aplicarán"
        return blockedText + (hasReviewableRows
            ? "; las propuestas válidas siguen disponibles para revisión."
            : "; no quedan propuestas válidas para revisión.")
    }

    private let hasReviewableRows: Bool

    init(photos: [PreviewPhoto]) {
        blockedPhotoCount = photos.filter { !$0.isReviewSelectable }.count
        hasReviewableRows = photos.contains { $0.isReviewSelectable && $0.hasReviewableChanges }
    }

    var accessibilityLabel: String { text }
}

/// A compact recovery surface for a review that cannot safely continue. The
/// review screen should explain the reason and point to the audit surface
/// without offering a retry that could mutate an interrupted or already
/// mutated manifest.
struct ReviewRecoverySummary: Equatable, Sendable {
    let isVisible: Bool
    let title: String
    let detail: String
    let actionTitle: String
    let symbolName: String
    private let preview: RunManifestPreview
    private let mutationRequiresManualReview: Bool

    init(
        preview: RunManifestPreview,
        mutationRequiresManualReview: Bool = false,
        consultationOnly: Bool = false
    ) {
        self.preview = preview
        self.mutationRequiresManualReview = mutationRequiresManualReview
        // A reviewed manifest without its verified source is consultation-only
        // regardless of other stale mutation flags. Surface that hard gate
        // first so users are not told to inspect a mutation they cannot safely
        // authorize from this package.
        if consultationOnly {
            isVisible = true
            title = "Este manifest es solo para consulta"
            detail = "La fuente local no está disponible o no es verificable. Abre Historial para importar también el dry-run fuente o ejecuta un dry-run nuevo antes de aplicar."
            actionTitle = "Abrir Historial"
            symbolName = "lock.shield.fill"
        } else if mutationRequiresManualReview {
            isVisible = true
            title = "Aplicación pausada por revisión manual"
            detail = "El helper terminó sin una verificación completa. Abre Historial para revisar el manifiesto y Fotos; no se reintentará ninguna escritura automáticamente."
            actionTitle = "Abrir Historial"
            symbolName = "exclamationmark.triangle.fill"
        } else if preview.scanStatus == "interrupted" {
            isVisible = true
            title = "Este dry-run se interrumpió"
            detail = "Revisa el manifiesto y el CSV en Historial, confirma el estado de Fotos y ejecuta un dry-run nuevo antes de aplicar."
            actionTitle = "Abrir Historial"
            symbolName = "pause.circle.fill"
        } else if preview.scanStatus == "cancelled" {
            isVisible = true
            title = "Este dry-run se canceló"
            detail = "La cancelación detuvo el análisis sin aplicar cambios en Fotos. Revisa el run en Historial y ejecuta un dry-run nuevo antes de aplicar."
            actionTitle = "Abrir Historial"
            symbolName = "pause.circle.fill"
        } else if preview.scanStatus != "ready" && preview.scanStatus != "ready_with_errors" {
            isVisible = true
            title = "Este run no está listo para aplicar"
            let safeError = preview.runErrors
                .map { HumanErrorCopy.message(for: $0.code) }
                .first { $0 != "La ejecución necesita revisión manual." }
            detail = (safeError ?? "Corrige el error indicado y ejecuta un dry-run nuevo.")
                + " Las propuestas de este run permanecen en solo lectura."
            actionTitle = "Abrir Historial"
            symbolName = "exclamationmark.octagon.fill"
        } else if preview.photos.isEmpty {
            isVisible = true
            title = "No hay fotos elegibles"
            detail = preview.scanStatus == "ready_with_errors"
                ? "El dry-run terminó, pero no hay fotos elegibles para revisar o aplicar. Revisa las advertencias restantes en Historial."
                : "El dry-run terminó sin fotos elegibles para revisar o aplicar. Conserva el run como auditoría local y usa Revisión para continuar con otra foto."
            actionTitle = "Abrir Historial"
            symbolName = "checkmark.circle"
        } else if preview.isNoopOnlyRun {
            isVisible = true
            title = "Sin cambios nuevos"
            detail = "El dry-run terminó sin keywords ni captions nuevas para revisar o aplicar. Conserva el run como auditoría local."
            actionTitle = "Abrir Historial"
            symbolName = "checkmark.circle"
        } else if preview.isFreshRunWithoutReviewableRows {
            isVisible = true
            title = "No hay propuestas revisables"
            detail = "Este dry-run no tiene filas válidas para revisar; conserva el run y ejecuta un dry-run nuevo antes de aplicar."
            actionTitle = "Abrir Historial"
            symbolName = "lock.shield.fill"
        } else if !preview.canPrepareReview {
            isVisible = true
            title = "Este run es de solo lectura"
            detail = "Ya fue revisado, aplicado o revertido. Conserva este run como auditoría y ejecuta un dry-run nuevo para preparar otra aplicación."
            actionTitle = "Abrir Historial"
            symbolName = "lock.shield.fill"
        } else {
            isVisible = false
            title = ""
            detail = ""
            actionTitle = ""
            symbolName = "checkmark.circle"
        }
    }

    var accessibilityLabel: String {
        guard isVisible else { return "" }
        return "\(title). \(detail)"
    }

    /// A fresh run with no reviewable scope should offer a direct route to a
    /// new read-only dry-run. Keep reviewed, mutated, and manual-review runs
    /// on their audit path so this shortcut never competes with a safety gate.
    var canStartNewDryRun: Bool {
        guard isVisible,
              preview.reviewedFromRunID == nil,
              !mutationRequiresManualReview,
              preview.photos.allSatisfy({
                  $0.applyState == "not_run" && $0.rollbackState == "not_run"
              }) else {
            return false
        }
        return preview.photos.isEmpty
            || ["failed", "cancelled", "interrupted"].contains(preview.scanStatus)
            || preview.isNoopOnlyRun
            || preview.isFreshRunWithoutReviewableRows
    }

    var newDryRunAccessibilityHint: String {
        "Abre Revisión para continuar el análisis local; no modifica Fotos hasta que guardes una foto explícitamente."
    }
}

/// Keeps the review summary honest when a reviewed manifest is available but
/// another safety guard still blocks mutation.
enum ReviewMutationCancellationCopy {
    static func buttonTitle(isCancellationRequested: Bool) -> String {
        isCancellationRequested ? "Cancelación solicitada…" : "Cancelar operación"
    }

    static func canRequestCancellation(isCancellationRequested: Bool) -> Bool {
        !isCancellationRequested
    }

    static func accessibilityHint(isCancellationRequested: Bool) -> String {
        if isCancellationRequested {
            return "La cancelación es cooperativa. Las escrituras ya iniciadas se verificarán o quedarán en revisión manual; revisa Historial al finalizar."
        }
        return "Solicita una cancelación cooperativa. Una escritura ya iniciada puede terminar o quedar en revisión manual; revisa Historial al finalizar."
    }
}

enum ReviewNextActionCopy {
    static func text(
        sourceAvailable: Bool,
        canContinue: Bool,
        hasSelectedChanges: Bool = true,
        hasWarnings: Bool = false,
        hasBlockedRows: Bool = true,
        isRetryingFailedApply: Bool = false
    ) -> String {
        guard sourceAvailable else {
            return HistoryImportCopy.queryOnlyNextAction
        }
        if canContinue && isRetryingFailedApply {
            return "Confirma el reintento de las filas fallidas; las filas verificadas no se modificarán"
        }
        if canContinue && !hasSelectedChanges {
            return "Selecciona al menos una keyword o caption para preparar la aplicación"
        }
        if canContinue && hasWarnings {
            return hasBlockedRows
                ? "Confirma la aplicación de las filas válidas; las filas con errores quedan bloqueadas"
                : "Confirma la aplicación tras revisar las advertencias del scan"
        }
        return canContinue
            ? "Confirma la aplicación del manifiesto revisado"
            : "Revisa manualmente el manifiesto y ejecuta un dry-run nuevo antes de aplicar"
    }
}

/// Keeps the Preview footer aligned with the recovery card for a fresh run.
/// A failed, cancelled, interrupted, or empty dry-run has no selectable scope;
/// it must never ask the user to select a change that cannot exist.
enum ReviewFreshNextActionCopy {
    static func text(for preview: RunManifestPreview, fallback: String) -> String {
        guard preview.reviewedFromRunID == nil else { return fallback }
        switch preview.scanStatus {
        case "failed":
            return "El análisis falló; ejecuta un dry-run nuevo antes de aplicar"
        case "interrupted":
            return "El dry-run se interrumpió; ejecuta un dry-run nuevo antes de aplicar"
        case "cancelled":
            return "El dry-run se canceló; ejecuta un dry-run nuevo antes de aplicar"
        default:
            break
        }
        if preview.photos.isEmpty {
            return "No hay fotos elegibles; conserva el run y ejecuta un dry-run nuevo si esperabas encontrar fotos"
        }
        if preview.isFreshRunWithoutReviewableRows {
            return "No hay propuestas revisables; conserva el run y ejecuta un dry-run nuevo antes de aplicar"
        }
        if !preview.canPrepareReview {
            return "Este run no se puede revisar de forma segura; ejecuta un dry-run nuevo antes de aplicar"
        }
        return fallback
    }
}

/// Keeps the review screen heading aligned with the lifecycle represented by
/// the manifest. A completed apply or rollback is a result, not another
/// pre-apply review; a failed or empty dry-run likewise must not present an
/// apply-oriented heading.
enum ReviewScreenCopy {
    static func title(for preview: RunManifestPreview) -> String {
        if preview.photos.contains(where: { $0.rollbackState != "not_run" }) {
            return "Resultado del rollback"
        }
        if preview.photos.contains(where: { $0.applyState != "not_run" }) {
            return "Resultado de la aplicación"
        }
        if preview.reviewedFromRunID != nil {
            return "Revisión aprobada antes de aplicar"
        }
        if preview.canPrepareReview {
            return "Revisión antes de aplicar"
        }
        return "Resultado del dry-run"
    }
}

/// Keeps a disabled mutation action honest about why it cannot proceed.
enum ReviewApplyButtonCopy {
    /// Preparing a reviewed manifest is read-only with respect to Apple
    /// Photos. Reserve the destructive affordance for the confirmed apply
    /// action, which is the first point where PhotoScript can write metadata.
    static func requiresDestructiveTreatment(reviewed: Bool, available: Bool) -> Bool {
        reviewed && available
    }

    static func title(
        reviewed: Bool,
        available: Bool,
        hasWarnings: Bool = false,
        hasBlockedRows: Bool = true,
        isRetryingFailedApply: Bool = false
    ) -> String {
        guard available else { return "Aplicación bloqueada" }
        if reviewed && isRetryingFailedApply {
            return "Reintentar filas fallidas"
        }
        if hasWarnings {
            if hasBlockedRows {
                return reviewed ? "Aplicar filas válidas" : "Preparar revisión con filas válidas"
            }
            return reviewed ? "Aplicar manifiesto revisado" : "Preparar revisión"
        }
        return reviewed ? "Aplicar manifiesto revisado" : "Preparar aplicación"
    }

    static func accessibilityHint(
        reviewed: Bool,
        available: Bool,
        requiresManualReview: Bool,
        hasSelectedChanges: Bool = true,
        hasWarnings: Bool = false,
        hasBlockedRows: Bool = true,
        sourceAvailable: Bool = false,
        isRetryingFailedApply: Bool = false
    ) -> String {
        if requiresManualReview {
            return "Revisa el run en Historial antes de intentar otra mutación."
        }
        if reviewed && !available {
            if sourceAvailable {
                return "Este manifest queda solo para consulta por su estado actual. Revisa Historial y ejecuta un dry-run nuevo antes de aplicar."
            }
            return "Este manifest queda solo para consulta. \(HistoryImportCopy.queryOnlyNextAction)."
        }
        if reviewed && isRetryingFailedApply {
            return "La siguiente pantalla permitirá reintentar solo las filas fallidas; las filas aplicadas y verificadas no se modificarán y requerirá confirmación explícita."
        }
        if !available {
            return "Ejecuta un dry-run nuevo y revisa sus propuestas antes de aplicar."
        }
        if !hasSelectedChanges {
            return "Selecciona al menos una keyword o caption para preparar la aplicación."
        }
        if hasWarnings {
            if reviewed {
                return hasBlockedRows
                    ? "La siguiente pantalla aplicará solo las filas válidas; las filas con errores están bloqueadas y requerirá confirmación explícita."
                    : "La siguiente pantalla mostrará el detalle exacto; revisa las advertencias del scan y confirma explícitamente."
            }
            return hasBlockedRows
                ? "La siguiente pantalla preparará una copia revisada con solo las filas válidas; las filas con errores quedan bloqueadas y no se aplicarán sin confirmación explícita."
                : "Abre las propuestas y revisa las advertencias del scan; no se aplicará nada sin confirmación explícita."
        }
        return "La siguiente pantalla mostrará el detalle exacto y requerirá confirmación explícita."
    }

    static func accessibilityLabel(
        reviewed: Bool,
        available: Bool,
        summary: String,
        hasWarnings: Bool = false,
        hasBlockedRows: Bool = true,
        isRetryingFailedApply: Bool = false,
        requiresManualReview: Bool = false
    ) -> String {
        let actionTitle = title(
            reviewed: reviewed,
            available: available,
            hasWarnings: hasWarnings,
            hasBlockedRows: hasBlockedRows,
            isRetryingFailedApply: isRetryingFailedApply
        )
        if !available && requiresManualReview {
            return "\(actionTitle). Revisa el run en Historial antes de intentar otra mutación."
        }
        if reviewed && !available {
            return "\(actionTitle). Este manifest queda solo para consulta."
        }
        if !available {
            return "\(actionTitle). Ejecuta un dry-run nuevo y revisa sus propuestas antes de aplicar."
        }
        return "\(actionTitle). \(summary)"
    }
}

/// Keeps History's review/apply control aligned with Preview without making
/// an unavailable action look like a confirmation button.
enum HistoryReviewActionCopy {
    static func title(
        reviewed: Bool,
        available: Bool,
        hasWarnings: Bool = false,
        hasBlockedRows: Bool = true,
        isRetryingFailedApply: Bool = false,
        mutationBlockMessage: String? = nil
    ) -> String {
        if reviewed {
            guard available else { return "Aplicación bloqueada" }
            if mutationBlockMessage != nil { return "Abrir revisión en solo lectura" }
            if isRetryingFailedApply { return "Abrir reintento revisado" }
            if hasWarnings && hasBlockedRows { return "Abrir revisión de filas válidas" }
            return "Abrir aplicación revisada"
        }
        if available && hasWarnings {
            return hasBlockedRows ? "Revisar filas válidas" : "Revisar propuestas"
        }
        return available ? "Revisar" : "Revisión bloqueada"
    }

    static func accessibilityLabel(
        reviewed: Bool,
        available: Bool,
        hasWarnings: Bool = false,
        hasBlockedRows: Bool = true,
        isRetryingFailedApply: Bool = false,
        mutationBlockMessage: String? = nil
    ) -> String {
        if reviewed {
            return available
                ? title(
                    reviewed: true,
                    available: true,
                    hasWarnings: hasWarnings,
                    hasBlockedRows: hasBlockedRows,
                    isRetryingFailedApply: isRetryingFailedApply,
                    mutationBlockMessage: mutationBlockMessage
                )
                : "Aplicación bloqueada"
        }
        if available && hasWarnings {
            return hasBlockedRows ? "Revisar filas válidas" : "Revisar propuestas"
        }
        return available ? "Revisar propuestas" : "Revisión bloqueada"
    }

    static func accessibilityHint(
        reviewed: Bool,
        available: Bool,
        requiresManualReview: Bool,
        hasWarnings: Bool = false,
        hasBlockedRows: Bool = true,
        sourceAvailable: Bool = false,
        isRetryingFailedApply: Bool = false,
        mutationBlockMessage: String? = nil
    ) -> String {
        if reviewed, available, let mutationBlockMessage {
            return "Abre la revisión local sin consultar ni modificar Fotos. \(mutationBlockMessage)"
        }
        if reviewed {
            return ReviewApplyButtonCopy.accessibilityHint(
                reviewed: true,
                available: available,
                requiresManualReview: requiresManualReview,
                hasWarnings: hasWarnings,
                hasBlockedRows: hasBlockedRows,
                sourceAvailable: sourceAvailable,
                isRetryingFailedApply: isRetryingFailedApply
            )
        }
        if available && hasWarnings {
            return hasBlockedRows
                ? "Abre las propuestas válidas; las filas con errores quedan bloqueadas y no se aplicarán sin confirmación explícita."
                : "Abre las propuestas y revisa las advertencias del scan; no se aplicará nada sin confirmación explícita."
        }
        return available
            ? "Abre las propuestas para seleccionar keywords y captions antes de aplicar."
            : "Esta ejecución no puede revisarse; ejecuta un dry-run nuevo."
    }
}

/// Deterministic filters for the review list. Keeping this policy in the
/// model lets the UI make a large run scannable without changing the
/// manifest, the selected values, or the apply contract.
enum ReviewPhotoFilter: String, CaseIterable, Identifiable, Sendable {
    case all
    case changes
    case captions
    case attention

    var id: String { rawValue }

    var title: String {
        switch self {
        case .all: return "Todas"
        case .changes: return "Cambios"
        case .captions: return "Captions"
        case .attention: return "Atención"
        }
    }

    var systemImage: String {
        switch self {
        case .all: return "square.grid.2x2"
        case .changes: return "tag"
        case .captions: return "text.quote"
        case .attention: return "exclamationmark.triangle"
        }
    }

    func includes(_ photo: PreviewPhoto) -> Bool {
        switch self {
        case .all:
            return true
        case .changes:
            return photo.applyState == "not_run"
                && photo.isReviewSelectable
                && photo.hasReviewableChanges
        case .captions:
            return photo.applyState == "not_run"
                && photo.isReviewSelectable
                && photo.hasCaptionProposal
        case .attention:
            return photo.needsReviewAttention
        }
    }

    func emptyMessage(for total: Int) -> String {
        switch self {
        case .all:
            return total == 0 ? "Este run no contiene fotos elegibles." : ""
        case .changes:
            return "No hay keywords o captions pendientes de aprobación."
        case .captions:
            return "No hay captions revisables en este run."
        case .attention:
            return "No hay fotos con errores o estados que requieran atención."
        }
    }

    func emptyDescription(for total: Int) -> String {
        if self == .all && total == 0 {
            return "No hay otra vista que mostrar; conserva este run como auditoría y ejecuta un dry-run nuevo si esperabas encontrar fotos."
        }
        return "Cambia el filtro para revisar otra parte del run."
    }
}

/// Stable presentation groups for the complete review list. A photo appears
/// in exactly one group so the user can scan the effective decision without
/// changing the independent keyword/caption selection state.
enum ReviewPhotoGroup: String, CaseIterable, Hashable, Identifiable, Sendable {
    case attention
    case captions
    case changes
    case unchanged

    var id: String { rawValue }

    var title: String {
        switch self {
        case .attention: return "Atención"
        case .captions: return "Captions para revisar"
        case .changes: return "Cambios de keywords"
        case .unchanged: return "Sin cambios pendientes"
        }
    }

    var systemImage: String {
        switch self {
        case .attention: return "exclamationmark.triangle"
        case .captions: return "text.quote"
        case .changes: return "tag"
        case .unchanged: return "checkmark.circle"
        }
    }

    func includes(_ photo: PreviewPhoto) -> Bool {
        switch self {
        case .attention:
            return photo.needsReviewAttention
        case .captions:
            return !photo.needsReviewAttention
                && photo.isReviewSelectable
                && photo.applyState == "not_run"
                && photo.hasCaptionProposal
        case .changes:
            return !photo.needsReviewAttention
                && photo.isReviewSelectable
                && photo.applyState == "not_run"
                && photo.hasReviewableChanges
                && !photo.hasCaptionProposal
        case .unchanged:
            return ReviewPhotoGroup.allCases.dropLast().allSatisfy { !$0.includes(photo) }
        }
    }
}

struct ReviewPhotoGroupSection: Equatable, Identifiable, Sendable {
    let group: ReviewPhotoGroup
    let photos: [PreviewPhoto]

    var id: ReviewPhotoGroup { group }
}

struct ReviewPhotoGroups: Equatable, Sendable {
    let sections: [ReviewPhotoGroupSection]

    init(photos: [PreviewPhoto]) {
        sections = ReviewPhotoGroup.allCases.compactMap { group in
            let matching = photos.filter(group.includes)
            return matching.isEmpty ? nil : ReviewPhotoGroupSection(group: group, photos: matching)
        }
    }
}

private func reviewComparisonKey(_ value: String) -> String {
    let compatible = value.precomposedStringWithCompatibilityMapping
    return compatible
        .split(whereSeparator: \.isWhitespace)
        .joined(separator: " ")
        .trimmingCharacters(in: .whitespacesAndNewlines)
        .lowercased()
}

extension PreviewPhoto {
    /// Rollback is available only when a verified read-back recorded at least
    /// one keyword or caption. A bare `verified` state is not enough evidence
    /// for a destructive action and must remain audit-only in the UI.
    var hasRollbackEvidence: Bool {
        applyState == "verified"
            && rollbackState == "not_run"
            && (!appliedKeywords.isEmpty || appliedCaption?.isEmpty == false)
    }

    var isRetryableApplyRow: Bool {
        ["failed", "cancelled"].contains(applyState)
            && rollbackState == "not_run"
            && appliedKeywords.isEmpty
            && appliedCaption == nil
            && hasReviewableChanges
            && !hasConfidenceBelowReviewThreshold
            && !hasUnavailableReviewConfidence
            && errors.contains { $0.stage == "apply" }
    }

    /// A reviewed retry also resumes rows that never reached a write before
    /// an earlier interruption. Verified/no-op rows stay outside this scope
    /// because the helper will not mutate them again.
    var isReviewedApplyCandidate: Bool {
        if applyState == "not_run" {
            return isReviewSelectable && hasReviewableChanges
        }
        return isRetryableApplyRow
    }

    var confidenceBand: ReviewConfidenceBand {
        guard let confidence, confidence.isFinite, (0 ... 1).contains(confidence) else {
            return .unavailable
        }
        if confidence >= 0.85 { return .high }
        if confidence >= 0.60 { return .medium }
        return .low
    }

    var confidenceText: String {
        guard let confidence, confidenceBand != .unavailable else { return "No disponible" }
        return "\(Int((confidence * 100).rounded()))% · \(confidenceBand.label)"
    }

    var confidenceAccessibilityLabel: String {
        "Confianza: \(confidenceText)."
    }

    /// A proposal without a score is a review-blocking fact, not an absent
    /// presentation detail. Keep it visible in the same confidence surface as
    /// valid percentages while quiet no-op rows remain compact.
    var shouldDisplayConfidence: Bool {
        confidence != nil || hasReviewableChanges
    }

    /// Keep the review boundary explicit without rendering model-generated
    /// explanations, which may contain unnecessary image details.
    var reviewEvidenceText: String {
        let visibleKeywords = existingKeywords
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty }
        guard !visibleKeywords.isEmpty else {
            return "Evidencia local registrada: no hay keywords existentes."
        }
        return "Evidencia local registrada: keywords existentes: \(visibleKeywords.joined(separator: ", "))."
    }

    /// Makes a known scan-time overlap visible before the final Photos read.
    /// A later external edit can still change this result, so the copy keeps
    /// the authoritative check at apply time.
    var reviewEffectiveChangeText: String? {
        guard isReviewSelectable, !proposedKeywords.isEmpty else { return nil }
        let existing = Set(existingKeywords.map(reviewComparisonKey))
        let overlap = proposedKeywords.filter { existing.contains(reviewComparisonKey($0)) }.count
        guard overlap > 0 else { return nil }
        let keywordLabel = overlap == 1 ? "keyword propuesta" : "keywords propuestas"
        let preservedLabel = overlap == 1 ? "se conservará" : "se conservarán"
        return "\(overlap) de \(proposedKeywords.count) \(keywordLabel) ya coincide con Fotos y \(preservedLabel); apply volverá a comprobar el resto."
    }

    var reviewReasonText: String {
        if !errors.isEmpty {
            let messages = errors.reduce(into: [String]()) { messages, error in
                let message = HumanErrorCopy.message(for: error.code)
                guard message != "La ejecución necesita revisión manual.", !messages.contains(message) else {
                    return
                }
                messages.append(message)
            }
            if messages.isEmpty {
                return "Atención: esta foto requiere revisión manual y no se aplicará."
            }
            return "Atención: \(messages.joined(separator: " ")) Revisa esta foto antes de continuar."
        }
        if hasUnavailableReviewConfidence {
            return "Atención: la confianza no está disponible o no es válida; revisa esta foto antes de continuar."
        }
        if hasConfidenceBelowReviewThreshold {
            return "Atención: la confianza es inferior al umbral de 60%; revisa esta foto antes de continuar."
        }
        let hasReason = modelReason?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
        return hasReason
            ? "Motivo del modelo disponible; confirma la propuesta con la evidencia visible y la confianza antes de aprobar."
            : "Motivo del modelo no disponible; confirma la propuesta con la evidencia visible y la confianza antes de aprobar."
    }

    /// Makes location-derived context visible without exposing coordinates or
    /// implying that location metadata will be written back to Photos.
    var locationContextReviewText: String? {
        guard modelReason == "location_context" else { return nil }
        return "Se usó contexto de ubicación solo para orientar el análisis; no se escribirá ni guardará la ubicación."
    }

    /// Caption text that is safe and useful to show as an approval control.
    /// Whitespace-only proposals are not actionable and must not render a
    /// checkbox that `ReviewSelection` will reject.
    var reviewCaptionText: String? {
        guard let proposedCaption else { return nil }
        let compact = proposedCaption.split(whereSeparator: \.isWhitespace).joined(separator: " ")
        let trimmed = compact.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : String(trimmed)
    }

    var hasCaptionProposal: Bool {
        reviewCaptionText != nil
    }

    /// The scan policy never proposes changes below 0.60. If a legacy,
    /// truncated or edited manifest contains such a combination, keep it
    /// visible for audit but out of every approval and retry path.
    var hasConfidenceBelowReviewThreshold: Bool {
        guard hasReviewableChanges, let confidence, confidence.isFinite else { return false }
        return confidence < 0.60
    }

    /// A proposal without a finite score inside the model contract cannot be
    /// compared with the review threshold. Keep it visible for audit, but
    /// fail closed before any keyword or caption can be approved.
    var hasUnavailableReviewConfidence: Bool {
        hasReviewableChanges && confidenceBand == .unavailable
    }

    /// Only completed photo analysis states without errors can enter the
    /// review/apply selection. A `noop` may still carry a caption proposal,
    /// while failed, cancelled, errored, or future states remain attention-only.
    var hasInvalidPendingCaptionState: Bool {
        let mutationIsUnstarted = applyState == "not_run" && rollbackState == "not_run"
        return mutationIsUnstarted
            && hasInvalidFreshReviewCaptionState
    }

    var isReviewSelectable: Bool {
        let mutationIsUnstarted = applyState == "not_run" && rollbackState == "not_run"
        let hasPendingReviewableChanges = hasReviewableChanges
        return !hasInvalidPendingCaptionState
            && !hasConfidenceBelowReviewThreshold
            && !hasUnavailableReviewConfidence
            && (((state == "ready" || state == "noop")
                && errors.isEmpty
                && (!hasPendingReviewableChanges || mutationIsUnstarted))
                || isRetryableApplyRow)
    }

    /// Explain the first row-level gate that keeps a proposal out of approval.
    /// Disabled controls use the same reason as the visible scope summary so
    /// VoiceOver never describes an unavailable proposal as an approval action.
    var reviewSelectionBlockReason: String? {
        if hasInvalidPendingCaptionState {
            return "el estado del caption no es coherente; ejecuta un dry-run nuevo"
        }
        if hasUnavailableReviewConfidence {
            return "confianza no disponible o inválida"
        }
        if hasConfidenceBelowReviewThreshold {
            return "confianza inferior al umbral de 60%"
        }
        if !errors.isEmpty && !isRetryableApplyRow {
            return "la foto contiene errores que requieren atención"
        }
        if hasReviewableChanges && (applyState != "not_run" || rollbackState != "not_run") {
            return "la foto ya tiene un resultado registrado"
        }
        return isReviewSelectable ? nil : stateLabel
    }

    var hasReviewableChanges: Bool {
        !proposedKeywords.isEmpty || hasCaptionProposal
    }

    /// Caption-only rows should not reserve a heading for an empty keyword
    /// list. Keeping the section label in the model lets the view and its
    /// accessibility tests share the same presentation decision.
    var keywordProposalSectionLabel: String? {
        proposedKeywords.isEmpty ? nil : "Propuestas"
    }

    /// The photo-level toggle selects keywords. Caption approval remains an
    /// independent action, so a caption-only row must not expose a no-op
    /// photo toggle.
    var canSelectPhoto: Bool {
        isReviewSelectable && !proposedKeywords.isEmpty
    }

    /// Makes the mutation boundary explicit in the row. A photo-level
    /// keyword toggle does not approve its caption; captions always require
    /// their own checkbox so the review cannot accidentally broaden the
    /// write scope.
    var approvalScopeText: String {
        if let reviewSelectionBlockReason {
            return "No seleccionable: \(reviewSelectionBlockReason)."
        }

        let keywordCount = proposedKeywords.count
        switch (keywordCount, hasCaptionProposal) {
        case (0, true):
            return "Caption propuesto; requiere aprobación separada."
        case (1, true):
            return "1 keyword propuesta; caption requiere aprobación separada."
        case (_, true):
            return "\(keywordCount) keywords propuestas; caption requiere aprobación separada."
        case (0, false):
            return "No hay keywords ni captions aprobables."
        case (1, false):
            return "1 keyword propuesta."
        default:
            return "\(keywordCount) keywords propuestas."
        }
    }

    /// Unknown future states are intentionally surfaced as attention items
    /// instead of being silently hidden by a filter.
    var needsReviewAttention: Bool {
        let knownScanStates = Set(["ready", "noop", "analysis_failed", "cancelled"])
        let knownApplyStates = Set(["not_run", "writing", "verified", "noop", "failed", "uncertain", "cancelled"])
        let knownRollbackStates = Set(["not_run", "removing", "verified_removed", "failed", "uncertain", "casing_conflict", "already_absent", "cancelled"])
        let scanNeedsAttention = !knownScanStates.contains(state)
            || ["analysis_failed", "cancelled"].contains(state)
        let applyNeedsAttention = !knownApplyStates.contains(applyState)
            || ["failed", "uncertain", "writing", "cancelled"].contains(applyState)
        let rollbackNeedsAttention = !knownRollbackStates.contains(rollbackState)
            || ["failed", "uncertain", "removing", "casing_conflict", "cancelled"].contains(rollbackState)
        let knownCaptionStates = Set(["not_requested", "proposed", "verified", "preserved", "failed", "uncertain", "removed"])
        let captionNeedsAttention = !knownCaptionStates.contains(captionState)
            || ["failed", "uncertain"].contains(captionState)
            || hasInvalidCaptionState
        return !errors.isEmpty
            || scanNeedsAttention
            || applyNeedsAttention
            || rollbackNeedsAttention
            || captionNeedsAttention
            || hasInvalidPendingCaptionState
            || hasConfidenceBelowReviewThreshold
            || hasUnavailableReviewConfidence
    }
}

struct ReviewFilterCounts: Equatable, Sendable {
    let all: Int
    let changes: Int
    let captions: Int
    let attention: Int

    init(photos: [PreviewPhoto]) {
        all = photos.count
        // Keep picker badges and their filtered lists driven by the same
        // predicate. In particular, failed/cancelled retry rows belong to
        // Atención rather than appearing as pending changes/captions.
        changes = photos.filter(ReviewPhotoFilter.changes.includes).count
        captions = photos.filter(ReviewPhotoFilter.captions.includes).count
        attention = photos.filter(ReviewPhotoFilter.attention.includes).count
    }

    func count(for filter: ReviewPhotoFilter) -> Int {
        switch filter {
        case .all: return all
        case .changes: return changes
        case .captions: return captions
        case .attention: return attention
        }
    }
}

/// Keeps the visible review subset distinct from the approved mutation scope.
/// Filtering is presentation-only, so this summary never derives or changes
/// selection state; it only reports the counts supplied by the review model.
struct ReviewScopeSummary: Equatable, Sendable {
    let filter: ReviewPhotoFilter
    let visiblePhotoCount: Int
    let totalPhotoCount: Int
    let approvedPhotoCount: Int
    let approvedKeywordCount: Int?
    let approvedCaptionCount: Int?

    init(
        filter: ReviewPhotoFilter,
        visiblePhotoCount: Int,
        totalPhotoCount: Int,
        approvedPhotoCount: Int,
        approvedKeywordCount: Int? = nil,
        approvedCaptionCount: Int? = nil
    ) {
        self.filter = filter
        self.visiblePhotoCount = visiblePhotoCount
        self.totalPhotoCount = totalPhotoCount
        self.approvedPhotoCount = approvedPhotoCount
        self.approvedKeywordCount = approvedKeywordCount
        self.approvedCaptionCount = approvedCaptionCount
    }

    var visibleScopeText: String {
        let photo = totalPhotoCount == 1 ? "foto visible" : "fotos visibles"
        return "Filtro \(filter.title): \(visiblePhotoCount) de \(totalPhotoCount) \(photo)"
    }

    var approvedScopeText: String {
        let photo = approvedPhotoCount == 1 ? "foto afectada" : "fotos afectadas"
        let base = "\(approvedPhotoCount) \(photo) por la selección aprobada"
        guard let approvedKeywordCount, let approvedCaptionCount else { return base }
        let keyword = approvedKeywordCount == 1 ? "keyword aprobada" : "keywords aprobadas"
        let caption = approvedCaptionCount == 1 ? "caption aprobado" : "captions aprobados"
        return "\(base) · \(approvedKeywordCount) \(keyword) · \(approvedCaptionCount) \(caption)"
    }

    var accessibilityLabel: String {
        "\(visibleScopeText). \(approvedScopeText). El filtro no cambia la selección aprobada."
    }
}

struct ReviewPhotoSelection: Codable, Equatable, Sendable {
    let uuid: String
    let keywords: [String]
}

/// A review-only description of the exact changes that the confirmation
/// surface is about to send to the helper. Photo identifiers are retained only
/// as in-memory PhotoKit references for on-demand thumbnails; they never enter
/// confirmation copy, IPC, logs, or persisted artifacts.
struct ReviewConfirmationDetail: Equatable, Sendable {
    let affectedPhotoCount: Int
    let keywordCount: Int
    let knownExistingKeywordCount: Int
    let potentiallyNewKeywordCount: Int
    let captionCount: Int
    let keywordLines: [String]
    let captionLines: [String]
    let photoItems: [ConfirmationPhotoItem]
    let isRetryingFailedApply: Bool

    var keywordDisplayLines: [ConfirmationDisplayLine] {
        confirmationDisplayLines(keywordLines)
    }

    var captionDisplayLines: [ConfirmationDisplayLine] {
        confirmationDisplayLines(captionLines)
    }

    init(
        selection: ReviewSelection,
        photos: [PreviewPhoto],
        isRetryingFailedApply: Bool = false
    ) {
        self.isRetryingFailedApply = isRetryingFailedApply
        let photosByID = Dictionary(uniqueKeysWithValues: photos.map { ($0.uuid, $0) })
        let selectedKeywords = selection.payload
        let selectedCaptionIDs = Set(
            selection.captionSelectionPayload.compactMap { $0.value ? $0.key : nil }
        )
        let selectedKeywordsByID = Dictionary(
            uniqueKeysWithValues: selectedKeywords.map { ($0.uuid, $0.keywords) }
        )

        affectedPhotoCount = selection.approvedPhotoCount
        keywordCount = selectedKeywords.reduce(0) { $0 + $1.keywords.count }
        knownExistingKeywordCount = selectedKeywords.reduce(0) { count, item in
            guard let photo = photosByID[item.uuid] else { return count }
            let existing = Set(photo.existingKeywords.map(reviewComparisonKey))
            return count + item.keywords.filter { existing.contains(reviewComparisonKey($0)) }.count
        }
        potentiallyNewKeywordCount = max(0, keywordCount - knownExistingKeywordCount)
        captionCount = selectedCaptionIDs.count
        keywordLines = selectedKeywords.compactMap { item in
            guard let photo = photosByID[item.uuid], !item.keywords.isEmpty else { return nil }
            return "\(Self.displayTitle(photo.title)): \(item.keywords.joined(separator: ", "))"
        }
        captionLines = photos.compactMap { photo in
            guard selectedCaptionIDs.contains(photo.uuid),
                  let compactCaption = photo.reviewCaptionText else { return nil }
            return "\(Self.displayTitle(photo.title)): \(compactCaption)"
        }
        photoItems = photos.compactMap { photo in
            let keywords = selectedKeywordsByID[photo.uuid] ?? []
            let caption = selectedCaptionIDs.contains(photo.uuid)
                ? photo.reviewCaptionText
                : nil
            guard !keywords.isEmpty || caption != nil else { return nil }
            return ConfirmationPhotoItem(
                id: photo.uuid,
                photosLocalIdentifier: photo.photosLocalIdentifier,
                displayTitle: Self.displayTitle(photo.title),
                approvedKeywords: keywords,
                approvedCaption: caption
            )
        }
    }

    var summaryText: String {
        guard affectedPhotoCount > 0 else { return "No hay cambios aprobados." }
        let photoLabel = affectedPhotoCount == 1 ? "foto afectada" : "fotos afectadas"
        let keywordLabel = keywordCount == 1 ? "keyword aprobada" : "keywords aprobadas"
        let captionLabel = captionCount == 1 ? "caption aprobado" : "captions aprobados"
        let action = isRetryingFailedApply ? "reintentar" : "escribir"
        return "Antes de \(action), se comprobarán \(affectedPhotoCount) \(photoLabel), \(keywordCount) \(keywordLabel) y \(captionCount) \(captionLabel)."
    }

    /// Repeats the exact approved scope on the destructive confirmation
    /// button, so the final action remains specific after the detail review.
    var applyActionText: String {
        if isRetryingFailedApply {
            let rowLabel = affectedPhotoCount == 1 ? "fila fallida" : "filas fallidas"
            return "Reintentar \(affectedPhotoCount) \(rowLabel)"
        }
        switch (keywordCount, captionCount) {
        case (0, 0):
            return "Sin cambios aprobados"
        case (0, let captions):
            return "Aplicar \(captions) " + (captions == 1 ? "caption" : "captions")
        case (let keywords, 0):
            return "Aplicar \(keywords) " + (keywords == 1 ? "keyword" : "keywords")
        case (let keywords, let captions):
            let keywordText = "\(keywords) " + (keywords == 1 ? "keyword" : "keywords")
            let captionText = "\(captions) " + (captions == 1 ? "caption" : "captions")
            return "Aplicar \(keywordText) y \(captionText)"
        }
    }

    /// Explains the difference between the approved proposal and the likely
    /// write set without pretending the dry-run knows about later edits in
    /// Photos. The helper performs the authoritative read immediately before
    /// writing.
    var effectiveChangeText: String {
        guard keywordCount > 0 else {
            return captionCount > 0
                ? "No hay keywords aprobadas; el caption se volverá a validar antes de escribir."
                : "No hay keywords ni captions aprobados para comparar."
        }
        guard knownExistingKeywordCount > 0 else {
            return "Las \(keywordCount) keywords aprobadas no coincidían con keywords existentes en el dry-run; apply volverá a comprobarlas antes de escribir."
        }
        let existingLabel = knownExistingKeywordCount == 1 ? "keyword ya coincide" : "keywords ya coinciden"
        let preservedLabel = knownExistingKeywordCount == 1 ? "se conservará" : "se conservarán"
        if potentiallyNewKeywordCount == 0 {
            return "\(knownExistingKeywordCount) de \(keywordCount) \(existingLabel) con Fotos y \(preservedLabel); no hay keywords nuevas que escribir."
        }
        return "\(knownExistingKeywordCount) de \(keywordCount) \(existingLabel) con Fotos y \(preservedLabel); \(potentiallyNewKeywordCount) keywords nuevas se volverán a comprobar antes de escribir."
    }

    /// Captions are conditionally writable: the final Photos read decides
    /// whether the proposed text can be added, so the review must not imply
    /// that every approved caption is guaranteed to replace existing text.
    var captionConflictText: String {
        guard captionCount > 0 else { return "No hay captions aprobados." }
        let label = captionCount == 1 ? "El caption aprobado" : "Los \(captionCount) captions aprobados"
        return "\(label) se escribirán solo si Fotos no tiene uno; un caption existente o añadido externamente se conserva."
    }

    /// States the concurrency boundary explicitly: an apply never overwrites
    /// an external keyword or caption discovered during its final read-back.
    var externalConflictText: String {
        "Antes de escribir, apply volverá a leer Apple Fotos. Las keywords o captions añadidos fuera de este run se conservarán; no se reemplazarán ni eliminarán."
    }

    var accessibilitySummary: String {
        var parts = [summaryText]
        if !keywordLines.isEmpty {
            parts.append("Keywords: \(keywordLines.joined(separator: "; ")).")
        }
        if !captionLines.isEmpty {
            parts.append("Captions: \(captionLines.joined(separator: "; ")).")
        }
        parts.append(effectiveChangeText)
        parts.append(captionConflictText)
        parts.append(externalConflictText)
        return parts.joined(separator: " ")
    }

    private static func displayTitle(_ title: String) -> String {
        compactDisplayText(title) ?? "Foto sin título"
    }

    private static func compactDisplayText(_ value: String) -> String? {
        let compact = value.split(whereSeparator: \.isWhitespace).joined(separator: " ")
        let trimmed = compact.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        return String(trimmed.prefix(160))
    }
}

/// Transient PhotoKit reference used only to make the final human review
/// visually verifiable. The local identifier is never rendered as text.
struct ConfirmationPhotoItem: Identifiable, Equatable, Sendable {
    let id: String
    let photosLocalIdentifier: String?
    let displayTitle: String
    let approvedKeywords: [String]
    let approvedCaption: String?
}

/// A confirmation line needs an identity independent from its display text.
/// Two photos can legitimately share a title and proposed values, so using
/// the text as a SwiftUI `ForEach` identity can collapse distinct rows.
struct ConfirmationDisplayLine: Identifiable, Equatable, Sendable {
    let id: Int
    let text: String
}

private func confirmationDisplayLines(_ lines: [String]) -> [ConfirmationDisplayLine] {
    lines.enumerated().map { ConfirmationDisplayLine(id: $0.offset, text: $0.element) }
}

/// Exact, read-only scope shown immediately before a verified rollback. Only
/// values recorded after a successful apply read-back enter this summary;
/// uncertain and externally changed metadata remains explicitly blocked.
struct RollbackConfirmationDetail: Equatable, Sendable {
    let affectedPhotoCount: Int
    let keywordCount: Int
    let captionCount: Int
    let blockedPhotoCount: Int
    let keywordLines: [String]
    let captionLines: [String]

    var keywordDisplayLines: [ConfirmationDisplayLine] {
        confirmationDisplayLines(keywordLines)
    }

    var captionDisplayLines: [ConfirmationDisplayLine] {
        confirmationDisplayLines(captionLines)
    }

    init(preview: RunManifestPreview) {
        let eligible = preview.photos.filter {
            $0.hasRollbackEvidence
        }
        affectedPhotoCount = eligible.count
        keywordCount = eligible.reduce(0) { $0 + $1.appliedKeywords.count }
        captionCount = eligible.filter {
            $0.appliedCaption?.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty == false
        }.count
        let eligibleIDs = Set(eligible.map(\.uuid))
        blockedPhotoCount = preview.photos.filter {
            $0.needsReviewAttention
                && $0.rollbackState != "verified_removed"
                && !eligibleIDs.contains($0.uuid)
        }.count
        keywordLines = eligible.compactMap { photo in
            guard !photo.appliedKeywords.isEmpty else { return nil }
            return "\(Self.displayTitle(photo.title)): \(photo.appliedKeywords.joined(separator: ", "))"
        }
        captionLines = eligible.compactMap { photo in
            guard let caption = photo.appliedCaption,
                  let compactCaption = Self.completeCaptionDisplayText(caption) else { return nil }
            return "\(Self.displayTitle(photo.title)): \(compactCaption)"
        }
    }

    var summaryText: String {
        guard affectedPhotoCount > 0 else { return "No hay cambios verificados elegibles para rollback." }
        let photoLabel = affectedPhotoCount == 1 ? "foto" : "fotos"
        let keywordLabel = keywordCount == 1 ? "keyword" : "keywords"
        let captionLabel = captionCount == 1 ? "caption" : "captions"
        return "Se eliminarán únicamente \(keywordCount) \(keywordLabel) y \(captionCount) \(captionLabel) verificados de \(affectedPhotoCount) \(photoLabel)."
    }

    var blockedText: String {
        var parts = [
            "Se conservarán keywords y captions externos, variantes de mayúsculas y cambios que no coincidan exactamente."
        ]
        if blockedPhotoCount > 0 {
            let photoLabel = blockedPhotoCount == 1 ? "foto queda bloqueada" : "fotos quedan bloqueadas"
            parts.append("\(blockedPhotoCount) \(photoLabel) porque su estado no verificado bloquea el rollback.")
        }
        return parts.joined(separator: " ")
    }

    var accessibilitySummary: String {
        var parts = [summaryText, blockedText]
        if !keywordLines.isEmpty {
            parts.append("Keywords a eliminar: \(keywordLines.joined(separator: "; ")).")
        }
        if !captionLines.isEmpty {
            parts.append("Captions a eliminar: \(captionLines.joined(separator: "; ")).")
        }
        return parts.joined(separator: " ")
    }

    private static func displayTitle(_ title: String) -> String {
        compactDisplayText(title) ?? "Foto sin título"
    }

    private static func compactDisplayText(_ value: String) -> String? {
        let compact = value.split(whereSeparator: \.isWhitespace).joined(separator: " ")
        let trimmed = compact.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return nil }
        return String(trimmed.prefix(160))
    }

    private static func completeCaptionDisplayText(_ value: String) -> String? {
        let compact = value.split(whereSeparator: \.isWhitespace).joined(separator: " ")
        let trimmed = compact.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }
}

struct ReviewSelection: Equatable, Sendable {
    private let photos: [PreviewPhoto]
    private let reviewedManifest: Bool
    private let historicalSummaryOnly: Bool
    private let historicalKeywordCount: Int
    private let historicalCaptionCount: Int
    private let historicalApprovedPhotoIDs: Set<String>
    private var selected: [String: Set<String>]
    private var selectedCaptions: Set<String>

    init(photos: [PreviewPhoto], reviewedManifest: Bool = false) {
        self.reviewedManifest = reviewedManifest
        let retryOnly = reviewedManifest && photos.contains(where: \.isRetryableApplyRow)
        historicalSummaryOnly = reviewedManifest
            && !retryOnly
            && photos.contains {
                $0.applyState != "not_run" || $0.rollbackState != "not_run"
            }
        let historicalRows = photos.filter { photo in
            ["ready", "noop"].contains(photo.state)
                && photo.filteredScanErrors.isEmpty
                && photo.hasReviewableChanges
        }
        historicalKeywordCount = historicalRows.reduce(0) { $0 + $1.proposedKeywords.count }
        historicalCaptionCount = historicalRows.filter(\.hasCaptionProposal).count
        historicalApprovedPhotoIDs = Set(historicalRows.map(\.uuid))
        self.photos = photos
        selected = reviewedManifest
            ? Dictionary(
                uniqueKeysWithValues: photos
                    .filter {
                        $0.canSelectPhoto
                            && $0.state == "ready"
                            && (!retryOnly || $0.isReviewedApplyCandidate)
                    }
                    .map { ($0.uuid, Set($0.proposedKeywords)) }
            )
            : [:]
        // Captions are free-form descriptions, so they require an explicit
        // per-photo approval. Fresh keywords also require explicit selection;
        // reviewed manifests preserve their already-approved keywords.
        selectedCaptions = reviewedManifest
            ? Set(photos.filter {
                $0.isReviewSelectable
                    && $0.hasCaptionProposal
                    && (!retryOnly || $0.isReviewedApplyCandidate)
            }.map(\.uuid))
            : []
    }

    var selectedPhotoCount: Int {
        selected.values.filter { !$0.isEmpty }.count
    }

    /// Number of distinct photos with at least one approved mutation. Unlike
    /// `selectedPhotoCount`, this also includes caption-only approvals so the
    /// review summary describes the actual write scope precisely.
    var approvedPhotoCount: Int {
        if historicalSummaryOnly {
            return historicalApprovedPhotoIDs.count
        }
        let keywordPhotos = selected.compactMap { uuid, keywords in
            keywords.isEmpty ? nil : uuid
        }
        return Set(keywordPhotos).union(selectedCaptions).count
    }

    /// Whether the review toolbar has at least one photo with keyword
    /// proposals that can be selected in one action.
    var canSelectAllKeywords: Bool {
        photos.contains { $0.isReviewedApplyCandidate && !$0.proposedKeywords.isEmpty }
    }

    /// Keeps the caption-only review path explicit even when no bulk keyword
    /// control is available in the toolbar.
    var hasCaptionProposals: Bool {
        photos.contains { $0.isReviewSelectable && $0.hasCaptionProposal }
    }

    var allKeywordsSelected: Bool {
        let selectable = photos.filter { $0.isReviewedApplyCandidate && !$0.proposedKeywords.isEmpty }
        guard !selectable.isEmpty else { return false }
        return selectable.allSatisfy { photo in
            selected[photo.uuid, default: []].count == Set(photo.proposedKeywords).count
        }
    }

    var selectedKeywordCount: Int {
        if historicalSummaryOnly { return historicalKeywordCount }
        return selected.values.reduce(0) { $0 + $1.count }
    }

    var selectedCaptionCount: Int {
        historicalSummaryOnly ? historicalCaptionCount : selectedCaptions.count
    }

    var selectedChangeCount: Int { selectedKeywordCount + selectedCaptionCount }

    var payload: [ReviewPhotoSelection] {
        photos.compactMap { photo in
            let chosen = photo.proposedKeywords.filter { selected[photo.uuid, default: []].contains($0) }
            return chosen.isEmpty ? nil : ReviewPhotoSelection(uuid: photo.uuid, keywords: chosen)
        }
    }

    var captionSelectionPayload: [String: Bool] {
        Dictionary(
            uniqueKeysWithValues: photos.compactMap { photo in
                guard photo.isReviewedApplyCandidate, photo.hasCaptionProposal else { return nil }
                return (photo.uuid, selectedCaptions.contains(photo.uuid))
            }
        )
    }

    var approvedPhotoIDs: Set<String> {
        if historicalSummaryOnly { return historicalApprovedPhotoIDs }
        return Set(payload.map(\.uuid))
            .union(captionSelectionPayload.compactMap { $0.value ? $0.key : nil })
    }

    func isPhotoSelected(_ photo: PreviewPhoto) -> Bool {
        // The photo-level toggle controls keywords only. Caption approval is
        // intentionally independent and has its own toggle in the row.
        selected[photo.uuid, default: []].isEmpty == false
    }

    func isKeywordSelected(_ keyword: String, for photo: PreviewPhoto) -> Bool {
        selected[photo.uuid, default: []].contains(keyword)
    }

    func isCaptionSelected(_ photo: PreviewPhoto) -> Bool {
        selectedCaptions.contains(photo.uuid)
    }

    mutating func setPhoto(_ photo: PreviewPhoto, selected isSelected: Bool) {
        guard !reviewedManifest,
              photo.isReviewedApplyCandidate,
              photo.canSelectPhoto else { return }
        selected[photo.uuid] = isSelected ? Set(photo.proposedKeywords) : []
    }

    mutating func setAllKeywords(selected isSelected: Bool) {
        guard !reviewedManifest else { return }
        for photo in photos where photo.isReviewedApplyCandidate && photo.canSelectPhoto {
            selected[photo.uuid] = isSelected ? Set(photo.proposedKeywords) : []
        }
    }

    mutating func setKeyword(_ keyword: String, for photo: PreviewPhoto, selected isSelected: Bool) {
        guard !reviewedManifest,
              photo.isReviewedApplyCandidate,
              photo.canSelectPhoto,
              photo.proposedKeywords.contains(keyword) else { return }
        if isSelected {
            selected[photo.uuid, default: []].insert(keyword)
        } else {
            selected[photo.uuid, default: []].remove(keyword)
        }
    }

    mutating func setCaption(uuid: String, selected isSelected: Bool) {
        guard !reviewedManifest,
              photos.contains(where: {
            $0.uuid == uuid && $0.isReviewedApplyCandidate && $0.hasCaptionProposal
        }) else { return }
        if isSelected {
            selectedCaptions.insert(uuid)
        } else {
            selectedCaptions.remove(uuid)
        }
    }
}

struct ApplySafetyGate: Equatable, Sendable {
    private(set) var reviewedManifest: URL?
    private(set) var selectedKeywordCount = 0
    private(set) var selectedCaptionCount = 0
    private(set) var allowsEmptySelectionForRetry = false
    private(set) var isConfirmationPresented = false
    var isWorkerRunning = false

    var canRequestApply: Bool {
        reviewedManifest != nil
            && (selectedKeywordCount + selectedCaptionCount > 0 || allowsEmptySelectionForRetry)
            && !isWorkerRunning
    }

    mutating func setReviewedManifest(_ url: URL?, allowsEmptySelectionForRetry: Bool = false) {
        reviewedManifest = url
        self.allowsEmptySelectionForRetry = url != nil && allowsEmptySelectionForRetry
        isConfirmationPresented = false
    }

    mutating func updateSelection(keywordCount: Int, captionCount: Int = 0) {
        selectedKeywordCount = max(0, keywordCount)
        selectedCaptionCount = max(0, captionCount)
        if selectedKeywordCount + selectedCaptionCount == 0 {
            isConfirmationPresented = false
        }
    }

    mutating func requestConfirmation() -> Bool {
        guard canRequestApply else { return false }
        isConfirmationPresented = true
        return true
    }

    mutating func cancelConfirmation() {
        isConfirmationPresented = false
    }

    mutating func confirmedManifest() -> URL? {
        guard isConfirmationPresented, canRequestApply else { return nil }
        isConfirmationPresented = false
        return reviewedManifest
    }
}
