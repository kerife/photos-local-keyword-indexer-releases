import Foundation

struct AppSettingsStore {
    let fileURL: URL
    private let fileManager: FileManager

    static var `default`: Self? {
        defaultFileURL().map { Self(fileURL: $0, fileManager: .default) }
    }

    static func defaultFileURL() -> URL? {
        guard let support = FileManager.default.urls(
            for: .applicationSupportDirectory,
            in: .userDomainMask
        ).first else { return nil }
        return support
            .appendingPathComponent("Photos Local Keyword Indexer", isDirectory: true)
            .appendingPathComponent("settings.json")
    }

    static func loadDefault() -> AppSettings? {
        `default`?.load()
    }

    init(fileURL: URL, fileManager: FileManager = .default) {
        self.fileURL = fileURL
        self.fileManager = fileManager
    }

    func load() -> AppSettings {
        guard (try? fileManager.destinationOfSymbolicLink(atPath: fileURL.path)) == nil else {
            return .defaults
        }
        guard let attributes = try? fileManager.attributesOfItem(atPath: fileURL.path),
              let permissions = attributes[.posixPermissions] as? NSNumber,
              permissions.intValue & 0o777 == 0o600 else {
            return .defaults
        }
        let directory = fileURL.deletingLastPathComponent()
        guard let directoryAttributes = try? fileManager.attributesOfItem(atPath: directory.path),
              let directoryPermissions = directoryAttributes[.posixPermissions] as? NSNumber,
              directoryPermissions.intValue & 0o777 == 0o700 else {
            return .defaults
        }
        guard let data = try? Data(contentsOf: fileURL),
              let settings = try? JSONDecoder().decode(AppSettings.self, from: data) else {
            return .defaults
        }
        return settings
    }

    func save(_ settings: AppSettings) throws {
        guard settings.isValid else { throw SettingsError.invalidSettings }
        let directory = fileURL.deletingLastPathComponent()
        guard !containsSymlinkComponent(fileURL), !containsSymlinkComponent(directory) else {
            throw SettingsError.unsafePath
        }
        try fileManager.createDirectory(
            at: directory,
            withIntermediateDirectories: true,
            attributes: [.posixPermissions: 0o700]
        )
        try fileManager.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)

        let data = try JSONEncoder.sorted.encode(settings)
        let temporaryURL = directory.appendingPathComponent(".settings-\(UUID().uuidString).tmp")
        do {
            try data.write(to: temporaryURL, options: .atomic)
            try fileManager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: temporaryURL.path)
            if fileManager.fileExists(atPath: fileURL.path) {
                _ = try fileManager.replaceItemAt(
                    fileURL,
                    withItemAt: temporaryURL,
                    backupItemName: nil,
                    options: []
                )
            } else {
                try fileManager.moveItem(at: temporaryURL, to: fileURL)
            }
            try fileManager.setAttributes([.posixPermissions: 0o600], ofItemAtPath: fileURL.path)
        } catch {
            try? fileManager.removeItem(at: temporaryURL)
            throw error
        }
    }

    private func containsSymlinkComponent(_ url: URL) -> Bool {
        var current = URL(fileURLWithPath: "/")
        for component in url.pathComponents.dropFirst() {
            current.appendPathComponent(component)
            if (try? fileManager.destinationOfSymbolicLink(atPath: current.path)) != nil
                && !isTrustedSystemSymlink(current)
            {
                return true
            }
        }
        return false
    }

    private func isTrustedSystemSymlink(_ url: URL) -> Bool {
        guard url.path == "/var" else { return false }
        return (try? fileManager.destinationOfSymbolicLink(atPath: url.path)) == "private/var"
            || (try? fileManager.destinationOfSymbolicLink(atPath: url.path)) == "/private/var"
    }
}

private extension JSONEncoder {
    static var sorted: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return encoder
    }
}
