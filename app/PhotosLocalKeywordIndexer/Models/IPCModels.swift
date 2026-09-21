import Foundation

enum WorkerCommand: String, Codable, Sendable {
    case preflight
    case scan
    case review
    case apply
    case rollback
    case cancel
    case queueStart = "queue_start"
    case queueResume = "queue_resume"
    case queueUpdate = "queue_update"
    case queuePause = "queue_pause"
    case queuePersist = "queue_persist"
    case queueDiscard = "queue_discard"
    case queueRescan = "queue_rescan"
    case queueStop = "queue_stop"
    case autonomyStart = "autonomy_start"
    case autonomyPause = "autonomy_pause"
    case autonomyResume = "autonomy_resume"
    case autonomyStatus = "autonomy_status"

    var isAutonomy: Bool { [.autonomyStart, .autonomyPause, .autonomyResume, .autonomyStatus].contains(self) }
}

enum WorkerRequestPayload: Equatable, Sendable {
    case preflight(models: [String])
    case scan(ScanPayload)
    case review(manifest: String, selections: [String: [String]], captionSelections: [String: Bool])
    case manifest(path: String)
    case cancel
    case autonomyStart(campaignID: String, decisionID: String, runsRoot: String, settingsPath: String, limit: Int?)
    case autonomyControl(campaignID: String, decisionID: String)
    case autonomyStatus
    case queueStart(sessionID: String, revision: Int, decisionID: String, runsRoot: String, settingsPath: String)
    case queueRevision(sessionID: String, revision: Int, decisionID: String)
    case queueUpdate(sessionID: String, revision: Int, decisionID: String, settingsPath: String)
    case queueItemDecision(sessionID: String, itemID: String, revision: Int, decisionID: String)
}

struct ScanPayload: Equatable, Sendable {
    let limit: Int
    let modelPolicy: String
    let model: String?
    let fastModel: String
    let detailedModel: String
    let appleMaps: Bool
    let includeCaption: Bool
    let randomSelection: Bool
    let runsRoot: String
}

struct WorkerRequest: Encodable, Equatable, Sendable {
    let id: String
    let command: WorkerCommand
    let payload: WorkerRequestPayload

    static func autonomyStart(id: String, campaignID: String, decisionID: String, runsRoot: URL, settings: URL, limit: Int?) -> Self {
        Self(id: id, command: .autonomyStart, payload: .autonomyStart(campaignID: campaignID, decisionID: decisionID, runsRoot: runsRoot.path, settingsPath: settings.path, limit: limit))
    }

    static func autonomyPause(id: String, campaignID: String, decisionID: String) -> Self {
        Self(id: id, command: .autonomyPause, payload: .autonomyControl(campaignID: campaignID, decisionID: decisionID))
    }

    static func autonomyResume(id: String, campaignID: String, decisionID: String) -> Self {
        Self(id: id, command: .autonomyResume, payload: .autonomyControl(campaignID: campaignID, decisionID: decisionID))
    }

    static func autonomyStatus(id: String) -> Self {
        Self(id: id, command: .autonomyStatus, payload: .autonomyStatus)
    }

    static func preflight(id: String, models: [String]) -> Self {
        Self(id: id, command: .preflight, payload: .preflight(models: models))
    }

    static func scan(
        id: String,
        limit: Int,
        modelPolicy: String,
        model: String?,
        fastModel: String,
        detailedModel: String,
        appleMaps: Bool,
        includeCaption: Bool = false,
        randomSelection: Bool = false,
        runsRoot: URL
    ) -> Self {
        Self(
            id: id,
            command: .scan,
            payload: .scan(
                ScanPayload(
                    limit: limit,
                    modelPolicy: modelPolicy,
                    model: model,
                    fastModel: fastModel,
                    detailedModel: detailedModel,
                    appleMaps: appleMaps,
                    includeCaption: includeCaption,
                    randomSelection: randomSelection,
                    runsRoot: runsRoot.path
                )
            )
        )
    }

    static func apply(id: String, manifest: URL) -> Self {
        Self(id: id, command: .apply, payload: .manifest(path: manifest.path))
    }

    static func review(
        id: String,
        manifest: URL,
        selections: [String: [String]],
        captionSelections: [String: Bool] = [:]
    ) -> Self {
        Self(
            id: id,
            command: .review,
            payload: .review(
                manifest: manifest.path,
                selections: selections,
                captionSelections: captionSelections
            )
        )
    }

    static func rollback(id: String, manifest: URL) -> Self {
        Self(id: id, command: .rollback, payload: .manifest(path: manifest.path))
    }

    static func cancel(id: String) -> Self {
        Self(id: id, command: .cancel, payload: .cancel)
    }

    static func queueStart(
        id: String,
        sessionID: String,
        decisionID: String,
        runsRoot: URL,
        settings: URL
    ) -> Self {
        Self(
            id: id,
            command: .queueStart,
            payload: .queueStart(
                sessionID: sessionID,
                revision: 0,
                decisionID: decisionID,
                runsRoot: runsRoot.path,
                settingsPath: settings.path
            )
        )
    }

    static func queueResume(id: String, sessionID: String, revision: Int, decisionID: String) -> Self {
        Self(
            id: id,
            command: .queueResume,
            payload: .queueRevision(sessionID: sessionID, revision: revision, decisionID: decisionID)
        )
    }

    static func queueUpdate(
        id: String,
        sessionID: String,
        revision: Int,
        decisionID: String,
        settings: URL
    ) -> Self {
        Self(
            id: id,
            command: .queueUpdate,
            payload: .queueUpdate(
                sessionID: sessionID,
                revision: revision,
                decisionID: decisionID,
                settingsPath: settings.path
            )
        )
    }

    static func queuePause(id: String, sessionID: String, revision: Int, decisionID: String) -> Self {
        Self(
            id: id,
            command: .queuePause,
            payload: .queueRevision(sessionID: sessionID, revision: revision, decisionID: decisionID)
        )
    }

    static func queuePersist(
        id: String,
        sessionID: String,
        itemID: String,
        revision: Int,
        decisionID: String
    ) -> Self {
        queueItemDecision(
            id: id,
            command: .queuePersist,
            sessionID: sessionID,
            itemID: itemID,
            revision: revision,
            decisionID: decisionID
        )
    }

    static func queueDiscard(
        id: String,
        sessionID: String,
        itemID: String,
        revision: Int,
        decisionID: String
    ) -> Self {
        queueItemDecision(
            id: id,
            command: .queueDiscard,
            sessionID: sessionID,
            itemID: itemID,
            revision: revision,
            decisionID: decisionID
        )
    }

    static func queueRescan(
        id: String,
        sessionID: String,
        itemID: String,
        revision: Int,
        decisionID: String
    ) -> Self {
        queueItemDecision(
            id: id,
            command: .queueRescan,
            sessionID: sessionID,
            itemID: itemID,
            revision: revision,
            decisionID: decisionID
        )
    }

    static func queueStop(id: String, sessionID: String, revision: Int, decisionID: String) -> Self {
        Self(
            id: id,
            command: .queueStop,
            payload: .queueRevision(sessionID: sessionID, revision: revision, decisionID: decisionID)
        )
    }

    private static func queueItemDecision(
        id: String,
        command: WorkerCommand,
        sessionID: String,
        itemID: String,
        revision: Int,
        decisionID: String
    ) -> Self {
        Self(
            id: id,
            command: command,
            payload: .queueItemDecision(
                sessionID: sessionID,
                itemID: itemID,
                revision: revision,
                decisionID: decisionID
            )
        )
    }

    private enum CodingKeys: String, CodingKey {
        case id
        case command
        case payload
    }

    private enum PayloadKeys: String, CodingKey {
        case models
        case limit
        case modelPolicy = "model_policy"
        case model
        case fastModel = "fast_model"
        case detailedModel = "detailed_model"
        case appleMaps = "apple_maps"
        case includeCaption = "include_caption"
        case randomSelection = "random_selection"
        case runsRoot = "runs_root"
        case manifest
        case selections
        case captionSelections = "caption_selections"
        case sessionID = "session_id"
        case itemID = "item_id"
        case revision
        case decisionID = "decision_id"
        case settingsPath = "settings_path"
        case campaignID = "campaign_id"
    }

    func encode(to encoder: Encoder) throws {
        guard !id.isEmpty else {
            throw EncodingError.invalidValue(id, .init(codingPath: [], debugDescription: "request id cannot be empty"))
        }
        if command.isAutonomy && !Self.isValidAutonomyIdentifier(id) {
            throw EncodingError.invalidValue(id, .init(codingPath: [], debugDescription: "autonomy request id is invalid"))
        }
        try validateQueuePayload()
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(id, forKey: .id)
        try container.encode(command, forKey: .command)
        var value = container.nestedContainer(keyedBy: PayloadKeys.self, forKey: .payload)
        switch payload {
        case let .autonomyStart(campaignID, decisionID, runsRoot, settingsPath, limit):
            try value.encode(campaignID, forKey: .campaignID)
            try value.encode(decisionID, forKey: .decisionID)
            try value.encode(runsRoot, forKey: .runsRoot)
            try value.encode(settingsPath, forKey: .settingsPath)
            if let limit { try value.encode(limit, forKey: .limit) } else { try value.encodeNil(forKey: .limit) }
        case let .autonomyControl(campaignID, decisionID):
            try value.encode(campaignID, forKey: .campaignID)
            try value.encode(decisionID, forKey: .decisionID)
        case .autonomyStatus: break
        case let .preflight(models):
            try value.encode(models, forKey: .models)
        case let .scan(scan):
            try value.encode(scan.limit, forKey: .limit)
            try value.encode(scan.modelPolicy, forKey: .modelPolicy)
            try value.encodeIfPresent(scan.model, forKey: .model)
            try value.encode(scan.fastModel, forKey: .fastModel)
            try value.encode(scan.detailedModel, forKey: .detailedModel)
            try value.encode(scan.appleMaps, forKey: .appleMaps)
            try value.encode(scan.includeCaption, forKey: .includeCaption)
            try value.encode(scan.randomSelection, forKey: .randomSelection)
            try value.encode(scan.runsRoot, forKey: .runsRoot)
        case let .review(manifest, selections, captionSelections):
            try value.encode(manifest, forKey: .manifest)
            try value.encode(selections, forKey: .selections)
            if !captionSelections.isEmpty {
                try value.encode(captionSelections, forKey: .captionSelections)
            }
        case let .manifest(path):
            try value.encode(path, forKey: .manifest)
        case .cancel:
            break
        case let .queueStart(sessionID, revision, decisionID, runsRoot, settingsPath):
            try value.encode(sessionID, forKey: .sessionID)
            try value.encode(revision, forKey: .revision)
            try value.encode(decisionID, forKey: .decisionID)
            try value.encode(runsRoot, forKey: .runsRoot)
            try value.encode(settingsPath, forKey: .settingsPath)
        case let .queueRevision(sessionID, revision, decisionID):
            try value.encode(sessionID, forKey: .sessionID)
            try value.encode(revision, forKey: .revision)
            try value.encode(decisionID, forKey: .decisionID)
        case let .queueUpdate(sessionID, revision, decisionID, settingsPath):
            try value.encode(sessionID, forKey: .sessionID)
            try value.encode(revision, forKey: .revision)
            try value.encode(decisionID, forKey: .decisionID)
            try value.encode(settingsPath, forKey: .settingsPath)
        case let .queueItemDecision(sessionID, itemID, revision, decisionID):
            try value.encode(sessionID, forKey: .sessionID)
            try value.encode(itemID, forKey: .itemID)
            try value.encode(revision, forKey: .revision)
            try value.encode(decisionID, forKey: .decisionID)
        }
    }

    private func validateQueuePayload() throws {
        let valid: Bool
        switch payload {
        case let .autonomyStart(campaignID, decisionID, runsRoot, settingsPath, limit):
            valid = command == .autonomyStart && Self.isValidAutonomyIdentifier(campaignID)
                && Self.isValidAutonomyIdentifier(decisionID) && Self.isValidQueueArtifactPath(runsRoot)
                && Self.isValidQueueArtifactPath(settingsPath) && (limit == nil || (1 ... Int(Int32.max)).contains(limit!))
        case let .autonomyControl(campaignID, decisionID):
            valid = [.autonomyPause, .autonomyResume].contains(command)
                && Self.isValidAutonomyIdentifier(campaignID) && Self.isValidAutonomyIdentifier(decisionID)
        case .autonomyStatus: valid = command == .autonomyStatus
        case let .queueStart(sessionID, revision, decisionID, runsRoot, settingsPath):
            valid = command == .queueStart
                && revision == 0
                && Self.isValidQueueIdentifier(sessionID)
                && Self.isValidQueueIdentifier(decisionID)
                && Self.isValidQueueArtifactPath(runsRoot)
                && Self.isValidQueueArtifactPath(settingsPath)
        case let .queueRevision(sessionID, revision, decisionID):
            valid = [.queueResume, .queuePause, .queueStop].contains(command)
                && Self.isValidQueueRevision(revision)
                && Self.isValidQueueIdentifier(sessionID)
                && Self.isValidQueueIdentifier(decisionID)
        case let .queueUpdate(sessionID, revision, decisionID, settingsPath):
            valid = command == .queueUpdate
                && Self.isValidQueueRevision(revision)
                && Self.isValidQueueIdentifier(sessionID)
                && Self.isValidQueueIdentifier(decisionID)
                && Self.isValidQueueArtifactPath(settingsPath)
        case let .queueItemDecision(sessionID, itemID, revision, decisionID):
            valid = [.queuePersist, .queueDiscard, .queueRescan].contains(command)
                && Self.isValidQueueRevision(revision)
                && Self.isValidQueueIdentifier(sessionID)
                && Self.isValidQueueIdentifier(itemID)
                && Self.isValidQueueIdentifier(decisionID)
        default:
            valid = !command.isAutonomy && ![
                .queueStart, .queueResume, .queueUpdate, .queuePause,
                .queuePersist, .queueDiscard, .queueRescan, .queueStop,
            ].contains(command)
        }
        guard valid else {
            throw EncodingError.invalidValue(
                payload,
                .init(codingPath: [], debugDescription: "queue payload is invalid")
            )
        }
    }

    private static func isValidAutonomyIdentifier(_ value: String) -> Bool {
        value.range(of: "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", options: .regularExpression) != nil
    }

    private static func isValidQueueRevision(_ revision: Int) -> Bool {
        (0 ... Int(Int32.max)).contains(revision)
    }

    private static func isValidQueueIdentifier(_ value: String) -> Bool {
        guard !value.isEmpty, value.utf8.count <= 64 else { return false }
        return value.unicodeScalars.allSatisfy { scalar in
            switch scalar.value {
            case 45, 46, 48 ... 57, 65 ... 90, 95, 97 ... 122:
                return true
            default:
                return false
            }
        }
    }

    private static func isValidQueueArtifactPath(_ path: String) -> Bool {
        guard path.hasPrefix("/"), !path.isEmpty, path.utf8.count <= 2_048 else { return false }
        let components = path.split(separator: "/", omittingEmptySubsequences: false)
        return !components.contains(where: { $0 == "." || $0 == ".." })
            && !path.unicodeScalars.contains(where: {
                $0.value < 0x20 || (0x7F ... 0x9F).contains($0.value)
            })
    }
}

enum WorkerEventKind: String, Codable, Sendable {
    case autonomyCampaign = "autonomy_campaign"
    case autonomyActivity = "autonomy_activity"
    case started
    case photoProgress = "photo_progress"
    case queueSession = "queue_session"
    case queueItem = "queue_item"
    case completed
    case error

    var humanLabel: String {
        switch self {
        case .autonomyCampaign: return "Estado del recorrido autónomo"
        case .autonomyActivity: return "Actividad del recorrido autónomo"
        case .started: return "Operación iniciada"
        case .photoProgress: return "Progreso de foto"
        case .queueSession: return "Estado de la cola"
        case .queueItem: return "Estado del elemento"
        case .completed: return "Operación completada"
        case .error: return "Error de operación"
        }
    }
}

enum WorkerEventError: LocalizedError {
    case unknownFields
    case invalidValue(String)

    var errorDescription: String? {
        switch self {
        case .unknownFields:
            return "El helper envió campos IPC no autorizados."
        case let .invalidValue(field):
            return "El helper envió un valor IPC inválido para \(field)."
        }
    }
}

struct WorkerEvent: Decodable, Equatable, Sendable {
    let autonomyCampaign: AutonomyCampaignSnapshot?
    let autonomyActivity: AutonomyActivitySnapshot?
    let id: String
    let event: WorkerEventKind
    let operation: String?
    let uuid: String?
    let state: String?
    let modelUsed: String?
    let modelReason: String?
    let keywordsCount: Int?
    let sessionID: String?
    let itemID: String?
    let revision: Int?
    let decisionID: String?
    let queuedCount: Int?
    let analyzingCount: Int?
    let readyCount: Int?
    let saveQueuedCount: Int?
    let savingCount: Int?
    let savedCount: Int?
    let attentionCount: Int?
    let exitCode: Int?
    let manifest: String?
    let warningCodes: [String]?
    let errorCodes: [String]?
    let nextAction: String?
    let code: String?
    let models: [String: String]?
    let safeInstruction: String?

    var humanDetailText: String? {
        switch event {
        case .started:
            switch operation {
            case "preflight": return "Preparación local"
            case "scan": return "Análisis dry-run"
            case "review": return "Revisión de propuestas"
            case "apply": return "Aplicación confirmada"
            case "rollback": return "Rollback verificado"
            default: return "Operación local"
            }
        case .photoProgress:
            guard let uuid, !uuid.isEmpty else { return nil }
            return String(uuid.prefix(8))
        case .queueSession, .queueItem, .autonomyCampaign:
            return nil
        case .autonomyActivity:
            return nil
        case .completed:
            if errorCodes?.contains("CANCELLED") == true {
                return "La cancelación terminó; revisa el run antes de continuar."
            }
            if exitCode.map({ $0 != 0 }) == true {
                return "Terminó con errores revisables; consulta el run."
            }
            if warningCodes?.isEmpty == false {
                return "Completada con advertencias; revisa el run."
            }
            return nil
        case .error:
            return HumanErrorCopy.message(for: code ?? "")
        }
    }

    var humanAccessibilityLabel: String {
        let detail = humanDetailText
        guard let detail, !detail.isEmpty else { return event.humanLabel }
        return "\(event.humanLabel): \(detail)"
    }

    private enum CodingKeys: String, CodingKey, CaseIterable {
        case id
        case event
        case campaignID = "campaign_id"
        case operation
        case uuid
        case state
        case modelUsed = "model_used"
        case modelReason = "model_reason"
        case keywordsCount = "keywords_count"
        case sessionID = "session_id"
        case itemID = "item_id"
        case revision
        case decisionID = "decision_id"
        case queuedCount = "queued"
        case analyzingCount = "analyzing"
        case readyCount = "ready"
        case saveQueuedCount = "save_queued"
        case savingCount = "saving"
        case savedCount = "saved"
        case attentionCount = "attention"
        case exitCode = "exit_code"
        case manifest
        case warningCodes = "warning_codes"
        case errorCodes = "error_codes"
        case nextAction = "next_action"
        case code
        case models
        case safeInstruction = "safe_instruction"
        case position
        case photosLocalIdentifier = "photos_local_identifier"
    }

    init(from decoder: Decoder) throws {
        let dynamic = try decoder.container(keyedBy: AnyCodingKey.self)
        let container = try decoder.container(keyedBy: CodingKeys.self)
        id = try container.decode(String.self, forKey: .id)
        event = try container.decode(WorkerEventKind.self, forKey: .event)
        autonomyCampaign = event == .autonomyCampaign ? try AutonomyCampaignSnapshot(from: decoder) : nil
        autonomyActivity = event == .autonomyActivity ? try AutonomyActivitySnapshot(from: decoder) : nil
        operation = try container.decodeIfPresent(String.self, forKey: .operation)
        uuid = try container.decodeIfPresent(String.self, forKey: .uuid)
        state = try container.decodeIfPresent(String.self, forKey: .state)
        modelUsed = try container.decodeIfPresent(String.self, forKey: .modelUsed)
        modelReason = try container.decodeIfPresent(String.self, forKey: .modelReason)
        keywordsCount = try container.decodeIfPresent(Int.self, forKey: .keywordsCount)
        sessionID = try container.decodeIfPresent(String.self, forKey: .sessionID)
        itemID = try container.decodeIfPresent(String.self, forKey: .itemID)
        revision = try container.decodeIfPresent(Int.self, forKey: .revision)
        decisionID = try container.decodeIfPresent(String.self, forKey: .decisionID)
        queuedCount = try container.decodeIfPresent(Int.self, forKey: .queuedCount)
        analyzingCount = try container.decodeIfPresent(Int.self, forKey: .analyzingCount)
        readyCount = try container.decodeIfPresent(Int.self, forKey: .readyCount)
        saveQueuedCount = try container.decodeIfPresent(Int.self, forKey: .saveQueuedCount)
        savingCount = try container.decodeIfPresent(Int.self, forKey: .savingCount)
        savedCount = try container.decodeIfPresent(Int.self, forKey: .savedCount)
        attentionCount = try container.decodeIfPresent(Int.self, forKey: .attentionCount)
        exitCode = try container.decodeIfPresent(Int.self, forKey: .exitCode)
        manifest = try container.decodeIfPresent(String.self, forKey: .manifest)
        warningCodes = try container.decodeIfPresent([String].self, forKey: .warningCodes)
        errorCodes = try container.decodeIfPresent([String].self, forKey: .errorCodes)
        nextAction = try container.decodeIfPresent(String.self, forKey: .nextAction)
        code = try container.decodeIfPresent(String.self, forKey: .code)
        models = try container.decodeIfPresent([String: String].self, forKey: .models)
        safeInstruction = try container.decodeIfPresent(String.self, forKey: .safeInstruction)

        let common = Set([CodingKeys.id.rawValue, CodingKeys.event.rawValue])
        let eventFields: Set<String>
        switch event {
        case .autonomyCampaign:
            eventFields = ["campaign_id", "revision", "state", "total", "examined", "analyzed", "saved", "no_change", "attention", "remaining", "in_flight", "invalid_count", "reason"]
        case .autonomyActivity:
            eventFields = [CodingKeys.campaignID.rawValue, CodingKeys.revision.rawValue, CodingKeys.position.rawValue, CodingKeys.state.rawValue, CodingKeys.photosLocalIdentifier.rawValue]
        case .started:
            eventFields = [CodingKeys.operation.rawValue]
        case .photoProgress:
            eventFields = [
                CodingKeys.uuid.rawValue,
                CodingKeys.state.rawValue,
                CodingKeys.modelUsed.rawValue,
                CodingKeys.modelReason.rawValue,
                CodingKeys.keywordsCount.rawValue,
            ]
        case .queueSession:
            eventFields = [
                CodingKeys.sessionID.rawValue,
                CodingKeys.revision.rawValue,
                CodingKeys.state.rawValue,
                CodingKeys.queuedCount.rawValue,
                CodingKeys.analyzingCount.rawValue,
                CodingKeys.readyCount.rawValue,
                CodingKeys.saveQueuedCount.rawValue,
                CodingKeys.savingCount.rawValue,
                CodingKeys.savedCount.rawValue,
                CodingKeys.attentionCount.rawValue,
            ]
        case .queueItem:
            eventFields = [
                CodingKeys.sessionID.rawValue,
                CodingKeys.itemID.rawValue,
                CodingKeys.revision.rawValue,
                CodingKeys.decisionID.rawValue,
                CodingKeys.state.rawValue,
                CodingKeys.manifest.rawValue,
            ]
        case .completed:
            eventFields = [
                CodingKeys.exitCode.rawValue,
                CodingKeys.manifest.rawValue,
                CodingKeys.warningCodes.rawValue,
                CodingKeys.errorCodes.rawValue,
                CodingKeys.nextAction.rawValue,
                CodingKeys.models.rawValue,
                CodingKeys.safeInstruction.rawValue,
            ]
        case .error:
            eventFields = [CodingKeys.code.rawValue]
        }
        guard dynamic.allKeys.allSatisfy({ common.union(eventFields).contains($0.stringValue) }) else {
            throw WorkerEventError.unknownFields
        }

        try validate()
    }

    private func validate() throws {
        guard !id.isEmpty else { throw WorkerEventError.invalidValue("id") }
        switch event {
        case .autonomyCampaign:
            try autonomyCampaign?.validate()
        case .autonomyActivity:
            guard autonomyActivity != nil else { throw WorkerEventError.invalidValue("autonomy_activity") }
        case .started:
            guard operation?.isEmpty == false else { throw WorkerEventError.invalidValue("operation") }
        case .photoProgress:
            guard uuid?.isEmpty == false, state?.isEmpty == false else {
                throw WorkerEventError.invalidValue("photo_progress")
            }
            // The state is rendered into progress counters and recovery copy.
            // Rejecting an unknown value keeps a malformed helper event from
            // being counted as a successful photo or enabling a misleading
            // next action. This is the union emitted by scan/apply/rollback.
            let knownStates = Set([
                "ready", "noop", "analysis_failed", "cancelled",
                "writing", "verified", "failed", "uncertain",
                "removing", "verified_removed", "casing_conflict", "already_absent"
            ])
            guard let state, knownStates.contains(state) else {
                throw WorkerEventError.invalidValue("photo_progress.state")
            }
            if let modelUsed {
                guard OllamaModelPresentationPolicy.isValidModelName(modelUsed) else {
                    throw WorkerEventError.invalidValue("model_used")
                }
            }
            if let keywordsCount, keywordsCount < 0 {
                throw WorkerEventError.invalidValue("keywords_count")
            }
        case .queueSession:
            guard Self.isValidQueueIdentifier(sessionID),
                  let revision, Self.isValidQueueRevision(revision),
                  let state,
                  Self.queueSessionStates.contains(state) else {
                throw WorkerEventError.invalidValue("queue_session")
            }
            for count in [queuedCount, analyzingCount, readyCount, saveQueuedCount, savingCount, savedCount, attentionCount].compactMap({ $0 }) {
                guard (0 ... Int(Int32.max)).contains(count) else {
                    throw WorkerEventError.invalidValue("queue_session.count")
                }
            }
        case .queueItem:
            guard Self.isValidQueueIdentifier(sessionID),
                  Self.isValidQueueIdentifier(itemID),
                  let revision, Self.isValidQueueRevision(revision),
                  let state,
                  Self.queueItemStates.contains(state),
                  decisionID.map(Self.isValidQueueIdentifier) ?? true else {
                throw WorkerEventError.invalidValue("queue_item")
            }
            if let manifest, !Self.isValidManifestPath(manifest) {
                throw WorkerEventError.invalidValue("manifest")
            }
        case .completed:
            guard let exitCode, (0 ... 2).contains(exitCode) else {
                throw WorkerEventError.invalidValue("exit_code")
            }
            if exitCode == 0 {
                guard errorCodes?.isEmpty != false, safeInstruction == nil else {
                    throw WorkerEventError.invalidValue("completed")
                }
            } else if let safeInstruction,
                      !OllamaModelPresentationPolicy.isValidPullInstruction(safeInstruction) {
                throw WorkerEventError.invalidValue("safe_instruction")
            }
            if let models {
                guard models.count <= 4,
                      models.allSatisfy({ model, version in
                          OllamaModelPresentationPolicy.isValidModelName(model)
                              && OllamaModelPresentationPolicy.isValidVersion(version)
                      }) else {
                    throw WorkerEventError.invalidValue("models")
                }
            }
            if let manifest {
                guard Self.isValidManifestPath(manifest) else {
                    throw WorkerEventError.invalidValue("manifest")
                }
            }
        case .error:
            guard code?.isEmpty == false else { throw WorkerEventError.invalidValue("code") }
        }
    }

    private static let queueSessionStates: Set<String> = [
        "running", "paused", "stopped", "attention",
    ]

    private static let queueItemStates: Set<String> = [
        "discovered", "queued", "preparing", "analyzing", "validating",
        "ready", "edited", "save_queued", "saving", "verified", "discarded", "failed", "uncertain",
    ]

    private static func isValidQueueIdentifier(_ value: String?) -> Bool {
        guard let value, !value.isEmpty, value.utf8.count <= 64 else { return false }
        return value.unicodeScalars.allSatisfy { scalar in
            switch scalar.value {
            case 45, 46, 48 ... 57, 65 ... 90, 95, 97 ... 122:
                return true
            default:
                return false
            }
        }
    }

    private static func isValidQueueRevision(_ revision: Int) -> Bool {
        (0 ... Int(Int32.max)).contains(revision)
    }

    private static func isValidManifestPath(_ manifest: String) -> Bool {
        let components = manifest.split(separator: "/", omittingEmptySubsequences: false)
        let hasUnsafeComponent = components.contains {
            $0 == "." || $0 == ".." || $0.hasPrefix(".exports-")
        }
        let hasControlCharacter = manifest.unicodeScalars.contains {
            $0.value < 0x20 || (0x7F ... 0x9F).contains($0.value)
        }
        return manifest.hasPrefix("/")
            && manifest.utf8.count <= 2_048
            && String(components.last ?? "") == "manifest.json"
            && !hasUnsafeComponent
            && !hasControlCharacter
    }
}

struct JSONLineEventDecoder {
    static let maxLineBytes = 64 * 1024
    private var buffer = Data()
    private let decoder = JSONDecoder()
    private var discardingOversizedLine = false

    mutating func reset() {
        buffer.removeAll(keepingCapacity: false)
        discardingOversizedLine = false
    }

    mutating func append(_ data: Data) -> [Result<WorkerEvent, Error>] {
        var results: [Result<WorkerEvent, Error>] = []
        var cursor = data.startIndex
        while cursor < data.endIndex {
            if discardingOversizedLine {
                guard let newline = data[cursor...].firstIndex(of: 0x0A) else {
                    return results
                }
                cursor = data.index(after: newline)
                discardingOversizedLine = false
                continue
            }

            guard let newline = data[cursor...].firstIndex(of: 0x0A) else {
                let tail = data[cursor..<data.endIndex]
                guard buffer.count + tail.count <= Self.maxLineBytes else {
                    buffer.removeAll(keepingCapacity: false)
                    discardingOversizedLine = true
                    results.append(.failure(WorkerEventError.invalidValue("line")))
                    return results
                }
                buffer.append(tail)
                return results
            }

            let line = data[cursor..<newline]
            if buffer.count + line.count > Self.maxLineBytes {
                buffer.removeAll(keepingCapacity: false)
                results.append(.failure(WorkerEventError.invalidValue("line")))
            } else {
                buffer.append(line)
                if !buffer.isEmpty {
                    do {
                        try StrictJSONManifestValidator.validateSyntax(buffer)
                        let event = try decoder.decode(WorkerEvent.self, from: buffer)
                        if event.event == .autonomyCampaign {
                            // JSONDecoder accepts integral float/exponent literals as Int;
                            // Python requires integer JSON syntax for this contract.
                            try StrictJSONManifestValidator.validateSyntax(buffer, integerRootKeys: [
                                "revision", "total", "examined", "analyzed", "saved", "no_change",
                                "attention", "remaining", "in_flight", "invalid_count",
                            ])
                        }
                        results.append(.success(event))
                    } catch {
                        results.append(.failure(error))
                    }
                }
                buffer.removeAll(keepingCapacity: false)
            }
            cursor = data.index(after: newline)
        }
        return results
    }
}

private struct AnyCodingKey: CodingKey {
    let stringValue: String
    let intValue: Int?

    init?(stringValue: String) {
        self.stringValue = stringValue
        intValue = nil
    }

    init?(intValue: Int) {
        stringValue = String(intValue)
        self.intValue = intValue
    }
}
