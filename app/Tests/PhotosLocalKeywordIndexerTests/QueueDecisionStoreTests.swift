import Foundation
import XCTest
@testable import PhotosLocalKeywordIndexer

final class QueueDecisionStoreTests: XCTestCase {
    func testDecisionIsStoredPrivatelyOutsideIPC() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let store = QueueDecisionStore(root: root)
        let decision = QueueReviewDecision(
            sessionID: UUID().uuidString.lowercased(),
            itemID: UUID().uuidString.lowercased(),
            revision: 2,
            decisionID: UUID().uuidString.lowercased(),
            approvedKeywords: ["gato", "perro"],
            approvedCaption: "Un gato y un perro descansan juntos."
        )

        let path = try store.write(decision)
        let data = try Data(contentsOf: path)
        let restored = try JSONDecoder().decode(QueueReviewDecision.self, from: data)
        let attributes = try FileManager.default.attributesOfItem(atPath: path.path)

        XCTAssertEqual(restored, decision)
        XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue, 0o600)
        XCTAssertTrue(path.path.contains(decision.sessionID))
        XCTAssertTrue(path.path.contains(decision.decisionID))
        try? FileManager.default.removeItem(at: root)
    }

    func testKeywordOnlyDecisionEncodesAllStrictPythonSchemaFields() throws {
        try assertDecisionJSON(
            approvedKeywords: ["gato", "perro"],
            approvedCaption: nil
        )
    }

    func testCaptionOnlyDecisionEncodesAllStrictPythonSchemaFields() throws {
        try assertDecisionJSON(
            approvedKeywords: [],
            approvedCaption: "Un gato descansa."
        )
    }

    func testDecisionWithKeywordsAndCaptionEncodesAllStrictPythonSchemaFields() throws {
        try assertDecisionJSON(
            approvedKeywords: ["gato", "perro"],
            approvedCaption: "Un gato y un perro descansan juntos."
        )
    }

    func testInvalidDecisionIsRejectedBeforeCreatingArtifacts() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        var decision = makeDecision()
        decision.sessionID = "../outside"

        XCTAssertThrowsError(try QueueDecisionStore(root: root).write(decision))
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.path))
    }

    func testDecisionRejectsUnsafeUserContent() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        var decision = makeDecision()
        decision.approvedCaption = "dato\u{202E}oculto"

        XCTAssertThrowsError(try QueueDecisionStore(root: root).write(decision))
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.path))
    }

    func testDecisionRejectsASymlinkedSessionDirectory() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let outside = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let decision = makeDecision()
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: root.path)
        try FileManager.default.createDirectory(at: outside, withIntermediateDirectories: true)
        try FileManager.default.createSymbolicLink(
            at: root.appendingPathComponent(decision.sessionID, isDirectory: true),
            withDestinationURL: outside
        )

        XCTAssertThrowsError(try QueueDecisionStore(root: root).write(decision))
        XCTAssertEqual(try FileManager.default.contentsOfDirectory(atPath: outside.path), [])
        try? FileManager.default.removeItem(at: root)
        try? FileManager.default.removeItem(at: outside)
    }

    func testEditableDraftIsAtomicallyReplacedAndRestoredPrivately() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let store = QueueDraftStore(root: root)
        var draft = QueueReviewDraftArtifact(
            sessionID: UUID().uuidString.lowercased(),
            itemID: UUID().uuidString.lowercased(),
            revision: 1,
            keywords: ["gato"],
            caption: "Un gato descansa."
        )

        let path = try store.write(draft)
        draft.revision = 2
        draft.keywords = ["gato", "lentes"]
        draft.caption = "Una persona con lentes junto a un gato."
        XCTAssertEqual(try store.write(draft), path)

        let restored = try XCTUnwrap(store.load(
            sessionID: draft.sessionID,
            itemID: draft.itemID,
            currentRevision: 2
        ))
        let attributes = try FileManager.default.attributesOfItem(atPath: path.path)
        XCTAssertEqual(restored, draft)
        XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue, 0o600)
        XCTAssertTrue(path.path.contains("/drafts/"))
        XCTAssertNil(try store.load(
            sessionID: draft.sessionID,
            itemID: draft.itemID,
            currentRevision: 3
        ))
        try? FileManager.default.removeItem(at: root)
    }

    func testDraftRejectsStaleFutureRevisionAndUnsafeContent() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let store = QueueDraftStore(root: root)
        var draft = QueueReviewDraftArtifact(
            sessionID: UUID().uuidString.lowercased(),
            itemID: UUID().uuidString.lowercased(),
            revision: 4,
            keywords: ["gato"],
            caption: nil
        )
        _ = try store.write(draft)

        XCTAssertNil(try store.load(
            sessionID: draft.sessionID,
            itemID: draft.itemID,
            currentRevision: 3
        ))
        draft.keywords = ["dato\u{202E}oculto"]
        XCTAssertThrowsError(try store.write(draft))
        try? FileManager.default.removeItem(at: root)
    }

    private func makeDecision() -> QueueReviewDecision {
        QueueReviewDecision(
            sessionID: UUID().uuidString.lowercased(),
            itemID: UUID().uuidString.lowercased(),
            revision: 2,
            decisionID: UUID().uuidString.lowercased(),
            approvedKeywords: ["gato", "perro"],
            approvedCaption: "Un gato y un perro descansan juntos."
        )
    }

    private func assertDecisionJSON(
        approvedKeywords: [String],
        approvedCaption: String?,
        file: StaticString = #filePath,
        line: UInt = #line
    ) throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let decision = QueueReviewDecision(
            sessionID: UUID().uuidString.lowercased(),
            itemID: UUID().uuidString.lowercased(),
            revision: 2,
            decisionID: UUID().uuidString.lowercased(),
            approvedKeywords: approvedKeywords,
            approvedCaption: approvedCaption
        )

        let path = try QueueDecisionStore(root: root).write(decision)
        let object = try XCTUnwrap(
            JSONSerialization.jsonObject(with: Data(contentsOf: path)) as? [String: Any],
            file: file,
            line: line
        )

        XCTAssertEqual(
            Set(object.keys),
            [
                "session_id", "item_id", "revision", "decision_id",
                "approved_keywords", "approved_caption",
            ],
            file: file,
            line: line
        )
        XCTAssertEqual(object["session_id"] as? String, decision.sessionID, file: file, line: line)
        XCTAssertEqual(object["item_id"] as? String, decision.itemID, file: file, line: line)
        XCTAssertEqual(object["revision"] as? Int, decision.revision, file: file, line: line)
        XCTAssertEqual(object["decision_id"] as? String, decision.decisionID, file: file, line: line)
        XCTAssertEqual(
            object["approved_keywords"] as? [String],
            approvedKeywords,
            file: file,
            line: line
        )
        if let approvedCaption {
            XCTAssertEqual(
                object["approved_caption"] as? String,
                approvedCaption,
                file: file,
                line: line
            )
        } else {
            XCTAssertTrue(object["approved_caption"] is NSNull, file: file, line: line)
        }
    }
}
