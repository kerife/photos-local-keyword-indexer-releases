import AppKit
import Foundation
import Photos
import SwiftUI

@MainActor
final class PhotoThumbnailLoader: ObservableObject {
    @Published private(set) var image: NSImage?
    @Published private(set) var isLoading = false

    private let photosLocalIdentifier: String?
    private let imageManager: PHImageManager
    private var requestID: PHImageRequestID = PHInvalidImageRequestID

    init(
        photosLocalIdentifier: String?,
        imageManager: PHImageManager = .default()
    ) {
        self.photosLocalIdentifier = photosLocalIdentifier
        self.imageManager = imageManager
    }

    func load() {
        guard image == nil, !isLoading,
              let photosLocalIdentifier = boundedIdentifier else { return }
        let status = PHPhotoLibrary.authorizationStatus(for: .readWrite)
        if status == .notDetermined {
            isLoading = true
            PHPhotoLibrary.requestAuthorization(for: .readWrite) { [weak self] updatedStatus in
                Task { @MainActor [weak self] in
                    guard let self else { return }
                    self.isLoading = false
                    if updatedStatus == .authorized || updatedStatus == .limited {
                        self.load()
                    }
                }
            }
            return
        }
        guard status == .authorized || status == .limited else { return }

        let assets = PHAsset.fetchAssets(
            withLocalIdentifiers: [photosLocalIdentifier],
            options: nil
        )
        guard let asset = assets.firstObject else { return }

        let options = PHImageRequestOptions()
        options.deliveryMode = .opportunistic
        options.resizeMode = .fast
        options.isSynchronous = false
        options.isNetworkAccessAllowed = false
        isLoading = true
        requestID = imageManager.requestImage(
            for: asset,
            targetSize: CGSize(width: 240, height: 180),
            contentMode: .aspectFill,
            options: options
        ) { [weak self] image, info in
            let cancelled = info?[PHImageCancelledKey] as? Bool ?? false
            guard !cancelled else { return }
            Task { @MainActor [weak self] in
                guard let self else { return }
                if let image {
                    self.image = image
                }
                let degraded = info?[PHImageResultIsDegradedKey] as? Bool ?? false
                if !degraded {
                    self.isLoading = false
                    self.requestID = PHInvalidImageRequestID
                }
            }
        }
    }

    func cancel() {
        guard requestID != PHInvalidImageRequestID else { return }
        imageManager.cancelImageRequest(requestID)
        requestID = PHInvalidImageRequestID
        isLoading = false
    }

    private var boundedIdentifier: String? {
        guard let photosLocalIdentifier else { return nil }
        let trimmed = photosLocalIdentifier.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty,
              trimmed != "unknown",
              trimmed.count <= 1024,
              trimmed.unicodeScalars.allSatisfy({
                  !CharacterSet.controlCharacters.contains($0)
              }) else {
            return nil
        }
        return trimmed
    }
}

struct PhotoThumbnailView: View {
    @StateObject private var loader: PhotoThumbnailLoader
    private let displayTitle: String
    private let presentationSize: CGSize

    init(
        photosLocalIdentifier: String?,
        displayTitle: String,
        presentationSize: CGSize = CGSize(width: 96, height: 72)
    ) {
        _loader = StateObject(
            wrappedValue: PhotoThumbnailLoader(
                photosLocalIdentifier: photosLocalIdentifier
            )
        )
        self.displayTitle = displayTitle
        self.presentationSize = presentationSize
    }

    var body: some View {
        ZStack {
            RoundedRectangle(cornerRadius: 8)
                .fill(Color.secondary.opacity(0.12))
            if let image = loader.image {
                Image(nsImage: image)
                    .resizable()
                    .scaledToFill()
            } else if loader.isLoading {
                ProgressView()
                    .controlSize(.small)
            } else {
                Image(systemName: "photo")
                    .font(.title2)
                    .foregroundStyle(.secondary)
            }
        }
        .frame(width: presentationSize.width, height: presentationSize.height)
        .clipShape(RoundedRectangle(cornerRadius: 8))
        .overlay {
            RoundedRectangle(cornerRadius: 8)
                .stroke(Color.secondary.opacity(0.22), lineWidth: 1)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(
            loader.image == nil
                ? "Miniatura no disponible para \(displayTitle)"
                : "Miniatura de \(displayTitle)"
        )
        .onAppear { loader.load() }
        .onDisappear { loader.cancel() }
    }
}
