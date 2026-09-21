import SwiftUI

struct AutonomyActivityCard: View {
    let activity: AutonomyActivitySnapshot
    let contentWidth: CGFloat

    private var thumbnailSize: CGSize {
        CGSize(width: contentWidth < 640 ? 64 : 96, height: contentWidth < 640 ? 48 : 72)
    }

    var body: some View {
        HStack(alignment: .top, spacing: 14) {
            PhotoThumbnailView(
                photosLocalIdentifier: activity.photosLocalIdentifier,
                displayTitle: "Foto en recorrido",
                presentationSize: thumbnailSize
            )
            .id("autonomy-\(activity.campaignID)-\(activity.position)")
            VStack(alignment: .leading, spacing: 5) {
                HStack {
                    Text("Foto en recorrido")
                        .font(.headline)
                    Spacer(minLength: 0)
                    Label(activity.state.statusText, systemImage: statusSymbol)
                        .font(.caption2.weight(.medium))
                        .foregroundStyle(Color.accentColor)
                }
                HStack(spacing: 8) {
                    ProgressView().controlSize(.mini)
                    Text(activity.state.statusText)
                        .font(.caption)
                        .foregroundStyle(.secondary)
                    Label("Solo lectura", systemImage: "lock.fill")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(nsColor: .controlBackgroundColor), in: RoundedRectangle(cornerRadius: 12))
        .overlay {
            RoundedRectangle(cornerRadius: 12)
                .strokeBorder(Color.accentColor.opacity(0.25), lineWidth: 1)
        }
        .accessibilityElement(children: .ignore)
        .accessibilityLabel("Foto en recorrido. \(activity.state.statusText). Solo lectura.")
    }

    private var statusSymbol: String {
        switch activity.state {
        case .preparing: return "photo.badge.arrow.down"
        case .analyzing: return "sparkles"
        case .validating: return "checkmark.shield"
        case .saveQueued: return "tray"
        case .saving: return "square.and.arrow.down"
        case .settled: return "checkmark.circle"
        }
    }
}
