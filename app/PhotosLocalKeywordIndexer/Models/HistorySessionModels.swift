import Darwin
import Foundation

enum HistoryDisplayRow: Identifiable, Equatable, Sendable {
    case session(HistorySessionSummary)
    case run(HistoryRunSummary)

    var id: String {
        switch self {
        case .session(let session): return "session-\(session.sessionID)"
        case .run(let run): return "run-\(run.runID)"
        }
    }
}

struct HistorySessionAttemptSummary: Identifiable, Equatable, Sendable {
    let itemID: String
    let revision: Int
    let state: HistorySessionItemState
    let decisions: [HistorySessionDecisionAction]

    var id: String { "\(itemID)-\(revision)" }
    var stateLabel: String { state.displayLabel }
    var decisionLabels: [String] { decisions.map(\.displayLabel) }

    var presentationText: String {
        let decisionText = decisionLabels.isEmpty
            ? "Sin decisión registrada"
            : decisionLabels.joined(separator: ", ")
        return "Intento \(revision): \(stateLabel). \(decisionText)."
    }
}

struct HistorySessionSummary: Identifiable, Equatable, Sendable {
    let sessionID: String
    let revision: Int
    let state: HistorySessionState
    let attempts: [HistorySessionAttemptSummary]
    let runs: [HistoryRunSummary]

    var id: String { sessionID }
    var stateLabel: String { state.displayLabel }
    var canRollback: Bool { runs.contains(where: \.canRollback) }

    var displayTitle: String {
        "Sesión \(String(sessionID.prefix(8)))"
    }

    var summaryText: String {
        let attemptLabel = attempts.count == 1 ? "1 intento" : "\(attempts.count) intentos"
        let decisionCount = attempts.reduce(0) { $0 + $1.decisions.count }
        let decisionLabel = decisionCount == 1 ? "1 decisión" : "\(decisionCount) decisiones"
        let runLabel = runs.count == 1 ? "1 ejecución local" : "\(runs.count) ejecuciones locales"
        return "\(attemptLabel) · \(decisionLabel) · \(runLabel)"
    }

    var rollbackAvailabilityText: String {
        canRollback
            ? "Rollback disponible solo para cambios verificados."
            : "Rollback no disponible en esta sesión."
    }

    /// Sanitized copy for History and assistive technologies. It intentionally
    /// excludes paths, manifest contents, prompts, photo metadata and full IDs.
    var presentationText: String {
        ([stateLabel, summaryText] + attempts.map(\.presentationText) + [rollbackAvailabilityText])
            .joined(separator: " ")
    }

    fileprivate var newestRunDate: String {
        runs.first?.createdAt ?? ""
    }
}

enum HistorySessionState: String, Equatable, Sendable {
    case running
    case paused
    case stopped
    case attention

    var displayLabel: String {
        switch self {
        case .running: return "Activa"
        case .paused: return "Pausada"
        case .stopped: return "Finalizada"
        case .attention: return "Requiere atención"
        }
    }
}

enum HistorySessionItemState: String, Equatable, Sendable {
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

    var displayLabel: String {
        switch self {
        case .discovered: return "Descubierta"
        case .queued: return "En cola"
        case .preparing: return "Preparando"
        case .analyzing: return "Analizando"
        case .validating: return "Validando"
        case .ready: return "Lista para revisar"
        case .edited: return "Editada"
        case .saveQueued: return "Esperando guardado"
        case .saving: return "Guardando"
        case .verified: return "Guardada y verificada"
        case .discarded: return "Descartada"
        case .failed: return "Fallida"
        case .uncertain: return "Escritura incierta"
        }
    }
}

enum HistorySessionDecisionAction: String, Equatable, Sendable {
    case persist
    case discard
    case rescan

    var displayLabel: String {
        switch self {
        case .persist: return "Guardar en Fotos"
        case .discard: return "Descartar"
        case .rescan: return "Reanalizar"
        }
    }
}

struct QueueSessionRecoveryConfiguration: Decodable, Equatable, Sendable {
    let photoCount: Int
    let inferenceConcurrency: Int
    let autoAnalyze: Bool
    let includeCaption: Bool
    let appleMaps: Bool
    let randomSelection: Bool
    let modelPolicy: String
    let model: String?
    let fastModel: String
    let detailedModel: String

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: SessionCodingKey.self)
        try values.requireExactKeys([
            "photo_count", "inference_concurrency", "auto_analyze", "include_caption",
            "apple_maps", "random_selection", "model_policy", "model", "fast_model", "detailed_model"
        ])
        photoCount = try values.decodeBoundedInt(for: "photo_count", range: 1...50)
        inferenceConcurrency = try values.decodeBoundedInt(for: "inference_concurrency", range: 1...4)
        autoAnalyze = try values.decode(Bool.self, forKey: SessionCodingKey("auto_analyze"))
        includeCaption = try values.decode(Bool.self, forKey: SessionCodingKey("include_caption"))
        appleMaps = try values.decode(Bool.self, forKey: SessionCodingKey("apple_maps"))
        randomSelection = try values.decode(Bool.self, forKey: SessionCodingKey("random_selection"))
        modelPolicy = try values.decode(String.self, forKey: SessionCodingKey("model_policy"))
        guard modelPolicy == "single" || modelPolicy == "adaptive" else {
            throw SessionDecodeError.invalidValue
        }
        model = try values.decodeIfPresent(String.self, forKey: SessionCodingKey("model"))
        fastModel = try values.decode(String.self, forKey: SessionCodingKey("fast_model"))
        detailedModel = try values.decode(String.self, forKey: SessionCodingKey("detailed_model"))
        try Self.validateModel(fastModel)
        try Self.validateModel(detailedModel)
        if let model { try Self.validateModel(model) }
        if modelPolicy == "single", model == nil { throw SessionDecodeError.invalidValue }
    }

    private static func validateModel(_ value: String) throws {
        let pattern = #"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*(?::[A-Za-z0-9][A-Za-z0-9._-]*)?$"#
        guard !value.isEmpty,
              value.utf8.count <= 128,
              value.range(of: pattern, options: .regularExpression) != nil,
              value.unicodeScalars.allSatisfy({ scalar in
                  switch scalar.properties.generalCategory {
                  case .control, .format, .surrogate: return false
                  default: return true
                  }
              }) else {
            throw SessionDecodeError.invalidValue
        }
    }
}

struct QueueSessionRecovery: Equatable, Sendable {
    let sessionID: String
    let revision: Int
    let state: HistorySessionState
    let config: QueueSessionRecoveryConfiguration
}

private struct HistorySessionLedger: Decodable {
    let sessionID: String
    let revision: Int
    let state: HistorySessionState
    let config: QueueSessionRecoveryConfiguration
    let items: [Item]
    let decisions: [Decision]

    struct Item: Decodable {
        let itemID: String
        let revision: Int
        let state: HistorySessionItemState
        let sourceRunID: String?
        let reviewedRunID: String?

        init(from decoder: Decoder) throws {
            let values = try decoder.container(keyedBy: SessionCodingKey.self)
            try values.requireExactKeys([
                "item_id", "revision", "state", "source_run_id", "reviewed_run_id"
            ])
            itemID = try values.decodeCanonicalUUID(for: "item_id")
            revision = try values.decodeBoundedInt(for: "revision", range: 1...Int(Int32.max))
            state = try values.decodeEnum(HistorySessionItemState.self, for: "state")
            sourceRunID = try values.decodeOptionalCanonicalUUID(for: "source_run_id")
            reviewedRunID = try values.decodeOptionalCanonicalUUID(for: "reviewed_run_id")
        }
    }

    struct Decision: Decodable {
        let decisionID: String
        let itemID: String
        let revision: Int
        let action: HistorySessionDecisionAction

        init(from decoder: Decoder) throws {
            let values = try decoder.container(keyedBy: SessionCodingKey.self)
            try values.requireExactKeys(["decision_id", "item_id", "revision", "action"])
            decisionID = try values.decodeCanonicalUUID(for: "decision_id")
            itemID = try values.decodeCanonicalUUID(for: "item_id")
            revision = try values.decodeBoundedInt(for: "revision", range: 1...Int(Int32.max))
            action = try values.decodeEnum(HistorySessionDecisionAction.self, for: "action")
        }
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: SessionCodingKey.self)
        try values.requireExactKeys([
            "schema_version", "session_id", "revision", "state", "config", "items", "decisions"
        ])
        let schemaVersion = try values.decode(Int.self, forKey: SessionCodingKey("schema_version"))
        guard schemaVersion == 1 else { throw SessionDecodeError.invalidValue }
        sessionID = try values.decodeCanonicalUUID(for: "session_id")
        revision = try values.decodeBoundedInt(for: "revision", range: 0...Int(Int32.max))
        state = try values.decodeEnum(HistorySessionState.self, for: "state")
        config = try values.decode(
            QueueSessionRecoveryConfiguration.self,
            forKey: SessionCodingKey("config")
        )
        items = try values.decode([Item].self, forKey: SessionCodingKey("items"))
        decisions = try values.decode([Decision].self, forKey: SessionCodingKey("decisions"))

        let itemKeys = items.map { "\($0.itemID)-\($0.revision)" }
        guard Set(itemKeys).count == itemKeys.count,
              Set(decisions.map(\.decisionID)).count == decisions.count else {
            throw SessionDecodeError.invalidValue
        }
        let knownItems = Set(itemKeys)
        guard decisions.allSatisfy({ knownItems.contains("\($0.itemID)-\($0.revision)") }) else {
            throw SessionDecodeError.invalidValue
        }
    }

    var referencedRunIDs: Set<String> {
        Set(items.flatMap { [$0.sourceRunID, $0.reviewedRunID].compactMap { $0 } })
    }

    func summary(runs: [HistoryRunSummary]) -> HistorySessionSummary {
        let decisionsByAttempt = Dictionary(grouping: decisions) { "\($0.itemID)-\($0.revision)" }
        let attempts = items.map { item in
            HistorySessionAttemptSummary(
                itemID: item.itemID,
                revision: item.revision,
                state: item.state,
                decisions: (decisionsByAttempt["\(item.itemID)-\(item.revision)"] ?? []).map(\.action)
            )
        }
        return HistorySessionSummary(
            sessionID: sessionID,
            revision: revision,
            state: state,
            attempts: attempts,
            runs: runs
        )
    }
}

private struct SessionCodingKey: CodingKey, Hashable {
    let stringValue: String
    let intValue: Int? = nil

    init(_ stringValue: String) {
        self.stringValue = stringValue
    }

    init?(stringValue: String) {
        self.init(stringValue)
    }

    init?(intValue: Int) {
        return nil
    }
}

private enum SessionDecodeError: Error {
    case invalidKeys
    case invalidValue
}

private extension KeyedDecodingContainer where Key == SessionCodingKey {
    func requireExactKeys(_ expected: Set<String>) throws {
        guard Set(allKeys.map(\.stringValue)) == expected else {
            throw SessionDecodeError.invalidKeys
        }
    }

    func decodeBoundedInt(for key: String, range: ClosedRange<Int>) throws -> Int {
        let value = try decode(Int.self, forKey: SessionCodingKey(key))
        guard range.contains(value) else { throw SessionDecodeError.invalidValue }
        return value
    }

    func decodeCanonicalUUID(for key: String) throws -> String {
        let value = try decode(String.self, forKey: SessionCodingKey(key))
        guard let parsed = UUID(uuidString: value), parsed.uuidString.lowercased() == value else {
            throw SessionDecodeError.invalidValue
        }
        return value
    }

    func decodeOptionalCanonicalUUID(for key: String) throws -> String? {
        guard let value = try decodeIfPresent(String.self, forKey: SessionCodingKey(key)) else { return nil }
        guard let parsed = UUID(uuidString: value), parsed.uuidString.lowercased() == value else {
            throw SessionDecodeError.invalidValue
        }
        return value
    }

    func decodeEnum<T: RawRepresentable>(_ type: T.Type, for key: String) throws -> T where T.RawValue == String {
        let value = try decode(String.self, forKey: SessionCodingKey(key))
        guard let result = T(rawValue: value) else { throw SessionDecodeError.invalidValue }
        return result
    }
}

enum HistorySessionStore {
    private static let maximumSessionBytes = 256 * 1024

    static func latestResumableSession(
        sessionsRoot: URL,
        fileManager: FileManager = .default
    ) -> QueueSessionRecovery? {
        guard isTrustedDirectory(sessionsRoot) else { return nil }
        let directories = (try? fileManager.contentsOfDirectory(
            at: sessionsRoot,
            includingPropertiesForKeys: [.contentModificationDateKey, .isDirectoryKey, .isSymbolicLinkKey],
            options: [.skipsHiddenFiles]
        )) ?? []
        let candidates: [(ledger: HistorySessionLedger, modified: Date)] = directories.compactMap { directory in
            let ledgerURL = directory.appendingPathComponent("session.json", isDirectory: false)
            guard isPrivateSessionDirectory(directory),
                  let data = try? ManifestFileSecurity.read(from: ledgerURL),
                  data.count <= maximumSessionBytes,
                  let ledger = try? JSONDecoder().decode(HistorySessionLedger.self, from: data),
                  ledger.sessionID == directory.lastPathComponent,
                  ledger.state != .stopped else {
                return nil
            }
            let modified = (try? ledgerURL.resourceValues(forKeys: [.contentModificationDateKey]))?
                .contentModificationDate ?? .distantPast
            return (ledger, modified)
        }
        guard let latest = candidates.max(by: { lhs, rhs in
            if lhs.modified == rhs.modified {
                return lhs.ledger.sessionID < rhs.ledger.sessionID
            }
            return lhs.modified < rhs.modified
        }) else { return nil }
        return QueueSessionRecovery(
            sessionID: latest.ledger.sessionID,
            revision: latest.ledger.revision,
            state: latest.ledger.state,
            config: latest.ledger.config
        )
    }

    static func group(
        runsRoot: URL,
        runs: [HistoryRunSummary],
        fileManager: FileManager = .default
    ) -> (sessions: [HistorySessionSummary], standaloneRuns: [HistoryRunSummary]) {
        let sessionsRoot = runsRoot.deletingLastPathComponent()
            .appendingPathComponent("queue-sessions", isDirectory: true)
        guard isTrustedDirectory(sessionsRoot) else {
            return ([], runs)
        }
        let directories = (try? fileManager.contentsOfDirectory(
            at: sessionsRoot,
            includingPropertiesForKeys: [.isDirectoryKey, .isSymbolicLinkKey],
            options: [.skipsHiddenFiles]
        )) ?? []
        let runByID = Dictionary(uniqueKeysWithValues: runs.map { ($0.runID, $0) })

        let ledgers: [HistorySessionLedger] = directories.compactMap { directory in
            guard isPrivateSessionDirectory(directory),
                  let data = try? ManifestFileSecurity.read(
                    from: directory.appendingPathComponent("session.json", isDirectory: false)
                  ),
                  data.count <= maximumSessionBytes,
                  let ledger = try? JSONDecoder().decode(HistorySessionLedger.self, from: data),
                  ledger.sessionID == directory.lastPathComponent,
                  ledger.referencedRunIDs.allSatisfy({ runByID[$0] != nil }) else {
                return nil
            }
            return ledger
        }

        let claimCounts = ledgers.reduce(into: [String: Int]()) { counts, ledger in
            for runID in ledger.referencedRunIDs {
                counts[runID, default: 0] += 1
            }
        }
        let accepted = ledgers.filter { ledger in
            ledger.referencedRunIDs.allSatisfy { claimCounts[$0] == 1 }
        }
        let claimed = accepted.reduce(into: Set<String>()) { values, ledger in
            values.formUnion(ledger.referencedRunIDs)
        }
        let sessions = accepted.map { ledger in
            ledger.summary(runs: runs.filter { ledger.referencedRunIDs.contains($0.runID) })
        }.sorted {
            if $0.newestRunDate == $1.newestRunDate { return $0.sessionID > $1.sessionID }
            return $0.newestRunDate > $1.newestRunDate
        }
        return (sessions, runs.filter { !claimed.contains($0.runID) })
    }

    private static func isTrustedDirectory(_ url: URL) -> Bool {
        var current = URL(fileURLWithPath: "/", isDirectory: true)
        for component in TrustedSystemPath.pathComponents(for: url).dropFirst() {
            current.appendPathComponent(component, isDirectory: true)
            var status = stat()
            guard lstat(current.path, &status) == 0,
                  status.st_mode & S_IFMT == S_IFDIR else {
                return false
            }
        }
        return true
    }

    private static func isPrivateSessionDirectory(_ url: URL) -> Bool {
        var status = stat()
        guard lstat(url.path, &status) == 0,
              status.st_mode & S_IFMT == S_IFDIR,
              status.st_uid == getuid(),
              status.st_mode & 0o777 == 0o700 else {
            return false
        }
        return true
    }
}
