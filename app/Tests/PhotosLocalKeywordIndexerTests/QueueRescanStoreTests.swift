import Foundation
import XCTest
@testable import PhotosLocalKeywordIndexer

final class QueueRescanStoreTests: XCTestCase {
    func testAdvancedRescanIsAValidatedPrivateOpaqueArtifact() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString.lowercased(), isDirectory: true)
        let artifact = makeArtifact()

        let path = try QueueRescanStore(root: root).write(artifact)
        let data = try Data(contentsOf: path)
        let restored = try JSONDecoder().decode(QueueRescanArtifact.self, from: data)
        let object = try XCTUnwrap(JSONSerialization.jsonObject(with: data) as? [String: Any])
        let attributes = try FileManager.default.attributesOfItem(atPath: path.path)

        XCTAssertEqual(restored, artifact)
        XCTAssertEqual((attributes[.posixPermissions] as? NSNumber)?.intValue, 0o600)
        XCTAssertEqual(
            Set(object.keys),
            [
                "session_id", "item_id", "revision", "decision_id", "model", "profile",
                "layers", "additional_information", "analysis_prompt", "reset_prompt", "reset_edits",
            ]
        )
        XCTAssertTrue(path.path.contains("/rescans/"))
        try? FileManager.default.removeItem(at: root)
    }

    func testAdvancedRescanRejectsSecretsCoordinatesAndConflictingReset() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString.lowercased(), isDirectory: true)
        var artifact = makeArtifact()
        artifact.options.additionalInformation = "API token: private-value"
        XCTAssertThrowsError(try QueueRescanStore(root: root).write(artifact))

        artifact = makeArtifact()
        artifact.options.analysisPrompt = "Usa 19.4326,-99.1332 como referencia"
        XCTAssertThrowsError(try QueueRescanStore(root: root).write(artifact))

        artifact = makeArtifact()
        artifact.options.resetPrompt = true
        XCTAssertThrowsError(try QueueRescanStore(root: root).write(artifact))
        XCTAssertFalse(FileManager.default.fileExists(atPath: root.path))
    }

    func testRestoreDefaultPromptClearsOnlyTheEditableBody() {
        var options = QueueRescanOptions.defaults(model: "qwen3-vl:4b")
        options.additionalInformation = "La escena pertenece a una colección deportiva."
        options.analysisPrompt = "Prioriza equipos y mascotas visibles."

        options.restoreDefaultPrompt()

        XCTAssertNil(options.analysisPrompt)
        XCTAssertTrue(options.resetPrompt)
        XCTAssertEqual(options.additionalInformation, "La escena pertenece a una colección deportiva.")
        XCTAssertTrue(options.layers.places)
    }

    private func makeArtifact() -> QueueRescanArtifact {
        QueueRescanArtifact(
            sessionID: UUID().uuidString.lowercased(),
            itemID: UUID().uuidString.lowercased(),
            revision: 2,
            decisionID: UUID().uuidString.lowercased(),
            options: QueueRescanOptions(
                model: "qwen3-vl:8b",
                profile: .freeLocal,
                layers: QueueRescanLayers(
                    places: true,
                    documentsText: true,
                    peopleAccessories: false,
                    semanticNormalization: true
                ),
                additionalInformation: "La escena pertenece a una colección deportiva.",
                analysisPrompt: "Prioriza equipos y mascotas visibles.",
                resetPrompt: false
            )
        )
    }
}
