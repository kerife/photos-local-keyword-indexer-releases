import Foundation
import XCTest
@testable import PhotosLocalKeywordIndexer

final class SelectionStrategyTests: XCTestCase {
    func testLegacyManifestDefaultsToRecentSelection() throws {
        let preview = try decode("""
        {"run_id":"legacy","created_at":"2026-08-25T16:00:00Z","scan_status":"ready","photos":[]}
        """)

        XCTAssertEqual(preview.selectionStrategyText, "Selección: fotos recientes elegibles.")
    }

    func testRandomManifestExposesStrategyWithoutSeedOrLocation() throws {
        let preview = try decode("""
        {"run_id":"random","created_at":"2026-08-25T16:00:00Z","scan_status":"ready","selection":{"strategy":"random"},"photos":[]}
        """)

        XCTAssertEqual(preview.selectionStrategyText, "Selección: fotos elegibles seleccionadas al azar.")
        XCTAssertFalse(preview.selectionStrategyText.contains("semilla"))
        XCTAssertFalse(preview.selectionStrategyText.contains("coorden"))
    }

    func testTargetedAndUnknownStrategiesDoNotPretendToBeRecent() throws {
        let targeted = try decode("""
        {"run_id":"targeted","created_at":"2026-08-25T16:00:00Z","scan_status":"ready","selection":{"strategy":"targeted"},"photos":[]}
        """)
        let unknown = try decode("""
        {"run_id":"unknown","created_at":"2026-08-25T16:00:00Z","scan_status":"ready","selection":{"strategy":"future"},"photos":[]}
        """)

        XCTAssertEqual(targeted.selectionStrategyText, "Selección: fotos elegibles dirigidas.")
        XCTAssertEqual(targeted.selectionStrategySystemImage, "scope")
        XCTAssertEqual(unknown.selectionStrategyText, "Selección: alcance no disponible.")
        XCTAssertEqual(unknown.selectionStrategySystemImage, "questionmark.circle")
    }

    func testLimitedPhotosAccessIsVisibleWithoutExposingPrivateSelectionData() throws {
        let preview = try decode("""
        {"run_id":"limited","created_at":"2026-08-25T16:00:00Z","scan_status":"ready","selection":{"access":"limited"},"photos":[]}
        """)

        XCTAssertEqual(
            preview.photosAccessText,
            "Acceso a Fotos limitado: solo se analizaron las fotos visibles."
        )
        XCTAssertEqual(preview.photosAccessSystemImage, "person.crop.circle.badge.exclamationmark")
        XCTAssertEqual(
            preview.photosAccessAccessibilityLabel,
            "Acceso a Fotos limitado: solo se analizaron las fotos visibles."
        )
        XCTAssertFalse(preview.photosAccessText?.contains("/" ) == true)
        XCTAssertFalse(preview.photosAccessText?.contains("coorden") == true)
    }

    func testAuthorizedPhotosAccessHasExplicitFullAccessCopy() throws {
        let preview = try decode("""
        {"run_id":"authorized","created_at":"2026-08-25T16:00:00Z","scan_status":"ready","selection":{"access":"authorized"},"photos":[]}
        """)

        XCTAssertEqual(preview.photosAccessText, "Acceso a Fotos: biblioteca disponible.")
        XCTAssertEqual(preview.photosAccessSystemImage, "checkmark.shield")
    }

    private func decode(_ json: String) throws -> RunManifestPreview {
        try JSONDecoder().decode(RunManifestPreview.self, from: Data(json.utf8))
    }
}
