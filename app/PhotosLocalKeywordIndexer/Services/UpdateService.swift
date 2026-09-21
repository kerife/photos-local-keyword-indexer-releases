import Combine
import Foundation

#if canImport(Sparkle)
import Sparkle
#endif

@MainActor
final class UpdateService: ObservableObject {
    private static let automaticChecksKey = "updates.automaticChecksEnabled"
    private static let unsafeFeedCharacters = CharacterSet(charactersIn: ";\"'\\")
    @Published private(set) var automaticChecksEnabled: Bool
#if canImport(Sparkle)
    private let controller: SPUStandardUpdaterController?

    init() {
        automaticChecksEnabled = Self.loadAutomaticChecksPreference()
        guard Self.permitsUpdates(buildChannel: Bundle.main.object(
            forInfoDictionaryKey: "PhotosLocalKeywordIndexerBuildChannel"
        ) as? String) else {
            automaticChecksEnabled = false
            controller = nil
            return
        }
        let feed = Bundle.main.object(forInfoDictionaryKey: "SUFeedURL") as? String
        let publicKey = Bundle.main.object(forInfoDictionaryKey: "SUPublicEDKey") as? String
        guard let feed,
              let feedURL = URL(string: feed),
              feedURL.scheme?.lowercased() == "https",
              let host = feedURL.host,
              !host.isEmpty,
              feedURL.user == nil,
              feedURL.password == nil,
              !feed.contains("@"),
              !feed.contains(where: \.isWhitespace),
              feed.rangeOfCharacter(from: Self.unsafeFeedCharacters) == nil,
              !Self.isPlaceholderHost(host),
              let publicKey,
              Self.isValidPublicEdKey(publicKey) else {
            controller = nil
            return
        }
        controller = SPUStandardUpdaterController(
            startingUpdater: false,
            updaterDelegate: nil,
            userDriverDelegate: nil
        )
        controller?.updater.automaticallyChecksForUpdates = automaticChecksEnabled
        controller?.startUpdater()
    }

    private static func isPlaceholderHost(_ host: String?) -> Bool {
        guard let host = host?.lowercased().trimmingCharacters(in: CharacterSet(charactersIn: ".")) else {
            return false
        }
        let placeholderDomains = ["example.invalid", "example.org", "example.com", "example.net"]
        return placeholderDomains.contains { host == $0 || host.hasSuffix(".\($0)") }
    }

    var isConfigured: Bool { controller != nil }

    func checkForUpdates() {
        controller?.checkForUpdates(nil)
    }

    func setAutomaticChecksEnabled(_ enabled: Bool) {
        guard isConfigured else { return }
        automaticChecksEnabled = enabled
        UserDefaults.standard.set(enabled, forKey: Self.automaticChecksKey)
        controller?.updater.automaticallyChecksForUpdates = enabled
    }
#else
    init() {
        automaticChecksEnabled = Self.loadAutomaticChecksPreference()
    }
    var isConfigured: Bool { false }
    func checkForUpdates() {}
    func setAutomaticChecksEnabled(_ enabled: Bool) {
        automaticChecksEnabled = enabled
        UserDefaults.standard.set(enabled, forKey: Self.automaticChecksKey)
    }
#endif

    nonisolated static func permitsUpdates(buildChannel: String?) -> Bool {
        buildChannel == "release"
    }

    nonisolated static func isValidPublicEdKey(_ value: String) -> Bool {
        guard let decoded = Data(base64Encoded: value, options: []),
              decoded.count == 32 else {
            return false
        }
        return decoded.base64EncodedString() == value
    }

    private static func loadAutomaticChecksPreference() -> Bool {
        guard let stored = UserDefaults.standard.object(forKey: automaticChecksKey) as? Bool else {
            return true
        }
        return stored
    }
}
