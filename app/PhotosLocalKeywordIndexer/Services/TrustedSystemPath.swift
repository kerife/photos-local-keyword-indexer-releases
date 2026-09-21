import Foundation

enum TrustedSystemPath {
    static func canonicalPath(for url: URL) -> String {
        let path = url.standardizedFileURL.path
        if path == "/tmp" || path.hasPrefix("/tmp/") {
            return "/private" + path
        }
        if path == "/var" || path.hasPrefix("/var/") {
            return "/private" + path
        }
        return path
    }

    static func pathComponents(for url: URL) -> [String] {
        URL(fileURLWithPath: canonicalPath(for: url), isDirectory: url.hasDirectoryPath).pathComponents
    }

    static func isTrustedSystemSymlink(
        _ url: URL,
        fileManager: FileManager = .default
    ) -> Bool {
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
}
