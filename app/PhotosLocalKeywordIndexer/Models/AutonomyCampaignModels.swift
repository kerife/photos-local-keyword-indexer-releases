import Foundation

enum AutonomyPilotPolicy {
    /// Broad-library autonomous writes are a deliberately bounded beta pilot.
    static let maximumPhotos = 3
}

enum AutonomyCampaignState: String, Decodable, Sendable {
    case preparing, running, pausing, paused, completed
    var ownsHelper: Bool { [.preparing, .running, .pausing].contains(self) }
}

enum AutonomyCampaignReason: String, Decodable, Sendable {
    case none, manualDrain = "manual_drain", snapshot, userPause = "user_pause", recovered, permission, storage
}

enum AutonomyActivityState: String, Decodable, Sendable {
    case preparing, analyzing, validating
    case saveQueued = "save_queued"
    case saving
    /// A withdrawal event; it is not rendered as a photo phase.
    case settled

    var isVisible: Bool { self != .settled }

    var statusText: String {
        switch self {
        case .preparing: return "Preparando una copia para el análisis local."
        case .analyzing: return "Analizando en este Mac."
        case .validating: return "Validando el resultado local."
        case .saveQueued: return "En cola para guardar."
        case .saving: return "Guardando y verificando."
        case .settled: return "Finalizada."
        }
    }
}

struct AutonomyActivitySnapshot: Equatable, Sendable {
    let campaignID: String
    let revision: Int
    let position: Int
    let state: AutonomyActivityState
    let photosLocalIdentifier: String

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        campaignID = try container.decode(String.self, forKey: .campaignID)
        revision = try container.decode(Int.self, forKey: .revision)
        position = try container.decode(Int.self, forKey: .position)
        state = try container.decode(AutonomyActivityState.self, forKey: .state)
        photosLocalIdentifier = try container.decode(String.self, forKey: .photosLocalIdentifier)
        guard Self.isValidIdentifier(campaignID),
              (0 ... Int(Int32.max)).contains(revision),
              (0 ... Int(Int32.max)).contains(position),
              Self.isValidLocalIdentifier(photosLocalIdentifier) else {
            throw WorkerEventError.invalidValue("autonomy_activity")
        }
    }

    private enum CodingKeys: String, CodingKey {
        case campaignID = "campaign_id", revision, position, state
        case photosLocalIdentifier = "photos_local_identifier"
    }

    private static func isValidIdentifier(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.count <= 64 && value.unicodeScalars.allSatisfy {
            switch $0.value {
            case 45, 46, 48 ... 57, 65 ... 90, 95, 97 ... 122: return true
            default: return false
            }
        }
    }

    private static func isValidLocalIdentifier(_ value: String) -> Bool {
        !value.isEmpty && value.utf8.count <= 1_024 && value.unicodeScalars.allSatisfy {
            $0.value >= 0x20 && !((0x7F ... 0x9F).contains($0.value))
        }
    }
}

struct AutonomyReviewPresentation: Equatable, Sendable {
    let campaign: AutonomyCampaignSnapshot?
    let activities: [AutonomyActivitySnapshot]
    let statusReady: Bool
    let activationPending: Bool
    let pausePending: Bool
    let controlPending: Bool
    let error: String?

    var isRunning: Bool {
        activationPending || [.preparing, .running].contains(campaign?.state)
    }

    /// A paused campaign is preserved as evidence, but the beta never resumes
    /// it: a new bounded pilot is the only path that can admit more writes.
    var requiresFreshBoundedPilot: Bool {
        campaign?.state == .paused
    }

    var progress: Double? {
        guard let campaign, campaign.total > 0 else { return nil }
        return Double(campaign.total - campaign.remaining) / Double(campaign.total)
    }

    var analyzingCount: Int {
        activities.filter { [.preparing, .analyzing, .validating].contains($0.state) }.count
    }

    var savingCount: Int {
        activities.filter { [.saveQueued, .saving].contains($0.state) }.count
    }

    var accessibilityLabel: String {
        if activationPending { return "Recorrido autónomo preparando la cola manual." }
        guard let campaign else {
            return statusReady ? "Guardado autónomo listo para iniciar." : "Comprobando el recorrido autónomo guardado."
        }
        let completed = campaign.total - campaign.remaining
        if requiresFreshBoundedPilot {
            return "Recorrido detenido. Conserva sus registros; inicia un nuevo piloto limitado a \(AutonomyPilotPolicy.maximumPhotos) fotos para continuar."
        }
        return "\(campaign.statusText). \(completed) de \(campaign.total) finalizadas. \(analyzingCount) analizando. \(savingCount) guardando. \(campaign.attention) requieren atención."
    }
}

struct AutonomyCampaignSnapshot: Decodable, Equatable, Sendable {
    let campaignID: String
    let revision: Int
    var state: AutonomyCampaignState
    let total: Int
    let examined: Int
    let analyzed: Int
    let saved: Int
    let noChange: Int
    let attention: Int
    let remaining: Int
    let inFlight: Int
    let invalidCount: Int
    var reason: AutonomyCampaignReason

    enum CodingKeys: String, CodingKey {
        case campaignID = "campaign_id", revision, state, total, examined, analyzed, saved
        case noChange = "no_change", attention, remaining, inFlight = "in_flight"
        case invalidCount = "invalid_count", reason
    }

    func validate() throws {
        guard !campaignID.isEmpty, campaignID.utf8.count <= 64,
              campaignID.range(of: "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", options: .regularExpression) != nil,
              [revision, total, examined, analyzed, saved, noChange, attention, remaining, inFlight, invalidCount]
                .allSatisfy({ (0 ... Int(Int32.max)).contains($0) }),
              remaining == total - saved - noChange - attention,
              examined <= total, analyzed <= examined, inFlight <= remaining,
              state != .completed || (remaining == 0 && inFlight == 0) else {
            throw WorkerEventError.invalidValue("autonomy_campaign")
        }
    }

    var statusText: String {
        switch state {
        case .preparing: return reason == .manualDrain ? "Terminando el trabajo manual iniciado…" : "Preparando el inventario de imágenes…"
        case .running: return "Recorrido autónomo en curso"
        case .pausing: return "Pausando: el trabajo aceptado termina; no se admiten nuevos guardados."
        case .paused:
            switch reason {
            case .permission: return "Recorrido pausado: revisa los permisos de Fotos."
            case .storage: return "Recorrido pausado: revisa el almacenamiento local."
            case .recovered: return "Recorrido detenido tras recuperar su estado. Conserva los registros y comienza un nuevo piloto limitado para continuar."
            default: return "Recorrido detenido. Conserva los registros y comienza un nuevo piloto limitado para continuar."
            }
        case .completed: return "Recorrido completado"
        }
    }

    var countersText: String {
        "Examinadas: \(examined) · Analizadas: \(analyzed) · Guardadas: \(saved) · Sin cambios: \(noChange) · Atención: \(attention) · Restantes: \(remaining)"
    }
}
