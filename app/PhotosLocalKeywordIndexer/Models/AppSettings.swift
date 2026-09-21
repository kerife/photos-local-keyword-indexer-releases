import Foundation

/// The only values persisted by the app. This intentionally excludes paths,
/// photo metadata, review state, and worker output.
struct AppSettings: Codable, Equatable, Sendable {
    static let currentVersion = 2

    static let defaults = Self(
        limit: 10,
        modelPolicy: "adaptive",
        singleModel: "qwen3-vl:4b",
        fastModel: "qwen3-vl:4b",
        detailedModel: "qwen3-vl:4b",
        appleMaps: false,
        includeCaption: true,
        randomSelection: true,
        autoAnalyze: true,
        analysisConcurrency: 2
    )

    var limit: Int
    var modelPolicy: String
    var singleModel: String
    var fastModel: String
    var detailedModel: String
    var appleMaps: Bool
    var includeCaption: Bool
    var randomSelection: Bool
    var autoAnalyze: Bool
    var analysisConcurrency: Int
    var version: Int

    init(
        limit: Int,
        modelPolicy: String,
        singleModel: String,
        fastModel: String,
        detailedModel: String,
        appleMaps: Bool,
        includeCaption: Bool,
        randomSelection: Bool,
        autoAnalyze: Bool = true,
        analysisConcurrency: Int = 2,
        version: Int = Self.currentVersion
    ) {
        self.limit = limit
        self.modelPolicy = modelPolicy
        self.singleModel = singleModel
        self.fastModel = fastModel
        self.detailedModel = detailedModel
        self.appleMaps = appleMaps
        self.includeCaption = includeCaption
        self.randomSelection = randomSelection
        self.autoAnalyze = autoAnalyze
        self.analysisConcurrency = analysisConcurrency
        self.version = version
    }

    init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        let version = try values.decode(Int.self, forKey: .version)
        guard version == 1 || version == Self.currentVersion else { throw SettingsError.invalidSettings }
        let loaded = Self(
            limit: version == 1 ? Self.defaults.limit : try values.decode(Int.self, forKey: .limit),
            modelPolicy: try values.decode(String.self, forKey: .modelPolicy),
            singleModel: try values.decode(String.self, forKey: .singleModel),
            fastModel: try values.decode(String.self, forKey: .fastModel),
            detailedModel: try values.decode(String.self, forKey: .detailedModel),
            appleMaps: try values.decode(Bool.self, forKey: .appleMaps),
            includeCaption: version == 1
                ? Self.defaults.includeCaption
                : try values.decode(Bool.self, forKey: .includeCaption),
            randomSelection: version == 1
                ? Self.defaults.randomSelection
                : try values.decode(Bool.self, forKey: .randomSelection),
            autoAnalyze: version == 1
                ? true
                : try values.decode(Bool.self, forKey: .autoAnalyze),
            analysisConcurrency: version == 1
                ? 2
                : try values.decode(Int.self, forKey: .analysisConcurrency),
            version: Self.currentVersion
        )
        guard loaded.isValid else { throw SettingsError.invalidSettings }
        self = loaded
    }

    private enum CodingKeys: String, CodingKey {
        case limit, modelPolicy, singleModel, fastModel, detailedModel
        case appleMaps, includeCaption, randomSelection
        case autoAnalyze, analysisConcurrency
        case version
    }

    var isValid: Bool {
        (1...500).contains(limit)
            && (modelPolicy == "adaptive" || modelPolicy == "single")
            && version == Self.currentVersion
            && (1...4).contains(analysisConcurrency)
            && [singleModel, fastModel, detailedModel].allSatisfy(Self.isValidModel)
    }

    private static func isValidModel(_ model: String) -> Bool {
        OllamaModelPresentationPolicy.isValidModelName(model)
    }
}

enum SettingsError: Error {
    case invalidSettings
    case unsafePath
}
