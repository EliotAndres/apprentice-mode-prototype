import Foundation

public protocol RecorderClock: AnyObject {
    var now: Date { get }
    /// Monotonic seconds (since boot), the same clock as input event timestamps.
    var uptime: Double { get }
}

public final class SystemClock: RecorderClock {
    public init() {}
    public var now: Date { Date() }
    public var uptime: Double { ProcessInfo.processInfo.systemUptime }
}

public enum SessionSplitReason: String, Sendable {
    case idle, screenLock = "screen_lock", midnight, restart, manual
}

public enum SessionPolicy {
    public static let idleSplitSeconds: Double = 600

    /// Whether the event about to be written belongs to a new session. `nil` session start = no open session.
    public static func splitReason(sessionStartedAt: Date?, lastInputAt: Date?, now: Date,
                                   lockedSinceSessionStart: Bool, calendar: Calendar) -> SessionSplitReason? {
        guard let sessionStartedAt else { return .restart }
        if lockedSinceSessionStart { return .screenLock }
        if let lastInputAt, now.timeIntervalSince(lastInputAt) >= idleSplitSeconds { return .idle }
        if !calendar.isDate(sessionStartedAt, inSameDayAs: now) { return .midnight }
        return nil
    }

    public static func sessionIdentifier(startingAt startDate: Date, calendar: Calendar, isTaken: (String) -> Bool) -> String {
        let formatter = DateFormatter()
        formatter.calendar = calendar
        formatter.timeZone = calendar.timeZone
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        let baseIdentifier = formatter.string(from: startDate)
        var candidate = baseIdentifier
        var suffixNumber = 1
        while isTaken(candidate) { candidate = "\(baseIdentifier)-\(suffixNumber)"; suffixNumber += 1 }
        return candidate
    }
}

/// Writes session bundles: `sessions/<id>/{manifest.json, traces/dom.jsonl, traces/context.jsonl, screenshots/}`.
/// Not thread safe: call it from one queue.
public final class SessionWriter {
    public let sessionsDirectory: URL
    private let clock: RecorderClock
    private let calendar: Calendar
    private let hostApplicationIdentifier: String
    private let displaysProvider: () -> [DisplayDescription]
    /// (session id, bytes, is an action) after each write, for live storage accounting.
    public var onBytesWritten: (String, Int64, Bool) -> Void = { _, _, _ in }

    private struct OpenSession {
        var identifier: String
        var directory: URL
        var startedAt: Date
        var startedAtUptime: Double
        var actionLog: FileHandle
        var contextLog: FileHandle
        var nextSequenceNumber = 0
        var lastActionOffset: Double = 0
        var lastEventOffset: Double = 0
        var screenshotCount = 0
        var contextCount = 0
        var sites: Set<String> = []
        var displays: [DisplayDescription]
        var lastManifestWriteUptime: Double
    }

    private var openSession: OpenSession?
    private var lastInputAt: Date?
    private var lockedSinceSessionStart = false

    public init(sessionsDirectory: URL, clock: RecorderClock, calendar: Calendar = .current,
                hostApplicationIdentifier: String, displaysProvider: @escaping () -> [DisplayDescription]) {
        self.sessionsDirectory = sessionsDirectory
        self.clock = clock
        self.calendar = calendar
        self.hostApplicationIdentifier = hostApplicationIdentifier
        self.displaysProvider = displaysProvider
    }

    public var currentSessionIdentifier: String? { openSession?.identifier }
    public var currentSessionActionCount: Int { openSession?.nextSequenceNumber ?? 0 }
    public var currentSessionStartedAt: Date? { openSession?.startedAt }

    /// Call on every user input, before writing what it produced. Splits after `idleSplitSeconds` without input.
    public func noteUserInput() {
        splitIfNeeded()
        lastInputAt = clock.now
    }

    @discardableResult
    public func appendAction(_ draft: ActionDraft, screenshotJPEG: Data?) -> ActionRecord? {
        guard var session = sessionForWriting() else { return nil }
        let timeOffset = max(session.lastActionOffset, roundedToMilliseconds(draft.occurredAtUptime - session.startedAtUptime))
        let screenshotPath = screenshotJPEG.map { _ in String(format: "screenshots/%05d.jpg", session.nextSequenceNumber) }
        let record = ActionRecord(seq: session.nextSequenceNumber, t: timeOffset, op: draft.operation, value: draft.value,
                                  site: draft.site, url: draft.url, app: draft.appBundleIdentifier, window: draft.windowTitle,
                                  element: draft.element, screenshot: screenshotPath)
        var writtenBytes: Int64 = 0
        if let screenshotJPEG, let screenshotPath {
            if (try? screenshotJPEG.write(to: session.directory.appendingPathComponent(screenshotPath))) != nil {
                session.screenshotCount += 1
                writtenBytes += Int64(screenshotJPEG.count)
            }
        }
        writtenBytes += appendLine(record, to: session.actionLog)
        session.nextSequenceNumber += 1
        session.lastActionOffset = timeOffset
        session.lastEventOffset = max(session.lastEventOffset, timeOffset)
        session.sites.insert(draft.site)
        openSession = session
        onBytesWritten(session.identifier, writtenBytes, true)
        return record
    }

    public func appendContext(_ draft: ContextDraft) {
        guard var session = sessionForWriting() else { return }
        let timeOffset = max(session.lastEventOffset, roundedToMilliseconds(draft.occurredAtUptime - session.startedAtUptime))
        let record = ContextRecord(t: timeOffset, event: draft.event, app: draft.appBundleIdentifier, app_name: draft.appName,
                                   window: draft.windowTitle, url: draft.url, site: draft.site)
        let writtenBytes = appendLine(record, to: session.contextLog)
        session.contextCount += 1
        session.lastEventOffset = timeOffset
        openSession = session
        onBytesWritten(session.identifier, writtenBytes, false)
    }

    /// Lock ends the session; the next event (e.g. the unlock) starts a new one.
    public func screenLocked(at uptime: Double) {
        appendContext(ContextDraft(event: .screenLocked, occurredAtUptime: uptime))
        endSession()
        lockedSinceSessionStart = true
    }

    /// Every minute: rewrite the manifest so a crash leaves a valid bundle. Also splits at midnight.
    public func periodicMaintenance() {
        guard let session = openSession else { return }
        if !calendar.isDate(session.startedAt, inSameDayAs: clock.now) { return endSession() }
        if clock.uptime - session.lastManifestWriteUptime >= 60 { writeManifest() }
    }

    public func endSession() {
        guard let session = openSession else { return }
        writeManifest()
        try? session.actionLog.close()
        try? session.contextLog.close()
        openSession = nil
    }

    // MARK: private

    private func splitIfNeeded() {
        let reason = SessionPolicy.splitReason(sessionStartedAt: openSession?.startedAt, lastInputAt: lastInputAt, now: clock.now,
                                               lockedSinceSessionStart: lockedSinceSessionStart, calendar: calendar)
        if reason != nil && reason != .restart { endSession() }
    }

    private func sessionForWriting() -> OpenSession? {
        splitIfNeeded()
        if openSession == nil { startSession() }
        return openSession
    }

    private func startSession() {
        let startedAt = clock.now
        let fileManager = FileManager.default
        let identifier = SessionPolicy.sessionIdentifier(startingAt: startedAt, calendar: calendar) { candidate in
            fileManager.fileExists(atPath: sessionsDirectory.appendingPathComponent(candidate).path)
        }
        let sessionDirectory = sessionsDirectory.appendingPathComponent(identifier)
        do {
            try fileManager.createDirectory(at: sessionDirectory.appendingPathComponent("traces"), withIntermediateDirectories: true)
            try fileManager.createDirectory(at: sessionDirectory.appendingPathComponent("screenshots"), withIntermediateDirectories: true)
            let actionLogURL = sessionDirectory.appendingPathComponent("traces/dom.jsonl")
            let contextLogURL = sessionDirectory.appendingPathComponent("traces/context.jsonl")
            fileManager.createFile(atPath: actionLogURL.path, contents: nil)
            fileManager.createFile(atPath: contextLogURL.path, contents: nil)
            openSession = OpenSession(identifier: identifier, directory: sessionDirectory, startedAt: startedAt,
                                      startedAtUptime: clock.uptime,
                                      actionLog: try FileHandle(forWritingTo: actionLogURL),
                                      contextLog: try FileHandle(forWritingTo: contextLogURL),
                                      displays: displaysProvider(), lastManifestWriteUptime: clock.uptime)
            lockedSinceSessionStart = false
            writeManifest()
        } catch {
            openSession = nil
        }
    }

    private func appendLine<Record: Encodable>(_ record: Record, to fileHandle: FileHandle) -> Int64 {
        guard var line = try? jsonLineEncoder.encode(record) else { return 0 }
        line.append(0x0A)
        fileHandle.write(line)
        return Int64(line.count)
    }

    private func writeManifest() {
        guard var session = openSession else { return }
        let manifest = SessionManifest(
            schema_version: manifestSchemaVersion,
            session_id: session.identifier,
            source: .init(kind: "macos_recorder", recorder_version: recorderVersion, host_app: hostApplicationIdentifier),
            started_at: Self.timestampFormatter(calendar: calendar).string(from: session.startedAt),
            duration_s: session.lastEventOffset,
            timing: .init(kind: "observed"),
            streams: .init(
                dom: .init(path: "traces/dom.jsonl", format: "jsonl", count: session.nextSequenceNumber),
                screenshots: .init(path: "screenshots/", format: "jpeg max 1280w", count: session.screenshotCount),
                accessibility: .init(path: "traces/context.jsonl", format: "jsonl", count: session.contextCount)),
            counts: .init(actions: session.nextSequenceNumber, sites: session.sites.count),
            displays: session.displays)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        guard let manifestData = try? encoder.encode(manifest) else { return }
        // .atomic = temp file + rename, so a crash never leaves a half-written manifest.
        try? manifestData.write(to: session.directory.appendingPathComponent("manifest.json"), options: .atomic)
        session.lastManifestWriteUptime = clock.uptime
        openSession = session
    }

    static func timestampFormatter(calendar: Calendar) -> ISO8601DateFormatter {
        let formatter = ISO8601DateFormatter()
        formatter.timeZone = calendar.timeZone
        formatter.formatOptions = [.withInternetDateTime]
        return formatter
    }
}
