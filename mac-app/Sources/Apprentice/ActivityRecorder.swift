import AppKit
import ApprenticeCore
import Carbon.HIToolbox

/// Where an action happened: the app, its focused window, and (in browsers) the page.
struct ActionContext: Equatable {
    var appBundleIdentifier: String
    var appName: String
    var windowTitle: String
    var url: String?
    var site: String
    var isExcluded: Bool
    var isPrivateWindow: Bool
    var isHidden: Bool { isExcluded || isPrivateWindow }
}

/// Typing fields are compared by AX identity; apps with no focused element fall back to the process.
enum TypingFieldIdentity: Equatable {
    case element(AXElement)
    case process(pid_t)
}

/// Turns raw input into actions and context events. Everything here runs on `recordingQueue`;
/// the event tap (main thread) only snapshots the event and the latest frame, then hops over.
final class ActivityRecorder {
    let recordingQueue: DispatchQueue
    let sessionWriter: SessionWriter
    let storageAccountant: StorageAccountant
    let frameSource: ScreenFrameSource
    let clock: RecorderClock

    // Set by the engine (recordingQueue).
    var excludedBundleIdentifiers: Set<String> = []
    var redactor = Redactor(patterns: RecorderConfiguration.defaults.redaction_patterns)

    private var keystrokeCoalescer = KeystrokeCoalescer<TypingFieldIdentity>()
    private struct TypingRun {
        var fieldElement: AXElement?
        var element: ElementRecord
        var context: ActionContext
        var frame: FrameSnapshot?
    }
    private var openTypingRun: TypingRun?

    private var frontmostProcessIdentifier: pid_t = 0
    private var lastRecordedContext: ActionContext?
    private var cachedWebArea: (window: AXElement, windowTitle: String, webArea: AXElement)?
    private var processesWithAccessibilityEnabled: Set<pid_t> = []
    private var isIdle = false
    private var secureFieldVisit: (field: AXElement, element: ElementRecord, context: ActionContext, startedAt: Double)?
    /// Secure fields whose keystrokes did reach the tap (so the coalescer already wrote their TYPE).
    private var secureFieldsWithKeystrokes: Set<AXElement> = []

    static let idleThresholdSeconds: Double = 60
    static let maximumReadableFieldValueLength = 500

    init(recordingQueue: DispatchQueue, sessionWriter: SessionWriter, storageAccountant: StorageAccountant,
         frameSource: ScreenFrameSource, clock: RecorderClock) {
        self.recordingQueue = recordingQueue
        self.sessionWriter = sessionWriter
        self.storageAccountant = storageAccountant
        self.frameSource = frameSource
        self.clock = clock
        sessionWriter.onBytesWritten = { [storageAccountant] sessionIdentifier, writtenBytes, isAction in
            storageAccountant.recordWrite(sessionIdentifier: sessionIdentifier, bytes: writtenBytes, isAction: isAction)
        }
    }

    // MARK: input (recordingQueue)

    /// Whether the frontmost on-screen window under `point` (global, top-left origin) belongs to this process.
    private static func isOwnWindow(at point: CGPoint) -> Bool {
        let windows = CGWindowListCopyWindowInfo([.optionOnScreenOnly, .excludeDesktopElements], kCGNullWindowID) as? [[String: Any]] ?? []
        for window in windows {  // front to back
            guard let bounds = window[kCGWindowBounds as String] as? NSDictionary,
                  let frame = CGRect(dictionaryRepresentation: bounds), frame.contains(point) else { continue }
            if (window[kCGWindowAlpha as String] as? Double ?? 1) == 0 { continue }
            return (window[kCGWindowOwnerPID as String] as? pid_t) == getpid()
        }
        return false
    }

    func handleClick(at clickLocation: CGPoint, uptime: Double, frameBeforeInput: FrameSnapshot?) {
        noteUserInput(at: uptime)
        emitTypingActions(keystrokeCoalescer.flush())
        // A hit test over our own menu or status item is answered in-process, on this queue, and AppKit crashes
        // off the main thread. Our own UI is never recorded anyway.
        guard !Self.isOwnWindow(at: clickLocation) else { return }
        let hitElement = AXElement.systemWide.element(atScreenPoint: clickLocation)
        let processIdentifier = hitElement?.processIdentifier ?? frontmostProcessIdentifier
        let context = currentContext(forProcess: processIdentifier)
        guard !context.isHidden else { return }

        var elementRecord = ElementRecord(role: "unknown", text: "")
        var operation = ActionOperation.click
        var value = ""
        if let hitElement {
            let targetElement = ElementExtraction.actionTarget(forHitElement: hitElement)
            elementRecord = ElementExtraction.record(for: targetElement, hitElement: hitElement,
                                                     bbox: boundingBox(of: targetElement, in: frameBeforeInput))
            // A menu item (popup button, context menu, combo box list, menu bar menu) is a choice.
            if targetElement.role == kAXMenuItemRole {
                operation = .select
                value = redactor.redact(elementRecord.text)
            }
        }
        writeAction(operation: operation, value: value, context: context, element: elementRecord,
                    uptime: uptime, frame: frameBeforeInput)
    }

    func handleKeystroke(_ keystroke: Keystroke, uptime: Double, frameBeforeInput: FrameSnapshot?, secureInputEnabled: Bool) {
        noteUserInput(at: uptime)
        let focusedElement = AXElement.systemWide.elementAttribute(kAXFocusedUIElementAttribute)
        let processIdentifier = focusedElement?.processIdentifier ?? frontmostProcessIdentifier
        let context = currentContext(forProcess: processIdentifier)
        guard !context.isHidden else {
            emitTypingActions(keystrokeCoalescer.flush())
            return
        }
        let fieldIsSecure = secureInputEnabled || (focusedElement?.isSecureTextField ?? false)
        if fieldIsSecure, let focusedElement { secureFieldsWithKeystrokes.insert(focusedElement) }
        let fieldIdentity: TypingFieldIdentity = focusedElement.map { .element($0) } ?? .process(processIdentifier)
        let (coalescedActions, startedRun) = keystrokeCoalescer.receive(keystroke, field: fieldIdentity, fieldIsSecure: fieldIsSecure, at: uptime)

        lazy var focusedElementRecord = focusedElement.map { element in
            ElementExtraction.record(for: element, bbox: boundingBox(of: element, in: frameBeforeInput))
        } ?? ElementRecord(role: "unknown", text: "")
        for coalescedAction in coalescedActions {
            switch coalescedAction.operation {
            case .type:
                emitTypingActions([coalescedAction])
            default:  // ENTER, KEY: the frame before this very keystroke
                writeAction(operation: coalescedAction.operation, value: coalescedAction.value, context: context,
                            element: focusedElementRecord, uptime: coalescedAction.occurredAtUptime,
                            frame: coalescedAction.isSecure ? nil : frameBeforeInput)
            }
        }
        if startedRun {
            openTypingRun = TypingRun(fieldElement: focusedElement, element: focusedElementRecord, context: context, frame: frameBeforeInput)
        }
        recordingQueue.asyncAfter(deadline: .now() + KeystrokeCoalescer<TypingFieldIdentity>.idleTimeoutSeconds + 0.05) { [weak self] in
            guard let self else { return }
            emitTypingActions(keystrokeCoalescer.tick(at: clock.uptime))
        }
    }

    /// Pause, lock, quit: close the open TYPE so it isn't lost.
    func flushTyping() { emitTypingActions(keystrokeCoalescer.flush()) }

    // MARK: context (recordingQueue)

    func applicationActivated(processIdentifier: pid_t) {
        frontmostProcessIdentifier = processIdentifier
        enableAccessibilityTree(forProcess: processIdentifier)
        recordContextIfChanged(forceEvent: .appActivated)
    }

    /// Every second: idle detection, window/URL changes, typing idle flush, manifest upkeep.
    func pollContext() {
        let uptime = clock.uptime
        emitTypingActions(keystrokeCoalescer.tick(at: uptime))
        let secondsSinceAnyInput = CGEventSource.secondsSinceLastEventType(.hidSystemState, eventType: CGEventType(rawValue: ~0)!)
        if secondsSinceAnyInput >= Self.idleThresholdSeconds, !isIdle {
            isIdle = true
            sessionWriter.appendContext(ContextDraft(event: .idleStart, occurredAtUptime: uptime - secondsSinceAnyInput))
        } else if secondsSinceAnyInput < 1.5 {
            noteUserInput(at: uptime)  // mouse moves and scrolls count as activity too
        }
        if !isIdle { recordContextIfChanged(forceEvent: nil) }
        checkSecureFieldVisit(uptime: uptime)
        sessionWriter.periodicMaintenance()
    }

    func screenLocked() {
        flushTyping()
        sessionWriter.screenLocked(at: clock.uptime)
        lastRecordedContext = nil
    }

    func screenUnlocked() {
        sessionWriter.appendContext(ContextDraft(event: .screenUnlocked, occurredAtUptime: clock.uptime))
        recordContextIfChanged(forceEvent: .appActivated)
    }

    func endSession() {
        flushTyping()
        sessionWriter.endSession()
        lastRecordedContext = nil
    }

    /// For NOTES.md experiments: what AX exposes for the frontmost window.
    func describeFrontmostWindow() -> [String: Any] {
        let application = AXElement.application(processIdentifier: frontmostProcessIdentifier)
        guard let window = application.elementAttribute(kAXFocusedWindowAttribute) else { return ["error": "no focused window"] }
        var labels: [String] = []
        _ = window.firstDescendant(maximumVisitedNodes: 300) { element in
            for attributeName in [kAXTitleAttribute, kAXDescriptionAttribute] {
                if let label = element.stringAttribute(attributeName), !label.isEmpty { labels.append("\(element.role): \(label)") }
            }
            return false
        }
        let context = currentContext(forProcess: frontmostProcessIdentifier)
        return ["app": context.appBundleIdentifier, "window": context.windowTitle, "url": context.url as Any,
                "site": context.site, "private": context.isPrivateWindow, "excluded": context.isExcluded,
                "labels": Array(labels.prefix(80))]
    }

    // MARK: private

    private func noteUserInput(at uptime: Double) {
        sessionWriter.noteUserInput()
        if isIdle {
            isIdle = false
            sessionWriter.appendContext(ContextDraft(event: .idleEnd, occurredAtUptime: uptime))
        }
    }

    private func emitTypingActions(_ coalescedActions: [CoalescedKeyAction<TypingFieldIdentity>]) {
        for coalescedAction in coalescedActions where coalescedAction.operation == .type {
            guard let typingRun = openTypingRun else { continue }
            openTypingRun = nil
            var value = coalescedAction.value
            if !coalescedAction.isSecure, let fieldElement = typingRun.fieldElement,
               let fieldValue = fieldElement.stringAttribute(kAXValueAttribute),
               fieldValue.count <= Self.maximumReadableFieldValueLength {
                value = fieldValue  // the field's resulting value beats keystroke reconstruction
            }
            writeAction(operation: .type, value: coalescedAction.isSecure ? secureValuePlaceholder : redactor.redact(value),
                        context: typingRun.context, element: typingRun.element, uptime: coalescedAction.occurredAtUptime,
                        frame: coalescedAction.isSecure ? nil : typingRun.frame)
        }
    }

    private func writeAction(operation: ActionOperation, value: String, context: ActionContext, element: ElementRecord,
                             uptime: Double, frame: FrameSnapshot?) {
        let screenshotJPEG = frame.flatMap(frameSource.jpegData(for:))
        let draft = ActionDraft(operation: operation, value: value, site: context.site, url: context.url,
                                appBundleIdentifier: context.appBundleIdentifier, windowTitle: context.windowTitle,
                                element: element, occurredAtUptime: uptime)
        sessionWriter.appendAction(draft, screenshotJPEG: screenshotJPEG)
    }

    private func boundingBox(of element: AXElement, in frame: FrameSnapshot?) -> [Double]? {
        guard let frame, let elementFrame = element.frame else { return nil }
        return ElementExtraction.boundingBoxInScreenshotPixels(elementFrame: elementFrame, displayBounds: frame.displayBounds,
                                                               screenshotPixelSize: frame.pixelSize)
    }

    private func currentContext(forProcess processIdentifier: pid_t) -> ActionContext {
        let runningApplication = NSRunningApplication(processIdentifier: processIdentifier)
        let bundleIdentifier = runningApplication?.bundleIdentifier ?? "unknown"
        let isExcluded = excludedBundleIdentifiers.contains(bundleIdentifier) || processIdentifier == getpid()
        var context = ActionContext(appBundleIdentifier: bundleIdentifier, appName: runningApplication?.localizedName ?? bundleIdentifier,
                                    windowTitle: "", url: nil, site: bundleIdentifier, isExcluded: isExcluded, isPrivateWindow: false)
        guard !isExcluded else { return context }
        let window = AXElement.application(processIdentifier: processIdentifier).elementAttribute(kAXFocusedWindowAttribute)
        context.windowTitle = window?.stringAttribute(kAXTitleAttribute) ?? ""
        if let window, SiteNaming.browserBundleIdentifiers.contains(bundleIdentifier) {
            context.isPrivateWindow = PrivateBrowsing.isPrivateWindow(appBundleIdentifier: bundleIdentifier, windowTitle: context.windowTitle,
                                                                      windowLabels: privateWindowLabels(of: window, bundleIdentifier: bundleIdentifier))
            if !context.isPrivateWindow {
                context.url = browserURL(in: window, windowTitle: context.windowTitle)
                context.site = SiteNaming.site(url: context.url, appBundleIdentifier: bundleIdentifier)
            }
        }
        return context
    }

    /// Chrome-family incognito windows don't say so in their title, but their toolbar has a button labelled
    /// "Incognito" (see NOTES.md). Read the toolbar's labels only, which is cheap.
    private func privateWindowLabels(of window: AXElement, bundleIdentifier: String) -> [String] {
        guard bundleIdentifier != "com.apple.Safari", bundleIdentifier != "org.mozilla.firefox" else { return [] }
        var labels: [String] = []
        let toolbar = window.firstDescendant(maximumVisitedNodes: 60) { $0.role == kAXToolbarRole }
        _ = toolbar?.firstDescendant(maximumVisitedNodes: 80) { element in
            if let label = element.stringAttribute(kAXDescriptionAttribute) ?? element.stringAttribute(kAXTitleAttribute) { labels.append(label) }
            return false
        }
        return labels
    }

    /// The page URL from the web area's AXURL (cached per window until its title changes, i.e. a tab switch).
    private func browserURL(in window: AXElement, windowTitle: String) -> String? {
        if let cachedWebArea, cachedWebArea.window == window, cachedWebArea.windowTitle == windowTitle,
           let url = cachedWebArea.webArea.attributeValue("AXURL") as? URL {
            return url.absoluteString
        }
        cachedWebArea = nil
        guard let webArea = window.firstDescendant(where: { $0.role == "AXWebArea" }) else {
            return (window.attributeValue(kAXDocumentAttribute) as? String)
        }
        cachedWebArea = (window, windowTitle, webArea)
        return (webArea.attributeValue("AXURL") as? URL)?.absoluteString ?? webArea.stringAttribute(kAXDocumentAttribute)
    }

    /// Chromium and Electron build their AX tree only for assistive tech; this flag turns it on per app.
    private func enableAccessibilityTree(forProcess processIdentifier: pid_t) {
        guard processesWithAccessibilityEnabled.insert(processIdentifier).inserted else { return }
        AXElement.application(processIdentifier: processIdentifier).setAttribute("AXManualAccessibility", to: kCFBooleanTrue)
    }

    private func recordContextIfChanged(forceEvent: ContextEventKind?) {
        guard frontmostProcessIdentifier != 0 else { return }
        let context = currentContext(forProcess: frontmostProcessIdentifier)
        let previousContext = lastRecordedContext
        guard forceEvent != nil || context != previousContext else { return }
        lastRecordedContext = context
        let uptime = clock.uptime
        DispatchQueue.main.async { [frameSource] in frameSource.setFramesSuppressed(context.isHidden) }
        if context.isHidden {
            // Excluded app or private window: only this, no name, title or URL.
            if previousContext?.isHidden != true {
                flushTyping()
                sessionWriter.appendContext(ContextDraft(event: .excludedAppActive, occurredAtUptime: uptime))
            }
            return
        }
        let eventKind: ContextEventKind
        if let forceEvent { eventKind = forceEvent }
        else if context.appBundleIdentifier != previousContext?.appBundleIdentifier || previousContext?.isHidden == true { eventKind = .appActivated }
        else if context.windowTitle != previousContext?.windowTitle { eventKind = .windowChanged }
        else { eventKind = .urlChanged }
        sessionWriter.appendContext(ContextDraft(event: eventKind, appBundleIdentifier: context.appBundleIdentifier, appName: context.appName,
                                                 windowTitle: context.windowTitle, url: context.url, site: context.site,
                                                 occurredAtUptime: uptime))
    }

    /// Under secure input (password fields) keystrokes never reach the tap. Detect the visit instead:
    /// focus entered a secure field and left it with characters in it → TYPE "[secure]", no screenshot.
    private func checkSecureFieldVisit(uptime: Double) {
        let focusedElement = AXElement.systemWide.elementAttribute(kAXFocusedUIElementAttribute)
        if let visit = secureFieldVisit, focusedElement != visit.field {
            secureFieldVisit = nil
            let characterCount = (visit.field.attributeValue(kAXNumberOfCharactersAttribute) as? Int)
                ?? visit.field.stringAttribute(kAXValueAttribute)?.count ?? 1
            let sawKeystrokes = secureFieldsWithKeystrokes.remove(visit.field) != nil
            if !sawKeystrokes, characterCount > 0 {
                writeAction(operation: .type, value: secureValuePlaceholder, context: visit.context, element: visit.element,
                            uptime: visit.startedAt, frame: nil)
            }
        }
        guard secureFieldVisit == nil, let focusedElement, focusedElement.isSecureTextField,
              let processIdentifier = focusedElement.processIdentifier else { return }
        let context = currentContext(forProcess: processIdentifier)
        guard !context.isHidden else { return }
        secureFieldVisit = (focusedElement, ElementExtraction.record(for: focusedElement, bbox: nil), context, uptime)
    }
}
