import SwiftUI

/// Measures the space available to a route after its native sidebar and margins.
/// Layout decisions stay in the content closure so resizing preserves view state.
struct ResponsivePage<Content: View>: View {
    private let content: (CGFloat) -> Content

    init(@ViewBuilder content: @escaping (CGFloat) -> Content) {
        self.content = content
    }

    var body: some View {
        GeometryReader { geometry in
            let margin: CGFloat = geometry.size.width < 640 ? 16 : 24
            let width = min(1180, max(0, geometry.size.width - margin * 2))
            content(width)
                .frame(width: width, height: max(0, geometry.size.height - margin * 2), alignment: .topLeading)
                .padding(margin)
                .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        }
    }
}
