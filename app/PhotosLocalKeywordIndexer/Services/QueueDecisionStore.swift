import Darwin
import Foundation

struct QueueReviewDecision: Codable, Equatable, Sendable {
    var sessionID: String
    var itemID: String
    var revision: Int
    var decisionID: String
    var approvedKeywords: [String]
    var approvedCaption: String?

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case itemID = "item_id"
        case revision
        case decisionID = "decision_id"
        case approvedKeywords = "approved_keywords"
        case approvedCaption = "approved_caption"
    }

    func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(sessionID, forKey: .sessionID)
        try values.encode(itemID, forKey: .itemID)
        try values.encode(revision, forKey: .revision)
        try values.encode(decisionID, forKey: .decisionID)
        try values.encode(approvedKeywords, forKey: .approvedKeywords)
        try values.encode(approvedCaption, forKey: .approvedCaption)
    }

    var isValid: Bool {
        Self.isCanonicalUUID(sessionID)
            && Self.isCanonicalUUID(itemID)
            && Self.isCanonicalUUID(decisionID)
            && (1 ... Int(Int32.max)).contains(revision)
            && Self.areValidKeywords(approvedKeywords)
            && Self.isValidCaption(approvedCaption)
    }

    private static func isCanonicalUUID(_ value: String) -> Bool {
        guard value.utf8.count == 36, let parsed = UUID(uuidString: value) else { return false }
        return parsed.uuidString.lowercased() == value
    }

    private static func areValidKeywords(_ values: [String]) -> Bool {
        guard values.count <= 8 else { return false }
        var canonicalValues = Set<String>()
        for value in values {
            guard isSafeText(value, maximumCharacters: 128),
                  value == value.trimmingCharacters(in: .whitespacesAndNewlines),
                  !value.isEmpty else { return false }
            guard canonicalValues.insert(value.folding(
                options: [.caseInsensitive, .diacriticInsensitive],
                locale: Locale(identifier: "es_MX")
            )).inserted else { return false }
        }
        return true
    }

    private static func isValidCaption(_ value: String?) -> Bool {
        guard let value else { return true }
        return isSafeText(value, maximumCharacters: 240)
            && value == value.trimmingCharacters(in: .whitespacesAndNewlines)
            && !value.isEmpty
    }

    private static func isSafeText(_ value: String, maximumCharacters: Int) -> Bool {
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

enum QueueDecisionStoreError: Error, Equatable {
    case invalidDecision
    case unsafePath
    case unsafeDirectory
    case unsafeFile
    case decisionConflict
}

struct QueueDecisionStore {
    let root: URL
    private let fileManager: FileManager

    init(root: URL, fileManager: FileManager = .default) {
        self.root = root.standardizedFileURL
        self.fileManager = fileManager
    }

    func write(_ decision: QueueReviewDecision) throws -> URL {
        guard decision.isValid, root.isFileURL, root.path.hasPrefix("/") else {
            throw QueueDecisionStoreError.invalidDecision
        }
        let sessionDirectory = root.appendingPathComponent(decision.sessionID, isDirectory: true)
        let decisionsDirectory = sessionDirectory.appendingPathComponent("decisions", isDirectory: true)
        let destination = decisionsDirectory.appendingPathComponent("\(decision.decisionID).json")
        guard !containsUnsafeSymlinkComponent(root),
              !containsUnsafeSymlinkComponent(sessionDirectory),
              !containsUnsafeSymlinkComponent(decisionsDirectory),
              !containsUnsafeSymlinkComponent(destination) else {
            throw QueueDecisionStoreError.unsafePath
        }

        try ensurePrivateDirectory(root)
        try ensurePrivateDirectory(sessionDirectory)
        try ensurePrivateDirectory(decisionsDirectory)

        let data = try Self.encoder.encode(decision)
        if fileManager.fileExists(atPath: destination.path) {
            guard isPrivateRegularFile(destination) else {
                throw QueueDecisionStoreError.unsafeFile
            }
            guard try Data(contentsOf: destination) == data else {
                throw QueueDecisionStoreError.decisionConflict
            }
            return destination
        }

        let temporary = decisionsDirectory.appendingPathComponent(
            ".decision-\(UUID().uuidString.lowercased()).tmp"
        )
        do {
            // The final move within this directory is the atomic publication step.
            try data.write(to: temporary, options: .withoutOverwriting)
            try fileManager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: temporary.path)
            guard isPrivateRegularFile(temporary) else {
                throw QueueDecisionStoreError.unsafeFile
            }
            try fileManager.moveItem(at: temporary, to: destination)
            guard isPrivateRegularFile(destination) else {
                throw QueueDecisionStoreError.unsafeFile
            }
            return destination
        } catch {
            try? fileManager.removeItem(at: temporary)
            throw error
        }
    }

    private func ensurePrivateDirectory(_ directory: URL) throws {
        if fileManager.fileExists(atPath: directory.path) {
            guard isPrivateDirectory(directory) else {
                throw QueueDecisionStoreError.unsafeDirectory
            }
            return
        }
        try fileManager.createDirectory(
            at: directory,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
        try fileManager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
        guard isPrivateDirectory(directory) else {
            throw QueueDecisionStoreError.unsafeDirectory
        }
    }

    private func isPrivateDirectory(_ url: URL) -> Bool {
        guard let status = lstat(url) else { return false }
        return (status.st_mode & S_IFMT) == S_IFDIR
            && (status.st_mode & 0o7777) == 0o700
            && status.st_uid == getuid()
    }

    private func isPrivateRegularFile(_ url: URL) -> Bool {
        guard let status = lstat(url) else { return false }
        return (status.st_mode & S_IFMT) == S_IFREG
            && (status.st_mode & 0o7777) == 0o600
            && status.st_uid == getuid()
            && status.st_nlink == 1
    }

    private func lstat(_ url: URL) -> stat? {
        var status = stat()
        guard url.path.withCString({ Darwin.lstat($0, &status) }) == 0 else { return nil }
        return status
    }

    private func containsUnsafeSymlinkComponent(_ url: URL) -> Bool {
        var current = URL(fileURLWithPath: "/")
        for component in TrustedSystemPath.pathComponents(for: url).dropFirst() {
            current.appendPathComponent(component)
            guard let status = lstat(current) else { continue }
            if (status.st_mode & S_IFMT) == S_IFLNK, !isTrustedSystemSymlink(current) {
                return true
            }
        }
        return false
    }

    private func isTrustedSystemSymlink(_ url: URL) -> Bool {
        let destinations: [String: Set<String>] = [
            "/tmp": ["private/tmp", "/private/tmp"],
            "/var": ["private/var", "/private/var"],
        ]
        guard let allowed = destinations[url.path],
              let destination = try? fileManager.destinationOfSymbolicLink(atPath: url.path) else {
            return false
        }
        return allowed.contains(destination)
    }

    private static var encoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }
}

struct QueueReviewDraftArtifact: Codable, Equatable, Sendable {
    var sessionID: String
    var itemID: String
    var revision: Int
    var keywords: [String]
    var caption: String?

    enum CodingKeys: String, CodingKey {
        case sessionID = "session_id"
        case itemID = "item_id"
        case revision
        case keywords
        case caption
    }

    func encode(to encoder: Encoder) throws {
        var values = encoder.container(keyedBy: CodingKeys.self)
        try values.encode(sessionID, forKey: .sessionID)
        try values.encode(itemID, forKey: .itemID)
        try values.encode(revision, forKey: .revision)
        try values.encode(keywords, forKey: .keywords)
        try values.encode(caption, forKey: .caption)
    }

    var isValid: Bool {
        Self.isCanonicalUUID(sessionID)
            && Self.isCanonicalUUID(itemID)
            && (1 ... Int(Int32.max)).contains(revision)
            && keywords.count <= 8
            && Set(keywords.map(Self.canonicalText)).count == keywords.count
            && keywords.allSatisfy { Self.isSafeText($0, maximumCharacters: 128) }
            && (caption == nil || Self.isSafeText(caption ?? "", maximumCharacters: 240))
    }

    private static func isCanonicalUUID(_ value: String) -> Bool {
        guard value.utf8.count == 36, let parsed = UUID(uuidString: value) else { return false }
        return parsed.uuidString.lowercased() == value
    }

    private static func canonicalText(_ value: String) -> String {
        value.folding(
            options: [.caseInsensitive, .diacriticInsensitive],
            locale: Locale(identifier: "es_MX")
        )
    }

    private static func isSafeText(_ value: String, maximumCharacters: Int) -> Bool {
        guard !value.isEmpty,
              value == value.trimmingCharacters(in: .whitespacesAndNewlines),
              value.count <= maximumCharacters else { return false }
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

enum QueueDraftStoreError: Error, Equatable {
    case invalidDraft
    case unsafePath
    case unsafeDirectory
    case unsafeFile
}

struct QueueDraftStore {
    let root: URL
    private let fileManager: FileManager

    init(root: URL, fileManager: FileManager = .default) {
        self.root = root.standardizedFileURL
        self.fileManager = fileManager
    }

    func write(_ draft: QueueReviewDraftArtifact) throws -> URL {
        guard draft.isValid, root.isFileURL, root.path.hasPrefix("/") else {
            throw QueueDraftStoreError.invalidDraft
        }
        let session = root.appendingPathComponent(draft.sessionID, isDirectory: true)
        let directory = session.appendingPathComponent("drafts", isDirectory: true)
        let destination = directory.appendingPathComponent("\(draft.itemID).json")
        guard !containsUnsafeSymlink(root),
              !containsUnsafeSymlink(session),
              !containsUnsafeSymlink(directory),
              !containsUnsafeSymlink(destination) else {
            throw QueueDraftStoreError.unsafePath
        }
        try ensurePrivateDirectory(root)
        try ensurePrivateDirectory(session)
        try ensurePrivateDirectory(directory)
        if fileManager.fileExists(atPath: destination.path), !isPrivateFile(destination) {
            throw QueueDraftStoreError.unsafeFile
        }
        let data = try Self.encoder.encode(draft)
        if fileManager.fileExists(atPath: destination.path),
           try Data(contentsOf: destination) == data {
            return destination
        }
        let temporary = directory.appendingPathComponent(".draft-\(UUID().uuidString).tmp")
        do {
            try data.write(to: temporary, options: .withoutOverwriting)
            try fileManager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: temporary.path)
            guard isPrivateFile(temporary),
                  temporary.path.withCString({ source in
                      destination.path.withCString { target in Darwin.rename(source, target) }
                  }) == 0,
                  isPrivateFile(destination) else {
                throw QueueDraftStoreError.unsafeFile
            }
            return destination
        } catch {
            try? fileManager.removeItem(at: temporary)
            throw error
        }
    }

    func load(
        sessionID: String,
        itemID: String,
        currentRevision: Int
    ) throws -> QueueReviewDraftArtifact? {
        let identity = QueueReviewDraftArtifact(
            sessionID: sessionID,
            itemID: itemID,
            revision: currentRevision,
            keywords: ["validación"],
            caption: nil
        )
        guard identity.isValid else { throw QueueDraftStoreError.invalidDraft }
        let path = root
            .appendingPathComponent(sessionID, isDirectory: true)
            .appendingPathComponent("drafts", isDirectory: true)
            .appendingPathComponent("\(itemID).json")
        guard !containsUnsafeSymlink(path) else { throw QueueDraftStoreError.unsafePath }
        guard fileManager.fileExists(atPath: path.path) else { return nil }
        guard isPrivateFile(path) else { throw QueueDraftStoreError.unsafeFile }
        let data = try Data(contentsOf: path)
        guard let object = try JSONSerialization.jsonObject(with: data) as? [String: Any],
              Set(object.keys) == Set(["session_id", "item_id", "revision", "keywords", "caption"]) else {
            throw QueueDraftStoreError.invalidDraft
        }
        let artifact = try JSONDecoder().decode(
            QueueReviewDraftArtifact.self,
            from: data
        )
        guard artifact.isValid,
              artifact.sessionID == sessionID,
              artifact.itemID == itemID else {
            throw QueueDraftStoreError.invalidDraft
        }
        // Drafts belong to one exact analysis attempt. A rescan advances the
        // item revision; preserving edits explicitly writes a new artifact
        // for that revision, while resetting edits intentionally leaves the
        // older draft in audit-only storage.
        guard artifact.revision == currentRevision else { return nil }
        return artifact
    }

    private func ensurePrivateDirectory(_ url: URL) throws {
        if fileManager.fileExists(atPath: url.path) {
            guard isPrivateDirectory(url) else { throw QueueDraftStoreError.unsafeDirectory }
            return
        }
        try fileManager.createDirectory(
            at: url,
            withIntermediateDirectories: false,
            attributes: [.posixPermissions: 0o700]
        )
        try fileManager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: url.path)
        guard isPrivateDirectory(url) else { throw QueueDraftStoreError.unsafeDirectory }
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
