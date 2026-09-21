import Foundation
import XCTest
@testable import PhotosLocalKeywordIndexer

final class HistoryRootIsolationTests: XCTestCase {
    func testInjectedRootsKeepLoadAndImportSeparate() throws {
        let base = FileManager.default.temporaryDirectory.resolvingSymlinksInPath()
            .appendingPathComponent("history-root-isolation-\(UUID().uuidString)")
        defer { try? FileManager.default.removeItem(at: base) }
        let sourceRoot = base.appendingPathComponent("source")
        let destinationRoot = base.appendingPathComponent("destination")
        for root in [sourceRoot, destinationRoot] {
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
        }
        let run = sourceRoot.appendingPathComponent("fictitious-run")
        try FileManager.default.createDirectory(at: run, withIntermediateDirectories: false,
                                                attributes: [.posixPermissions: 0o700])
        let manifest = run.appendingPathComponent("manifest.json")
        try Data(#"{"run_id":"fictitious-run","created_at":"2026-09-12T10:00:00Z","scan_status":"ready","photos":[]}"#.utf8).write(to: manifest)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: manifest.path)

        XCTAssertEqual(HistoryRunStore.loadResult(from: sourceRoot, expectedRoot: sourceRoot).runs.count, 1)
        XCTAssertTrue(HistoryRunStore.loadResult(from: destinationRoot, expectedRoot: destinationRoot).runs.isEmpty)
        XCTAssertTrue(HistoryRunStore.loadResult(from: sourceRoot, expectedRoot: destinationRoot).runs.isEmpty)

        let imported = try HistoryRunStore.importManifest(from: manifest, to: destinationRoot)
        XCTAssertTrue(imported.path.hasPrefix(destinationRoot.path + "/"))
        XCTAssertEqual(HistoryRunStore.loadResult(from: sourceRoot, expectedRoot: sourceRoot).runs.count, 1)
        let destination = HistoryRunStore.loadResult(from: destinationRoot, expectedRoot: destinationRoot)
        XCTAssertEqual(destination.runs.count, 1)
        XCTAssertEqual(destination.runs.first?.manifestURL.resolvingSymlinksInPath(), imported.resolvingSymlinksInPath())
    }
}
