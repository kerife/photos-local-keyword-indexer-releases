import SwiftUI

@main
struct PhotosLocalKeywordIndexerApp: App {
    @StateObject private var model = AppModel(
        persistedSettings: AppSettingsStore.loadDefault() ?? .defaults,
        persistSettings: true
    )
    @StateObject private var updates = UpdateService()

    var body: some Scene {
        WindowGroup("Photos Local Keyword Indexer") {
            ContentView()
                .environmentObject(model)
                .environmentObject(updates)
                .frame(minWidth: 760, minHeight: 560)
        }
        .defaultSize(width: 1100, height: 760)
        .windowResizability(.contentSize)
        .commands {
            CommandGroup(after: .appInfo) {
                Button("Buscar actualizaciones…") { updates.checkForUpdates() }
                    .disabled(!updates.isConfigured)
            }
        }
    }
}

struct ContentView: View {
    @EnvironmentObject private var model: AppModel
    @Environment(\.scenePhase) private var scenePhase
    @State private var navigation = AppNavigationState()
    @State private var isShowingHistoricalReview = false
    @State private var didResolveInitialRoute = false
    @State private var columnVisibility: NavigationSplitViewVisibility = .automatic

    var body: some View {
        GeometryReader { geometry in
        NavigationSplitView(columnVisibility: $columnVisibility) {
            List(selection: routeBinding) {
                Label(AppRoute.setup.accessibilityLabel, systemImage: "checkmark.shield")
                    .tag(AppRoute.setup)
                Label(AppRoute.preview.accessibilityLabel, systemImage: "checklist")
                    .accessibilityValue(
                        navigation.accessibilityValue(
                            for: .preview,
                            preparation: model.preparation,
                            hasPreview: model.preview != nil,
                            preflightIsStale: model.preflightIsStale
                        )
                    )
                    .tag(AppRoute.preview)
                Label(AppRoute.history.accessibilityLabel, systemImage: "clock.arrow.circlepath")
                    .tag(AppRoute.history)
                Label(AppRoute.settings.accessibilityLabel, systemImage: "gearshape")
                    .tag(AppRoute.settings)
            }
            .navigationTitle("Fotos Keywords")
            .navigationSplitViewColumnWidth(min: 180, ideal: 200, max: 240)
        } detail: {
            Group {
                switch navigation.route {
                case .scan where isShowingHistoricalReview,
                     .preview where isShowingHistoricalReview:
                    PreviewView(
                        onOpenHistory: {
                            isShowingHistoricalReview = false
                            _ = navigation.navigate(
                                to: .history,
                                preparation: model.preparation
                            )
                        },
                        onStartScan: {
                            isShowingHistoricalReview = false
                            model.startContinuousReviewIfNeeded()
                        }
                    )
                case .scan, .preview:
                    ContinuousReviewView(
                        controls: Binding(
                            get: { model.continuousControls },
                            set: model.updateContinuousControls
                        ),
                        session: Binding(
                            get: { model.reviewSession },
                            set: model.replaceContinuousReviewSession
                        ),
                        availableModels: Array(Set([
                            model.singleModel,
                            model.fastModel,
                            model.detailedModel,
                        ])).sorted(),
                        preparation: model.continuousReviewPreparation,
                        manualQueueMutationAllowed: model.manualQueueMutationAllowed,
                        onPreparationAction: model.resolveContinuousReviewPreparation,
                        onDraftChange: { item, keywords, caption in
                            model.editContinuousItem(
                                id: item.id,
                                keywords: keywords,
                                caption: caption
                            )
                        },
                        onSave: { model.persistContinuousItem(id: $0.id) },
                        onDiscard: { model.discardContinuousItem(id: $0.id) },
                        canUndoDiscard: model.canUndoContinuousDiscard,
                        onUndoDiscard: model.undoContinuousDiscard,
                        onStop: model.stopContinuousReview,
                        accessibilityAnnouncement: model.continuousReviewAnnouncement,
                        onRescan: { item, options in
                            model.rescanContinuousItem(id: item.id, options: options)
                        },
                        autonomy: model.autonomyReviewPresentation,
                        onAutonomyStart: model.startAutonomy,
                        onAutonomyPause: model.pauseAutonomy,
                        onAutonomyResume: model.resumeAutonomy,
                        onAutonomyRefresh: model.bootstrapAutonomyStatusIfNeeded
                    )
                    .task { model.startContinuousReviewIfNeeded() }
                case .history:
                    RunDetailView(
                        onReview: { manifest in
                            let loaded = model.loadPreview(from: manifest)
                            isShowingHistoricalReview = loaded
                            _ = navigation.openHistoryReview(loadSucceeded: loaded)
                        },
                        onStartScan: {
                            _ = navigation.navigate(
                                to: .preview,
                                preparation: model.preparation,
                                hasPreview: true,
                                preflightIsStale: model.preflightIsStale
                            )
                        }
                    )
                case .setup:
                    OnboardingView {
                        _ = navigation.navigate(
                            to: .preview,
                            preparation: model.preparation,
                            preflightIsStale: model.preflightIsStale
                        )
                    }
                case .settings:
                    SettingsView()
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        }
        .onChange(of: geometry.size.width < 1000, initial: true) { _, isCompact in
            columnVisibility = isCompact ? .detailOnly : .all
        }
        }
        .task {
            model.bootstrapAutonomyStatusIfNeeded()
            model.bootstrapPreparationIfNeeded()
        }
        .onChange(of: model.preparation.isReady, initial: true) { _, isReady in
            guard !didResolveInitialRoute, isReady, navigation.route == .setup else { return }
            didResolveInitialRoute = true
            _ = navigation.navigate(
                to: .preview,
                preparation: model.preparation,
                hasPreview: true,
                preflightIsStale: model.preflightIsStale
            )
        }
        .onChange(of: scenePhase, initial: true) { _, phase in
            guard phase == .active else { return }
            model.refreshPreparation()
        }
    }

    private var routeBinding: Binding<AppRoute> {
        Binding(
            get: { navigation.route },
            set: { destination in
                didResolveInitialRoute = true
                if destination == .preview {
                    isShowingHistoricalReview = false
                }
                _ = navigation.navigate(
                    to: destination,
                    preparation: model.preparation,
                    hasPreview: destination == .preview || model.preview != nil,
                    preflightIsStale: model.preflightIsStale
                )
            }
        )
    }
}
