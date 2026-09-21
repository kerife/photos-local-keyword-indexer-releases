import CryptoKit
import Darwin
import Foundation

/// Bounded, read-only verification of the backend's run-side authorization link.
/// This does not recover campaigns, consult Photos, or authorize any operation.
enum HistoryAutonomyEvidenceStore {
    enum EvidenceError: Error { case invalid }

    static func isAutonomous(manifestURL: URL) throws -> Bool {
        let run = manifestURL.deletingLastPathComponent()
        let linkURL = run.appendingPathComponent("autonomy.json")
        var status = stat()
        if lstat(linkURL.path, &status) != 0 {
            guard errno == ENOENT else { throw EvidenceError.invalid }
            return false
        }
        let support = run.deletingLastPathComponent().deletingLastPathComponent()
        try directory(support)
        try directory(run.deletingLastPathComponent())
        try directory(run)
        let link = try read(linkURL, keys: ["version", "campaign_id", "position", "decision_id", "authorization_id", "authorization_digest", "admission_digest"])
        try version(link)
        let campaignID = try identifier(link["campaign_id"])
        let authorizationID = try identifier(link["authorization_id"])
        let position = try integer(link["position"])
        let campaignRoot = support.appendingPathComponent("autonomous-campaigns")
        let campaign = campaignRoot.appendingPathComponent(campaignID)
        for path in [campaignRoot, campaign, campaign.appendingPathComponent("authorizations"), campaign.appendingPathComponent("controls"), campaign.appendingPathComponent("positions")] { try directory(path) }
        let positionRoot = campaign.appendingPathComponent("positions").appendingPathComponent(String(format: "%010d", position))
        try directory(positionRoot)
        let metadata = try read(campaign.appendingPathComponent("campaign.json"), keys: ["version", "campaign_id", "created_at", "config", "settings_digest", "limit", "inventory_digest"])
        try version(metadata)
        guard try identifier(metadata["campaign_id"]) == campaignID,
              validTimestamp(metadata["created_at"]),
              let config = metadata["config"] as? [String: Any] else { throw EvidenceError.invalid }
        try validateConfiguration(config)
        let settingsDigest = try digestString(metadata["settings_digest"])
        let inventoryDigest = try digestString(metadata["inventory_digest"])
        let limit = try campaignLimit(metadata["limit"])
        guard try digest(config) == settingsDigest else { throw EvidenceError.invalid }
        let authorization = try read(campaign.appendingPathComponent("authorizations/\(authorizationID).json"), keys: ["version", "campaign_id", "control_decision_id", "origin", "policy_version", "threshold", "settings_digest", "inventory_digest", "limit", "activated_at"])
        try version(authorization)
        let authorizationDigest = try digestString(link["authorization_digest"])
        guard try digest(authorization) == authorizationDigest,
              authorization["campaign_id"] as? String == campaignID,
              authorization["control_decision_id"] as? String == authorizationID,
              authorization["policy_version"] as? String == "autonomous-v1",
              let threshold = authorization["threshold"] as? NSNumber,
              CFGetTypeID(threshold) != CFBooleanGetTypeID(), threshold.doubleValue == 0.85,
              authorization["settings_digest"] as? String == settingsDigest,
              authorization["inventory_digest"] as? String == inventoryDigest,
              try campaignLimit(authorization["limit"]) == limit,
              validTimestamp(authorization["activated_at"]),
              let origin = authorization["origin"] as? String,
              ["autonomous_toggle", "autonomous_resume"].contains(origin) else { throw EvidenceError.invalid }
        let command = origin == "autonomous_toggle" ? "autonomy_start" : "autonomy_resume"
        let control = try read(campaign.appendingPathComponent("controls/\(authorizationID).json"), keys: ["command", "payload", "digest"])
        guard let payload = control["payload"] as? [String: Any], control["command"] as? String == command,
              try digest(["command": command, "payload": payload]) == digestString(control["digest"]) else { throw EvidenceError.invalid }
        var expected: [String: Any] = ["campaign_id": campaignID, "decision_id": authorizationID]
        if command == "autonomy_start" {
            expected["runs_root"] = support.appendingPathComponent("runs").path
            expected["settings_path"] = support.appendingPathComponent("settings.json").path
            expected["limit"] = limit.map { $0 as Any } ?? NSNull()
        }
        guard try digest(payload) == digest(expected) else { throw EvidenceError.invalid }
        let admission = try read(positionRoot.appendingPathComponent("decision.json"), keys: ["version", "campaign_id", "position", "policy_version", "decision_id", "authorization_id", "authorization_digest", "source_run_id", "source_digest", "selection_digest"])
        try version(admission)
        let sourceID = try identifier(admission["source_run_id"])
        let sourceDigest = try digestString(admission["source_digest"])
        let decisionID = try digestString(link["decision_id"])
        let identity: [String: Any] = ["campaign_id": campaignID, "position": position, "source_run_id": sourceID, "source_digest": sourceDigest, "policy_version": "autonomous-v1"]
        guard admission["campaign_id"] as? String == campaignID,
              try integer(admission["position"]) == position,
              admission["policy_version"] as? String == "autonomous-v1",
              admission["decision_id"] as? String == decisionID,
              try digest(identity) == decisionID,
              admission["authorization_id"] as? String == authorizationID,
              admission["authorization_digest"] as? String == authorizationDigest,
              try digest(admission) == digestString(link["admission_digest"]) else { throw EvidenceError.invalid }
        let manifestData = try ManifestFileSecurity.read(from: manifestURL)
        try StrictJSONManifestValidator.validate(manifestData)
        guard let manifest = try JSONSerialization.jsonObject(with: manifestData) as? [String: Any],
              try integer(manifest["schema_version"]) == 4,
              manifest["reviewed_from_run_id"] as? String == sourceID,
              manifest["source_scan_digest"] as? String == sourceDigest,
              let photos = manifest["photos"] as? [[String: Any]], photos.count == 1,
              let keywords = photos[0]["approved_keywords"] as? [String],
              let caption = photos[0]["approved_caption"], caption is NSNull || caption is String else { throw EvidenceError.invalid }
        let reviewed = try read(positionRoot.appendingPathComponent("reviewed.json"), keys: ["run_name", "run_id", "review_digest"])
        guard reviewed["run_name"] as? String == run.lastPathComponent,
              try identifier(reviewed["run_id"]) == identifier(manifest["run_id"]),
              try digestString(reviewed["review_digest"]) == digestString(manifest["review_decision_digest"]),
              try digest(["keywords": keywords, "caption": caption]) == digestString(admission["selection_digest"]) else { throw EvidenceError.invalid }
        return true
    }

    private static func read(_ url: URL, keys: Set<String>) throws -> [String: Any] {
        let data = try ManifestFileSecurity.read(from: url, maximumBytes: 64 * 1024)
        try StrictJSONManifestValidator.validateSyntax(data)
        guard let value = try JSONSerialization.jsonObject(with: data) as? [String: Any], Set(value.keys) == keys else { throw EvidenceError.invalid }
        return value
    }

    private static func directory(_ url: URL) throws {
        var value = stat()
        guard lstat(url.path, &value) == 0, value.st_mode & S_IFMT == S_IFDIR,
              value.st_uid == getuid(), value.st_mode & 0o7777 == 0o700 else { throw EvidenceError.invalid }
    }

    private static func integer(_ value: Any?) throws -> Int {
        guard let number = value as? NSNumber, CFGetTypeID(number) != CFBooleanGetTypeID(),
              !["f", "d"].contains(String(cString: number.objCType)),
              (0 ... Int64(Int32.max)).contains(number.int64Value) else { throw EvidenceError.invalid }
        return number.intValue
    }

    private static func version(_ value: [String: Any]) throws {
        guard try integer(value["version"]) == 1 else { throw EvidenceError.invalid }
    }

    private static func identifier(_ value: Any?) throws -> String {
        guard let text = value as? String, text.utf8.count <= 64,
              text != ".", text != "..",
              text.range(of: "^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$", options: .regularExpression) != nil else { throw EvidenceError.invalid }
        return text
    }

    private static func digestString(_ value: Any?) throws -> String {
        guard let text = value as? String, text.range(of: "^[a-f0-9]{64}$", options: .regularExpression) != nil else { throw EvidenceError.invalid }
        return text
    }

    private static func campaignLimit(_ value: Any?) throws -> Int? {
        if value is NSNull { return nil }
        let number = try integer(value)
        guard number > 0 else { throw EvidenceError.invalid }
        return number
    }

    private static func validTimestamp(_ value: Any?) -> Bool {
        guard let text = value as? String else { return false }
        let formatter = ISO8601DateFormatter()
        formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        if formatter.date(from: text) != nil { return true }
        formatter.formatOptions = [.withInternetDateTime]
        return formatter.date(from: text) != nil
    }

    private static func digest(_ object: [String: Any]) throws -> String {
        let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys, .withoutEscapingSlashes])
        return SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()
    }

    private static func validateConfiguration(_ config: [String: Any]) throws {
        guard Set(config.keys) == ["photo_count", "inference_concurrency", "auto_analyze", "include_caption", "apple_maps", "random_selection", "model_policy", "model", "fast_model", "detailed_model"],
              (1 ... 50).contains(try integer(config["photo_count"])),
              (1 ... 4).contains(try integer(config["inference_concurrency"])),
              let policy = config["model_policy"] as? String, ["single", "adaptive"].contains(policy) else { throw EvidenceError.invalid }
        for key in ["auto_analyze", "include_caption", "apple_maps", "random_selection"] {
            guard let value = config[key] as? NSNumber, CFGetTypeID(value) == CFBooleanGetTypeID() else { throw EvidenceError.invalid }
        }
        for key in ["fast_model", "detailed_model", "model"] {
            if key == "model", config[key] is NSNull, policy == "adaptive" { continue }
            guard let model = config[key] as? String, OllamaModelPresentationPolicy.isValidModelName(model) else { throw EvidenceError.invalid }
        }
    }
}
