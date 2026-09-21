import Foundation
import XCTest
@testable import PhotosLocalKeywordIndexer

final class AppSettingsPersistenceTests: XCTestCase {
    func testDefaultsEncodeAndDecodeWithoutPhotoMetadata() throws {
        let data = try JSONEncoder().encode(AppSettings.defaults)
        let decoded = try JSONDecoder().decode(AppSettings.self, from: data)

        XCTAssertEqual(decoded, AppSettings.defaults)
        let json = String(decoding: data, as: UTF8.self)
        XCTAssertTrue(json.contains("modelPolicy"))
        XCTAssertFalse(json.contains("runsRoot"))
        XCTAssertFalse(json.contains("caption"))
        XCTAssertFalse(json.contains("coordinate"))
    }

    func testInvalidStoredSettingsFailClosedToDefaults() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("settings-tests-\(UUID().uuidString)", isDirectory: true)
        let fileURL = root.appendingPathComponent("settings.json")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try Data(#"{"limit":0,"modelPolicy":"adaptive","singleModel":"qwen3-vl:4b","fastModel":"qwen3-vl:4b","detailedModel":"qwen3-vl:4b","appleMaps":false,"includeCaption":false,"randomSelection":false}"#.utf8)
            .write(to: fileURL)

        XCTAssertEqual(AppSettingsStore(fileURL: fileURL).load(), .defaults)
        try? FileManager.default.removeItem(at: root)
    }

    func testModelValidationMatchesTheActiveOllamaPolicy() {
        var invalid = AppSettings.defaults
        invalid.singleModel = "vision:cloud"
        XCTAssertFalse(invalid.isValid)

        invalid.singleModel = "../../local-model"
        XCTAssertFalse(invalid.isValid)
    }

    func testUnversionedStoredSettingsFailClosedToDefaults() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("settings-tests-\(UUID().uuidString)", isDirectory: true)
        let fileURL = root.appendingPathComponent("settings.json")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try Data(#"{"limit":20,"modelPolicy":"adaptive","singleModel":"qwen3-vl:4b","fastModel":"qwen3-vl:4b","detailedModel":"qwen3-vl:4b","appleMaps":false,"includeCaption":false,"randomSelection":false}"#.utf8)
            .write(to: fileURL)

        XCTAssertEqual(AppSettingsStore(fileURL: fileURL).load(), .defaults)
        try? FileManager.default.removeItem(at: root)
    }

    func testSaveRoundTripsWithPrivatePermissionsAndReplacement() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("settings-tests-\(UUID().uuidString)", isDirectory: true)
        let fileURL = root.appendingPathComponent("Photos Local Keyword Indexer", isDirectory: true)
            .appendingPathComponent("settings.json")
        let store = AppSettingsStore(fileURL: fileURL)
        let settings = AppSettings(
            limit: 37,
            modelPolicy: "single",
            singleModel: "qwen3-vl:8b",
            fastModel: AppSettings.defaults.fastModel,
            detailedModel: AppSettings.defaults.detailedModel,
            appleMaps: AppSettings.defaults.appleMaps,
            includeCaption: true,
            randomSelection: true
        )

        try store.save(settings)
        XCTAssertEqual(store.load(), settings)
        var fileMode = try FileManager.default.attributesOfItem(atPath: fileURL.path)[.posixPermissions] as? NSNumber
        XCTAssertEqual(fileMode?.intValue ?? 0 & 0o777, 0o600)
        let directory = fileURL.deletingLastPathComponent()
        let directoryMode = try FileManager.default.attributesOfItem(atPath: directory.path)[.posixPermissions] as? NSNumber
        XCTAssertEqual(directoryMode?.intValue ?? 0 & 0o777, 0o700)

        let replacement = AppSettings(
            limit: 42,
            modelPolicy: settings.modelPolicy,
            singleModel: settings.singleModel,
            fastModel: settings.fastModel,
            detailedModel: settings.detailedModel,
            appleMaps: settings.appleMaps,
            includeCaption: settings.includeCaption,
            randomSelection: settings.randomSelection
        )
        try store.save(replacement)
        XCTAssertEqual(store.load(), replacement)
        fileMode = try FileManager.default.attributesOfItem(atPath: fileURL.path)[.posixPermissions] as? NSNumber
        XCTAssertEqual(fileMode?.intValue ?? 0 & 0o777, 0o600)
        try? FileManager.default.removeItem(at: root)
    }

    func testSaveRejectsInvalidSettingsBeforeCreatingAFile() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("settings-tests-\(UUID().uuidString)", isDirectory: true)
        let fileURL = root.appendingPathComponent("settings.json")
        var invalid = AppSettings.defaults
        invalid.limit = 0

        XCTAssertThrowsError(try AppSettingsStore(fileURL: fileURL).save(invalid))
        XCTAssertFalse(FileManager.default.fileExists(atPath: fileURL.path))
        try? FileManager.default.removeItem(at: root)
    }

    func testSaveRejectsASymlinkedDirectoryWithoutWritingOutsideIt() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("settings-tests-\(UUID().uuidString)", isDirectory: true)
        let outside = root.appendingPathComponent("outside", isDirectory: true)
        let link = root.appendingPathComponent("linked", isDirectory: true)
        try FileManager.default.createDirectory(at: outside, withIntermediateDirectories: true)
        try FileManager.default.createSymbolicLink(at: link, withDestinationURL: outside)
        let fileURL = link.appendingPathComponent("settings.json")

        XCTAssertThrowsError(try AppSettingsStore(fileURL: fileURL).save(.defaults))
        XCTAssertFalse(FileManager.default.fileExists(atPath: outside.appendingPathComponent("settings.json").path))
        try? FileManager.default.removeItem(at: root)
    }

    func testLoadRejectsASettingsFileThatIsNotPrivate() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("settings-tests-\(UUID().uuidString)", isDirectory: true)
        let fileURL = root.appendingPathComponent("settings.json")
        let store = AppSettingsStore(fileURL: fileURL)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        try JSONEncoder().encode(AppSettings.defaults).write(to: fileURL)
        try FileManager.default.setAttributes([.posixPermissions: 0o644], ofItemAtPath: fileURL.path)

        XCTAssertEqual(store.load(), .defaults)
        try? FileManager.default.removeItem(at: root)
    }

    func testLoadRejectsASettingsDirectoryThatIsNotPrivate() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("settings-tests-\(UUID().uuidString)", isDirectory: true)
        let directory = root.appendingPathComponent("Photos Local Keyword Indexer", isDirectory: true)
        let fileURL = directory.appendingPathComponent("settings.json")
        let store = AppSettingsStore(fileURL: fileURL)
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try JSONEncoder().encode(AppSettings.defaults).write(to: fileURL)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: fileURL.path)
        try FileManager.default.setAttributes([.posixPermissions: 0o755], ofItemAtPath: directory.path)

        XCTAssertEqual(store.load(), .defaults)
        try? FileManager.default.removeItem(at: root)
    }
}
