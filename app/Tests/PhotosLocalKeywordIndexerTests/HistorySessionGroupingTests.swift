import Foundation
import XCTest
@testable import PhotosLocalKeywordIndexer

final class HistorySessionGroupingTests: XCTestCase {
    func testPrivateSessionGroupsReferencedRunsAndLeavesLegacyRunsStandalone() throws {
        let fixture = try makeFixture()
        defer { try? FileManager.default.removeItem(at: fixture.root) }

        let sourceID = "49f027c6-0000-4000-8000-000000000010"
        let reviewedID = "49f027c6-0000-4000-8000-000000000011"
        let legacyID = "49f027c6-0000-4000-8000-000000000012"
        try writeManifest(runID: sourceID, createdAt: "2026-08-25T16:00:00Z", under: fixture.runs)
        try writeManifest(runID: reviewedID, createdAt: "2026-08-25T16:01:00Z", under: fixture.runs)
        try writeManifest(runID: legacyID, createdAt: "2026-08-25T16:02:00Z", under: fixture.runs)
        try writeSession(
            sessionID: fixture.sessionID,
            state: "paused",
            itemState: "verified",
            sourceRunID: sourceID,
            reviewedRunID: reviewedID,
            action: "persist",
            under: fixture.sessions
        )

        XCTAssertTrue(HistoryRunStore.isTrustedRunsRoot(fixture.runs, expected: fixture.runs))
        XCTAssertNoThrow(try RunManifestPreview.load(
            from: fixture.runs
                .appendingPathComponent("run-\(sourceID)", isDirectory: true)
                .appendingPathComponent("manifest.json")
        ))

        let result = HistoryRunStore.loadResult(from: fixture.runs, expectedRoot: fixture.runs)

        XCTAssertEqual(result.runs.map(\.runID), [legacyID, reviewedID, sourceID])
        XCTAssertEqual(result.sessions.count, 1)
        let session = try XCTUnwrap(result.sessions.first)
        XCTAssertEqual(session.runs.map(\.runID), [reviewedID, sourceID])
        XCTAssertEqual(session.attempts.count, 1)
        XCTAssertEqual(session.attempts[0].stateLabel, "Guardada y verificada")
        XCTAssertEqual(session.attempts[0].decisionLabels, ["Guardar en Fotos"])
        XCTAssertEqual(session.stateLabel, "Pausada")
        XCTAssertEqual(session.rollbackAvailabilityText, "Rollback no disponible en esta sesión.")
        XCTAssertEqual(result.standaloneRuns.map(\.runID), [legacyID])
        XCTAssertEqual(
            result.displayRows.map(\.id),
            [
                "session-\(fixture.sessionID)",
                "run-\(reviewedID)",
                "run-\(sourceID)",
                "run-\(legacyID)"
            ]
        )
    }

    func testUntrustedSessionIndexFallsBackToOrdinaryHistory() throws {
        let fixture = try makeFixture()
        defer { try? FileManager.default.removeItem(at: fixture.root) }

        let runID = "49f027c6-0000-4000-8000-000000000020"
        try writeManifest(runID: runID, createdAt: "2026-08-25T16:00:00Z", under: fixture.runs)
        let sessionDirectory = fixture.sessions.appendingPathComponent(fixture.sessionID, isDirectory: true)
        try FileManager.default.createDirectory(at: sessionDirectory, withIntermediateDirectories: false)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: sessionDirectory.path)
        let external = fixture.root.appendingPathComponent("external.json")
        try sessionData(
            sessionID: fixture.sessionID,
            sourceRunID: runID,
            reviewedRunID: nil
        ).write(to: external)
        try FileManager.default.createSymbolicLink(
            at: sessionDirectory.appendingPathComponent("session.json"),
            withDestinationURL: external
        )

        let result = HistoryRunStore.loadResult(from: fixture.runs, expectedRoot: fixture.runs)

        XCTAssertTrue(result.sessions.isEmpty)
        XCTAssertEqual(result.standaloneRuns.map(\.runID), [runID])
        XCTAssertEqual(result.runs.map(\.runID), [runID])
    }

    func testSessionPresentationDoesNotExposePhotoContentOrLocalPaths() throws {
        let fixture = try makeFixture()
        defer { try? FileManager.default.removeItem(at: fixture.root) }

        let runID = "49f027c6-0000-4000-8000-000000000030"
        try writeManifest(
            runID: runID,
            createdAt: "2026-08-25T16:00:00Z",
            title: "Paciente privado",
            keyword: "receta oftalmológica",
            under: fixture.runs
        )
        try writeSession(
            sessionID: fixture.sessionID,
            state: "attention",
            itemState: "uncertain",
            sourceRunID: runID,
            reviewedRunID: nil,
            action: "rescan",
            under: fixture.sessions
        )

        let result = HistoryRunStore.loadResult(from: fixture.runs, expectedRoot: fixture.runs)
        let session = try XCTUnwrap(result.sessions.first)
        let presentation = session.presentationText

        XCTAssertTrue(presentation.contains("Requiere atención"))
        XCTAssertTrue(presentation.contains("Reanalizar"))
        XCTAssertFalse(presentation.contains("Paciente privado"))
        XCTAssertFalse(presentation.contains("receta oftalmológica"))
        XCTAssertFalse(presentation.contains(fixture.root.path))
        XCTAssertFalse(presentation.contains("manifest.json"))
        XCTAssertFalse(presentation.contains(fixture.sessionID))
    }

    func testMissingSessionRootKeepsCurrentHistoryFallback() throws {
        let fixture = try makeFixture(createSessionsRoot: false)
        defer { try? FileManager.default.removeItem(at: fixture.root) }

        let runID = "49f027c6-0000-4000-8000-000000000040"
        try writeManifest(runID: runID, createdAt: "2026-08-25T16:00:00Z", under: fixture.runs)

        let result = HistoryRunStore.loadResult(from: fixture.runs, expectedRoot: fixture.runs)

        XCTAssertTrue(result.sessions.isEmpty)
        XCTAssertEqual(result.standaloneRuns, result.runs)
    }

    private struct Fixture {
        let root: URL
        let runs: URL
        let sessions: URL
        let sessionID: String
    }

    private func makeFixture(createSessionsRoot: Bool = true) throws -> Fixture {
        let root = URL(fileURLWithPath: "/private/tmp", isDirectory: true)
            .appendingPathComponent("history-session-test-runs", isDirectory: true)
            .appendingPathComponent("history-session-tests-\(UUID().uuidString)", isDirectory: true)
        let support = root.appendingPathComponent("Photos Local Keyword Indexer", isDirectory: true)
        let runs = support.appendingPathComponent("runs", isDirectory: true)
        let sessions = support.appendingPathComponent("queue-sessions", isDirectory: true)
        try FileManager.default.createDirectory(at: runs, withIntermediateDirectories: true)
        if createSessionsRoot {
            try FileManager.default.createDirectory(at: sessions, withIntermediateDirectories: true)
            try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: sessions.path)
        }
        return Fixture(
            root: root,
            runs: runs,
            sessions: sessions,
            sessionID: "49f027c6-0000-4000-8000-000000000001"
        )
    }

    private func writeManifest(
        runID: String,
        createdAt: String,
        title: String = "",
        keyword: String? = nil,
        under runs: URL
    ) throws {
        let directory = runs.appendingPathComponent("run-\(runID)", isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        let photo: [String: Any] = [
            "uuid": "59f027c6-0000-4000-8000-000000000001",
            "title": title,
            "date": "2026-08-25T16:00:00",
            "existing_keywords": keyword.map { [$0] } ?? [],
            "proposed_keywords": [],
            "confidence": 0.9,
            "scan_state": "noop",
            "apply_state": "not_run",
            "rollback_state": "not_run",
            "errors": []
        ]
        let object: [String: Any] = [
            "run_id": runID,
            "created_at": createdAt,
            "scan_status": "ready",
            "photos": [photo]
        ]
        let manifest = directory.appendingPathComponent("manifest.json")
        try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]).write(to: manifest)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: manifest.path)
    }

    private func writeSession(
        sessionID: String,
        state: String,
        itemState: String,
        sourceRunID: String?,
        reviewedRunID: String?,
        action: String,
        under sessions: URL
    ) throws {
        let directory = sessions.appendingPathComponent(sessionID, isDirectory: true)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: directory.path)
        let data = sessionData(
            sessionID: sessionID,
            state: state,
            itemState: itemState,
            sourceRunID: sourceRunID,
            reviewedRunID: reviewedRunID,
            action: action
        )
        let session = directory.appendingPathComponent("session.json")
        try data.write(to: session)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: session.path)
    }

    private func sessionData(
        sessionID: String,
        state: String = "running",
        itemState: String = "ready",
        sourceRunID: String?,
        reviewedRunID: String?,
        action: String = "persist"
    ) -> Data {
        let itemID = "69f027c6-0000-4000-8000-000000000001"
        var item: [String: Any] = [
            "item_id": itemID,
            "revision": 1,
            "state": itemState,
            "source_run_id": NSNull(),
            "reviewed_run_id": NSNull()
        ]
        if let sourceRunID { item["source_run_id"] = sourceRunID }
        if let reviewedRunID { item["reviewed_run_id"] = reviewedRunID }
        let object: [String: Any] = [
            "schema_version": 1,
            "session_id": sessionID,
            "revision": 3,
            "state": state,
            "config": [
                "photo_count": 10,
                "inference_concurrency": 2,
                "auto_analyze": true,
                "include_caption": true,
                "apple_maps": false,
                "random_selection": true,
                "model_policy": "single",
                "model": "qwen3-vl:4b",
                "fast_model": "qwen3-vl:4b",
                "detailed_model": "qwen3-vl:4b"
            ],
            "items": [item],
            "decisions": [[
                "decision_id": "79f027c6-0000-4000-8000-000000000001",
                "item_id": itemID,
                "revision": 1,
                "action": action
            ]]
        ]
        return try! JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    }
}
