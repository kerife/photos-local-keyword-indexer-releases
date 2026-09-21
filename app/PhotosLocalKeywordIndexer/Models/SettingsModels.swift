import Foundation

/// Explains which Settings values are evidence-only and which control is
/// intentionally editable. Keeping this copy centralized prevents the screen
/// from describing itself as entirely read-only while exposing the Sparkle
/// preference toggle.
enum SettingsScopeCopy {
    static let detail = "Rutas, modelos y privacidad son un resumen técnico de solo lectura. Las opciones de nuevas ejecuciones se conservan localmente; la preferencia de comprobación automática de actualizaciones puede cambiarse en esta pantalla cuando la build está configurada."
    static let accessibilityLabel = "Configuración: resumen técnico de solo lectura para rutas, modelos y privacidad; las opciones de nuevas ejecuciones se conservan localmente; preferencia editable para la comprobación automática de actualizaciones cuando está disponible."
}

enum SettingsUpdateCopy {
    static let localBuildTitle = "Versión local sin actualizaciones"
}

enum BuildChannel: Equatable, Sendable {
    case development
    case release
    case unknown

    static func from(infoValue: Any?) -> Self {
        guard let value = infoValue as? String else { return .unknown }
        switch value.trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "development": return .development
        case "release": return .release
        default: return .unknown
        }
    }

    var label: String {
        switch self {
        case .development: return "Build de desarrollo"
        case .release: return "Build release"
        case .unknown: return "Canal de build no verificado"
        }
    }

    var detail: String {
        switch self {
        case .development: return "Beta pública sin firma Developer ID ni notarización; comprueba el checksum y abre solo esta app mediante el flujo específico de Gatekeeper. El dry-run local sigue disponible."
        case .release: return "Esta build está marcada para distribución; la firma y notarización deben verificarse por separado."
        case .unknown: return "No se pudo verificar el canal de distribución; el dry-run local no depende de esta comprobación."
        }
    }

    var symbolName: String {
        switch self {
        case .development: return "hammer"
        case .release: return "checkmark.seal"
        case .unknown: return "questionmark.diamond"
        }
    }

    var accessibilityLabel: String { "\(label). \(detail)" }
}

enum BuildChannelCopy {
    static let infoPlistKey = "PhotosLocalKeywordIndexerBuildChannel"

    static func current(bundle: Bundle = .main) -> BuildChannel {
        BuildChannel.from(infoValue: bundle.object(forInfoDictionaryKey: infoPlistKey))
    }
}

enum SmokeTestSafetyCopy {
    static let sectionTitle = "Prueba segura antes de aplicar"
    static let detail = "La app no ejecuta smoke tests automáticamente ni valida tu biblioteca personal. Para probar escritura y rollback, usa una biblioteca de Fotos de prueba y un respaldo reciente."
    static let accessibilityLabel = "No se ha validado automáticamente una biblioteca real. Usa una biblioteca de Fotos de prueba antes de aplicar cambios."
}

/// Read-only paths displayed by Configuración. The UI intentionally does not
/// expose an action that opens or changes any of these locations.
struct SettingsPaths: Equatable, Sendable {
    let applicationSupport: String
    let runs: String
    let helper: String

    @MainActor
    static func current(
        fileManager: FileManager = .default,
        bundle: Bundle = .main
    ) -> Self {
        let support = fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
            .appendingPathComponent("Photos Local Keyword Indexer", isDirectory: true)
        let supportPath = support?.path ?? "No disponible"
        return Self(
            applicationSupport: supportPath,
            runs: support?.appendingPathComponent("runs", isDirectory: true).path ?? "No disponible",
            helper: WorkerProcess.defaultExecutableURL(bundle: bundle).path
        )
    }
}

/// Keeps local filesystem paths readable without putting the account name or
/// an unbounded path in the settings layout. The full value remains available
/// to the explicit copy action in SettingsView.
enum SettingsPathCopy {
    private static let maximumLength = 96

    static func display(_ value: String, homePath: String = NSHomeDirectory()) -> String {
        let normalized = value
            .split(whereSeparator: \.isWhitespace)
            .joined(separator: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty else { return "No disponible" }

        let home = homePath.trimmingCharacters(in: .whitespacesAndNewlines)
        var compact = normalized
        if !home.isEmpty, compact == home {
            compact = "~"
        } else if !home.isEmpty, compact.hasPrefix(home + "/") {
            compact = "~" + compact.dropFirst(home.count)
        }
        guard compact.count > maximumLength else { return compact }
        return "…/" + compact.suffix(maximumLength - 2).drop(while: { $0 == "/" })
    }
}

enum SettingsUpdateStatus: Equatable, Sendable {
    case configured
    case unconfigured

    var label: String {
        switch self {
        case .configured:
            return "Actualizaciones configuradas"
        case .unconfigured:
            return "Actualizaciones no configuradas en esta build"
        }
    }

    var detail: String {
        switch self {
        case .configured:
            return "Sparkle puede comprobar actualizaciones desde el menú de la aplicación."
        case .unconfigured:
            return "Esta build no tiene un feed firmado disponible; no se realizará ninguna descarga automática."
        }
    }

    var canCheck: Bool {
        self == .configured
    }

    var automaticChecksLabel: String {
        canCheck ? "Disponible" : "No disponible en esta build"
    }

    var manualCheckDetail: String {
        switch self {
        case .configured:
            return "Puedes buscar actualizaciones manualmente aunque desactives la comprobación automática."
        case .unconfigured:
            return "Esta build no puede comprobar actualizaciones: falta un feed HTTPS firmado. Usa una build release configurada."
        }
    }

    /// Update checks are the only optional network activity in the native
    /// shell. Keep their narrow scope explicit beside the opt-in control so
    /// it cannot be confused with the local photo-analysis workflow.
    var privacyDetail: String {
        switch self {
        case .configured:
            return "Las comprobaciones consultan solo el feed HTTPS de actualizaciones; no consultan Fotos, no descargan modelos ni modifican keywords ni captions."
        case .unconfigured:
            return "Esta build no consulta ningún feed de actualizaciones; el análisis de Fotos sigue siendo local."
        }
    }

    var symbolName: String {
        self == .configured ? "checkmark.seal.fill" : "exclamationmark.triangle.fill"
    }

    var accessibilityLabel: String {
        "\(label). \(detail)"
    }
}

struct SettingsPrivacySnapshot: Equatable, Sendable {
    let appleMapsEnabled: Bool
    let includeCaptionEnabled: Bool

    var appleMapsLabel: String {
        appleMapsEnabled
            ? "Opt-in por ejecución; si se activa, Apple Maps recibe coordenadas."
            : "Desactivado por defecto; se decide por ejecución."
    }

    var captionLabel: String {
        includeCaptionEnabled
            ? "Activado para proponer captions breves."
            : "Desactivado; no se proponen captions."
    }
}

struct SettingsSnapshot: Equatable, Sendable {
    let buildChannel: BuildChannel
    let paths: SettingsPaths
    let modelPolicy: String
    let singleModel: String
    let fastModel: String
    let detailedModel: String
    let privacy: SettingsPrivacySnapshot
    let updateStatus: SettingsUpdateStatus
    let automaticChecksEnabled: Bool

    init(
        paths: SettingsPaths,
        modelPolicy: String,
        singleModel: String,
        fastModel: String,
        detailedModel: String,
        appleMapsEnabled: Bool,
        includeCaptionEnabled: Bool,
        updateStatus: SettingsUpdateStatus,
        automaticChecksEnabled: Bool = true,
        buildChannel: BuildChannel = .unknown
    ) {
        self.buildChannel = buildChannel
        self.paths = paths
        self.modelPolicy = modelPolicy
        self.singleModel = singleModel
        self.fastModel = fastModel
        self.detailedModel = detailedModel
        privacy = SettingsPrivacySnapshot(
            appleMapsEnabled: appleMapsEnabled,
            includeCaptionEnabled: includeCaptionEnabled
        )
        self.updateStatus = updateStatus
        self.automaticChecksEnabled = automaticChecksEnabled
    }

    var modelPolicyLabel: String {
        switch modelPolicy {
        case "single": return "Un solo modelo"
        case "adaptive": return "Adaptativa"
        default: return "Política no válida"
        }
    }

    var modelPolicyDetail: String {
        switch modelPolicy {
        case "single": return "Un solo modelo local; se usará en todas las fotos."
        case "adaptive":
            let fast = fastModel.trimmingCharacters(in: .whitespacesAndNewlines)
            let detailed = detailedModel.trimmingCharacters(in: .whitespacesAndNewlines)
            if !fast.isEmpty && fast == detailed {
                return "Adaptativa con un modelo local; se usará en todas las fotos."
            }
            return "Modelos locales adaptativos; el detallado se reserva para fotos con ubicación."
        default: return "La política configurada no es válida; selecciona una política compatible en Revisión."
        }
    }

    var modelDefaults: [String] {
        [fastModel, detailedModel].reduce(into: []) { values, model in
            guard !model.isEmpty, !values.contains(model) else { return }
            values.append(model)
        }
    }

    /// Only values that participate in the selected policy. Keeping this
    /// separate from `modelDefaults` avoids showing an unused adaptive model
    /// when the user selected a single-model run.
    var activeModelDefaults: [String] {
        switch modelPolicy {
        case "single": return [singleModel]
        case "adaptive": return modelDefaults
        default: return []
        }
    }
}

/// Readiness state included in support reports. These labels are derived from
/// the native preparation model and never contain helper output, paths, or
/// photo data.
struct SettingsPreparationDiagnostic: Equatable, Sendable {
    let photos: String
    let automation: String
    let ollama: String
    let models: String
    let helper: String

    init(state: PreparationState) {
        photos = state.photos.humanLabel
        automation = state.automation.humanLabel
        ollama = state.ollama.humanLabel
        models = state.models.humanLabel
        if state.errorCodes.contains("HELPER_UNAVAILABLE") {
            helper = "No disponible; reinstala una build válida antes de continuar."
        } else if state.errorCodes.contains("INVALID_HELPER_EVENT") || state.errorCodes.contains("UNSAFE_PREFLIGHT_RESULT") {
            helper = "Respuesta incompatible; reinstala una build válida antes de continuar."
        } else {
            helper = "No comprobado en esta etapa."
        }
    }
}

/// A support payload that is safe to paste into a report. It deliberately
/// contains operational settings only: no paths, UUIDs, coordinates, image
/// data, captions, or raw helper/model output.
struct SettingsSupportDiagnostic: Equatable, Sendable {
    let appVersion: String
    let operatingSystem: String
    let snapshot: SettingsSnapshot
    let preparation: SettingsPreparationDiagnostic

    init(
        snapshot: SettingsSnapshot,
        appVersion: String = "desconocida",
        operatingSystem: String = "desconocido",
        preparation: PreparationState = PreparationState()
    ) {
        self.snapshot = snapshot
        self.appVersion = Self.compact(appVersion, fallback: "desconocida")
        self.operatingSystem = Self.compact(operatingSystem, fallback: "desconocido")
        self.preparation = SettingsPreparationDiagnostic(state: preparation)
    }

    var report: String {
        let models = snapshot.activeModelDefaults
            .map { Self.compact($0, fallback: "configuración no mostrada") }
            .joined(separator: ", ")
        return [
            "Photos Local Keyword Indexer",
            "Versión: \(appVersion)",
            "macOS: \(operatingSystem)",
            "Política de modelos: \(snapshot.modelPolicyLabel)",
            "Modelos configurados: \(models)",
            "Ollama: solo loopback local; sin servicios cloud.",
            "Apple Maps: \(snapshot.privacy.appleMapsEnabled ? "activado por ejecución" : "desactivado")",
            "Captions: \(snapshot.privacy.includeCaptionEnabled ? "activados" : "desactivados")",
            "Preparación — Fotos: \(preparation.photos); Automatización: \(preparation.automation); Ollama: \(preparation.ollama); Modelos: \(preparation.models)",
            "Helper local: \(preparation.helper)",
            "Actualizaciones: \(snapshot.updateStatus.label)",
            "Privacidad: análisis local; sin rutas ni datos de fotos en este diagnóstico.",
        ].joined(separator: "\n")
    }

    var accessibilityLabel: String {
        "Diagnóstico sanitizado listo para copiar. No incluye rutas ni datos de fotos."
    }

    private static func compact(_ value: String, fallback: String) -> String {
        let normalized = value.split(whereSeparator: \.isWhitespace).joined(separator: " ")
        guard !normalized.isEmpty,
              !normalized.contains("/"),
              !normalized.contains("\\"),
              !normalized.contains(".."),
              !normalized.hasPrefix("~"),
              !normalized.contains(where: { $0.isASCII && $0.isNewline }) else {
            return fallback
        }
        return String(normalized.prefix(120))
    }
}
