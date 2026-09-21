import Foundation
import XCTest
@testable import PhotosLocalKeywordIndexer

final class IPCModelsTests: XCTestCase {
    func testWorkerEventKindsUseHumanLabelsForSessionHistory() {
        XCTAssertEqual(WorkerEventKind.started.humanLabel, "Operación iniciada")
        XCTAssertEqual(WorkerEventKind.photoProgress.humanLabel, "Progreso de foto")
        XCTAssertEqual(WorkerEventKind.queueSession.humanLabel, "Estado de la cola")
        XCTAssertEqual(WorkerEventKind.queueItem.humanLabel, "Estado del elemento")
        XCTAssertEqual(WorkerEventKind.completed.humanLabel, "Operación completada")
        XCTAssertEqual(WorkerEventKind.error.humanLabel, "Error de operación")
    }

    func testWorkerEventAccessibilityLabelKeepsHumanStateAndBoundedDetail() throws {
        let event = try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"scan-1","event":"photo_progress","uuid":"A1B2C3D4","state":"ready","keywords_count":1}"#.utf8)
        )

        XCTAssertEqual(event.humanAccessibilityLabel, "Progreso de foto: A1B2C3D4")
        XCTAssertFalse(event.humanAccessibilityLabel.contains("photo_progress"))
    }

    func testWorkerErrorAccessibilityUsesSafeHumanCopy() throws {
        let event = try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"scan-1","event":"error","code":"OLLAMA_MODEL_MISSING"}"#.utf8)
        )

        XCTAssertEqual(
            event.humanAccessibilityLabel,
            "Error de operación: Falta un modelo local; instala el modelo indicado."
        )
        XCTAssertFalse(event.humanAccessibilityLabel.contains("OLLAMA_MODEL_MISSING"))
    }

    func testCompletedCancellationEventExplainsRecoveryWithoutExposingCodes() throws {
        let event = try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"scan-1","event":"completed","exit_code":1,"error_codes":["CANCELLED"],"next_action":"none"}"#.utf8)
        )

        XCTAssertEqual(
            event.humanAccessibilityLabel,
            "Operación completada: La cancelación terminó; revisa el run antes de continuar."
        )
        XCTAssertFalse(event.humanAccessibilityLabel.contains("CANCELLED"))
    }

    func testCompletedFailureEventIsActionableInSessionHistory() throws {
        let event = try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"apply-1","event":"completed","exit_code":1,"error_codes":["APPLY_FAILED"],"next_action":"retry_failed_operation"}"#.utf8)
        )

        XCTAssertEqual(
            event.humanAccessibilityLabel,
            "Operación completada: Terminó con errores revisables; consulta el run."
        )
        XCTAssertFalse(event.humanAccessibilityLabel.contains("APPLY_FAILED"))
    }

    func testDefaultWorkerPathIgnoresEnvironmentOverride() {
        setenv("PHOTOS_INDEXER_WORKER", "/tmp/untrusted-worker", 1)
        defer { unsetenv("PHOTOS_INDEXER_WORKER") }

        let expected = Bundle.main.bundleURL
            .appendingPathComponent(
                "Contents/Helpers/PhotosIndexerWorker.app/Contents/MacOS/PhotosIndexerWorker"
            )
        let actual = WorkerProcess.defaultExecutableURL(bundle: .main)

        XCTAssertEqual(actual.path, expected.path)
    }

    func testScanRequestEncodesOnlyTheApprovedWireContract() throws {
        let request = WorkerRequest.scan(
            id: "scan-1",
            limit: 20,
            modelPolicy: "adaptive",
            model: nil,
            fastModel: "qwen3-vl:4b",
            detailedModel: "qwen3-vl:8b",
            appleMaps: true,
            randomSelection: true,
            runsRoot: URL(fileURLWithPath: "/Users/example/Library/Application Support/Photos Local Keyword Indexer/runs")
        )

        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(request)) as? [String: Any]
        )
        XCTAssertEqual(object["id"] as? String, "scan-1")
        XCTAssertEqual(object["command"] as? String, "scan")
        let payload = try XCTUnwrap(object["payload"] as? [String: Any])
        XCTAssertEqual(payload["limit"] as? Int, 20)
        XCTAssertEqual(payload["model_policy"] as? String, "adaptive")
        XCTAssertEqual(payload["fast_model"] as? String, "qwen3-vl:4b")
        XCTAssertEqual(payload["detailed_model"] as? String, "qwen3-vl:8b")
        XCTAssertEqual(payload["apple_maps"] as? Bool, true)
        XCTAssertEqual(payload["runs_root"] as? String, "/Users/example/Library/Application Support/Photos Local Keyword Indexer/runs")
        XCTAssertEqual(payload["random_selection"] as? Bool, true)
        XCTAssertNil(payload["images"])
        XCTAssertNil(payload["latitude"])
        XCTAssertNil(payload["longitude"])
    }

    func testQueueStartRequestEncodesOnlyPrivateConfigurationReferences() throws {
        let request = WorkerRequest.queueStart(
            id: "queue-start-1",
            sessionID: "session-1",
            decisionID: "decision-0",
            runsRoot: URL(fileURLWithPath: "/private/app/runs"),
            settings: URL(fileURLWithPath: "/private/app/settings.json")
        )

        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(request)) as? [String: Any]
        )
        XCTAssertEqual(object["command"] as? String, "queue_start")
        let payload = try XCTUnwrap(object["payload"] as? [String: Any])
        XCTAssertEqual(
            Set(payload.keys),
            ["session_id", "revision", "decision_id", "runs_root", "settings_path"]
        )
        XCTAssertEqual(payload["session_id"] as? String, "session-1")
        XCTAssertEqual(payload["revision"] as? Int, 0)
        XCTAssertEqual(payload["decision_id"] as? String, "decision-0")
        XCTAssertEqual(payload["runs_root"] as? String, "/private/app/runs")
        XCTAssertEqual(payload["settings_path"] as? String, "/private/app/settings.json")
    }

    func testQueueSessionControlsEncodeRevisionAndDecisionReferencesOnly() throws {
        let requests = [
            WorkerRequest.queueResume(
                id: "resume-1", sessionID: "session-1", revision: 4,
                decisionID: "decision-4"
            ),
            WorkerRequest.queueUpdate(
                id: "update-1",
                sessionID: "session-1",
                revision: 4,
                decisionID: "decision-4",
                settings: URL(fileURLWithPath: "/private/app/settings.json")
            ),
            WorkerRequest.queuePause(
                id: "pause-1", sessionID: "session-1", revision: 4,
                decisionID: "decision-4"
            ),
            WorkerRequest.queueStop(
                id: "stop-1", sessionID: "session-1", revision: 4,
                decisionID: "decision-4"
            ),
        ]
        let expected: [(String, Set<String>)] = [
            ("queue_resume", ["session_id", "revision", "decision_id"]),
            ("queue_update", ["session_id", "revision", "decision_id", "settings_path"]),
            ("queue_pause", ["session_id", "revision", "decision_id"]),
            ("queue_stop", ["session_id", "revision", "decision_id"]),
        ]

        for (request, expectation) in zip(requests, expected) {
            let object = try XCTUnwrap(
                JSONSerialization.jsonObject(with: JSONEncoder().encode(request)) as? [String: Any]
            )
            XCTAssertEqual(object["command"] as? String, expectation.0)
            let payload = try XCTUnwrap(object["payload"] as? [String: Any])
            XCTAssertEqual(Set(payload.keys), expectation.1)
            XCTAssertEqual(payload["session_id"] as? String, "session-1")
            XCTAssertEqual(payload["revision"] as? Int, 4)
            XCTAssertEqual(payload["decision_id"] as? String, "decision-4")
            if expectation.0 == "queue_update" {
                XCTAssertEqual(payload["settings_path"] as? String, "/private/app/settings.json")
            }
        }
    }

    func testQueueItemControlsRequireItemRevisionAndDecisionReferences() throws {
        let requests = [
            WorkerRequest.queuePersist(
                id: "persist-1", sessionID: "session-1", itemID: "item-1",
                revision: 7, decisionID: "decision-7"
            ),
            WorkerRequest.queueDiscard(
                id: "discard-1", sessionID: "session-1", itemID: "item-1",
                revision: 7, decisionID: "decision-7"
            ),
            WorkerRequest.queueRescan(
                id: "rescan-1", sessionID: "session-1", itemID: "item-1",
                revision: 7, decisionID: "decision-7"
            ),
        ]
        let commands = ["queue_persist", "queue_discard", "queue_rescan"]

        for (request, command) in zip(requests, commands) {
            let object = try XCTUnwrap(
                JSONSerialization.jsonObject(with: JSONEncoder().encode(request)) as? [String: Any]
            )
            XCTAssertEqual(object["command"] as? String, command)
            let payload = try XCTUnwrap(object["payload"] as? [String: Any])
            XCTAssertEqual(
                Set(payload.keys),
                ["session_id", "item_id", "revision", "decision_id"]
            )
            XCTAssertEqual(payload["session_id"] as? String, "session-1")
            XCTAssertEqual(payload["item_id"] as? String, "item-1")
            XCTAssertEqual(payload["revision"] as? Int, 7)
            XCTAssertEqual(payload["decision_id"] as? String, "decision-7")
            for forbidden in ["keywords", "captions", "prompt", "images", "latitude", "longitude"] {
                XCTAssertNil(payload[forbidden])
            }
        }
    }

    func testQueueRequestsRejectEmptyIdentitiesAndInvalidRevisionsBeforeEncoding() {
        let requests = [
            WorkerRequest(
                id: "persist-empty-session",
                command: .queuePersist,
                payload: .queueItemDecision(
                    sessionID: "", itemID: "item-1", revision: 1,
                    decisionID: "decision-1"
                )
            ),
            WorkerRequest(
                id: "persist-negative-revision",
                command: .queuePersist,
                payload: .queueItemDecision(
                    sessionID: "session-1", itemID: "item-1", revision: -1,
                    decisionID: "decision-1"
                )
            ),
            WorkerRequest(
                id: "persist-empty-decision",
                command: .queuePersist,
                payload: .queueItemDecision(
                    sessionID: "session-1", itemID: "item-1", revision: 1,
                    decisionID: ""
                )
            ),
        ]

        for request in requests {
            XCTAssertThrowsError(try JSONEncoder().encode(request))
        }
    }

    func testQueueSessionEventDecodesOnlyCorrelationAndStateFields() throws {
        let event = try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"queue-1","event":"queue_session","session_id":"session-1","revision":8,"state":"paused","queued":3,"analyzing":2,"ready":4,"save_queued":1,"saving":2,"saved":8,"attention":1}"#.utf8)
        )

        XCTAssertEqual(event.event, .queueSession)
        XCTAssertEqual(event.sessionID, "session-1")
        XCTAssertNil(event.itemID)
        XCTAssertEqual(event.revision, 8)
        XCTAssertNil(event.decisionID)
        XCTAssertEqual(event.state, "paused")
        XCTAssertEqual(event.queuedCount, 3)
        XCTAssertEqual(event.analyzingCount, 2)
        XCTAssertEqual(event.readyCount, 4)
        XCTAssertEqual(event.saveQueuedCount, 1)
        XCTAssertEqual(event.savingCount, 2)
        XCTAssertEqual(event.savedCount, 8)
        XCTAssertEqual(event.attentionCount, 1)
        XCTAssertNil(event.manifest)
    }

    func testQueueSessionAcceptsLongRunningSavedCount() throws {
        let event = try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"queue-1","event":"queue_session","session_id":"session-1","revision":2001,"state":"running","queued":0,"analyzing":0,"ready":0,"saved":1000,"attention":0}"#.utf8)
        )

        XCTAssertEqual(event.savedCount, 1_000)
    }

    func testQueueItemEventDecodesTrustedManifestReferenceWithoutContent() throws {
        let event = try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"queue-1","event":"queue_item","session_id":"session-1","item_id":"item-3","revision":9,"decision_id":"decision-9","state":"ready","manifest":"/private/app/runs/run-1/manifest.json"}"#.utf8)
        )

        XCTAssertEqual(event.event, .queueItem)
        XCTAssertEqual(event.sessionID, "session-1")
        XCTAssertEqual(event.itemID, "item-3")
        XCTAssertEqual(event.revision, 9)
        XCTAssertEqual(event.decisionID, "decision-9")
        XCTAssertEqual(event.state, "ready")
        XCTAssertEqual(event.manifest, "/private/app/runs/run-1/manifest.json")
        XCTAssertNil(event.uuid)
        XCTAssertNil(event.keywordsCount)
    }

    func testQueueEventsRejectMissingCorrelationInvalidRevisionAndContentFields() {
        let invalidEvents = [
            #"{"id":"queue-1","event":"queue_session","revision":1,"state":"running"}"#,
            #"{"id":"queue-1","event":"queue_item","session_id":"session-1","revision":1,"state":"ready"}"#,
            #"{"id":"queue-1","event":"queue_session","session_id":"session-1","revision":-1,"state":"running"}"#,
            #"{"id":"queue-1","event":"queue_session","session_id":"session-1","revision":1,"state":"running","queued":2147483648}"#,
            #"{"id":"queue-1","event":"queue_session","session_id":"session-1","revision":1,"decision_id":"decision-1","state":"running"}"#,
            #"{"id":"queue-1","event":"queue_item","session_id":"session-1","item_id":"item-1","revision":1,"decision_id":"decision-1","state":"ready","keywords":["private"]}"#,
            #"{"id":"queue-1","event":"queue_item","session_id":"session-1","item_id":"item-1","revision":1,"decision_id":"decision-1","state":"ready","caption":"private"}"#,
            #"{"id":"queue-1","event":"queue_item","session_id":"session-1","item_id":"item-1","revision":1,"state":"ready","prompt":"private"}"#,
            #"{"id":"queue-1","event":"queue_item","session_id":"session-1","item_id":"item-1","revision":1,"state":"ready","image":"base64"}"#,
            #"{"id":"queue-1","event":"queue_item","session_id":"session-1","item_id":"item-1","revision":1,"state":"ready","coordinates":"private"}"#,
            #"{"id":"queue-1","event":"queue_item","session_id":"session-1","item_id":"item-1","revision":1,"decision_id":"decision-1","state":"ready","manifest":"/private/run/../outside/manifest.json"}"#,
        ]

        for encoded in invalidEvents {
            XCTAssertThrowsError(
                try JSONDecoder().decode(WorkerEvent.self, from: Data(encoded.utf8)),
                encoded
            )
        }
    }

    func testReviewRequestEncodesExplicitCaptionSelections() throws {
        let request = WorkerRequest.review(
            id: "review-1",
            manifest: URL(fileURLWithPath: "/private/run/manifest.json"),
            selections: [:],
            captionSelections: ["49F027C6-0000-4000-8000-000000000000": true]
        )

        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: JSONEncoder().encode(request)) as? [String: Any]
        )
        let payload = try XCTUnwrap(object["payload"] as? [String: Any])
        XCTAssertEqual(
            payload["caption_selections"] as? [String: Bool],
            ["49F027C6-0000-4000-8000-000000000000": true]
        )
    }

    func testPhotoEventRejectsUnapprovedVisualOrLocationFields() throws {
        let unsafe = Data(#"{"id":"scan-1","event":"photo_progress","uuid":"49F027C6","state":"ready","images":["base64"],"latitude":45.4}"#.utf8)

        XCTAssertThrowsError(try JSONDecoder().decode(WorkerEvent.self, from: unsafe))
    }

    func testCompletedEventRequiresExitCode() throws {
        let incomplete = Data(#"{"id":"scan-1","event":"completed","manifest":"/tmp/run/manifest.json"}"#.utf8)

        XCTAssertThrowsError(try JSONDecoder().decode(WorkerEvent.self, from: incomplete))
    }

    func testSuccessfulCompletedEventRejectsErrorAndRepairDetails() throws {
        let contradictory = Data(#"{"id":"scan-1","event":"completed","exit_code":0,"error_codes":["MODEL_INVALID"],"safe_instruction":"ollama pull qwen3-vl:4b"}"#.utf8)

        XCTAssertThrowsError(try JSONDecoder().decode(WorkerEvent.self, from: contradictory))
    }

    func testCompletedFailureAcceptsOnlyAnExactLocalOllamaPullInstruction() throws {
        let valid = Data(#"{"id":"preflight-1","event":"completed","exit_code":2,"error_codes":["OLLAMA_MODEL_MISSING"],"next_action":"install_missing_model","safe_instruction":"ollama pull qwen3-vl:4b"}"#.utf8)

        let event = try JSONDecoder().decode(WorkerEvent.self, from: valid)

        XCTAssertEqual(event.safeInstruction, "ollama pull qwen3-vl:4b")
    }

    func testCompletedFailureRejectsUnsafeOllamaPullInstructions() {
        let instructions = [
            "ollama pull qwen3-vl:4b && open /private/tmp",
            "ollama pull ../private/model",
            "ollama pull qwen3-vl:4b --insecure",
            "ollama pull vision:cloud",
        ]

        for instruction in instructions {
            let encoded = "{\"id\":\"preflight-1\",\"event\":\"completed\",\"exit_code\":2,\"error_codes\":[\"OLLAMA_MODEL_MISSING\"],\"next_action\":\"install_missing_model\",\"safe_instruction\":\"\(instruction)\"}"
            XCTAssertThrowsError(try JSONDecoder().decode(WorkerEvent.self, from: Data(encoded.utf8)), instruction)
        }
    }

    func testPreflightRejectsModelNamesOrVersionsThatCouldExposePaths() throws {
        let modelPath = Data(#"{"id":"preflight-1","event":"completed","exit_code":2,"models":{"/Users/private/model":"0.32.1"},"error_codes":["OLLAMA_MODEL_MISSING"],"next_action":"install_missing_model"}"#.utf8)
        let versionPath = Data(#"{"id":"preflight-1","event":"completed","exit_code":2,"models":{"qwen3-vl:4b":"0.32.1/private"},"error_codes":["OLLAMA_MODEL_MISSING"],"next_action":"install_missing_model"}"#.utf8)

        XCTAssertThrowsError(try JSONDecoder().decode(WorkerEvent.self, from: modelPath))
        XCTAssertThrowsError(try JSONDecoder().decode(WorkerEvent.self, from: versionPath))
    }

    func testPhotoProgressRejectsAnUntrustedModelIdentifier() throws {
        let unsafe = Data(#"{"id":"scan-1","event":"photo_progress","uuid":"A1B2C3D4","state":"ready","model_used":"/Users/private/model","keywords_count":1}"#.utf8)

        XCTAssertThrowsError(try JSONDecoder().decode(WorkerEvent.self, from: unsafe))
    }

    func testCompletedEventRejectsManifestPathTraversal() throws {
        let unsafe = Data(#"{"id":"scan-1","event":"completed","exit_code":0,"manifest":"/tmp/run/../outside/manifest.json"}"#.utf8)

        XCTAssertThrowsError(try JSONDecoder().decode(WorkerEvent.self, from: unsafe))
    }

    func testWorkerManifestPathMustStayInsideItsPrivateRunsRoot() {
        let runsRoot = URL(fileURLWithPath: "/private/app-support/Photos Local Keyword Indexer/runs", isDirectory: true)
        let trusted = runsRoot
            .appendingPathComponent("run-1", isDirectory: true)
            .appendingPathComponent("manifest.json")
        let outside = URL(fileURLWithPath: "/private/other/manifest.json")

        XCTAssertTrue(WorkerProcess.isTrustedManifestPath(trusted, runsRoot: runsRoot))
        XCTAssertFalse(WorkerProcess.isTrustedManifestPath(outside, runsRoot: runsRoot))
    }

    func testWorkerManifestPathRejectsAHardLinkToAnExternalFile() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("photos-indexer-ipc-\\(UUID().uuidString)", isDirectory: true)
        let runsRoot = root.appendingPathComponent("runs", isDirectory: true)
        let runDirectory = runsRoot.appendingPathComponent("run-1", isDirectory: true)
        let outside = root.appendingPathComponent("outside.json")
        let manifest = runDirectory.appendingPathComponent("manifest.json")
        defer { try? FileManager.default.removeItem(at: root) }

        try FileManager.default.createDirectory(at: runDirectory, withIntermediateDirectories: true)
        try Data("{}".utf8).write(to: outside, options: .atomic)
        try FileManager.default.linkItem(at: outside, to: manifest)

        XCTAssertFalse(WorkerProcess.isTrustedManifestPath(manifest, runsRoot: runsRoot))
    }

    func testProgressEventRequiresBoundedCounters() throws {
        let invalid = Data(#"{"id":"scan-1","event":"photo_progress","uuid":"49F027C6","state":"ready","keywords_count":-1}"#.utf8)

        XCTAssertThrowsError(try JSONDecoder().decode(WorkerEvent.self, from: invalid))
    }

    func testPhotoProgressRejectsUnknownStateInsteadOfCountingItAsCompleted() throws {
        let invalid = Data(#"{"id":"scan-1","event":"photo_progress","uuid":"49F027C6","state":"future_state"}"#.utf8)

        XCTAssertThrowsError(try JSONDecoder().decode(WorkerEvent.self, from: invalid))
    }

    func testJSONLineDecoderKeepsPartialEventsUntilNewline() throws {
        var decoder = JSONLineEventDecoder()

        XCTAssertTrue(decoder.append(Data(#"{"id":"scan-1","event":"started","operation":"sc"#.utf8)).isEmpty)
        let events = decoder.append(Data("an\"}\n".utf8))

        XCTAssertEqual(events.count, 1)
        let event = try XCTUnwrap(events.first).get()
        XCTAssertEqual(event.event, .started)
        XCTAssertEqual(event.operation, "scan")
    }

    func testJSONLineDecoderResetDiscardsPartialEventBetweenHelperRuns() throws {
        var decoder = JSONLineEventDecoder()

        XCTAssertTrue(decoder.append(Data(#"{"id":"old-1","event":"started","operation":"sc"#.utf8)).isEmpty)
        decoder.reset()

        let events = decoder.append(Data("{\"id\":\"new-1\",\"event\":\"started\",\"operation\":\"scan\"}\n".utf8))
        let event = try XCTUnwrap(events.first).get()
        XCTAssertEqual(event.id, "new-1")
        XCTAssertEqual(event.operation, "scan")
    }

    func testJSONLineDecoderRejectsOversizedLineAndRecoversAfterNewline() throws {
        var decoder = JSONLineEventDecoder()
        let oversized = Data(repeating: 0x20, count: 65 * 1024)

        let rejected = decoder.append(oversized)
        XCTAssertEqual(rejected.count, 1)
        XCTAssertThrowsError(try rejected[0].get())

        let events = decoder.append(Data("\n{\"id\":\"new-2\",\"event\":\"started\",\"operation\":\"scan\"}\n".utf8))
        let event = try XCTUnwrap(events.first).get()
        XCTAssertEqual(event.id, "new-2")
        XCTAssertEqual(event.operation, "scan")
    }
}
