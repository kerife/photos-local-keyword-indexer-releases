import Foundation
import Combine
import AppKit
import Photos

enum PhotosPermissionState: Equatable {
    case notDetermined
    case authorized
    case limited
    case denied
    case restricted
}

enum AutomationPermissionState: Equatable {
    case requiresFirstOperation
    case instructionsAvailable
}

enum PermissionSettingsRoute: Equatable {
    case photos
    case automation

    var url: URL {
        let pane: String
        switch self {
        case .photos: pane = "Privacy_Photos"
        case .automation: pane = "Privacy_Automation"
        }
        return URL(string: "x-apple.systempreferences:com.apple.preference.security?\(pane)")!
    }
}

@MainActor
final class PermissionChecker: ObservableObject {
    @Published private(set) var photos = PhotosPermissionState.notDetermined
    @Published private(set) var automation = AutomationPermissionState.requiresFirstOperation

    private let readPhotosState: () -> PhotosPermissionState
    private let requestPhotosState: () async -> PhotosPermissionState
    private let openSettings: (PermissionSettingsRoute) -> Bool

    init(
        readPhotosState: @escaping () -> PhotosPermissionState = {
            PermissionChecker.map(PHPhotoLibrary.authorizationStatus(for: .readWrite))
        },
        requestPhotosState: @escaping () async -> PhotosPermissionState = {
            PermissionChecker.map(await PHPhotoLibrary.requestAuthorization(for: .readWrite))
        },
        openSettings: @escaping (PermissionSettingsRoute) -> Bool = {
            NSWorkspace.shared.open($0.url)
        }
    ) {
        self.readPhotosState = readPhotosState
        self.requestPhotosState = requestPhotosState
        self.openSettings = openSettings
        refresh()
    }

    func refresh() {
        photos = readPhotosState()
        // macOS does not expose a passive public API for the Apple Events TCC
        // decision. The first PhotoScript operation prompts through the helper.
        automation = .instructionsAvailable
    }

    func requestPhotosAccess() async {
        photos = await requestPhotosState()
    }

    @discardableResult
    func openPhotosSettings() -> Bool {
        openSettings(.photos)
    }

    @discardableResult
    func openAutomationSettings() -> Bool {
        openSettings(.automation)
    }

    nonisolated private static func map(_ status: PHAuthorizationStatus) -> PhotosPermissionState {
        switch status {
        case .authorized:
            return .authorized
        case .limited:
            return .limited
        case .denied:
            return .denied
        case .restricted:
            return .restricted
        case .notDetermined:
            return .notDetermined
        @unknown default:
            return .restricted
        }
    }
}
