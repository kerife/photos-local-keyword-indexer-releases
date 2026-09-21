import Darwin
import Foundation

enum QueueRescanProfile: String, Codable, CaseIterable, Equatable, Sendable {
    case freeLocal = "free_local"
    case balanced
    case conservative
}

struct QueueRescanLayers: Codable, Equatable, Sendable {
    var places: Bool
    var documentsText: Bool
    var peopleAccessories: Bool
    var semanticNormalization: Bool

    enum CodingKeys: String, CodingKey {
        case places
        case documentsText = "documents_text"
        case peopleAccessories = "people_accessories"
        case semanticNormalization = "semantic_normalization"
    }

    static let all = Self(
        places: true,
        documentsText: true,
        peopleAccessories: true,
        semanticNormalization: true
    )
}

struct QueueRescanOptions: Equatable, Sendable {
    var model: String
    var profile: QueueRescanProfile
    var layers: QueueRescanLayers
    var additionalInformation: String?
    var analysisPrompt: String?
    var resetPrompt: Bool
    var resetEdits: Bool = false

    static func defaults(model: String) -> Self {
        Self(
            model: model,
            profile: .freeLocal,
            layers: .all,
            additionalInformation: nil,
            analysisPrompt: nil,
            resetPrompt: false,
            resetEdits: false
        )
    }

    mutating func restoreDefaultPrompt() {
        analysisPrompt = nil
        resetPrompt = true
    }

    var isValid: Bool {
        Self.isValidModel(model)
            && Self.isSafeOptionalText(additionalInformation, maximumCharacters: 1_024)
            && Self.isSafeOptionalText(analysisPrompt, maximumCharacters: 4_096)
            && (!resetPrompt || analysisPrompt == nil)
    }

    private static func isValidModel(_ value: String) -> Bool {
        guard !value.isEmpty, value.utf8.count <= 128, !value.localizedCaseInsensitiveContains("cloud") else {
            return false
        }
        let pattern = #"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*(?::[A-Za-z0-9][A-Za-z0-9._-]*)?$"#
        return value.range(of: pattern, options: .regularExpression) != nil
    }

    private static func isSafeOptionalText(_ value: String?, maximumCharacters: Int) -> Bool {
        guard let value else { return true }
        guard !value.isEmpty,
              value == value.trimmingCharacters(in: .whitespacesAndNewlines),
              value.count <= maximumCharacters,
              value.unicodeScalars.allSatisfy({ scalar in
                  switch scalar.properties.generalCategory {
                  case .control, .format, .surrogate:
                      return false
                  default:
                      return true
                  }
              }) else { return false }
        let lowered = value.folding(
            options: [.caseInsensitive, .diacriticInsensitive],
            locale: Locale(identifier: "en_US_POSIX")
        )
        let deniedTerms = [
            "password", "passwd", "token", "api key", "api_key", "secret",
            "credential", "bearer", "cookie", "contrasena", "latitud", "longitud",
            "latitude", "longitude",
        ]
        guard !deniedTerms.contains(where: lowered.contains) else { return false }
        let coordinatePattern = #"[-+]?\d{1,3}(?:\.\d+)?\s*[,;]\s*[-+]?\d{1,3}(?:\.\d+)?"#
        return lowered.range(of: coordinatePattern, options: .regularExpression) == nil
    }
}

struct QueueRescanArtifact: Codable, Equatable, Sendable {
    var sessionID: String
    var itemID: String
    var revision: Int
    var decisionID: String
    var options: QueueRescanOptions

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case itemID = "item_id"
        case revision
        case decisionID = "decision_id"
        case model
        case profile
        case layers
        case additionalInformation = "additional_information"
        case analysisPrompt = "analysis_prompt"
        case resetPrompt = "reset_prompt"
        case resetEdits = "reset_edits"
    }

    init(
        sessionID: String,
        itemID: String,
        revision: Int,
        decisionID: String,
        options: QueueRescanOptions
    ) {
        self.sessionID = sessionID
        self.itemID = itemID
        self.revision = revision
        self.decisionID = decisionID
        self.options = options
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        sessionID = try values.decode(String.self, forKey: .sessionID)
        itemID = try values.decode(String.self, forKey: .itemID)
        revision = try values.decode(Int.self, forKey: .revision)
        decisionID = try values.decode(String.self, forKey: .decisionID)
        options = QueueRescanOptions(
            model: try values.decode(String.self, forKey: .model),
            profile: try values.decode(QueueRescanProfile.self, forKey: .profile),
            layers: try values.decode(QueueRescanLayers.self, forKey: .layers),
            additionalInformation: try values.decodeIfPresent(String.self, forKey: .additionalInformation),
            analysisPrompt: try values.decodeIfPresent(String.self, forKey: .analysisPrompt),
            resetPrompt: try values.decode(Bool.self, forKey: .resetPrompt),
            resetEdits: try values.decode(Bool.self, forKey: .resetEdits)
        )
    }

    func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(sessionID, forKey: .sessionID)
        try values.encode(itemID, forKey: .itemID)
        try values.encode(revision, forKey: .revision)
        try values.encode(decisionID, forKey: .decisionID)
        try values.encode(options.model, forKey: .model)
        try values.encode(options.profile, forKey: .profile)
        try values.encode(options.layers, forKey: .layers)
        try values.encode(options.additionalInformation, forKey: .additionalInformation)
        try values.encode(options.analysisPrompt, forKey: .analysisPrompt)
        try values.encode(options.resetPrompt, forKey: .resetPrompt)
        try values.encode(options.resetEdits, forKey: .resetEdits)
    }

    var isValid: Bool {
        Self.isCanonicalUUID(sessionID)
            && Self.isCanonicalUUID(itemID)
            && Self.isCanonicalUUID(decisionID)
            && (1 ... Int(Int32.max)).contains(revision)
            && options.isValid
    }

    private static func isCanonicalUUID(_ value: String) -> Bool {
        guard value.utf8.count == 36, let parsed = UUID(uuidString: value) else { return false }
        return parsed.uuidString.lowercased() == value
    }
}

enum QueueRescanStoreError: Error, Equatable {
    case invalidArtifact
    case unsafePath
    case unsafeDirectory
    case unsafeFile
    case artifactConflict
}

struct QueueRescanStore {
    let root: URL
    private let fileManager: FileManager

    init(root: URL, fileManager: FileManager = .default) {
        self.root = root.standardizedFileURL
        self.fileManager = fileManager
    }

    func write(_ artifact: QueueRescanArtifact) throws -> URL {
        guard artifact.isValid, root.isFileURL, root.path.hasPrefix("/") else {
            throw QueueRescanStoreError.invalidArtifact
        }
        let session = root.appendingPathComponent(artifact.sessionID, isDirectory: true)
        let directory = session.appendingPathComponent("rescans", isDirectory: true)
        let destination = directory.appendingPathComponent("\(artifact.decisionID).json")
        guard !containsUnsafeSymlink(root),
              !containsUnsafeSymlink(session),
              !containsUnsafeSymlink(directory),
              !containsUnsafeSymlink(destination) else {
            throw QueueRescanStoreError.unsafePath
        }
        try ensurePrivateDirectory(root)
        try ensurePrivateDirectory(session)
        try ensurePrivateDirectory(directory)
        let data = try Self.encoder.encode(artifact)
        if fileManager.fileExists(atPath: destination.path) {
            guard isPrivateFile(destination) else { throw QueueRescanStoreError.unsafeFile }
            guard try Data(contentsOf: destination) == data else {
                throw QueueRescanStoreError.artifactConflict
            }
            return destination
        }
        let temporary = directory.appendingPathComponent(".rescan-\(UUID().uuidString).tmp")
        do {
            try data.write(to: temporary, options: .withoutOverwriting)
            try fileManager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: temporary.path)
            guard isPrivateFile(temporary) else { throw QueueRescanStoreError.unsafeFile }
            try fileManager.moveItem(at: temporary, to: destination)
            guard isPrivateFile(destination) else { throw QueueRescanStoreError.unsafeFile }
            return destination
        } catch {
            try? fileManager.removeItem(at: temporary)
            throw error
        }
    }

    private func ensurePrivateDirectory(_ url: URL) throws {
        if fileManager.fileExists(atPath: url.path) {
            guard isPrivateDirectory(url) else { throw QueueRescanStoreError.unsafeDirectory }
            return
        }
        try fileManager.createDirectory(
            at: url,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
        try fileManager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: url.path)
        guard isPrivateDirectory(url) else { throw QueueRescanStoreError.unsafeDirectory }
    }

    private func isPrivateDirectory(_ url: URL) -> Bool {
        guard let details = status(url) else { return false }
        return (details.st_mode & S_IFMT) == S_IFDIR
            && (details.st_mode & 0o7777) == 0o700
            && details.st_uid == getuid()
    }

    private func isPrivateFile(_ url: URL) -> Bool {
        guard let details = status(url) else { return false }
        return (details.st_mode & S_IFMT) == S_IFREG
            && (details.st_mode & 0o7777) == 0o600
            && details.st_uid == getuid()
            && details.st_nlink == 1
    }

    private func status(_ url: URL) -> stat? {
        var details = stat()
        guard url.path.withCString({ Darwin.lstat($0, &details) }) == 0 else { return nil }
        return details
    }

    private func containsUnsafeSymlink(_ url: URL) -> Bool {
        var current = URL(fileURLWithPath: "/")
        for component in TrustedSystemPath.pathComponents(for: url).dropFirst() {
            current.appendPathComponent(component)
            guard let details = status(current) else { continue }
            if (details.st_mode & S_IFMT) == S_IFLNK, !isTrustedSystemSymlink(current) {
                return true
            }
        }
        return false
    }

    private func isTrustedSystemSymlink(_ url: URL) -> Bool {
        let allowed: [String: Set<String>] = [
            "/tmp": ["private/tmp", "/private/tmp"],
            "/var": ["private/var", "/private/var"],
        ]
        guard let destinations = allowed[url.path],
              let destination = try? fileManager.destinationOfSymbolicLink(atPath: url.path) else {
            return false
        }
        return destinations.contains(destination)
    }

    private static var encoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }
}
