import XCTest
@testable import PhotosLocalKeywordIndexer

final class UpdateChannelTests: XCTestCase {
    func testOnlyOfficialReleaseChannelMayStartUpdater() {
        XCTAssertFalse(UpdateService.permitsUpdates(buildChannel: nil))
        XCTAssertFalse(UpdateService.permitsUpdates(buildChannel: "development"))
        XCTAssertFalse(UpdateService.permitsUpdates(buildChannel: "beta"))
        XCTAssertTrue(UpdateService.permitsUpdates(buildChannel: "release"))
    }
}
