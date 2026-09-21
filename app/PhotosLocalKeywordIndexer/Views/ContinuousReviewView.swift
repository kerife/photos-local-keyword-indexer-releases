import SwiftUI
import AppKit

struct ContinuousReviewView: View {
    @Binding private var controls: ContinuousReviewControls
    @Binding private var session: ReviewSessionStore

    private let availableModels: [String]
    private let preparation: ContinuousReviewPreparation
    private let manualQueueMutationAllowed: Bool
    private let onControlsChange: (ContinuousReviewControls) -> Void
    private let onPreparationAction: () -> Void
    private let onDraftChange: (QueueReviewItem, [String], String?) -> Bool
    private let onSave: (QueueReviewItem) -> Void
    private let onDiscard: (QueueReviewItem) -> Void
    private let canUndoDiscard: Bool
    private let onUndoDiscard: () -> Void
    private let onStop: () -> Void
    private let accessibilityAnnouncement: ContinuousReviewAnnouncement?
    private let onRescan: (QueueReviewItem, QueueRescanOptions) -> Void
    private let autonomy: AutonomyReviewPresentation?
    private let onAutonomyStart: () -> Void
    private let onAutonomyPause: () -> Void
    private let onAutonomyResume: () -> Void
    private let onAutonomyRefresh: () -> Void

    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var pendingRescanItem: QueueReviewItem?
    @State private var expandedItemID: String?
    @State private var inspectedItemID: String?
    @State private var showsOptions = false
    @State private var showsActivity = false

    init(
        controls: Binding<ContinuousReviewControls>,
        session: Binding<ReviewSessionStore>,
        availableModels: [String],
        preparation: ContinuousReviewPreparation = .ready,
        manualQueueMutationAllowed: Bool = true,
        onControlsChange: @escaping (ContinuousReviewControls) -> Void = { _ in },
        onPreparationAction: @escaping () -> Void = {},
        onDraftChange: @escaping (QueueReviewItem, [String], String?) -> Bool = { _, _, _ in false },
        onSave: @escaping (QueueReviewItem) -> Void = { _ in },
        onDiscard: @escaping (QueueReviewItem) -> Void = { _ in },
        canUndoDiscard: Bool = false,
        onUndoDiscard: @escaping () -> Void = {},
        onStop: @escaping () -> Void = {},
        accessibilityAnnouncement: ContinuousReviewAnnouncement? = nil,
        onRescan: @escaping (QueueReviewItem, QueueRescanOptions) -> Void = { _, _ in },
        autonomy: AutonomyReviewPresentation? = nil,
        onAutonomyStart: @escaping () -> Void = {},
        onAutonomyPause: @escaping () -> Void = {},
        onAutonomyResume: @escaping () -> Void = {},
        onAutonomyRefresh: @escaping () -> Void = {}
    ) {
        _controls = controls
        _session = session
        self.availableModels = availableModels
        self.preparation = preparation
        self.manualQueueMutationAllowed = manualQueueMutationAllowed
        self.onControlsChange = onControlsChange
        self.onPreparationAction = onPreparationAction
        self.onDraftChange = onDraftChange
        self.onSave = onSave
        self.onDiscard = onDiscard
        self.canUndoDiscard = canUndoDiscard
        self.onUndoDiscard = onUndoDiscard
        self.onStop = onStop
        self.accessibilityAnnouncement = accessibilityAnnouncement
        self.onRescan = onRescan
        self.autonomy = autonomy
        self.onAutonomyStart = onAutonomyStart
        self.onAutonomyPause = onAutonomyPause
        self.onAutonomyResume = onAutonomyResume
        self.onAutonomyRefresh = onAutonomyRefresh
    }

    private var modelOptions: [String] {
        var seen = Set<String>()
        return (["adaptive", controls.modelSelection] + availableModels)
            .map { $0.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty && seen.insert($0).inserted }
    }

    private var summary: ContinuousReviewSummary {
        ContinuousReviewSummary(items: session.items)
    }

    private var table: ContinuousReviewTable {
        ContinuousReviewTable(session: session, autonomyActivities: autonomy?.activities ?? [])
    }

    var body: some View {
        GeometryReader { geometry in
            let margin: CGFloat = geometry.size.width < 640 ? 16 : 24
            let contentWidth = min(1180, max(0, geometry.size.width - margin * 2))
            let includesAutonomousCaptions = controls.includeCaptions
            VStack(alignment: .leading, spacing: 14) {
                controlBar
                if let autonomy {
                    AutonomyReviewBanner(
                        presentation: autonomy,
                        includeCaptions: includesAutonomousCaptions,
                        onStart: onAutonomyStart,
                        onPause: onAutonomyPause,
                        onResume: onAutonomyResume,
                        onRefresh: onAutonomyRefresh
                    )
                }
                summaryBar
                if canUndoDiscard {
                    undoDiscardBanner
                }
                if preparation.phase != .ready {
                    preparationBanner
                }
                if !manualQueueMutationAllowed {
                    Label("La revisión manual está suspendida mientras el modo autónomo controla la cola.", systemImage: "lock.circle")
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
                reviewTable(contentWidth: contentWidth)
            }
            .frame(maxWidth: 1180, maxHeight: .infinity, alignment: .topLeading)
            .padding(margin)
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            .sheet(item: $pendingRescanItem) { item in
                if let current = currentItem(id: item.id) {
                    ContinuousReviewRescanSheet(
                        item: current,
                        modelOptions: rescanModelOptions(for: current),
                        initialModel: rescanInitialModel(for: current),
                        onCancel: { pendingRescanItem = nil },
                        onSubmit: { options in
                            guard let current = currentItem(id: item.id),
                                  preparation.allowsQueueWork, manualQueueMutationAllowed,
                                  ContinuousReviewActions(item: current).canRescan else { return }
                            onRescan(current, options)
                            pendingRescanItem = nil
                        }
                    )
                    .frame(
                        width: min(700, max(560, geometry.size.width)),
                        height: min(640, max(460, geometry.size.height))
                    )
                }
            }
            .sheet(isPresented: inspectorPresented) {
                inspectorSheet(availableSize: geometry.size)
            }
        }
        .background(Color(nsColor: .windowBackgroundColor))
        .navigationTitle("Revisión")
        .onChange(of: table.visibleItems.map(\.id)) { _, visibleIDs in
            if let expandedItemID, !visibleIDs.contains(expandedItemID) {
                self.expandedItemID = nil
            }
            if let inspectedItemID, !visibleIDs.contains(inspectedItemID) {
                self.inspectedItemID = nil
            }
            if let pendingRescanItem, !visibleIDs.contains(pendingRescanItem.id) {
                self.pendingRescanItem = nil
            }
        }
        .onChange(of: accessibilityAnnouncement) { _, announcement in
            guard let announcement else { return }
            NSAccessibility.post(
                element: NSApplication.shared,
                notification: .announcementRequested,
                userInfo: [
                    .announcement: announcement.text,
                    .priority: NSAccessibilityPriorityLevel.medium.rawValue,
                ]
            )
        }
    }

    @ViewBuilder
    private func inspectorSheet(availableSize: CGSize) -> some View {
        if let inspectedItemID, let item = currentItem(id: inspectedItemID) {
            let width = min(CGFloat(480), max(CGFloat(440), availableSize.width))
            let height = min(CGFloat(600), max(CGFloat(440), availableSize.height))
            ContinuousReviewInspector(item: item, onClose: { self.inspectedItemID = nil })
                .frame(width: width, height: height)
        }
    }

    @ViewBuilder
    private func reviewTable(contentWidth: CGFloat) -> some View {
        if table.visibleItems.isEmpty && table.autonomyActivities.isEmpty {
            ContentUnavailableView(
                session.items.isEmpty ? "Preparando la mesa de revisión" : "Mesa al día",
                systemImage: session.items.isEmpty ? "photo.stack" : "checkmark.circle",
                description: Text(
                    !session.items.isEmpty
                        ? "Las fotos guardadas o descartadas permanecen registradas en los contadores y el historial."
                        : controls.autoAnalyze
                        ? "Las fotos disponibles aparecerán aquí sin bloquear el resto del análisis."
                        : "Activa el análisis automático o reanuda la sesión para cargar fotos."
                )
            )
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 10) {
                        if !table.autonomyActivities.isEmpty || table.sections.contains(where: { $0.kind == .active }) {
                            sectionHeading(.active)
                            ForEach(table.autonomyActivities, id: \.position) { activity in
                                AutonomyActivityCard(activity: activity, contentWidth: contentWidth)
                                    .transition(reduceMotion ? .identity : .opacity)
                            }
                            if let active = table.sections.first(where: { $0.kind == .active }) {
                                ForEach(active.items) { item in manualCard(item, contentWidth: contentWidth) }
                            }
                        }
                        ForEach(table.sections.filter { $0.kind != .active }) { section in
                            sectionHeading(section.kind)
                            ForEach(section.items) { item in manualCard(item, contentWidth: contentWidth) }
                        }
                    }
                    .padding(2)
                    .padding(.bottom, 12)
                }
                .onChange(of: table.focusedItem?.id) { _, focusedID in
                    guard let focusedID else { return }
                    withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.2)) {
                        proxy.scrollTo(focusedID, anchor: .top)
                    }
                }
            }
        }
    }

    private func sectionHeading(_ kind: ContinuousReviewSectionKind) -> some View {
        Text(kind.title)
            .font(.headline)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.top, 4)
            .accessibilityAddTraits(.isHeader)
    }

    private func manualCard(_ item: QueueReviewItem, contentWidth: CGFloat) -> some View {
        ContinuousReviewCard(
            item: item,
            isExpanded: expandedItemID == item.id,
            contentWidth: contentWidth,
            queueWorkAllowed: preparation.allowsQueueWork && manualQueueMutationAllowed,
            manualQueueMutationAllowed: manualQueueMutationAllowed,
            onToggleExpanded: {
                withAnimation(reduceMotion ? nil : .easeInOut(duration: 0.2)) {
                    expandedItemID = expandedItemID == item.id ? nil : item.id
                }
            },
            onInspect: { inspectedItemID = item.id },
            onDraftChange: { keywords, caption in
                guard let current = currentItem(id: item.id),
                      manualQueueMutationAllowed,
                      ContinuousReviewActions(item: current).canEdit else { return false }
                return onDraftChange(current, keywords, caption)
            },
            onSave: {
                guard let current = currentItem(id: item.id),
                      preparation.allowsQueueWork, manualQueueMutationAllowed,
                      ContinuousReviewActions(item: current).canSave else { return }
                onSave(current)
            },
            onDiscard: {
                guard let current = currentItem(id: item.id),
                      manualQueueMutationAllowed,
                      ContinuousReviewActions(item: current).canDiscard else { return }
                onDiscard(current)
            },
            onRescan: {
                guard let current = currentItem(id: item.id),
                      preparation.allowsQueueWork, manualQueueMutationAllowed,
                      ContinuousReviewActions(item: current).canRescan else { return }
                pendingRescanItem = current
            }
        )
        .id(item.id)
    }

    private var controlBar: some View {
        ViewThatFits(in: .horizontal) {
            HStack(alignment: .center, spacing: 16) {
                heading
                Spacer(minLength: 12)
                sessionControls
            }
            VStack(alignment: .leading, spacing: 12) {
                heading
                sessionControls
            }
        }
    }

    private var heading: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text("Mesa de revisión")
                .font(.title2.weight(.semibold))
            Label(controls.isPaused ? "Análisis local en pausa" : "Análisis en este Mac", systemImage: "desktopcomputer")
                .font(.callout)
                .foregroundStyle(.secondary)
        }
    }

    private var sessionControls: some View {
        HStack(spacing: 8) {
            Button { showsOptions.toggle() } label: {
                Label("Opciones", systemImage: "slider.horizontal.3")
            }
            .popover(isPresented: $showsOptions, arrowEdge: .bottom) { optionsPopover }
            Button {
                mutateControls { $0.isPaused.toggle() }
            } label: {
                Label(controls.isPaused ? "Reanudar" : "Pausar", systemImage: controls.isPaused ? "play.fill" : "pause.fill")
            }
            .disabled(!preparation.allowsQueueWork || !manualQueueMutationAllowed)
            .accessibilityHint(
                controls.isPaused
                    ? "Permite iniciar nuevos análisis de la cola."
                    : "Detiene trabajo nuevo; las operaciones iniciadas terminan de forma segura."
            )
            Menu {
                Button("Finalizar sesión", action: onStop)
                    .disabled(!preparation.allowsQueueWork || !manualQueueMutationAllowed)
            } label: {
                Image(systemName: "ellipsis")
            }
            .menuStyle(.borderlessButton)
            .frame(width: 28)
            .accessibilityLabel("Menú de sesión")
            .accessibilityHint("Incluye Finalizar sesión; termina el trabajo iniciado sin modificar ni eliminar datos de Fotos.")
        }
        .buttonStyle(.bordered)
        .fixedSize(horizontal: true, vertical: false)
    }

    private var optionsPopover: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                Text("Opciones de la mesa")
                    .font(.headline)
                Stepper(value: photoCountBinding, in: 1 ... 50) {
                    LabeledContent("Fotos", value: "\(controls.photoCount)")
                }
                .accessibilityLabel("Fotos en la mesa: \(controls.photoCount)")
                VStack(alignment: .leading, spacing: 6) {
                    Text("Modelo").font(.callout.weight(.medium))
                    Picker("Modelo", selection: modelBinding) {
                        ForEach(modelOptions, id: \.self) { model in
                            Text(model == "adaptive" ? "Política adaptativa" : model).tag(model)
                        }
                    }
                    .labelsHidden()
                }
                Toggle("Análisis automático", isOn: booleanControlBinding(\.autoAnalyze))
                    .accessibilityHint("Inicia el análisis local de nuevos elementos sin otro clic.")
                Picker("Análisis paralelos", selection: concurrencyBinding) {
                    ForEach(1 ... 4, id: \.self) { value in Text("\(value)").tag(value) }
                }
                .accessibilityHint("Limita únicamente las inferencias simultáneas de Ollama.")
                Divider()
                Toggle("Proponer descripciones", isOn: booleanControlBinding(\.includeCaptions))
                Toggle("Apple Maps", isOn: booleanControlBinding(\.appleMaps))
                    .accessibilityHint("Usa contexto de lugar solo cuando esta opción está activada.")
                Text("Los cambios se aplican al nuevo trabajo de la cola.")
                    .font(.caption)
                    .foregroundStyle(.secondary)
            }
            .disabled(!preparation.allowsQueueWork || !manualQueueMutationAllowed)
            .padding(20)
        }
        .frame(width: 340, height: 380)
    }

    private var summaryBar: some View {
        HStack(spacing: 14) {
            summaryMetric("Listas", value: summary.ready, symbol: "checklist", color: .green)
            Button { showsActivity.toggle() } label: {
                Label("Actividad: \(summary.queued + summary.analyzing + summary.saveQueued + summary.saving)", systemImage: "waveform.path")
                    .foregroundStyle(.secondary)
            }
            .buttonStyle(.plain)
            .popover(isPresented: $showsActivity, arrowEdge: .bottom) { activityPopover }
            summaryMetric("Guardadas", value: summary.saved, symbol: "checkmark.circle", color: .secondary)
            if summary.attention > 0 {
                summaryMetric("Atención", value: summary.attention, symbol: "exclamationmark.triangle", color: .orange)
            }
            Spacer(minLength: 0)
        }
        .font(.caption)
        .padding(.vertical, 11)
        .frame(maxWidth: .infinity, alignment: .leading)
        .overlay(alignment: .bottom) { Divider() }
        .accessibilityElement(children: .contain)
    }

    private var activityPopover: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text("Actividad de la sesión").font(.headline)
            LabeledContent("En cola", value: "\(summary.queued)")
            LabeledContent("Analizando", value: "\(summary.analyzing)")
            LabeledContent("Listas", value: "\(summary.ready)")
            Divider()
            LabeledContent("Esperando guardado", value: "\(summary.saveQueued)")
            LabeledContent("Guardando y verificando", value: "\(summary.saving)")
            LabeledContent("Guardadas y verificadas", value: "\(summary.saved)")
            Divider()
            LabeledContent("Descartadas", value: "\(summary.discarded)")
            LabeledContent("Atención", value: "\(summary.attention)")
        }
        .font(.callout)
        .padding(20)
        .frame(width: 300)
        .accessibilityElement(children: .contain)
        .accessibilityLabel(summary.accessibilityLabel)
    }

    private var preparationBanner: some View {
        GroupBox {
            ViewThatFits(in: .horizontal) {
                HStack(spacing: 12) {
                    preparationMessage
                    Spacer(minLength: 8)
                    preparationAction
                }
                VStack(alignment: .leading, spacing: 10) {
                    preparationMessage
                    preparationAction
                }
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(preparation.title). \(preparation.detail)")
    }

    private var preparationMessage: some View {
        HStack(alignment: .top, spacing: 10) {
            if preparation.phase == .checking {
                ProgressView().controlSize(.small)
                    .accessibilityLabel("Comprobando preparación local")
            } else {
                Image(systemName: preparation.symbolName).foregroundStyle(.orange)
            }
            VStack(alignment: .leading, spacing: 3) {
                Text(preparation.title).font(.callout.weight(.semibold))
                Text(preparation.detail).font(.caption).foregroundStyle(.secondary)
            }
            .fixedSize(horizontal: false, vertical: true)
        }
    }

    private var preparationAction: some View {
        Button(preparation.actionTitle, action: onPreparationAction)
            .buttonStyle(.borderedProminent)
            .disabled(preparation.phase == .checking)
            .accessibilityHint(preparation.actionAccessibilityHint)
    }

    private var undoDiscardBanner: some View {
        ViewThatFits(in: .horizontal) {
            HStack(spacing: 12) {
                undoMessage
                Spacer()
                undoButton
            }
            VStack(alignment: .leading, spacing: 8) {
                undoMessage
                undoButton
            }
        }
        .padding(10)
        .background(.quaternary, in: RoundedRectangle(cornerRadius: 8))
        .accessibilityElement(children: .contain)
    }

    private var undoMessage: some View {
        Label("Foto descartada sin modificar Fotos.", systemImage: "arrow.uturn.backward.circle")
            .font(.callout)
    }

    private var undoButton: some View {
        Button("Deshacer", action: onUndoDiscard)
            .buttonStyle(.borderedProminent)
            .keyboardShortcut("z", modifiers: .command)
            .disabled(!manualQueueMutationAllowed)
            .accessibilityHint("Devuelve la última foto descartada a la mesa antes de confirmar el descarte.")
    }

    private func summaryMetric(_ title: String, value: Int, symbol: String, color: Color) -> some View {
        Label("\(title): \(value)", systemImage: symbol)
            .foregroundStyle(color)
    }

    private var inspectorPresented: Binding<Bool> {
        Binding(
            get: { inspectedItemID != nil },
            set: { if !$0 { inspectedItemID = nil } }
        )
    }

    private func currentItem(id: String) -> QueueReviewItem? {
        session.item(id: id)
    }

    private func rescanModelOptions(for item: QueueReviewItem) -> [String] {
        var seen = Set<String>()
        return ([item.photo?.modelUsed, controls.modelSelection] + availableModels.map(Optional.some))
            .compactMap { $0?.trimmingCharacters(in: .whitespacesAndNewlines) }
            .filter { !$0.isEmpty && $0 != "adaptive" && seen.insert($0).inserted }
    }

    private func rescanInitialModel(for item: QueueReviewItem) -> String {
        rescanModelOptions(for: item).first ?? ""
    }

    private var photoCountBinding: Binding<Int> {
        Binding(
            get: { controls.photoCount },
            set: { value in mutateControls { _ = $0.setPhotoCount(value) } }
        )
    }

    private var concurrencyBinding: Binding<Int> {
        Binding(
            get: { controls.analysisConcurrency },
            set: { value in mutateControls { _ = $0.setAnalysisConcurrency(value) } }
        )
    }

    private var modelBinding: Binding<String> {
        Binding(
            get: { controls.modelSelection },
            set: { value in mutateControls { _ = $0.setModelSelection(value) } }
        )
    }

    private func booleanControlBinding(
        _ keyPath: WritableKeyPath<ContinuousReviewControls, Bool>
    ) -> Binding<Bool> {
        Binding(
            get: { controls[keyPath: keyPath] },
            set: { value in mutateControls { $0[keyPath: keyPath] = value } }
        )
    }

    private func mutateControls(_ operation: (inout ContinuousReviewControls) -> Void) {
        guard preparation.allowsQueueWork, manualQueueMutationAllowed else { return }
        operation(&controls)
        onControlsChange(controls)
    }
}
