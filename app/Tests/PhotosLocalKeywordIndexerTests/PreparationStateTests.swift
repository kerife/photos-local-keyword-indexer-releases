import AppKit
import XCTest
@testable import PhotosLocalKeywordIndexer

final class PreparationStateTests: XCTestCase {
    func testLocalAnalysisPrivacySymbolExistsOnSupportedMacOS() {
        XCTAssertNotNil(
            NSImage(
                systemSymbolName: LocalAnalysisPrivacyCopy.symbolName,
                accessibilityDescription: nil
            )
        )
    }

    func testAppRoutesExposeStableNavigationTitlesForTheDetailShell() {
        XCTAssertEqual(AppRoute.setup.navigationTitle, "Preparación local")
        XCTAssertEqual(AppRoute.scan.navigationTitle, "Revisión")
        XCTAssertEqual(AppRoute.preview.navigationTitle, "Revisión")
        XCTAssertEqual(AppRoute.history.navigationTitle, "Historial")
        XCTAssertEqual(AppRoute.settings.navigationTitle, "Configuración")
    }

    func testPreflightCopyIncludesPhotoScriptCompatibilityCheck() {
        XCTAssertTrue(PreparationPreflightCopy.detail.contains("PhotoScript"))
        XCTAssertTrue(PreparationPreflightCopy.detail.contains("Ollama"))
        XCTAssertTrue(PreparationPreflightCopy.detail.contains("no abre ni modifica Fotos"))
    }

    func testStalePreparationCopyExplainsTheSafeNextAction() {
        XCTAssertTrue(PreparationStaleCopy.text.contains("vuelve a comprobar Preparación"))
        XCTAssertTrue(PreparationStaleCopy.accessibilityLabel.contains("cambió desde la última comprobación"))
        XCTAssertFalse(PreparationStaleCopy.text.contains("Aplicar"))
    }

    func testScanStatusMessageDoesNotSayReadyWhenPreflightIsStale() {
        XCTAssertEqual(
            ScanActionCopy.statusMessage(base: "Listo para analizar", preflightIsStale: true),
            PreparationStaleCopy.text
        )
        XCTAssertEqual(
            ScanActionCopy.statusMessage(base: "Listo para analizar", preflightIsStale: false),
            "Listo para analizar"
        )
    }

    func testReadinessSummaryMarksStalePreflightAsNotReadyForScan() {
        let summary = PreparationReadinessSummary(
            state: PreparationState(),
            preflightIsStale: true
        )

        XCTAssertTrue(summary.title.contains("requiere nueva comprobación"))
        XCTAssertEqual(summary.detail, PreparationStaleCopy.text)
        XCTAssertEqual(summary.symbolName, "arrow.triangle.2.circlepath")
        XCTAssertEqual(summary.progressLabel, "Requiere comprobación")
        XCTAssertTrue(summary.accessibilityLabel.contains("requiere nueva comprobación"))
    }

    func testReadinessCardUsesSpanishSectionTitle() {
        XCTAssertEqual(PreparationReadinessSummary.sectionTitle, "Preparación local")
        XCTAssertFalse(PreparationReadinessSummary.sectionTitle.contains("Readiness"))
    }

    func testStalePreparationActionSummaryCannotContinueToScan() {
        var state = PreparationState()
        state.updatePhotos(.authorized)
        state.beginPreflight(models: ["qwen3-vl:4b", "qwen3-vl:4b"])
        state.completePreflight(
            exitCode: 0,
            installedModels: ["qwen3-vl:4b"],
            warningCodes: [],
            errorCodes: [],
            nextAction: "none",
            safeInstruction: nil
        )

        let summary = PreparationActionSummary(state: state, preflightIsStale: true)

        XCTAssertEqual(summary.action, "run_preflight")
        XCTAssertEqual(summary.buttonTitle, "Comprobar de nuevo")
        XCTAssertTrue(summary.detail.contains("cambió"))
        XCTAssertFalse(summary.buttonAccessibilityHint.contains("Nueva ejecución"))
    }

    func testReadinessSummaryMakesPendingLocalChecksVisible() {
        let summary = PreparationReadinessSummary(state: PreparationState())

        XCTAssertEqual(summary.readyCount, 0)
        XCTAssertEqual(summary.requiredCount, 3)
        XCTAssertEqual(summary.fraction, 0)
        XCTAssertEqual(summary.title, "0 de 3 componentes requeridos listos")
        XCTAssertTrue(summary.detail.contains("Completa la comprobación local"))
        XCTAssertTrue(summary.accessibilityLabel.contains(summary.title))
    }

    func testPhotosStatusCopyDoesNotImplyAccessBeforeItIsChecked() {
        XCTAssertEqual(
            PhotosStatusCopy.detail(state: .pending, accessIsLimited: false),
            "Aún no se ha comprobado el acceso de lectura a Fotos."
        )
        XCTAssertEqual(
            PhotosStatusCopy.detail(state: .checking, accessIsLimited: false),
            "Comprobando el acceso de lectura a Fotos."
        )
        XCTAssertFalse(
            PhotosStatusCopy.detail(state: .pending, accessIsLimited: false)
                .contains("Lectura y exportación")
        )
    }

    func testPhotosStatusCopyKeepsWorkerVerificationPendingAfterAppAuthorization() {
        XCTAssertEqual(
            PhotosStatusCopy.detail(state: .ready, accessIsLimited: false),
            "Acceso de Fotos concedido a la app; PhotosIndexerWorker comprobará su propio acceso de lectura y exportación al iniciar el dry-run. Las escrituras siguen requiriendo confirmación."
        )
        XCTAssertEqual(
            PhotosStatusCopy.detail(state: .ready, accessIsLimited: true),
            "Acceso limitado concedido a la app; PhotosIndexerWorker comprobará su acceso y las fotos visibles al iniciar el dry-run."
        )
        XCTAssertFalse(
            PhotosStatusCopy.detail(state: .ready, accessIsLimited: false)
                .contains("exportación disponible")
        )
    }

    func testOnboardingUsesTheWorkerAwarePhotosStatusCopy() throws {
        let appRoot = URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent()
            .deletingLastPathComponent()
            .deletingLastPathComponent()
        let source = try String(
            contentsOf: appRoot.appendingPathComponent("PhotosLocalKeywordIndexer/Views/OnboardingView.swift"),
            encoding: .utf8
        )

        XCTAssertTrue(source.contains("PhotosStatusCopy.detail("))
        XCTAssertTrue(
            PhotosStatusCopy.detail(state: .ready, accessIsLimited: false)
                .contains("PhotosIndexerWorker")
        )
    }

    func testReadinessSummarySeparatesDeferredAutomationFromRequiredComponents() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:4b"])
        preparation.completePreflight(
            exitCode: 0,
            installedModels: ["qwen3-vl:4b"],
            warningCodes: [],
            errorCodes: [],
            nextAction: "none",
            safeInstruction: nil
        )

        let summary = PreparationReadinessSummary(state: preparation)

        XCTAssertEqual(summary.readyCount, 3)
        XCTAssertEqual(summary.fraction, 1)
        XCTAssertEqual(summary.title, "3 de 3 componentes base listos")
        XCTAssertEqual(summary.progressLabel, "Base lista")
        XCTAssertEqual(summary.symbolName, "clock.badge.checkmark")
        XCTAssertTrue(summary.detail.contains("Automatización aún no está verificada"))
        XCTAssertTrue(summary.accessibilityLabel.contains("iniciar PhotoScript"))

        let action = PreparationActionSummary(state: preparation)
        XCTAssertEqual(action.buttonTitle, "Continuar y comprobar PhotoScript")
        XCTAssertTrue(action.buttonAccessibilityHint.contains("mesa continua"))
        XCTAssertTrue(action.buttonAccessibilityHint.contains("Automatización"))
        XCTAssertTrue(action.buttonAccessibilityHint.contains("No se escribirán cambios"))
    }

    func testReadinessSummaryCallsOutBlockingPermissionFailure() {
        var preparation = PreparationState()
        preparation.updatePhotos(.denied)

        let summary = PreparationReadinessSummary(state: preparation)

        XCTAssertEqual(summary.readyCount, 0)
        XCTAssertTrue(summary.detail.contains("Corrige el componente marcado"))
        XCTAssertFalse(summary.accessibilityLabel.contains("componente interno"))
    }

    func testReadinessSummaryCountsObservedAutomationFailureAsRequired() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:4b"])
        preparation.completePreflight(
            exitCode: 0,
            installedModels: ["qwen3-vl:4b"],
            warningCodes: [],
            errorCodes: [],
            nextAction: "none",
            safeInstruction: nil
        )
        preparation.applyPermissionErrors(["PHOTOS_AUTOMATION_DENIED"])

        let summary = PreparationReadinessSummary(state: preparation)

        XCTAssertEqual(summary.readyCount, 3)
        XCTAssertEqual(summary.requiredCount, 4)
        XCTAssertEqual(summary.fraction, 0.75)
        XCTAssertEqual(summary.title, "3 de 4 componentes requeridos listos")
        XCTAssertTrue(summary.detail.contains("Corrige el componente marcado"))
        XCTAssertFalse(summary.accessibilityLabel.contains("3 de 3"))
    }

    func testPreparationActionSummaryStartsWithAVisibleLocalCheck() {
        let summary = PreparationActionSummary(state: PreparationState())

        XCTAssertEqual(summary.action, "run_preflight")
        XCTAssertEqual(summary.title, "Comprueba los componentes locales")
        XCTAssertEqual(summary.buttonTitle, "Comprobar preparación local")
        XCTAssertEqual(summary.symbolName, "checkmark.shield")
        XCTAssertNil(summary.safeInstruction)
        XCTAssertFalse(summary.isBlocking)
    }

    func testMissingModelActionShowsOnlyAValidatedInstallCommandWithoutCopyAffordance() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:8b"])
        preparation.completePreflight(
            exitCode: 2,
            installedModels: [],
            warningCodes: [],
            errorCodes: ["OLLAMA_MODEL_MISSING"],
            nextAction: "install_missing_model",
            safeInstruction: "ollama pull qwen3-vl:8b"
        )

        let summary = PreparationActionSummary(state: preparation)

        XCTAssertEqual(summary.action, "install_missing_model")
        XCTAssertEqual(summary.title, "Instala el modelo local")
        XCTAssertEqual(summary.buttonTitle, "Volver a comprobar")
        XCTAssertEqual(summary.safeInstruction, "ollama pull qwen3-vl:8b")
        XCTAssertTrue(summary.isBlocking)
        XCTAssertFalse(summary.accessibilityLabel.contains("OLLAMA_MODEL_MISSING"))
        XCTAssertFalse(summary.accessibilityLabel.contains("/"))
    }

    func testPreflightProgressPrioritizesUnavailableOllamaOverMissingModels() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:4b"])
        preparation.completePreflight(
            exitCode: 2,
            installedModels: [],
            warningCodes: [],
            errorCodes: ["OLLAMA_UNAVAILABLE"],
            nextAction: "retry_preflight",
            safeInstruction: nil
        )

        XCTAssertEqual(
            preparation.preflightProgressText,
            "Ollama no está disponible en 127.0.0.1:11434. Inicia el servicio local y vuelve a comprobar."
        )
    }

    func testPhotoScriptFailureDoesNotMislabelHealthyOllamaAndModels() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:4b"])
        preparation.completePreflight(
            exitCode: 2,
            installedModels: ["qwen3-vl:4b"],
            warningCodes: [],
            errorCodes: ["PHOTOSCRIPT_UNAVAILABLE"],
            nextAction: "fix_fatal_error",
            safeInstruction: nil
        )

        XCTAssertEqual(preparation.ollama, .ready)
        XCTAssertEqual(preparation.models, .ready)
        XCTAssertEqual(preparation.automation, .failed)
        XCTAssertFalse(preparation.isReady)
        XCTAssertTrue(
            AutomationStatusCopy.detail(
                state: preparation.automation,
                errorCodes: preparation.errorCodes
            ).contains("PhotoScript")
        )
    }

    func testPreflightProgressDoesNotPresentPhotoScriptFailureAsDryRunReady() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:4b"])
        preparation.completePreflight(
            exitCode: 2,
            installedModels: ["qwen3-vl:4b"],
            warningCodes: [],
            errorCodes: ["PHOTOSCRIPT_UNAVAILABLE"],
            nextAction: "fix_fatal_error",
            safeInstruction: nil
        )

        XCTAssertTrue(preparation.preflightProgressText.contains("PhotoScript"))
        XCTAssertFalse(preparation.preflightProgressText.contains("listos para un dry-run"))
    }

    func testUnsafePreflightInstructionNeverReachesThePreparationCard() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:8b"])
        preparation.completePreflight(
            exitCode: 2,
            installedModels: [],
            warningCodes: [],
            errorCodes: ["OLLAMA_MODEL_MISSING"],
            nextAction: "install_missing_model",
            safeInstruction: "ollama pull qwen3-vl:8b && open /private/secret"
        )

        let summary = PreparationActionSummary(state: preparation)

        XCTAssertNil(summary.safeInstruction)
        XCTAssertFalse(summary.accessibilityLabel.contains("secret"))
    }

    func testPreparationRejectsUnsafeOllamaPullInstructions() {
        let instructions = [
            "ollama pull qwen3-vl:4b && open /private/tmp",
            "ollama pull ../private/model",
            "ollama pull qwen3-vl:4b --insecure",
            "ollama pull vision:cloud",
        ]

        for instruction in instructions {
            var preparation = PreparationState()
            preparation.updatePhotos(.authorized)
            preparation.beginPreflight(models: ["qwen3-vl:4b"])
            preparation.completePreflight(
                exitCode: 2,
                installedModels: [],
                warningCodes: [],
                errorCodes: ["OLLAMA_MODEL_MISSING"],
                nextAction: "install_missing_model",
                safeInstruction: instruction
            )

            XCTAssertNil(preparation.safeInstruction, instruction)
        }
    }

    func testPhotosPermissionActionProvidesASettingsSafeNextStep() {
        var preparation = PreparationState()
        preparation.updatePhotos(.denied)

        let summary = PreparationActionSummary(state: preparation)

        XCTAssertEqual(summary.action, "grant_photos_access")
        XCTAssertEqual(summary.title, "Concede acceso de lectura a Fotos")
        XCTAssertEqual(summary.buttonTitle, "Abrir configuración de Fotos")
        XCTAssertTrue(summary.buttonAccessibilityHint.contains("activa"))
        XCTAssertTrue(summary.detail.contains("Configuración del Sistema"))
        XCTAssertTrue(summary.isBlocking)
    }

    func testPhotosPermissionRefreshMovesPastTheStaleSettingsCTA() {
        var preparation = PreparationState()
        preparation.updatePhotos(.denied)
        XCTAssertEqual(preparation.nextAction, "grant_photos_access")

        preparation.updatePhotos(.authorized)

        XCTAssertEqual(preparation.nextAction, "run_preflight")
        XCTAssertFalse(preparation.statusMessage.contains("Conceder acceso a Fotos"))
    }

    func testAutomationCTAExplainsThatTCCIsVerifiedOnlyByPhotoScript() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.applyPermissionErrors(["PHOTOS_AUTOMATION_DENIED"])

        let summary = PreparationActionSummary(state: preparation)

        XCTAssertEqual(summary.action, "grant_photos_automation")
        XCTAssertTrue(summary.buttonAccessibilityHint.contains("PhotoScript"))
        XCTAssertTrue(summary.detail.contains("PhotoScript"))
    }

    func testAutomationRecoveryCTARearmsDeniedStateWithoutClaimingVerification() {
        var preparation = makeBaseReadyPreparation()
        preparation.applyPermissionErrors(["PHOTOS_AUTOMATION_DENIED"])

        XCTAssertEqual(preparation.automation, .actionRequired)
        preparation.prepareAutomationRetry()

        XCTAssertEqual(preparation.automation, .pending)
        XCTAssertTrue(preparation.isReady)
        XCTAssertEqual(preparation.nextAction, "none")
        XCTAssertFalse(preparation.statusMessage.contains("verificado"))
        XCTAssertEqual(AutomationRecoveryCopy.buttonTitle, "Ya concedí Automatización · continuar")
        XCTAssertTrue(AutomationRecoveryCopy.buttonAccessibilityHint.lowercased().contains("no confirma"))
        XCTAssertTrue(AutomationRecoveryCopy.buttonAccessibilityHint.lowercased().contains("no se abrirá un shell"))
    }

    func testAutomationRecoveryTreatsOnlyObservedPermissionDenialAsRetryable() {
        XCTAssertTrue(
            AutomationRecoveryPolicy.canRetry(
                from: .actionRequired,
                errorCodes: ["PHOTOS_AUTOMATION_DENIED"]
            )
        )
        XCTAssertFalse(
            AutomationRecoveryPolicy.canRetry(
                from: .failed,
                errorCodes: ["PHOTOSCRIPT_UNAVAILABLE"]
            )
        )
        XCTAssertEqual(
            AutomationRecoveryPolicy.stateAfterCTA(from: .failed),
            .pending
        )
        XCTAssertFalse(AutomationRecoveryCopy.buttonAccessibilityHint.contains("ejecuta"))
    }

    func testPhotoScriptCompatibilityFailureCannotBeRearmedAsAutomationPermission() {
        var preparation = makeBaseReadyPreparation()
        preparation.completePreflight(
            exitCode: 2,
            installedModels: ["qwen3-vl:4b"],
            warningCodes: [],
            errorCodes: ["PHOTOSCRIPT_UNAVAILABLE"],
            nextAction: "fix_fatal_error",
            safeInstruction: nil
        )

        XCTAssertEqual(preparation.automation, .failed)
        XCTAssertFalse(preparation.canPrepareAutomationRetry)

        preparation.prepareAutomationRetry()

        XCTAssertEqual(preparation.automation, .failed)
        XCTAssertFalse(preparation.isReady)
        XCTAssertEqual(preparation.nextAction, "fix_fatal_error")
        XCTAssertTrue(preparation.errorCodes.contains("PHOTOSCRIPT_UNAVAILABLE"))
    }

    func testPackagedAutomationGuideNamesTheExactHelperAndRejectsTerminalAsASubstitute() {
        let guide = AutomationTCCGuide.packagedHelper

        XCTAssertEqual(guide.displayName, "PhotosIndexerWorker")
        XCTAssertTrue(guide.detail.contains("PhotosIndexerWorker"))
        XCTAssertTrue(guide.detail.contains("autorizar Terminal por separado no concede"))
        XCTAssertTrue(guide.accessibilityHint.contains("PhotosIndexerWorker"))
        XCTAssertTrue(guide.accessibilityHint.contains("Terminal no es suficiente"))
    }

    func testAutomationStatusCopyNamesTheSignedHelperWhenPermissionNeedsAction() {
        let detail = AutomationStatusCopy.detail(state: .actionRequired)

        XCTAssertTrue(detail.contains("PhotosIndexerWorker"))
        XCTAssertFalse(detail.contains("esta app"))
        XCTAssertFalse(detail.contains("/"))
        XCTAssertTrue(AutomationStatusCopy.detail(state: .pending).contains("comprobar pasivamente"))
    }

    func testDevelopmentAutomationGuidePointsToTheInterpreterThatSendsAppleEvents() {
        let guide = AutomationTCCGuide.developmentInterpreter

        XCTAssertEqual(guide.displayName, "el intérprete Python que ejecuta PhotoScript")
        XCTAssertTrue(guide.detail.contains("intérprete Python"))
        XCTAssertTrue(guide.accessibilityHint.contains("intérprete Python"))
        XCTAssertFalse(guide.detail.contains("PhotosIndexerWorker"))
    }

    func testAutomationActionSummaryUsesThePackagedHelperIdentity() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.applyPermissionErrors(["PHOTOS_AUTOMATION_DENIED"])

        let summary = PreparationActionSummary(state: preparation)

        XCTAssertTrue(summary.detail.contains("PhotosIndexerWorker"))
        XCTAssertTrue(summary.buttonAccessibilityHint.contains("Terminal no es suficiente"))
        XCTAssertTrue(summary.accessibilityLabel.contains("helper local"))
    }

    func testPhotosAndAutomationGuideStatesThatTheTCCDecisionsAreIndependent() {
        XCTAssertEqual(PermissionBoundaryGuide.sectionTitle, "Permisos de Fotos y Automatización")
        XCTAssertTrue(PermissionBoundaryGuide.title.contains("distintos"))
        XCTAssertTrue(PermissionBoundaryGuide.distinction.contains("uno"))
        XCTAssertTrue(PermissionBoundaryGuide.photos.contains("PhotoKit"))
        XCTAssertTrue(PermissionBoundaryGuide.automation.contains("PhotosIndexerWorker"))
        XCTAssertTrue(PermissionBoundaryGuide.accessibilityLabel.contains("Terminal, Codex u otro intérprete"))
        XCTAssertTrue(PermissionBoundaryGuide.accessibilityLabel.contains("Conceder uno no concede el otro"))
    }

    func testPhotosTCCGuideDistinguishesInstalledAppFromDevelopmentInterpreter() {
        let packaged = PhotosTCCGuide.packagedApplication
        let development = PhotosTCCGuide.developmentInterpreter

        XCTAssertTrue(packaged.detail.contains("app firmada"))
        XCTAssertTrue(packaged.detail.contains("Photos Local Keyword Indexer"))
        XCTAssertTrue(packaged.accessibilityHint.contains("helper"))
        XCTAssertTrue(development.detail.contains("intérprete Python"))
        XCTAssertTrue(development.accessibilityHint.contains("CLI"))
        XCTAssertTrue(development.detail.contains("app firmada"))
    }

    func testPackagedPhotosGuideNamesTheHelperThatActuallyUsesPhotoKit() {
        let guide = PhotosTCCGuide.packagedApplication

        XCTAssertTrue(guide.detail.contains("PhotosIndexerWorker"))
        XCTAssertTrue(guide.detail.contains("concretamente"))
        XCTAssertTrue(guide.accessibilityHint.contains("PhotosIndexerWorker"))
        XCTAssertTrue(guide.recoveryActionText.contains("PhotosIndexerWorker"))
    }

    func testPhotosAccessActionCopyExplainsRequestAndSettingsActions() {
        let requestHint = PhotosAccessActionCopy.accessibilityHint(for: .pending)
        XCTAssertTrue(requestHint.contains("permiso de lectura"))
        XCTAssertTrue(requestHint.contains("No se escribirán cambios"))

        let settingsHint = PhotosAccessActionCopy.accessibilityHint(for: .actionRequired)
        XCTAssertTrue(settingsHint.contains("configuración de Fotos"))
        XCTAssertTrue(settingsHint.contains("Después vuelve aquí"))
    }

    func testPreflightProgressNamesRequestedModelsWhileChecking() {
        var preparation = PreparationState()
        preparation.beginPreflight(models: ["qwen3-vl:4b", "qwen3-vl:8b"])

        XCTAssertEqual(preparation.preflightProgressText, "Comprobando Ollama y modelos locales: qwen3-vl:4b, qwen3-vl:8b.")
        XCTAssertTrue(preparation.preflightProgressAccessibilityLabel.contains("qwen3-vl:8b"))
    }

    func testPreflightProgressDeduplicatesAnAdaptiveModelUsedForBothRoles() {
        var preparation = PreparationState()
        preparation.beginPreflight(models: ["qwen3-vl:4b", "qwen3-vl:4b"])

        XCTAssertEqual(preparation.preflightProgressText, "Comprobando Ollama y modelos locales: qwen3-vl:4b.")
        XCTAssertEqual(
            preparation.preflightProgressAccessibilityLabel,
            "Progreso de preparación: Comprobando Ollama y modelos locales: qwen3-vl:4b."
        )
    }

    func testPreflightProgressNamesMissingModelAndSafeNextStep() {
        var preparation = PreparationState()
        preparation.beginPreflight(models: ["qwen3-vl:4b", "qwen3-vl:8b"])
        preparation.completePreflight(
            exitCode: 2,
            installedModels: ["qwen3-vl:4b"],
            warningCodes: [],
            errorCodes: ["OLLAMA_MODEL_MISSING"],
            nextAction: "install_missing_model",
            safeInstruction: "ollama pull qwen3-vl:8b"
        )

        XCTAssertEqual(preparation.preflightProgressText, "Falta instalar: qwen3-vl:8b. Ejecuta el comando indicado y vuelve a comprobar.")
        XCTAssertTrue(preparation.preflightProgressAccessibilityLabel.contains("Falta instalar"))
    }

    func testSuccessfulPreflightDoesNotEraseDeferredAutomationRecovery() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.applyPermissionErrors(["PHOTOS_AUTOMATION_DENIED"])
        preparation.beginPreflight(models: ["qwen3-vl:4b"])
        preparation.completePreflight(
            exitCode: 0,
            installedModels: ["qwen3-vl:4b"],
            warningCodes: [],
            errorCodes: [],
            nextAction: "none",
            safeInstruction: nil
        )

        XCTAssertEqual(preparation.nextAction, "grant_photos_automation")
        XCTAssertFalse(preparation.isReady)
    }

    func testReadyPreparationActionInvitesAReadOnlyDryRun() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:4b"])
        preparation.completePreflight(
            exitCode: 0,
            installedModels: ["qwen3-vl:4b"],
            warningCodes: [],
            errorCodes: [],
            nextAction: "none",
            safeInstruction: nil
        )

        let summary = PreparationActionSummary(state: preparation)

        XCTAssertEqual(summary.action, "none")
        XCTAssertEqual(summary.title, "Componentes base listos")
        XCTAssertTrue(summary.detail.contains("Automatización de Fotos se comprobará"))
        XCTAssertEqual(summary.buttonTitle, "Continuar y comprobar PhotoScript")
        XCTAssertFalse(summary.isBlocking)
    }

    func testScanOptionsLockWhileWorkerIsRunningAndUnlockAfterCompletion() {
        XCTAssertTrue(ScanControlState.optionsAreEditable(workerIsRunning: false))
        XCTAssertFalse(ScanControlState.optionsAreEditable(workerIsRunning: true))
        XCTAssertEqual(
            ScanControlState.lockedMessage,
            "Opciones bloqueadas durante el análisis; se aplicarán a la siguiente ejecución."
        )
    }

    func testCancellationControlBecomesExplicitAndSingleShotAfterRequest() {
        XCTAssertEqual(
            ScanControlState.cancellationButtonTitle(isCancellationRequested: false),
            "Cancelar"
        )
        XCTAssertTrue(ScanControlState.canRequestCancellation(isCancellationRequested: false))

        XCTAssertEqual(
            ScanControlState.cancellationButtonTitle(isCancellationRequested: true),
            "Cancelación solicitada…"
        )
        XCTAssertFalse(ScanControlState.canRequestCancellation(isCancellationRequested: true))
        XCTAssertEqual(
            ScanControlState.cancellationAccessibilityHint(isCancellationRequested: true),
            "La operación terminará de forma cooperativa; no se iniciarán fotos nuevas."
        )
    }

    func testFreshInstallationStartsInPreparationAndBlocksScan() {
        let navigation = AppNavigationState()
        let preparation = PreparationState()

        XCTAssertEqual(navigation.route, .setup)
        XCTAssertFalse(preparation.isReady)
        XCTAssertFalse(navigation.canNavigate(to: .scan, preparation: preparation))
    }

    func testBlockedScanNavigationReturnsToPreparation() {
        var navigation = AppNavigationState()

        XCTAssertFalse(navigation.navigate(to: .scan, preparation: PreparationState()))
        XCTAssertEqual(navigation.route, .setup)
    }

    func testStalePreflightBlocksNewExecutionNavigationAndExplainsRecovery() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:4b"])
        preparation.completePreflight(
            exitCode: 0,
            installedModels: ["qwen3-vl:4b"],
            warningCodes: [],
            errorCodes: [],
            nextAction: "none",
            safeInstruction: nil
        )
        var navigation = AppNavigationState()

        XCTAssertTrue(preparation.isReady)
        XCTAssertFalse(navigation.canNavigate(to: .scan, preparation: preparation, preflightIsStale: true))
        XCTAssertFalse(navigation.navigate(to: .scan, preparation: preparation, preflightIsStale: true))
        XCTAssertEqual(navigation.route, .setup)
        XCTAssertTrue(
            navigation.accessibilityValue(
                for: .scan,
                preparation: preparation,
                hasPreview: false,
                preflightIsStale: true
            ).contains("obsoleta")
        )
    }

    func testReviewNavigationRequiresALoadedManifest() {
        let navigation = AppNavigationState()
        let preparation = PreparationState()

        XCTAssertTrue(navigation.canNavigate(to: .preview, preparation: preparation, hasPreview: false))
        XCTAssertTrue(navigation.canNavigate(to: .preview, preparation: preparation, hasPreview: true))
    }

    func testNavigationAccessibilityKeepsContinuousReviewAvailable() {
        let navigation = AppNavigationState()

        XCTAssertEqual(
            navigation.accessibilityValue(
                for: .preview,
                preparation: PreparationState(),
                hasPreview: false
            ),
            "Disponible"
        )
        XCTAssertEqual(
            navigation.accessibilityValue(
                for: .scan,
                preparation: PreparationState(),
                hasPreview: false
            ),
            "No disponible: completa Preparación antes de iniciar un análisis."
        )
    }

    func testAuthorizedPhotosAndSuccessfulLocalPreflightUnlockScan() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:4b", "qwen3-vl:8b"])
        preparation.completePreflight(
            exitCode: 0,
            installedModels: ["qwen3-vl:4b", "qwen3-vl:8b"],
            warningCodes: [],
            errorCodes: [],
            nextAction: "none",
            safeInstruction: nil
        )

        XCTAssertTrue(preparation.isReady)
        XCTAssertEqual(preparation.ollama, .ready)
        XCTAssertEqual(preparation.models, .ready)
        XCTAssertTrue(AppNavigationState().canNavigate(to: .scan, preparation: preparation))
    }

    func testSuccessfulLocalPreflightKeepsPhotosAccessAsTheNextSafeAction() {
        var preparation = PreparationState()
        preparation.updatePhotos(.denied)
        preparation.beginPreflight(models: ["qwen3-vl:4b"])
        preparation.completePreflight(
            exitCode: 0,
            installedModels: ["qwen3-vl:4b"],
            warningCodes: [],
            errorCodes: [],
            nextAction: "none",
            safeInstruction: nil
        )

        XCTAssertFalse(preparation.isReady)
        XCTAssertEqual(preparation.nextAction, "grant_photos_access")
        XCTAssertEqual(preparation.nextActionText, "Conceder acceso a Fotos")
        XCTAssertTrue(preparation.statusMessage.contains("Conceder acceso a Fotos"))
        XCTAssertFalse(preparation.statusMessage.contains("Iniciar una nueva ejecución"))
    }

    func testAutomationIsShownAsDeferredAndDoesNotPretendTCCWasChecked() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:4b"])
        preparation.completePreflight(
            exitCode: 0,
            installedModels: ["qwen3-vl:4b"],
            warningCodes: [],
            errorCodes: [],
            nextAction: "none",
            safeInstruction: nil
        )

        XCTAssertEqual(preparation.automation, .pending)
        XCTAssertTrue(preparation.isReady)
        XCTAssertTrue(preparation.statusMessage.contains("Automatización de Fotos"))
    }

    func testReadyPreflightKeepsWarningsVisibleWithoutBlockingTheDryRun() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:4b"])
        preparation.completePreflight(
            exitCode: 0,
            installedModels: ["qwen3-vl:4b"],
            warningCodes: ["OLLAMA_VERSION_OLD"],
            errorCodes: [],
            nextAction: "none",
            safeInstruction: nil
        )

        XCTAssertTrue(preparation.isReady)
        XCTAssertTrue(preparation.statusMessage.contains("Preparación completa con advertencias"))
        XCTAssertTrue(preparation.statusMessage.contains("Ollama necesita una versión más reciente"))
        XCTAssertTrue(preparation.preflightProgressText.contains("listos con advertencias"))
        XCTAssertTrue(preparation.statusMessage.contains("Automatización de Fotos"))
    }

    func testReadyPreparationWithWarningsUsesWarningReadinessHierarchy() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:4b"])
        preparation.completePreflight(
            exitCode: 0,
            installedModels: ["qwen3-vl:4b"],
            warningCodes: ["OLLAMA_VERSION_OLD"],
            errorCodes: [],
            nextAction: "none",
            safeInstruction: nil
        )

        let summary = PreparationReadinessSummary(state: preparation)

        XCTAssertTrue(preparation.hasWarnings)
        XCTAssertEqual(summary.title, "3 de 3 componentes base listos con advertencias")
        XCTAssertEqual(summary.progressLabel, "Base lista con advertencias")
        XCTAssertEqual(summary.symbolName, "exclamationmark.triangle.fill")
        XCTAssertTrue(summary.accessibilityLabel.contains("listos con advertencias"))
        XCTAssertTrue(summary.accessibilityLabel.contains("Revisa el estado indicado"))
    }

    func testReadyPreparationCTAAdvancesThroughTheGuardedNavigationState() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:4b"])
        preparation.completePreflight(
            exitCode: 0,
            installedModels: ["qwen3-vl:4b"],
            warningCodes: [],
            errorCodes: [],
            nextAction: "none",
            safeInstruction: nil
        )
        var navigation = AppNavigationState()

        XCTAssertTrue(navigation.navigate(to: .scan, preparation: preparation))
        XCTAssertEqual(navigation.route, .scan)
    }

    func testFailedPreflightPreservesSpecificNextActionAndCodes() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:8b"])
        preparation.completePreflight(
            exitCode: 2,
            installedModels: [],
            warningCodes: ["OLLAMA_VERSION_OLD"],
            errorCodes: ["OLLAMA_MODEL_MISSING"],
            nextAction: "install_missing_model",
            safeInstruction: "ollama pull qwen3-vl:8b"
        )

        XCTAssertFalse(preparation.isReady)
        XCTAssertEqual(preparation.nextAction, "install_missing_model")
        XCTAssertEqual(preparation.warningCodes, ["OLLAMA_VERSION_OLD"])
        XCTAssertEqual(preparation.errorCodes, ["OLLAMA_MODEL_MISSING"])
        XCTAssertEqual(preparation.safeInstruction, "ollama pull qwen3-vl:8b")
        XCTAssertTrue(preparation.statusMessage.contains("Instalar el modelo indicado"))
        XCTAssertFalse(preparation.statusMessage.contains("install_missing_model"))
    }

    func testMissingModelFromIPCUsesInstallRecoveryCopyEvenWithFatalNextAction() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:4b"])
        preparation.completePreflight(
            exitCode: 2,
            installedModels: [],
            warningCodes: [],
            errorCodes: ["OLLAMA_MODEL_MISSING"],
            nextAction: "fix_fatal_error",
            safeInstruction: "ollama pull qwen3-vl:4b"
        )

        let summary = PreparationActionSummary(state: preparation)

        XCTAssertEqual(summary.title, "Instala el modelo local")
        XCTAssertEqual(summary.safeInstruction, "ollama pull qwen3-vl:4b")
        XCTAssertTrue(summary.detail.contains("nunca descarga modelos"))
        XCTAssertFalse(summary.accessibilityLabel.contains("fix_fatal_error"))
    }

    func testNextActionTextUsesHumanSafeCopyWithoutExposingInternalCode() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:8b"])
        preparation.completePreflight(
            exitCode: 2,
            installedModels: [],
            warningCodes: [],
            errorCodes: ["OLLAMA_MODEL_MISSING"],
            nextAction: "install_missing_model",
            safeInstruction: "ollama pull qwen3-vl:8b"
        )

        XCTAssertEqual(preparation.nextActionText, "Instalar el modelo indicado")
        XCTAssertFalse(preparation.nextActionText.contains("install_missing_model"))
    }

    func testMutationValidationCodesUseSpecificSafeCopyAndActions() {
        let cases: [(String, String, String)] = [
            (
                "SCAN_NOT_READY",
                "fix_failed_scan",
                "El análisis aún no está listo; ejecuta un dry-run nuevo antes de continuar."
            ),
            (
                "REVIEW_PROVENANCE_INVALID",
                "rescan_after_provenance",
                "El manifiesto revisado no conserva una fuente local verificable; ejecuta un dry-run nuevo antes de aplicar."
            ),
            (
                "MUTATION_EVIDENCE_INVALID",
                "rescan_after_mutation_evidence",
                "No se pudieron verificar los cambios registrados; revisa Fotos manualmente y ejecuta un dry-run nuevo."
            ),
            (
                "REVIEW_NOT_PRISTINE",
                "rescan_after_provenance",
                "El manifiesto revisado ya fue modificado; ejecuta un dry-run nuevo antes de aplicar."
            ),
            (
                "ROLLBACK_ALREADY_STARTED",
                "manual_review",
                "La reversión ya comenzó; revisa Fotos manualmente antes de intentarlo de nuevo."
            )
        ]

        for (code, action, expectedMessage) in cases {
            let message = HumanErrorCopy.message(for: code)
            XCTAssertEqual(message, expectedMessage, code)
            XCTAssertFalse(message.contains(code), code)
            XCTAssertNotNil(HumanNextActionCopy.message(for: action), code)
            XCTAssertFalse(HumanNextActionCopy.message(for: action)?.contains(action) == true, code)
        }

        XCTAssertEqual(
            HumanNextActionCopy.message(for: "rescan_after_permissions"),
            "Comprobar permisos y ejecutar un nuevo dry-run"
        )
        XCTAssertEqual(
            HumanNextActionCopy.message(for: "rescan_after_provenance"),
            "Ejecutar un nuevo dry-run con su fuente local"
        )
        XCTAssertEqual(
            HumanNextActionCopy.message(for: "rescan_after_mutation_evidence"),
            "Revisar Fotos y ejecutar un nuevo dry-run"
        )
    }

    func testFatalWorkflowCodesUseSpecificSafeHumanCopy() {
        let codes = [
            "PLATFORM_UNSUPPORTED",
            "LIMIT_INVALID",
            "MODEL_INVALID",
            "SCAN_OPTIONS_INVALID",
            "SCAN_SETUP_FAILED",
            "UNSAFE_REVIEW_RESULT",
            "MANIFEST_WRITE_FAILED",
            "MANIFEST_INVALID",
            "LOCK_OR_MANIFEST_FAILED",
            "MANIFEST_PATH_INVALID",
            "MODEL_POLICY_UNSUPPORTED",
            "RUNS_ROOT_INVALID"
        ]

        for code in codes {
            let message = HumanErrorCopy.message(for: code)
            XCTAssertNotEqual(message, "La ejecución necesita revisión manual.", code)
            XCTAssertFalse(message.contains(code), code)
        }
    }

    func testPhotoAndCleanupCodesUseSpecificSafeHumanCopy() {
        let codes = [
            "WORKSPACE_FAILED",
            "IDENTITY_MISMATCH",
            "APPLIED_KEYWORD_MISSING",
            "CASING_CONFLICT",
            "EXPORT_DELETE_FAILED",
            "INTERRUPTED_WRITE",
            "APPLY_READ_FAILED",
            "KEYWORD_WRITE_UNCERTAIN",
            "CAPTION_WRITE_UNCERTAIN",
            "INTERRUPTED_REMOVAL",
            "ROLLBACK_FAILED",
            "REMOVAL_UNCERTAIN"
        ]

        for code in codes {
            let message = HumanErrorCopy.message(for: code)
            XCTAssertNotEqual(message, "La ejecución necesita revisión manual.", code)
            XCTAssertFalse(message.contains(code), code)
        }
    }

    func testMissingModelActionExposesSafeCopyInstructionWithoutExecutionPromise() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:4b"])
        preparation.completePreflight(
            exitCode: 2,
            installedModels: [],
            warningCodes: [],
            errorCodes: ["OLLAMA_MODEL_MISSING"],
            nextAction: "install_missing_model",
            safeInstruction: "ollama pull qwen3-vl:4b"
        )

        let summary = PreparationActionSummary(state: preparation)

        XCTAssertEqual(summary.safeInstruction, "ollama pull qwen3-vl:4b")
        XCTAssertEqual(
            summary.commandCopyAccessibilityLabel(copied: false),
            "Copiar comando: ollama pull qwen3-vl:4b"
        )
        XCTAssertTrue(summary.commandCopyAccessibilityHint.contains("no lo ejecutará"))
        XCTAssertTrue(summary.commandCopyAccessibilityHint.contains("ni descargará el modelo"))
    }

    func testModelStatusNamesMissingModelsInThePreparationGrid() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:4b", "qwen3-vl:8b"])
        preparation.completePreflight(
            exitCode: 2,
            installedModels: [],
            warningCodes: [],
            errorCodes: ["OLLAMA_MODEL_MISSING"],
            nextAction: "install_missing_model",
            safeInstruction: "ollama pull qwen3-vl:4b"
        )

        XCTAssertEqual(
            preparation.modelStatusText,
            "Faltan modelos: qwen3-vl:4b, qwen3-vl:8b. Instálalos manualmente y vuelve a comprobar."
        )
    }

    func testTCCPermissionErrorsExplainTheSafeSystemSettingsAction() {
        XCTAssertEqual(
            HumanErrorCopy.message(for: "PHOTOS_AUTOMATION_DENIED"),
            "Falta el permiso de Automatización para que PhotosIndexerWorker envíe Apple Events a Fotos. Es independiente del permiso de Fotos. En Configuración del Sistema > Privacidad y seguridad > Automatización, permite que Fotos sea controlado por PhotosIndexerWorker; después vuelve aquí y ejecuta un dry-run nuevo."
        )
        XCTAssertEqual(
            HumanErrorCopy.message(for: "PHOTOS_ACCESS_DENIED"),
            "Falta el permiso de Fotos para PhotoKit en la app firmada Photos Local Keyword Indexer, concretamente en su helper PhotosIndexerWorker. Es independiente del permiso de Automatización. En Configuración del Sistema > Privacidad y seguridad > Fotos, activa esa entrada; PhotoKit se ejecuta desde ese helper. Después vuelve aquí y ejecuta un dry-run nuevo."
        )
    }

    func testPhotosAccessActionRemainsVisibleWhenPreparationReportsHelperDenial() {
        XCTAssertTrue(
            PhotosAccessActionCopy.shouldShowAccessAction(
                permission: .authorized,
                preparation: .actionRequired
            )
        )
        XCTAssertFalse(
            PhotosAccessActionCopy.shouldShowAccessAction(
                permission: .authorized,
                preparation: .ready
            )
        )
    }

    func testPhotosAccessAuxiliaryActionIsHiddenWhenItDuplicatesThePrimaryAction() {
        XCTAssertFalse(
            PhotosAccessActionCopy.shouldShowAuxiliaryAction(
                permission: .authorized,
                preparation: .actionRequired,
                primaryAction: "grant_photos_access"
            )
        )
        XCTAssertFalse(
            PhotosAccessActionCopy.shouldShowAuxiliaryAction(
                permission: .notDetermined,
                preparation: .pending,
                primaryAction: "grant_photos_access"
            )
        )
    }

    func testPhotosAccessResolutionRequestsBeforeOpeningSettings() {
        XCTAssertEqual(
            PhotosAccessActionCopy.resolution(for: .notDetermined),
            .requestAuthorization
        )
        XCTAssertEqual(
            PhotosAccessActionCopy.resolution(for: .denied),
            .openSettings
        )
        XCTAssertEqual(
            PhotosAccessActionCopy.resolution(for: .restricted),
            .openSettings
        )
        XCTAssertEqual(
            PhotosAccessActionCopy.resolution(for: .authorized),
            .none
        )
        XCTAssertEqual(
            PhotosAccessActionCopy.resolution(for: .limited),
            .none
        )
    }

    func testPhotoMutationGateBlocksApplyAfterPhotosAccessDenial() {
        var state = PreparationState()
        state.applyPermissionErrors(["PHOTOS_ACCESS_DENIED"])

        XCTAssertTrue(PhotosMutationAccess.isBlocked(state))
        XCTAssertTrue(
            PhotosMutationAccess.blockingMessage(for: state)?.contains("Preparación de Fotos") == true
        )
        XCTAssertFalse(PhotosMutationAccess.isBlocked(PreparationState()))
        XCTAssertNil(PhotosMutationAccess.blockingMessage(for: PreparationState()))
    }

    func testPhotoMutationGateNamesAutomationAfterAppleEventsDenial() {
        var state = PreparationState()
        state.applyPermissionErrors(["PHOTOS_AUTOMATION_DENIED"])

        XCTAssertTrue(PhotosMutationAccess.isBlocked(state))
        XCTAssertEqual(
            PhotosMutationAccess.blockingMessage(for: state),
            AutomationTCCGuide.packagedHelper.errorMessage
        )
    }

    func testPhotoMutationGateKeepsLocalReviewAvailableAfterAutomationDenial() {
        var state = PreparationState()
        state.applyPermissionErrors(["PHOTOS_AUTOMATION_DENIED"])

        XCTAssertNil(
            PhotosMutationAccess.blockingMessage(
                for: state,
                operation: .apply,
                localReviewOnly: true
            )
        )
        XCTAssertNotNil(
            PhotosMutationAccess.blockingMessage(
                for: state,
                operation: .apply,
                localReviewOnly: false
            )
        )
    }

    func testPhotoMutationGateNamesPhotoScriptCompatibilityFailure() {
        var state = PreparationState()
        state.updatePhotos(.authorized)
        state.beginPreflight(models: ["qwen3-vl:4b"])
        state.completePreflight(
            exitCode: 2,
            installedModels: ["qwen3-vl:4b"],
            warningCodes: [],
            errorCodes: ["PHOTOSCRIPT_UNAVAILABLE"],
            nextAction: "fix_fatal_error",
            safeInstruction: nil
        )

        XCTAssertTrue(PhotosMutationAccess.isBlocked(state))
        XCTAssertEqual(
            PhotosMutationAccess.blockingMessage(for: state),
            HumanErrorCopy.message(for: "PHOTOSCRIPT_UNAVAILABLE")
        )
    }

    func testAbruptPhotoScriptPreflightFailureStillBlocksMutationAndRequiresRepair() {
        var state = PreparationState()
        state.updatePhotos(.authorized)

        state.failPreflight(code: "PHOTOSCRIPT_UNAVAILABLE")

        XCTAssertEqual(state.automation, .failed)
        XCTAssertEqual(state.nextAction, "fix_fatal_error")
        XCTAssertTrue(PhotosMutationAccess.isBlocked(state))
        XCTAssertEqual(
            PhotosMutationAccess.blockingMessage(for: state),
            HumanErrorCopy.message(for: "PHOTOSCRIPT_UNAVAILABLE")
        )
    }

    func testPhotoScriptErrorsExplainPhaseSpecificSafeRecovery() {
        XCTAssertEqual(
            HumanErrorCopy.message(for: "READ_FAILED"),
            "PhotoScript no pudo leer una foto; comprueba el acceso de Fotos y ejecuta un dry-run nuevo."
        )
        XCTAssertEqual(
            HumanErrorCopy.message(for: "EXPORT_FAILED"),
            "PhotoScript no pudo exportar una copia temporal; comprueba el acceso de Fotos y el almacenamiento local, y ejecuta un dry-run nuevo."
        )
        XCTAssertEqual(
            HumanErrorCopy.message(for: "APPLY_FAILED"),
            "PhotoScript no pudo verificar la escritura; revisa Fotos manualmente y no reintentes automáticamente."
        )
        XCTAssertEqual(
            HumanErrorCopy.message(for: "ROLLBACK_FAILED"),
            "PhotoScript no pudo verificar la reversión; revisa Fotos manualmente y no reintentes automáticamente."
        )
    }

    func testPartialPhotoAvailabilityHasSpecificSafeCopy() {
        let message = HumanErrorCopy.message(for: "FEWER_PHOTOS_AVAILABLE")

        XCTAssertEqual(
            message,
            "Hay menos fotos elegibles de las solicitadas; revisa únicamente las fotos disponibles."
        )
        XCTAssertFalse(message.contains("revisión manual"))
    }

    func testPermissionErrorsBecomeSpecificBlockingActions() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:4b"])
        preparation.completePreflight(
            exitCode: 2,
            installedModels: ["qwen3-vl:4b"],
            warningCodes: [],
            errorCodes: ["PHOTOS_AUTOMATION_DENIED"],
            nextAction: "retry_preflight",
            safeInstruction: nil
        )

        XCTAssertEqual(preparation.automation, .actionRequired)
        XCTAssertFalse(preparation.isReady)
        XCTAssertEqual(preparation.nextAction, "grant_photos_automation")
        XCTAssertEqual(preparation.nextActionText, "Permitir controlar Fotos")
        let summary = PreparationActionSummary(state: preparation)
        XCTAssertEqual(summary.title, "Falta Automatización de Fotos")
        XCTAssertEqual(summary.buttonTitle, "Abrir configuración de Automatización")
        XCTAssertTrue(summary.detail.contains("Privacidad y seguridad > Automatización"))
    }

    func testPhotosDeniedPermissionHasPriorityOverAutomationWhenBothAreMissing() {
        var preparation = PreparationState()
        preparation.applyPermissionErrors(["PHOTOS_AUTOMATION_DENIED", "PHOTOS_ACCESS_DENIED"])

        XCTAssertEqual(preparation.photos, .actionRequired)
        XCTAssertEqual(preparation.automation, .actionRequired)
        XCTAssertEqual(preparation.nextAction, "grant_photos_access")
        XCTAssertTrue(preparation.statusMessage.contains("Falta el permiso de Fotos"))
        XCTAssertTrue(preparation.statusMessage.contains("Automatización"))
    }

    func testGrantingPhotosAfterBothPermissionFailuresSurfacesAutomationNext() {
        var preparation = makeBaseReadyPreparation()
        preparation.applyPermissionErrors(["PHOTOS_AUTOMATION_DENIED", "PHOTOS_ACCESS_DENIED"])

        preparation.updatePhotos(.authorized)

        XCTAssertEqual(preparation.photos, .ready)
        XCTAssertEqual(preparation.automation, .actionRequired)
        XCTAssertEqual(preparation.nextAction, "grant_photos_automation")
        XCTAssertEqual(
            PreparationActionSummary(state: preparation).title,
            "Falta Automatización de Fotos"
        )
    }

    func testSettingsRoutesStayLocalAndPointToTheRelevantTCCPane() {
        XCTAssertEqual(
            PermissionSettingsRoute.photos.url.absoluteString,
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Photos"
        )
        XCTAssertEqual(
            PermissionSettingsRoute.automation.url.absoluteString,
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Automation"
        )
    }

    func testStatusMessageUsesHumanSafeErrorCopy() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:8b"])
        preparation.completePreflight(
            exitCode: 2,
            installedModels: [],
            warningCodes: [],
            errorCodes: ["OLLAMA_MODEL_MISSING"],
            nextAction: "install_missing_model",
            safeInstruction: nil
        )

        XCTAssertTrue(preparation.statusMessage.contains("Falta un modelo local"))
        XCTAssertFalse(preparation.statusMessage.contains("OLLAMA_MODEL_MISSING"))
    }

    func testReadyNextActionTextPointsToTheDryRun() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:4b"])
        preparation.completePreflight(
            exitCode: 0,
            installedModels: ["qwen3-vl:4b"],
            warningCodes: [],
            errorCodes: [],
            nextAction: "none",
            safeInstruction: nil
        )

        XCTAssertEqual(preparation.nextActionText, "Iniciar una nueva ejecución")
    }

    func testPreflightFailureCanBeRetriedAfterHelperError() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:4b"])
        preparation.failPreflight(code: "HELPER_UNAVAILABLE")

        XCTAssertFalse(preparation.isReady)
        XCTAssertTrue(preparation.requiresAttention)
        XCTAssertEqual(preparation.errorCodes, ["HELPER_UNAVAILABLE"])
        XCTAssertEqual(preparation.nextAction, "retry_preflight")
        XCTAssertNotEqual(preparation.ollama, .checking)
        XCTAssertNotEqual(preparation.models, .checking)
    }

    func testPhotoScriptAndHelperFailuresOfferSpecificSafePreparationActions() {
        var photoScriptPreparation = PreparationState()
        photoScriptPreparation.updatePhotos(.authorized)
        photoScriptPreparation.failPreflight(code: "PHOTOSCRIPT_UNAVAILABLE")
        let photoScriptSummary = PreparationActionSummary(state: photoScriptPreparation)

        XCTAssertEqual(photoScriptSummary.title, "PhotoScript no está disponible")
        XCTAssertEqual(photoScriptSummary.symbolName, "applescript")
        XCTAssertTrue(photoScriptSummary.detail.contains("puente AppleScript"))
        XCTAssertTrue(photoScriptSummary.buttonAccessibilityHint.contains("compatibilidad"))

        var helperPreparation = PreparationState()
        helperPreparation.updatePhotos(.authorized)
        helperPreparation.failPreflight(code: "HELPER_UNAVAILABLE")
        let helperSummary = PreparationActionSummary(state: helperPreparation)

        XCTAssertEqual(helperSummary.title, "Helper local no disponible")
        XCTAssertEqual(helperSummary.symbolName, "shippingbox")
        XCTAssertTrue(helperSummary.detail.contains("helper firmado"))
        XCTAssertFalse(helperSummary.accessibilityLabel.contains("/"))
    }

    func testUnsafeHelperResultsExplainSafeRecovery() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.failPreflight(code: "UNSAFE_PREFLIGHT_RESULT")

        let summary = PreparationActionSummary(state: preparation)

        XCTAssertEqual(summary.title, "Helper local no compatible")
        XCTAssertTrue(summary.detail.contains("respuesta incompatible"))
        XCTAssertTrue(summary.buttonAccessibilityHint.contains("build válida"))
        XCTAssertEqual(
            HumanErrorCopy.message(for: "UNSAFE_WORKFLOW_RESULT"),
            "El helper local devolvió una respuesta incompatible; reinstala la app o usa una build válida y vuelve a comprobar."
        )
    }

    func testLocalModelFailuresOfferSpecificPreparationRecoveryCopy() {
        let cases: [(String, String, String)] = [
            (
                "OLLAMA_UNAVAILABLE",
                "Ollama no está disponible",
                "Inicia el servicio local"
            ),
            (
                "OLLAMA_VERSION_OLD",
                "Actualiza Ollama",
                "versión local"
            ),
            (
                "OLLAMA_NO_VISION",
                "El modelo no admite visión",
                "capacidad de visión"
            ),
            (
                "MODEL_INVALID",
                "Modelo local no válido",
                "configuración de modelos"
            ),
            (
                "OLLAMA_PREFLIGHT_FAILED",
                "No se pudo comprobar Ollama",
                "servicio local"
            )
        ]

        for (code, title, recovery) in cases {
            var preparation = PreparationState()
            preparation.updatePhotos(.authorized)
            preparation.failPreflight(code: code)

            let summary = PreparationActionSummary(state: preparation)

            XCTAssertEqual(summary.title, title, code)
            XCTAssertTrue(summary.detail.contains(recovery), code)
            XCTAssertTrue(summary.buttonAccessibilityHint.contains("sin abrir ni modificar Fotos"), code)
            XCTAssertFalse(summary.accessibilityLabel.contains(code), code)
        }
    }

    func testHelperProtocolAndLocalStorageFailuresOfferSpecificSafePreparationActions() {
        let cases: [(String, String, String)] = [
            (
                "INVALID_HELPER_EVENT",
                "Protocolo local no compatible",
                "Reinstala la app o usa una build válida"
            ),
            (
                "RUNS_ROOT_INVALID",
                "Almacenamiento local no disponible",
                "ubicación predeterminada"
            )
        ]

        for (code, title, recovery) in cases {
            var preparation = PreparationState()
            preparation.updatePhotos(.authorized)
            preparation.failPreflight(code: code)
            let summary = PreparationActionSummary(state: preparation)

            XCTAssertEqual(summary.title, title, code)
            XCTAssertEqual(summary.buttonTitle, "Comprobar de nuevo", code)
            XCTAssertTrue(summary.detail.contains(recovery), code)
            XCTAssertTrue(summary.buttonAccessibilityHint.contains("sin abrir ni modificar Fotos"), code)
            XCTAssertFalse(summary.accessibilityLabel.contains(code), code)
            XCTAssertFalse(summary.accessibilityLabel.contains("/"), code)
        }
    }

    func testPendingPreparationIsNotPresentedAsAnError() {
        XCTAssertFalse(PreparationState().requiresAttention)
    }

    func testOllamaPreflightFailureExplainsTheLocalRecoveryPath() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:4b"])
        preparation.completePreflight(
            exitCode: 2,
            installedModels: [],
            warningCodes: [],
            errorCodes: ["OLLAMA_PREFLIGHT_FAILED"],
            nextAction: "fix_fatal_error",
            safeInstruction: nil
        )

        XCTAssertTrue(preparation.statusMessage.contains("No se pudo comprobar Ollama o los modelos locales"))
        XCTAssertFalse(preparation.statusMessage.contains("OLLAMA_PREFLIGHT_FAILED"))
    }

    func testCancelledPreflightOffersARecheckInsteadOfManualReview() {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:4b"])
        preparation.completePreflight(
            exitCode: 1,
            installedModels: [],
            warningCodes: [],
            errorCodes: ["CANCELLED"],
            nextAction: "none",
            safeInstruction: nil
        )

        XCTAssertEqual(preparation.nextAction, "retry_preflight")
        XCTAssertEqual(preparation.nextActionText, "Reintentar la comprobación local")
        XCTAssertTrue(preparation.statusMessage.contains("La comprobación local se canceló"))
    }

    func testAppleMapsOptInProducesExplicitCoordinateDisclosure() {
        let notice = ScanPrivacyNotice(appleMapsEnabled: true)

        XCTAssertTrue(notice.isVisible)
        XCTAssertEqual(
            notice.text,
            "Apple Maps recibirá las coordenadas geográficas de las fotos con ubicación durante esta ejecución. Las imágenes no se enviarán."
        )
    }

    func testAppleMapsPrivacyCopyKeepsIconAndStateSemanticsAligned() {
        XCTAssertEqual(AppleMapsPrivacyCopy.symbolName(enabled: true), "location.fill")
        XCTAssertTrue(AppleMapsPrivacyCopy.text(enabled: true).contains("recibirá"))
        XCTAssertEqual(AppleMapsPrivacyCopy.symbolName(enabled: false), "location.slash")
        XCTAssertTrue(AppleMapsPrivacyCopy.text(enabled: false).contains("no recibirá"))
    }

    func testScanOptionAccessibilityHintsRepeatTheDataAndApprovalBoundaries() {
        XCTAssertEqual(
            ScanPrivacyNotice(appleMapsEnabled: true).accessibilityHint,
            "Apple Maps recibirá las coordenadas geográficas de las fotos con ubicación durante esta ejecución. Las imágenes no se enviarán."
        )
        XCTAssertEqual(
            ScanPrivacyNotice(appleMapsEnabled: false).accessibilityHint,
            "Apple Maps está desactivado; no recibirá coordenadas en esta ejecución."
        )
        XCTAssertEqual(ScanCaptionCopy.accessibilityHint, ScanCaptionCopy.detail)
    }

    func testScanActionAccessibilityExplainsTheFirstBlockingCondition() {
        XCTAssertEqual(
            ScanActionCopy.accessibilityHint(
                preparationReady: false,
                modelConfigurationValid: true,
                missingModelCount: 0,
                workerIsRunning: false
            ),
            "Completa Preparación antes de iniciar el dry-run."
        )
        XCTAssertEqual(
            ScanActionCopy.accessibilityHint(
                preparationReady: true,
                modelConfigurationValid: false,
                missingModelCount: 0,
                workerIsRunning: false
            ),
            "Configura un modelo local válido antes de iniciar el dry-run."
        )
        XCTAssertEqual(
            ScanActionCopy.accessibilityHint(
                preparationReady: true,
                modelConfigurationValid: true,
                missingModelCount: 1,
                workerIsRunning: false
            ),
            "Vuelve a Preparación para comprobar los modelos antes de iniciar el dry-run."
        )
    }

    func testScanActionAccessibilityHintExplainsAStaleModelPreflight() {
        XCTAssertEqual(
            ScanActionCopy.accessibilityHint(
                preparationReady: true,
                modelConfigurationValid: true,
                missingModelCount: 0,
                workerIsRunning: false,
                preflightIsStale: true
            ),
            "La configuración de modelos cambió; vuelve a comprobar la preparación local antes de iniciar el dry-run."
        )
    }

    func testCaptionOptionCopyMakesReviewAndNoReplacementExplicit() {
        XCTAssertEqual(ScanCaptionCopy.toggleLabel, "Proponer caption breve para revisión")
        XCTAssertEqual(
            ScanCaptionCopy.detail,
            "Cada caption se revisa por separado y solo se escribe tras confirmar; nunca reemplaza un caption existente."
        )
    }

    func testScanModelConfigurationExplainsQualityAndBlocksEmptyModels() {
        let adaptive = ScanModelConfiguration(
            policy: "adaptive",
            singleModel: "qwen3-vl:4b",
            fastModel: "qwen3-vl:4b",
            detailedModel: "qwen3-vl:8b"
        )
        XCTAssertTrue(adaptive.isValid)
        XCTAssertTrue(adaptive.qualityText.contains("sin GPS"))
        XCTAssertTrue(adaptive.qualityText.contains("cuando hay ubicación"))

        let incomplete = ScanModelConfiguration(
            policy: "adaptive",
            singleModel: "qwen3-vl:4b",
            fastModel: "   ",
            detailedModel: "qwen3-vl:8b"
        )
        XCTAssertFalse(incomplete.isValid)
        XCTAssertEqual(
            incomplete.validationMessage,
            "Configura un modelo local válido antes de iniciar el dry-run."
        )
    }

    func testScanModelConfigurationPickerLabelMatchesRequiredAdaptiveModels() {
        let adaptive = ScanModelConfiguration(
            policy: "adaptive",
            singleModel: "qwen3-vl:4b",
            fastModel: "qwen3-vl:4b",
            detailedModel: "qwen3-vl:8b"
        )
        let single = ScanModelConfiguration(
            policy: "single",
            singleModel: "qwen3-vl:4b",
            fastModel: "qwen3-vl:4b",
            detailedModel: "qwen3-vl:8b"
        )

        XCTAssertEqual(adaptive.policyPickerLabel, "Adaptativa: rápido y detallado (ambos obligatorios)")
        XCTAssertEqual(single.policyPickerLabel, "Un solo modelo")
        XCTAssertFalse(adaptive.policyPickerLabel.contains("opcional"))
    }

    func testScanModelConfigurationRejectsInvalidOrCloudModelNamesBeforeDryRun() {
        let invalid = ScanModelConfiguration(
            policy: "single",
            singleModel: "vision model",
            fastModel: "qwen3-vl:4b",
            detailedModel: "qwen3-vl:8b"
        )
        let cloud = ScanModelConfiguration(
            policy: "adaptive",
            singleModel: "qwen3-vl:4b",
            fastModel: "qwen3-vl:4b",
            detailedModel: "vision:cloud"
        )

        XCTAssertFalse(invalid.isValid)
        XCTAssertFalse(cloud.isValid)
        XCTAssertEqual(
            invalid.validationMessage,
            "Usa un nombre de modelo Ollama local válido; los modelos cloud no están permitidos."
        )
        XCTAssertEqual(cloud.validationMessage, invalid.validationMessage)
    }

    func testMissingModelsDoesNotSuggestCheckingAnInvalidModelName() {
        let configuration = ScanModelConfiguration(
            policy: "single",
            singleModel: "vision:cloud",
            fastModel: "qwen3-vl:4b",
            detailedModel: "qwen3-vl:8b"
        )

        XCTAssertTrue(configuration.missingModels(from: []).isEmpty)
    }

    func testAdaptiveConfigurationWithOneModelStatesThatGPSPhotosUseTheSameModel() {
        let configuration = ScanModelConfiguration(
            policy: "adaptive",
            singleModel: "qwen3-vl:4b",
            fastModel: "qwen3-vl:4b",
            detailedModel: "qwen3-vl:4b"
        )

        XCTAssertTrue(configuration.qualityText.contains("se usará en todas las fotos"))
        XCTAssertTrue(configuration.qualityText.contains("segunda etapa"))
        XCTAssertEqual(configuration.configuredModelsText, "Modelo adaptativo único: qwen3-vl:4b")
    }

    func testMissingModelsDeduplicatesAdaptiveRolesUsingTheSameModel() {
        let configuration = ScanModelConfiguration(
            policy: "adaptive",
            singleModel: "qwen3-vl:4b",
            fastModel: "qwen3-vl:4b",
            detailedModel: "qwen3-vl:4b"
        )

        XCTAssertEqual(configuration.missingModels(from: []), ["qwen3-vl:4b"])
    }

    func testScanModelConfigurationSummarizesTheSelectedPolicyModels() {
        let single = ScanModelConfiguration(
            policy: "single",
            singleModel: "qwen3-vl:8b",
            fastModel: "qwen3-vl:4b",
            detailedModel: "qwen3-vl:8b"
        )
        XCTAssertEqual(single.configuredModelsText, "Modelo único: qwen3-vl:8b")

        let adaptive = ScanModelConfiguration(
            policy: "adaptive",
            singleModel: "qwen3-vl:4b",
            fastModel: "qwen3-vl:4b",
            detailedModel: "qwen3-vl:8b"
        )
        XCTAssertEqual(
            adaptive.configuredModelsText,
            "Modelos adaptativos: rápido qwen3-vl:4b · detallado qwen3-vl:8b"
        )
    }

    func testModelPresentationBoundsLongNamesWithoutChangingTheConfiguredValue() {
        let longName = "vision/" + String(repeating: "x", count: 120) + ":latest"

        let displayed = ModelPresentationCopy.display("  \(longName)  ")

        XCTAssertEqual(displayed.count, ModelPresentationCopy.maximumLength)
        XCTAssertTrue(displayed.hasPrefix("vision/"))
        XCTAssertTrue(displayed.hasSuffix(":latest"))
        XCTAssertEqual(ModelPresentationCopy.display("  qwen3-vl:4b  "), "qwen3-vl:4b")
        XCTAssertEqual(longName.count, 134)
    }

    func testChangedModelPolicyRequiresPreflightForUninstalledModels() {
        let configuration = ScanModelConfiguration(
            policy: "single",
            singleModel: "qwen3-vl:8b",
            fastModel: "qwen3-vl:4b",
            detailedModel: "qwen3-vl:8b"
        )

        XCTAssertEqual(configuration.missingModels(from: ["qwen3-vl:4b"]), ["qwen3-vl:8b"])
        XCTAssertTrue(configuration.missingModels(from: ["qwen3-vl:8b"]).isEmpty)
    }

    func testScanCompletionOpensReviewOnlyForNewScanRuns() {
        XCTAssertTrue(AppNavigationState.shouldAutoOpenPreview(from: .scan, runID: "run-1", lastOpenedRunID: nil))
        XCTAssertFalse(AppNavigationState.shouldAutoOpenPreview(from: .scan, runID: "run-1", lastOpenedRunID: "run-1"))
        XCTAssertFalse(AppNavigationState.shouldAutoOpenPreview(from: .history, runID: "run-2", lastOpenedRunID: "run-1"))
        XCTAssertFalse(AppNavigationState.shouldAutoOpenPreview(from: .scan, runID: "", lastOpenedRunID: nil))
    }

    func testScanOutcomeCopyDoesNotInviteApplyAfterInterruptionOrFailure() {
        let interrupted = ScanOutcomeCopy(state: .interrupted)
        XCTAssertEqual(
            interrupted.text,
            "El dry-run se interrumpió; abre Historial para revisar el run. No apliques cambios hasta completar una revisión manual."
        )
        XCTAssertEqual(interrupted.symbolName, "pause.circle.fill")

        let failed = ScanOutcomeCopy(state: .failed(code: "HELPER_UNAVAILABLE"))
        XCTAssertEqual(
            failed.text,
            "El dry-run falló; abre Historial y ejecuta un dry-run nuevo. No hay una aplicación segura disponible."
        )
        XCTAssertEqual(failed.symbolName, "xmark.octagon.fill")
        XCTAssertFalse(failed.accessibilityLabel.contains("aplicar"))
    }

    func testScanOutcomeCopyUsesTerminalEventWhenWorkerAlreadyReturnedToReady() throws {
        let events = try [
            #"{"id":"scan-1","event":"photo_progress","uuid":"A1B2C3D4","state":"ready"}"#,
            #"{"id":"scan-1","event":"completed","exit_code":1,"warning_codes":["ANALYSIS_FAILED"]}"#,
        ].map { try JSONDecoder().decode(WorkerEvent.self, from: Data($0.utf8)) }
        let progress = ScanProgressSummary(events: events, requestedLimit: 20)

        let outcome = ScanOutcomeCopy(state: .ready, terminalOutcome: progress.terminalOutcome)

        XCTAssertEqual(
            outcome.text,
            "El dry-run terminó con errores o advertencias; abre Historial y revisa el run antes de aplicar."
        )
        XCTAssertEqual(outcome.symbolName, "exclamationmark.triangle.fill")
        XCTAssertFalse(outcome.accessibilityLabel.contains("confirma"))
    }

    func testScanOutcomeCopyNamesCancellationRecoveryFromTerminalEvent() throws {
        let events = try [
            #"{"id":"scan-1","event":"photo_progress","uuid":"A1B2C3D4","state":"cancelled"}"#,
            #"{"id":"scan-1","event":"completed","exit_code":1,"error_codes":["CANCELLED"]}"#,
        ].map { try JSONDecoder().decode(WorkerEvent.self, from: Data($0.utf8)) }
        let progress = ScanProgressSummary(events: events, requestedLimit: 20)

        let outcome = ScanOutcomeCopy(state: .ready, terminalOutcome: progress.terminalOutcome)

        XCTAssertEqual(
            outcome.text,
            "El dry-run se canceló; abre Historial y ejecuta un dry-run nuevo antes de aplicar."
        )
        XCTAssertEqual(outcome.symbolName, "pause.circle.fill")
        XCTAssertFalse(outcome.accessibilityLabel.contains("confirmar"))
    }

    func testScanProgressSummarizesPhotoResultsWithoutCountingLifecycleEvents() throws {
        let events = try [
            #"{"id":"scan-1","event":"started","operation":"scan"}"#,
            #"{"id":"scan-1","event":"photo_progress","uuid":"A1B2C3D4","state":"ready","model_used":"qwen3-vl:4b","keywords_count":2}"#,
            #"{"id":"scan-1","event":"photo_progress","uuid":"E5F6A7B8","state":"analysis_failed","model_used":"qwen3-vl:4b","keywords_count":0}"#,
            #"{"id":"scan-1","event":"completed","exit_code":1}"#,
        ].map { try JSONDecoder().decode(WorkerEvent.self, from: Data($0.utf8)) }

        let summary = ScanProgressSummary(events: events, requestedLimit: 20)

        XCTAssertEqual(summary.processedCount, 2)
        XCTAssertEqual(summary.successfulCount, 1)
        XCTAssertEqual(summary.failedCount, 1)
        XCTAssertEqual(summary.cancelledCount, 0)
        XCTAssertEqual(summary.currentUUID, "E5F6A7B8")
        XCTAssertEqual(summary.currentModel, "qwen3-vl:4b")
        XCTAssertEqual(summary.statusText, "Dry-run terminó con advertencias o errores. Revisa el run antes de aplicar.")
        XCTAssertEqual(
            summary.accessibilitySummary,
            "2 de hasta 20 fotos procesadas. 1 completada, 1 con error, 0 canceladas."
        )
        XCTAssertEqual(summary.fraction, 1, accuracy: 0.0001)
    }

    func testScanProgressBoundsTheDisplayedCurrentModelWithoutChangingTheRawValue() throws {
        let longModel = "vision/" + String(repeating: "x", count: 80) + ":latest"
        let events = try [
            #"{"id":"scan-1","event":"photo_progress","uuid":"A1B2C3D4","state":"ready","model_used":"\#(longModel)"}"#,
        ].map { try JSONDecoder().decode(WorkerEvent.self, from: Data($0.utf8)) }

        let summary = ScanProgressSummary(events: events, requestedLimit: 1)

        XCTAssertEqual(summary.currentModel, longModel)
        XCTAssertEqual(summary.currentModelDisplayText, ModelPresentationCopy.display(longModel))
        XCTAssertLessThanOrEqual(summary.currentModelDisplayText?.count ?? 0, ModelPresentationCopy.maximumLength)
    }

    func testScanProgressSeparatesSuccessfulAndCancelledPhotos() throws {
        let events = try [
            #"{"id":"scan-1","event":"photo_progress","uuid":"A1B2C3D4","state":"ready"}"#,
            #"{"id":"scan-1","event":"photo_progress","uuid":"E5F6A7B8","state":"cancelled"}"#,
            #"{"id":"scan-1","event":"photo_progress","uuid":"C9D0E1F2","state":"uncertain"}"#,
        ].map { try JSONDecoder().decode(WorkerEvent.self, from: Data($0.utf8)) }

        let summary = ScanProgressSummary(events: events, requestedLimit: 3)

        XCTAssertEqual(summary.successfulCount, 1)
        XCTAssertEqual(summary.failedCount, 0)
        XCTAssertEqual(summary.uncertainCount, 1)
        XCTAssertEqual(summary.cancelledCount, 1)
        XCTAssertEqual(
            summary.accessibilitySummary,
            "3 de hasta 3 fotos procesadas. 1 completada, 0 con errores, 1 requiere revisión manual, 1 cancelada."
        )
        XCTAssertEqual(
            summary.statusText,
            "3 fotos procesadas de hasta 3 · 1 requiere revisión manual · 1 cancelada"
        )
    }

    func testScanProgressDoesNotClaimCompletionBeforeTheFirstPhoto() {
        let summary = ScanProgressSummary(events: [], requestedLimit: 20)

        XCTAssertEqual(summary.processedCount, 0)
        XCTAssertEqual(summary.statusText, "Esperando la primera foto…")
        XCTAssertEqual(summary.fraction, 0)
    }

    func testScanProgressSurfacesWorkerFailureWhenNoTerminalEventWasEmitted() {
        let summary = ScanProgressSummary(
            events: [],
            requestedLimit: 20,
            workerState: .failed(code: "HELPER_UNAVAILABLE")
        )

        XCTAssertTrue(summary.isTerminal)
        XCTAssertEqual(summary.fraction, 1)
        XCTAssertEqual(
            summary.statusText,
            "No se encontró el helper local firmado. Reinstala la app o usa una build válida y vuelve a comprobar."
        )
        XCTAssertTrue(summary.accessibilityLabel.contains("helper local firmado"))
        XCTAssertFalse(summary.accessibilityLabel.contains("Esperando"))
    }

    func testScanProgressUsesSingularCopyForOnePhotoWhileRunning() throws {
        let events = try [
            #"{"id":"scan-1","event":"photo_progress","uuid":"A1B2C3D4","state":"ready"}"#
        ].map { try JSONDecoder().decode(WorkerEvent.self, from: Data($0.utf8)) }

        let summary = ScanProgressSummary(events: events, requestedLimit: 20)

        XCTAssertEqual(summary.statusText, "1 foto procesada de hasta 20")
    }

    func testScanProgressClosesAtOneWhenFewerEligiblePhotosWereFound() throws {
        let events = try [
            #"{"id":"scan-1","event":"photo_progress","uuid":"A1B2C3D4","state":"ready","keywords_count":1}"#,
            #"{"id":"scan-1","event":"completed","exit_code":0,"next_action":"review_then_apply"}"#,
        ].map { try JSONDecoder().decode(WorkerEvent.self, from: Data($0.utf8)) }

        let summary = ScanProgressSummary(events: events, requestedLimit: 20)

        XCTAssertTrue(summary.isTerminal)
        XCTAssertEqual(summary.fraction, 1)
        XCTAssertEqual(summary.statusText, "Dry-run completado: 1 foto procesada de hasta 20.")
        XCTAssertFalse(summary.accessibilityLabel.contains("revisa las propuestas"))
    }

    func testScanProgressExplainsAnInterruptedHelperWithoutExposingDetails() throws {
        let events = try [
            #"{"id":"scan-1","event":"photo_progress","uuid":"A1B2C3D4","state":"ready","keywords_count":1}"#,
            #"{"id":"scan-1","event":"error","code":"HELPER_UNAVAILABLE"}"#,
        ].map { try JSONDecoder().decode(WorkerEvent.self, from: Data($0.utf8)) }

        let summary = ScanProgressSummary(events: events, requestedLimit: 20)

        XCTAssertTrue(summary.isTerminal)
        XCTAssertEqual(summary.fraction, 1)
        XCTAssertEqual(summary.statusText, "El dry-run no pudo completarse. Revisa Preparación y vuelve a intentarlo.")
        XCTAssertFalse(summary.statusText.contains("HELPER_UNAVAILABLE"))
    }

    func testScanProgressTerminalCancellationIsReflectedInStatusAndVoiceOver() throws {
        let events = try [
            #"{"id":"scan-1","event":"photo_progress","uuid":"A1B2C3D4","state":"cancelled"}"#,
            #"{"id":"scan-1","event":"completed","exit_code":1,"error_codes":["CANCELLED"]}"#,
        ].map { try JSONDecoder().decode(WorkerEvent.self, from: Data($0.utf8)) }

        let summary = ScanProgressSummary(events: events, requestedLimit: 20)

        XCTAssertEqual(summary.statusText, "Dry-run cancelado; ejecuta un dry-run nuevo antes de aplicar.")
        XCTAssertTrue(summary.accessibilityLabel.contains("El dry-run se canceló"))
        XCTAssertTrue(summary.accessibilityLabel.contains("1 cancelada"))
    }

    func testScanOutcomeCopyDistinguishesCompletedRunWithNoEligiblePhotos() throws {
        let events = try [
            #"{"id":"scan-1","event":"completed","exit_code":0}"#,
        ].map { try JSONDecoder().decode(WorkerEvent.self, from: Data($0.utf8)) }
        let progress = ScanProgressSummary(events: events, requestedLimit: 20)

        let outcome = ScanOutcomeCopy(
            state: .ready,
            terminalOutcome: progress.terminalOutcome,
            processedCount: progress.processedCount
        )

        XCTAssertEqual(
            outcome.text,
            "Dry-run completado; no hay fotos elegibles para revisar o aplicar. Conserva el resultado y revisa el alcance o el acceso a Fotos antes de ejecutar otro dry-run."
        )
        XCTAssertFalse(outcome.accessibilityLabel.contains("propuestas antes de aplicar"))
    }

    func testScanOutcomeCopyGivesSafeNextStepWhenNoPhotosAreEligible() {
        let outcome = ScanOutcomeCopy(
            state: .ready,
            terminalOutcome: .completed,
            processedCount: 0
        )

        XCTAssertEqual(
            outcome.text,
            "Dry-run completado; no hay fotos elegibles para revisar o aplicar. Conserva el resultado y revisa el alcance o el acceso a Fotos antes de ejecutar otro dry-run."
        )
        XCTAssertTrue(outcome.accessibilityLabel.contains("revisa el alcance o el acceso a Fotos"))
        XCTAssertFalse(outcome.accessibilityLabel.contains("aplica cambios"))
    }

    func testScanProgressAccessibilityMatchesNoEligiblePhotosNextStep() throws {
        let events = try [
            #"{"id":"scan-1","event":"completed","exit_code":0}"#,
        ].map { try JSONDecoder().decode(WorkerEvent.self, from: Data($0.utf8)) }
        let summary = ScanProgressSummary(events: events, requestedLimit: 20)

        XCTAssertTrue(summary.accessibilityLabel.contains("revisa el alcance o el acceso a Fotos"))
        XCTAssertFalse(summary.accessibilityLabel.contains("propuestas antes de aplicar"))
    }

    func testScanOutcomeCopyNamesCaptionOnlyProposalsLikeHistory() {
        let outcome = ScanOutcomeCopy(
            state: .ready,
            terminalOutcome: .completed,
            processedCount: 1,
            proposedKeywordCount: 0,
            proposedCaptionCount: 1
        )

        XCTAssertEqual(
            outcome.text,
            "Dry-run completado; revisa los captions propuestos antes de aplicar."
        )
        XCTAssertEqual(outcome.accessibilityLabel, outcome.text)
        XCTAssertFalse(outcome.text.contains("keywords propuestas"))
    }

    func testScanOutcomeCopyNamesCompletedRunWithoutAnyProposals() {
        let outcome = ScanOutcomeCopy(
            state: .ready,
            terminalOutcome: .completed,
            processedCount: 10,
            proposedKeywordCount: 0,
            proposedCaptionCount: 0
        )

        XCTAssertEqual(
            outcome.text,
            "Dry-run completado; no hay keywords ni captions nuevas para revisar o aplicar."
        )
        XCTAssertFalse(outcome.text.contains("revisa las propuestas"))
    }

    func testScanProgressDoesNotHideWarningsBehindSuccessfulExitCode() throws {
        let events = try [
            #"{"id":"scan-1","event":"completed","exit_code":0,"warning_codes":["PHOTOS_ACCESS_LIMITED"]}"#,
        ].map { try JSONDecoder().decode(WorkerEvent.self, from: Data($0.utf8)) }

        let summary = ScanProgressSummary(events: events, requestedLimit: 20)

        XCTAssertEqual(
            summary.statusText,
            "Dry-run terminó con advertencias o errores. Revisa el run antes de aplicar."
        )
        XCTAssertEqual(summary.terminalOutcome, .completedWithIssues)
        XCTAssertTrue(summary.accessibilityLabel.contains("errores o advertencias"))
    }

    func testScanProgressIgnoresLaterMutationEventsFromTheSharedWorkerStream() throws {
        let events = try [
            #"{"id":"scan-1","event":"started","operation":"scan"}"#,
            #"{"id":"scan-1","event":"photo_progress","uuid":"A1B2C3D4","state":"ready","model_used":"qwen3-vl:4b","keywords_count":2}"#,
            #"{"id":"scan-1","event":"completed","exit_code":0,"next_action":"review_then_apply"}"#,
            #"{"id":"apply-1","event":"started","operation":"apply"}"#,
            #"{"id":"apply-1","event":"photo_progress","uuid":"A1B2C3D4","state":"failed","keywords_count":0}"#,
            #"{"id":"apply-1","event":"completed","exit_code":1,"error_codes":["APPLY_FAILED"],"next_action":"manual_review"}"#,
        ].map { try JSONDecoder().decode(WorkerEvent.self, from: Data($0.utf8)) }

        let summary = ScanProgressSummary(events: events, requestedLimit: 20)

        XCTAssertEqual(summary.processedCount, 1)
        XCTAssertEqual(summary.successfulCount, 1)
        XCTAssertEqual(summary.failedCount, 0)
        XCTAssertEqual(summary.currentUUID, "A1B2C3D4")
        XCTAssertEqual(summary.currentModel, "qwen3-vl:4b")
        XCTAssertEqual(summary.terminalOutcome, .completed)
        XCTAssertEqual(summary.statusText, "Dry-run completado: 1 foto procesada de hasta 20.")
        XCTAssertFalse(summary.accessibilityLabel.contains("terminó con errores"))
    }

    private func makeBaseReadyPreparation() -> PreparationState {
        var preparation = PreparationState()
        preparation.updatePhotos(.authorized)
        preparation.beginPreflight(models: ["qwen3-vl:4b"])
        preparation.completePreflight(
            exitCode: 0,
            installedModels: ["qwen3-vl:4b"],
            warningCodes: [],
            errorCodes: [],
            nextAction: "none",
            safeInstruction: nil
        )
        return preparation
    }
}
