import AppKit
import SwiftUI
import Combine

private enum PreviewScenario: String, CaseIterable, Identifiable {
    case compact
    case populated
    case longContent = "long-content"
    case empty
    case blocked
    case checking

    var id: String { rawValue }
    var title: String {
        switch self {
        case .compact: return "Tarjetas listas"
        case .populated: return "Estados mixtos"
        case .longContent: return "Textos largos"
        case .empty: return "Mesa vacía"
        case .blocked: return "Preparación bloqueada"
        case .checking: return "Comprobando"
        }
    }
}

private enum PreviewSaveOutcome: String, CaseIterable, Identifiable {
    case verified = "Verificado"
    case failed = "Fallido"
    case uncertain = "Incierto"
    var id: String { rawValue }
}

@MainActor
private final class PreviewState: ObservableObject {
    @Published var controls = ContinuousReviewControls.defaults
    @Published var session = ReviewSessionStore(items: [])
    @Published var scenario: PreviewScenario
    @Published var saveOutcome = PreviewSaveOutcome.verified
    @Published var message = "Datos ficticios · todas las acciones ocurren en memoria."
    @Published var discardedItem: QueueReviewItem?
    private var generation = UUID()

    init(scenario: PreviewScenario) {
        self.scenario = scenario
        reset()
    }

    var preparation: ContinuousReviewPreparation {
        switch scenario {
        case .blocked: return .blocked(.model)
        case .checking: return .checking
        default: return .ready
        }
    }

    func reset() {
        generation = UUID()
        session = Self.fixtures(scenario)
        controls = .defaults
        discardedItem = nil
        message = "Datos ficticios · todas las acciones ocurren en memoria."
    }

    func edit(_ item: QueueReviewItem, keywords: [String], caption: String?) -> Bool {
        session.editDraft(id: item.id, keywords: keywords, caption: caption)
    }

    func save(_ item: QueueReviewItem) {
        guard preparation.allowsQueueWork,
              let current = session.item(id: item.id),
              ContinuousReviewActions(item: current).canSave else { return }
        let requestGeneration = generation
        let outcome = saveOutcome
        session.setSaveRequestStatus(id: item.id, status: .sending)
        message = "Guardado simulado: solicitud enviada."
        Task { @MainActor [weak self] in
            try? await Task.sleep(for: .milliseconds(350))
            guard let self, self.generation == requestGeneration else { return }
            self.session.setSaveRequestStatus(id: item.id, status: nil)
            guard self.session.queuePersistence(id: item.id) else { return }
            try? await Task.sleep(for: .milliseconds(600))
            guard self.generation == requestGeneration,
                  self.session.beginPersistence(id: item.id) else { return }
            try? await Task.sleep(for: .milliseconds(900))
            guard self.generation == requestGeneration else { return }
            switch outcome {
            case .verified: _ = self.session.completePersistence(id: item.id)
            case .failed: _ = self.session.markFailed(id: item.id)
            case .uncertain: _ = self.session.markBlocked(id: item.id)
            }
            self.message = "Guardado simulado: \(outcome.rawValue.lowercased())."
        }
    }

    func discard(_ item: QueueReviewItem) {
        guard let current = session.item(id: item.id),
              ContinuousReviewActions(item: current).canDiscard,
              session.discard(id: item.id) else { return }
        discardedItem = current
        message = "Descarte simulado; puedes deshacerlo."
    }

    func rescan(_ item: QueueReviewItem, options: QueueRescanOptions) {
        guard preparation.allowsQueueWork,
              let current = session.item(id: item.id),
              ContinuousReviewActions(item: current).canRescan else { return }
        let requestGeneration = generation
        session.applyQueueEvent(id: current.id, revision: current.revision + 1, state: .analyzing,
                                manifestURL: current.manifestURL, photo: current.photo)
        message = "Reanálisis ficticio: estado cambiado solo en memoria."
        Task { @MainActor [weak self] in
            try? await Task.sleep(for: .milliseconds(900))
            guard let self, self.generation == requestGeneration else { return }
            self.session.applyQueueEvent(id: current.id, revision: current.revision + 1, state: .ready,
                                         manifestURL: current.manifestURL, photo: current.photo)
        }
    }

    func undoDiscard() {
        guard let discardedItem else { return }
        session.restoreDiscardedItem(discardedItem)
        self.discardedItem = nil
        message = "Descarte simulado deshecho."
    }

    static func fixtures(_ scenario: PreviewScenario) -> ReviewSessionStore {
        guard scenario != .empty else { return ReviewSessionStore(items: []) }
        var session = ReviewSessionStore(items: [])
        let names = ["Luz de la tarde", "Entre los árboles", "Un paseo tranquilo", "Horizonte abierto",
                     "Sombras en la pared", "Día de lluvia", "Una pausa", "Líneas y formas",
                     "Reflejos", "El jardín", "Un camino", "Vista de la ciudad"]
        let states: [QueueReviewItemState] = scenario == .compact ? [.ready, .edited, .ready, .ready, .ready, .ready] : [
            .ready, .edited, .analyzing, .ready, .failed, .uncertain,
            .saveQueued, .saving, .queued, .verified, .discarded, .ready,
            .ready, .discovered, .preparing, .validating,
        ]
        for (index, state) in states.enumerated() {
            let id = "preview-photo-\(index + 1)"
            let long = scenario == .longContent
            let keywords = long
                ? ["arquitectura contemporánea de líneas geométricas", "iluminación natural al atardecer",
                   "reflejos sobre una superficie de agua tranquila", "composición de formas orgánicas",
                   "vegetación abundante de hojas grandes", "textura de piedra y materiales naturales",
                   "perspectiva de un espacio abierto", "contraste entre luces y sombras"]
                : ["paisaje", "luz natural", "exterior", "tranquilidad"]
            let caption = long
                ? String(String(repeating: "Una escena ficticia con luz suave y detalles naturales invita a observar la composición. ", count: 4).prefix(240))
                : "Una escena ficticia con luz suave y detalles naturales."
            let photo = PreviewPhoto(
                uuid: id,
                photosLocalIdentifier: nil,
                title: long ? String(repeating: names[index % names.count] + " en un entorno de arquitectura y naturaleza · ", count: 4) : names[index % names.count],
                date: "2026-09-12T16:30:00",
                existingKeywords: [],
                proposedKeywords: keywords,
                confidence: state == .failed ? nil : 0.89,
                modelUsed: state == .failed ? nil : "qwen3-vl:4b",
                modelReason: "no_location",
                state: state == .failed ? "analysis_failed" : "ready",
                proposedCaption: caption,
                errors: state == .failed ? [ManifestPreviewError(stage: "analysis", code: "ANALYSIS_FAILED")] : [],
                technicalTrace: PreviewTechnicalTrace(
                    promptEffective: "Ejemplo ficticio para revisar la presentación del inspector.",
                    promptVersion: "preview", promptSHA256: "preview", ollamaVersion: "preview",
                    usedGPS: false, usedAppleMaps: false, usedLandmark: false, placeContext: [],
                    durationsMilliseconds: ["inference": 1250, "total": 1600]
                )
            )
            session.applyQueueEvent(
                id: id, revision: 1, state: state,
                manifestURL: URL(string: "ui-preview://synthetic/\(index)/manifest.json")!,
                photo: photo
            )
            if state == .edited {
                _ = session.editDraft(id: id, keywords: keywords + (long ? [] : ["árboles"]), caption: caption)
            }
            if index == 12 {
                session.setSaveRequestStatus(id: id, status: .sending)
            }
            if index == 11 {
                session.setSaveRequestStatus(id: id, status: .unconfirmed)
            }
        }
        return session
    }

    static func verifyFixtures() {
        for scenario in PreviewScenario.allCases {
            let fixture = fixtures(scenario)
            precondition(fixture.items.allSatisfy { $0.photo?.photosLocalIdentifier == nil })
            precondition(fixture.items.allSatisfy { $0.manifestURL?.scheme == "ui-preview" })
            precondition(fixture.items.allSatisfy { $0.draft.keywords.count <= 8 })
            precondition(fixture.items.allSatisfy { ($0.draft.caption?.count ?? 0) <= 240 })
        }
        var fixture = fixtures(.populated)
        precondition(fixture.editDraft(id: "preview-photo-1", keywords: ["edición ficticia"], caption: nil))
        precondition(fixture.queuePersistence(id: "preview-photo-1"))
        precondition(fixture.beginPersistence(id: "preview-photo-1"))
        precondition(fixture.completePersistence(id: "preview-photo-1"))
        precondition(!ContinuousReviewTable(session: fixture).visibleItems.contains { $0.id == "preview-photo-1" })
        precondition(ContinuousReviewActions(item: fixture.item(id: "preview-photo-12")!).canSave == false)
        print("ui_review_preview:PASS:synthetic_fixtures_no_photo_identifiers_no_file_manifests")
    }
}

// This host is intentionally outside the distributed application's sources.
// Worker requests end here: there is no Process, URLSession, PhotoScript or
// PhotoKit operation in this provider, including the autonomous-save controls.
@MainActor
private final class PreviewWorker: WorkerClient {
    private let subject = CurrentValueSubject<WorkerProcessState, Never>(.ready)
    var state: WorkerProcessState { subject.value }
    var statePublisher: AnyPublisher<WorkerProcessState, Never> { subject.eraseToAnyPublisher() }
    private(set) var activeOperation: WorkerCommand?
    var onEvent: ((WorkerEvent) -> Void)?
    private(set) var submitted: [WorkerRequest] = []
    private var campaignID = "fictitious-campaign"
    private var campaignRevision = 0
    var campaignState: AutonomyCampaignState?
    let root: URL

    init(root: URL) { self.root = root }

    func submit(_ request: WorkerRequest) throws {
        let data = try JSONEncoder().encode(request)
        let object = try JSONSerialization.jsonObject(with: data) as! [String: Any]
        let payload = object["payload"] as? [String: Any] ?? [:]
        for field in ["runs_root", "settings_path", "manifest", "decision_path", "rescan_path"] {
            if let path = payload[field] as? String {
                precondition(path.hasPrefix(root.path + "/"), "Preview request escaped temporary storage")
            }
        }
        submitted.append(request)
        activeOperation = request.command
        subject.send(.running(requestID: request.id))
        Task { @MainActor [weak self] in
            await Task.yield()
            guard let self else { return }
            if let id = payload["campaign_id"] as? String { self.campaignID = id }
            switch request.command {
            case .autonomyStatus:
                if let campaignState { self.emitCampaign(requestID: request.id, state: campaignState) }
                self.complete(request.id)
            case .autonomyStart, .autonomyResume:
                self.emitCampaign(requestID: request.id, state: .running)
                self.complete(request.id)
            case .autonomyPause:
                self.emitCampaign(requestID: request.id, state: .paused)
                self.complete(request.id)
            case .preflight:
                self.emit(["id": request.id, "event": "completed", "exit_code": 0,
                           "installed_models": ["qwen3-vl:4b", "qwen3-vl:8b"], "next_action": "none"])
            default:
                // Mutating and analysis requests are deliberately not executed.
                // Real confirmation-sheet buttons in this host use separate
                // in-memory callbacks; ordinary views remain fail-closed.
                self.emit(["id": request.id, "event": "error", "code": "WORKER_OPERATION_FAILED"])
            }
        }
    }

    func cancelActive() { subject.send(.ready); activeOperation = nil }

    func setCampaign(_ state: AutonomyCampaignState?) {
        campaignState = state
        guard state != nil else { return }
        emitCampaign(requestID: "fictitious-status", state: state!)
    }

    private func emitCampaign(requestID: String, state: AutonomyCampaignState) {
        campaignState = state
        campaignRevision += 1
        let completed = state == .completed
        emit(["id": requestID, "event": "autonomy_campaign", "campaign_id": campaignID,
              "revision": campaignRevision, "state": state.rawValue, "total": 12,
              "examined": completed ? 12 : 6, "analyzed": completed ? 10 : 5,
              "saved": completed ? 8 : 3, "no_change": 2, "attention": completed ? 2 : 1,
              "remaining": completed ? 0 : 6, "in_flight": state == .running ? 1 : 0,
              "invalid_count": 1, "reason": state == .paused ? "recovered" : "none"])
    }

    private func complete(_ id: String) {
        emit(["id": id, "event": "completed", "exit_code": 0, "next_action": "none"])
    }

    private func emit(_ object: [String: Any]) {
        do {
            let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
            let event = try JSONDecoder().decode(WorkerEvent.self, from: data)
            if event.event == .completed || event.event == .error {
                subject.send(.ready)
                activeOperation = nil
            }
            onEvent?(event)
        } catch { preconditionFailure("Invalid fictitious event: \(error)") }
    }
}

@MainActor
private final class PreviewEnvironment: ObservableObject {
    let root: URL
    let runsRoot: URL
    let worker: PreviewWorker
    let model: AppModel
    let updates: UpdateService
    let manifests: [String: URL]
    let permissionAudit: PermissionAudit

    final class PermissionAudit {
        var reads = 0
        var requests = 0
        var opened: [PermissionSettingsRoute] = []
    }

    init(scenario: PreviewScenario) throws {
        root = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .resolvingSymlinksInPath().appendingPathComponent("fictitious-photos-ui-\(UUID().uuidString)", isDirectory: true)
        runsRoot = root.appendingPathComponent("runs", isDirectory: true)
        try FileManager.default.createDirectory(at: runsRoot, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: root.path)
        manifests = try Self.makeManifests(root: runsRoot)
        worker = PreviewWorker(root: root)
        permissionAudit = PermissionAudit()
        let audit = permissionAudit
        let permission = PermissionChecker(
            readPhotosState: { audit.reads += 1; return scenario == .blocked ? .denied : .authorized },
            requestPhotosState: { audit.requests += 1; return .authorized },
            openSettings: { route in audit.opened.append(route); return true }
        )
        var preparation = PreparationState()
        preparation.updatePhotos(permission.photos)
        preparation.beginPreflight(models: ["qwen3-vl:4b", "qwen3-vl:4b"])
        if scenario != .checking {
            preparation.completePreflight(exitCode: scenario == .blocked ? 1 : 0,
                installedModels: ["qwen3-vl:4b", "qwen3-vl:8b"], warningCodes: [],
                errorCodes: scenario == .blocked ? ["PHOTOS_ACCESS_DENIED"] : [],
                nextAction: scenario == .blocked ? "grant_photos_access" : "none", safeInstruction: nil)
        }
        var settings = AppSettings.defaults
        settings.autoAnalyze = false
        model = AppModel(worker: worker, permissions: permission, initialPreparation: preparation,
            persistedSettings: settings, persistSettings: true,
            settingsStore: AppSettingsStore(fileURL: root.appendingPathComponent("settings.json")),
            queueRunsRoot: runsRoot,
            queueDecisionStore: QueueDecisionStore(root: root.appendingPathComponent("queue-sessions")))
        updates = UpdateService()
        precondition(!updates.isConfigured, "The fictitious bundle must not contain an update feed")
        precondition(model.loadPreview(from: manifests["Propuestas"]!))
        model.bootstrapAutonomyStatusIfNeeded()
    }

    deinit { try? FileManager.default.removeItem(at: root) }

    private static func makeManifests(root: URL) throws -> [String: URL] {
        var result: [String: URL] = [:]
        for (index, title) in ["Propuestas", "Solo consulta", "Atención"].enumerated() {
            let runID = "fictitious-run-\(index)"
            let folder = root.appendingPathComponent(runID, isDirectory: true)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            var photos: [[String: Any]] = []
            for row in 0..<4 {
                var photo: [String: Any] = [
                    "uuid": "fictitious-history-\(row)", "photos_local_identifier": NSNull(),
                    "title": ["Luz de la tarde", "Jardín tranquilo", "Formas en la pared", "Reflejos en el agua"][row],
                    "date": "2026-09-12T10:00:00Z", "existing_keywords": ["ficticia"],
                    "proposed_keywords": ["luz natural", "exterior", "composición"],
                    "confidence": 0.91, "model_used": "qwen3-vl:4b", "model_reason": "no_location",
                    "scan_state": "ready", "apply_state": "not_run", "rollback_state": "not_run",
                    "proposed_caption": "Escena de prueba creada para la revisión visual.",
                    "caption_state": "proposed", "errors": []]
                if index == 2 && row == 0 {
                    photo["scan_state"] = "analysis_failed"
                    photo["proposed_keywords"] = []
                    photo["proposed_caption"] = NSNull()
                    photo["caption_state"] = "not_requested"
                    photo["errors"] = [["stage": "analysis", "code": "ANALYSIS_FAILED"]]
                }
                photos.append(photo)
            }
            var manifest: [String: Any] = ["schema_version": 3, "run_id": runID,
                "created_at": "2026-09-12T10:0\(index):00Z", "scan_status": index == 2 ? "ready_with_errors" : "ready",
                "selection": ["strategy": "recent", "access": "limited", "captions_requested": true],
                "model": ["policy": "adaptive", "fast_name": "qwen3-vl:4b", "detailed_name": "qwen3-vl:4b"], "policy": ["id": "fictitious"],
                "scan_digest": String(repeating: "a", count: 64), "photos": photos]
            if index == 1 { manifest["reviewed_from_run_id"] = "fictitious-missing-source" }
            let url = folder.appendingPathComponent("manifest.json")
            try JSONSerialization.data(withJSONObject: manifest, options: [.sortedKeys, .withoutEscapingSlashes]).write(to: url)
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
            result[title] = url
        }
        let approvedDirectory = root.appendingPathComponent("fictitious-approved", isDirectory: true)
        try FileManager.default.createDirectory(at: approvedDirectory, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        var approved = try JSONSerialization.jsonObject(with: Data(contentsOf: result["Propuestas"]!)) as! [String: Any]
        approved["run_id"] = "fictitious-approved"
        approved["reviewed_from_run_id"] = "fictitious-run-0"
        approved["source_scan_digest"] = String(repeating: "a", count: 64)
        let approvedURL = approvedDirectory.appendingPathComponent("manifest.json")
        try JSONSerialization.data(withJSONObject: approved, options: [.sortedKeys, .withoutEscapingSlashes]).write(to: approvedURL)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: approvedURL.path)
        result["Revisado disponible"] = approvedURL
        return result
    }

    func showCampaign(_ status: AutonomyCampaignState) {
        if model.autonomyCampaign == nil { model.startAutonomy() }
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(30))
            worker.setCampaign(status)
        }
    }

    func verifyIsolation() async throws {
        precondition(root.path.hasPrefix(URL(fileURLWithPath: NSTemporaryDirectory()).resolvingSymlinksInPath().path))
        precondition(permissionAudit.reads == 1 && permissionAudit.requests == 0 && permissionAudit.opened.isEmpty)
        await model.permissions.requestPhotosAccess()
        precondition(permissionAudit.requests == 1)
        precondition(model.permissions.openPhotosSettings() && model.permissions.openAutomationSettings())
        precondition(permissionAudit.opened == [.photos, .automation])
        for url in manifests.values {
            let preview = try RunManifestPreview.load(from: url)
            precondition(preview.photos.allSatisfy { $0.photosLocalIdentifier == nil })
            precondition(model.loadPreview(from: url))
        }
        precondition(model.loadPreview(from: manifests["Revisado disponible"]!))
        precondition(model.canContinueReviewedApply && model.review.approvedPhotoCount == 4)
        let history = HistoryRunStore.loadResult(from: runsRoot, expectedRoot: runsRoot)
        precondition(history.runs.count == manifests.count)
        let importRoot = root.appendingPathComponent("import-check", isDirectory: true)
        try FileManager.default.createDirectory(at: importRoot, withIntermediateDirectories: true,
                                                attributes: [.posixPermissions: 0o700])
        let imported = try HistoryRunStore.importManifest(from: manifests["Propuestas"]!, to: importRoot)
        precondition(imported.path.hasPrefix(importRoot.path + "/"))
        precondition(HistoryRunStore.loadResult(from: importRoot, expectedRoot: importRoot).runs.count == 1)
        precondition(HistoryRunStore.loadResult(from: runsRoot, expectedRoot: runsRoot).runs.count == manifests.count)
        await Task.yield()
        precondition(worker.submitted.allSatisfy { $0.command == .autonomyStatus })
        precondition(FileManager.default.fileExists(atPath: root.appendingPathComponent("settings.json").path))
        print("preview_isolation:PASS:fake_permissions_fake_worker_temporary_settings_history_import_no_update_feed")
    }
}

private enum PreviewRoute: String, CaseIterable, Identifiable {
    case setup = "Preparación", review = "Revisión", history = "Historial", settings = "Configuración", legacy = "Revisión histórica"
    var id: String { rawValue }
    var symbol: String {
        switch self {
        case .setup: return "checkmark.shield"
        case .review: return "checklist"
        case .history: return "clock.arrow.circlepath"
        case .settings: return "gearshape"
        case .legacy: return "doc.text.magnifyingglass"
        }
    }
}

private enum PreviewSheet: String, Identifiable { case apply, rollback; var id: String { rawValue } }

private struct PreviewWorkspace: View {
    @Environment(\.accessibilityReduceMotion) private var systemReduceMotion
    @Environment(\.colorSchemeContrast) private var systemContrast
    @ObservedObject var state: PreviewState
    @ObservedObject var fixtures: PreviewEnvironment
    @ObservedObject private var model: AppModel
    @State private var appearance: String
    @State private var reduceMotion: Bool
    @State private var increasedContrast = false
    @State private var route: PreviewRoute
    @State private var columns: NavigationSplitViewVisibility
    @State private var wasWide: Bool
    @State private var sheet: PreviewSheet?

    init(state: PreviewState, fixtures: PreviewEnvironment, appearance: String, reduceMotion: Bool,
         route: PreviewRoute, width: CGFloat, sidebar: String) {
        self.state = state
        self.fixtures = fixtures
        self.model = fixtures.model
        _appearance = State(initialValue: appearance)
        _reduceMotion = State(initialValue: reduceMotion)
        _route = State(initialValue: route)
        _wasWide = State(initialValue: width >= 1000)
        _columns = State(initialValue: sidebar == "open" ? .all : sidebar == "closed" ? .detailOnly : width >= 1000 ? .all : .detailOnly)
    }

    var body: some View {
        GeometryReader { geometry in
            NavigationSplitView(columnVisibility: $columns) {
                List(selection: $route) {
                    ForEach(PreviewRoute.allCases) { item in
                        Label(item.rawValue, systemImage: item.symbol).tag(item)
                    }
                }
                .navigationTitle("Vista ficticia")
                .navigationSplitViewColumnWidth(min: 180, ideal: 200, max: 240)
            } detail: {
                destination
            }
            .toolbar {
                ToolbarItem(placement: .primaryAction) {
                    Menu {
                        Picker("Pantalla", selection: $route) {
                            ForEach(PreviewRoute.allCases) { Text($0.rawValue).tag($0) }
                        }
                        Picker("Escenario", selection: $state.scenario) {
                            ForEach(PreviewScenario.allCases) { Text($0.title).tag($0) }
                        }
                        Picker("Resultado simulado", selection: $state.saveOutcome) {
                            ForEach(PreviewSaveOutcome.allCases) { Text($0.rawValue).tag($0) }
                        }
                        Menu("Tamaño de contenido") {
                            Text("Actual: \(Int(geometry.size.width)) × \(Int(geometry.size.height))")
                            ForEach(["760x560", "900x620", "1100x760", "1280x820", "1600x1000"], id: \.self) { size in
                                Button(size) { resize(size) }
                            }
                        }
                        Button(columns == .detailOnly ? "Mostrar barra lateral" : "Ocultar barra lateral") {
                            columns = columns == .detailOnly ? .all : .detailOnly
                        }
                        Picker("Apariencia", selection: $appearance) {
                            Text("Clara").tag("light"); Text("Oscura").tag("dark")
                        }
                        Toggle("Desactivar animaciones (simulado)", isOn: $reduceMotion)
                        Toggle("Contraste aumentado", isOn: $increasedContrast)
                        Menu("Recorrido ficticio") {
                            ForEach([AutonomyCampaignState.preparing, .running, .pausing, .paused, .completed], id: \.rawValue) { status in
                                Button(status.rawValue) { fixtures.showCampaign(status) }
                            }
                        }
                        Menu("Histórico ficticio") {
                            ForEach(fixtures.manifests.keys.sorted(), id: \.self) { title in
                                Button(title) {
                                    _ = fixtures.model.loadPreview(from: fixtures.manifests[title]!)
                                    route = .legacy
                                }
                            }
                        }
                        Button("Hoja de aplicación ficticia") { sheet = .apply }
                        Button("Hoja de rollback ficticio") { sheet = .rollback }
                        Divider()
                        Button("Restablecer mesa", action: state.reset)
                        Text("Accesibilidad efectiva: movimiento reducido \(systemReduceMotion ? "sí" : "no"), contraste \(systemContrast == .increased ? "aumentado" : "normal")")
                        Text(state.message)
                    } label: {
                        Label("Pruebas ficticias", systemImage: "slider.horizontal.3")
                    }
                }
            }
            .onChange(of: geometry.size.width) { _, width in
                let wide = width >= 1000
                if wide != wasWide { columns = wide ? .all : .detailOnly; wasWide = wide }
            }
        }
        .transaction { transaction in
            if reduceMotion { transaction.animation = nil; transaction.disablesAnimations = true }
        }
        .onChange(of: appearance, initial: true) { _, _ in updateAppearance() }
        .onChange(of: increasedContrast) { _, _ in updateAppearance() }
        .environmentObject(fixtures.model)
        .environmentObject(fixtures.updates)
        .environment(\.openURL, OpenURLAction { _ in
            state.message = "Apertura externa interceptada por la vista ficticia."
            return .handled
        })
        .onChange(of: state.scenario) { _, _ in state.reset() }
        .sheet(item: $sheet) { kind in
            switch kind {
            case .apply:
                let photos = PreviewState.fixtures(state.scenario).items.compactMap(\.photo).prefix(3)
                ApplyConfirmationSheet(detail: ReviewConfirmationDetail(selection: ReviewSelection(photos: Array(photos), reviewedManifest: true), photos: Array(photos)),
                    onCancel: { sheet = nil }, onConfirm: { state.message = "Aplicación confirmada solo en memoria."; sheet = nil })
            case .rollback:
                RollbackConfirmationSheet(detail: RollbackConfirmationDetail(preview: rollbackFixture),
                    onCancel: { sheet = nil }, onConfirm: { state.message = "Rollback confirmado solo en memoria."; sheet = nil })
            }
        }
    }

    @ViewBuilder private var destination: some View {
        switch route {
        case .setup: OnboardingView(onContinue: { route = .review })
        case .review:
            VStack(spacing: 0) {
                ContinuousReviewView(controls: $state.controls, session: $state.session,
                    availableModels: ["qwen3-vl:4b", "qwen3-vl:8b"], preparation: state.preparation,
                    manualQueueMutationAllowed: model.manualQueueMutationAllowed,
                    onPreparationAction: { state.scenario = .populated; state.reset() },
                    onDraftChange: state.edit, onSave: state.save, onDiscard: state.discard,
                    canUndoDiscard: state.discardedItem != nil, onUndoDiscard: state.undoDiscard,
                    onStop: { state.controls.isPaused = true; state.message = "Sesión ficticia finalizada." },
                    onRescan: state.rescan,
                    autonomy: model.autonomyReviewPresentation,
                    onAutonomyStart: model.startAutonomy,
                    onAutonomyPause: model.pauseAutonomy,
                    onAutonomyResume: model.resumeAutonomy,
                    onAutonomyRefresh: model.bootstrapAutonomyStatusIfNeeded)
            }
        case .history:
            RunDetailView(runsRoot: fixtures.runsRoot, onReview: { url in
                precondition(url.path.hasPrefix(fixtures.runsRoot.path + "/"))
                _ = fixtures.model.loadPreview(from: url); route = .legacy
            }, onStartScan: { route = .review })
        case .settings: SettingsView()
        case .legacy: PreviewView(onOpenHistory: { route = .history }, onStartScan: { route = .review })
        }
    }

    private var rollbackFixture: RunManifestPreview {
        RunManifestPreview(runID: "fictitious-rollback", createdAt: "2026-09-12T10:00:00Z", scanStatus: "ready", photos: [
            PreviewPhoto(uuid: "fictitious-rollback-photo", title: "Paisaje de prueba para confirmar los valores completos",
                         date: "2026-09-12", existingKeywords: [], proposedKeywords: ["luz natural"], confidence: 0.91,
                         modelUsed: "qwen3-vl:4b", state: "ready", applyState: "verified", appliedKeywords: ["luz natural", "paisaje"],
                         appliedCaption: "Descripción ficticia verificada solo como dato de prueba.", captionState: "verified")])
    }

    private func updateAppearance() {
        let name: NSAppearance.Name = appearance == "dark"
            ? (increasedContrast ? .accessibilityHighContrastDarkAqua : .darkAqua)
            : (increasedContrast ? .accessibilityHighContrastAqua : .aqua)
        NSApp.appearance = NSAppearance(named: name)
    }

    private func resize(_ size: String) {
        let values = size.split(separator: "x").compactMap { Double($0) }
        let window = NSApp.windows.first { $0.identifier?.rawValue == "fictitious-ui-main" }
        window?.setContentSize(NSSize(width: values[0], height: values[1]))
    }
}

// The fictitious viewport may exceed the attached screen for exact-size QA.
private final class PreviewWindow: NSWindow {
    override func constrainFrameRect(_ frameRect: NSRect, to screen: NSScreen?) -> NSRect {
        frameRect
    }
}

@main
private enum PreviewLauncher {
    @MainActor static func main() async throws {
        let arguments = ProcessInfo.processInfo.arguments
        func option(_ name: String, fallback: String) -> String {
            guard let index = arguments.firstIndex(of: name), index + 1 < arguments.count else { return fallback }
            return arguments[index + 1]
        }
        let scenario = PreviewScenario(rawValue: option("--scenario", fallback: "compact")) ?? .compact
        let fixtures = try PreviewEnvironment(scenario: scenario)
        if arguments.contains("--verify-fixtures") {
            PreviewState.verifyFixtures()
            try await fixtures.verifyIsolation()
            return
        }
        let dimensions = option("--size", fallback: "1100x760").split(separator: "x").compactMap { Double($0) }
        precondition(dimensions.count == 2 && dimensions[0] >= 760 && dimensions[1] >= 560)
        let app = NSApplication.shared
        app.setActivationPolicy(.regular)
        let menu = NSMenu()
        let appMenu = NSMenu()
        appMenu.addItem(withTitle: "Salir de Vista previa ficticia", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let appItem = NSMenuItem(); appItem.submenu = appMenu; menu.addItem(appItem)
        let editMenu = NSMenu(title: "Edición")
        editMenu.addItem(withTitle: "Cortar", action: #selector(NSText.cut(_:)), keyEquivalent: "x")
        editMenu.addItem(withTitle: "Copiar", action: #selector(NSText.copy(_:)), keyEquivalent: "c")
        editMenu.addItem(withTitle: "Pegar", action: #selector(NSText.paste(_:)), keyEquivalent: "v")
        editMenu.addItem(withTitle: "Seleccionar todo", action: #selector(NSText.selectAll(_:)), keyEquivalent: "a")
        let editItem = NSMenuItem(title: "Edición", action: nil, keyEquivalent: ""); editItem.submenu = editMenu; menu.addItem(editItem)
        app.mainMenu = menu
        let window = PreviewWindow(contentRect: NSRect(x: 0, y: 0, width: dimensions[0], height: dimensions[1]),
            styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = "Vista previa ficticia · Fotos"
        window.identifier = NSUserInterfaceItemIdentifier("fictitious-ui-main")
        window.contentMinSize = NSSize(width: 760, height: 560)
        window.contentView = NSHostingView(rootView: PreviewWorkspace(state: PreviewState(scenario: scenario), fixtures: fixtures,
            appearance: option("--appearance", fallback: "light"), reduceMotion: arguments.contains("--reduce-motion"),
            route: PreviewRoute(rawValue: option("--route", fallback: "Revisión")) ?? .review,
            width: dimensions[0], sidebar: option("--sidebar", fallback: "auto")))
        window.center(); window.makeKeyAndOrderFront(nil)
        app.activate(ignoringOtherApps: true)
        print("Fictitious fixtures stored only at \(fixtures.root.path)")
        withExtendedLifetime(window) { app.run() }
    }
}
