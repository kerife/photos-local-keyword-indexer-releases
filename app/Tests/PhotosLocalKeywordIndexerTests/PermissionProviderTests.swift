import XCTest
@testable import PhotosLocalKeywordIndexer

@MainActor
final class PermissionProviderTests: XCTestCase {
    func testInitialAndRefreshReadsUseInjectedProvider() {
        var reads = 0
        var next = PhotosPermissionState.denied
        let checker = PermissionChecker(
            readPhotosState: { reads += 1; return next },
            requestPhotosState: { XCTFail("Passive reads must not request access"); return .restricted },
            openSettings: { _ in XCTFail("Passive reads must not open settings"); return false }
        )

        XCTAssertEqual(reads, 1)
        XCTAssertEqual(checker.photos, .denied)
        XCTAssertEqual(checker.automation, .instructionsAvailable)
        next = .limited
        checker.refresh()
        XCTAssertEqual(reads, 2)
        XCTAssertEqual(checker.photos, .limited)
    }

    func testRequestUsesOnlyInjectedAsyncProvider() async {
        var requests = 0
        var reads = 0
        let checker = PermissionChecker(
            readPhotosState: { reads += 1; return .notDetermined },
            requestPhotosState: { requests += 1; return .authorized },
            openSettings: { _ in XCTFail("Request must not open settings"); return false }
        )

        await checker.requestPhotosAccess()
        XCTAssertEqual(requests, 1)
        XCTAssertEqual(reads, 1)
        XCTAssertEqual(checker.photos, .authorized)
    }

    func testSettingsRoutesAndResultAreForwardedToInjectedProvider() {
        var routes: [PermissionSettingsRoute] = []
        let checker = PermissionChecker(
            readPhotosState: { .restricted },
            requestPhotosState: { XCTFail("Settings must not request access"); return .restricted },
            openSettings: { route in routes.append(route); return route == .photos }
        )

        XCTAssertTrue(checker.openPhotosSettings())
        XCTAssertFalse(checker.openAutomationSettings())
        XCTAssertEqual(routes, [.photos, .automation])
        XCTAssertEqual(checker.photos, .restricted)
    }
}
