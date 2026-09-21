import Foundation
import XCTest
@testable import PhotosLocalKeywordIndexer

final class AutonomyTests: XCTestCase {
    static let snapshot = #"{"id":"start","event":"autonomy_campaign","campaign_id":"campaign","revision":1,"state":"running","total":3,"examined":1,"analyzed":1,"saved":1,"no_change":0,"attention":0,"remaining":2,"in_flight":0,"invalid_count":2,"reason":"none"}"#

    func testCampaignWireDecodesContentFreeProgress() throws {
        let event = try JSONDecoder().decode(WorkerEvent.self, from: Data(Self.snapshot.utf8))
        XCTAssertEqual(event.state, "running")
        XCTAssertEqual(event.savedCount, 1)
    }

    func testEveryPausedCampaignRequiresANewBoundedPilot() throws {
        for reason in ["recovered", "user_pause"] {
            let json = Self.snapshot
                .replacingOccurrences(of: "\"state\":\"running\"", with: "\"state\":\"paused\"")
                .replacingOccurrences(of: "\"reason\":\"none\"", with: "\"reason\":\"\(reason)\"")
            let event = try JSONDecoder().decode(WorkerEvent.self, from: Data(json.utf8))
            let presentation = AutonomyReviewPresentation(
                campaign: event.autonomyCampaign,
                activities: [],
                statusReady: true,
                activationPending: false,
                pausePending: false,
                controlPending: false,
                error: nil
            )
            XCTAssertTrue(presentation.requiresFreshBoundedPilot, reason)
            XCTAssertTrue(presentation.accessibilityLabel.contains("nuevo piloto limitado"), reason)
        }
    }

    func testCampaignWireRejectsMissingCountersAndContent() {
        for value in [Self.snapshot.replacingOccurrences(of: ",\"invalid_count\":2", with: ""),
                      Self.snapshot.replacingOccurrences(of: "\"remaining\":2", with: "\"remaining\":3"),
                      Self.snapshot.replacingOccurrences(of: "\"reason\":\"none\"", with: "\"reason\":\"none\",\"keywords\":[]")] {
            XCTAssertThrowsError(try JSONDecoder().decode(WorkerEvent.self, from: Data(value.utf8)))
        }
    }

    func testAutonomyActivityWireDecodesOnlyEphemeralThumbnailIdentity() throws {
        let event = try JSONDecoder().decode(
            WorkerEvent.self,
            from: Data(#"{"id":"campaign","event":"autonomy_activity","campaign_id":"campaign","revision":3,"position":7,"state":"analyzing","photos_local_identifier":"A1B2/C3D4"}"#.utf8)
        )

        XCTAssertEqual(event.autonomyActivity?.campaignID, "campaign")
        XCTAssertEqual(event.autonomyActivity?.position, 7)
        XCTAssertEqual(event.autonomyActivity?.state, .analyzing)
        XCTAssertEqual(event.autonomyActivity?.photosLocalIdentifier, "A1B2/C3D4")
    }

    func testAutonomyActivityWireRejectsContentAndTerminalStates() {
        let base = #"{"id":"campaign","event":"autonomy_activity","campaign_id":"campaign","revision":3,"position":7,"state":"analyzing","photos_local_identifier":"A1B2/C3D4"}"#
        for value in [
            base.replacingOccurrences(of: "\"state\":\"analyzing\"", with: "\"state\":\"verified\""),
            base.replacingOccurrences(of: "}", with: ",\"keywords\":[\"private\"]}"),
        ] {
            XCTAssertThrowsError(try JSONDecoder().decode(WorkerEvent.self, from: Data(value.utf8)))
        }
    }

    func testStartAlwaysEncodesNullAndBoundedScope() throws {
        for limit: Int? in [nil, 3] {
            let request = WorkerRequest.autonomyStart(id: "start", campaignID: "campaign", decisionID: "decision", runsRoot: URL(fileURLWithPath: "/private/tmp/runs"), settings: URL(fileURLWithPath: "/private/tmp/settings.json"), limit: limit)
            let object = try XCTUnwrap(JSONSerialization.jsonObject(with: JSONEncoder().encode(request)) as? [String: Any])
            let payload = try XCTUnwrap(object["payload"] as? [String: Any])
            XCTAssertEqual(Set(payload.keys), ["campaign_id", "decision_id", "runs_root", "settings_path", "limit"])
            if limit == nil { XCTAssertTrue(payload["limit"] is NSNull) } else { XCTAssertEqual(payload["limit"] as? Int, 3) }
        }
    }

    func testAutonomyControlsCanDrainRetainedManualOperation() {
        XCTAssertFalse(WorkerProcess.hasSubmissionConflict(activeRequestID: "legacy", activeQueueSessionID: "manual", requestCommand: .autonomyStart, requestQueueSessionID: nil))
        XCTAssertFalse(WorkerProcess.hasSubmissionConflict(activeRequestID: nil, activeQueueSessionID: "manual", requestCommand: .autonomyStatus, requestQueueSessionID: nil))
    }

    func testBackgroundCampaignStreamKeepsIdentityAfterControlCompletion() {
        XCTAssertTrue(WorkerProcess.shouldAcceptEvent(eventID: "finished", activeRequestID: nil, event: .autonomyCampaign, eventSessionID: nil, activeQueueSessionID: "manual", eventCampaignID: "campaign", activeAutonomyCampaignID: "campaign"))
        XCTAssertFalse(WorkerProcess.shouldAcceptEvent(eventID: "finished", activeRequestID: nil, event: .autonomyCampaign, eventSessionID: nil, activeQueueSessionID: "manual", activeAutonomyRequestIDs: ["finished"], eventCampaignID: "other", activeAutonomyCampaignID: "campaign"))
    }

    func testCampaignJSONLRejectsDuplicateKeys() throws {
        var decoder = JSONLineEventDecoder()
        let line = Self.snapshot.replacingOccurrences(of: "\"revision\":1", with: "\"revision\":1,\"revision\":2") + "\n"
        let result = try XCTUnwrap(decoder.append(Data(line.utf8)).first)
        XCTAssertThrowsError(try result.get())
    }

    func testAutonomyRejectsTraversalIdentitiesAndInvalidCaps() {
        for campaign in ["..", ".", "-bad", "bad/name"] {
            let request = WorkerRequest.autonomyResume(id: "resume", campaignID: campaign, decisionID: "decision")
            XCTAssertThrowsError(try JSONEncoder().encode(request))
        }
        for limit in [0, -1, Int(Int32.max) + 1] {
            let request = WorkerRequest.autonomyStart(id: "start", campaignID: "campaign", decisionID: "decision", runsRoot: URL(fileURLWithPath: "/private/tmp/runs"), settings: URL(fileURLWithPath: "/private/tmp/settings.json"), limit: limit)
            XCTAssertThrowsError(try JSONEncoder().encode(request))
        }
    }

    func testCampaignJSONLRejectsIntegralFloatCountersAndRevisions() throws {
        for field in ["revision", "examined", "analyzed", "saved"] {
            for number in ["1.0", "1e0", "true", "\"1\""] {
                var decoder = JSONLineEventDecoder()
                let line = Self.snapshot.replacingOccurrences(of: "\"\(field)\":1", with: "\"\(field)\":\(number)") + "\n"
                let result = try XCTUnwrap(decoder.append(Data(line.utf8)).first)
                XCTAssertThrowsError(try result.get(), "\(field)=\(number)")
            }
        }
    }
}
