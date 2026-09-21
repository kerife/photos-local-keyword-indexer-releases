import XCTest
@testable import PhotosLocalKeywordIndexer

final class TechnicalTraceTests: XCTestCase {
    func testStrictManifestValidationAcceptsPersistedTechnicalTrace() throws {
        let data = Data(#"""
        {
          "run_id":"trace-run",
          "created_at":"2026-09-01T12:00:00Z",
          "schema_version":2,
          "scan_status":"ready",
          "photos":[{
            "uuid":"12345678-1234-1234-1234-1234567890ab",
            "date":"2026-09-01T12:00:00",
            "existing_keywords":[],
            "proposed_keywords":["canal"],
            "scan_state":"ready",
            "apply_state":"not_run",
            "rollback_state":"not_run",
            "errors":[],
            "technical_trace":{
              "prompt_effective":"Analiza evidencia visual.",
              "prompt_version":"vision-prompt-v1",
              "prompt_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
              "ollama_version":"0.12.7",
              "used_gps":false,
              "used_apple_maps":false,
              "used_landmark":false,
              "place_context":[],
              "durations_ms":{"total":1}
            }
          }]
        }
        """#.utf8)

        XCTAssertNoThrow(try RunManifestPreview.decodeStrict(data))
    }

    func testPreviewAndInspectorExposeOnlySanitizedManifestTrace() throws {
        let data = Data(#"""
        {
          "uuid":"12345678-1234-1234-1234-1234567890ab",
          "photos_local_identifier":"local-1",
          "title":"Canal",
          "date":"2026-09-01T12:00:00",
          "date_timezone":null,
          "existing_keywords":[],
          "proposed_keywords":["canal"],
          "contains_people":false,
          "contains_text":false,
          "confidence":0.91,
          "model_used":"qwen3-vl:4b",
          "model_reason":"location_context",
          "scan_state":"ready",
          "apply_state":"not_run",
          "applied_keywords":[],
          "rollback_state":"not_run",
          "rolled_back_keywords":[],
          "errors":[],
          "technical_trace":{
            "prompt_effective":"Analiza evidencia visual y no transcribas texto literal.",
            "prompt_version":"vision-prompt-v1",
            "prompt_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            "ollama_version":"0.12.7",
            "used_gps":true,
            "used_apple_maps":true,
            "used_landmark":false,
            "place_context":["Canal Grande"],
            "durations_ms":{"metadata":2,"export":3,"context":5,"inference":11,"postprocess":7,"total":28}
          }
        }
        """#.utf8)

        let photo = try JSONDecoder().decode(PreviewPhoto.self, from: data)
        XCTAssertEqual(photo.technicalTrace?.promptVersion, "vision-prompt-v1")
        XCTAssertEqual(photo.technicalTrace?.placeContext, ["Canal Grande"])
        XCTAssertEqual(photo.technicalTrace?.durationsMilliseconds["total"], 28)

        var item = QueueReviewItem(id: "attempt-1", ordinal: 0)
        item.attach(manifestURL: URL(fileURLWithPath: "/private/run/manifest.json"), photo: photo)
        let inspector = ContinuousReviewInspectorSnapshot(item: item)

        XCTAssertEqual(inspector.promptVersion, "vision-prompt-v1")
        XCTAssertEqual(inspector.ollamaVersion, "0.12.7")
        XCTAssertEqual(inspector.promptHash, String(repeating: "a", count: 64))
        XCTAssertEqual(inspector.promptEffective, "Analiza evidencia visual y no transcribas texto literal.")
        XCTAssertEqual(inspector.placeContext, "Canal Grande")
        XCTAssertEqual(inspector.stageDurations, "Metadata 2 ms · Export 3 ms · Contexto 5 ms · Inferencia 11 ms · Postproceso 7 ms · Total 28 ms")
        XCTAssertFalse(inspector.promptEffective.contains("19.4326"))
    }

    func testStrictManifestValidationAcceptsSchemaFourReviewTrace() throws {
        let data = Data(#"""
        {
          "run_id":"review-run",
          "created_at":"2026-09-01T12:00:00Z",
          "schema_version":4,
          "scan_status":"ready",
          "reviewed_from_run_id":"source-run",
          "source_scan_digest":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "review_decision_digest":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
          "photos":[{
            "uuid":"12345678-1234-1234-1234-1234567890ab",
            "date":"2026-09-01T12:00:00",
            "existing_keywords":[],
            "proposed_keywords":["canal"],
            "model_proposed_keywords":["canal"],
            "approved_keywords":["canal"],
            "keyword_origins":{"canal":"model"},
            "model_proposed_caption":"Un canal visible.",
            "approved_caption":"Un canal visible.",
            "proposed_caption":"Un canal visible.",
            "caption_origin":"model",
            "caption_state":"proposed",
            "scan_state":"ready",
            "apply_state":"not_run",
            "applied_keywords":[],
            "rollback_state":"not_run",
            "rolled_back_keywords":[],
            "errors":[],
            "model_used":"qwen3-vl:4b",
            "model_reason":"location_context",
            "technical_trace":{
              "prompt_effective":"Analiza evidencia visual y contexto de lugar sanitizado.",
              "prompt_version":"vision-prompt-v1",
              "prompt_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
              "used_gps":true,
              "used_apple_maps":true,
              "used_landmark":false,
              "place_context":["Canal Grande"],
              "place_lookup_state":"results",
              "place_evidence_state":"context_available",
              "durations_ms":{"total":1}
            }
          }]
        }
        """#.utf8)

        let preview = try RunManifestPreview.decodeStrict(data)

        XCTAssertEqual(preview.schemaVersion, 4)
        XCTAssertEqual(preview.photos.first?.technicalTrace?.placeLookupState, "results")
        XCTAssertEqual(preview.photos.first?.technicalTrace?.placeEvidenceState, "context_available")
        XCTAssertFalse(preview.canPrepareReview)
    }

    func testLegacyPreviewWithoutTraceRemainsDecodable() throws {
        let data = Data(#"""
        {
          "uuid":"12345678-1234-1234-1234-1234567890ab",
          "date":"2026-09-01T12:00:00",
          "existing_keywords":[],
          "proposed_keywords":[],
          "contains_people":false,
          "contains_text":false,
          "confidence":0.91,
          "scan_state":"noop",
          "apply_state":"not_run",
          "rollback_state":"not_run",
          "errors":[]
        }
        """#.utf8)

        let photo = try JSONDecoder().decode(PreviewPhoto.self, from: data)

        XCTAssertNil(photo.technicalTrace)
    }
}
