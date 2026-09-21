import CryptoKit
import Foundation
import XCTest
@testable import PhotosLocalKeywordIndexer

final class HistoryAutonomyTests: XCTestCase {
    func testLinkedAuthorizationLabelsOnlyTheReviewedRunAndRejectsTampering() throws {
        let root = URL(fileURLWithPath: "/private/tmp").appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let manifest = try fixture(root: root)
        _ = try RunManifestPreview.load(from: manifest)
        XCTAssertTrue(try HistoryAutonomyEvidenceStore.isAutonomous(manifestURL: manifest))
        let runs = root.appendingPathComponent("runs")
        let result = HistoryRunStore.loadResult(from: runs, expectedRoot: runs)
        XCTAssertEqual(result.runs.count, 1)
        XCTAssertTrue(try XCTUnwrap(result.runs.first).isAutonomous)
        let linkURL = manifest.deletingLastPathComponent().appendingPathComponent("autonomy.json")
        let original = try Data(contentsOf: linkURL)
        let duplicate = String(decoding: original, as: UTF8.self).replacingOccurrences(of: "\"version\":1", with: "\"version\":1,\"version\":1")
        try Data(duplicate.utf8).write(to: linkURL)
        XCTAssertThrowsError(try HistoryAutonomyEvidenceStore.isAutonomous(manifestURL: manifest))
        try original.write(to: linkURL)
        let authorization = root.appendingPathComponent("autonomous-campaigns/campaign/authorizations/activation.json")
        var value = try XCTUnwrap(JSONSerialization.jsonObject(with: Data(contentsOf: authorization)) as? [String: Any])
        value["threshold"] = 0.84
        try write(value, to: authorization)
        XCTAssertThrowsError(try HistoryAutonomyEvidenceStore.isAutonomous(manifestURL: manifest))
        let rejected = HistoryRunStore.loadResult(from: runs, expectedRoot: runs)
        XCTAssertTrue(rejected.runs.isEmpty)
        XCTAssertEqual(rejected.rejectedRunCount, 1)
    }

    func testMissingLinkRemainsManualAndUnsafeLinkCannotClaimAutonomy() throws {
        let root = URL(fileURLWithPath: "/private/tmp").appendingPathComponent(UUID().uuidString)
        defer { try? FileManager.default.removeItem(at: root) }
        let manifest = try fixture(root: root)
        let link = manifest.deletingLastPathComponent().appendingPathComponent("autonomy.json")
        let copy = root.appendingPathComponent("copy.json")
        try FileManager.default.moveItem(at: link, to: copy)
        XCTAssertFalse(try HistoryAutonomyEvidenceStore.isAutonomous(manifestURL: manifest))
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: copy)
        XCTAssertThrowsError(try HistoryAutonomyEvidenceStore.isAutonomous(manifestURL: manifest))
    }

    private func fixture(root: URL) throws -> URL {
        let run = root.appendingPathComponent("runs/reviewed")
        let campaign = root.appendingPathComponent("autonomous-campaigns/campaign")
        let position = campaign.appendingPathComponent("positions/0000000000")
        for directory in [root, root.appendingPathComponent("runs"), run, root.appendingPathComponent("autonomous-campaigns"), campaign, campaign.appendingPathComponent("authorizations"), campaign.appendingPathComponent("controls"), campaign.appendingPathComponent("positions"), position] {
            try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: false, attributes: [.posixPermissions: 0o700])
        }
        let sourceDigest = String(repeating: "a", count: 64)
        let reviewDigest = String(repeating: "b", count: 64)
        let inventoryDigest = String(repeating: "c", count: 64)
        let config: [String: Any] = ["photo_count": 3, "inference_concurrency": 2, "auto_analyze": true, "include_caption": true, "apple_maps": false, "random_selection": false, "model_policy": "adaptive", "model": NSNull(), "fast_model": "qwen3-vl:4b", "detailed_model": "qwen3-vl:4b"]
        let configurationDigest = try digest(config)
        try write(["version": 1, "campaign_id": "campaign", "created_at": "2026-09-12T16:00:00+00:00", "config": config, "settings_digest": configurationDigest, "limit": NSNull(), "inventory_digest": inventoryDigest], to: campaign.appendingPathComponent("campaign.json"))
        let payload: [String: Any] = ["campaign_id": "campaign", "decision_id": "activation", "runs_root": root.appendingPathComponent("runs").path, "settings_path": root.appendingPathComponent("settings.json").path, "limit": NSNull()]
        let control: [String: Any] = ["command": "autonomy_start", "payload": payload]
        try write(control.merging(["digest": try digest(control)]) { _, new in new }, to: campaign.appendingPathComponent("controls/activation.json"))
        let authorization: [String: Any] = ["version": 1, "campaign_id": "campaign", "control_decision_id": "activation", "origin": "autonomous_toggle", "policy_version": "autonomous-v1", "threshold": 0.85, "settings_digest": configurationDigest, "inventory_digest": inventoryDigest, "limit": NSNull(), "activated_at": "2026-09-12T16:00:00+00:00"]
        try write(authorization, to: campaign.appendingPathComponent("authorizations/activation.json"))
        let authorizationDigest = try digest(authorization)
        let identity: [String: Any] = ["campaign_id": "campaign", "position": 0, "source_run_id": "source", "source_digest": sourceDigest, "policy_version": "autonomous-v1"]
        let decision = try digest(identity)
        let selection: [String: Any] = ["keywords": ["árbol"], "caption": "Un árbol junto a una casa/camino."]
        let admission: [String: Any] = ["version": 1, "campaign_id": "campaign", "position": 0, "policy_version": "autonomous-v1", "decision_id": decision, "authorization_id": "activation", "authorization_digest": authorizationDigest, "source_run_id": "source", "source_digest": sourceDigest, "selection_digest": try digest(selection)]
        try write(admission, to: position.appendingPathComponent("decision.json"))
        try write(["run_name": "reviewed", "run_id": "reviewed", "review_digest": reviewDigest], to: position.appendingPathComponent("reviewed.json"))
        try write(["version": 1, "campaign_id": "campaign", "position": 0, "decision_id": decision, "authorization_id": "activation", "authorization_digest": authorizationDigest, "admission_digest": try digest(admission)], to: run.appendingPathComponent("autonomy.json"))
        let photo: [String: Any] = ["uuid": "12345678-1234-1234-1234-1234567890ab", "date": "2026-09-12T16:00:00", "existing_keywords": [], "proposed_keywords": ["árbol"], "model_proposed_keywords": ["árbol"], "approved_keywords": ["árbol"], "approved_caption": selection["caption"]!, "scan_state": "ready", "apply_state": "not_run", "rollback_state": "not_run", "errors": []]
        let manifest = run.appendingPathComponent("manifest.json")
        try write(["run_id": "reviewed", "created_at": "2026-09-12T16:00:00Z", "schema_version": 4, "scan_status": "ready", "reviewed_from_run_id": "source", "source_scan_digest": sourceDigest, "review_decision_digest": reviewDigest, "photos": [photo]], to: manifest)
        return manifest
    }

    private func write(_ object: [String: Any], to url: URL) throws {
        try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes]).write(to: url)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }

    private func digest(_ object: [String: Any]) throws -> String {
        SHA256.hash(data: try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes])).map { String(format: "%02x", $0) }.joined()
    }
}
