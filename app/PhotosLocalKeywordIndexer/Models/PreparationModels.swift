import Foundation

enum HumanErrorCopy {
    static func message(for code: String) -> String {
        switch code {
        case "OLLAMA_MODEL_MISSING": return "Falta un modelo local; instala el modelo indicado."
        case "OLLAMA_VERSION_OLD": return "Ollama necesita una versión más reciente."
        case "OLLAMA_UNAVAILABLE": return "Ollama no está disponible en este Mac."
        case "OLLAMA_NO_VISION": return "El modelo local no tiene capacidad de visión."
        case "OLLAMA_RESPONSE_INVALID": return "Ollama respondió sin el formato esperado para el análisis."
        case "OLLAMA_REQUEST_FAILED": return "Ollama rechazó o no completó la solicitud de análisis."
        case "VISION_IMAGE_INVALID": return "No se pudo preparar una copia compatible de la foto para el modelo local."
        case "OLLAMA_PREFLIGHT_FAILED": return "No se pudo comprobar Ollama o los modelos locales; corrige el servicio local y vuelve a intentarlo."
        case "PLATFORM_UNSUPPORTED": return "Esta app necesita macOS 14 o posterior; ejecuta el análisis en un sistema compatible."
        case "LIMIT_INVALID": return "El límite de fotos no es válido; elige un valor entre 1 y 500."
        case "SCAN_OPTIONS_INVALID": return "La configuración de la ejecución no es válida; revisa las opciones y ejecuta un dry-run nuevo."
        case "MODEL_INVALID": return "El modelo local no es válido; comprueba el nombre instalado y vuelve a intentarlo."
        case "SCAN_SETUP_FAILED": return "No se pudo preparar el análisis local; revisa permisos y vuelve a ejecutar un dry-run."
        case "MANIFEST_WRITE_FAILED": return "No se pudo guardar el resultado local; comprueba el espacio y permisos de la carpeta de ejecuciones."
        case "MANIFEST_INVALID": return "El manifiesto no es válido o no coincide con el esquema; importa un dry-run nuevo."
        case "LOCK_OR_MANIFEST_FAILED": return "No se pudo bloquear o leer el run de forma segura; cierra otra ejecución y vuelve a intentarlo."
        case "MANIFEST_PATH_INVALID": return "La ubicación del manifiesto no es segura; selecciona un manifest.json local válido."
        case "MODEL_POLICY_UNSUPPORTED": return "La política de modelos no está disponible en esta versión; usa una configuración compatible."
        case "RUNS_ROOT_INVALID": return "La carpeta local de ejecuciones no es segura; usa la ubicación predeterminada."
        case "WORKSPACE_FAILED": return "No se pudo preparar el espacio local; revisa permisos y vuelve a intentarlo."
        case "IDENTITY_MISMATCH": return "La foto cambió durante el análisis; ejecuta un dry-run nuevo antes de aplicar."
        case "APPLIED_KEYWORD_MISSING": return "La keyword no apareció en la verificación; revisa Fotos manualmente."
        case "CASING_CONFLICT": return "Hay una variante de mayúsculas; no se eliminó automáticamente."
        case "EXPORT_DELETE_FAILED": return "Quedó un temporal pendiente de limpieza; vuelve a intentarlo cuando termine la ejecución."
        case "PHOTOS_ACCESS_DENIED": return PhotosTCCGuide.packagedApplication.errorMessage
        case "PHOTOS_AUTOMATION_DENIED": return AutomationTCCGuide.packagedHelper.errorMessage
        case "PHOTOS_ACCESS_LIMITED": return "El acceso a Fotos es limitado; solo se analizarán las fotos visibles."
        case "FEWER_PHOTOS_AVAILABLE": return "Hay menos fotos elegibles de las solicitadas; revisa únicamente las fotos disponibles."
        case "CANCELLED": return "La comprobación local se canceló; puedes reintentarlo."
        case "EXPORT_FAILED": return "PhotoScript no pudo exportar una copia temporal; comprueba el acceso de Fotos y el almacenamiento local, y ejecuta un dry-run nuevo."
        case "ANALYSIS_FAILED": return "El análisis local terminó antes de producir una propuesta válida; esta versión no registró una causa más específica."
        case "LOW_CONFIDENCE": return "La confianza fue baja; revisa las propuestas antes de aplicar."
        case "SCAN_NOT_READY": return "El análisis aún no está listo; ejecuta un dry-run nuevo antes de continuar."
        case "REVIEW_PROVENANCE_INVALID": return "El manifiesto revisado no conserva una fuente local verificable; ejecuta un dry-run nuevo antes de aplicar."
        case "MUTATION_EVIDENCE_INVALID": return "No se pudieron verificar los cambios registrados; revisa Fotos manualmente y ejecuta un dry-run nuevo."
        case "REVIEW_NOT_PRISTINE": return "El manifiesto revisado ya fue modificado; ejecuta un dry-run nuevo antes de aplicar."
        case "ROLLBACK_ALREADY_STARTED": return "La reversión ya comenzó; revisa Fotos manualmente antes de intentarlo de nuevo."
        case "READ_FAILED": return "PhotoScript no pudo leer una foto; comprueba el acceso de Fotos y ejecuta un dry-run nuevo."
        case "APPLY_FAILED": return "PhotoScript no pudo verificar la escritura; revisa Fotos manualmente y no reintentes automáticamente."
        case "APPLY_READ_FAILED": return "No se pudo revalidar la foto antes de escribir; revisa Fotos manualmente y ejecuta un análisis nuevo si cambió."
        case "WRITE_UNCERTAIN", "INTERRUPTED_WRITE": return "La escritura no pudo verificarse; revisa Fotos manualmente."
        case "KEYWORD_WRITE_UNCERTAIN": return "Las keywords no pudieron verificarse; revisa Fotos manualmente."
        case "CAPTION_WRITE_UNCERTAIN": return "El caption no pudo verificarse; revisa Fotos manualmente."
        case "ROLLBACK_FAILED": return "PhotoScript no pudo verificar la reversión; revisa Fotos manualmente y no reintentes automáticamente."
        case "REMOVAL_UNCERTAIN", "INTERRUPTED_REMOVAL": return "La reversión no pudo verificarse; revisa Fotos manualmente."
        case "MANIFEST_NOT_REVIEWED": return "Revisa las propuestas antes de aplicar cambios."
        case "INVALID_HELPER_EVENT": return "El helper devolvió un evento no válido."
        case "UNSAFE_PREFLIGHT_RESULT", "UNSAFE_WORKFLOW_RESULT": return "El helper local devolvió una respuesta incompatible; reinstala la app o usa una build válida y vuelve a comprobar."
        case "UNSAFE_REVIEW_RESULT": return "La revisión local no pudo validarse; crea una revisión nueva desde el dry-run."
        case "PHOTOSCRIPT_UNAVAILABLE": return "PhotoScript no pudo cargar su puente AppleScript; comprueba la compatibilidad de Photos, PhotoScript y macOS, y ejecuta un dry-run nuevo."
        case "HELPER_UNAVAILABLE": return "No se encontró el helper local firmado; reinstala la app o usa una build válida y vuelve a comprobar."
        case "WORKER_OPERATION_FAILED": return "El helper local no pudo completar la operación; vuelve a comprobar la preparación y ejecuta un dry-run nuevo."
        default: return "La ejecución necesita revisión manual."
        }
    }
}

enum HumanNextActionCopy {
    static func message(for action: String?) -> String? {
        switch action {
        case nil, "none": return nil
        case "run_preflight": return "Comprobar la preparación local"
        case "wait_for_preflight": return "Esperar la comprobación local"
        case "grant_photos_access": return "Conceder acceso a Fotos"
        case "grant_photos_automation": return "Permitir controlar Fotos"
        case "install_missing_model": return "Instalar el modelo indicado"
        case "retry_preflight": return "Reintentar la comprobación local"
        case "fix_preflight": return "Corregir la preparación local"
        case "review_then_apply": return "Revisar propuestas antes de aplicar"
        case "rollback_available": return "Puedes revertir solo los cambios verificados"
        case "retry_failed_operation": return "Reintentar solo las operaciones fallidas"
        case "manual_review": return "Revisar manualmente el estado de la foto"
        case "fix_failed_scan": return "Corregir el análisis y ejecutar un nuevo dry-run"
        case "rescan_after_permissions": return "Comprobar permisos y ejecutar un nuevo dry-run"
        case "rescan_after_provenance": return "Ejecutar un nuevo dry-run con su fuente local"
        case "rescan_after_mutation_evidence": return "Revisar Fotos y ejecutar un nuevo dry-run"
        case "fix_fatal_error": return "Corregir el error local antes de continuar"
        default: return "Revisar la preparación local"
        }
    }
}

enum AppRoute: String, Hashable, CaseIterable {
    case setup
    case scan
    case preview
    case history
    case settings

    var accessibilityLabel: String {
        switch self {
        case .setup: return "Preparación"
        case .scan: return "Revisión"
        case .preview: return "Revisión"
        case .history: return "Historial"
        case .settings: return "Configuración"
        }
    }

    var navigationTitle: String {
        switch self {
        case .setup: return "Preparación local"
        case .scan: return "Revisión"
        case .preview: return "Revisión"
        case .history: return "Historial"
        case .settings: return "Configuración"
        }
    }
}

/// Settings that affect a worker request must not change while that request
/// is running. Keeping this decision in a small value-level policy makes the
/// UI contract testable without launching SwiftUI or PhotoScript.
enum ScanControlState {
    static func optionsAreEditable(workerIsRunning: Bool) -> Bool {
        !workerIsRunning
    }

    static func cancellationButtonTitle(isCancellationRequested: Bool) -> String {
        isCancellationRequested ? "Cancelación solicitada…" : "Cancelar"
    }

    static func canRequestCancellation(isCancellationRequested: Bool) -> Bool {
        !isCancellationRequested
    }

    static func cancellationAccessibilityHint(isCancellationRequested: Bool) -> String {
        isCancellationRequested
            ? "La operación terminará de forma cooperativa; no se iniciarán fotos nuevas."
            : "Solicita una cancelación cooperativa; no se escribirán cambios en Fotos."
    }

    static var lockedMessage: String {
        "Opciones bloqueadas durante el análisis; se aplicarán a la siguiente ejecución."
    }
}

enum PreparationStaleCopy {
    static let text = "La configuración de modelos cambió desde la última comprobación; vuelve a comprobar Preparación antes de analizar."
    static let accessibilityLabel = "Preparación pendiente: la configuración de modelos cambió desde la última comprobación. Vuelve a comprobar Preparación antes de analizar."
}

/// Explains why the primary dry-run action is disabled without changing its
/// safety gate. The first blocking condition is the most useful next step for
/// both visual review and assistive technologies.
enum ScanActionCopy {
    static func statusMessage(base: String, preflightIsStale: Bool) -> String {
        preflightIsStale ? PreparationStaleCopy.text : base
    }

    static func accessibilityHint(
        preparationReady: Bool,
        modelConfigurationValid: Bool,
        missingModelCount: Int,
        workerIsRunning: Bool,
        preflightIsStale: Bool = false
    ) -> String {
        if workerIsRunning {
            return "El análisis ya está en curso; espera o solicita su cancelación."
        }
        if !preparationReady {
            return "Completa Preparación antes de iniciar el dry-run."
        }
        if !modelConfigurationValid {
            return "Configura un modelo local válido antes de iniciar el dry-run."
        }
        if preflightIsStale {
            return "La configuración de modelos cambió; vuelve a comprobar la preparación local antes de iniciar el dry-run."
        }
        if missingModelCount > 0 {
            return "Vuelve a Preparación para comprobar los modelos antes de iniciar el dry-run."
        }
        return "Inicia un dry-run de solo lectura; no se modificará Apple Fotos."
    }
}

/// Keeps the post-scan safety message aligned with the actual worker outcome.
/// A failed or interrupted helper must never look like a normal completed run
/// that can proceed directly to Apply.
enum ScanTerminalOutcome: Equatable, Sendable {
    case completed
    case completedWithIssues
    case cancelled
    case failed
}

struct ScanOutcomeCopy: Equatable, Sendable {
    let text: String
    let symbolName: String
    let accessibilityLabel: String

    init(
        state: WorkerProcessState,
        terminalOutcome: ScanTerminalOutcome? = nil,
        processedCount: Int? = nil,
        proposedKeywordCount: Int? = nil,
        proposedCaptionCount: Int? = nil
    ) {
        if let terminalOutcome {
            switch terminalOutcome {
            case .completed:
                if processedCount == 0 {
                    text = "Dry-run completado; no hay fotos elegibles para revisar o aplicar. Conserva el resultado y revisa el alcance o el acceso a Fotos antes de ejecutar otro dry-run."
                } else if proposedKeywordCount == 0, let proposedCaptionCount, proposedCaptionCount > 0 {
                    text = "Dry-run completado; revisa los captions propuestos antes de aplicar."
                } else if let proposedKeywordCount, proposedKeywordCount > 0,
                          let proposedCaptionCount, proposedCaptionCount > 0 {
                    text = "Dry-run completado; revisa las keywords y captions propuestos antes de aplicar."
                } else if proposedKeywordCount == 0,
                          let proposedCaptionCount,
                          proposedCaptionCount == 0 {
                    text = "Dry-run completado; no hay keywords ni captions nuevas para revisar o aplicar."
                } else {
                    text = "El dry-run terminó; revisa las propuestas antes de aplicar."
                }
                symbolName = "checkmark.shield"
            case .completedWithIssues:
                text = "El dry-run terminó con errores o advertencias; abre Historial y revisa el run antes de aplicar."
                symbolName = "exclamationmark.triangle.fill"
            case .cancelled:
                text = "El dry-run se canceló; abre Historial y ejecuta un dry-run nuevo antes de aplicar."
                symbolName = "pause.circle.fill"
            case .failed:
                text = "El dry-run falló; abre Historial y ejecuta un dry-run nuevo. No hay una aplicación segura disponible."
                symbolName = "xmark.octagon.fill"
            }
        } else {
            switch state {
            case .interrupted:
                text = "El dry-run se interrumpió; abre Historial para revisar el run. No apliques cambios hasta completar una revisión manual."
                symbolName = "pause.circle.fill"
            case .failed:
                text = "El dry-run falló; abre Historial y ejecuta un dry-run nuevo. No hay una aplicación segura disponible."
                symbolName = "xmark.octagon.fill"
            case .running, .cancellationRequested:
                text = "El dry-run sigue activo; no se escribirán cambios en Fotos."
                symbolName = "lock.shield"
            case .stopped, .ready:
                text = "El dry-run terminó; revisa las propuestas antes de aplicar."
                symbolName = "checkmark.shield"
            }
        }
        accessibilityLabel = text
    }
}

/// Provides a direct, read-only route to the audit surface when the worker
/// stops abruptly or reports a non-clean terminal outcome. Completed clean
/// runs already navigate to Preview automatically.
enum ScanHistoryRecoveryCopy {
    static let buttonTitle = "Abrir Historial"
    static let accessibilityHint = "Abre el historial local en solo lectura para revisar el resultado del dry-run antes de continuar."

    static func shouldShow(
        for state: WorkerProcessState,
        terminalOutcome: ScanTerminalOutcome? = nil
    ) -> Bool {
        if state.shouldRefreshHistory {
            return true
        }
        guard let terminalOutcome else { return false }
        switch terminalOutcome {
        case .completed:
            return false
        case .completedWithIssues, .cancelled, .failed:
            return true
        }
    }
}

struct AppNavigationState: Equatable {
    private(set) var route: AppRoute = .setup

    static func shouldAutoOpenPreview(
        from currentRoute: AppRoute,
        runID: String,
        lastOpenedRunID: String?
    ) -> Bool {
        currentRoute == .scan && !runID.isEmpty && runID != lastOpenedRunID
    }

    func canNavigate(
        to destination: AppRoute,
        preparation: PreparationState,
        hasPreview: Bool = true,
        preflightIsStale: Bool = false
    ) -> Bool {
        switch destination {
        case .scan:
            return preparation.isReady && !preflightIsStale
        case .preview:
            // Revisión is now the continuous workspace and must remain
            // reachable so it can explain missing preparation in context.
            return true
        case .setup, .history, .settings:
            return true
        }
    }

    func accessibilityValue(
        for destination: AppRoute,
        preparation: PreparationState,
        hasPreview: Bool,
        preflightIsStale: Bool = false
    ) -> String {
        guard !canNavigate(
            to: destination,
            preparation: preparation,
            hasPreview: hasPreview,
            preflightIsStale: preflightIsStale
        ) else {
            return "Disponible"
        }
        switch destination {
        case .scan:
            return preflightIsStale
                ? "No disponible: la comprobación de modelos quedó obsoleta; vuelve a Preparación antes de iniciar un análisis."
                : "No disponible: completa Preparación antes de iniciar un análisis."
        case .preview:
            return "Disponible"
        case .setup, .history, .settings:
            return "Disponible"
        }
    }

    @discardableResult
    mutating func navigate(
        to destination: AppRoute,
        preparation: PreparationState,
        hasPreview: Bool = true,
        preflightIsStale: Bool = false
    ) -> Bool {
        guard canNavigate(
            to: destination,
            preparation: preparation,
            hasPreview: hasPreview,
            preflightIsStale: preflightIsStale
        ) else {
            route = .setup
            return false
        }
        route = destination
        return true
    }

    @discardableResult
    mutating func openHistoryReview(loadSucceeded: Bool) -> Bool {
        guard loadSucceeded else { return false }
        route = .preview
        return true
    }
}

enum PreparationCheckState: String, Equatable {
    case pending
    case checking
    case ready
    case actionRequired
    case failed

    var humanLabel: String {
        switch self {
        case .pending: return "Pendiente"
        case .checking: return "Comprobando"
        case .ready: return "Listo"
        case .actionRequired: return "Requiere acción"
        case .failed: return "No disponible"
        }
    }
}

struct PreparationState: Equatable {
    private(set) var photos: PreparationCheckState = .pending
    // Apple Events TCC has no passive public check on macOS. Keep this
    // visibly deferred instead of presenting a green state that was never
    // observed; the first PhotoScript operation remains the authoritative
    // permission check.
    private(set) var automation: PreparationCheckState = .pending
    private(set) var ollama: PreparationCheckState = .pending
    private(set) var models: PreparationCheckState = .pending
    private(set) var requestedModels: [String] = []
    private(set) var installedModels: [String] = []
    private(set) var warningCodes: [String] = []
    private(set) var errorCodes: [String] = []
    private(set) var nextAction = "run_preflight"
    private(set) var safeInstruction: String?

    var isReady: Bool {
        photos == .ready && ollama == .ready && models == .ready && !automationRequiresAction
    }

    private var automationRequiresAction: Bool {
        automation == .actionRequired || automation == .failed
    }

    /// Distinguish a real blocking condition from the neutral pending/checking
    /// states so the preparation screen never makes a failure look idle.
    var requiresAttention: Bool {
        [photos, automation, ollama, models].contains { state in
            state == .actionRequired || state == .failed
        }
    }

    var hasWarnings: Bool { !warningCodes.isEmpty }

    /// Human-facing copy for the next safe action. Keep internal workflow
    /// codes available to the IPC contract, but never make the person decode
    /// them in the preparation screen.
    var nextActionText: String {
        HumanNextActionCopy.message(for: nextAction) ?? "Iniciar una nueva ejecución"
    }

    /// Progress copy for the local preflight. It names only configured model
    /// identifiers and safe next steps; no helper paths or raw errors cross
    /// into the UI.
    var preflightProgressText: String {
        let requested = requestedModels.reduce(into: [String]()) { values, model in
            let normalized = model.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !normalized.isEmpty, !values.contains(normalized) else { return }
            values.append(normalized)
        }
        let missing = requested.filter { !installedModels.contains($0) }

        if ollama == .checking || models == .checking {
            let names = requested.isEmpty
                ? "los modelos configurados"
                : requested.map(ModelPresentationCopy.display).joined(separator: ", ")
            return "Comprobando Ollama y modelos locales: \(names)."
        }
        if ollama == .failed && errorCodes.contains("OLLAMA_UNAVAILABLE") {
            return "Ollama no está disponible en 127.0.0.1:11434. Inicia el servicio local y vuelve a comprobar."
        }
        if ollama == .failed && errorCodes.contains("OLLAMA_VERSION_OLD") {
            return "La versión local de Ollama no cumple el mínimo requerido. Actualízala y vuelve a comprobar."
        }
        if errorCodes.contains("PHOTOSCRIPT_UNAVAILABLE") {
            return "PhotoScript no está disponible; comprueba la compatibilidad local de Photos, PhotoScript y macOS antes de iniciar un dry-run."
        }
        if !missing.isEmpty && models == .actionRequired {
            let names = missing.map(ModelPresentationCopy.display).joined(separator: ", ")
            return "Falta instalar: \(names). Ejecuta el comando indicado y vuelve a comprobar."
        }
        if ollama == .ready && models == .ready && !warningCodes.isEmpty {
            return "Ollama y modelos locales listos con advertencias; revisa la preparación antes de continuar."
        }
        if ollama == .ready && models == .ready {
            return "Ollama y modelos locales listos para un dry-run."
        }
        return "La preparación local necesita atención antes de analizar fotos."
    }

    var preflightProgressAccessibilityLabel: String {
        "Progreso de preparación: \(preflightProgressText)"
    }

    var modelStatusText: String {
        let requested = Array(Set(requestedModels.filter { !$0.isEmpty })).sorted()
        let missing = requested.filter { !installedModels.contains($0) }
        if !missing.isEmpty {
            let names = missing.map(ModelPresentationCopy.display).joined(separator: ", ")
            return "Faltan modelos: \(names). Instálalos manualmente y vuelve a comprobar."
        }
        if !installedModels.isEmpty {
            let names = installedModels.map(ModelPresentationCopy.display).joined(separator: ", ")
            return "Instalados: \(names)."
        }
        return requested.isEmpty
            ? "Aún no se han comprobado modelos locales."
            : "No se encontraron los modelos configurados."
    }

    var hasPreflightProgress: Bool {
        !requestedModels.isEmpty
            && (ollama == .checking
                || models == .checking
                || ollama == .ready
                || models == .ready
                || !errorCodes.isEmpty
                || !warningCodes.isEmpty)
    }

    var statusMessage: String {
        if isReady {
            if !warningCodes.isEmpty {
                let warnings = warningCodes.map(HumanErrorCopy.message(for:)).joined(separator: " ")
                return "Preparación completa con advertencias. Ya puedes abrir Revisión; revisa estas advertencias antes de continuar. \(warnings) Automatización de Fotos se comprobará al iniciar PhotoScript."
            }
            return "Preparación completa. Ya puedes abrir Revisión. Automatización de Fotos se comprobará al iniciar PhotoScript."
        }
        var parts = ["Siguiente acción: \(nextActionText)."]
        if !errorCodes.isEmpty {
            parts.append(errorCodes.map(HumanErrorCopy.message(for:)).joined(separator: " "))
        }
        if !warningCodes.isEmpty {
            parts.append(warningCodes.map(HumanErrorCopy.message(for:)).joined(separator: " "))
        }
        if let safeInstruction, !safeInstruction.isEmpty { parts.append(safeInstruction) }
        return parts.joined(separator: " ")
    }

    mutating func updatePhotos(_ state: PhotosPermissionState) {
        switch state {
        case .authorized, .limited:
            photos = .ready
        case .notDetermined:
            photos = .pending
        case .denied, .restricted:
            photos = .actionRequired
        }
        if photos != .ready {
            nextAction = "grant_photos_access"
        } else if automationRequiresAction {
            nextAction = "grant_photos_automation"
        } else if ollama != .ready || models != .ready {
            nextAction = "run_preflight"
        } else if nextAction == "grant_photos_access" {
            nextAction = "none"
        }
    }

    /// Apply permission failures observed by PhotoKit/PhotoScript. TCC cannot
    /// be checked passively, so a denied Apple Event is the authoritative
    /// signal that the Automation card must become blocking.
    mutating func applyPermissionErrors(_ codes: [String]) {
        let permissionCodes = codes.filter {
            $0 == "PHOTOS_ACCESS_DENIED" || $0 == "PHOTOS_AUTOMATION_DENIED"
        }
        for code in permissionCodes where !errorCodes.contains(code) {
            errorCodes.append(code)
        }
        if codes.contains("PHOTOS_ACCESS_DENIED") {
            photos = .actionRequired
        }
        if codes.contains("PHOTOS_AUTOMATION_DENIED") {
            automation = .actionRequired
        }
        if photos != .ready && codes.contains("PHOTOS_ACCESS_DENIED") {
            nextAction = "grant_photos_access"
        } else if automationRequiresAction && codes.contains("PHOTOS_AUTOMATION_DENIED") {
            nextAction = "grant_photos_automation"
        }
    }

    /// Re-arm the deferred TCC check after the person returns from System
    /// Settings. macOS does not expose a passive Automation check; the next
    /// dry-run remains the authoritative PhotoScript verification.
    var canPrepareAutomationRetry: Bool {
        AutomationRecoveryPolicy.canRetry(
            from: automation,
            errorCodes: errorCodes
        )
    }

    mutating func prepareAutomationRetry() {
        guard canPrepareAutomationRetry else { return }
        automation = AutomationRecoveryPolicy.stateAfterCTA(from: automation)
        errorCodes.removeAll { $0 == "PHOTOS_AUTOMATION_DENIED" }
        if photos != .ready {
            nextAction = "grant_photos_access"
        } else if ollama != .ready || models != .ready {
            nextAction = "run_preflight"
        } else {
            nextAction = "none"
        }
    }

    mutating func beginPreflight(models requested: [String]) {
        requestedModels = requested
        installedModels = []
        ollama = .checking
        models = .checking
        warningCodes = []
        errorCodes = []
        nextAction = "wait_for_preflight"
        safeInstruction = nil
    }

    mutating func completePreflight(
        exitCode: Int,
        installedModels: [String],
        warningCodes: [String],
        errorCodes: [String],
        nextAction: String?,
        safeInstruction: String?
    ) {
        self.installedModels = installedModels.sorted()
        self.warningCodes = warningCodes
        self.errorCodes = errorCodes
        self.nextAction = errorCodes.contains("CANCELLED")
            ? "retry_preflight"
            : nextAction.flatMap { $0 == "none" ? nil : $0 }
                ?? (exitCode == 0 ? "none" : "fix_preflight")
        if photos != .ready && self.nextAction == "none" {
            self.nextAction = "grant_photos_access"
        } else if automationRequiresAction && self.nextAction == "none" {
            self.nextAction = "grant_photos_automation"
        }
        self.safeInstruction = Self.sanitizedInstruction(safeInstruction)

        applyPermissionErrors(errorCodes)

        if errorCodes.contains("PHOTOSCRIPT_UNAVAILABLE") {
            // The local Ollama/model checks can succeed before the separate
            // PhotoScript compatibility check fails. Keep that evidence
            // visible in its own card instead of turning healthy components
            // into misleading failures.
            automation = .failed
        }

        let hasEveryModel = Set(requestedModels).isSubset(of: Set(installedModels))
        let independentErrors = Set([
            "PHOTOSCRIPT_UNAVAILABLE",
            "PHOTOS_AUTOMATION_DENIED",
            "PHOTOS_ACCESS_DENIED"
        ])
        let ollamaHealthy = hasEveryModel
            && !errorCodes.isEmpty
            && errorCodes.allSatisfy { independentErrors.contains($0) }
        if (exitCode == 0 && hasEveryModel) || ollamaHealthy {
            ollama = .ready
            models = .ready
        } else {
            ollama = errorCodes.contains("OLLAMA_MODEL_MISSING") ? .ready : .failed
            models = hasEveryModel ? .failed : .actionRequired
        }
    }

    mutating func failPreflight(code: String) {
        ollama = .failed
        models = .failed
        warningCodes = []
        errorCodes = [code]
        if code == "PHOTOSCRIPT_UNAVAILABLE" {
            automation = .failed
            nextAction = "fix_fatal_error"
        } else {
            nextAction = "retry_preflight"
        }
        safeInstruction = nil
    }

    /// Keep a command from the local helper useful without allowing paths,
    /// shell operators or arbitrary text to cross into the native UI. The
    /// helper applies the same boundary; this second check keeps the app
    /// fail-closed if a future helper or test double changes its output.
    fileprivate static func sanitizedInstruction(_ instruction: String?) -> String? {
        guard let instruction,
              OllamaModelPresentationPolicy.isValidPullInstruction(instruction) else {
            return nil
        }
        return instruction
    }
}

/// The one high-signal action shown in Preparación. It deliberately contains
/// no file paths, raw helper errors or copy affordance; the only optional
/// instruction is the validated, local Ollama install command.
struct PreparationActionSummary: Equatable, Sendable {
    let action: String
    let title: String
    let detail: String
    let buttonTitle: String
    let buttonAccessibilityHint: String
    let symbolName: String
    let isBlocking: Bool
    let safeInstruction: String?

    init(state: PreparationState, preflightIsStale: Bool = false) {
        if preflightIsStale {
            action = "run_preflight"
            title = "La comprobación de modelos quedó obsoleta"
            detail = PreparationStaleCopy.text
            buttonTitle = "Comprobar de nuevo"
            buttonAccessibilityHint = "Vuelve a comprobar la configuración actual de modelos locales; no se abrirá ni modificará Fotos."
            symbolName = "arrow.triangle.2.circlepath"
            isBlocking = true
            safeInstruction = nil
            return
        }

        action = state.nextAction
        safeInstruction = PreparationState.sanitizedInstruction(state.safeInstruction)

        switch state.nextAction {
        case "grant_photos_access":
            title = "Concede acceso de lectura a Fotos"
            if state.photos == .actionRequired {
                detail = PhotosTCCGuide.packagedApplication.detail
                buttonTitle = "Abrir configuración de Fotos"
                buttonAccessibilityHint = PhotosAccessActionCopy.accessibilityHint(for: state.photos)
                symbolName = "photo.badge.exclamationmark"
            } else {
                detail = "La app solo leerá y exportará copias temporales; no cambiará originales ni otros metadatos."
                buttonTitle = "Solicitar acceso de Fotos"
                buttonAccessibilityHint = PhotosAccessActionCopy.accessibilityHint(for: state.photos)
                symbolName = "photo.badge.checkmark"
            }
            isBlocking = true
        case "grant_photos_automation":
            title = "Falta Automatización de Fotos"
            detail = AutomationTCCGuide.packagedHelper.detail
            buttonTitle = "Abrir configuración de Automatización"
            buttonAccessibilityHint = AutomationTCCGuide.packagedHelper.accessibilityHint
            symbolName = "gear.badge"
            isBlocking = true
        case "install_missing_model":
            title = "Instala el modelo local"
            detail = "Ollama responde, pero falta un modelo configurado. Instálalo manualmente y vuelve a comprobar; la app nunca descarga modelos."
            buttonTitle = "Volver a comprobar"
            buttonAccessibilityHint = "Vuelve a comprobar Ollama después de instalar el modelo local indicado."
            symbolName = "shippingbox"
            isBlocking = true
        case "wait_for_preflight":
            title = "Comprobando componentes locales"
            detail = "Espera a que termine la comprobación de Ollama y modelos; todavía no se analizarán fotos."
            buttonTitle = "Comprobando…"
            buttonAccessibilityHint = "La comprobación local sigue en curso."
            symbolName = "clock.arrow.circlepath"
            isBlocking = true
        case "retry_preflight", "fix_preflight", "fix_fatal_error":
            if state.errorCodes.contains("OLLAMA_MODEL_MISSING") {
                title = "Instala el modelo local"
                detail = "Ollama responde, pero falta un modelo configurado. Instálalo manualmente y vuelve a comprobar; la app nunca descarga modelos."
                symbolName = "shippingbox"
                buttonAccessibilityHint = "Vuelve a comprobar Ollama después de instalar el modelo local indicado."
            } else if state.errorCodes.contains("PHOTOSCRIPT_UNAVAILABLE") {
                title = "PhotoScript no está disponible"
                detail = "El puente AppleScript de PhotoScript no pudo cargarse. Comprueba la compatibilidad de Photos, PhotoScript y macOS; después vuelve a comprobar. No se usará otro puente ni se modificará Fotos."
                symbolName = "applescript"
                buttonAccessibilityHint = "Comprueba la compatibilidad local de Photos y PhotoScript antes de iniciar el dry-run."
            } else if state.errorCodes.contains("HELPER_UNAVAILABLE") {
                title = "Helper local no disponible"
                detail = "El helper firmado no pudo iniciarse. Reinstala la app o usa una build válida; después vuelve a comprobar. No se sustituirá por un intérprete externo."
                symbolName = "shippingbox"
                buttonAccessibilityHint = "Vuelve a comprobar el helper firmado; no se ejecutará un intérprete externo."
            } else if state.errorCodes.contains("INVALID_HELPER_EVENT") {
                title = "Protocolo local no compatible"
                detail = "La app recibió una respuesta incompatible del helper firmado. Reinstala la app o usa una build válida y vuelve a comprobar; no se usará un intérprete externo."
                symbolName = "arrow.triangle.2.circlepath"
                buttonAccessibilityHint = "Vuelve a comprobar el helper firmado sin abrir ni modificar Fotos."
            } else if state.errorCodes.contains("UNSAFE_PREFLIGHT_RESULT")
                        || state.errorCodes.contains("UNSAFE_WORKFLOW_RESULT") {
                title = "Helper local no compatible"
                detail = "El helper devolvió una respuesta incompatible. Reinstala la app o usa una build válida y vuelve a comprobar; no se usará un intérprete externo."
                symbolName = "arrow.triangle.2.circlepath"
                buttonAccessibilityHint = "Vuelve a comprobar el helper firmado con una build válida, sin abrir ni modificar Fotos."
            } else if state.errorCodes.contains("RUNS_ROOT_INVALID") {
                title = "Almacenamiento local no disponible"
                detail = "La carpeta local de ejecuciones no es segura. Usa la ubicación predeterminada y vuelve a comprobar; no se mostrarán ni modificarán rutas desde esta acción."
                symbolName = "folder.badge.questionmark"
                buttonAccessibilityHint = "Vuelve a comprobar el almacenamiento local sin abrir ni modificar Fotos."
            } else if state.errorCodes.contains("OLLAMA_UNAVAILABLE") {
                title = "Ollama no está disponible"
                detail = "Inicia el servicio local de Ollama en 127.0.0.1:11434 y vuelve a comprobar; la app no usará servicios cloud."
                symbolName = "network.slash"
                buttonAccessibilityHint = "Vuelve a comprobar Ollama en loopback sin abrir ni modificar Fotos."
            } else if state.errorCodes.contains("OLLAMA_VERSION_OLD") {
                title = "Actualiza Ollama"
                detail = "La versión local de Ollama no cumple el mínimo requerido. Actualízala manualmente y vuelve a comprobar antes del dry-run."
                symbolName = "arrow.up.circle"
                buttonAccessibilityHint = "Vuelve a comprobar la versión local de Ollama sin abrir ni modificar Fotos."
            } else if state.errorCodes.contains("OLLAMA_NO_VISION") {
                title = "El modelo no admite visión"
                detail = "El modelo configurado no tiene capacidad de visión. Selecciona un modelo local compatible y vuelve a comprobar; la app no descargará modelos."
                symbolName = "eye.slash"
                buttonAccessibilityHint = "Vuelve a comprobar el modelo local con capacidad de visión sin abrir ni modificar Fotos."
            } else if state.errorCodes.contains("MODEL_INVALID") {
                title = "Modelo local no válido"
                detail = "La configuración de modelos no es válida. Corrige el nombre del modelo local y vuelve a comprobar antes del dry-run."
                symbolName = "shippingbox"
                buttonAccessibilityHint = "Corrige la configuración del modelo y vuelve a comprobar sin abrir ni modificar Fotos."
            } else if state.errorCodes.contains("OLLAMA_PREFLIGHT_FAILED") {
                title = "No se pudo comprobar Ollama"
                detail = "La comprobación del servicio local no terminó correctamente. Revisa Ollama y vuelve a comprobar antes del dry-run."
                symbolName = "arrow.clockwise.circle"
                buttonAccessibilityHint = "Repite la comprobación del servicio local sin abrir ni modificar Fotos."
            } else {
                title = "Vuelve a comprobar la preparación"
                detail = "Corrige el componente local indicado y repite la comprobación antes de iniciar un dry-run."
                symbolName = "arrow.clockwise.circle"
                buttonAccessibilityHint = "Repite la comprobación local sin abrir ni modificar Fotos."
            }
            buttonTitle = "Comprobar de nuevo"
            isBlocking = true
        case "none" where state.isReady:
            if state.automation == .pending {
                title = "Componentes base listos"
                detail = "El análisis será local y de solo lectura; Automatización de Fotos se comprobará al iniciar PhotoScript."
                buttonTitle = "Continuar y comprobar PhotoScript"
                buttonAccessibilityHint = "Abre Revisión; la mesa continua comprobará PhotoScript y Automatización localmente antes de analizar fotos. No se escribirán cambios sin confirmación."
            } else {
                title = "Todo listo para un dry-run"
                detail = "El análisis será local, de solo lectura y no modificará Apple Fotos."
                buttonTitle = "Continuar a Revisión"
                buttonAccessibilityHint = "Abre la mesa continua de Revisión para analizar fotos localmente; no se escriben cambios sin pulsar Guardar en Fotos."
            }
            symbolName = "checkmark.shield.fill"
            isBlocking = false
        default:
            title = "Comprueba los componentes locales"
            detail = "Confirma Ollama, los modelos configurados y el acceso de lectura a Fotos antes de continuar."
            buttonTitle = "Comprobar preparación local"
            buttonAccessibilityHint = "Comprueba los componentes locales sin modificar Fotos."
            symbolName = "checkmark.shield"
            isBlocking = false
        }
    }

    var accessibilityLabel: String {
        var value = "\(title). \(detail)"
        if let safeInstruction {
            value += " Comando informativo: \(safeInstruction). No se copiarán rutas ni datos privados."
        }
        return value
    }

    var commandCopyAccessibilityHint: String {
        "Copia únicamente el comando local de Ollama al portapapeles; la app no lo ejecutará ni descargará el modelo."
    }

    func commandCopyAccessibilityLabel(copied: Bool) -> String {
        guard let safeInstruction else { return "" }
        return copied ? "Comando copiado: \(safeInstruction)" : "Copiar comando: \(safeInstruction)"
    }
}

/// Keeps the auxiliary Photos permission CTA as informative for VoiceOver as
/// the primary preparation action. This only describes the action; it does not
/// change permission state or bypass any preparation gate.
enum PhotosAccessActionCopy {
    enum Resolution: Equatable {
        case requestAuthorization
        case openSettings
        case none
    }

    static func resolution(for permission: PhotosPermissionState) -> Resolution {
        switch permission {
        case .notDetermined:
            return .requestAuthorization
        case .denied, .restricted:
            return .openSettings
        case .authorized, .limited:
            return .none
        }
    }

    static func shouldShowAccessAction(
        permission: PhotosPermissionState,
        preparation: PreparationCheckState
    ) -> Bool {
        if preparation == .actionRequired {
            return true
        }
        switch permission {
        case .authorized, .limited:
            return false
        case .denied, .restricted, .notDetermined:
            return true
        }
    }

    static func shouldShowAuxiliaryAction(
        permission: PhotosPermissionState,
        preparation: PreparationCheckState,
        primaryAction: String
    ) -> Bool {
        primaryAction != "grant_photos_access"
            && shouldShowAccessAction(permission: permission, preparation: preparation)
    }

    static func accessibilityHint(for state: PreparationCheckState) -> String {
        state == .actionRequired
            ? PhotosTCCGuide.packagedApplication.accessibilityHint
            : "Solicita el permiso de lectura de Fotos. No se escribirán cambios en la biblioteca."
    }
}

/// A permission failure observed by PhotoKit must block follow-up mutation
/// attempts until the person rechecks access. The worker remains the final
/// authority, but this gate prevents a predictable failed write from being
/// offered as an actionable UI flow.
enum PhotosMutationAccess {
    private static let photosMessage = "La Preparación de Fotos requiere atención antes de aplicar o revertir cambios. Comprueba el acceso de Fotos y ejecuta un dry-run nuevo."

    enum Operation {
        case apply
        case rollback
    }

    static func isBlocked(_ state: PreparationState) -> Bool {
        blockingMessage(for: state) != nil
    }

    static func blockingMessage(
        for state: PreparationState,
        operation: Operation = .apply,
        localReviewOnly: Bool = false
    ) -> String? {
        // Materializing a reviewed manifest is a private local operation. A
        // current Photos or Automation denial must block the later mutation,
        // while still allowing the person to inspect and approve the scope.
        guard !localReviewOnly else { return nil }
        if state.photos == .actionRequired || state.photos == .failed {
            switch operation {
            case .apply:
                return photosMessage
            case .rollback:
                return "La Preparación de Fotos requiere atención antes de revertir cambios. Comprueba el acceso de Fotos, conserva el run verificado y reintenta el rollback desde Historial."
            }
        }
        if state.automation == .actionRequired || state.automation == .failed {
            if state.errorCodes.contains("PHOTOSCRIPT_UNAVAILABLE") {
                switch operation {
                case .apply:
                    return HumanErrorCopy.message(for: "PHOTOSCRIPT_UNAVAILABLE")
                case .rollback:
                    return "PhotoScript no está disponible para revertir cambios. Conserva el run verificado, corrige la compatibilidad local y reintenta el rollback desde Historial."
                }
            }
            switch operation {
            case .apply:
                return AutomationTCCGuide.packagedHelper.errorMessage
            case .rollback:
                return "Falta el permiso de Automatización para que PhotosIndexerWorker revierta cambios en Fotos. Actívalo en Configuración del Sistema, conserva el run verificado y reintenta el rollback desde Historial."
            }
        }
        return nil
    }
}

/// Keeps the Photos status card honest about what has actually been checked.
/// Pending or in-flight checks must not look like confirmed read/export
/// capability; the ready state is the only one that describes those actions.
enum PhotosStatusCopy {
    static func detail(state: PreparationCheckState, accessIsLimited: Bool) -> String {
        switch state {
        case .pending:
            return "Aún no se ha comprobado el acceso de lectura a Fotos."
        case .checking:
            return "Comprobando el acceso de lectura a Fotos."
        case .actionRequired:
            return PhotosTCCGuide.packagedApplication.detail
        case .failed:
            return "No se pudo comprobar el acceso de lectura a Fotos; vuelve a intentarlo."
        case .ready:
            return accessIsLimited
                ? "Acceso limitado concedido a la app; PhotosIndexerWorker comprobará su acceso y las fotos visibles al iniciar el dry-run."
                : "Acceso de Fotos concedido a la app; PhotosIndexerWorker comprobará su propio acceso de lectura y exportación al iniciar el dry-run. Las escrituras siguen requiriendo confirmación."
        }
    }
}

enum AutomationRecoveryPolicy {
    static func canRetry(
        from state: PreparationCheckState,
        errorCodes: [String]
    ) -> Bool {
        (state == .actionRequired || state == .failed)
            && errorCodes.contains("PHOTOS_AUTOMATION_DENIED")
    }

    static func stateAfterCTA(from state: PreparationCheckState) -> PreparationCheckState {
        (state == .actionRequired || state == .failed) ? .pending : state
    }
}

enum AutomationRecoveryCopy {
    static let buttonTitle = "Ya concedí Automatización · continuar"
    static let buttonAccessibilityHint = "No confirma el permiso; el próximo dry-run lo comprobará mediante PhotoScript. No se abrirá un shell ni se modificará Fotos."
}

/// Keeps the prominent Automation status card aligned with the process that
/// actually sends Apple Events. macOS grants TCC to that client, not to a
/// generic app or Terminal label.
enum AutomationStatusCopy {
    static func detail(
        state: PreparationCheckState,
        errorCodes: [String] = []
    ) -> String {
        if errorCodes.contains("PHOTOSCRIPT_UNAVAILABLE") {
            return "El puente AppleScript de PhotoScript no pudo cargarse; comprueba la compatibilidad local de Photos, PhotoScript y macOS."
        }
        switch state {
        case .actionRequired, .failed:
            return AutomationTCCGuide.packagedHelper.detail
        default:
            return "No se puede comprobar pasivamente; PhotoScript solicitará controlar Fotos en la primera operación."
        }
    }
}

/// Compact readiness projection for the preparation header. Automation is
/// intentionally excluded from the numeric denominator because macOS cannot
/// passively verify Apple Events; its deferred state remains visible in the
/// status grid and in the final detail.
struct PreparationReadinessSummary: Equatable, Sendable {
    static let sectionTitle = "Preparación local"

    let readyCount: Int
    let requiredCount: Int
    let fraction: Double
    let title: String
    let detail: String
    let symbolName: String

    init(state: PreparationState, preflightIsStale: Bool = false) {
        let automationBlocks = state.automation == .actionRequired || state.automation == .failed
        let requiredStates = automationBlocks
            ? [state.photos, state.ollama, state.models, state.automation]
            : [state.photos, state.ollama, state.models]
        readyCount = requiredStates.filter { $0 == .ready }.count
        requiredCount = requiredStates.count
        fraction = requiredCount == 0 ? 0 : Double(readyCount) / Double(requiredCount)

        let componentLabel: String
        if state.isReady && state.automation == .pending {
            componentLabel = requiredCount == 1 ? "componente base" : "componentes base"
        } else {
            componentLabel = requiredCount == 1 ? "componente requerido" : "componentes requeridos"
        }
        let warningSuffix = state.isReady && !state.warningCodes.isEmpty ? " con advertencias" : ""
        title = preflightIsStale
            ? "\(readyCount) de \(requiredCount) \(componentLabel) listos; requiere nueva comprobación"
            : "\(readyCount) de \(requiredCount) \(componentLabel) listos\(warningSuffix)"
        if preflightIsStale {
            detail = PreparationStaleCopy.text
        } else if state.isReady && !state.warningCodes.isEmpty {
            detail = "Componentes técnicos listos con advertencias. Revisa el estado indicado antes de continuar; Automatización aún no está verificada y se comprobará al iniciar PhotoScript."
        } else if state.isReady && state.automation == .pending {
            detail = "Componentes técnicos listos. Automatización aún no está verificada y se comprobará al iniciar PhotoScript."
        } else if state.isReady {
            detail = "Listo para un dry-run."
        } else if state.requiresAttention {
            detail = "Corrige el componente marcado en Preparación antes de analizar fotos."
        } else {
            detail = "Completa la comprobación local antes de iniciar un dry-run."
        }
        symbolName = preflightIsStale
            ? "arrow.triangle.2.circlepath"
            : (state.isReady && !state.warningCodes.isEmpty
            ? "exclamationmark.triangle.fill"
            : (state.isReady && state.automation == .pending
            ? "clock.badge.checkmark"
            : (state.isReady ? "checkmark.circle.fill" : "chart.bar")))
    }

    /// A 100% base check must not look like every permission is authorized.
    /// Automation is deferred until PhotoScript runs and therefore has no
    /// honest numeric completion value at onboarding time.
    var progressLabel: String {
        if symbolName == "arrow.triangle.2.circlepath" {
            return "Requiere comprobación"
        }
        if symbolName == "exclamationmark.triangle.fill" {
            return "Base lista con advertencias"
        }
        guard symbolName == "clock.badge.checkmark" else {
            return "\(Int((fraction * 100).rounded()))%"
        }
        return "Base lista"
    }

    var accessibilityLabel: String {
        "Preparación local: \(title). \(detail)"
    }
}

/// TCC grants Apple Events to the process that actually sends them. Keeping
/// this copy separate from the permission state prevents the onboarding card
/// from claiming that authorizing Terminal (or the app label) is sufficient
/// when PhotoScript is running inside another executable.
struct AutomationTCCGuide: Equatable, Sendable {
    enum Client: Equatable, Sendable {
        case packagedHelper
        case developmentInterpreter

        var displayName: String {
            switch self {
            case .packagedHelper: return "PhotosIndexerWorker"
            case .developmentInterpreter: return "el intérprete Python que ejecuta PhotoScript"
            }
        }

        var detail: String {
            switch self {
            case .packagedHelper:
                return "PhotoScript controla Fotos desde el helper local PhotosIndexerWorker. En Configuración del Sistema > Privacidad y seguridad > Automatización, permite que Fotos sea controlado por PhotosIndexerWorker; autorizar Terminal por separado no concede este permiso al helper."
            case .developmentInterpreter:
                return "PhotoScript controla Fotos desde el intérprete Python que ejecuta el comando. En Configuración del Sistema > Privacidad y seguridad > Automatización, permite que Fotos sea controlado por ese intérprete; autorizar Terminal por separado no concede este permiso al proceso Python."
            }
        }

        var accessibilityHint: String {
            switch self {
            case .packagedHelper:
                return "Abre Automatización. Activa Fotos para PhotosIndexerWorker, el helper local que envía los eventos; autorizar Terminal no es suficiente. La decisión se confirmará al ejecutar PhotoScript."
            case .developmentInterpreter:
                return "Abre Automatización. Activa Fotos para el intérprete Python que ejecuta PhotoScript; autorizar Terminal no es suficiente. La decisión se confirmará al ejecutar PhotoScript."
            }
        }
    }

    let client: Client

    static let packagedHelper = Self(client: .packagedHelper)
    static let developmentInterpreter = Self(client: .developmentInterpreter)

    var detail: String { client.detail }
    var accessibilityHint: String { client.accessibilityHint }

    var errorMessage: String {
        switch client {
        case .packagedHelper:
            return "Falta el permiso de Automatización para que PhotosIndexerWorker envíe Apple Events a Fotos. Es independiente del permiso de Fotos. En Configuración del Sistema > Privacidad y seguridad > Automatización, permite que Fotos sea controlado por PhotosIndexerWorker; después vuelve aquí y ejecuta un dry-run nuevo."
        case .developmentInterpreter:
            return "Falta el permiso de Automatización para que el intérprete Python envíe Apple Events a Fotos. Es independiente del permiso de Fotos. En Configuración del Sistema > Privacidad y seguridad > Automatización, permite que Fotos sea controlado por ese intérprete; después vuelve aquí y ejecuta el CLI de nuevo."
        }
    }

    var recoveryActionText: String {
        switch client {
        case .packagedHelper:
            return "Abre Configuración del Sistema > Privacidad y seguridad > Automatización y permite que Fotos sea controlado por PhotosIndexerWorker; después ejecuta un dry-run nuevo"
        case .developmentInterpreter:
            return "Abre Configuración del Sistema > Privacidad y seguridad > Automatización y permite que Fotos sea controlado por el intérprete Python; después ejecuta el CLI de nuevo"
        }
    }

    var displayName: String { client.displayName }

    var shortLabel: String {
        switch client {
        case .packagedHelper: return "Helper que controla Fotos: PhotosIndexerWorker"
        case .developmentInterpreter: return "Proceso que controla Fotos: intérprete Python"
        }
    }
}

/// Photos privacy and Apple Events Automation are separate macOS TCC
/// decisions. Keep their boundary explicit in onboarding so granting one is
/// never presented as evidence that the other is ready.
struct PermissionBoundaryGuide: Equatable, Sendable {
    static let sectionTitle = "Permisos de Fotos y Automatización"
    static let title = "Fotos y Automatización son permisos distintos"
    static let distinction = "Conceder uno no concede el otro. Comprueba ambos antes de iniciar el dry-run."
    static let photos = "Fotos: PhotoKit necesita acceso de lectura para enumerar y exportar copias temporales."
    static let automation = "Automatización: PhotosIndexerWorker necesita permiso para enviar eventos a Fotos mediante PhotoScript; autorizar Terminal, Codex u otro intérprete por separado no concede este permiso al helper."

    static var accessibilityLabel: String {
        "\(title). \(distinction) \(photos) \(automation)"
    }
}

/// PhotoKit's authorization is associated with the process/bundle making the
/// request. A development CLI and the signed app therefore need separate
/// guidance and must not be presented as interchangeable clients.
struct PhotosTCCGuide: Equatable, Sendable {
    enum Client: Equatable, Sendable {
        case packagedApplication
        case developmentInterpreter

        var detail: String {
            switch self {
            case .packagedApplication:
                return "Falta el permiso de Fotos para PhotoKit en la app firmada Photos Local Keyword Indexer, concretamente en su helper PhotosIndexerWorker. En Configuración del Sistema > Privacidad y seguridad > Fotos, activa esa entrada; PhotoKit se ejecuta desde ese helper y el permiso concedido a un intérprete de desarrollo no se transfiere a la app instalada."
            case .developmentInterpreter:
                return "Falta el permiso de Fotos para PhotoKit en el intérprete Python que ejecuta el CLI. En Configuración del Sistema > Privacidad y seguridad > Fotos, activa esa entrada; el permiso de la app firmada no se transfiere al CLI."
            }
        }

        var errorMessage: String {
            switch self {
            case .packagedApplication:
                return "Falta el permiso de Fotos para PhotoKit en la app firmada Photos Local Keyword Indexer, concretamente en su helper PhotosIndexerWorker. Es independiente del permiso de Automatización. En Configuración del Sistema > Privacidad y seguridad > Fotos, activa esa entrada; PhotoKit se ejecuta desde ese helper. Después vuelve aquí y ejecuta un dry-run nuevo."
            case .developmentInterpreter:
                return "Falta el permiso de Fotos para PhotoKit en el intérprete Python que ejecuta el CLI. Es independiente del permiso de Automatización. En Configuración del Sistema > Privacidad y seguridad > Fotos, activa esa entrada; después ejecuta el CLI de nuevo."
            }
        }

        var recoveryActionText: String {
            switch self {
            case .packagedApplication:
                return "Abre Configuración del Sistema > Privacidad y seguridad > Fotos y activa el acceso de Photos Local Keyword Indexer / PhotosIndexerWorker para PhotoKit; después ejecuta un dry-run nuevo"
            case .developmentInterpreter:
                return "Abre Configuración del Sistema > Privacidad y seguridad > Fotos y activa el acceso del intérprete Python para PhotoKit; después ejecuta el CLI de nuevo"
            }
        }

        var accessibilityHint: String {
            switch self {
            case .packagedApplication:
                return "Abre la configuración de Fotos y activa Photos Local Keyword Indexer / PhotosIndexerWorker. PhotoKit se ejecuta desde el helper firmado; una autorización del intérprete Python del CLI no basta. Después vuelve aquí y comprueba de nuevo."
            case .developmentInterpreter:
                return "En la configuración de Fotos activa el intérprete Python que ejecuta el CLI. PhotoKit se ejecuta desde ese proceso; autorizar la app firmada o Terminal no basta. Después vuelve a ejecutar el CLI."
            }
        }
    }

    let client: Client

    static let packagedApplication = Self(client: .packagedApplication)
    static let developmentInterpreter = Self(client: .developmentInterpreter)

    var detail: String { client.detail }
    var errorMessage: String { client.errorMessage }
    var recoveryActionText: String { client.recoveryActionText }
    var accessibilityHint: String { client.accessibilityHint }
}

struct ScanPrivacyNotice: Equatable {
    let appleMapsEnabled: Bool

    var isVisible: Bool { appleMapsEnabled }

    var text: String {
        guard appleMapsEnabled else { return "" }
        return AppleMapsPrivacyCopy.text(enabled: true)
    }

    /// Keeps the opt-in data disclosure attached to the control itself so it
    /// is announced by VoiceOver before the user changes the setting.
    var accessibilityHint: String {
        AppleMapsPrivacyCopy.text(enabled: appleMapsEnabled)
    }
}

/// Keeps the Apple Maps disclosure semantically aligned with the state shown
/// by its icon. Coordinates are only sent when the user opts in for a run;
/// this copy never exposes or persists a coordinate value.
enum AppleMapsPrivacyCopy {
    static func text(enabled: Bool) -> String {
        enabled
            ? "Apple Maps recibirá las coordenadas geográficas de las fotos con ubicación durante esta ejecución. Las imágenes no se enviarán."
            : "Apple Maps está desactivado; no recibirá coordenadas en esta ejecución."
    }

    static func symbolName(enabled: Bool) -> String {
        enabled ? "location.fill" : "location.slash"
    }
}

enum ScanCaptionCopy {
    static let toggleLabel = "Proponer caption breve para revisión"
    static let detail = "Cada caption se revisa por separado y solo se escribe tras confirmar; nunca reemplaza un caption existente."
    static let accessibilityHint = detail
}

/// The preparation preflight also performs a local PhotoScript compatibility
/// check. Keep this copy aligned with that contract so the UI does not imply
/// that only Ollama is inspected.
enum PreparationPreflightCopy {
    static let detail = "Esta comprobación consulta Ollama y modelos locales, y comprueba la compatibilidad de PhotoScript; todavía no abre ni modifica Fotos."
}

enum LocalAnalysisPrivacyCopy {
    static let symbolName = "lock.shield"
}

/// A compact, privacy-safe projection of the controls that will be sent to a
/// dry-run. Keeping it as a value model gives the scan screen one visual
/// summary without duplicating wording across several form sections.
struct ScanPlanSummary: Equatable, Sendable {
    let photoLimit: Int
    let modelText: String
    /// Optional detail from `ScanModelConfiguration` explaining the actual
    /// routing decision. It is included in the combined VoiceOver summary so
    /// adaptive runs do not sound like a silent fallback to one model.
    let modelRoutingText: String?
    let appleMapsEnabled: Bool
    let captionsEnabled: Bool
    let randomSelection: Bool

    init(
        photoLimit: Int,
        modelText: String,
        modelRoutingText: String? = nil,
        appleMapsEnabled: Bool,
        captionsEnabled: Bool,
        randomSelection: Bool = false
    ) {
        self.photoLimit = photoLimit
        let normalized = modelText.trimmingCharacters(in: .whitespacesAndNewlines)
        self.modelText = normalized.isEmpty ? "Modelo local pendiente de configurar" : normalized
        let routing = modelRoutingText?.trimmingCharacters(in: .whitespacesAndNewlines)
        self.modelRoutingText = routing?.isEmpty == false ? routing : nil
        self.appleMapsEnabled = appleMapsEnabled
        self.captionsEnabled = captionsEnabled
        self.randomSelection = randomSelection
    }

    var scopeText: String {
        if randomSelection {
            switch photoLimit {
            case 1: return "1 foto elegible seleccionada al azar"
            case let count where count > 1: return "Hasta \(count) fotos elegibles seleccionadas al azar"
            default: return "Fotos elegibles seleccionadas al azar"
            }
        }
        switch photoLimit {
        case 1: return "1 foto reciente elegible"
        case let count where count > 1: return "Hasta \(count) fotos recientes elegibles"
        default: return "Fotos recientes elegibles"
        }
    }

    var contextText: String {
        appleMapsEnabled
            ? "Apple Maps: activado para fotos con ubicación"
            : "Apple Maps: desactivado"
    }

    var modelLabelText: String {
        "Modelo: \(modelText)"
    }

    var outputText: String {
        captionsEnabled
            ? "Keywords: propuestas · Captions: propuestas para revisión"
            : "Keywords: propuestas · Captions: desactivados"
    }

    var accessibilityLabel: String {
        let routing = modelRoutingText.map { " Decisión de modelo: \($0)." } ?? ""
        return "Plan de ejecución. \(scopeText). Modelo: \(modelText).\(routing) \(contextText). \(outputText). El análisis es dry-run y no modifica Apple Fotos."
    }
}

/// Validation shared by the editable model configuration and the untrusted
/// helper event boundary. Model names and Ollama versions are displayed in
/// onboarding/settings, so neither may carry a path or arbitrary loopback
/// response into the UI.
enum OllamaModelPresentationPolicy {
    static func isValidModelName(_ model: String) -> Bool {
        guard model.count <= 128,
              model == model.trimmingCharacters(in: .whitespacesAndNewlines),
              model.range(
                  of: #"^[A-Za-z0-9][A-Za-z0-9._-]*(?:/[A-Za-z0-9][A-Za-z0-9._-]*)*(?::[A-Za-z0-9][A-Za-z0-9._-]*)?$"#,
                  options: .regularExpression
              ) != nil else {
            return false
        }
        return model.range(of: "cloud", options: .caseInsensitive) == nil
    }

    static func isValidPullInstruction(_ instruction: String) -> Bool {
        let prefix = "ollama pull "
        guard instruction.hasPrefix(prefix) else { return false }
        return isValidModelName(String(instruction.dropFirst(prefix.count)))
    }

    static func isValidVersion(_ version: String) -> Bool {
        guard version.count <= 64 else { return false }
        return version.range(
            of: #"^[0-9]+\.[0-9]+\.[0-9]+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$"#,
            options: .regularExpression
        ) != nil
    }
}

/// Keeps configurable model identifiers readable in cards, tables, and
/// VoiceOver without changing the complete value sent to the helper.
enum ModelPresentationCopy {
    static let maximumLength = 64

    static func display(_ value: String) -> String {
        let normalized = value
            .split(whereSeparator: \.isWhitespace)
            .joined(separator: " ")
            .trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty else { return "Modelo local pendiente de configurar" }
        guard normalized.count > maximumLength else { return normalized }

        let prefixLength = (maximumLength - 1) / 2
        let suffixLength = maximumLength - 1 - prefixLength
        return "\(normalized.prefix(prefixLength))…\(normalized.suffix(suffixLength))"
    }
}

struct ScanModelConfiguration: Equatable, Sendable {
    let policy: String
    let singleModel: String
    let fastModel: String
    let detailedModel: String

    var policyPickerLabel: String {
        switch policy {
        case "adaptive": return "Adaptativa: rápido y detallado (ambos obligatorios)"
        case "single": return "Un solo modelo"
        default: return "Política no válida"
        }
    }

    var validationMessage: String? {
        guard policy == "adaptive" || policy == "single" else {
            return "Selecciona una política de modelos válida antes de continuar."
        }
        let models = policy == "single" ? [singleModel] : [fastModel, detailedModel]
        guard models.allSatisfy({ !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }) else {
            return "Configura un modelo local válido antes de iniciar el dry-run."
        }
        guard models.allSatisfy(OllamaModelPresentationPolicy.isValidModelName) else {
            return "Usa un nombre de modelo Ollama local válido; los modelos cloud no están permitidos."
        }
        return nil
    }

    var isValid: Bool { validationMessage == nil }

    var selectedModels: [String] {
        policy == "single" ? [singleModel] : [fastModel, detailedModel]
    }

    func missingModels(from installedModels: [String]) -> [String] {
        let installed = Set(installedModels)
        var seen = Set<String>()
        return selectedModels.compactMap { model in
            let normalized = model.trimmingCharacters(in: .whitespacesAndNewlines)
            guard !normalized.isEmpty,
                  OllamaModelPresentationPolicy.isValidModelName(normalized),
                  !installed.contains(normalized),
                  seen.insert(normalized).inserted else { return nil }
            return normalized
        }
    }

    var qualityText: String {
        switch policy {
        case "adaptive":
            let rawFast = fastModel.trimmingCharacters(in: .whitespacesAndNewlines)
            let rawDetailed = detailedModel.trimmingCharacters(in: .whitespacesAndNewlines)
            let fast = ModelPresentationCopy.display(rawFast)
            if rawFast == rawDetailed {
                return "Adaptativa con un modelo: \(fast) se usará en todas las fotos, incluidas las geolocalizadas. Configura otro modelo detallado si quieres una segunda etapa."
            }
            return "Calidad adaptativa: usa el modelo rápido sin GPS y el detallado cuando hay ubicación; ambos deben estar instalados."
        case "single":
            return "Calidad uniforme: usa el mismo modelo en todas las fotos; debe estar instalado."
        default:
            return "Selecciona una política para definir cómo se analizarán las fotos."
        }
    }

    var configuredModelsText: String {
        switch policy {
        case "single":
            return "Modelo único: \(ModelPresentationCopy.display(singleModel))"
        case "adaptive":
            let rawFast = fastModel.trimmingCharacters(in: .whitespacesAndNewlines)
            let rawDetailed = detailedModel.trimmingCharacters(in: .whitespacesAndNewlines)
            let fast = ModelPresentationCopy.display(rawFast)
            let detailed = ModelPresentationCopy.display(rawDetailed)
            if rawFast == rawDetailed {
                return "Modelo adaptativo único: \(fast)"
            }
            return "Modelos adaptativos: rápido \(fast) · detallado \(detailed)"
        default:
            return "Modelos no configurados"
        }
    }
}

/// Recovery copy for a model selected in Revisión but not observed by
/// the last local preflight. The action only returns to Preparación; model
/// installation remains a manual user action and never runs a shell command.
enum ScanModelRecoveryCopy {
    static let buttonTitle = "Volver a Preparación"
    static let buttonAccessibilityHint = "Abre Preparación para comprobar o instalar manualmente los modelos locales; la app no descargará modelos."
}

/// A small, privacy-safe projection of helper events for the live scan UI.
///
/// The helper intentionally does not send photo titles, paths, captions or
/// image data. The UI can still communicate useful progress from the bounded
/// event stream without widening that IPC contract.
struct ScanProgressSummary: Equatable, Sendable {
    let requestedLimit: Int
    let processedCount: Int
    let failedCount: Int
    let uncertainCount: Int
    let cancelledCount: Int
    let currentUUID: String?
    let currentModel: String?
    private let workerTerminalState: WorkerProcessState?

    /// A terminal event means the helper has closed the request. In that
    /// state the progress indicator must not remain at (for example) 50% just
    /// because the library contained fewer eligible photos than requested.
    var isTerminal: Bool {
        terminalEvent != nil || workerTerminalState?.terminalStatusText != nil
    }

    /// The worker returns to `.ready` as soon as it receives a terminal event.
    /// Keep the terminal outcome in the progress model so the UI does not
    /// mistake a non-zero completion or cancellation for a clean dry-run.
    var terminalOutcome: ScanTerminalOutcome? {
        guard let terminalEvent else { return nil }
        if terminalEvent.event == .error {
            return terminalEvent.code == "CANCELLED" ? .cancelled : .failed
        }
        if terminalEvent.errorCodes?.contains("CANCELLED") == true {
            return .cancelled
        }
        if terminalEvent.exitCode.map({ $0 != 0 }) == true
            || terminalEvent.errorCodes?.isEmpty == false
            || terminalEvent.warningCodes?.isEmpty == false {
            return .completedWithIssues
        }
        return .completed
    }

    /// A VoiceOver label that combines the bounded counters with the terminal
    /// recovery action. The visible counters remain unchanged for scanning.
    var accessibilityLabel: String {
        guard let terminalOutcome else {
            guard let workerStatus = workerTerminalState?.terminalStatusText else {
                return accessibilitySummary
            }
            return accessibilitySummary + " Estado final: " + workerStatus
        }
        let outcomeText: String
        switch terminalOutcome {
        case .completed:
            outcomeText = processedCount == 0
                ? "Dry-run completado; no hay fotos elegibles para revisar o aplicar. Conserva el resultado y revisa el alcance o el acceso a Fotos antes de ejecutar otro dry-run."
                : "Dry-run completado; revisa el resultado antes de aplicar."
        case .completedWithIssues:
            outcomeText = "El dry-run terminó con errores o advertencias; revisa el run antes de aplicar."
        case .cancelled:
            outcomeText = "El dry-run se canceló; ejecuta un dry-run nuevo antes de aplicar."
        case .failed:
            outcomeText = "El dry-run falló; ejecuta un dry-run nuevo antes de aplicar."
        }
        return accessibilitySummary + " " + outcomeText
    }

    private let terminalEvent: WorkerEvent?

    /// Results which completed without a failure or cancellation. Keep this
    /// derived from the sanitized event counts so the UI never needs the
    /// helper's raw response or any photo metadata.
    var successfulCount: Int {
        max(0, processedCount - failedCount - uncertainCount - cancelledCount)
    }

    /// A compact VoiceOver summary for the progress card. It deliberately
    /// reports counts only; UUIDs, captions and paths remain outside the UI
    /// status surface.
    var accessibilitySummary: String {
        let processedLabel = processedCount == 1 ? "foto procesada" : "fotos procesadas"
        let successfulLabel = successfulCount == 1 ? "completada" : "completadas"
        let failedLabel = failedCount == 1 ? "con error" : "con errores"
        let uncertainLabel = uncertainCount == 1 ? "requiere revisión manual" : "requieren revisión manual"
        let cancelledLabel = cancelledCount == 1 ? "cancelada" : "canceladas"
        let base = String(processedCount) + " de hasta " + String(requestedLimit) + " " + processedLabel + "."
        var parts = [
            String(successfulCount) + " " + successfulLabel,
            String(failedCount) + " " + failedLabel
        ]
        if uncertainCount > 0 {
            parts.append(String(uncertainCount) + " " + uncertainLabel)
        }
        parts.append(String(cancelledCount) + " " + cancelledLabel)
        return base + " " + parts.joined(separator: ", ") + "."
    }

    /// Keep the live progress card consistent with the bounded model labels
    /// used elsewhere in the app. The raw value remains available for
    /// internal state and tests, but untrusted event text must not be shown
    /// verbatim in the UI.
    var currentModelDisplayText: String? {
        guard let currentModel else { return nil }
        let normalized = currentModel.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalized.isEmpty,
              OllamaModelPresentationPolicy.isValidModelName(normalized) else {
            return nil
        }
        return ModelPresentationCopy.display(normalized)
    }

    init(
        events: [WorkerEvent],
        requestedLimit: Int,
        workerState: WorkerProcessState? = nil
    ) {
        // AppModel keeps one bounded event stream for scan, review, apply and
        // rollback so Historial can audit the complete interaction. Anchor the
        // live scan card to the latest explicit scan request; otherwise a
        // later apply failure would be counted as a failed photo in the
        // already-completed dry-run. Older test fixtures and interrupted
        // streams without a started event keep the conservative fallback.
        let latestScanRequestID = events.last(where: {
            $0.event == .started && $0.operation == "scan"
        })?.id
        let scanEvents = latestScanRequestID.map { requestID in
            events.filter { $0.id == requestID }
        } ?? events
        let photoEvents = scanEvents.filter { $0.event == .photoProgress }
        self.requestedLimit = max(0, requestedLimit)
        self.workerTerminalState = workerState
        processedCount = photoEvents.count
        failedCount = photoEvents.filter { event in
            event.state.map { ["analysis_failed", "failed"].contains($0) } ?? false
        }.count
        uncertainCount = photoEvents.filter { $0.state == "uncertain" }.count
        cancelledCount = photoEvents.filter { $0.state == "cancelled" }.count
        currentUUID = photoEvents.last?.uuid
        currentModel = photoEvents.last?.modelUsed
        terminalEvent = scanEvents.last { event in
            event.event == .completed || event.event == .error
        }
    }

    var fraction: Double {
        if isTerminal { return 1 }
        guard requestedLimit > 0 else { return 0 }
        return min(1, Double(processedCount) / Double(requestedLimit))
    }

    var statusText: String {
        if let terminalEvent = terminalEvent {
            if terminalEvent.errorCodes?.contains("CANCELLED") == true
                || terminalEvent.code == "CANCELLED" {
                return "Dry-run cancelado; ejecuta un dry-run nuevo antes de aplicar."
            }
            if terminalEvent.event == .error {
                return "El dry-run no pudo completarse. Revisa Preparación y vuelve a intentarlo."
            }
            if terminalEvent.errorCodes?.isEmpty == false
                || terminalEvent.warningCodes?.isEmpty == false {
                return "Dry-run terminó con advertencias o errores. Revisa el run antes de aplicar."
            }
            if terminalEvent.exitCode == 0 {
                guard processedCount > 0 else { return "Dry-run completado: no hay fotos elegibles." }
                let photoLabel = processedCount == 1 ? "foto procesada" : "fotos procesadas"
                return "Dry-run completado: \(processedCount) \(photoLabel) de hasta \(requestedLimit)."
            }
            return "Dry-run terminó con advertencias o errores. Revisa el run antes de aplicar."
        }
        if let workerStatus = workerTerminalState?.terminalStatusText {
            return workerStatus
        }
        guard processedCount > 0 else { return "Esperando la primera foto…" }
        let photoLabel = processedCount == 1 ? "foto procesada" : "fotos procesadas"
        var parts = ["\(processedCount) \(photoLabel) de hasta \(requestedLimit)"]
        if failedCount > 0 {
            parts.append("\(failedCount) " + (failedCount == 1 ? "con error" : "con errores"))
        }
        if uncertainCount > 0 {
            parts.append("\(uncertainCount) " + (uncertainCount == 1 ? "requiere revisión manual" : "requieren revisión manual"))
        }
        if cancelledCount > 0 {
            parts.append("\(cancelledCount) " + (cancelledCount == 1 ? "cancelada" : "canceladas"))
        }
        return parts.joined(separator: " · ")
    }

}
