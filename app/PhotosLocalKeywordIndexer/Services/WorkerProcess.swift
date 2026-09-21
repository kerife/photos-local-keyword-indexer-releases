import Foundation
import Combine
import Darwin

enum WorkerProcessState: Equatable {
    case stopped
    case ready
    case running(requestID: String)
    case cancellationRequested(requestID: String)
    case interrupted
    case failed(code: String)

    var isRunning: Bool {
        if case .running = self { return true }
        if case .cancellationRequested = self { return true }
        return false
    }

    var isCancellationRequested: Bool {
        if case .cancellationRequested = self { return true }
        return false
    }

    /// Safe, human-facing copy for a process termination that did not produce
    /// a terminal helper event. Keep internal failure codes out of the UI.
    var terminalStatusText: String? {
        switch self {
        case .interrupted:
            return "La operación se interrumpió. Abre Historial para revisar el run antes de continuar."
        case let .failed(code):
            switch code {
            case "HELPER_UNAVAILABLE":
                return "No se encontró el helper local firmado. Reinstala la app o usa una build válida y vuelve a comprobar."
            case "PHOTOSCRIPT_UNAVAILABLE":
                return "PhotoScript no pudo cargar su puente AppleScript. Comprueba la compatibilidad de Photos, PhotoScript y macOS, y vuelve a comprobar."
            default:
                return "La operación falló. Abre Historial para revisar el run antes de volver a intentarlo."
            }
        default:
            return nil
        }
    }

    var terminalStatusAccessibilityLabel: String? {
        terminalStatusText.map { "Estado de la operación: \($0)" }
    }

    var shouldRefreshHistory: Bool {
        switch self {
        case .interrupted, .failed:
            return true
        default:
            return false
        }
    }
}

enum WorkerProcessError: LocalizedError {
    case helperMissing
    case alreadyRunning
    case launchFailed
    case encodingFailed

    var errorDescription: String? {
        switch self {
        case .helperMissing:
            return "No se encontró el helper local firmado dentro de la aplicación. Reinstala la app o usa una build válida; no se sustituirá por un intérprete Python externo."
        case .alreadyRunning:
            return "Ya hay una operación en curso."
        case .launchFailed:
            return "No fue posible iniciar el helper local."
        case .encodingFailed:
            return "No fue posible preparar la solicitud local."
        }
    }
}

enum WorkerProtocolFailureDisposition: Equatable {
    case terminateAndFail(code: String)
}

/// Read-only validation of the exact helper path used by the native app.
/// TCC is deliberately not part of this result: macOS has no supported
/// passive check for Apple Events permission, so the first PhotoScript call
/// remains authoritative.
enum EmbeddedHelperValidation: Equatable {
    case valid
    case outsideBundle
    case missing
    case symlink
    case notDirectory
    case notRegularFile
    case notExecutable
    case invalidBundle
    case wrongOwner
    case unsafePermissions
    case hardlink
    case unsupportedArchitecture
}

@MainActor
protocol WorkerClient: AnyObject {
    var state: WorkerProcessState { get }
    var statePublisher: AnyPublisher<WorkerProcessState, Never> { get }
    var activeOperation: WorkerCommand? { get }
    var onEvent: ((WorkerEvent) -> Void)? { get set }

    func submit(_ request: WorkerRequest) throws
    func cancelActive()
}

@MainActor
final class WorkerProcess: ObservableObject, WorkerClient {
    nonisolated static let packagedHelperDisplayName = "PhotosIndexerWorker"

    @Published private(set) var state: WorkerProcessState = .stopped
    @Published private(set) var lastProtocolErrorCode: String?

    var onEvent: ((WorkerEvent) -> Void)?

    var activeOperation: WorkerCommand? { activeCommand }
    var statePublisher: AnyPublisher<WorkerProcessState, Never> {
        $state.eraseToAnyPublisher()
    }

    private let executableURL: URL
    private let bundleURL: URL
    private let helperDirectoryURL: URL
    private var process: Process?
    private var input: Pipe?
    private var output: Pipe?
    private var errorOutput: Pipe?
    private var lineDecoder = JSONLineEventDecoder()
    private var activeRequestID: String?
    private var activeCommand: WorkerCommand?
    private var activeQueueSessionID: String?
    private var activeQueueRequests: [String: WorkerCommand] = [:]
    private var activeQueueRequestOrder: [String] = []
    private var activeAutonomyCampaignID: String?
    private var activeAutonomyRequests: [String: WorkerCommand] = [:]
    private var activeAutonomyRequestOrder: [String] = []
    private var previousAutonomyCampaignID: String?
    private var stderrReceived = false
    private var protocolFailureInProgress = false
    private var processGeneration: UUID?

    init(executableURL: URL? = nil, bundle: Bundle = .main) {
        bundleURL = bundle.bundleURL
        helperDirectoryURL = Self.helperDirectoryURL(bundle: bundle)
        self.executableURL = executableURL ?? Self.defaultExecutableURL(bundle: bundle)
    }

    nonisolated static func defaultExecutableURL(bundle: Bundle = .main) -> URL {
        helperDirectoryURL(bundle: bundle)
            .appendingPathComponent(
                "Contents/MacOS/PhotosIndexerWorker",
                isDirectory: false
            )
    }

    private nonisolated static func helperDirectoryURL(bundle: Bundle) -> URL {
        bundle.bundleURL
            .appendingPathComponent(
                "Contents/Helpers/PhotosIndexerWorker.app",
                isDirectory: true
            )
    }

    func submit(_ request: WorkerRequest) throws {
        let queueSessionID = Self.queueSessionID(for: request.payload)
        if Self.hasSubmissionConflict(
            activeRequestID: activeRequestID,
            activeQueueSessionID: activeQueueSessionID,
            requestCommand: request.command,
            requestQueueSessionID: queueSessionID
        ) {
            throw WorkerProcessError.alreadyRunning
        }
        try launchIfNeeded()
        guard let input else { throw WorkerProcessError.launchFailed }
        var data: Data
        do {
            data = try JSONEncoder().encode(request)
        } catch {
            throw WorkerProcessError.encodingFailed
        }
        data.append(0x0A)
        if request.command.isAutonomy {
            activeAutonomyRequests[request.id] = request.command
            activeAutonomyRequestOrder.append(request.id)
            if case let .autonomyStart(campaignID, _, _, _, _) = request.payload {
                previousAutonomyCampaignID = activeAutonomyCampaignID
                activeAutonomyCampaignID = campaignID
            } else if case let .autonomyControl(campaignID, _) = request.payload {
                activeAutonomyCampaignID = campaignID
            }
        } else if queueSessionID != nil {
            activeQueueRequests[request.id] = request.command
            activeQueueRequestOrder.append(request.id)
            if request.command == .queueStart || request.command == .queueResume {
                activeQueueSessionID = queueSessionID
            }
        } else {
            activeRequestID = request.id
            activeCommand = request.command
        }
        state = .running(requestID: request.id)
        input.fileHandleForWriting.write(data)
    }

    func cancelActive() {
        guard let activeRequestID, let input else { return }
        let cancellation = WorkerRequest.cancel(id: "cancel-\(activeRequestID)")
        if var data = try? JSONEncoder().encode(cancellation) {
            data.append(0x0A)
            input.fileHandleForWriting.write(data)
        }
        state = .cancellationRequested(requestID: activeRequestID)
        guard let activeCommand, Self.usesTerminationFallback(for: activeCommand) else { return }
        let activeProcess = process
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) { [weak self, weak activeProcess] in
            guard let self, self.activeRequestID == activeRequestID, activeProcess?.isRunning == true else { return }
            activeProcess?.terminate()
        }
    }

    nonisolated static func usesTerminationFallback(for command: WorkerCommand) -> Bool {
        command == .scan || command == .preflight
    }

    nonisolated static func hasSubmissionConflict(
        activeRequestID: String?,
        activeQueueSessionID: String?,
        requestCommand: WorkerCommand,
        requestQueueSessionID: String?
    ) -> Bool {
        if requestCommand.isAutonomy { return false }
        let isQueueCommand = requestQueueSessionID != nil
        if activeRequestID != nil { return true }
        if activeQueueSessionID != nil && !isQueueCommand { return true }
        if requestCommand == .queueStart && activeQueueSessionID != nil { return true }
        if requestCommand != .queueStart,
           isQueueCommand,
           activeQueueSessionID != nil,
           activeQueueSessionID != requestQueueSessionID {
            return true
        }
        return false
    }

    nonisolated static func isTerminalEvent(
        eventID: String,
        activeID: String?,
        activeQueueRequestIDs: Set<String> = [],
        event: WorkerEventKind
    ) -> Bool {
        guard event == .completed || event == .error else { return false }
        return eventID == activeID || activeQueueRequestIDs.contains(eventID)
    }

    nonisolated static func shouldAcceptEvent(
        eventID: String,
        activeRequestID: String?,
        activeQueueRequestIDs: Set<String> = [],
        event: WorkerEventKind,
        eventSessionID: String?,
        activeQueueSessionID: String?,
        activeAutonomyRequestIDs: Set<String> = [],
        eventCampaignID: String? = nil,
        activeAutonomyCampaignID: String? = nil,
        isAutonomyStatusResponse: Bool = false
    ) -> Bool {
        if event == .autonomyCampaign || event == .autonomyActivity {
            return eventCampaignID != nil && (eventCampaignID == activeAutonomyCampaignID || isAutonomyStatusResponse)
        }
        if activeAutonomyRequestIDs.contains(eventID) { return event == .completed || event == .error || event == .started }
        if eventID == activeRequestID || activeQueueRequestIDs.contains(eventID) { return true }
        return (event == .queueSession || event == .queueItem)
            && eventSessionID != nil
            && eventSessionID == activeQueueSessionID
    }

    nonisolated static func shouldHandleTermination(
        terminatedGeneration: UUID,
        activeGeneration: UUID?
    ) -> Bool {
        terminatedGeneration == activeGeneration
    }

    /// A helper completion may only point the app at the manifest it just
    /// created under the app-owned runs root. The Python service enforces the
    /// same boundary, but keeping it at the IPC consumer prevents a malformed
    /// or compromised helper from making the UI read an arbitrary local file.
    nonisolated static func isTrustedManifestPath(
        _ manifest: URL,
        runsRoot: URL,
        fileManager: FileManager = .default
    ) -> Bool {
        let rawComponents = manifest.pathComponents
        guard manifest.isFileURL,
              manifest.path.hasPrefix("/"),
              manifest.lastPathComponent == "manifest.json",
              !rawComponents.contains("."),
              !rawComponents.contains("..") else { return false }

        let root = runsRoot.standardizedFileURL
        let candidate = manifest.standardizedFileURL
        let expectedParent = candidate.deletingLastPathComponent().deletingLastPathComponent()
        guard expectedParent.path == root.path,
              candidate.deletingLastPathComponent().lastPathComponent != "" else {
            return false
        }
        let runDirectory = candidate.deletingLastPathComponent()
        guard runDirectory.lastPathComponent.hasPrefix(".exports-") == false else { return false }

        for directory in [root, runDirectory] {
            if let values = try? directory.resourceValues(forKeys: [.isDirectoryKey, .isSymbolicLinkKey]),
               values.isDirectory != true || values.isSymbolicLink == true {
                return false
            }
        }
        var fileStatus = stat()
        let lstatResult = candidate.path.withCString { pointer in
            Darwin.lstat(pointer, &fileStatus)
        }
        guard lstatResult == 0 else {
            // Keep the path-only policy usable with test doubles whose
            // completion points at a file that has not been persisted yet.
            return !fileManager.fileExists(atPath: candidate.path)
        }
        guard (fileStatus.st_mode & S_IFMT) == S_IFREG,
              fileStatus.st_uid == getuid(),
              (fileStatus.st_mode & 0o7777) == 0o600,
              fileStatus.st_nlink == 1 else {
            return false
        }
        return fileManager.fileExists(atPath: root.path)
            ? fileManager.fileExists(atPath: runDirectory.path)
            : true
    }

    nonisolated static func protocolFailureDisposition() -> WorkerProtocolFailureDisposition {
        .terminateAndFail(code: "INVALID_HELPER_EVENT")
    }

    nonisolated static func cancellationMessage(for command: WorkerCommand?) -> String {
        switch command {
        case .apply, .rollback:
            return "Cancelación solicitada. La foto actual terminará su escritura y verificación; no se iniciarán fotos nuevas."
        default:
            return "Cancelación solicitada; no se escribirán cambios en Fotos."
        }
    }

    func stop() {
        if let activeCommand, activeCommand == .apply || activeCommand == .rollback {
            cancelActive()
            return
        }
        output?.fileHandleForReading.readabilityHandler = nil
        errorOutput?.fileHandleForReading.readabilityHandler = nil
        if process?.isRunning == true {
            process?.terminate()
        }
        processGeneration = nil
        process = nil
        input = nil
        output = nil
        errorOutput = nil
        activeRequestID = nil
        activeCommand = nil
        activeQueueSessionID = nil
        activeQueueRequests.removeAll()
        activeQueueRequestOrder.removeAll()
        clearAutonomyRouting()
        state = .stopped
    }

    private func launchIfNeeded() throws {
        if process?.isRunning == true { return }
        lineDecoder.reset()
        guard isEmbeddedHelper(executableURL) else {
            throw WorkerProcessError.helperMissing
        }

        let process = Process()
        let processGeneration = UUID()
        let input = Pipe()
        let output = Pipe()
        let errorOutput = Pipe()
        process.executableURL = executableURL
        process.standardInput = input
        process.standardOutput = output
        process.standardError = errorOutput
        process.environment = Self.safeEnvironment()

        output.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            DispatchQueue.main.async {
                self?.consumeStdout(data)
            }
        }
        errorOutput.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { return }
            DispatchQueue.main.async {
                // stderr is deliberately not retained or displayed because it may
                // contain library paths. The protocol supplies sanitized error codes.
                self?.stderrReceived = true
            }
        }
        process.terminationHandler = { [weak self] terminated in
            DispatchQueue.main.async {
                self?.handleTermination(
                    status: terminated.terminationStatus,
                    generation: processGeneration
                )
            }
        }

        do {
            try process.run()
        } catch {
            output.fileHandleForReading.readabilityHandler = nil
            errorOutput.fileHandleForReading.readabilityHandler = nil
            throw WorkerProcessError.launchFailed
        }

        self.process = process
        self.processGeneration = processGeneration
        self.input = input
        self.output = output
        self.errorOutput = errorOutput
        state = .ready
    }

    private func consumeStdout(_ data: Data) {
        for result in lineDecoder.append(data) {
            switch result {
            case let .success(event):
                let activeQueueRequestIDs = Set(activeQueueRequests.keys)
                guard Self.shouldAcceptEvent(
                    eventID: event.id,
                    activeRequestID: activeRequestID,
                    activeQueueRequestIDs: activeQueueRequestIDs,
                    event: event.event,
                    eventSessionID: event.sessionID,
                    activeQueueSessionID: activeQueueSessionID,
                    activeAutonomyRequestIDs: Set(activeAutonomyRequests.keys),
                    eventCampaignID: event.autonomyCampaign?.campaignID ?? event.autonomyActivity?.campaignID,
                    activeAutonomyCampaignID: activeAutonomyCampaignID,
                    isAutonomyStatusResponse: activeAutonomyRequests[event.id] == .autonomyStatus
                ) else {
                    lastProtocolErrorCode = "STALE_HELPER_EVENT"
                    continue
                }
                if let manifest = event.manifest,
                   !Self.isTrustedManifestPath(
                       URL(fileURLWithPath: manifest),
                       runsRoot: Self.applicationSupportRunsRoot()
                   ) {
                    lastProtocolErrorCode = "INVALID_HELPER_EVENT"
                    failAfterProtocolError(code: "INVALID_HELPER_EVENT")
                    continue
                }
                if let snapshot = event.autonomyCampaign {
                    activeAutonomyCampaignID = snapshot.campaignID
                }
                if let command = activeAutonomyRequests[event.id], event.event == .completed || event.event == .error {
                    activeAutonomyRequests.removeValue(forKey: event.id)
                    activeAutonomyRequestOrder.removeAll { $0 == event.id }
                    if command == .autonomyStart {
                        if event.event == .error || event.exitCode != 0 { activeAutonomyCampaignID = previousAutonomyCampaignID }
                        previousAutonomyCampaignID = nil
                    }
                    recomputeRequestState()
                } else if Self.isTerminalEvent(
                    eventID: event.id,
                    activeID: activeRequestID,
                    activeQueueRequestIDs: activeQueueRequestIDs,
                    event: event.event
                ) {
                    let completedCommand = activeQueueRequests.removeValue(forKey: event.id) ?? activeCommand
                    activeQueueRequestOrder.removeAll { $0 == event.id }
                    if completedCommand == .queueStop
                        || (completedCommand == .queueStart && (event.event == .error || event.exitCode != 0))
                    {
                        activeQueueSessionID = nil
                        activeQueueRequests.removeAll()
                        activeQueueRequestOrder.removeAll()
                    }
                    if event.id == activeRequestID {
                        activeRequestID = nil
                        activeCommand = nil
                    }
                    recomputeRequestState()
                }
                onEvent?(event)
            case .failure:
                guard case let .terminateAndFail(code) = Self.protocolFailureDisposition() else { return }
                lastProtocolErrorCode = code
                failAfterProtocolError(code: code)
            }
        }
    }

    private func failAfterProtocolError(code: String) {
        protocolFailureInProgress = true
        lineDecoder.reset()
        output?.fileHandleForReading.readabilityHandler = nil
        errorOutput?.fileHandleForReading.readabilityHandler = nil
        if process?.isRunning == true {
            process?.terminate()
            return
        }
        process = nil
        processGeneration = nil
        input = nil
        output = nil
        errorOutput = nil
        activeRequestID = nil
        activeCommand = nil
        activeQueueSessionID = nil
        activeQueueRequests.removeAll()
        activeQueueRequestOrder.removeAll()
        clearAutonomyRouting()
        protocolFailureInProgress = false
        state = .failed(code: code)
        stderrReceived = false
    }

    private func handleTermination(status: Int32, generation: UUID) {
        guard Self.shouldHandleTermination(
            terminatedGeneration: generation,
            activeGeneration: processGeneration
        ) else { return }
        lineDecoder.reset()
        output?.fileHandleForReading.readabilityHandler = nil
        errorOutput?.fileHandleForReading.readabilityHandler = nil
        let wasActive = activeRequestID != nil || !activeQueueRequests.isEmpty || activeAutonomyCampaignID != nil || !activeAutonomyRequests.isEmpty
        let wasProtocolFailure = protocolFailureInProgress
        protocolFailureInProgress = false
        processGeneration = nil
        process = nil
        input = nil
        output = nil
        errorOutput = nil
        activeRequestID = nil
        activeCommand = nil
        activeQueueSessionID = nil
        activeQueueRequests.removeAll()
        activeQueueRequestOrder.removeAll()
        clearAutonomyRouting()
        if wasProtocolFailure {
            state = .failed(code: "INVALID_HELPER_EVENT")
        } else if wasActive {
            state = .interrupted
        } else if status == 0 {
            state = .stopped
        } else {
            state = .failed(code: stderrReceived ? "HELPER_EXITED" : "HELPER_UNAVAILABLE")
        }
        stderrReceived = false
    }

    nonisolated static func validateEmbeddedHelper(
        executableURL: URL,
        bundleURL: URL,
        fileManager: FileManager = .default
    ) -> EmbeddedHelperValidation {
        let outerContents = bundleURL.appendingPathComponent("Contents", isDirectory: true)
        let helpers = outerContents.appendingPathComponent("Helpers", isDirectory: true)
        let helperApp = helpers.appendingPathComponent("PhotosIndexerWorker.app", isDirectory: true)
        let contents = helperApp.appendingPathComponent("Contents", isDirectory: true)
        let macOSDirectory = contents.appendingPathComponent("MacOS", isDirectory: true)
        let resources = contents.appendingPathComponent("Resources", isDirectory: true)
        let infoPlist = contents.appendingPathComponent("Info.plist", isDirectory: false)
        let expected = macOSDirectory
            .appendingPathComponent(packagedHelperDisplayName, isDirectory: false)
            .standardizedFileURL
        let candidate = executableURL.standardizedFileURL
        guard candidate.path == expected.path else { return .outsideBundle }

        let resolvedBundle = bundleURL.resolvingSymlinksInPath().standardizedFileURL.path
        let resolvedHelperApp = helperApp.resolvingSymlinksInPath().standardizedFileURL.path
        let resolvedCandidate = candidate.resolvingSymlinksInPath().standardizedFileURL.path
        guard resolvedHelperApp.hasPrefix(resolvedBundle + "/"),
              resolvedCandidate.hasPrefix(resolvedHelperApp + "/") else {
            return .outsideBundle
        }

        for directory in [
            bundleURL,
            outerContents,
            helpers,
            helperApp,
            contents,
            macOSDirectory,
            resources,
        ] {
            if let failure = validateHelperDirectory(directory) {
                return failure
            }
        }
        if let failure = validateHelperFile(infoPlist, requiresExecutableMode: false) {
            return failure
        }
        if let failure = validateHelperFile(candidate, requiresExecutableMode: true) {
            return failure
        }
        guard fileManager.isExecutableFile(atPath: candidate.path) else {
            return .notExecutable
        }
        if let failure = validateHelperPayload(
            contents,
            helperApp: helperApp,
            fileManager: fileManager
        ) {
            return failure
        }

        guard let plistData = try? Data(contentsOf: infoPlist),
              let plist = try? PropertyListSerialization.propertyList(
                from: plistData,
                options: [],
                format: nil
              ) as? [String: Any],
              plist["CFBundleIdentifier"] as? String == "com.photoslocalkeywordindexer.worker",
              plist["CFBundlePackageType"] as? String == "APPL",
              plist["CFBundleExecutable"] as? String == packagedHelperDisplayName,
              plist["LSUIElement"] as? Bool == true,
              hasNonemptyPlistString(plist, key: "NSPhotoLibraryUsageDescription"),
              hasNonemptyPlistString(plist, key: "NSPhotoLibraryAddUsageDescription"),
              hasNonemptyPlistString(plist, key: "NSAppleEventsUsageDescription") else {
            return .invalidBundle
        }
        guard containsArm64MachO(at: candidate) else { return .unsupportedArchitecture }
        return .valid
    }

    private nonisolated static func validateHelperDirectory(
        _ directory: URL
    ) -> EmbeddedHelperValidation? {
        guard let metadata = helperMetadata(at: directory) else { return .missing }
        if (metadata.st_mode & S_IFMT) == S_IFLNK { return .symlink }
        guard (metadata.st_mode & S_IFMT) == S_IFDIR else { return .notDirectory }
        guard metadata.st_uid == getuid() else { return .wrongOwner }
        guard (metadata.st_mode & 0o022) == 0 else { return .unsafePermissions }
        return nil
    }

    private nonisolated static func validateHelperPayload(
        _ contents: URL,
        helperApp: URL,
        fileManager: FileManager
    ) -> EmbeddedHelperValidation? {
        let resolvedHelperApp = helperApp.resolvingSymlinksInPath().standardizedFileURL.path
        var pendingDirectories = [contents]

        while let directory = pendingDirectories.popLast() {
            guard let entries = try? fileManager.contentsOfDirectory(
                at: directory,
                includingPropertiesForKeys: nil,
                options: []
            ) else {
                return .missing
            }
            for entry in entries {
                guard let metadata = helperMetadata(at: entry) else { return .missing }
                switch metadata.st_mode & S_IFMT {
                case S_IFLNK:
                    guard metadata.st_uid == getuid() else { return .wrongOwner }
                    let resolved = entry.resolvingSymlinksInPath().standardizedFileURL.path
                    guard (resolved == resolvedHelperApp || resolved.hasPrefix(resolvedHelperApp + "/")),
                          fileManager.fileExists(atPath: resolved) else {
                        return .outsideBundle
                    }
                case S_IFDIR:
                    if let failure = validateHelperDirectory(entry) { return failure }
                    pendingDirectories.append(entry)
                case S_IFREG:
                    if let failure = validateHelperFile(entry, requiresExecutableMode: false) {
                        return failure
                    }
                default:
                    return .notRegularFile
                }
            }
        }
        return nil
    }

    private nonisolated static func hasNonemptyPlistString(
        _ plist: [String: Any],
        key: String
    ) -> Bool {
        guard let value = plist[key] as? String else { return false }
        return !value.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
    }

    private nonisolated static func validateHelperFile(
        _ file: URL,
        requiresExecutableMode: Bool
    ) -> EmbeddedHelperValidation? {
        guard let metadata = helperMetadata(at: file) else { return .missing }
        if (metadata.st_mode & S_IFMT) == S_IFLNK { return .symlink }
        guard (metadata.st_mode & S_IFMT) == S_IFREG else { return .notRegularFile }
        guard metadata.st_uid == getuid() else { return .wrongOwner }
        guard (metadata.st_mode & 0o022) == 0 else { return .unsafePermissions }
        guard metadata.st_nlink == 1 else { return .hardlink }
        if requiresExecutableMode, (metadata.st_mode & 0o111) == 0 {
            return .notExecutable
        }
        return nil
    }

    private nonisolated static func helperMetadata(at url: URL) -> stat? {
        var metadata = stat()
        let result = url.path.withCString { pointer in
            Darwin.lstat(pointer, &metadata)
        }
        return result == 0 ? metadata : nil
    }

    private nonisolated static func containsArm64MachO(at executable: URL) -> Bool {
        guard let data = try? Data(contentsOf: executable, options: .mappedIfSafe),
              data.count >= 8 else {
            return false
        }

        let magic = Array(data.prefix(4))
        switch magic {
        case [0xcf, 0xfa, 0xed, 0xfe]:
            return isArm64ExecutableMachO(data, at: 0, sliceSize: data.count)
        case [0xfe, 0xed, 0xfa, 0xcf]:
            return isArm64ExecutableMachO(data, at: 0, sliceSize: data.count)
        case [0xca, 0xfe, 0xba, 0xbe], [0xca, 0xfe, 0xba, 0xbf]:
            return fatMachOContainsArm64(data, littleEndian: false, is64Bit: magic[3] == 0xbf)
        case [0xbe, 0xba, 0xfe, 0xca], [0xbf, 0xba, 0xfe, 0xca]:
            return fatMachOContainsArm64(data, littleEndian: true, is64Bit: magic[0] == 0xbf)
        default:
            return false
        }
    }

    private nonisolated static func isArm64ExecutableMachO(
        _ data: Data,
        at offset: Int,
        sliceSize: Int
    ) -> Bool {
        guard sliceSize >= 32,
              offset >= 0,
              offset <= data.count - 32 else {
            return false
        }
        let magic = Array(data[offset..<(offset + 4)])
        let littleEndian: Bool
        switch magic {
        case [0xcf, 0xfa, 0xed, 0xfe]:
            littleEndian = true
        case [0xfe, 0xed, 0xfa, 0xcf]:
            littleEndian = false
        default:
            return false
        }
        guard
              readUInt32(data, at: offset + 4, littleEndian: littleEndian) == 0x0100000c,
              readUInt32(data, at: offset + 12, littleEndian: littleEndian) == 0x2 else {
            return false
        }
        return true
    }

    private nonisolated static func fatMachOContainsArm64(
        _ data: Data,
        littleEndian: Bool,
        is64Bit: Bool
    ) -> Bool {
        guard let architectureCount = readUInt32(data, at: 4, littleEndian: littleEndian),
              architectureCount > 0,
              architectureCount <= 64 else {
            return false
        }
        let entrySize = is64Bit ? 32 : 20
        let tableEnd = 8 + Int(architectureCount) * entrySize
        guard tableEnd <= data.count else { return false }
        var foundArm64Executable = false
        for index in 0..<Int(architectureCount) {
            let offset = 8 + index * entrySize
            let sliceOffset: UInt64?
            let sliceSize: UInt64?
            if is64Bit {
                sliceOffset = readUInt64(data, at: offset + 8, littleEndian: littleEndian)
                sliceSize = readUInt64(data, at: offset + 16, littleEndian: littleEndian)
            } else {
                sliceOffset = readUInt32(data, at: offset + 8, littleEndian: littleEndian).map(UInt64.init)
                sliceSize = readUInt32(data, at: offset + 12, littleEndian: littleEndian).map(UInt64.init)
            }
            guard let sliceOffset,
                  let sliceSize,
                  sliceSize > 0,
                  sliceOffset >= UInt64(tableEnd),
                  sliceOffset <= UInt64(data.count),
                  sliceSize <= UInt64(data.count) - sliceOffset else {
                return false
            }
            if readUInt32(data, at: offset, littleEndian: littleEndian) == 0x0100000c {
                guard sliceOffset <= UInt64(Int.max),
                      sliceSize <= UInt64(Int.max),
                      isArm64ExecutableMachO(
                        data,
                        at: Int(sliceOffset),
                        sliceSize: Int(sliceSize)
                      ) else {
                    return false
                }
                foundArm64Executable = true
            }
        }
        return foundArm64Executable
    }

    private nonisolated static func readUInt32(
        _ data: Data,
        at offset: Int,
        littleEndian: Bool
    ) -> UInt32? {
        guard offset >= 0, offset + 4 <= data.count else { return nil }
        let bytes = data[offset..<(offset + 4)].map(UInt32.init)
        if littleEndian {
            return bytes[0] | (bytes[1] << 8) | (bytes[2] << 16) | (bytes[3] << 24)
        }
        return (bytes[0] << 24) | (bytes[1] << 16) | (bytes[2] << 8) | bytes[3]
    }

    private nonisolated static func readUInt64(
        _ data: Data,
        at offset: Int,
        littleEndian: Bool
    ) -> UInt64? {
        guard offset >= 0, offset + 8 <= data.count else { return nil }
        var value: UInt64 = 0
        if littleEndian {
            for index in 0..<8 {
                value |= UInt64(data[offset + index]) << UInt64(index * 8)
            }
        } else {
            for index in 0..<8 {
                value = (value << 8) | UInt64(data[offset + index])
            }
        }
        return value
    }

    private func isEmbeddedHelper(_ url: URL) -> Bool {
        Self.validateEmbeddedHelper(executableURL: url, bundleURL: bundleURL) == .valid
    }

    private static func safeEnvironment() -> [String: String] {
        let source = ProcessInfo.processInfo.environment
        var environment = [
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin:/usr/local/bin",
            "LANG": source["LANG"] ?? "es_MX.UTF-8",
        ]
        if let temporary = source["TMPDIR"] {
            environment["TMPDIR"] = temporary
        }
        return environment
    }

    private func clearAutonomyRouting() {
        activeAutonomyCampaignID = nil
        previousAutonomyCampaignID = nil
        activeAutonomyRequests.removeAll()
        activeAutonomyRequestOrder.removeAll()
    }

    private func recomputeRequestState() {
        if let id = activeRequestID ?? activeQueueRequestOrder.first ?? activeAutonomyRequestOrder.first {
            state = .running(requestID: id)
        } else { state = .ready }
    }

    private nonisolated static func queueSessionID(for payload: WorkerRequestPayload) -> String? {
        switch payload {
        case let .queueStart(sessionID, _, _, _, _),
             let .queueRevision(sessionID, _, _),
             let .queueUpdate(sessionID, _, _, _),
             let .queueItemDecision(sessionID, _, _, _):
            return sessionID
        default:
            return nil
        }
    }

    private nonisolated static func applicationSupportRunsRoot(
        fileManager: FileManager = .default
    ) -> URL {
        fileManager.urls(for: .applicationSupportDirectory, in: .userDomainMask).first?
            .appendingPathComponent("Photos Local Keyword Indexer", isDirectory: true)
            .appendingPathComponent("runs", isDirectory: true)
            ?? URL(fileURLWithPath: "/", isDirectory: true)
    }
}
