import AppKit
import ApprenticeCore
import Carbon.HIToolbox
import ServiceManagement
import UserNotifications

/// Paths under the data root. The root defaults to ~/.apprentice-mode (hidden, and outside ~/Library so Cowork can be
/// given it) and can be moved with
/// `defaults write blog.eliot.apprentice dataRoot /path` (or APPRENTICE_DATA_ROOT for one run).
struct DataLocations {
    let root: URL
    var sessions: URL { root.appendingPathComponent("sessions") }
    var configuration: URL { root.appendingPathComponent("config.json") }
    var status: URL { root.appendingPathComponent("status.json") }
    var controlSocket: URL { root.appendingPathComponent("control.sock") }
    // Same commands as the socket, for clients that can only write files (Cowork): drop a request, read the result.
    var commandRequest: URL { root.appendingPathComponent("command.json") }
    var commandResult: URL { root.appendingPathComponent("command-result.json") }
    var processLock: URL { root.appendingPathComponent("recorder.pid") }
    // The instructions "Trigger Workflow Harvesting" sends to Claude Code; the user's to edit.
    var automatePrompt: URL { root.appendingPathComponent("AUTOMATE_TASKS.md") }
    // The automation feed page; Claude writes data.js next to it.
    var feedPage: URL { root.appendingPathComponent("feed/index.html") }

    static func resolve() -> DataLocations {
        let configuredPath = ProcessInfo.processInfo.environment["APPRENTICE_DATA_ROOT"]
            ?? UserDefaults.standard.string(forKey: "dataRoot")  // the CLI shares the app bundle, so the same domain
        let defaultRoot = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".apprentice-mode")
        return DataLocations(root: configuredPath.map { URL(fileURLWithPath: ($0 as NSString).expandingTildeInPath) } ?? defaultRoot)
    }
}

struct PermissionState: Equatable {
    var screenRecording = false
    var accessibility = false
    var inputMonitoring = false
    var allGranted: Bool { screenRecording && accessibility && inputMonitoring }

    static func current() -> PermissionState {
        PermissionState(screenRecording: CGPreflightScreenCaptureAccess(), accessibility: AXIsProcessTrusted(),
                        inputMonitoring: CGPreflightListenEventAccess())
    }
}

/// Owns the recorder's state and timers on the main thread; all recording work goes to `recordingQueue`.
final class RecorderEngine {
    let locations: DataLocations
    let hostApplicationIdentifier = Bundle.main.bundleIdentifier ?? "blog.eliot.apprentice"
    let clock = SystemClock()
    let recordingQueue = DispatchQueue(label: "apprentice.recording")
    let frameSource = ScreenFrameSource()
    let sessionWriter: SessionWriter
    let storageAccountant: StorageAccountant
    let activityRecorder: ActivityRecorder
    let launchedAt = Date()

    private(set) var configuration = RecorderConfiguration.defaults
    private var configurationModifiedAt: Date?
    private(set) var permissions = PermissionState()
    private(set) var pausedUntil: Date?        // .distantFuture = until resumed
    private(set) var lowDiskPaused = false
    private(set) var storageFullPaused = false
    private(set) var screenIsLocked = false
    private(set) var freeDiskBytes: Int64 = 0
    private(set) var lastError: String?
    private var eventTap: CFMachPort?
    private var timers: [Timer] = []

    var onStateChange: () -> Void = {}

    init(locations: DataLocations) {
        self.locations = locations
        try? FileManager.default.createDirectory(at: locations.sessions, withIntermediateDirectories: true)
        sessionWriter = SessionWriter(sessionsDirectory: locations.sessions, clock: clock,
                                      hostApplicationIdentifier: hostApplicationIdentifier, displaysProvider: Self.currentDisplays)
        storageAccountant = StorageAccountant(sessionsDirectory: locations.sessions, clock: clock)
        activityRecorder = ActivityRecorder(recordingQueue: recordingQueue, sessionWriter: sessionWriter,
                                            storageAccountant: storageAccountant, frameSource: frameSource, clock: clock)
        frameSource.onError = { [weak self] message in self?.recordError(message) }
    }

    // MARK: lifecycle

    func start() {
        excludeDataRootFromTimeMachine()
        reloadConfigurationIfChanged()
        AXUIElementSetMessagingTimeout(AXElement.systemWide.reference, 0.25)
        // We hold our own grants, so asking is right: it's also what lists Apprentice in each Privacy pane.
        if !CGPreflightScreenCaptureAccess() { CGRequestScreenCaptureAccess() }
        if !AXIsProcessTrusted() { AXIsProcessTrustedWithOptions(["AXTrustedCheckOptionPrompt": true] as CFDictionary) }
        if !CGPreflightListenEventAccess() { CGRequestListenEventAccess() }
        recordingQueue.sync { storageAccountant.reconcile() }
        refreshPermissions()
        observeWorkspace()

        func every(_ interval: TimeInterval, _ action: @escaping (RecorderEngine) -> Void) {
            timers.append(Timer.scheduledTimer(withTimeInterval: interval, repeats: true) { [weak self] _ in self.map(action) })
        }
        every(1) { engine in engine.tickEverySecond() }
        every(10) { engine in engine.tickEveryTenSeconds() }
        every(30) { engine in engine.refreshPermissions() }
        every(600) { engine in engine.reconcileAndPrune() }
        tickEveryTenSeconds()
    }

    func shutdown() {
        recordingQueue.sync { activityRecorder.endSession() }
        frameSource.stop()
        writeStatusFile()
    }

    // MARK: state

    var isPaused: Bool { pausedUntil != nil }

    var isCapturing: Bool {
        permissions.allGranted && !isPaused && !lowDiskPaused && !storageFullPaused && !screenIsLocked
    }

    var stateName: String {
        if !permissions.allGranted { return "missing_permissions" }
        if lowDiskPaused { return "paused_low_disk" }
        if storageFullPaused { return "paused_storage_full" }
        if isPaused { return "paused" }
        if screenIsLocked { return "locked" }
        return "recording"
    }

    var storageLevel: StorageLevel {
        StoragePolicy.level(totalBytes: recordingQueue.sync { storageAccountant.totalBytes }, capBytes: configuration.capBytes)
    }

    func pause(minutes: Int?) {
        recordingQueue.async { [activityRecorder] in activityRecorder.flushTyping() }
        pausedUntil = minutes.map { Date().addingTimeInterval(Double($0) * 60) } ?? .distantFuture
        capturingStateChanged()
    }

    func resume() {
        pausedUntil = nil
        capturingStateChanged()
    }

    func startNewSession() {
        recordingQueue.sync { activityRecorder.endSession() }
        onStateChange()
    }

    /// Deletes whole sessions, oldest first, until at most `keepBytes` remain. Never the open session.
    @discardableResult
    func prune(keepBytes: Int64) -> (deletedSessions: [String], freedBytes: Int64) {
        recordingQueue.sync {
            let sessionsToDelete = StoragePolicy.sessionsToPrune(Array(storageAccountant.sessions.values), targetBytes: keepBytes,
                                                                 retentionDays: configuration.retention_days, now: clock.now,
                                                                 protectedSessionIdentifier: sessionWriter.currentSessionIdentifier)
            return (sessionsToDelete.map(\.identifier), storageAccountant.delete(sessionsToDelete))
        }
    }

    /// Menu "Delete today" / "Delete all": closes the open session first, then deletes.
    func deleteSessions(onlyToday: Bool) {
        recordingQueue.sync {
            activityRecorder.endSession()
            let calendar = Calendar.current
            let sessionsToDelete = storageAccountant.sessions.values.filter { session in
                !onlyToday || calendar.isDateInToday(session.startedAt)
            }
            storageAccountant.delete(Array(sessionsToDelete))
        }
        onStateChange()
    }

    func setExcluded(_ bundleIdentifier: String, excluded: Bool) {
        var updatedConfiguration = configuration
        updatedConfiguration.excluded_apps.removeAll { $0 == bundleIdentifier }
        if excluded { updatedConfiguration.excluded_apps.append(bundleIdentifier) }
        do { try updatedConfiguration.save(to: locations.configuration) } catch { recordError("config.json: \(error.localizedDescription)") }
        reloadConfigurationIfChanged(force: true)
    }

    func statusSnapshot() -> [String: Any] {
        let (todayBytes, totalBytes, todayActions, currentSessionIdentifier) = recordingQueue.sync {
            (storageAccountant.todayBytes, storageAccountant.totalBytes, storageAccountant.todayActions, sessionWriter.currentSessionIdentifier)
        }
        return [
            "ok": true,
            "state": stateName,
            "permissions": ["screen_recording": permissions.screenRecording, "accessibility": permissions.accessibility,
                            "input_monitoring": permissions.inputMonitoring],
            "host_app": hostApplicationIdentifier,
            "open_at_login": SMAppService.mainApp.status == .enabled,
            "pid": Int(getpid()),
            "today_bytes": todayBytes,
            "total_bytes": totalBytes,
            "actions_today": todayActions,
            "current_session_id": currentSessionIdentifier as Any,
            "free_disk_bytes": freeDiskBytes,
            "cap_bytes": configuration.capBytes,
            "retention_days": configuration.retention_days,
            "storage_level": storageLevel.rawValue,
            "paused_until": pausedUntil.flatMap { $0 == .distantFuture ? "until_resumed" : ISO8601DateFormatter().string(from: $0) } as Any,
            "data_root": locations.root.path,
            "last_error": lastError as Any,
            "updated_at": ISO8601DateFormatter().string(from: Date()),
        ]
    }

    // MARK: event tap (main thread)

    private func installEventTapIfNeeded() {
        guard eventTap == nil else { return }
        let eventMask = CGEventMask(1 << CGEventType.leftMouseDown.rawValue | 1 << CGEventType.keyDown.rawValue)
        eventTap = CGEvent.tapCreate(
            tap: .cgSessionEventTap, place: .tailAppendEventTap, options: .listenOnly, eventsOfInterest: eventMask,
            callback: { _, eventType, event, userInfo in
                Unmanaged<RecorderEngine>.fromOpaque(userInfo!).takeUnretainedValue().handleTappedEvent(eventType, event)
                return Unmanaged.passUnretained(event)  // listen-only: never modified
            },
            userInfo: Unmanaged.passUnretained(self).toOpaque())
        guard let eventTap else { return recordError("could not create the event tap") }
        CFRunLoopAddSource(CFRunLoopGetMain(), CFMachPortCreateRunLoopSource(nil, eventTap, 0), .commonModes)
        CGEvent.tapEnable(tap: eventTap, enable: true)
    }

    /// Kept tiny: snapshot the event and the pre-input frame, then hand off. AX work happens on the queue.
    private func handleTappedEvent(_ eventType: CGEventType, _ event: CGEvent) {
        if eventType == .tapDisabledByTimeout || eventType == .tapDisabledByUserInput {
            if let eventTap { CGEvent.tapEnable(tap: eventTap, enable: true) }
            return
        }
        guard isCapturing else { return }
        let uptime = clock.uptime
        let frameBeforeInput = frameSource.takeLatestFrame()
        frameSource.noteInput()
        if eventType == .leftMouseDown {
            let clickLocation = event.location
            recordingQueue.async { [activityRecorder] in
                activityRecorder.handleClick(at: clickLocation, uptime: uptime, frameBeforeInput: frameBeforeInput)
            }
        } else if eventType == .keyDown, let keyEvent = NSEvent(cgEvent: event) {
            let modifierFlags = keyEvent.modifierFlags.intersection(.deviceIndependentFlagsMask)
            let keystroke = KeystrokeClassifier.classify(
                keyCode: keyEvent.keyCode, characters: keyEvent.characters ?? "",
                charactersIgnoringModifiers: keyEvent.charactersIgnoringModifiers ?? "",
                command: modifierFlags.contains(.command), control: modifierFlags.contains(.control),
                option: modifierFlags.contains(.option), shift: modifierFlags.contains(.shift))
            if keystroke == .shortcut("cmd+ctrl+alt+p") { return }  // our own pause hotkey
            let secureInputEnabled = IsSecureEventInputEnabled()
            recordingQueue.async { [activityRecorder] in
                activityRecorder.handleKeystroke(keystroke, uptime: uptime, frameBeforeInput: frameBeforeInput,
                                                 secureInputEnabled: secureInputEnabled)
            }
        }
    }

    // MARK: timers and observers

    private func tickEverySecond() {
        if let pausedUntil, pausedUntil <= Date() { resume() }
        guard isCapturing else { return }
        recordingQueue.async { [activityRecorder] in activityRecorder.pollContext() }
    }

    private func tickEveryTenSeconds() {
        reloadConfigurationIfChanged()
        checkFreeDisk()
        enforceStorageCap()
        writeStatusFile()
        onStateChange()
    }

    /// Every 30 s: a grant revoked mid-session pauses capture and shows the warning; it resumes on its own.
    private func refreshPermissions() {
        let previousPermissions = permissions
        permissions = PermissionState.current()
        if permissions != previousPermissions {
            NSLog("permissions screen=\(permissions.screenRecording) ax=\(permissions.accessibility) input=\(permissions.inputMonitoring)")
            if !permissions.allGranted { recordError("missing permission") }
            capturingStateChanged()
        }
        if permissions.allGranted { installEventTapIfNeeded(); frameSource.restartIfStopped() }
    }

    private func reconcileAndPrune() {
        recordingQueue.async { [storageAccountant] in storageAccountant.reconcile() }
        prune(keepBytes: Int64(Double(configuration.capBytes) * StoragePolicy.pruneTargetFraction))  // also applies retention
    }

    /// At 100% of the cap, prune; if the open session alone is too big, pause and notify.
    private func enforceStorageCap() {
        let capBytes = configuration.capBytes
        if storageLevel == .full {
            let pruneResult = prune(keepBytes: Int64(Double(capBytes) * StoragePolicy.pruneTargetFraction))
            if !pruneResult.deletedSessions.isEmpty { NSLog("pruned \(pruneResult.deletedSessions.count) sessions, \(pruneResult.freedBytes) bytes") }
        }
        let storageStillFull = storageLevel == .full
        if storageStillFull != storageFullPaused {
            storageFullPaused = storageStillFull
            if storageStillFull { notify("Apprentice paused", "Recordings hit the \(Self.formatBytes(capBytes)) cap and pruning couldn't free enough.") }
            capturingStateChanged()
        }
    }

    private func checkFreeDisk() {
        let volumeValues = try? locations.root.resourceValues(forKeys: [.volumeAvailableCapacityForImportantUsageKey])
        freeDiskBytes = volumeValues?.volumeAvailableCapacityForImportantUsage ?? 0
        let shouldPause = StoragePolicy.lowDiskPaused(currentlyPaused: lowDiskPaused, freeBytes: freeDiskBytes)
        if shouldPause != lowDiskPaused {
            lowDiskPaused = shouldPause
            if shouldPause { notify("Apprentice paused", "Less than 10 GB free on disk. Recording resumes above 15 GB.") }
            capturingStateChanged()
        }
    }

    private func reloadConfigurationIfChanged(force: Bool = false) {
        let modifiedAt = (try? locations.configuration.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
        guard force || modifiedAt == nil || modifiedAt != configurationModifiedAt else { return }
        let (loadedConfiguration, loadError) = RecorderConfiguration.load(from: locations.configuration)
        configurationModifiedAt = (try? locations.configuration.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate
        if let loadError { recordError(loadError) }
        configuration = loadedConfiguration
        let excludedBundleIdentifiers = Set(loadedConfiguration.excluded_apps)
        let redactor = Redactor(patterns: loadedConfiguration.redaction_patterns)
        recordingQueue.async { [activityRecorder] in
            activityRecorder.excludedBundleIdentifiers = excludedBundleIdentifiers
            activityRecorder.redactor = redactor
        }
        frameSource.setExcludedApplications(excludedBundleIdentifiers)
    }

    private func observeWorkspace() {
        let workspaceCenter = NSWorkspace.shared.notificationCenter
        workspaceCenter.addObserver(forName: NSWorkspace.didActivateApplicationNotification, object: nil, queue: .main) { [weak self] notification in
            guard let self, isCapturing,
                  let application = notification.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication else { return }
            let processIdentifier = application.processIdentifier
            recordingQueue.async { [activityRecorder] in activityRecorder.applicationActivated(processIdentifier: processIdentifier) }
        }
        // Newly launched excluded apps must be added to the capture filter.
        workspaceCenter.addObserver(forName: NSWorkspace.didLaunchApplicationNotification, object: nil, queue: .main) { [weak self] notification in
            guard let self, let application = notification.userInfo?[NSWorkspace.applicationUserInfoKey] as? NSRunningApplication,
                  let bundleIdentifier = application.bundleIdentifier, configuration.excluded_apps.contains(bundleIdentifier) else { return }
            DispatchQueue.main.asyncAfter(deadline: .now() + 2) { self.restartCapture() }
        }
        let distributedCenter = DistributedNotificationCenter.default()
        distributedCenter.addObserver(forName: .init("com.apple.screenIsLocked"), object: nil, queue: .main) { [weak self] _ in
            guard let self else { return }
            let wasCapturing = isCapturing
            screenIsLocked = true
            if wasCapturing { recordingQueue.async { [activityRecorder] in activityRecorder.screenLocked() } }
            capturingStateChanged()
        }
        distributedCenter.addObserver(forName: .init("com.apple.screenIsUnlocked"), object: nil, queue: .main) { [weak self] _ in
            guard let self else { return }
            screenIsLocked = false
            capturingStateChanged()
            if isCapturing { recordingQueue.async { [activityRecorder] in activityRecorder.screenUnlocked() } }
        }
    }

    private func restartCapture() {
        guard isCapturing else { return }
        frameSource.stop()
        frameSource.start()
    }

    /// Start or stop the frame stream to match `isCapturing`; flush typing when capture stops.
    private var frameStreamRunning = false
    private func capturingStateChanged() {
        if isCapturing && !frameStreamRunning {
            frameStreamRunning = true
            frameSource.start()
            let frontmostProcessIdentifier = NSWorkspace.shared.frontmostApplication?.processIdentifier ?? 0
            recordingQueue.async { [activityRecorder] in activityRecorder.applicationActivated(processIdentifier: frontmostProcessIdentifier) }
        } else if !isCapturing && frameStreamRunning {
            frameStreamRunning = false
            frameSource.stop()
            recordingQueue.async { [activityRecorder] in activityRecorder.flushTyping() }
        }
        writeStatusFile()
        onStateChange()
    }

    // MARK: helpers

    func recordError(_ message: String) {
        NSLog("%@", message)
        lastError = message
    }

    private func writeStatusFile() {
        let encodedStatus = try? JSONSerialization.data(withJSONObject: statusSnapshot(), options: [.prettyPrinted, .sortedKeys])
        try? encodedStatus?.write(to: locations.status, options: .atomic)
    }

    private func excludeDataRootFromTimeMachine() {
        let tmutil = Process()
        tmutil.executableURL = URL(fileURLWithPath: "/usr/bin/tmutil")
        tmutil.arguments = ["addexclusion", locations.root.path]
        try? tmutil.run()
    }

    private func notify(_ title: String, _ body: String) {
        let notificationCenter = UNUserNotificationCenter.current()
        notificationCenter.requestAuthorization(options: [.alert]) { granted, _ in
            guard granted else { return }
            let content = UNMutableNotificationContent()
            content.title = title
            content.body = body
            notificationCenter.add(UNNotificationRequest(identifier: UUID().uuidString, content: content, trigger: nil))
        }
    }

    static func formatBytes(_ byteCount: Int64) -> String { ByteCountFormatter.string(fromByteCount: byteCount, countStyle: .file) }

    /// For the manifest: every active display in native pixels.
    static func currentDisplays() -> [DisplayDescription] {
        var displayIdentifiers = [CGDirectDisplayID](repeating: 0, count: 16)
        var displayCount: UInt32 = 0
        CGGetActiveDisplayList(16, &displayIdentifiers, &displayCount)
        return displayIdentifiers.prefix(Int(displayCount)).compactMap { displayIdentifier in
            guard let displayMode = CGDisplayCopyDisplayMode(displayIdentifier), displayMode.width > 0 else { return nil }
            return DisplayDescription(id: Int(displayIdentifier), widthPixels: displayMode.pixelWidth, heightPixels: displayMode.pixelHeight,
                                      scale: Double(displayMode.pixelWidth) / Double(displayMode.width))
        }
    }
}
