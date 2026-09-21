import Combine
import Foundation
import XCTest
@testable import PhotosLocalKeywordIndexer

@MainActor
final class AppModelIntegrationTests: XCTestCase {
    func testStartupMustReadAutonomyBeforeManualResume() throws {
        let worker = FakeWorkerClient()
        worker.automaticallyCompletesAutonomyStatus = false
        worker.onSubmit = { _, _ in }
        let model = AppModel(worker: worker)
        model.startContinuousReviewIfNeeded()
        XCTAssertEqual(worker.allSubmitted.map(\.command), [.autonomyStatus])
    }

    func testRecoveredCampaignDoesNotResumeWithoutStartingNewBoundedPilot() throws {
        let worker = FakeWorkerClient()
        worker.automaticallyCompletesAutonomyStatus = false
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let model = AppModel(
            worker: worker,
            initialPreparation: readyPreparation(),
            persistSettings: true,
            settingsStore: AppSettingsStore(fileURL: root.appendingPathComponent("settings.json")),
            queueRunsRoot: root.appendingPathComponent("runs")
        )
        model.startContinuousReviewIfNeeded()
        let status = try XCTUnwrap(worker.allSubmitted.first)
        XCTAssertEqual(worker.allSubmitted.map(\.command), [.autonomyStatus])
        worker.emit(try campaignEvent(id: status.id, state: "paused", reason: "recovered"))
        XCTAssertFalse(model.autonomyIsOn)
        XCTAssertFalse(model.manualQueueMutationAllowed)
        XCTAssertEqual(worker.allSubmitted.map(\.command), [.autonomyStatus])
        model.startAutonomy()
        XCTAssertEqual(worker.allSubmitted.map(\.command), [.autonomyStatus])
        XCTAssertEqual(model.autonomyError, "Comprueba la preparación y el estado del recorrido antes de activarlo.")
        worker.emit(try terminalEvent(id: status.id))
        XCTAssertEqual(worker.allSubmitted.map(\.command), [.autonomyStatus, .queueStart])
        XCTAssertFalse(worker.allSubmitted.contains { $0.command == .autonomyResume })
        XCTAssertFalse(model.autonomyIsOn)
        XCTAssertTrue(model.manualQueueMutationAllowed)
        let manualID = model.queueSessionID
        model.resumeAutonomy()
        XCTAssertFalse(worker.allSubmitted.contains { $0.command == .autonomyResume })
        XCTAssertEqual(model.autonomyError, "Este recorrido detenido no se reanuda en la beta. Inicia un nuevo piloto limitado a 3 fotos.")
        XCTAssertTrue(model.manualQueueMutationAllowed)
        model.startAutonomy()
        let start = try XCTUnwrap(worker.allSubmitted.last)
        XCTAssertEqual(start.command, .autonomyStart)
        let payload = try XCTUnwrap(jsonObject(start)["payload"] as? [String: Any])
        XCTAssertEqual(payload["limit"] as? Int, 3)
        worker.emit(try campaignEvent(id: start.id, campaign: payload["campaign_id"] as? String ?? "new", revision: 2, state: "running"))
        worker.emit(try terminalEvent(id: start.id))
        XCTAssertTrue(model.autonomyIsOn)
        model.pauseAutonomy()
        XCTAssertTrue(model.autonomyPausePending)
        let pause = try XCTUnwrap(worker.allSubmitted.last)
        XCTAssertEqual(pause.command, .autonomyPause)
        worker.emit(try campaignEvent(id: pause.id, campaign: payload["campaign_id"] as? String ?? "new", revision: 3, state: "paused", reason: "user_pause"))
        worker.emit(try terminalEvent(id: pause.id))
        XCTAssertTrue(model.manualQueueMutationAllowed)
        XCTAssertEqual(model.queueSessionID, manualID)
        XCTAssertEqual(worker.allSubmitted.filter { $0.command == .queueStart }.count, 1)
    }

    func testAutonomyStartPreservesManualDraftAndTerminationNeverReactivates() throws {
        let worker = FakeWorkerClient()
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let model = AppModel(worker: worker, initialPreparation: readyPreparation(), persistSettings: true,
                             settingsStore: AppSettingsStore(fileURL: root.appendingPathComponent("settings.json")),
                             queueRunsRoot: root.appendingPathComponent("runs"))
        model.startContinuousReviewIfNeeded()
        let sessionID = try XCTUnwrap(model.queueSessionID)
        let manual = try XCTUnwrap(worker.submitted.first)
        let itemID = UUID().uuidString.lowercased()
        worker.emit(try JSONDecoder().decode(WorkerEvent.self, from: Data(#"{"id":"\#(manual.id)","event":"queue_item","session_id":"\#(sessionID)","item_id":"\#(itemID)","revision":1,"state":"ready"}"#.utf8)))
        XCTAssertTrue(model.editContinuousItem(id: itemID, keywords: ["manual"], caption: "Mi borrador"))
        let draft = model.reviewSession
        model.startAutonomy()
        let start = try XCTUnwrap(worker.allSubmitted.last)
        XCTAssertEqual(start.command, .autonomyStart)
        XCTAssertEqual(model.reviewSession, draft)
        XCTAssertEqual(model.queueSessionID, sessionID)
        model.persistContinuousItem(id: itemID)
        XCTAssertEqual(worker.allSubmitted.last, start)
        let payload = try XCTUnwrap(jsonObject(start)["payload"] as? [String: Any])
        XCTAssertEqual(payload["limit"] as? Int, 3)
        let campaign = try XCTUnwrap(payload["campaign_id"] as? String)
        worker.emit(try campaignEvent(id: start.id, campaign: campaign))
        worker.emit(try terminalEvent(id: start.id))
        XCTAssertTrue(model.autonomyIsOn)
        worker.forceState(.interrupted)
        XCTAssertFalse(model.autonomyIsOn)
        XCTAssertEqual(model.autonomyCampaign?.state, .paused)
        XCTAssertEqual(model.reviewSession, draft)
        XCTAssertEqual(model.queueSessionID, sessionID)
        XCTAssertFalse(worker.allSubmitted.contains { $0.command == .autonomyResume })
    }

    func testAutonomyActivityIsEphemeralAndRejectsStaleOrForeignEvents() throws {
        let worker = FakeWorkerClient()
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let model = AppModel(worker: worker, initialPreparation: readyPreparation(), persistSettings: true,
                             settingsStore: AppSettingsStore(fileURL: root.appendingPathComponent("settings.json")),
                             queueRunsRoot: root.appendingPathComponent("runs"))
        model.startContinuousReviewIfNeeded()
        model.startAutonomy()
        let start = try XCTUnwrap(worker.allSubmitted.last)
        let payload = try XCTUnwrap(jsonObject(start)["payload"] as? [String: Any])
        let campaign = try XCTUnwrap(payload["campaign_id"] as? String)
        worker.emit(try campaignEvent(id: start.id, campaign: campaign))

        worker.emit(try autonomyActivityEvent(campaign: campaign, revision: 2, state: "analyzing"))
        XCTAssertEqual(model.autonomyActivity.count, 1)
        XCTAssertEqual(model.reviewSession.items.count, 0)

        worker.emit(try autonomyActivityEvent(campaign: campaign, revision: 1, state: "saving"))
        worker.emit(try autonomyActivityEvent(campaign: "other", revision: 3, state: "saving"))
        XCTAssertEqual(model.autonomyActivity.values.first?.state, .analyzing)

        worker.emit(try autonomyActivityEvent(campaign: campaign, revision: 3, state: "settled"))
        worker.emit(try autonomyActivityEvent(campaign: campaign, revision: 4, state: "saving"))
        XCTAssertTrue(model.autonomyActivity.isEmpty)
    }

    private func campaignEvent(id: String, campaign: String = "campaign", revision: Int = 1, state: String = "running", reason: String = "none") throws -> WorkerEvent {
        let json = AutonomyTests.snapshot.replacingOccurrences(of: "\"id\":\"start\"", with: "\"id\":\"\(id)\"")
            .replacingOccurrences(of: "\"campaign_id\":\"campaign\"", with: "\"campaign_id\":\"\(campaign)\"")
            .replacingOccurrences(of: "\"revision\":1", with: "\"revision\":\(revision)")
            .replacingOccurrences(of: "\"state\":\"running\"", with: "\"state\":\"\(state)\"")
            .replacingOccurrences(of: "\"reason\":\"none\"", with: "\"reason\":\"\(reason)\"")
        return try JSONDecoder().decode(WorkerEvent.self, from: Data(json.utf8))
    }

    private func terminalEvent(id: String) throws -> WorkerEvent {
        try JSONDecoder().decode(WorkerEvent.self, from: Data(#"{"id":"\#(id)","event":"completed","exit_code":0,"next_action":"none"}"#.utf8))
    }

    private func autonomyActivityEvent(campaign: String, revision: Int, state: String) throws -> WorkerEvent {
        try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"activity","event":"autonomy_activity","campaign_id":"\#(campaign)","revision":\#(revision),"position":4,"state":"\#(state)","photos_local_identifier":"local-id"}"#.utf8)
        )
    }

    func testConcurrentSaveRejectionPreservesOnlyAffectedDraftAndOtherProgress() throws {
        try withSaveRequests { model, worker, requests, emit in
            XCTAssertEqual(model.reviewSession.items[0].statusCopy, "Enviando solicitud…")
            XCTAssertFalse(ContinuousReviewActions(item: model.reviewSession.items[0]).canSave)
            try emit(requests[1], "save_queued", 1)
            worker.emit(try JSONDecoder().decode(WorkerEvent.self, from: Data(
                #"{"id":"\#(requests[1].id)","event":"completed","exit_code":0}"#.utf8)))
            XCTAssertNil(model.reviewSession.items[1].saveRequestStatus)
            try emit(requests[2], "verified", 1)
            worker.emit(try JSONDecoder().decode(WorkerEvent.self, from: Data(
                #"{"id":"\#(requests[0].id)","event":"error","code":"QUEUE_DECISION_INVALID"}"#.utf8)))
            XCTAssertEqual(model.reviewSession.items.map(\.state), [.edited, .saveQueued, .verified])
            XCTAssertEqual(model.reviewSession.items[0].draft.keywords, ["manual"])
            XCTAssertEqual(model.reviewSession.items[0].draft.caption, "Borrador")
            XCTAssertTrue(ContinuousReviewActions(item: model.reviewSession.items[0]).canSave)
        }
    }

    func testSaveGenericErrorAndDisconnectBlockWithoutRetry() throws {
        try withSaveRequests { model, worker, requests, emit in
            try emit(requests[0], "save_queued", 1)
            worker.emit(try JSONDecoder().decode(WorkerEvent.self, from: Data(
                #"{"id":"\#(requests[0].id)","event":"error","code":"QUEUE_DECISION_INVALID"}"#.utf8)))
            worker.emit(try JSONDecoder().decode(WorkerEvent.self, from: Data(
                #"{"id":"\#(requests[1].id)","event":"error","code":"QUEUE_OPERATION_FAILED"}"#.utf8)))
            worker.forceState(.running(requestID: requests[2].id))
            worker.forceState(.interrupted)
            for item in model.reviewSession.items {
                XCTAssertEqual(item.statusCopy, "Guardado no confirmado. Requiere revisión antes de continuar.")
                XCTAssertFalse(ContinuousReviewActions(item: item).canSave)
                model.persistContinuousItem(id: item.id)
            }
            XCTAssertEqual(worker.submitted.filter { $0.command == .queuePersist }.count, 3)
        }
    }

    func testLateSaveErrorsDoNotUndoNewRevisionOrVerifiedAndCompletedDoesNotVerify() throws {
        try withSaveRequests { model, worker, requests, emit in
            try emit(requests[0], "ready", 2)
            try emit(requests[1], "verified", 1)
            for request in requests.prefix(2) {
                worker.emit(try JSONDecoder().decode(WorkerEvent.self, from: Data(
                    #"{"id":"\#(request.id)","event":"error","code":"QUEUE_DECISION_INVALID"}"#.utf8)))
            }
            worker.emit(try JSONDecoder().decode(WorkerEvent.self, from: Data(
                #"{"id":"\#(requests[2].id)","event":"completed","exit_code":0}"#.utf8)))
            XCTAssertEqual(model.reviewSession.items[0].revision, 2)
            XCTAssertEqual(model.reviewSession.items[1].state, .verified)
            XCTAssertNotEqual(model.reviewSession.items[2].state, .verified)
            XCTAssertEqual(model.reviewSession.items[2].saveRequestStatus, .unconfirmed)
            XCTAssertFalse(ContinuousReviewActions(item: model.reviewSession.items[2]).canSave)
            try emit(requests[2], "saving", 1)
            worker.emit(try JSONDecoder().decode(WorkerEvent.self, from: Data(
                #"{"id":"\#(requests[2].id)","event":"error","code":"QUEUE_OPERATION_FAILED"}"#.utf8)))
            XCTAssertEqual(model.reviewSession.items[2].saveRequestStatus, .unconfirmed)
        }
    }

    func testOldDecisionCannotAcceptNewRequestAndSynchronousThrowPreservesOtherCards() throws {
        try withSaveRequests { model, worker, requests, emit in
            worker.emit(try JSONDecoder().decode(WorkerEvent.self, from: Data(
                #"{"id":"\#(requests[0].id)","event":"error","code":"QUEUE_DECISION_INVALID"}"#.utf8)))
            let item = model.reviewSession.items[0].id
            withoutActuallyEscaping(emit) { emit in
                worker.onSubmit = { _, _ in
                    try emit(requests[1], "verified", 1)
                    throw CocoaError(.fileWriteUnknown)
                }
                model.persistContinuousItem(id: item)
                worker.onSubmit = nil
            }
            XCTAssertEqual(model.reviewSession.items[0].saveRequestStatus, .unconfirmed)
            XCTAssertEqual(model.reviewSession.items[1].state, .verified)
            try emit(requests[0], "save_queued", 1)
            XCTAssertEqual(model.reviewSession.items[0].saveRequestStatus, .unconfirmed)
            XCTAssertEqual(model.reviewSession.items[0].state, .edited)
            XCTAssertEqual(model.reviewSession.items[2].saveRequestStatus, .sending)
        }
    }

    func testDiscardUndoOrSubmitFailurePreservesConcurrentSaveSendingAndVerified() throws {
        for submitFails in [false, true] {
            for verifySave in [false, true] {
                try withSaveRequests { model, worker, requests, emit in
                    for request in requests.prefix(2) {
                        worker.emit(try JSONDecoder().decode(WorkerEvent.self, from: Data(
                            #"{"id":"\#(request.id)","event":"error","code":"QUEUE_DECISION_INVALID"}"#.utf8)))
                    }
                    let discardedID = model.reviewSession.items[0].id
                    let savedID = model.reviewSession.items[1].id
                    model.discardContinuousItem(id: discardedID)
                    model.persistContinuousItem(id: savedID)
                    let save = try XCTUnwrap(worker.submitted.last)
                    if verifySave { try emit(save, "verified", 1) }
                    let savedBeforeUndo = try XCTUnwrap(model.reviewSession.item(id: savedID))
                    if submitFails {
                        worker.onSubmit = { request, _ in
                            if request.command == .queueDiscard { throw CocoaError(.fileWriteUnknown) }
                        }
                        model.finalizePendingContinuousDiscard()
                    } else {
                        model.undoContinuousDiscard()
                    }
                    XCTAssertEqual(model.reviewSession.item(id: savedID), savedBeforeUndo)
                    XCTAssertEqual(model.reviewSession.item(id: discardedID)?.state, .edited)
                    XCTAssertEqual(model.reviewSession.item(id: discardedID)?.draft.caption, "Borrador")
                    XCTAssertFalse(ContinuousReviewActions(item: try XCTUnwrap(model.reviewSession.item(id: savedID))).canSave)
                    let count = worker.submitted.count
                    model.persistContinuousItem(id: savedID)
                    XCTAssertEqual(worker.submitted.count, count)
                }
            }
        }
    }

    private func withSaveRequests(
        _ body: (AppModel, FakeWorkerClient, [WorkerRequest], (WorkerRequest, String, Int) throws -> Void) throws -> Void
    ) throws {
        let worker = FakeWorkerClient()
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString.lowercased())
        defer { try? FileManager.default.removeItem(at: root) }
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        let model = AppModel(worker: worker, initialPreparation: readyPreparation(),
                             queueRunsRoot: root.appendingPathComponent("runs"))
        model.startContinuousReviewIfNeeded()
        let start = try XCTUnwrap(worker.submitted.first)
        let session = try XCTUnwrap(model.queueSessionID)
        for _ in 0..<3 {
            let item = UUID().uuidString.lowercased()
            worker.emit(try JSONDecoder().decode(WorkerEvent.self, from: Data(
                #"{"id":"\#(start.id)","event":"queue_item","session_id":"\#(session)","item_id":"\#(item)","revision":1,"state":"ready"}"#.utf8)))
            XCTAssertTrue(model.editContinuousItem(id: item, keywords: ["manual"], caption: "Borrador"))
            model.persistContinuousItem(id: item)
        }
        let requests = worker.submitted.filter { $0.command == .queuePersist }
        XCTAssertEqual(requests.count, 3)
        guard requests.count == 3 else { return }
        try body(model, worker, requests) { request, state, revision in
            let payload = try XCTUnwrap(self.jsonObject(request)["payload"] as? [String: Any])
            let item = try XCTUnwrap(payload["item_id"] as? String)
            let decision = try XCTUnwrap(payload["decision_id"] as? String)
            worker.emit(try JSONDecoder().decode(WorkerEvent.self, from: Data(
                #"{"id":"\#(start.id)","event":"queue_item","session_id":"\#(session)","item_id":"\#(item)","decision_id":"\#(decision)","revision":\#(revision),"state":"\#(state)"}"#.utf8)))
        }
    }

    func testFailedAnalysisQueueEventKeepsFailureCopyAndIgnoresOlderRevisionAnnouncements() throws {
        let worker = FakeWorkerClient()
        let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString.lowercased())
        let model = AppModel(
            worker: worker, initialPreparation: readyPreparation(), persistedSettings: .defaults,
            persistSettings: true,
            settingsStore: AppSettingsStore(fileURL: root.appendingPathComponent("settings.json")),
            queueRunsRoot: root.appendingPathComponent("runs", isDirectory: true)
        )
        let manifest = try writeManifest()
        defer {
            try? FileManager.default.removeItem(at: root)
            try? FileManager.default.removeItem(at: manifest.deletingLastPathComponent().deletingLastPathComponent())
        }
        var object = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: manifest)) as? [String: Any])
        var photos = try XCTUnwrap(object["photos"] as? [[String: Any]])
        photos[0]["scan_state"] = "analysis_failed"
        photos[0]["confidence"] = NSNull()
        photos[0]["proposed_caption"] = NSNull()
        photos[0]["caption_state"] = "not_requested"
        photos[0]["errors"] = [["stage": "analysis", "code": "ANALYSIS_FAILED"]]
        object["photos"] = photos
        object["scan_status"] = "ready_with_errors"
        try JSONSerialization.data(withJSONObject: object).write(to: manifest)
        model.startContinuousReviewIfNeeded()
        let start = try XCTUnwrap(worker.submitted.first)
        let sessionID = try XCTUnwrap(model.queueSessionID)
        let itemID = UUID().uuidString.lowercased()
        worker.emit(try JSONDecoder().decode(WorkerEvent.self, from: Data(
            #"{"id":"\#(start.id)","event":"queue_item","session_id":"\#(sessionID)","item_id":"\#(itemID)","revision":2,"state":"failed","manifest":"\#(manifest.path)"}"#.utf8
        )))
        XCTAssertEqual(model.reviewSession.item(id: itemID)?.photo?.state, "analysis_failed")
        XCTAssertEqual(model.reviewSession.item(id: itemID)?.state, .failed)
        XCTAssertEqual(model.message, "El análisis local terminó antes de producir una propuesta válida; esta versión no registró una causa más específica. No se guardó ningún cambio en Fotos. Reanaliza esta foto o descártala; no se guardará ningún cambio automáticamente.")
        XCTAssertFalse(ContinuousReviewActions(item: try XCTUnwrap(model.reviewSession.item(id: itemID))).canSave)
        let announcement = model.continuousReviewAnnouncement
        worker.emit(try JSONDecoder().decode(WorkerEvent.self, from: Data(
            #"{"id":"\#(start.id)","event":"queue_item","session_id":"\#(sessionID)","item_id":"\#(itemID)","revision":1,"state":"ready","manifest":"\#(manifest.path)"}"#.utf8
        )))
        XCTAssertEqual(model.message, "El análisis local terminó antes de producir una propuesta válida; esta versión no registró una causa más específica. No se guardó ningún cambio en Fotos. Reanaliza esta foto o descártala; no se guardará ningún cambio automáticamente.")
        XCTAssertEqual(model.continuousReviewAnnouncement, announcement)
    }

    func testLaunchingWithPhotosReadyAutomaticallyStartsLocalPreflightOnce() throws {
        let worker = FakeWorkerClient()
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        let model = AppModel(
            worker: worker,
            initialPreparation: preparation,
            persistedSettings: .defaults
        )

        model.bootstrapPreparationIfNeeded()
        model.bootstrapPreparationIfNeeded()

        let request = try XCTUnwrap(worker.submitted.first)
        XCTAssertEqual(request.command, .preflight)
        XCTAssertEqual(worker.submitted.count, 1)
    }

    func testLaunchingBeforePhotosGrantStillChecksOllamaIndependently() throws {
        let worker = FakeWorkerClient()
        let model = AppModel(
            worker: worker,
            initialPreparation: PreparationState(),
            persistedSettings: .defaults
        )

        model.bootstrapPreparationIfNeeded()

        XCTAssertEqual(worker.submitted.map(\.command), [.preflight])
    }

    func testSuccessfulPreflightStartsContinuousReviewWhenAlreadyOnReviewSurface() throws {
        let worker = FakeWorkerClient()
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        let root = URL(fileURLWithPath: NSTemporaryDirectory(), isDirectory: true)
            .appendingPathComponent(UUID().uuidString.lowercased(), isDirectory: true)
        let model = AppModel(
            worker: worker,
            initialPreparation: preparation,
            persistedSettings: .defaults,
            persistSettings: true,
            settingsStore: AppSettingsStore(fileURL: root.appendingPathComponent("settings.json")),
            queueRunsRoot: root.appendingPathComponent("runs", isDirectory: true),
            queueDecisionStore: QueueDecisionStore(
                root: root.appendingPathComponent("queue-sessions", isDirectory: true)
            )
        )
        defer { try? FileManager.default.removeItem(at: root) }

        model.bootstrapPreparationIfNeeded()
        let preflight = try XCTUnwrap(worker.submitted.first)
        worker.emit(try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data("{\"id\":\"\(preflight.id)\",\"event\":\"completed\",\"exit_code\":0,\"models\":{\"qwen3-vl:4b\":\"0.33.2\"},\"next_action\":\"none\"}".utf8)
        ))

        XCTAssertEqual(worker.submitted.map(\.command), [.preflight, .queueStart])
    }

    func testQueueStartPhotosPermissionErrorExitsPreparationInsteadOfStayingBusy() throws {
        let worker = FakeWorkerClient()
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString.lowercased())
        let model = AppModel(
            worker: worker,
            initialPreparation: readyPreparation(),
            persistedSettings: .defaults,
            persistSettings: true,
            settingsStore: AppSettingsStore(fileURL: root.appendingPathComponent("settings.json")),
            queueRunsRoot: root.appendingPathComponent("runs", isDirectory: true)
        )
        defer { try? FileManager.default.removeItem(at: root) }

        model.startContinuousReviewIfNeeded()
        let start = try XCTUnwrap(worker.submitted.first)
        worker.emit(try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data("{\"id\":\"\(start.id)\",\"event\":\"error\",\"code\":\"PHOTOS_ACCESS_DENIED\"}".utf8)
        ))

        XCTAssertNil(model.queueSessionID)
        XCTAssertEqual(model.preparation.photos, .actionRequired)
        XCTAssertTrue(model.message.contains("Fotos"))
    }

    func testRefreshingPreparationWhilePreflightIsActiveDoesNotSubmitAnotherRequest() throws {
        let worker = FakeWorkerClient()
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        let model = AppModel(
            worker: worker,
            initialPreparation: preparation,
            persistedSettings: .defaults
        )

        model.preflight()
        model.refreshPreparation()

        _ = try XCTUnwrap(worker.submitted.first)
        XCTAssertEqual(worker.submitted.count, 1)
    }

    func testContinuousReviewAcceptsQueueEventsEmittedDuringSubmit() throws {
        let worker = FakeWorkerClient()
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString.lowercased())
        let settings = AppSettingsStore(fileURL: root.appendingPathComponent("settings.json"))
        let itemID = UUID().uuidString.lowercased()
        worker.onSubmit = { request, worker in
            guard request.command == .queueStart,
                  let encoded = try? JSONEncoder().encode(request),
                  let object = try? JSONSerialization.jsonObject(with: encoded) as? [String: Any],
                  let payload = object["payload"] as? [String: Any],
                  let sessionID = payload["session_id"] as? String else { return }
            let event = try! JSONDecoder().decode(
                WorkerEvent.self,
                from: Data(#"{"id":"\#(request.id)","event":"queue_item","session_id":"\#(sessionID)","item_id":"\#(itemID)","revision":1,"state":"analyzing"}"#.utf8)
            )
            worker.emit(event)
        }
        let model = AppModel(
            worker: worker,
            initialPreparation: readyPreparation(),
            persistedSettings: .defaults,
            persistSettings: true,
            settingsStore: settings,
            queueRunsRoot: root.appendingPathComponent("runs", isDirectory: true)
        )

        model.startContinuousReviewIfNeeded()

        XCTAssertEqual(model.reviewSession.item(id: itemID)?.state, .analyzing)
        XCTAssertEqual(model.reviewSession.item(id: itemID)?.revision, 1)
        try? FileManager.default.removeItem(at: root)
    }

    func testContinuousReviewStartsAQueueAndStreamsItemsBeforeTheSessionFinishes() throws {
        let worker = FakeWorkerClient()
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString.lowercased(), isDirectory: true)
        let runs = root.appendingPathComponent("runs", isDirectory: true)
        let settings = AppSettingsStore(fileURL: root.appendingPathComponent("settings.json"))
        let model = AppModel(
            worker: worker,
            initialPreparation: readyPreparation(),
            persistedSettings: .defaults,
            persistSettings: true,
            settingsStore: settings,
            queueRunsRoot: runs,
            queueDecisionStore: QueueDecisionStore(
                root: root.appendingPathComponent("queue-sessions", isDirectory: true)
            )
        )

        model.startContinuousReviewIfNeeded()

        let request = try XCTUnwrap(worker.submitted.first)
        XCTAssertEqual(request.command, .queueStart)
        let payload = try XCTUnwrap(jsonObject(request)["payload"] as? [String: Any])
        let sessionID = try XCTUnwrap(payload["session_id"] as? String)
        worker.emit(try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"\#(request.id)","event":"queue_session","session_id":"\#(sessionID)","revision":1,"state":"running","queued":1,"analyzing":0,"ready":0,"saved":0,"attention":0}"#.utf8)
        ))
        let itemID = UUID().uuidString.lowercased()
        worker.emit(try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"\#(request.id)","event":"queue_item","session_id":"\#(sessionID)","item_id":"\#(itemID)","revision":1,"state":"analyzing"}"#.utf8)
        ))

        XCTAssertEqual(model.queueRevision, 1)
        XCTAssertEqual(model.reviewSession.item(id: itemID)?.state, .analyzing)
        XCTAssertEqual(model.reviewSession.item(id: itemID)?.revision, 1)
        try? FileManager.default.removeItem(at: root)
    }

    func testContinuousReviewPersistsDraftThroughPrivateArtifactAndOpaqueIPC() throws {
        let worker = FakeWorkerClient()
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString.lowercased(), isDirectory: true)
        let runs = root.appendingPathComponent("runs", isDirectory: true)
        let decisionStore = QueueDecisionStore(
            root: root.appendingPathComponent("queue-sessions", isDirectory: true)
        )
        let settings = AppSettingsStore(fileURL: root.appendingPathComponent("settings.json"))
        let model = AppModel(
            worker: worker,
            initialPreparation: readyPreparation(),
            persistedSettings: .defaults,
            persistSettings: true,
            settingsStore: settings,
            queueRunsRoot: runs,
            queueDecisionStore: decisionStore
        )
        model.startContinuousReviewIfNeeded()
        let start = try XCTUnwrap(worker.submitted.first)
        let sessionID = try XCTUnwrap(
            (try XCTUnwrap(jsonObject(start)["payload"] as? [String: Any]))["session_id"] as? String
        )
        let itemID = UUID().uuidString.lowercased()
        let manifest = try writeManifest()
        worker.emit(try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"\#(start.id)","event":"queue_item","session_id":"\#(sessionID)","item_id":"\#(itemID)","revision":1,"state":"ready","manifest":"\#(manifest.path)"}"#.utf8)
        ))
        XCTAssertTrue(model.editContinuousItem(
            id: itemID,
            keywords: ["góndola", "canal"],
            caption: "Una góndola navega por un canal."
        ))

        model.persistContinuousItem(id: itemID)

        let persist = try XCTUnwrap(worker.submitted.last)
        XCTAssertEqual(persist.command, .queuePersist)
        let encoded = try JSONEncoder().encode(persist)
        let text = String(decoding: encoded, as: UTF8.self)
        XCTAssertFalse(text.contains("góndola"))
        XCTAssertFalse(text.contains("Una góndola"))
        let payload = try XCTUnwrap(jsonObject(persist)["payload"] as? [String: Any])
        let decisionID = try XCTUnwrap(payload["decision_id"] as? String)
        let artifact = decisionStore.root
            .appendingPathComponent(sessionID, isDirectory: true)
            .appendingPathComponent("decisions", isDirectory: true)
            .appendingPathComponent("\(decisionID).json")
        let restored = try JSONDecoder().decode(QueueReviewDecision.self, from: Data(contentsOf: artifact))
        XCTAssertEqual(restored.approvedKeywords, ["góndola", "canal"])
        XCTAssertEqual(restored.approvedCaption, "Una góndola navega por un canal.")
        try? FileManager.default.removeItem(at: manifest.deletingLastPathComponent().deletingLastPathComponent())
        try? FileManager.default.removeItem(at: root)
    }

    func testContinuousDiscardLeavesTheTableButCanBeUndoneBeforeIPCCommit() throws {
        let worker = FakeWorkerClient()
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString.lowercased(), isDirectory: true)
        let model = AppModel(
            worker: worker,
            initialPreparation: readyPreparation(),
            persistedSettings: .defaults,
            queueRunsRoot: root.appendingPathComponent("runs", isDirectory: true)
        )
        model.startContinuousReviewIfNeeded()
        let start = try XCTUnwrap(worker.submitted.first)
        let sessionID = try XCTUnwrap(
            (try XCTUnwrap(jsonObject(start)["payload"] as? [String: Any]))["session_id"] as? String
        )
        let itemID = UUID().uuidString.lowercased()
        let manifest = try writeManifest()
        worker.emit(try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"\#(start.id)","event":"queue_item","session_id":"\#(sessionID)","item_id":"\#(itemID)","revision":1,"state":"ready","manifest":"\#(manifest.path)"}"#.utf8)
        ))

        model.discardContinuousItem(id: itemID)

        XCTAssertEqual(model.reviewSession.item(id: itemID)?.state, .discarded)
        XCTAssertTrue(model.canUndoContinuousDiscard)
        XCTAssertEqual(worker.submitted.count, 1)

        model.undoContinuousDiscard()

        XCTAssertEqual(model.reviewSession.item(id: itemID)?.state, .ready)
        XCTAssertFalse(model.canUndoContinuousDiscard)
        XCTAssertEqual(worker.submitted.count, 1)

        model.discardContinuousItem(id: itemID)
        model.finalizePendingContinuousDiscard()

        XCTAssertEqual(worker.submitted.last?.command, .queueDiscard)
        XCTAssertFalse(model.canUndoContinuousDiscard)
        try? FileManager.default.removeItem(at: manifest.deletingLastPathComponent().deletingLastPathComponent())
        try? FileManager.default.removeItem(at: root)
    }

    func testContinuousControlsSendOnlyTheRequiredQueueTransition() throws {
        let worker = FakeWorkerClient()
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString.lowercased(), isDirectory: true)
        let settings = AppSettingsStore(fileURL: root.appendingPathComponent("settings.json"))
        let model = AppModel(
            worker: worker,
            initialPreparation: readyPreparation(),
            persistedSettings: .defaults,
            persistSettings: true,
            settingsStore: settings,
            queueRunsRoot: root.appendingPathComponent("runs", isDirectory: true)
        )
        model.startContinuousReviewIfNeeded()
        let start = try XCTUnwrap(worker.submitted.first)
        let sessionID = try XCTUnwrap(
            (try XCTUnwrap(jsonObject(start)["payload"] as? [String: Any]))["session_id"] as? String
        )
        worker.emit(try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"\#(start.id)","event":"queue_session","session_id":"\#(sessionID)","revision":3,"state":"running","queued":0,"analyzing":0,"ready":0,"saved":0,"attention":0}"#.utf8)
        ))

        var controls = model.continuousControls
        controls.isPaused = true
        model.updateContinuousControls(controls)
        XCTAssertEqual(worker.submitted.map(\.command), [.queueStart, .queuePause])
        XCTAssertEqual(queueRevision(in: worker.submitted.last), 3)

        model.updateContinuousControls(controls)
        XCTAssertEqual(worker.submitted.map(\.command), [.queueStart, .queuePause])

        controls.isPaused = false
        model.updateContinuousControls(controls)
        XCTAssertEqual(worker.submitted.map(\.command), [.queueStart, .queuePause, .queueResume])

        XCTAssertTrue(controls.setAnalysisConcurrency(4))
        model.updateContinuousControls(controls)
        XCTAssertEqual(
            worker.submitted.map(\.command),
            [.queueStart, .queuePause, .queueResume, .queueUpdate]
        )
        let update = try XCTUnwrap(worker.submitted.last)
        let encoded = String(decoding: try JSONEncoder().encode(update), as: UTF8.self)
        XCTAssertFalse(encoded.contains("approved_keywords"))
        XCTAssertFalse(encoded.contains("approved_caption"))
        XCTAssertFalse(encoded.contains("góndola"))
        XCTAssertEqual(settings.load().analysisConcurrency, 4)
        try? FileManager.default.removeItem(at: root)
    }

    func testStoppingContinuousReviewUsesCurrentRevisionAndReleasesTheWorker() throws {
        let worker = FakeWorkerClient()
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString.lowercased(), isDirectory: true)
        let model = AppModel(
            worker: worker,
            initialPreparation: readyPreparation(),
            persistedSettings: .defaults,
            persistSettings: true,
            settingsStore: AppSettingsStore(fileURL: root.appendingPathComponent("settings.json")),
            queueRunsRoot: root.appendingPathComponent("runs", isDirectory: true)
        )
        model.startContinuousReviewIfNeeded()
        let start = try XCTUnwrap(worker.submitted.first)
        let sessionID = try XCTUnwrap(
            (try XCTUnwrap(jsonObject(start)["payload"] as? [String: Any]))["session_id"] as? String
        )
        worker.emit(try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"\#(start.id)","event":"queue_session","session_id":"\#(sessionID)","revision":8,"state":"paused","queued":0,"analyzing":0,"ready":0,"saved":0,"attention":0}"#.utf8)
        ))

        model.stopContinuousReview()

        let stop = try XCTUnwrap(worker.submitted.last)
        XCTAssertEqual(stop.command, .queueStop)
        XCTAssertEqual(queueRevision(in: stop), 8)
        worker.emit(try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"\#(stop.id)","event":"completed","exit_code":0,"next_action":"none"}"#.utf8)
        ))
        XCTAssertNil(model.queueSessionID)
        XCTAssertEqual(model.message, "Sesión finalizada. Historial y rollback vuelven a estar disponibles.")
        try? FileManager.default.removeItem(at: root)
    }

    func testQueueStartPersistsTheNormalizedContinuousPhotoCount() throws {
        let worker = FakeWorkerClient()
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString.lowercased(), isDirectory: true)
        let settings = AppSettingsStore(fileURL: root.appendingPathComponent("settings.json"))
        var legacySettings = AppSettings.defaults
        legacySettings.limit = 500
        let model = AppModel(
            worker: worker,
            initialPreparation: readyPreparation(),
            persistedSettings: legacySettings,
            persistSettings: true,
            settingsStore: settings,
            queueRunsRoot: root.appendingPathComponent("runs", isDirectory: true)
        )

        XCTAssertEqual(model.continuousControls.photoCount, 50)
        model.startContinuousReviewIfNeeded()

        XCTAssertEqual(worker.submitted.first?.command, .queueStart)
        XCTAssertEqual(settings.load().limit, 50)
        try? FileManager.default.removeItem(at: root)
    }

    func testAdvancedRescanPreservesManualDraftAndKeepsOptionsOutsideJSONL() throws {
        let worker = FakeWorkerClient()
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString.lowercased(), isDirectory: true)
        let decisionRoot = root.appendingPathComponent("queue-sessions", isDirectory: true)
        let model = AppModel(
            worker: worker,
            initialPreparation: readyPreparation(),
            persistedSettings: .defaults,
            persistSettings: true,
            settingsStore: AppSettingsStore(fileURL: root.appendingPathComponent("settings.json")),
            queueRunsRoot: root.appendingPathComponent("runs", isDirectory: true),
            queueDecisionStore: QueueDecisionStore(root: decisionRoot)
        )
        model.startContinuousReviewIfNeeded()
        let start = try XCTUnwrap(worker.submitted.first)
        let sessionID = try XCTUnwrap(
            (try XCTUnwrap(jsonObject(start)["payload"] as? [String: Any]))["session_id"] as? String
        )
        let itemID = UUID().uuidString.lowercased()
        let manifest = try writeManifest()
        worker.emit(try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"\#(start.id)","event":"queue_item","session_id":"\#(sessionID)","item_id":"\#(itemID)","revision":1,"state":"ready","manifest":"\#(manifest.path)"}"#.utf8)
        ))
        XCTAssertTrue(model.editContinuousItem(
            id: itemID,
            keywords: ["mascota manual"],
            caption: "Caption manual conservado."
        ))
        var options = QueueRescanOptions.defaults(model: "qwen3-vl:8b")
        options.analysisPrompt = "Distingue cada animal visible."

        model.rescanContinuousItem(id: itemID, options: options)

        let request = try XCTUnwrap(worker.submitted.last)
        XCTAssertEqual(request.command, .queueRescan)
        let encoded = String(decoding: try JSONEncoder().encode(request), as: UTF8.self)
        XCTAssertFalse(encoded.contains("Distingue cada animal"))
        XCTAssertFalse(encoded.contains("mascota manual"))
        XCTAssertFalse(encoded.contains("Caption manual"))
        let payload = try XCTUnwrap(jsonObject(request)["payload"] as? [String: Any])
        let decisionID = try XCTUnwrap(payload["decision_id"] as? String)
        let artifact = decisionRoot
            .appendingPathComponent(sessionID, isDirectory: true)
            .appendingPathComponent("rescans", isDirectory: true)
            .appendingPathComponent("\(decisionID).json")
        let restored = try JSONDecoder().decode(
            QueueRescanArtifact.self,
            from: Data(contentsOf: artifact)
        )
        XCTAssertEqual(restored.options, options)
        XCTAssertEqual(model.reviewSession.item(id: itemID)?.draft.keywords, ["mascota manual"])
        XCTAssertEqual(model.reviewSession.item(id: itemID)?.draft.caption, "Caption manual conservado.")
        let migratedDraft = try QueueDraftStore(root: decisionRoot).load(
            sessionID: sessionID,
            itemID: itemID,
            currentRevision: 2
        )
        XCTAssertEqual(migratedDraft?.keywords, ["mascota manual"])
        XCTAssertEqual(migratedDraft?.caption, "Caption manual conservado.")
        try? FileManager.default.removeItem(at: manifest.deletingLastPathComponent().deletingLastPathComponent())
        try? FileManager.default.removeItem(at: root)
    }

    func testAdvancedRescanCanResetManualDraftInsteadOfMigratingIt() throws {
        let worker = FakeWorkerClient()
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString.lowercased(), isDirectory: true)
        let decisionRoot = root.appendingPathComponent("queue-sessions", isDirectory: true)
        let model = AppModel(
            worker: worker,
            initialPreparation: readyPreparation(),
            persistedSettings: .defaults,
            persistSettings: true,
            settingsStore: AppSettingsStore(fileURL: root.appendingPathComponent("settings.json")),
            queueRunsRoot: root.appendingPathComponent("runs", isDirectory: true),
            queueDecisionStore: QueueDecisionStore(root: decisionRoot)
        )
        model.startContinuousReviewIfNeeded()
        let start = try XCTUnwrap(worker.submitted.first)
        let sessionID = try XCTUnwrap(
            (try XCTUnwrap(jsonObject(start)["payload"] as? [String: Any]))["session_id"] as? String
        )
        let itemID = UUID().uuidString.lowercased()
        let manifest = try writeManifest()
        worker.emit(try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"\#(start.id)","event":"queue_item","session_id":"\#(sessionID)","item_id":"\#(itemID)","revision":1,"state":"ready","manifest":"\#(manifest.path)"}"#.utf8)
        ))
        XCTAssertTrue(model.editContinuousItem(
            id: itemID,
            keywords: ["edición manual"],
            caption: "Caption manual."
        ))
        var options = QueueRescanOptions.defaults(model: "qwen3-vl:4b")
        options.resetEdits = true

        model.rescanContinuousItem(id: itemID, options: options)

        let item = try XCTUnwrap(model.reviewSession.item(id: itemID))
        XCTAssertEqual(item.state, .queued)
        XCTAssertFalse(item.hasManualEdits)
        XCTAssertTrue(item.draft.keywords.isEmpty)
        XCTAssertNil(item.draft.caption)
        XCTAssertNil(try QueueDraftStore(root: decisionRoot).load(
            sessionID: sessionID,
            itemID: itemID,
            currentRevision: 2
        ))
        try? FileManager.default.removeItem(at: manifest.deletingLastPathComponent().deletingLastPathComponent())
        try? FileManager.default.removeItem(at: root)
    }

    func testContinuousDraftSurvivesHelperAndAppModelRestartWithoutEnteringJSONL() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString.lowercased(), isDirectory: true)
        let decisionRoot = root.appendingPathComponent("queue-sessions", isDirectory: true)
        let decisionStore = QueueDecisionStore(root: decisionRoot)
        let firstWorker = FakeWorkerClient()
        let first = AppModel(
            worker: firstWorker,
            initialPreparation: readyPreparation(),
            persistedSettings: .defaults,
            persistSettings: true,
            settingsStore: AppSettingsStore(fileURL: root.appendingPathComponent("settings.json")),
            queueRunsRoot: root.appendingPathComponent("runs", isDirectory: true),
            queueDecisionStore: decisionStore
        )
        first.startContinuousReviewIfNeeded()
        let start = try XCTUnwrap(firstWorker.submitted.first)
        let sessionID = try XCTUnwrap(
            (try XCTUnwrap(jsonObject(start)["payload"] as? [String: Any]))["session_id"] as? String
        )
        let itemID = UUID().uuidString.lowercased()
        let manifest = try writeManifest()
        firstWorker.emit(try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"\#(start.id)","event":"queue_item","session_id":"\#(sessionID)","item_id":"\#(itemID)","revision":1,"state":"ready","manifest":"\#(manifest.path)"}"#.utf8)
        ))
        XCTAssertTrue(first.editContinuousItem(
            id: itemID,
            keywords: ["keyword manual"],
            caption: "Caption manual conservado."
        ))

        let secondWorker = FakeWorkerClient()
        let second = AppModel(
            worker: secondWorker,
            initialPreparation: readyPreparation(),
            persistedSettings: .defaults,
            persistSettings: true,
            settingsStore: AppSettingsStore(fileURL: root.appendingPathComponent("settings.json")),
            queueRunsRoot: root.appendingPathComponent("runs", isDirectory: true),
            queueDecisionStore: decisionStore
        )
        var recoveredControls = second.continuousControls
        recoveredControls.autoAnalyze = false
        second.updateContinuousControls(recoveredControls)
        second.bootstrapAutonomyStatusIfNeeded()
        second.resumeContinuousReview(sessionID: sessionID, revision: 7)
        let resume = try XCTUnwrap(secondWorker.submitted.last)
        XCTAssertEqual(resume.command, .queueResume)
        XCTAssertFalse(String(decoding: try JSONEncoder().encode(resume), as: UTF8.self).contains("keyword manual"))
        secondWorker.emit(try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"\#(resume.id)","event":"queue_item","session_id":"\#(sessionID)","item_id":"\#(itemID)","revision":1,"state":"ready","manifest":"\#(manifest.path)"}"#.utf8)
        ))

        XCTAssertEqual(second.reviewSession.item(id: itemID)?.draft.keywords, ["keyword manual"])
        XCTAssertEqual(second.reviewSession.item(id: itemID)?.draft.caption, "Caption manual conservado.")
        XCTAssertTrue(second.reviewSession.item(id: itemID)?.hasManualEdits == true)
        try? FileManager.default.removeItem(at: manifest.deletingLastPathComponent().deletingLastPathComponent())
        try? FileManager.default.removeItem(at: root)
    }

    func testContinuousReviewAutomaticallyResumesTheLatestSafePrivateSession() throws {
        let root = URL(fileURLWithPath: "/private/tmp", isDirectory: true)
            .appendingPathComponent("app-model-resume-\(UUID().uuidString.lowercased())", isDirectory: true)
        let sessions = root.appendingPathComponent("queue-sessions", isDirectory: true)
        let sessionID = UUID().uuidString.lowercased()
        let session = sessions.appendingPathComponent(sessionID, isDirectory: true)
        try FileManager.default.createDirectory(at: session, withIntermediateDirectories: true)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: sessions.path)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: session.path)
        let ledger: [String: Any] = [
            "schema_version": 1,
            "session_id": sessionID,
            "revision": 9,
            "state": "paused",
            "config": [
                "photo_count": 7,
                "inference_concurrency": 4,
                "auto_analyze": false,
                "include_caption": false,
                "apple_maps": true,
                "random_selection": false,
                "model_policy": "single",
                "model": "qwen3-vl:8b",
                "fast_model": "qwen3-vl:4b",
                "detailed_model": "qwen3-vl:8b",
            ],
            "items": [],
            "decisions": [],
        ]
        let ledgerURL = session.appendingPathComponent("session.json")
        try JSONSerialization.data(withJSONObject: ledger, options: [.sortedKeys]).write(to: ledgerURL)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: ledgerURL.path)
        let worker = FakeWorkerClient()
        let model = AppModel(
            worker: worker,
            initialPreparation: readyPreparation(),
            persistedSettings: .defaults,
            persistSettings: true,
            settingsStore: AppSettingsStore(fileURL: root.appendingPathComponent("settings.json")),
            queueRunsRoot: root.appendingPathComponent("runs", isDirectory: true),
            queueDecisionStore: QueueDecisionStore(root: sessions)
        )

        model.startContinuousReviewIfNeeded()

        let request = try XCTUnwrap(worker.submitted.first)
        XCTAssertEqual(request.command, .queueResume)
        let payload = try XCTUnwrap(jsonObject(request)["payload"] as? [String: Any])
        XCTAssertEqual(payload["session_id"] as? String, sessionID)
        XCTAssertEqual(payload["revision"] as? Int, 9)
        XCTAssertEqual(model.continuousControls.photoCount, 7)
        XCTAssertEqual(model.continuousControls.modelSelection, "qwen3-vl:8b")
        XCTAssertEqual(model.continuousControls.analysisConcurrency, 4)
        XCTAssertFalse(model.continuousControls.autoAnalyze)
        XCTAssertFalse(model.continuousControls.includeCaptions)
        XCTAssertTrue(model.continuousControls.appleMaps)
        XCTAssertTrue(model.continuousControls.isPaused)
        XCTAssertEqual(model.limit, 7)
        XCTAssertEqual(model.singleModel, "qwen3-vl:8b")
        XCTAssertFalse(model.randomSelection)
        try? FileManager.default.removeItem(at: root)
    }

    func testContinuousReviewDoesNotRecoverBeforePreparationIsReady() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString.lowercased(), isDirectory: true)
        let sessions = root.appendingPathComponent("queue-sessions", isDirectory: true)
        let sessionID = UUID().uuidString.lowercased()
        let session = sessions.appendingPathComponent(sessionID, isDirectory: true)
        try FileManager.default.createDirectory(at: session, withIntermediateDirectories: true)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: sessions.path)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: session.path)
        let ledger: [String: Any] = [
            "schema_version": 1,
            "session_id": sessionID,
            "revision": 1,
            "state": "running",
            "config": [
                "photo_count": 1,
                "inference_concurrency": 1,
                "auto_analyze": true,
                "include_caption": false,
                "apple_maps": false,
                "random_selection": true,
                "model_policy": "single",
                "model": "qwen3-vl:4b",
                "fast_model": "qwen3-vl:4b",
                "detailed_model": "qwen3-vl:4b",
            ],
            "items": [],
            "decisions": [],
        ]
        let ledgerURL = session.appendingPathComponent("session.json")
        try JSONSerialization.data(withJSONObject: ledger, options: [.sortedKeys]).write(to: ledgerURL)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: ledgerURL.path)

        let worker = FakeWorkerClient()
        let model = AppModel(
            worker: worker,
            initialPreparation: PreparationState(),
            persistedSettings: .defaults,
            persistSettings: true,
            settingsStore: AppSettingsStore(fileURL: root.appendingPathComponent("settings.json")),
            queueRunsRoot: root.appendingPathComponent("runs", isDirectory: true),
            queueDecisionStore: QueueDecisionStore(root: sessions)
        )

        model.startContinuousReviewIfNeeded()

        XCTAssertTrue(worker.submitted.isEmpty)
        XCTAssertFalse(model.message.contains("Recuperando"))
        try? FileManager.default.removeItem(at: root)
    }

    func testAppModelPersistsChangedOperationalSettings() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let store = AppSettingsStore(fileURL: directory.appendingPathComponent("settings.json"))
        let model = AppModel(
            worker: FakeWorkerClient(),
            initialPreparation: PreparationState(),
            persistedSettings: .defaults,
            persistSettings: true,
            settingsStore: store
        )

        model.limit = 12
        model.includeCaption = true
        model.autoAnalyze = false
        model.analysisConcurrency = 4

        XCTAssertEqual(store.load().limit, 12)
        XCTAssertTrue(store.load().includeCaption)
        XCTAssertFalse(store.load().autoAnalyze)
        XCTAssertEqual(store.load().analysisConcurrency, 4)
        try? FileManager.default.removeItem(at: directory)
    }

    func testDefaultAdaptiveConfigurationUsesBaseModelForBothRoles() {
        let model = AppModel(worker: FakeWorkerClient(), initialPreparation: readyPreparation())

        XCTAssertEqual(model.modelPolicy, "adaptive")
        XCTAssertEqual(model.fastModel, "qwen3-vl:4b")
        XCTAssertEqual(model.detailedModel, "qwen3-vl:4b")
    }

    func testScanSendsCaptionMapsAndAdaptiveModelPolicyToWorker() throws {
        let worker = FakeWorkerClient()
        let model = AppModel(
            worker: worker,
            initialPreparation: readyPreparation(models: ["qwen3-vl:4b", "qwen3-vl:8b"])
        )
        model.includeCaption = true
        model.appleMaps = true
        model.fastModel = "qwen3-vl:4b"
        model.detailedModel = "qwen3-vl:8b"

        model.scan()

        let request = try XCTUnwrap(worker.submitted.first)
        let payload = try XCTUnwrap(jsonObject(request)["payload"] as? [String: Any])
        XCTAssertEqual(request.command, .scan)
        XCTAssertEqual(payload["model_policy"] as? String, "adaptive")
        XCTAssertNil(payload["model"])
        XCTAssertEqual(payload["fast_model"] as? String, "qwen3-vl:4b")
        XCTAssertEqual(payload["detailed_model"] as? String, "qwen3-vl:8b")
        XCTAssertEqual(payload["apple_maps"] as? Bool, true)
        XCTAssertEqual(payload["include_caption"] as? Bool, true)
        let runsRoot = try XCTUnwrap(payload["runs_root"] as? String)
        XCTAssertTrue(runsRoot.hasPrefix("/"))
        XCTAssertTrue(runsRoot.hasSuffix("/Library/Application Support/Photos Local Keyword Indexer/runs"))
    }

    func testScanProgressKeepsTheSubmittedPhotoLimitWhenTheNextPlanChanges() {
        let worker = FakeWorkerClient()
        let model = AppModel(worker: worker, initialPreparation: readyPreparation())
        model.limit = 10

        model.scan()
        model.limit = 25

        XCTAssertEqual(model.scanProgressLimit, 10)
        XCTAssertEqual(model.limit, 25)
    }

    func testChangingEffectiveModelPlanRequiresAnotherPreflightBeforeScan() {
        let worker = FakeWorkerClient()
        let model = AppModel(worker: worker, initialPreparation: readyPreparation())
        model.detailedModel = "qwen3-vl:8b"

        model.scan()

        XCTAssertTrue(worker.submitted.isEmpty)
        XCTAssertFalse(model.preflightMatchesCurrentModelConfiguration)
        XCTAssertEqual(
            model.message,
            "La configuración de modelos cambió; vuelve a comprobar la preparación local antes de iniciar el dry-run."
        )
    }

    func testScanDoesNotSubmitWhenAConfiguredModelIsEmpty() {
        let worker = FakeWorkerClient()
        let model = AppModel(worker: worker, initialPreparation: readyPreparation())
        model.fastModel = "   "

        model.scan()

        XCTAssertTrue(worker.submitted.isEmpty)
        XCTAssertEqual(model.message, "Configura un modelo local válido antes de iniciar el dry-run.")
    }

    func testCompletionMessageUsesHumanSafeActionAndErrorCopy() throws {
        let worker = FakeWorkerClient()
        let model = AppModel(worker: worker)
        model.preflight()
        let request = try XCTUnwrap(worker.submitted.first)
        let event = try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"\#(request.id)","event":"completed","exit_code":2,"error_codes":["OLLAMA_MODEL_MISSING"],"next_action":"install_missing_model"}"#.utf8)
        )

        worker.emit(event)

        XCTAssertTrue(model.message.contains("Instalar el modelo indicado"))
        XCTAssertTrue(model.message.contains("Falta un modelo local"))
        XCTAssertFalse(model.message.contains("install_missing_model"))
        XCTAssertFalse(model.message.contains("OLLAMA_MODEL_MISSING"))
    }

    func testPhotosAccessDeniedEventUpdatesPreparationAndExplainsExactRecovery() throws {
        let worker = FakeWorkerClient()
        let model = AppModel(worker: worker, initialPreparation: readyPreparation())
        let event = try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"scan-1","event":"error","code":"PHOTOS_ACCESS_DENIED"}"#.utf8)
        )

        worker.emit(event)

        XCTAssertEqual(model.preparation.photos, .actionRequired)
        XCTAssertEqual(model.preparation.nextAction, "grant_photos_access")
        XCTAssertFalse(model.preparation.isReady)
        XCTAssertTrue(model.message.contains("Privacidad y seguridad > Fotos"))
        XCTAssertFalse(model.message.contains("PHOTOS_ACCESS_DENIED"))
    }

    func testAutomationDeniedCompletionUpdatesPreparationWithoutPretendingTCCWasChecked() throws {
        let worker = FakeWorkerClient()
        let model = AppModel(worker: worker, initialPreparation: readyPreparation())
        let event = try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"scan-1","event":"completed","exit_code":1,"error_codes":["PHOTOS_AUTOMATION_DENIED"],"next_action":"retry_preflight"}"#.utf8)
        )

        worker.emit(event)

        XCTAssertEqual(model.preparation.automation, .actionRequired)
        XCTAssertEqual(model.preparation.nextAction, "grant_photos_automation")
        XCTAssertFalse(model.preparation.isReady)
        XCTAssertTrue(model.message.contains("Privacidad y seguridad > Automatización"))
        XCTAssertFalse(model.message.contains("PHOTOS_AUTOMATION_DENIED"))
    }

    func testHelperTerminationDuringScanSurfacesSafePreparationRecovery() {
        let worker = FakeWorkerClient()
        let model = AppModel(worker: worker, initialPreparation: readyPreparation())

        model.scan()
        worker.forceState(.failed(code: "HELPER_UNAVAILABLE"))

        XCTAssertEqual(model.preparation.nextAction, "retry_preflight")
        XCTAssertEqual(model.preparation.errorCodes, ["HELPER_UNAVAILABLE"])
        let summary = PreparationActionSummary(state: model.preparation)
        XCTAssertEqual(summary.title, "Helper local no disponible")
        XCTAssertEqual(summary.buttonTitle, "Comprobar de nuevo")
        XCTAssertTrue(summary.buttonAccessibilityHint.contains("no se ejecutará un intérprete externo"))
        XCTAssertFalse(summary.accessibilityLabel.contains("HELPER_UNAVAILABLE"))
    }

    func testIncompatibleHelperTerminationSurfacesProtocolRecovery() {
        let worker = FakeWorkerClient()
        let model = AppModel(worker: worker, initialPreparation: readyPreparation())

        model.scan()
        worker.forceState(.failed(code: "INVALID_HELPER_EVENT"))

        XCTAssertEqual(model.preparation.nextAction, "retry_preflight")
        let summary = PreparationActionSummary(state: model.preparation)
        XCTAssertEqual(summary.title, "Protocolo local no compatible")
        XCTAssertTrue(summary.detail.contains("build válida"))
        XCTAssertTrue(summary.accessibilityLabel.contains("helper firmado"))
        XCTAssertFalse(summary.accessibilityLabel.contains("INVALID_HELPER_EVENT"))
    }

    func testHelperErrorMessageDoesNotExposeRawCode() throws {
        let worker = FakeWorkerClient()
        let model = AppModel(worker: worker)
        let event = try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"error-1","event":"error","code":"PRIVATE_INTERNAL_FAILURE"}"#.utf8)
        )

        worker.emit(event)

        XCTAssertEqual(model.message, "La ejecución necesita revisión manual.")
        XCTAssertFalse(model.message.contains("PRIVATE_INTERNAL_FAILURE"))
    }

    func testCancelAfterACompletedOperationDoesNotReopenCancellationState() throws {
        let worker = FakeWorkerClient()
        let model = AppModel(worker: worker)
        model.preflight()
        let request = try XCTUnwrap(worker.submitted.first)
        let event = try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"\#(request.id)","event":"completed","exit_code":0,"next_action":"none"}"#.utf8)
        )

        worker.emit(event)
        let completedMessage = model.message

        model.cancel()

        XCTAssertNil(worker.activeOperation)
        XCTAssertEqual(worker.state, .ready)
        XCTAssertEqual(model.message, completedMessage)
    }

    func testCompletedManifestEventLoadsTheLocalRunForPreviewAndHistoryNavigation() async throws {
        let worker = FakeWorkerClient()
        let model = AppModel(worker: worker)
        let manifest = try writeManifest()
        defer { try? FileManager.default.removeItem(at: manifest.deletingLastPathComponent().deletingLastPathComponent()) }

        let event = try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"scan-1","event":"completed","exit_code":0,"manifest":"\#(manifest.path)","next_action":"review_then_apply"}"#.utf8)
        )

        worker.emit(event)
        await Task.yield()

        XCTAssertEqual(model.manifestURL, manifest)
        XCTAssertEqual(model.preview?.runID, "run-1")
        XCTAssertEqual(model.preview?.photos.count, 1)
        XCTAssertEqual(model.message, "Revisa las propuestas antes de aplicar.")
    }

    func testUnreadableCompletedManifestShowsSafeRecoveryMessage() throws {
        let worker = FakeWorkerClient()
        let model = AppModel(worker: worker)
        let event = try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"scan-1","event":"completed","exit_code":0,"manifest":"/tmp/photos-indexer-missing/manifest.json","next_action":"review_then_apply"}"#.utf8)
        )

        worker.emit(event)

        XCTAssertNil(model.preview)
        XCTAssertEqual(
            model.message,
            "No se pudo cargar el resultado local; abre Historial y ejecuta un dry-run nuevo."
        )
    }

    func testWorkerInterruptionInvalidatesApplyConfirmationAndExplainsRecovery() async throws {
        let worker = FakeWorkerClient()
        let model = AppModel(worker: worker, initialPreparation: readyPreparation())
        let source = try writeManifest()
        defer { try? FileManager.default.removeItem(at: source.deletingLastPathComponent().deletingLastPathComponent()) }
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: source.path)
        let photo = try XCTUnwrap(try loadPhoto(from: source))

        XCTAssertTrue(model.loadPreview(from: source))
        model.setCaption(photo, selected: true)
        model.requestApply()
        let reviewRequest = try XCTUnwrap(worker.submitted.first)
        let reviewed = try writeReviewedManifest(nextTo: source)
        try await awaitReviewedEvent(worker, requestID: reviewRequest.id, manifest: reviewed)
        model.requestApply()
        XCTAssertTrue(model.showApplyConfirmation)

        worker.forceState(.running(requestID: reviewRequest.id))
        worker.forceState(.interrupted)

        XCTAssertFalse(model.showApplyConfirmation)
        XCTAssertFalse(model.safetyGate.canRequestApply)
        XCTAssertTrue(model.mutationRequiresManualReview)
        XCTAssertEqual(model.message, "La operación se interrumpió; revisa el run antes de intentar de nuevo.")
        let submittedBeforeRetry = worker.submitted.count
        model.requestApply()
        XCTAssertEqual(worker.submitted.count, submittedBeforeRetry)
    }

    func testPhotoProgressMessageUsesAnAbbreviatedIdentifier() throws {
        let worker = FakeWorkerClient()
        let model = AppModel(worker: worker, initialPreparation: readyPreparation())
        model.scan()
        let request = try XCTUnwrap(worker.submitted.first)
        let event = try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"\#(request.id)","event":"photo_progress","uuid":"49F027C6-0000-4000-8000-000000000000","state":"ready","keywords_count":2}"#.utf8)
        )

        worker.emit(event)

        XCTAssertEqual(model.message, "Procesando foto 49F027C6…")
        XCTAssertFalse(model.message.contains("0000-4000-8000-000000000000"))
    }

    func testApplyIsBlockedForAnInterruptedScan() throws {
        let worker = FakeWorkerClient()
        let model = AppModel(worker: worker)
        let manifest = try writeInterruptedManifest()
        defer { try? FileManager.default.removeItem(at: manifest.deletingLastPathComponent().deletingLastPathComponent()) }
        let photo = try XCTUnwrap(try loadPhoto(from: manifest))

        XCTAssertTrue(model.loadPreview(from: manifest))
        model.setKeyword("góndola", photo: photo, selected: true)
        model.requestApply()

        XCTAssertTrue(worker.submitted.isEmpty)
        XCTAssertEqual(
            model.message,
            "Este run no está listo para aplicar; ejecuta un dry-run nuevo o abre un run válido."
        )
    }

    func testFreshReviewRemainsLocalAfterAutomationDenialAndApplyStillBlocks() async throws {
        let worker = FakeWorkerClient()
        var preparation = PreparationState()
        preparation.applyPermissionErrors(["PHOTOS_AUTOMATION_DENIED"])
        let model = AppModel(worker: worker, initialPreparation: preparation)
        let manifest = try writeManifest()
        defer { try? FileManager.default.removeItem(at: manifest.deletingLastPathComponent().deletingLastPathComponent()) }
        let photo = try XCTUnwrap(try loadPhoto(from: manifest))

        XCTAssertTrue(model.loadPreview(from: manifest))
        model.setCaption(photo, selected: true)
        model.requestApply()

        let reviewRequest = try XCTUnwrap(worker.submitted.first)
        XCTAssertEqual(reviewRequest.command, .review)

        let reviewed = try writeReviewedManifest(nextTo: manifest)
        try await awaitReviewedEvent(worker, requestID: reviewRequest.id, manifest: reviewed)

        model.requestApply()

        XCTAssertEqual(worker.submitted.count, 1)
        XCTAssertFalse(model.showApplyConfirmation)
        XCTAssertEqual(model.message, AutomationTCCGuide.packagedHelper.errorMessage)
    }

    func testConfirmedApplyPermissionBlockDoesNotUseRollbackRecoveryCopy() {
        let worker = FakeWorkerClient()
        var preparation = PreparationState()
        preparation.applyPermissionErrors(["PHOTOS_ACCESS_DENIED"])
        let model = AppModel(worker: worker, initialPreparation: preparation)

        model.applyReviewedManifest()

        XCTAssertTrue(worker.submitted.isEmpty)
        XCTAssertTrue(model.message.contains("dry-run nuevo"))
        XCTAssertFalse(model.message.contains("reintenta el rollback"))
    }

    func testCaptionOnlyReviewSendsExactSelectionsToWorker() throws {
        let worker = FakeWorkerClient()
        let model = AppModel(worker: worker)
        let manifest = try writeManifest()
        defer { try? FileManager.default.removeItem(at: manifest.deletingLastPathComponent().deletingLastPathComponent()) }
        let photo = try XCTUnwrap(try loadPhoto(from: manifest))

        XCTAssertTrue(model.loadPreview(from: manifest))
        model.setCaption(photo, selected: true)
        model.requestApply()

        let request = try XCTUnwrap(worker.submitted.first)
        let payload = try XCTUnwrap(jsonObject(request)["payload"] as? [String: Any])
        XCTAssertEqual(request.command, .review)
        XCTAssertEqual(payload["selections"] as? [String: [String]], [:])
        XCTAssertEqual(
            payload["caption_selections"] as? [String: Bool],
            [photo.uuid: true]
        )
    }

    func testApplyRequiresExplicitConfirmationAfterReviewedManifest() async throws {
        let worker = FakeWorkerClient()
        let model = AppModel(worker: worker, initialPreparation: readyPreparation())
        let source = try writeManifest()
        defer { try? FileManager.default.removeItem(at: source.deletingLastPathComponent().deletingLastPathComponent()) }
        let photo = try XCTUnwrap(try loadPhoto(from: source))

        XCTAssertTrue(model.loadPreview(from: source))
        model.setCaption(photo, selected: true)
        model.requestApply()
        let reviewRequest = try XCTUnwrap(worker.submitted.first)
        let reviewed = try writeReviewedManifest(nextTo: source)
        try await awaitReviewedEvent(worker, requestID: reviewRequest.id, manifest: reviewed)

        model.requestApply()
        XCTAssertTrue(model.showApplyConfirmation)
        XCTAssertEqual(worker.submitted.count, 1)

        model.safetyGate.cancelConfirmation()
        model.applyReviewedManifest()
        XCTAssertEqual(worker.submitted.count, 1)

        model.requestApply()
        model.applyReviewedManifest()
        XCTAssertEqual(worker.submitted.last?.command, .apply)
    }

    func testCompletedReviewManifestReplacesSourcePreviewBeforeConfirmation() async throws {
        let worker = FakeWorkerClient()
        let model = AppModel(worker: worker)
        let source = try writeManifest()
        defer { try? FileManager.default.removeItem(at: source.deletingLastPathComponent().deletingLastPathComponent()) }
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: source.path)
        let photo = try XCTUnwrap(try loadPhoto(from: source))

        XCTAssertTrue(model.loadPreview(from: source))
        model.setCaption(photo, selected: true)
        model.requestApply()
        let reviewRequest = try XCTUnwrap(worker.submitted.first)
        let reviewed = try writeReviewedManifest(nextTo: source)

        try await awaitReviewedEvent(worker, requestID: reviewRequest.id, manifest: reviewed)

        XCTAssertEqual(model.manifestURL, reviewed)
        XCTAssertEqual(model.preview?.reviewedFromRunID, "run-1")
        XCTAssertEqual(model.preview?.photos.first?.proposedCaption, "Un canal visible.")
        XCTAssertEqual(model.message, "Manifiesto revisado listo. Confirma la aplicación explícitamente.")
    }

    func testRollbackRejectsManifestWithoutVerifiedChangesBeforeSubmitting() throws {
        let worker = FakeWorkerClient()
        let model = AppModel(worker: worker, initialPreparation: readyPreparation())
        let manifest = try writeManifest()
        defer { try? FileManager.default.removeItem(at: manifest.deletingLastPathComponent().deletingLastPathComponent()) }

        model.rollback(manifest: manifest)

        XCTAssertTrue(worker.submitted.isEmpty)
        XCTAssertEqual(model.message, "No hay cambios verificados elegibles para rollback.")
    }

    func testRollbackPermissionBlockPreservesTheVerifiedRunInsteadOfRequestingANewScan() {
        let worker = FakeWorkerClient()
        var preparation = PreparationState()
        preparation.applyPermissionErrors(["PHOTOS_ACCESS_DENIED"])
        let model = AppModel(worker: worker, initialPreparation: preparation)

        model.rollback(manifest: URL(fileURLWithPath: "/private/run/manifest.json"))

        XCTAssertTrue(worker.submitted.isEmpty)
        XCTAssertTrue(model.message.contains("conserva el run verificado"))
        XCTAssertTrue(model.message.contains("reintenta el rollback"))
        XCTAssertFalse(model.message.contains("dry-run nuevo"))
    }

    func testRollbackAutomationBlockPreservesTheVerifiedRunInsteadOfRequestingANewScan() {
        let worker = FakeWorkerClient()
        var preparation = PreparationState()
        preparation.applyPermissionErrors(["PHOTOS_AUTOMATION_DENIED"])
        let model = AppModel(worker: worker, initialPreparation: preparation)

        model.rollback(manifest: URL(fileURLWithPath: "/private/run/manifest.json"))

        XCTAssertTrue(worker.submitted.isEmpty)
        XCTAssertTrue(model.message.contains("Automatización"))
        XCTAssertTrue(model.message.contains("conserva el run verificado"))
        XCTAssertTrue(model.message.contains("reintenta el rollback"))
        XCTAssertFalse(model.message.contains("dry-run nuevo"))
    }

    func testSelectionChangedDuringReviewInvalidatesReviewedManifest() async throws {
        let worker = FakeWorkerClient()
        let model = AppModel(worker: worker)
        let source = try writeManifest()
        defer { try? FileManager.default.removeItem(at: source.deletingLastPathComponent().deletingLastPathComponent()) }
        let photo = try XCTUnwrap(try loadPhoto(from: source))

        XCTAssertTrue(model.loadPreview(from: source))
        model.setCaption(photo, selected: true)
        model.requestApply()
        let reviewRequest = try XCTUnwrap(worker.submitted.first)
        model.setCaption(photo, selected: false)

        let reviewed = try writeReviewedManifest(nextTo: source)
        try await awaitReviewedEvent(worker, requestID: reviewRequest.id, manifest: reviewed)

        XCTAssertNil(model.safetyGate.reviewedManifest)
        XCTAssertFalse(model.showApplyConfirmation)
        XCTAssertEqual(worker.submitted.count, 1)
    }

    private func readyPreparation(models: [String] = ["qwen3-vl:4b", "qwen3-vl:4b"]) -> PreparationState {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: models)
        preparation.completePreflight(
            exitCode: 0,
            installedModels: Array(Set(models)),
            warningCodes: [],
            errorCodes: [],
            nextAction: "none",
            safeInstruction: nil
        )
        return preparation
    }

    private func jsonObject(_ request: WorkerRequest) throws -> [String: Any] {
        try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(request)) as? [String: Any])
    }

    private func writeManifest() throws -> URL {
        let root = URL(fileURLWithPath: "/private/tmp", isDirectory: true)
            .appendingPathComponent(UUID().uuidString)
        let directory = root.appendingPathComponent("source-run", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let manifest = directory.appendingPathComponent("manifest.json")
        try Data(#"{"run_id":"run-1","created_at":"2026-08-25T16:00:00Z","scan_status":"ready","scan_digest":"source-digest","model":{},"policy":{},"photos":[{"uuid":"49F027C6-0000-4000-8000-000000000000","title":"Canal","date":"2026-08-25T16:00:00","existing_keywords":[],"proposed_keywords":[],"proposed_caption":"Un canal visible.","caption_state":"proposed","confidence":0.9,"scan_state":"noop","apply_state":"not_run","rollback_state":"not_run","errors":[]}]}"#.utf8).write(to: manifest)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: manifest.path)
        return manifest
    }

    private func writeInterruptedManifest() throws -> URL {
        let root = URL(fileURLWithPath: "/private/tmp", isDirectory: true)
            .appendingPathComponent(UUID().uuidString)
        let directory = root.appendingPathComponent("source-run", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        let manifest = directory.appendingPathComponent("manifest.json")
        try Data(#"{"run_id":"run-1","created_at":"2026-08-25T16:00:00Z","scan_status":"interrupted","photos":[{"uuid":"49F027C6-0000-4000-8000-000000000000","title":"Canal","date":"2025-08-25T16:00:00","existing_keywords":[],"proposed_keywords":["góndola"],"confidence":0.9,"scan_state":"ready","apply_state":"not_run","rollback_state":"not_run","errors":[]}]}"#.utf8).write(to: manifest)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: manifest.path)
        return manifest
    }

    private func writeReviewedManifest(nextTo source: URL) throws -> URL {
        let reviewedDirectory = source.deletingLastPathComponent().deletingLastPathComponent()
            .appendingPathComponent("reviewed-run", isDirectory: true)
        try FileManager.default.createDirectory(at: reviewedDirectory, withIntermediateDirectories: true)
        let reviewed = reviewedDirectory.appendingPathComponent("manifest.json")
        let data = Data(#"{"run_id":"review-1","created_at":"2026-08-25T16:01:00Z","scan_status":"ready","schema_version":3,"reviewed_from_run_id":"run-1","source_scan_digest":"source-digest","model":{},"policy":{},"photos":[{"uuid":"49F027C6-0000-4000-8000-000000000000","title":"Canal","date":"2026-08-25T16:00:00","existing_keywords":[],"proposed_keywords":[],"proposed_caption":"Un canal visible.","caption_state":"proposed","confidence":0.9,"scan_state":"noop","apply_state":"not_run","rollback_state":"not_run","errors":[]}]}"#.utf8)
        try data.write(to: reviewed, options: [.atomic])
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: reviewed.path)
        return reviewed
    }

    private func loadPhoto(from manifest: URL) throws -> PreviewPhoto? {
        _ = try RunManifestPreview.load(from: manifest)
        let data = try Data(contentsOf: manifest)
        let preview = try JSONDecoder().decode(RunManifestPreview.self, from: data)
        return preview.photos.first
    }

    private func awaitReviewedEvent(_ worker: FakeWorkerClient, requestID: String, manifest: URL) async throws {
        let event = try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"\#(requestID)","event":"completed","exit_code":0,"manifest":"\#(manifest.path)","next_action":"review_then_apply"}"#.utf8)
        )
        worker.emit(event)
        await Task.yield()
    }

    private func queueRevision(in request: WorkerRequest?) -> Int? {
        guard let request,
              let payload = try? jsonObject(request)["payload"] as? [String: Any] else {
            return nil
        }
        return payload["revision"] as? Int
    }
}

@MainActor
private final class FakeWorkerClient: WorkerClient {
    var automaticallyCompletesAutonomyStatus = true
    private let stateSubject = CurrentValueSubject<WorkerProcessState, Never>(.ready)
    private(set) var state: WorkerProcessState = .ready {
        didSet { stateSubject.send(state) }
    }
    private(set) var allSubmitted: [WorkerRequest] = []
    // Legacy manual-flow assertions exclude the startup read-only status probe;
    // autonomy/barrier tests inspect allSubmitted, including that probe.
    var submitted: [WorkerRequest] { allSubmitted.filter { $0.command != .autonomyStatus } }
    private(set) var activeOperation: WorkerCommand?
    var onSubmit: ((WorkerRequest, FakeWorkerClient) throws -> Void)?
    var onEvent: ((WorkerEvent) -> Void)?

    var statePublisher: AnyPublisher<WorkerProcessState, Never> {
        stateSubject.eraseToAnyPublisher()
    }

    func submit(_ request: WorkerRequest) throws {
        allSubmitted.append(request)
        activeOperation = request.command
        state = .running(requestID: request.id)
        if request.command == .autonomyStatus && automaticallyCompletesAutonomyStatus {
            emit(try JSONDecoder().decode(WorkerEvent.self, from: Data(#"{"id":"\#(request.id)","event":"completed","exit_code":0,"next_action":"none"}"#.utf8)))
            return
        }
        try onSubmit?(request, self)
    }

    func cancelActive() {
        guard let request = submitted.last else { return }
        state = .cancellationRequested(requestID: request.id)
    }

    func emit(_ event: WorkerEvent) {
        if event.event == .completed || event.event == .error {
            state = .ready
            activeOperation = nil
        }
        onEvent?(event)
    }

    func forceState(_ state: WorkerProcessState) {
        self.state = state
    }
}
