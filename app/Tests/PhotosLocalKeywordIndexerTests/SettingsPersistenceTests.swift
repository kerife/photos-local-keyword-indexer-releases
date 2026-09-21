import Foundation
import XCTest
@testable import PhotosLocalKeywordIndexer

final class SettingsPersistenceTests: XCTestCase {
    func testDefaultsAreOperationalAndDoNotContainPhotoData() {
        let settings = AppSettings.defaults

        XCTAssertEqual(settings.limit, 10)
        XCTAssertEqual(settings.modelPolicy, "adaptive")
        XCTAssertEqual(settings.fastModel, "qwen3-vl:4b")
        XCTAssertEqual(settings.detailedModel, "qwen3-vl:4b")
        XCTAssertFalse(settings.appleMaps)
        XCTAssertTrue(settings.includeCaption)
        XCTAssertTrue(settings.randomSelection)
        XCTAssertTrue(settings.autoAnalyze)
        XCTAssertEqual(settings.analysisConcurrency, 2)
    }

    func testStoreRoundTripUsesVersionedCodableSettings() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let file = directory.appendingPathComponent("settings.json")
        let store = AppSettingsStore(fileURL: file)
        let expected = AppSettings(
            limit: 10,
            modelPolicy: "single",
            singleModel: "qwen3-vl:8b",
            fastModel: "qwen3-vl:4b",
            detailedModel: "qwen3-vl:8b",
            appleMaps: true,
            includeCaption: true,
            randomSelection: true
        )

        try store.save(expected)

        XCTAssertEqual(store.load(), expected)
        let encoded = try String(decoding: Data(contentsOf: file), as: UTF8.self)
        XCTAssertTrue(encoded.contains("\"version\":2"))
        XCTAssertEqual(
            try FileManager.default.attributesOfItem(atPath: file.path)[.posixPermissions] as? NSNumber,
            NSNumber(value: 0o600)
        )
        XCTAssertEqual(
            try FileManager.default.attributesOfItem(atPath: directory.path)[.posixPermissions] as? NSNumber,
            NSNumber(value: 0o700)
        )
        try? FileManager.default.removeItem(at: directory)
    }

    func testVersionOneSettingsMigrateToContinuousReviewDefaults() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let file = directory.appendingPathComponent("settings.json")
        let store = AppSettingsStore(fileURL: file)
        try FileManager.default.createDirectory(
            at: directory,
            withIntermediateDirectories: true
        )
        try Data(#"{"version":1,"limit":7,"modelPolicy":"single","singleModel":"qwen3-vl:4b","fastModel":"qwen3-vl:4b","detailedModel":"qwen3-vl:4b","appleMaps":true,"includeCaption":false,"randomSelection":false}"#.utf8)
            .write(to: file)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: file.path)
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o700],
            ofItemAtPath: directory.path
        )

        let migrated = store.load()
        XCTAssertEqual(migrated.version, 2)
        XCTAssertEqual(migrated.limit, 10)
        XCTAssertEqual(migrated.modelPolicy, "single")
        XCTAssertTrue(migrated.appleMaps)
        XCTAssertTrue(migrated.includeCaption)
        XCTAssertTrue(migrated.randomSelection)
        XCTAssertTrue(migrated.autoAnalyze)
        XCTAssertEqual(migrated.analysisConcurrency, 2)
        try? FileManager.default.removeItem(at: directory)
    }

    func testUnsupportedSettingsVersionFallsBackToDefaults() throws {
        let file = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
        let store = AppSettingsStore(fileURL: file)
        try FileManager.default.createDirectory(
            at: file.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try Data(#"{"version":99,"limit":10,"modelPolicy":"single","singleModel":"qwen3-vl:4b","fastModel":"qwen3-vl:4b","detailedModel":"qwen3-vl:4b","appleMaps":false,"includeCaption":true,"randomSelection":true,"autoAnalyze":true,"analysisConcurrency":2}"#.utf8)
            .write(to: file)

        XCTAssertEqual(store.load(), .defaults)
        try? FileManager.default.removeItem(at: file.deletingLastPathComponent())
    }

    func testInvalidOrUnexpectedSettingsFallBackToDefaults() throws {
        let file = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString)
        let store = AppSettingsStore(fileURL: file)
        try FileManager.default.createDirectory(
            at: file.deletingLastPathComponent(),
            withIntermediateDirectories: true
        )
        try Data(#"{"version":1,"limit":999,"modelPolicy":"single","singleModel":"qwen3-vl:4b","fastModel":"qwen3-vl:4b","detailedModel":"qwen3-vl:4b","appleMaps":false,"includeCaption":false,"randomSelection":false}"#.utf8)
            .write(to: file)

        XCTAssertEqual(store.load(), .defaults)
        try? FileManager.default.removeItem(at: file.deletingLastPathComponent())
    }

    func testSymlinkedSettingsFileFallsBackWithoutFollowingIt() throws {
        let directory = FileManager.default.temporaryDirectory
            .appendingPathComponent(UUID().uuidString, isDirectory: true)
        let outside = FileManager.default.temporaryDirectory
            .appendingPathComponent("outside-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try Data(#"{"version":1,"limit":10,"modelPolicy":"single","singleModel":"qwen3-vl:4b","fastModel":"qwen3-vl:4b","detailedModel":"qwen3-vl:4b","appleMaps":false,"includeCaption":false,"randomSelection":false}"#.utf8)
            .write(to: outside)
        let file = directory.appendingPathComponent("settings.json")
        try FileManager.default.createSymbolicLink(at: file, withDestinationURL: outside)

        XCTAssertEqual(AppSettingsStore(fileURL: file).load(), .defaults)
        try? FileManager.default.removeItem(at: directory)
        try? FileManager.default.removeItem(at: outside)
    }

}
