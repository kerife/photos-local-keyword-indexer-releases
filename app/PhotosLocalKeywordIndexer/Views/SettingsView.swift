import SwiftUI
import AppKit

struct SettingsView: View {
    @EnvironmentObject private var model: AppModel
    @EnvironmentObject private var updates: UpdateService
    @State private var diagnosticCopied = false
    @State private var copiedPathTitle: String?

    private var snapshot: SettingsSnapshot {
        SettingsSnapshot(
            paths: .current(),
            modelPolicy: model.modelPolicy,
            singleModel: model.singleModel,
            fastModel: model.fastModel,
            detailedModel: model.detailedModel,
            appleMapsEnabled: model.appleMaps,
            includeCaptionEnabled: model.includeCaption,
            updateStatus: updates.isConfigured ? .configured : .unconfigured,
            automaticChecksEnabled: updates.automaticChecksEnabled,
            buildChannel: BuildChannelCopy.current()
        )
    }

    var body: some View {
        ResponsivePage { width in
            Form {
                Section {
                    Text(SettingsScopeCopy.detail)
                        .font(.callout)
                        .foregroundStyle(.secondary)
                        .accessibilityLabel(SettingsScopeCopy.accessibilityLabel)
                }

                pathsSection(width: width)
                buildChannelSection
                modelsSection(width: width)
                privacySection
                updatesSection(width: width)
                supportSection
            }
            .formStyle(.grouped)
        }
        .navigationTitle("Configuración")
    }

    private var buildChannelSection: some View {
        Section("Canal de build") {
            Label(snapshot.buildChannel.label, systemImage: snapshot.buildChannel.symbolName)
            Text(snapshot.buildChannel.detail)
                .font(.callout)
                .foregroundStyle(.secondary)
                .accessibilityLabel(snapshot.buildChannel.accessibilityLabel)
        }
    }

    private func pathsSection(width: CGFloat) -> some View {
        Section("Almacenamiento local") {
            pathRow("Application Support", value: snapshot.paths.applicationSupport, width: width)
            pathRow("Runs y manifiestos", value: snapshot.paths.runs, width: width)
            pathRow("Helper embebido", value: snapshot.paths.helper, width: width)
            Text("Los manifiestos y CSV permanecen en almacenamiento local privado. No se muestran coordenadas ni datos de Fotos en esta pantalla.")
                .font(.caption)
                .foregroundStyle(.secondary)
            Text("Las opciones de nuevas ejecuciones se conservan en settings.json local; no se guardan imágenes, captions ni respuestas del modelo.")
                .font(.caption)
                .foregroundStyle(.secondary)
            if let copiedPathTitle {
                Label("Ruta copiada: \(copiedPathTitle)", systemImage: "checkmark.circle")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                    .accessibilityLabel("Ruta local copiada: \(copiedPathTitle)")
            }
        }
    }

    private func modelsSection(width: CGFloat) -> some View {
        Section("Defaults de modelos") {
            valueRow("Política", value: snapshot.modelPolicyLabel, width: width)
            if snapshot.modelPolicy == "single" {
                valueRow("Modelo en uso", value: ModelPresentationCopy.display(snapshot.singleModel), width: width)
            } else if snapshot.modelPolicy == "adaptive" {
                valueRow("Modelo rápido", value: ModelPresentationCopy.display(snapshot.fastModel), width: width)
                valueRow("Modelo detallado", value: ModelPresentationCopy.display(snapshot.detailedModel), width: width)
            } else {
                valueRow("Modelos en uso", value: "No disponibles", width: width)
            }
            Text(snapshot.modelPolicyDetail)
                .font(.callout)
                .foregroundStyle(.secondary)
            Text("Estos valores se ejecutan en Ollama local al preparar una nueva ejecución; esta vista no los cambia.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private var privacySection: some View {
        Section("Privacidad y Apple Maps") {
            Label(
                "Análisis local: las imágenes no se suben a servicios cloud.",
                systemImage: LocalAnalysisPrivacyCopy.symbolName
            )
            Label(snapshot.privacy.appleMapsLabel, systemImage: snapshot.privacy.appleMapsEnabled ? "location.fill" : "location.slash")
                .foregroundStyle(snapshot.privacy.appleMapsEnabled ? .orange : .secondary)
            Label(snapshot.privacy.captionLabel, systemImage: "text.quote")
            Text("Apple Maps es una comunicación externa opt-in por ejecución. La aplicación no guarda coordenadas en artefactos ni las muestra aquí.")
                .font(.caption)
                .foregroundStyle(.secondary)
        }
    }

    private func updatesSection(width: CGFloat) -> some View {
        Section("Actualizaciones") {
            valueRow("Estado", value: snapshot.updateStatus.label, width: width)
            Text(snapshot.updateStatus.detail)
                .font(.caption)
                .foregroundStyle(.secondary)
            if snapshot.updateStatus.canCheck {
                Toggle("Comprobar automáticamente al abrir", isOn: Binding(
                        get: { updates.automaticChecksEnabled },
                        set: { updates.setAutomaticChecksEnabled($0) }
                ))
                .accessibilityHint("Consulta solo el feed HTTPS de actualizaciones; no consulta Fotos, no descarga modelos ni modifica keywords ni captions.")
            } else {
                valueRow(
                    "Comprobación automática",
                    value: snapshot.updateStatus.automaticChecksLabel,
                    width: width
                )
            }
            Text(snapshot.updateStatus.manualCheckDetail)
                .font(.caption)
                .foregroundStyle(.secondary)
            Text(snapshot.updateStatus.privacyDetail)
                .font(.caption)
                .foregroundStyle(.secondary)
            if snapshot.updateStatus.canCheck {
                Button("Buscar actualizaciones…") { updates.checkForUpdates() }
            }
        }
    }

    private var supportSection: some View {
        let diagnostic = SettingsSupportDiagnostic(
            snapshot: snapshot,
            appVersion: Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "desconocida",
            operatingSystem: ProcessInfo.processInfo.operatingSystemVersionString,
            preparation: model.preparation
        )
        return Section("Soporte") {
            Label(
                "Genera un diagnóstico local sanitizado para reportar problemas.",
                systemImage: "checkmark.shield"
            )
            Text("Solo incluye versión, sistema, preparación local, configuración de modelos y estados de privacidad. No incluye rutas, UUID, coordenadas, imágenes, captions ni respuestas del modelo.")
            .font(.caption)
            .foregroundStyle(.secondary)
            Button(diagnosticCopied ? "Diagnóstico copiado" : "Copiar diagnóstico sanitizado") {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(diagnostic.report, forType: .string)
                diagnosticCopied = true
            }
            .accessibilityLabel(diagnosticCopied ? "Diagnóstico sanitizado copiado" : diagnostic.accessibilityLabel)
            .accessibilityHint("Copia únicamente estados operativos seguros para compartir con soporte.")
        }
    }

    private func valueRow(_ title: String, value: String, width: CGFloat) -> some View {
        let layout = width < 560
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 6))
            : AnyLayout(HStackLayout(alignment: .top, spacing: 16))
        return layout {
            Text(title)
                .frame(maxWidth: width < 560 ? .infinity : 180, alignment: .leading)
            Text(value)
                .foregroundStyle(.secondary)
                .textSelection(.enabled)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: width < 560 ? .leading : .trailing)
        }
        .accessibilityElement(children: .combine)
    }

    private func pathRow(_ title: String, value: String, width: CGFloat) -> some View {
        let layout = width < 560
            ? AnyLayout(VStackLayout(alignment: .leading, spacing: 6))
            : AnyLayout(HStackLayout(alignment: .top, spacing: 16))
        return layout {
            Text(title)
                .frame(maxWidth: width < 560 ? .infinity : 180, alignment: .leading)
            VStack(alignment: width < 560 ? .leading : .trailing, spacing: 6) {
                Text(SettingsPathCopy.display(value))
                    .font(.caption.monospaced())
                    .textSelection(.enabled)
                    .multilineTextAlignment(width < 560 ? .leading : .trailing)
                    .fixedSize(horizontal: false, vertical: true)
                    .frame(maxWidth: .infinity, alignment: width < 560 ? .leading : .trailing)
                Button("Copiar") {
                    NSPasteboard.general.clearContents()
                    NSPasteboard.general.setString(value, forType: .string)
                    copiedPathTitle = title
                }
                .buttonStyle(.borderless)
                .accessibilityHint("Copia la ruta local completa sin abrir archivos.")
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityLabel("\(title): \(SettingsPathCopy.display(value))")
    }
}
