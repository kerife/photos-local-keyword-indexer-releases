import Foundation
import Combine

private struct PendingContinuousSave {
    let sessionID: String
    let itemID: String
    let revision: Int
    let decisionID: String
    var accepted = false
    var resolved = false
}

private struct PendingContinuousDiscard {
    let sessionID: String
    let itemID: String
    let revision: Int
    let decisionID: String
    let previousItem: QueueReviewItem
    var deferredEvent: WorkerEvent?
}

@MainActor
final class AppModel: ObservableObject {
    @Published var limit = 20 { didSet { persistCurrentSettingsIfReady() } }
    @Published var modelPolicy = "adaptive" { didSet { persistCurrentSettingsIfReady() } }
    @Published var singleModel = "qwen3-vl:4b" { didSet { persistCurrentSettingsIfReady() } }
    @Published var fastModel = "qwen3-vl:4b" { didSet { persistCurrentSettingsIfReady() } }
    // Keep the shipped adaptive configuration usable with the required base
    // model alone. A distinct detailed model remains an explicit opt-in.
    @Published var detailedModel = "qwen3-vl:4b" { didSet { persistCurrentSettingsIfReady() } }
    @Published var appleMaps = false { didSet { persistCurrentSettingsIfReady() } }
    @Published var includeCaption = false { didSet { persistCurrentSettingsIfReady() } }
    @Published var randomSelection = false { didSet { persistCurrentSettingsIfReady() } }
    @Published var autoAnalyze = true { didSet { persistCurrentSettingsIfReady() } }
    @Published var analysisConcurrency = 2 { didSet { persistCurrentSettingsIfReady() } }
    @Published private(set) var continuousControls = ContinuousReviewControls.defaults
    @Published private(set) var reviewSession = ReviewSessionStore(items: [])
    @Published private(set) var queueSessionID: String?
    @Published private(set) var queueRevision = 0
    @Published private(set) var autonomyCampaign: AutonomyCampaignSnapshot?
    @Published private(set) var autonomyActivity: [String: AutonomyActivitySnapshot] = [:]
    private var autonomyActivityRevisions: [String: Int] = [:]
    private var settledAutonomyActivityKeys = Set<String>()
    /// The public beta only permits an explicitly bounded autonomous pilot.
    @Published private(set) var autonomyLimit: Int? = AutonomyPilotPolicy.maximumPhotos
    @Published private(set) var autonomyError: String?
    @Published private(set) var autonomyStatusReady = false
    @Published private(set) var autonomyPausePending = false
    @Published private(set) var autonomyActivationPending = false
    private var autonomyCampaignID: String?
    private var autonomyRequests: [String: WorkerCommand] = [:]
    private var autonomyStatusRequestID: String?

    var autonomyIsOn: Bool {
        !autonomyPausePending && autonomyStatusReady && (autonomyActivationPending || [.preparing, .running].contains(autonomyCampaign?.state))
    }

    var manualQueueMutationAllowed: Bool {
        autonomyStatusReady && !autonomyActivationPending && !autonomyPausePending && autonomyCampaign?.state.ownsHelper != true
    }

    var autonomyControlPending: Bool { !autonomyRequests.isEmpty }

    var autonomyReviewPresentation: AutonomyReviewPresentation {
        AutonomyReviewPresentation(
            campaign: autonomyCampaign,
            activities: autonomyActivity.values.sorted { lhs, rhs in
                lhs.position == rhs.position ? lhs.revision < rhs.revision : lhs.position < rhs.position
            },
            statusReady: autonomyStatusReady,
            activationPending: autonomyActivationPending,
            pausePending: autonomyPausePending,
            controlPending: autonomyControlPending,
            error: autonomyError
        )
    }
    @Published private(set) var canUndoContinuousDiscard = false
    @Published private(set) var continuousReviewAnnouncement: ContinuousReviewAnnouncement?
    @Published private(set) var scanProgressLimit = 20
    @Published private(set) var preparation = PreparationState()
    @Published private(set) var events: [WorkerEvent] = []
    @Published private(set) var manifestURL: URL?
    @Published private(set) var preview: RunManifestPreview?
    @Published private(set) var reviewSourceAvailable = true
    @Published var review = ReviewSelection(photos: [])
    @Published var safetyGate = ApplySafetyGate()
    @Published private(set) var message = "Listo para analizar sin modificar Fotos."
    @Published private(set) var mutationRequiresManualReview = false
    @Published var showApplyConfirmation = false
    private var preparingReview = false
    private var pendingReviewSelection: ReviewSelection?
    private var preflightRequestID: String?
    private var photosAccessRequestInFlight = false
    private var activeQueueRequestID: String?
    private var activeQueueRequestCommand: WorkerCommand?
    private var pendingContinuousDiscard: PendingContinuousDiscard?
    private var continuousSaveRequests: [String: PendingContinuousSave] = [:]
    private var currentContinuousSaveRequest: [String: String] = [:]
    private var pendingContinuousDiscardTask: Task<Void, Never>?
    private var continuousReviewAnnouncementSequence = 0
    private var workerStateCancellable: AnyCancellable?
    private let settingsStore: AppSettingsStore?
    private let queueRunsRoot: URL?
    private let queueDecisionStore: QueueDecisionStore?
    private var settingsPersistenceReady = false

    var canContinueReviewedApply: Bool {
        guard let preview, let manifestURL else { return false }
        return HistoryRunSummary(
            manifestURL: manifestURL,
            preview: preview,
            localReviewSourceAvailable: reviewSourceAvailable
        ).canContinueReviewedApply
    }

    /// A successful model preflight is only valid for the exact policy and
    /// model roles that were checked. Changing either after preflight must
    /// require another local check before a dry-run can start.
    var preflightMatchesCurrentModelConfiguration: Bool {
        let configured = modelPolicy == "single" ? [singleModel] : [fastModel, detailedModel]
        return preparation.models == .ready && preparation.requestedModels == configured
    }

    var preflightIsStale: Bool {
        preparation.isReady && !preflightMatchesCurrentModelConfiguration
    }

    var continuousReviewPreparation: ContinuousReviewPreparation {
        if preparation.isReady && preflightMatchesCurrentModelConfiguration {
            return .ready
        }
        if preparation.ollama == .checking || preparation.models == .checking {
            return .checking
        }
        return .blocked(continuousReviewPreparationIssue)
    }

    private var continuousReviewPreparationIssue: ContinuousReviewPreparationIssue {
        if preparation.photos != .ready { return .photosAccess }
        if preparation.errorCodes.contains("PHOTOS_AUTOMATION_DENIED") { return .photosAutomation }
        if preparation.errorCodes.contains("PHOTOSCRIPT_UNAVAILABLE") { return .photoScript }
        if preparation.errorCodes.contains("HELPER_UNAVAILABLE") { return .helper }
        if preparation.models != .ready || preflightIsStale { return .model }
        return .ollama
    }

    var isRetryingFailedApply: Bool {
        guard let preview, let manifestURL else { return false }
        return HistoryRunSummary(
            manifestURL: manifestURL,
            preview: preview,
            localReviewSourceAvailable: reviewSourceAvailable
        ).isRetryingFailedApply
    }

    var reviewedManifestStatusMessage: String? {
        guard let preview,
              preview.reviewedFromRunID != nil,
              let manifestURL else { return nil }
        return HistoryRunSummary(
            manifestURL: manifestURL,
            preview: preview,
            localReviewSourceAvailable: reviewSourceAvailable
        ).reviewedManifestMessage
    }

    let worker: any WorkerClient
    let permissions: PermissionChecker

    init(
        worker: (any WorkerClient)? = nil,
        permissions: PermissionChecker? = nil,
        initialPreparation: PreparationState? = nil,
        persistedSettings: AppSettings? = nil,
        persistSettings: Bool = false,
        settingsStore: AppSettingsStore? = nil,
        queueRunsRoot: URL? = nil,
        queueDecisionStore: QueueDecisionStore? = nil
    ) {
        let resolvedWorker = worker ?? WorkerProcess()
        let resolvedPermissions = permissions ?? PermissionChecker()
        self.worker = resolvedWorker
        self.permissions = resolvedPermissions
        let resolvedSettingsStore = persistSettings
            ? (settingsStore ?? AppSettingsStore.defaultFileURL().map { AppSettingsStore(fileURL: $0) })
            : nil
        self.settingsStore = resolvedSettingsStore
        let resolvedQueueRunsRoot = queueRunsRoot ?? Self.applicationSupportRunsRoot()
        self.queueRunsRoot = resolvedQueueRunsRoot
        self.queueDecisionStore = queueDecisionStore ?? resolvedQueueRunsRoot.map {
            QueueDecisionStore(
                root: $0.deletingLastPathComponent()
                    .appendingPathComponent("queue-sessions", isDirectory: true)
            )
        }
        if let initialPreparation {
            preparation = initialPreparation
        } else {
            preparation.updatePhotos(resolvedPermissions.photos)
        }
        if let persistedSettings {
            limit = persistedSettings.limit
            modelPolicy = persistedSettings.modelPolicy
            singleModel = persistedSettings.singleModel
            fastModel = persistedSettings.fastModel
            detailedModel = persistedSettings.detailedModel
            appleMaps = persistedSettings.appleMaps
            includeCaption = persistedSettings.includeCaption
            randomSelection = persistedSettings.randomSelection
            autoAnalyze = persistedSettings.autoAnalyze
            analysisConcurrency = persistedSettings.analysisConcurrency
        }
        continuousControls = ContinuousReviewControls(
            photoCount: min(max(limit, 1), 50),
            modelSelection: modelPolicy == "adaptive" ? "adaptive" : singleModel,
            autoAnalyze: autoAnalyze,
            analysisConcurrency: analysisConcurrency,
            includeCaptions: includeCaption,
            appleMaps: appleMaps,
            isPaused: false
        )
        workerStateCancellable = resolvedWorker.statePublisher.sink { [weak self] state in
            guard let self else { return }
            let wasRunning = self.safetyGate.isWorkerRunning
            self.safetyGate.isWorkerRunning = state.isRunning
            guard wasRunning || self.autonomyCampaign != nil || self.reviewSession.items.contains(where: { $0.saveRequestStatus != nil }) else { return }
            switch state {
            case .interrupted:
                self.handleWorkerTermination(message: "La operación se interrumpió; revisa el run antes de intentar de nuevo.")
            case let .failed(code):
                self.handleWorkerTermination(message: HumanErrorCopy.message(for: code), failureCode: code)
            default:
                break
            }
        }
        resolvedWorker.onEvent = { [weak self] event in
            self?.receive(event)
        }
        settingsPersistenceReady = self.settingsStore != nil
        persistCurrentSettings()
    }

    func startContinuousReviewIfNeeded() {
        guard autonomyStatusReady else {
            if autonomyError == nil { bootstrapAutonomyStatusIfNeeded() }
            return
        }
        guard manualQueueMutationAllowed else { return }
        guard queueSessionID == nil else { return }
        guard preparation.isReady, preflightMatchesCurrentModelConfiguration else {
            message = preparation.isReady
                ? "Vuelve a comprobar los modelos locales antes de iniciar la revisión."
                : preparation.statusMessage
            return
        }
        if let sessionsRoot = queueDecisionStore?.root,
           let recovery = HistorySessionStore.latestResumableSession(sessionsRoot: sessionsRoot) {
            applyRecoveredQueueConfiguration(recovery)
            resumeContinuousReview(
                sessionID: recovery.sessionID,
                revision: recovery.revision
            )
            return
        }
        guard continuousControls.autoAnalyze else {
            message = "Activa el análisis automático para iniciar la mesa de revisión."
            return
        }
        guard let runsRoot = queueRunsRoot,
              let settings = settingsStore?.fileURL ?? AppSettingsStore.defaultFileURL() else {
            message = "No fue posible resolver el almacenamiento privado de la cola."
            return
        }
        // Legacy batch settings allowed up to 500 photos. The continuous
        // workspace persists its normalized active-card window before Python
        // reads the private settings artifact.
        limit = continuousControls.photoCount
        persistCurrentSettings()
        let sessionID = UUID().uuidString.lowercased()
        let request = WorkerRequest.queueStart(
            id: requestID(),
            sessionID: sessionID,
            decisionID: UUID().uuidString.lowercased(),
            runsRoot: runsRoot,
            settings: settings
        )
        // Publish the session identity before handing the request to the
        // worker. A helper may emit queue_session/queue_item synchronously
        // while submit() is starting; those events must not be discarded as
        // belonging to an unknown session.
        queueSessionID = sessionID
        queueRevision = 0
        reviewSession = ReviewSessionStore(items: [])
        activeQueueRequestID = request.id
        activeQueueRequestCommand = request.command
        do {
            try worker.submit(request)
            message = "Preparando la mesa continua de revisión…"
        } catch {
            clearQueueSession()
            message = error.localizedDescription
        }
    }

    func resumeContinuousReview(sessionID: String, revision: Int) {
        guard manualQueueMutationAllowed else { return }
        guard queueSessionID == nil,
              UUID(uuidString: sessionID)?.uuidString.lowercased() == sessionID,
              (0 ... Int(Int32.max)).contains(revision) else {
            message = "La sesión guardada no es válida para reanudar."
            return
        }
        let request = WorkerRequest.queueResume(
            id: requestID(),
            sessionID: sessionID,
            revision: revision,
            decisionID: UUID().uuidString.lowercased()
        )
        // As with a new session, accept synchronous recovery events emitted
        // during submit() only after the identity and store are installed.
        queueSessionID = sessionID
        queueRevision = revision
        reviewSession = ReviewSessionStore(items: [])
        activeQueueRequestID = request.id
        activeQueueRequestCommand = request.command
        do {
            try worker.submit(request)
            message = "Recuperando la mesa continua de revisión…"
        } catch {
            clearQueueSession()
            message = error.localizedDescription
        }
    }

    private func clearQueueSession() {
        queueSessionID = nil
        queueRevision = 0
        reviewSession = ReviewSessionStore(items: [])
        activeQueueRequestID = nil
        activeQueueRequestCommand = nil
    }

    private func applyRecoveredQueueConfiguration(_ recovery: QueueSessionRecovery) {
        let config = recovery.config
        settingsPersistenceReady = false
        limit = config.photoCount
        modelPolicy = config.modelPolicy
        if let model = config.model { singleModel = model }
        fastModel = config.fastModel
        detailedModel = config.detailedModel
        appleMaps = config.appleMaps
        includeCaption = config.includeCaption
        randomSelection = config.randomSelection
        autoAnalyze = config.autoAnalyze
        analysisConcurrency = config.inferenceConcurrency
        continuousControls = ContinuousReviewControls(
            photoCount: config.photoCount,
            modelSelection: config.modelPolicy == "adaptive" ? "adaptive" : (config.model ?? config.fastModel),
            autoAnalyze: config.autoAnalyze,
            analysisConcurrency: config.inferenceConcurrency,
            includeCaptions: config.includeCaption,
            appleMaps: config.appleMaps,
            isPaused: recovery.state == .paused
        )
        settingsPersistenceReady = settingsStore != nil
        persistCurrentSettings()
    }

    func updateContinuousControls(_ controls: ContinuousReviewControls) {
        let previousControls = continuousControls
        guard controls != previousControls else { return }
        continuousControls = controls
        limit = controls.photoCount
        autoAnalyze = controls.autoAnalyze
        analysisConcurrency = controls.analysisConcurrency
        includeCaption = controls.includeCaptions
        appleMaps = controls.appleMaps
        if controls.modelSelection == "adaptive" {
            modelPolicy = "adaptive"
        } else {
            modelPolicy = "single"
            singleModel = controls.modelSelection
        }
        guard manualQueueMutationAllowed else { return }
        guard let sessionID = queueSessionID,
              let settings = settingsStore?.fileURL ?? AppSettingsStore.defaultFileURL() else {
            if controls.autoAnalyze { startContinuousReviewIfNeeded() }
            return
        }
        if controls.isPaused != previousControls.isPaused, controls.isPaused {
            submitQueueControl(.queuePause(
                id: requestID(), sessionID: sessionID, revision: queueRevision,
                decisionID: UUID().uuidString.lowercased()
            ))
        } else if controls.isPaused != previousControls.isPaused {
            submitQueueControl(.queueResume(
                id: requestID(), sessionID: sessionID, revision: queueRevision,
                decisionID: UUID().uuidString.lowercased()
            ))
        } else {
            submitQueueControl(.queueUpdate(
                id: requestID(), sessionID: sessionID, revision: queueRevision,
                decisionID: UUID().uuidString.lowercased(), settings: settings
            ))
        }
    }

    func stopContinuousReview() {
        guard manualQueueMutationAllowed else { return }
        guard let sessionID = queueSessionID else {
            message = "No hay una sesión continua activa."
            return
        }
        if pendingContinuousDiscard != nil {
            finalizePendingContinuousDiscard()
        }
        let request = WorkerRequest.queueStop(
            id: requestID(),
            sessionID: sessionID,
            revision: queueRevision,
            decisionID: UUID().uuidString.lowercased()
        )
        activeQueueRequestID = request.id
        activeQueueRequestCommand = request.command
        do {
            try worker.submit(request)
            message = "Finalizando la sesión; el trabajo iniciado terminará de forma segura…"
        } catch {
            activeQueueRequestID = nil
            activeQueueRequestCommand = nil
            message = error.localizedDescription
        }
    }

    func replaceContinuousReviewSession(_ session: ReviewSessionStore) {
        reviewSession = session
    }

    @discardableResult
    func editContinuousItem(id: String, keywords: [String], caption: String?) -> Bool {
        guard let sessionID = queueSessionID,
              let root = queueDecisionStore?.root,
              let item = reviewSession.item(id: id) else { return false }
        let previous = reviewSession
        guard reviewSession.editDraft(id: id, keywords: keywords, caption: caption) else { return false }
        let artifact = QueueReviewDraftArtifact(
            sessionID: sessionID,
            itemID: id,
            revision: item.revision,
            keywords: keywords,
            caption: caption
        )
        do {
            _ = try QueueDraftStore(root: root).write(artifact)
            return true
        } catch {
            reviewSession = previous
            message = "No fue posible guardar el borrador privado de esta foto."
            return false
        }
    }

    func persistContinuousItem(id: String) {
        guard manualQueueMutationAllowed else { return }
        guard let sessionID = queueSessionID,
              let item = reviewSession.item(id: id),
              ContinuousReviewActions(item: item).canSave,
              item.revision > 0,
              let queueDecisionStore else {
            message = "La foto aún no está lista para guardar."
            return
        }
        let decisionID = UUID().uuidString.lowercased()
        let decision = QueueReviewDecision(
            sessionID: sessionID,
            itemID: id,
            revision: item.revision,
            decisionID: decisionID,
            approvedKeywords: item.draft.keywords,
            approvedCaption: item.draft.caption
        )
        do {
            _ = try queueDecisionStore.write(decision)
        } catch {
            message = "No fue posible preparar la decisión privada para esta foto."
            return
        }
        let request = requestID()
        continuousSaveRequests[request] = PendingContinuousSave(
            sessionID: sessionID, itemID: id, revision: item.revision, decisionID: decisionID
        )
        currentContinuousSaveRequest[id] = request
        reviewSession.setSaveRequestStatus(id: id, status: .sending)
        message = "Enviando solicitud…"
        do {
            try worker.submit(.queuePersist(
                id: request, sessionID: sessionID, itemID: id,
                revision: item.revision, decisionID: decisionID
            ))
        } catch {
            // Submission can throw after a synchronous helper event or a
            // partial pipe write. Only an explicit rejection proves no admission.
            resolveContinuousSaveFailure(requestID: request, rejected: false)
        }
    }

    private func resolveContinuousSaveFailure(requestID: String, rejected: Bool) {
        guard var request = continuousSaveRequests[requestID], !request.resolved,
              currentContinuousSaveRequest[request.itemID] == requestID,
              request.sessionID == queueSessionID,
              let item = reviewSession.item(id: request.itemID),
              item.revision == request.revision, item.state != .verified else { return }
        request.resolved = true
        continuousSaveRequests[requestID] = request
        let canRestore = rejected && !request.accepted
        reviewSession.setSaveRequestStatus(id: request.itemID, status: canRestore ? nil : .unconfirmed)
        message = canRestore
            ? "La solicitud fue rechazada antes de guardar. Tu borrador se conserva."
            : "Guardado no confirmado. Requiere revisión antes de continuar."
    }

    func discardContinuousItem(id: String) {
        guard let sessionID = queueSessionID,
              let item = reviewSession.item(id: id),
              item.revision > 0 else { return }
        if pendingContinuousDiscard != nil {
            finalizePendingContinuousDiscard()
        }
        guard reviewSession.discard(id: id) else {
            message = "Esta foto ya no se puede descartar."
            return
        }
        let decisionID = UUID().uuidString.lowercased()
        pendingContinuousDiscard = PendingContinuousDiscard(
            sessionID: sessionID,
            itemID: id,
            revision: item.revision,
            decisionID: decisionID,
            previousItem: item,
            deferredEvent: nil
        )
        canUndoContinuousDiscard = true
        postContinuousReviewAnnouncement("Foto descartada sin modificar Fotos. Deshacer disponible.")
        message = "Foto descartada sin modificar Fotos. Puedes deshacer durante unos segundos."
        pendingContinuousDiscardTask?.cancel()
        pendingContinuousDiscardTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds: 5_000_000_000)
            guard !Task.isCancelled else { return }
            self?.finalizePendingContinuousDiscard(decisionID: decisionID)
        }
    }

    func undoContinuousDiscard() {
        guard let pending = pendingContinuousDiscard else { return }
        pendingContinuousDiscardTask?.cancel()
        pendingContinuousDiscardTask = nil
        pendingContinuousDiscard = nil
        canUndoContinuousDiscard = false
        if queueSessionID == pending.sessionID {
            reviewSession.restoreDiscardedItem(pending.previousItem)
        }
        if let event = pending.deferredEvent {
            applyQueueItemEvent(event)
        }
        postContinuousReviewAnnouncement("Descarte deshecho; la foto volvió a la mesa.")
        message = "Descarte deshecho; la foto volvió a la mesa."
    }

    func finalizePendingContinuousDiscard() {
        finalizePendingContinuousDiscard(decisionID: pendingContinuousDiscard?.decisionID)
    }

    func rescanContinuousItem(id: String) {
        let model = modelPolicy == "single" ? singleModel : detailedModel
        rescanContinuousItem(id: id, options: .defaults(model: model))
    }

    func rescanContinuousItem(id: String, options: QueueRescanOptions) {
        guard manualQueueMutationAllowed else { return }
        guard let sessionID = queueSessionID,
              let item = reviewSession.item(id: id),
              item.revision > 0,
              item.revision < Int(Int32.max),
              let root = queueDecisionStore?.root else {
            message = "La foto aún no está lista para reanalizar."
            return
        }
        let decisionID = UUID().uuidString.lowercased()
        let artifact = QueueRescanArtifact(
            sessionID: sessionID,
            itemID: id,
            revision: item.revision,
            decisionID: decisionID,
            options: options
        )
        do {
            _ = try QueueRescanStore(root: root).write(artifact)
            if item.hasManualEdits, !options.resetEdits {
                _ = try QueueDraftStore(root: root).write(
                    QueueReviewDraftArtifact(
                        sessionID: sessionID,
                        itemID: id,
                        revision: item.revision + 1,
                        keywords: item.draft.keywords,
                        caption: item.draft.caption
                    )
                )
            }
            try worker.submit(.queueRescan(
                id: requestID(), sessionID: sessionID, itemID: id,
                revision: item.revision, decisionID: decisionID
            ))
            _ = reviewSession.queueRescan(
                id: id,
                resetManualEdits: options.resetEdits
            )
            message = "Reanalizando esta foto con las opciones privadas seleccionadas…"
        } catch {
            message = "No fue posible preparar el reanálisis privado para esta foto."
        }
    }

    private func submitQueueControl(_ request: WorkerRequest) {
        do {
            try worker.submit(request)
        } catch {
            message = error.localizedDescription
        }
    }

    private func finalizePendingContinuousDiscard(decisionID: String?) {
        guard let pending = pendingContinuousDiscard,
              decisionID == nil || pending.decisionID == decisionID else { return }
        pendingContinuousDiscardTask?.cancel()
        pendingContinuousDiscardTask = nil
        pendingContinuousDiscard = nil
        canUndoContinuousDiscard = false
        do {
            try worker.submit(.queueDiscard(
                id: requestID(),
                sessionID: pending.sessionID,
                itemID: pending.itemID,
                revision: pending.revision,
                decisionID: pending.decisionID
            ))
            message = "Foto descartada sin modificar Fotos."
        } catch {
            if queueSessionID == pending.sessionID {
                reviewSession.restoreDiscardedItem(pending.previousItem)
            }
            if let event = pending.deferredEvent {
                applyQueueItemEvent(event)
            }
            message = "No fue posible descartar la foto; volvió a la mesa."
        }
    }

    private func persistCurrentSettingsIfReady() {
        guard settingsPersistenceReady else { return }
        persistCurrentSettings()
    }

    private func persistCurrentSettings() {
        guard let settingsStore else { return }
        try? settingsStore.save(currentSettings)
    }

    private var currentSettings: AppSettings {
        AppSettings(
            limit: limit,
            modelPolicy: modelPolicy,
            singleModel: singleModel,
            fastModel: fastModel,
            detailedModel: detailedModel,
            appleMaps: appleMaps,
            includeCaption: includeCaption,
            randomSelection: randomSelection,
            autoAnalyze: autoAnalyze,
            analysisConcurrency: analysisConcurrency
        )
    }

    func preflight() {
        guard preflightRequestID == nil else { return }
        guard modelConfigurationError == nil else {
            message = modelConfigurationError ?? "Configura los modelos locales antes de continuar."
            return
        }
        let models = modelPolicy == "adaptive" ? [fastModel, detailedModel] : [singleModel]
        let id = requestID()
        preflightRequestID = id
        preparation.beginPreflight(models: models)
        do {
            try worker.submit(.preflight(id: id, models: models))
            message = "Comprobando Ollama y modelos locales…"
        } catch {
            preflightRequestID = nil
            preparation.failPreflight(code: "HELPER_UNAVAILABLE")
            message = preparation.statusMessage
        }
    }

    /// Re-check local prerequisites when the app is launched or restored.
    /// Preparation state is intentionally not persisted as authority, so a
    /// fresh process starts with pending Ollama/model checks even when the
    /// service is healthy. The preflight is safe before Photos access because
    /// it only checks Ollama and compiles the PhotoScript bridge; the queue
    /// itself remains blocked until PhotoKit access is granted.
    func bootstrapPreparationIfNeeded() {
        guard preflightRequestID == nil,
              preparation.ollama == .pending || preparation.models == .pending || preflightIsStale else {
            return
        }
        preflight()
    }

    func requestPhotosAccess() async {
        guard !photosAccessRequestInFlight else { return }
        photosAccessRequestInFlight = true
        defer { photosAccessRequestInFlight = false }
        await permissions.requestPhotosAccess()
        preparation.updatePhotos(permissions.photos)
        bootstrapPreparationIfNeeded()
    }

    func resolveContinuousReviewPreparation() {
        switch continuousReviewPreparationIssue {
        case .photosAccess:
            if permissions.photos == .notDetermined {
                Task { await requestPhotosAccess() }
            } else {
                _ = permissions.openPhotosSettings()
            }
        case .photosAutomation:
            _ = permissions.openAutomationSettings()
        case .ollama, .model, .photoScript, .helper:
            preflight()
        }
    }

    /// Refresh the public Photos permission after the user returns from
    /// System Settings. Apple Events/Automation cannot be checked passively;
    /// that state remains deferred until PhotoScript is actually exercised.
    /// If Photos is now readable, rerun the local Ollama/model preflight so
    /// the preparation card can converge without requiring an app restart.
    func refreshPreparation() {
        permissions.refresh()
        // Ask once when macOS has not decided yet so a fresh install can
        // reach the usable state without requiring a separate button click.
        // Existing authorized/denied decisions continue through the normal
        // status and Settings paths.
        if permissions.photos == .notDetermined {
            Task { await requestPhotosAccess() }
            return
        }
        preparation.updatePhotos(permissions.photos)
        guard preparation.photos == .ready else {
            message = preparation.statusMessage
            return
        }
        if preparation.ollama == .ready && preparation.models == .ready {
            message = preparation.statusMessage
        } else {
            preflight()
        }
    }

    func prepareAutomationRetry() {
        preparation.prepareAutomationRetry()
        message = preparation.isReady
            ? "Automatización queda pendiente de comprobación; inicia el dry-run para que PhotoScript la verifique."
            : preparation.statusMessage
    }

    func scan() {
        mutationRequiresManualReview = false
        preparingReview = false
        pendingReviewSelection = nil
        guard preparation.isReady else {
            message = "Completa Preparación antes de iniciar un análisis."
            return
        }
        guard modelConfigurationError == nil else {
            message = modelConfigurationError ?? "Configura los modelos locales antes de iniciar el dry-run."
            return
        }
        guard preflightMatchesCurrentModelConfiguration else {
            message = "La configuración de modelos cambió; vuelve a comprobar la preparación local antes de iniciar el dry-run."
            return
        }
        guard (1...500).contains(limit) else {
            message = "El límite debe estar entre 1 y 500."
            return
        }
        guard let runsRoot = Self.applicationSupportRunsRoot() else {
            message = "No fue posible resolver el almacenamiento privado de la aplicación."
            return
        }
        let model = modelPolicy == "single" ? singleModel : nil
        let request = WorkerRequest.scan(
            id: requestID(), limit: limit, modelPolicy: modelPolicy, model: model,
            fastModel: fastModel, detailedModel: detailedModel, appleMaps: appleMaps,
            includeCaption: includeCaption, randomSelection: randomSelection, runsRoot: runsRoot
        )
        // Keep progress attached to the request that actually ran. Once the
        // worker finishes, the form becomes editable for the next dry-run;
        // changing that next limit must not rewrite the completed run's scope.
        scanProgressLimit = limit
        events.removeAll()
        preview = nil
        manifestURL = nil
        reviewSourceAvailable = true
        safetyGate = ApplySafetyGate()
        submit(request, message: "Analizando en modo dry-run…")
    }

    func cancel() {
        guard worker.state.isRunning, worker.activeOperation != nil else { return }
        let operation = worker.activeOperation
        worker.cancelActive()
        safetyGate.isWorkerRunning = worker.state.isRunning
        message = WorkerProcess.cancellationMessage(for: operation)
    }

    @discardableResult
    func loadPreview(from url: URL) -> Bool {
        preparingReview = false
        pendingReviewSelection = nil
        do {
            let loaded = try RunManifestPreview.load(from: url)
            preview = loaded
            manifestURL = url
            reviewSourceAvailable = HistoryRunStore.reviewSourceAvailable(for: loaded, manifestURL: url)
            let summary = HistoryRunSummary(
                manifestURL: url,
                preview: loaded,
                localReviewSourceAvailable: reviewSourceAvailable
            )
            let isReviewedManifest = loaded.reviewedFromRunID != nil
            let canReview = isReviewedManifest
                && reviewSourceAvailable
                && summary.canContinueReviewedApply
            // Keep a reviewed manifest read-only even after its mutation is
            // terminal, so Preview can display the historical approved scope
            // without re-enabling any selection or apply path.
            review = ReviewSelection(photos: loaded.photos, reviewedManifest: isReviewedManifest)
            safetyGate.updateSelection(keywordCount: review.selectedKeywordCount, captionCount: review.selectedCaptionCount)
            if canReview {
                safetyGate.setReviewedManifest(
                    url,
                    allowsEmptySelectionForRetry: summary.isRetryingFailedApply
                )
                message = summary.reviewedManifestMessage
            } else {
                safetyGate.setReviewedManifest(nil)
                message = loaded.reviewedFromRunID != nil
                    ? summary.reviewedManifestMessage
                    : "Revisa las propuestas antes de aplicar."
            }
            return true
        } catch {
            message = "No se pudo leer el manifiesto local."
            return false
        }
    }

    func setPhoto(_ photo: PreviewPhoto, selected: Bool) {
        review.setPhoto(photo, selected: selected)
        safetyGate.updateSelection(keywordCount: review.selectedKeywordCount, captionCount: review.selectedCaptionCount)
        safetyGate.setReviewedManifest(nil)
    }

    func setKeyword(_ keyword: String, photo: PreviewPhoto, selected: Bool) {
        review.setKeyword(keyword, for: photo, selected: selected)
        safetyGate.updateSelection(keywordCount: review.selectedKeywordCount, captionCount: review.selectedCaptionCount)
        safetyGate.setReviewedManifest(nil)
    }

    func setAllKeywords(selected: Bool) {
        review.setAllKeywords(selected: selected)
        safetyGate.updateSelection(keywordCount: review.selectedKeywordCount, captionCount: review.selectedCaptionCount)
        safetyGate.setReviewedManifest(nil)
    }

    func setCaption(_ photo: PreviewPhoto, selected: Bool) {
        review.setCaption(uuid: photo.uuid, selected: selected)
        safetyGate.updateSelection(keywordCount: review.selectedKeywordCount, captionCount: review.selectedCaptionCount)
        safetyGate.setReviewedManifest(nil)
    }

    func requestApply() {
        guard !worker.state.isRunning else {
            message = "Espera a que finalice la operación en curso."
            return
        }
        guard !mutationRequiresManualReview else {
            message = "Revisa el run en Historial antes de intentar otra mutación."
            return
        }
        guard let preview, preview.canPrepareReview else {
            if let preview = self.preview, preview.reviewedFromRunID != nil {
                if let mutationBlockMessage = PhotosMutationAccess.blockingMessage(
                    for: preparation,
                    operation: .apply
                ) {
                    message = mutationBlockMessage
                    return
                }
                guard canContinueReviewedApply else {
                    message = reviewedManifestStatusMessage
                        ?? "Este manifiesto requiere revisión manual; ejecuta un dry-run nuevo antes de aplicar."
                    return
                }
                guard reviewSourceAvailable else {
                    message = reviewedManifestStatusMessage
                        ?? "Este manifiesto revisado queda solo para consulta; ejecuta un dry-run nuevo."
                    return
                }
                guard safetyGate.reviewedManifest != nil else {
                    message = "La selección revisada cambió; vuelve a preparar la aplicación antes de confirmar."
                    return
                }
                guard ReviewApplyAvailability.hasActionableSelection(
                    selectedChangeCount: review.selectedChangeCount,
                    isRetryingFailedApply: isRetryingFailedApply
                ), safetyGate.requestConfirmation() else {
                    message = "El manifiesto revisado no contiene cambios aprobables."
                    return
                }
                showApplyConfirmation = true
                return
            }
            message = "Este run no está listo para aplicar; ejecuta un dry-run nuevo o abre un run válido."
            return
        }
        if safetyGate.reviewedManifest != nil {
            if safetyGate.requestConfirmation() {
                showApplyConfirmation = true
            }
            return
        }
        guard review.selectedChangeCount > 0 else {
            message = "Selecciona al menos una keyword o caption."
            return
        }
        guard let manifestURL else {
            message = "No hay un manifiesto para revisar."
            return
        }
        let selections = Dictionary(uniqueKeysWithValues: review.payload.map { ($0.uuid, $0.keywords) })
        preparingReview = true
        pendingReviewSelection = review
        submit(
            .review(
                id: requestID(),
                manifest: manifestURL,
                selections: selections,
                captionSelections: review.captionSelectionPayload
            ),
            message: "Creando copia revisada y validando su digest…"
        )
    }

    func applyReviewedManifest() {
        if let mutationBlockMessage = PhotosMutationAccess.blockingMessage(
            for: preparation,
            operation: .apply
        ) {
            message = mutationBlockMessage
            return
        }
        guard let reviewed = safetyGate.confirmedManifest() else {
            message = "La confirmación ya no es válida; revisa el manifiesto nuevamente."
            return
        }
        showApplyConfirmation = false
        submit(.apply(id: requestID(), manifest: reviewed), message: "Aplicando únicamente los cambios aprobados…")
    }

    func rollback() {
        guard let manifestURL else { return }
        rollback(manifest: manifestURL)
    }

    func rollback(manifest url: URL) {
        guard !worker.state.isRunning else {
            message = "Espera a que finalice la operación en curso."
            return
        }
        if let mutationBlockMessage = PhotosMutationAccess.blockingMessage(
            for: preparation,
            operation: .rollback
        ) {
            message = mutationBlockMessage
            return
        }
        guard let loaded = try? RunManifestPreview.load(from: url) else {
            message = "No hay cambios verificados elegibles para rollback."
            return
        }
        let summary = HistoryRunSummary(
            manifestURL: url,
            preview: loaded,
            localReviewSourceAvailable: HistoryRunStore.reviewSourceAvailable(for: loaded, manifestURL: url)
        )
        guard summary.canRollback else {
            message = loaded.reviewedFromRunID != nil
                ? "El rollback está bloqueado: la fuente local no está disponible o no es verificable."
                : "No hay cambios verificados elegibles para rollback."
            return
        }
        manifestURL = url
        submit(.rollback(id: requestID(), manifest: url), message: "Ejecutando rollback verificado…")
    }

    private func submit(_ request: WorkerRequest, message: String) {
        do {
            try worker.submit(request)
            safetyGate.isWorkerRunning = worker.state.isRunning
            self.message = message
        } catch {
            if preparingReview {
                preparingReview = false
                pendingReviewSelection = nil
                safetyGate.setReviewedManifest(nil)
            }
            self.message = error.localizedDescription
        }
    }

    private func receive(_ event: WorkerEvent) {
        safetyGate.isWorkerRunning = worker.state.isRunning
        events.append(event)
        if (event.event == .completed || event.event == .error), let command = autonomyRequests.removeValue(forKey: event.id) {
            let success = event.event == .completed && event.exitCode == 0
            if command == .autonomyStatus {
                autonomyStatusRequestID = nil
                autonomyStatusReady = success
                if success {
                    autonomyError = nil
                    startContinuousReviewIfNeeded()
                } else {
                    autonomyError = "No se pudo comprobar el recorrido guardado. Vuelve a comprobar su estado antes de continuar."
                }
            } else {
                if command == .autonomyPause { autonomyPausePending = false }
                if command == .autonomyStart || command == .autonomyResume { autonomyActivationPending = false }
                if !success {
                    autonomyError = "No se pudo completar el control del recorrido. Comprueba su estado antes de continuar."
                    autonomyStatusReady = false
                }
            }
            return
        }
        switch event.event {
        case .autonomyCampaign:
            applyAutonomyCampaignEvent(event)
        case .autonomyActivity:
            applyAutonomyActivityEvent(event)
        case .started:
            break
        case .photoProgress:
            let identifier = event.uuid.map { String($0.prefix(8)) } ?? "actual"
            message = "Procesando foto \(identifier)…"
        case .queueSession:
            if event.sessionID == queueSessionID, let revision = event.revision {
                queueRevision = max(queueRevision, revision)
                if let state = event.state {
                    continuousControls.isPaused = state == "paused"
                }
            }
            message = event.state == "paused"
                ? "La cola de revisión está pausada."
                : "Actualizando la cola de revisión…"
        case .queueItem:
            if var pending = pendingContinuousDiscard,
               event.itemID == pending.itemID,
               event.revision == pending.revision {
                pending.deferredEvent = event
                pendingContinuousDiscard = pending
                return
            }
            applyQueueItemEvent(event)
        case .completed:
            if let request = continuousSaveRequests[event.id] {
                if !request.accepted {
                    resolveContinuousSaveFailure(requestID: event.id, rejected: false)
                }
                return
            }
            let completedQueueCommand = event.id == activeQueueRequestID
                ? activeQueueRequestCommand
                : nil
            if event.id == activeQueueRequestID {
                activeQueueRequestID = nil
                activeQueueRequestCommand = nil
            }
            if completedQueueCommand == .queueStop {
                clearQueueSession()
                continuousControls.isPaused = false
                message = "Sesión finalizada. Historial y rollback vuelven a estar disponibles."
                return
            }
            if event.id == preflightRequestID {
                preflightRequestID = nil
                preparation.completePreflight(
                    exitCode: event.exitCode ?? 2,
                    installedModels: event.models.map { Array($0.keys) } ?? [],
                    warningCodes: event.warningCodes ?? [],
                    errorCodes: event.errorCodes ?? [],
                    nextAction: event.nextAction,
                    safeInstruction: event.safeInstruction
                )
                message = preparation.statusMessage
                if preparation.isReady {
                    startContinuousReviewIfNeeded()
                }
                return
            }
            preparation.applyPermissionErrors(event.errorCodes ?? [])
            var handledManifest = false
            var manifestLoadFailed = false
            if let path = event.manifest {
                handledManifest = true
                let url = URL(fileURLWithPath: path)
                if preparingReview {
                    preparingReview = false
                    guard pendingReviewSelection == review else {
                        pendingReviewSelection = nil
                        safetyGate.setReviewedManifest(nil)
                        message = "La selección cambió durante la revisión; vuelve a preparar la aplicación."
                        return
                    }
                    pendingReviewSelection = nil
                    guard loadPreview(from: url), preview?.reviewedFromRunID != nil else {
                        safetyGate.setReviewedManifest(nil)
                        message = "No se pudo cargar el manifiesto revisado; vuelve a preparar la aplicación."
                        return
                    }
                    guard canContinueReviewedApply, reviewSourceAvailable else {
                        safetyGate.setReviewedManifest(nil)
                        message = reviewedManifestStatusMessage
                            ?? "El manifiesto revisado queda en solo lectura; ejecuta un dry-run nuevo antes de aplicar."
                        return
                    }
                    message = "Manifiesto revisado listo. Confirma la aplicación explícitamente."
                } else {
                    if !loadPreview(from: url) {
                        manifestLoadFailed = true
                        message = "No se pudo cargar el resultado local; abre Historial y ejecuta un dry-run nuevo."
                    }
                }
            }
            if event.safeInstruction != nil {
                message = "Falta un modelo local; usa el comando indicado y vuelve a comprobar."
            } else if manifestLoadFailed {
                // Keep the actionable recovery message from the local load
                // failure instead of replacing it with the event's generic
                // next action.
                break
            } else if handledManifest && event.exitCode == 0 {
                // A successfully loaded manifest already supplied the precise
                // review or history message; do not replace it with a generic
                // next-action summary.
                break
            } else if let specific = specificCompletionMessage(event) {
                message = specific
            } else if !handledManifest {
                message = event.exitCode == 0 ? "Operación completada." : "La operación terminó con errores revisables."
            } else if event.exitCode != 0 {
                message = "La operación terminó con errores revisables; consulta el estado del run."
            }
        case .error:
            if continuousSaveRequests[event.id] != nil {
                resolveContinuousSaveFailure(requestID: event.id, rejected: event.code == "QUEUE_DECISION_INVALID")
                return
            }
            let queueStartFailed = event.id == activeQueueRequestID
                && (activeQueueRequestCommand == .queueStart || activeQueueRequestCommand == .queueResume)
            if event.id == activeQueueRequestID {
                activeQueueRequestID = nil
                activeQueueRequestCommand = nil
            }
            if event.id == preflightRequestID {
                preflightRequestID = nil
                preparation.failPreflight(code: event.code ?? "INVALID_HELPER_EVENT")
                message = preparation.statusMessage
            } else {
                let permissionError = event.code == "PHOTOS_ACCESS_DENIED" || event.code == "PHOTOS_AUTOMATION_DENIED"
                if let code = event.code {
                    preparation.applyPermissionErrors([code])
                }
                if preparingReview {
                    preparingReview = false
                    pendingReviewSelection = nil
                    safetyGate.setReviewedManifest(nil)
                }
                if queueStartFailed {
                    clearQueueSession()
                }
                message = permissionError
                    ? preparation.statusMessage
                    : HumanErrorCopy.message(for: event.code ?? "")
            }
        }
    }

    private func applyQueueItemEvent(_ event: WorkerEvent) {
        guard event.sessionID == queueSessionID,
              let itemID = event.itemID,
              let revision = event.revision,
              let rawState = event.state,
              let state = QueueReviewItemState(rawValue: rawState) else {
            message = "La cola recibió un estado que requiere volver a abrir la sesión."
            return
        }
        let previousItem = reviewSession.item(id: itemID)
        guard revision >= (previousItem?.revision ?? 0) else { return }
        if previousItem?.revision == revision, previousItem?.state == .verified { return }
        if let id = currentContinuousSaveRequest[itemID],
           var request = continuousSaveRequests[id],
           request.sessionID == event.sessionID, request.revision == revision {
            if [.saveQueued, .saving, .verified, .failed, .uncertain].contains(state) {
                guard event.decisionID == request.decisionID else { return }
                request.accepted = true
                request.resolved = [.verified, .failed, .uncertain].contains(state)
                continuousSaveRequests[id] = request
                reviewSession.setSaveRequestStatus(id: itemID, status: nil)
            }
        }
        let previousState = previousItem?.state
        var manifestURL: URL?
        var photo: PreviewPhoto?
        if let path = event.manifest {
            let candidate = URL(fileURLWithPath: path)
            if let loaded = try? RunManifestPreview.load(from: candidate), loaded.photos.count == 1 {
                manifestURL = candidate
                photo = loaded.photos[0]
            }
        }
        reviewSession.applyQueueEvent(
            id: itemID,
            revision: revision,
            state: state,
            manifestURL: manifestURL,
            photo: photo
        )
        if let root = queueDecisionStore?.root,
           let draft = try? QueueDraftStore(root: root).load(
               sessionID: event.sessionID ?? "",
               itemID: itemID,
               currentRevision: revision
           ) {
            _ = reviewSession.editDraft(
                id: itemID,
                keywords: draft.keywords,
                caption: draft.caption
            )
        }
        if previousState != state {
            announceContinuousReviewStage(state)
        }
        message = reviewSession.item(id: itemID)?.statusCopy ?? state.statusMessage
    }

    private func announceContinuousReviewStage(_ state: QueueReviewItemState) {
        guard let text = ContinuousReviewAnnouncementCopy.text(for: state) else { return }
        postContinuousReviewAnnouncement(text)
    }

    private func postContinuousReviewAnnouncement(_ text: String) {
        continuousReviewAnnouncementSequence += 1
        continuousReviewAnnouncement = ContinuousReviewAnnouncement(
            sequence: continuousReviewAnnouncementSequence,
            text: text
        )
    }

    private func handleWorkerTermination(message: String, failureCode: String? = nil) {
        autonomyStatusReady = false
        autonomyStatusRequestID = nil
        autonomyRequests.removeAll()
        autonomyActivationPending = false
        autonomyPausePending = false
        autonomyCampaign?.state = .paused
        autonomyCampaign?.reason = .recovered
        autonomyActivity.removeAll()
        autonomyActivityRevisions.removeAll()
        settledAutonomyActivityKeys.removeAll()
        autonomyError = "La conexión se interrumpió. Comprueba el recorrido guardado; permanece desactivado."
        for id in Array(continuousSaveRequests.keys) {
            resolveContinuousSaveFailure(requestID: id, rejected: false)
        }
        mutationRequiresManualReview = true
        preparingReview = false
        pendingReviewSelection = nil
        showApplyConfirmation = false
        safetyGate.setReviewedManifest(nil)
        if preflightRequestID != nil {
            preflightRequestID = nil
            preparation.failPreflight(code: failureCode ?? "HELPER_UNAVAILABLE")
        } else if let failureCode,
                  [
            "HELPER_UNAVAILABLE",
            "PHOTOSCRIPT_UNAVAILABLE",
            "INVALID_HELPER_EVENT",
            "RUNS_ROOT_INVALID"
                  ].contains(failureCode) {
            // A scan can fail before emitting a manifest. Keep Preparation in
            // sync with the authoritative helper failure so the next screen
            // offers a safe re-check instead of appearing ready.
            preparation.failPreflight(code: failureCode)
        }
        self.message = message
    }

    private func requestID() -> String { UUID().uuidString.lowercased() }

    func bootstrapAutonomyStatusIfNeeded() {
        guard !autonomyStatusReady, autonomyStatusRequestID == nil else { return }
        let request = WorkerRequest.autonomyStatus(id: requestID())
        autonomyStatusRequestID = request.id
        autonomyError = nil
        submitAutonomy(request)
    }

    func startAutonomy() {
        guard autonomyStatusReady, !autonomyControlPending, manualQueueMutationAllowed,
              preparation.isReady, preflightMatchesCurrentModelConfiguration,
              let runs = queueRunsRoot, let settingsStore else {
            autonomyError = "Comprueba la preparación y el estado del recorrido antes de activarlo."
            return
        }
        do { try settingsStore.save(currentSettings) } catch {
            autonomyError = "No fue posible guardar la configuración privada del recorrido."
            return
        }
        let campaignID = requestID()
        autonomyCampaignID = campaignID
        autonomyCampaign = nil
        autonomyActivity.removeAll()
        autonomyActivityRevisions.removeAll()
        settledAutonomyActivityKeys.removeAll()
        autonomyActivationPending = true
        submitAutonomy(.autonomyStart(id: requestID(), campaignID: campaignID, decisionID: requestID(), runsRoot: runs, settings: settingsStore.fileURL, limit: AutonomyPilotPolicy.maximumPhotos))
    }

    func pauseAutonomy() {
        guard let campaignID = autonomyCampaignID, !autonomyPausePending,
              autonomyActivationPending || autonomyCampaign?.state.ownsHelper == true else { return }
        autonomyPausePending = true
        submitAutonomy(.autonomyPause(id: requestID(), campaignID: campaignID, decisionID: requestID()))
    }

    func resumeAutonomy() {
        guard autonomyStatusReady, !autonomyControlPending,
              let campaign = autonomyCampaign, campaign.state == .paused else { return }
        autonomyError = "Este recorrido detenido no se reanuda en la beta. Inicia un nuevo piloto limitado a \(AutonomyPilotPolicy.maximumPhotos) fotos."
    }

    private func submitAutonomy(_ request: WorkerRequest) {
        autonomyError = nil
        autonomyRequests[request.id] = request.command
        do { try worker.submit(request) } catch {
            autonomyRequests.removeValue(forKey: request.id)
            autonomyStatusRequestID = nil
            autonomyActivationPending = false
            autonomyPausePending = false
            autonomyStatusReady = false
            autonomyError = "No fue posible contactar al helper. Vuelve a comprobar el estado del recorrido."
        }
    }

    private func applyAutonomyCampaignEvent(_ event: WorkerEvent) {
        guard let snapshot = event.autonomyCampaign,
              snapshot.campaignID == autonomyCampaignID || event.id == autonomyStatusRequestID else { return }
        if let previous = autonomyCampaign, previous.campaignID == snapshot.campaignID,
           previous.revision > snapshot.revision { return }
        autonomyCampaignID = snapshot.campaignID
        autonomyCampaign = snapshot
        if snapshot.state == .paused || snapshot.state == .completed {
            autonomyActivity.removeAll()
            autonomyActivityRevisions.removeAll()
            settledAutonomyActivityKeys.removeAll()
        }
    }

    private func applyAutonomyActivityEvent(_ event: WorkerEvent) {
        guard let activity = event.autonomyActivity,
              activity.campaignID == autonomyCampaignID,
              autonomyCampaign?.state.ownsHelper == true || autonomyActivationPending else { return }
        let key = "\(activity.campaignID)-\(activity.position)"
        if settledAutonomyActivityKeys.contains(key) { return }
        if let knownRevision = autonomyActivityRevisions[key], knownRevision >= activity.revision { return }
        autonomyActivityRevisions[key] = activity.revision
        if activity.state.isVisible {
            autonomyActivity[key] = activity
        } else {
            autonomyActivity.removeValue(forKey: key)
            settledAutonomyActivityKeys.insert(key)
        }
    }

    private var modelConfigurationError: String? {
        ScanModelConfiguration(
            policy: modelPolicy,
            singleModel: singleModel,
            fastModel: fastModel,
            detailedModel: detailedModel
        ).validationMessage
    }

    private func specificCompletionMessage(_ event: WorkerEvent) -> String? {
        let next = HumanNextActionCopy.message(for: event.nextAction)
        let warnings = event.warningCodes ?? []
        let errors = event.errorCodes ?? []
        guard next != nil || !warnings.isEmpty || !errors.isEmpty else { return nil }
        if errors.contains("PHOTOS_ACCESS_DENIED") || errors.contains("PHOTOS_AUTOMATION_DENIED") {
            return preparation.statusMessage
        }
        var parts: [String] = []
        if let next { parts.append("Siguiente acción: \(next).") }
        if !errors.isEmpty {
            parts.append(errors.map(HumanErrorCopy.message(for:)).joined(separator: " "))
        }
        if !warnings.isEmpty {
            parts.append(warnings.map(HumanErrorCopy.message(for:)).joined(separator: " "))
        }
        return parts.joined(separator: " ")
    }

    private static func applicationSupportRunsRoot(fileManager: FileManager = .default) -> URL? {
        fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
            .appendingPathComponent("Photos Local Keyword Indexer", isDirectory: true)
            .appendingPathComponent("runs", isDirectory: true)
    }
}
