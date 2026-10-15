import CoreGraphics
import Foundation
import Testing
@testable import ApprenticeCore

// MARK: fixtures

final class FakeClock: RecorderClock {
    var now: Date
    var uptime: Double
    init(now: Date, uptime: Double = 1000) { self.now = now; self.uptime = uptime }
    func advance(_ seconds: Double) { now = now.addingTimeInterval(seconds); uptime += seconds }
}

final class FixtureNode: AccessibilityNode {
    let attributes: [String: String]
    let parentNode: FixtureNode?
    init(_ attributes: [String: String], parent: FixtureNode? = nil) { self.attributes = attributes; self.parentNode = parent }
    func stringAttribute(_ attributeName: String) -> String? { attributes[attributeName] }
}

let pacificCalendar: Calendar = {
    var calendar = Calendar(identifier: .gregorian)
    calendar.timeZone = TimeZone(identifier: "America/Los_Angeles")!
    return calendar
}()

func pacificDate(_ text: String) -> Date {
    let formatter = DateFormatter()
    formatter.calendar = pacificCalendar
    formatter.timeZone = pacificCalendar.timeZone
    formatter.dateFormat = "yyyy-MM-dd HH:mm:ss"
    return formatter.date(from: text)!
}

func temporaryDirectory() -> URL {
    let directory = FileManager.default.temporaryDirectory.appendingPathComponent("apprentice-tests-\(UUID().uuidString)")
    try! FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
    return directory
}

// MARK: keystroke coalescing

@Suite struct KeystrokeCoalescingTests {
    typealias Coalescer = KeystrokeCoalescer<String>

    @Test func typingInOneFieldBecomesOneTypeOnEnter() {
        var coalescer = Coalescer()
        var allActions: [CoalescedKeyAction<String>] = []
        let firstResult = coalescer.receive(.characters("h"), field: "search", fieldIsSecure: false, at: 10)
        #expect(firstResult.startedRun)
        for (offset, keystroke) in [Keystroke.characters("i"), .characters("x"), .deleteBackward, .characters("!")].enumerated() {
            allActions += coalescer.receive(keystroke, field: "search", fieldIsSecure: false, at: 10.1 + Double(offset) * 0.1).actions
        }
        allActions += coalescer.receive(.returnKey, field: "search", fieldIsSecure: false, at: 11).actions
        #expect(allActions.map(\.operation) == [.type, .enter])
        #expect(allActions[0].value == "hi!")
        #expect(allActions[0].occurredAtUptime == 10)
        #expect(allActions[1].occurredAtUptime == 11)
    }

    @Test func fieldChangeTabIdleAndShortcutEndTheRun() {
        var coalescer = Coalescer()
        _ = coalescer.receive(.characters("a"), field: "first", fieldIsSecure: false, at: 0)
        let afterFieldChange = coalescer.receive(.characters("b"), field: "second", fieldIsSecure: false, at: 1)
        #expect(afterFieldChange.actions.map(\.value) == ["a"] && afterFieldChange.startedRun)

        let afterTab = coalescer.receive(.tab, field: "second", fieldIsSecure: false, at: 2)
        #expect(afterTab.actions.map(\.value) == ["b"])

        _ = coalescer.receive(.characters("c"), field: "third", fieldIsSecure: false, at: 3)
        #expect(coalescer.tick(at: 5.9).isEmpty)
        #expect(coalescer.tick(at: 6.0).map(\.value) == ["c"])

        _ = coalescer.receive(.characters("d"), field: "third", fieldIsSecure: false, at: 7)
        let afterShortcut = coalescer.receive(.shortcut("cmd+s"), field: "third", fieldIsSecure: false, at: 7.5)
        #expect(afterShortcut.actions.map(\.operation) == [.type, .key])
        #expect(afterShortcut.actions.map(\.value) == ["d", "cmd+s"])
    }

    @Test func idleGapStartsANewRunInTheSameField() {
        var coalescer = Coalescer()
        _ = coalescer.receive(.characters("a"), field: "notes", fieldIsSecure: false, at: 0)
        let later = coalescer.receive(.characters("b"), field: "notes", fieldIsSecure: false, at: 4)
        #expect(later.actions.map(\.value) == ["a"] && later.startedRun)
    }

    @Test func focusChangeFlushesOnlyForAnotherField() {
        var coalescer = Coalescer()
        _ = coalescer.receive(.characters("a"), field: "notes", fieldIsSecure: false, at: 0)
        #expect(coalescer.focusChanged(to: "notes").isEmpty)
        #expect(coalescer.focusChanged(to: "other").map(\.value) == ["a"])
        #expect(coalescer.flush().isEmpty)
    }

    @Test func classifierSeparatesTextShortcutsAndNoise() {
        func classify(_ keyCode: UInt16, _ characters: String, command: Bool = false, control: Bool = false, shift: Bool = false) -> Keystroke {
            KeystrokeClassifier.classify(keyCode: keyCode, characters: characters, charactersIgnoringModifiers: characters.lowercased(),
                                         command: command, control: control, option: false, shift: shift)
        }
        #expect(classify(0, "a") == .characters("a"))
        #expect(classify(17, "T", command: true, shift: true) == .shortcut("cmd+shift+t"))
        #expect(classify(123, "\u{F702}", command: true) == .shortcut("cmd+left"))
        #expect(classify(36, "\r") == .returnKey)
        #expect(classify(48, "\t") == .tab)
        #expect(classify(51, "\u{7F}") == .deleteBackward)
        #expect(classify(123, "\u{F702}") == .ignored)
        #expect(classify(53, "\u{1B}") == .ignored)
        #expect(classify(0, "\u{1}", control: true) == .ignored)
    }
}

// MARK: secure fields

@Suite struct SecureFieldSuppressionTests {
    @Test func secureFieldNeverKeepsTypedCharacters() {
        var coalescer = KeystrokeCoalescer<String>()
        for character in "hunter2" { _ = coalescer.receive(.characters(String(character)), field: "password", fieldIsSecure: true, at: 1) }
        let actions = coalescer.receive(.returnKey, field: "password", fieldIsSecure: true, at: 2).actions
        #expect(actions.first?.operation == .type)
        #expect(actions.first?.value == "[secure]")
        #expect(actions.first?.isSecure == true)
        #expect(!String(describing: coalescer).contains("hunter"))
    }

    @Test func runTurnsSecureIfSecureInputStartsMidway() {
        var coalescer = KeystrokeCoalescer<String>()
        _ = coalescer.receive(.characters("a"), field: "login", fieldIsSecure: false, at: 1)
        _ = coalescer.receive(.characters("b"), field: "login", fieldIsSecure: true, at: 1.1)
        #expect(coalescer.flush().first?.value == "[secure]")
    }
}

// MARK: AX role / text extraction

@Suite struct ElementExtractionTests {
    @Test func roleIsStrippedAndLowercased() {
        #expect(ElementExtraction.role(of: FixtureNode(["AXRole": "AXTextField"])) == "textfield")
        #expect(ElementExtraction.role(of: FixtureNode(["AXRole": "AXMenuItem"])) == "menuitem")
        #expect(ElementExtraction.role(of: FixtureNode([:])) == "unknown")
    }

    @Test func textPrefersTitleThenDescriptionPlaceholderHelp() {
        #expect(ElementExtraction.text(of: FixtureNode(["AXRole": "AXButton", "AXTitle": "Save", "AXDescription": "Save file"])) == "Save")
        #expect(ElementExtraction.text(of: FixtureNode(["AXRole": "AXButton", "AXTitle": "", "AXDescription": "Close"])) == "Close")
        #expect(ElementExtraction.text(of: FixtureNode(["AXRole": "AXTextField", "AXPlaceholderValue": "Search"])) == "Search")
        #expect(ElementExtraction.text(of: FixtureNode(["AXRole": "AXButton", "AXHelp": "Opens settings"])) == "Opens settings")
    }

    @Test func clickOnLinkLabelResolvesToTheLink() {
        // Chrome: AXWebArea > AXLink > AXStaticText("Attractions"); the link itself has no title.
        let webArea = FixtureNode(["AXRole": "AXWebArea", "AXTitle": "Booking.com"])
        let link = FixtureNode(["AXRole": "AXLink"], parent: webArea)
        let label = FixtureNode(["AXRole": "AXStaticText", "AXValue": "Attractions"], parent: link)
        let target = ElementExtraction.actionTarget(forHitElement: label)
        let record = ElementExtraction.record(for: target, hitElement: label, bbox: nil)
        #expect(record.role == "link")
        #expect(record.text == "Attractions")
    }

    @Test func unlabelledElementUsesNearestLabelledAncestorButNotTheWindow() {
        let window = FixtureNode(["AXRole": "AXWindow", "AXTitle": "Document"])
        let group = FixtureNode(["AXRole": "AXGroup", "AXDescription": "Formatting"], parent: window)
        let checkbox = FixtureNode(["AXRole": "AXCheckBox", "AXSubrole": "AXToggle"], parent: group)
        #expect(ElementExtraction.text(of: checkbox) == "Formatting")
        #expect(ElementExtraction.tag(of: checkbox) == "AXToggle")
        #expect(ElementExtraction.text(of: FixtureNode(["AXRole": "AXButton"], parent: window)) == "")
    }

    @Test func textIsCollapsedAndTruncatedTo200() {
        let longTitle = String(repeating: "word ", count: 100)
        let text = ElementExtraction.text(of: FixtureNode(["AXRole": "AXButton", "AXTitle": "  multi\n line  \(longTitle)"]))
        #expect(text.hasPrefix("multi line word"))
        #expect(text.count == 200)
    }

    @Test func bboxIsConvertedToScreenshotPixels() {
        // 1512x982 pt display captured at 1280 px wide.
        let bbox = ElementExtraction.boundingBoxInScreenshotPixels(
            elementFrame: CGRect(x: 1512 + 100, y: 50, width: 151.2, height: 20),
            displayBounds: CGRect(x: 1512, y: 0, width: 1512, height: 982),
            screenshotPixelSize: CGSize(width: 1280, height: 831))
        #expect(bbox == [84.7, 42.3, 128.0, 16.9])
    }
}

// MARK: sites, redaction, private windows

@Suite struct PrivacyAndSiteTests {
    @Test func siteIsTheRegistrableDomainLabel() {
        #expect(SiteNaming.site(url: "https://www.booking.com/attractions", appBundleIdentifier: "com.google.Chrome") == "booking")
        #expect(SiteNaming.site(url: "https://docs.google.com/x", appBundleIdentifier: "com.google.Chrome") == "google")
        #expect(SiteNaming.site(url: "https://www.bbc.co.uk/news", appBundleIdentifier: "com.apple.Safari") == "bbc")
        #expect(SiteNaming.site(url: "http://localhost:3000/", appBundleIdentifier: "com.apple.Safari") == "localhost")
        #expect(SiteNaming.site(url: "http://192.168.1.20/", appBundleIdentifier: "com.apple.Safari") == "192.168.1.20")
        #expect(SiteNaming.site(url: nil, appBundleIdentifier: "com.apple.finder") == "com.apple.finder")
        #expect(SiteNaming.site(url: "about:blank", appBundleIdentifier: "com.google.Chrome") == "com.google.Chrome")
    }

    @Test func redactionCatchesCardsIbansAndSsns() {
        let redactor = Redactor(patterns: RecorderConfiguration.defaults.redaction_patterns)
        #expect(redactor.redact("card 4111 1111 1111 1111 exp") == "card [redacted] exp")
        #expect(redactor.redact("4111-1111-1111-1111") == "[redacted]")
        #expect(redactor.redact("IBAN FR76 3000 6000 0112 3456 7890 189") == "IBAN [redacted]")
        #expect(redactor.redact("ssn 123-45-6789.") == "ssn [redacted].")
        #expect(redactor.redact("order 12345 on 2026-10-12, call 555-1234") == "order 12345 on 2026-10-12, call 555-1234")
    }

    @Test func configFallsBackToDefaultsPerKey() throws {
        let directory = temporaryDirectory()
        let configurationFile = directory.appendingPathComponent("config.json")
        try #"{"cap_gb": 0.2}"#.write(to: configurationFile, atomically: true, encoding: .utf8)
        let loaded = RecorderConfiguration.load(from: configurationFile)
        #expect(loaded.configuration.cap_gb == 0.2)
        #expect(loaded.configuration.capBytes == 200_000_000)
        #expect(loaded.configuration.excluded_apps.contains("com.1password.1password"))
        #expect(loaded.error == nil)
    }

    @Test func privateWindowsAreDetectedOnlyInBrowsers() {
        #expect(PrivateBrowsing.isPrivateWindow(appBundleIdentifier: "org.mozilla.firefox", windowTitle: "Mozilla Firefox Private Browsing"))
        #expect(PrivateBrowsing.isPrivateWindow(appBundleIdentifier: "com.google.Chrome", windowTitle: "New Tab", windowLabels: ["Incognito"]))
        #expect(!PrivateBrowsing.isPrivateWindow(appBundleIdentifier: "com.google.Chrome", windowTitle: "Private equity – Google Search"))
        #expect(!PrivateBrowsing.isPrivateWindow(appBundleIdentifier: "com.apple.Notes", windowTitle: "Incognito ideas"))
    }
}

// MARK: session splitting

@Suite struct SessionSplittingTests {
    let start = pacificDate("2026-10-12 09:15:00")

    @Test func splitRules() {
        func reason(lastInputAfter: Double, now nowOffset: Double, locked: Bool = false) -> SessionSplitReason? {
            SessionPolicy.splitReason(sessionStartedAt: start, lastInputAt: start.addingTimeInterval(lastInputAfter),
                                      now: start.addingTimeInterval(nowOffset), lockedSinceSessionStart: locked, calendar: pacificCalendar)
        }
        #expect(reason(lastInputAfter: 10, now: 609) == nil)
        #expect(reason(lastInputAfter: 10, now: 610) == .idle)
        #expect(reason(lastInputAfter: 10, now: 20, locked: true) == .screenLock)
        #expect(reason(lastInputAfter: 14 * 3600 + 44 * 60, now: 14 * 3600 + 46 * 60) == .midnight) // 23:59 → 00:01
        #expect(SessionPolicy.splitReason(sessionStartedAt: nil, lastInputAt: nil, now: start, lockedSinceSessionStart: false,
                                          calendar: pacificCalendar) == .restart)
    }

    @Test func identifierFormatAndCollisions() {
        let identifier = SessionPolicy.sessionIdentifier(startingAt: start, calendar: pacificCalendar) { _ in false }
        #expect(identifier == "20261012-091500")
        let taken: Set = ["20261012-091500", "20261012-091500-1"]
        #expect(SessionPolicy.sessionIdentifier(startingAt: start, calendar: pacificCalendar) { taken.contains($0) } == "20261012-091500-2")
    }

    @Test func writerSplitsOnIdleLockAndMidnight() throws {
        let sessionsDirectory = temporaryDirectory()
        let clock = FakeClock(now: pacificDate("2026-10-12 23:40:00"))
        let writer = SessionWriter(sessionsDirectory: sessionsDirectory, clock: clock, calendar: pacificCalendar,
                                   hostApplicationIdentifier: "test", displaysProvider: { [] })
        func act() {
            writer.noteUserInput()
            writer.appendAction(ActionDraft(operation: .click, value: "", site: "test", url: nil, appBundleIdentifier: "test",
                                            windowTitle: "", element: ElementRecord(role: "button", text: "x"),
                                            occurredAtUptime: clock.uptime), screenshotJPEG: nil)
        }
        act()
        let firstSession = writer.currentSessionIdentifier
        clock.advance(300); act()
        #expect(writer.currentSessionIdentifier == firstSession)
        clock.advance(600); act()                                 // 10 min idle → new
        let afterIdle = writer.currentSessionIdentifier
        #expect(afterIdle != firstSession)
        writer.screenLocked(at: clock.uptime)
        #expect(writer.currentSessionIdentifier == nil)
        clock.advance(5); act()                                   // after lock → new
        let afterLock = writer.currentSessionIdentifier
        #expect(afterLock != afterIdle)
        clock.advance(400); act()                                 // 23:55:05 → 00:01:45: midnight → new
        #expect(writer.currentSessionIdentifier != afterLock)
        #expect(writer.currentSessionIdentifier!.hasPrefix("20261013-"))
        let sessionCount = try FileManager.default.contentsOfDirectory(atPath: sessionsDirectory.path).count
        #expect(sessionCount == 4)
    }
}

// MARK: storage accounting and pruning

@Suite struct StorageTests {
    func makeSession(in sessionsDirectory: URL, identifier: String, bytes: Int, actions: Int) throws {
        let traces = sessionsDirectory.appendingPathComponent("\(identifier)/traces")
        try FileManager.default.createDirectory(at: traces, withIntermediateDirectories: true)
        let actionLines = String(repeating: "{}\n", count: actions)
        try actionLines.write(to: traces.appendingPathComponent("dom.jsonl"), atomically: true, encoding: .utf8)
        try Data(count: bytes - actionLines.utf8.count).write(to: sessionsDirectory.appendingPathComponent("\(identifier)/blob"))
    }

    @Test func pruningRemovesExpiredThenOldestAndNeverTheCurrentSession() {
        let now = pacificDate("2026-10-12 12:00:00")
        func session(_ identifier: String, daysAgo: Double, bytes: Int64) -> SessionUsage {
            SessionUsage(identifier: identifier, startedAt: now.addingTimeInterval(-daysAgo * 86_400), bytes: bytes, actions: 1)
        }
        let sessions = [session("current", daysAgo: 0, bytes: 50), session("expired", daysAgo: 31, bytes: 1),
                        session("old", daysAgo: 5, bytes: 30), session("older", daysAgo: 6, bytes: 30), session("recent", daysAgo: 1, bytes: 30)]
        let pruned = StoragePolicy.sessionsToPrune(sessions, targetBytes: 90, retentionDays: 30, now: now, protectedSessionIdentifier: "current")
        #expect(pruned.map(\.identifier) == ["expired", "older", "old"])

        // Even if only the current session is left over target, it stays.
        let onlyCurrent = StoragePolicy.sessionsToPrune([session("current", daysAgo: 0, bytes: 500)], targetBytes: 10, retentionDays: 30,
                                                        now: now, protectedSessionIdentifier: "current")
        #expect(onlyCurrent.isEmpty)
    }

    @Test func levelsAndLowDiskHysteresis() {
        #expect(StoragePolicy.level(totalBytes: 79, capBytes: 100) == .normal)
        #expect(StoragePolicy.level(totalBytes: 80, capBytes: 100) == .nearCap)
        #expect(StoragePolicy.level(totalBytes: 100, capBytes: 100) == .full)
        let gigabyte: Int64 = 1_000_000_000
        #expect(!StoragePolicy.lowDiskPaused(currentlyPaused: false, freeBytes: 12 * gigabyte))
        #expect(StoragePolicy.lowDiskPaused(currentlyPaused: false, freeBytes: 9 * gigabyte))
        #expect(StoragePolicy.lowDiskPaused(currentlyPaused: true, freeBytes: 12 * gigabyte))
        #expect(!StoragePolicy.lowDiskPaused(currentlyPaused: true, freeBytes: 16 * gigabyte))
    }

    @Test func liveCountsReconcileWithScanAndDeletionFreesBytes() throws {
        let sessionsDirectory = temporaryDirectory()
        let clock = FakeClock(now: pacificDate("2026-10-12 12:00:00"))
        let accountant = StorageAccountant(sessionsDirectory: sessionsDirectory, clock: clock, calendar: pacificCalendar)
        try makeSession(in: sessionsDirectory, identifier: "20261010-080000", bytes: 4000, actions: 3)
        try makeSession(in: sessionsDirectory, identifier: "20261012-090000", bytes: 1000, actions: 2)
        try FileManager.default.createDirectory(at: sessionsDirectory.appendingPathComponent("not-a-session"), withIntermediateDirectories: true)
        accountant.reconcile()
        #expect(accountant.totalBytes == 5000)
        #expect(accountant.todayBytes == 1000)
        #expect(accountant.todayActions == 2)

        accountant.recordWrite(sessionIdentifier: "20261012-090000", bytes: 250, isAction: true)
        #expect(accountant.todayBytes == 1250 && accountant.todayActions == 3)

        let week = accountant.lastDays(7)
        #expect(week.count == 7)
        #expect(week[0].bytes == 1250 && week[2].bytes == 4000 && week[2].actions == 3 && week[1].bytes == 0)

        let pruned = StoragePolicy.sessionsToPrune(Array(accountant.sessions.values), targetBytes: 2000, retentionDays: 30,
                                                   now: clock.now, protectedSessionIdentifier: "20261012-090000")
        #expect(accountant.delete(pruned) == 4000)
        #expect(!FileManager.default.fileExists(atPath: sessionsDirectory.appendingPathComponent("20261010-080000").path))
        accountant.reconcile()
        #expect(accountant.totalBytes == 1000)  // live count drift (+250) corrected by the scan
    }
}

// MARK: schema validation of a generated bundle

@Suite struct BundleSchemaTests {
    static let repositoryRoot = URL(fileURLWithPath: #filePath).deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()

    func loadSchema(_ name: String) throws -> [String: Any] {
        try JSONSerialization.jsonObject(with: Data(contentsOf: Self.repositoryRoot.appendingPathComponent("schemas/\(name)"))) as! [String: Any]
    }

    /// Validates a finished session directory; returns every error found.
    func validateBundle(at sessionDirectory: URL) throws -> [String] {
        let manifest = try JSONSerialization.jsonObject(with: Data(contentsOf: sessionDirectory.appendingPathComponent("manifest.json")))
        var foundErrors = SchemaValidator.errors(in: manifest, schema: try loadSchema("manifest.schema.json"), path: "manifest")
        let actionSchema = try loadSchema("action.schema.json")
        let actionLines = try String(contentsOf: sessionDirectory.appendingPathComponent("traces/dom.jsonl"), encoding: .utf8)
            .split(separator: "\n")
        var previousTime = 0.0
        for (lineNumber, line) in actionLines.enumerated() {
            let action = try JSONSerialization.jsonObject(with: Data(line.utf8)) as! [String: Any]
            foundErrors += SchemaValidator.errors(in: action, schema: actionSchema, path: "dom[\(lineNumber)]")
            if action["seq"] as? Int != lineNumber { foundErrors.append("dom[\(lineNumber)]: seq not contiguous") }
            let time = action["t"] as? Double ?? -1
            if time < previousTime { foundErrors.append("dom[\(lineNumber)]: t decreased") }
            previousTime = time
            if let screenshot = action["screenshot"] as? String,
               !FileManager.default.fileExists(atPath: sessionDirectory.appendingPathComponent(screenshot).path) {
                foundErrors.append("dom[\(lineNumber)]: missing \(screenshot)")
            }
        }
        let manifestObject = manifest as! [String: Any]
        let domCount = ((manifestObject["streams"] as! [String: Any])["dom"] as! [String: Any])["count"] as! Int
        if domCount != actionLines.count { foundErrors.append("manifest dom count \(domCount) != \(actionLines.count) lines") }
        return foundErrors
    }

    @Test func generatedBundleMatchesTheSchemas() throws {
        let sessionsDirectory = temporaryDirectory()
        let clock = FakeClock(now: pacificDate("2026-10-12 09:15:00"))
        let writer = SessionWriter(sessionsDirectory: sessionsDirectory, clock: clock, calendar: pacificCalendar,
                                   hostApplicationIdentifier: "blog.eliot.apprentice",
                                   displaysProvider: { [DisplayDescription(id: 1, widthPixels: 3024, heightPixels: 1964, scale: 2)] })
        let fakeJPEG = Data([0xFF, 0xD8, 0xFF, 0xD9])
        writer.noteUserInput()
        writer.appendContext(ContextDraft(event: .appActivated, appBundleIdentifier: "com.google.Chrome", appName: "Google Chrome",
                                          windowTitle: "Attractions – Booking.com", url: "https://www.booking.com/attractions",
                                          site: "booking", occurredAtUptime: clock.uptime))
        clock.advance(9.22)
        let click = writer.appendAction(ActionDraft(
            operation: .click, value: "", site: "booking", url: "https://www.booking.com/attractions", appBundleIdentifier: "com.google.Chrome",
            windowTitle: "Attractions – Booking.com",
            element: ElementRecord(role: "link", text: "Attractions", bbox: [577.6, 68.0, 128.1, 44.0]),
            occurredAtUptime: clock.uptime), screenshotJPEG: fakeJPEG)
        #expect(click?.screenshot == "screenshots/00000.jpg")
        clock.advance(2)
        // A TYPE whose run started before the previous action still gets a non-decreasing t.
        writer.appendAction(ActionDraft(operation: .type, value: "[secure]", site: "booking", url: nil, appBundleIdentifier: "com.google.Chrome",
                                        windowTitle: "Sign in", element: ElementRecord(tag: "AXSecureTextField", role: "textfield", text: "Password"),
                                        occurredAtUptime: clock.uptime - 5), screenshotJPEG: nil)
        writer.appendAction(ActionDraft(operation: .key, value: "cmd+t", site: "com.apple.finder", url: nil, appBundleIdentifier: "com.apple.finder",
                                        windowTitle: "Downloads", element: ElementRecord(role: "window", text: "Downloads"),
                                        occurredAtUptime: clock.uptime), screenshotJPEG: fakeJPEG)
        let sessionDirectory = sessionsDirectory.appendingPathComponent(writer.currentSessionIdentifier!)
        // Mid-session (as after a crash) the manifest is already valid; it's rewritten at the end.
        #expect(try validateBundle(at: sessionDirectory).filter { !$0.contains("count") } == [])
        writer.endSession()
        #expect(try validateBundle(at: sessionDirectory) == [])

        let manifest = try JSONSerialization.jsonObject(with: Data(contentsOf: sessionDirectory.appendingPathComponent("manifest.json"))) as! [String: Any]
        #expect(manifest["session_id"] as? String == "20261012-091500")
        #expect(manifest["started_at"] as? String == "2026-10-12T09:15:00-07:00")
        #expect((manifest["counts"] as! [String: Any])["sites"] as? Int == 2)
        #expect(((manifest["streams"] as! [String: Any])["screenshots"] as! [String: Any])["count"] as? Int == 2)

        let firstLine = try String(contentsOf: sessionDirectory.appendingPathComponent("traces/dom.jsonl"), encoding: .utf8).split(separator: "\n")[0]
        #expect(firstLine.contains(#""dom_snapshot":null"#) && firstLine.contains(#""t":9.22"#))
    }

    @Test func validatorRejectsBadActions() throws {
        let schema = try loadSchema("action.schema.json")
        let badAction: [String: Any] = ["seq": -1, "t": 1, "type": "action", "op": "SCROLL", "site": "x", "element": ["role": "button"]]
        let foundErrors = SchemaValidator.errors(in: badAction, schema: schema)
        #expect(foundErrors.contains { $0.contains("seq") })
        #expect(foundErrors.contains { $0.contains("enum") })
        #expect(foundErrors.contains { $0.contains("missing text") })
    }
}

// MARK: real recordings (acceptance)

/// `APPRENTICE_VALIDATE_SESSIONS=<sessions dir> swift test --filter RecordedSessions` validates real bundles.
@Suite struct RecordedSessionsTests {
    @Test func recordedSessionsMatchTheSchemas() throws {
        guard let sessionsPath = ProcessInfo.processInfo.environment["APPRENTICE_VALIDATE_SESSIONS"] else { return }
        let sessionDirectories = try FileManager.default.contentsOfDirectory(at: URL(fileURLWithPath: sessionsPath), includingPropertiesForKeys: nil)
        #expect(!sessionDirectories.isEmpty)
        for sessionDirectory in sessionDirectories {
            let foundErrors = try BundleSchemaTests().validateBundle(at: sessionDirectory)
                .filter { !$0.contains("count") }  // the open session's manifest lags by up to a minute
            #expect(foundErrors == [], "\(sessionDirectory.lastPathComponent)")
        }
    }
}
