import XCTest
@testable import PhotosLocalKeywordIndexer

final class ScanRecoveryTests: XCTestCase {
    func testMissingModelsOfferAReadinessRecoveryAction() {
        XCTAssertEqual(ScanModelRecoveryCopy.buttonTitle, "Volver a Preparación")
        XCTAssertTrue(ScanModelRecoveryCopy.buttonAccessibilityHint.contains("comprobar"))
        XCTAssertTrue(ScanModelRecoveryCopy.buttonAccessibilityHint.contains("no descargará modelos"))
        XCTAssertFalse(ScanModelRecoveryCopy.buttonAccessibilityHint.contains("ollama pull"))
    }

    func testMissingModelsRecoveryDoesNotExposePathsOrPrivateData() {
        let hint = ScanModelRecoveryCopy.buttonAccessibilityHint

        XCTAssertFalse(hint.contains("/"))
        XCTAssertFalse(hint.contains("coordenadas"))
        XCTAssertFalse(hint.contains("caption"))
    }
}
