import Foundation

public struct SessionUsage: Equatable, Sendable {
    public var identifier: String
    public var startedAt: Date
    public var bytes: Int64
    public var actions: Int

    public init(identifier: String, startedAt: Date, bytes: Int64, actions: Int) {
        self.identifier = identifier
        self.startedAt = startedAt
        self.bytes = bytes
        self.actions = actions
    }
}

public enum StorageLevel: String, Sendable { case normal, nearCap = "near_cap", full }

public enum StoragePolicy {
    public static let nearCapFraction = 0.8
    /// Pruning frees down to this fraction of the cap, so we don't prune again on the next write.
    public static let pruneTargetFraction = 0.9
    public static let pauseBelowFreeBytes: Int64 = 10_000_000_000
    public static let resumeAboveFreeBytes: Int64 = 15_000_000_000

    public static func level(totalBytes: Int64, capBytes: Int64) -> StorageLevel {
        if totalBytes >= capBytes { return .full }
        if Double(totalBytes) >= Double(capBytes) * nearCapFraction { return .nearCap }
        return .normal
    }

    /// Whole sessions to delete, oldest first: everything past retention, then the oldest until
    /// `targetBytes` is met. Never the session being written.
    public static func sessionsToPrune(_ sessions: [SessionUsage], targetBytes: Int64, retentionDays: Int, now: Date,
                                       protectedSessionIdentifier: String?) -> [SessionUsage] {
        let retentionCutoff = now.addingTimeInterval(-Double(retentionDays) * 86_400)
        var remainingBytes = sessions.reduce(0) { $0 + $1.bytes }
        var sessionsToDelete: [SessionUsage] = []
        for session in sessions.sorted(by: { $0.startedAt < $1.startedAt }) where session.identifier != protectedSessionIdentifier {
            guard session.startedAt < retentionCutoff || remainingBytes > targetBytes else { continue }
            sessionsToDelete.append(session)
            remainingBytes -= session.bytes
        }
        return sessionsToDelete
    }

    /// Low-disk pause with hysteresis: pause under 10 GB free, resume over 15 GB.
    public static func lowDiskPaused(currentlyPaused: Bool, freeBytes: Int64) -> Bool {
        currentlyPaused ? freeBytes <= resumeAboveFreeBytes : freeBytes < pauseBelowFreeBytes
    }
}

public struct DailyUsage: Equatable, Sendable {
    public var day: Date
    public var bytes: Int64
    public var actions: Int
}

/// Byte accounting for the sessions directory: live increments from the writer, reconciled with a real scan.
/// Not thread safe: call it from the recorder queue.
public final class StorageAccountant {
    public let sessionsDirectory: URL
    private let clock: RecorderClock
    private let calendar: Calendar
    public private(set) var sessions: [String: SessionUsage] = [:]

    public init(sessionsDirectory: URL, clock: RecorderClock, calendar: Calendar = .current) {
        self.sessionsDirectory = sessionsDirectory
        self.clock = clock
        self.calendar = calendar
    }

    public var totalBytes: Int64 { sessions.values.reduce(0) { $0 + $1.bytes } }

    public var todayBytes: Int64 { todaySessions.reduce(0) { $0 + $1.bytes } }
    public var todayActions: Int { todaySessions.reduce(0) { $0 + $1.actions } }
    private var todaySessions: [SessionUsage] { sessions.values.filter { calendar.isDate($0.startedAt, inSameDayAs: clock.now) } }

    public func lastDays(_ dayCount: Int) -> [DailyUsage] {
        let today = calendar.startOfDay(for: clock.now)
        return (0..<dayCount).compactMap { daysAgo in
            guard let day = calendar.date(byAdding: .day, value: -daysAgo, to: today) else { return nil }
            let daySessions = sessions.values.filter { calendar.isDate($0.startedAt, inSameDayAs: day) }
            return DailyUsage(day: day, bytes: daySessions.reduce(0) { $0 + $1.bytes }, actions: daySessions.reduce(0) { $0 + $1.actions })
        }
    }

    public func recordWrite(sessionIdentifier: String, bytes: Int64, isAction: Bool) {
        var usage = sessions[sessionIdentifier]
            ?? SessionUsage(identifier: sessionIdentifier, startedAt: Self.startDate(of: sessionIdentifier, calendar: calendar) ?? clock.now,
                            bytes: 0, actions: 0)
        usage.bytes += bytes
        if isAction { usage.actions += 1 }
        sessions[sessionIdentifier] = usage
    }

    /// Real directory scan; replaces the live counts. Action counts come from each dom.jsonl's line count.
    public func reconcile() {
        let fileManager = FileManager.default
        var scannedSessions: [String: SessionUsage] = [:]
        let sessionDirectories = (try? fileManager.contentsOfDirectory(at: sessionsDirectory, includingPropertiesForKeys: nil)) ?? []
        for sessionDirectory in sessionDirectories {
            let identifier = sessionDirectory.lastPathComponent
            guard let startedAt = Self.startDate(of: identifier, calendar: calendar) else { continue }
            var sessionBytes: Int64 = 0
            let fileEnumerator = fileManager.enumerator(at: sessionDirectory, includingPropertiesForKeys: [.fileAllocatedSizeKey, .fileSizeKey])
            while let fileURL = fileEnumerator?.nextObject() as? URL {
                let resourceValues = try? fileURL.resourceValues(forKeys: [.fileAllocatedSizeKey, .fileSizeKey])
                sessionBytes += Int64(resourceValues?.fileSize ?? 0)
            }
            let actionLog = (try? Data(contentsOf: sessionDirectory.appendingPathComponent("traces/dom.jsonl"))) ?? Data()
            scannedSessions[identifier] = SessionUsage(identifier: identifier, startedAt: startedAt, bytes: sessionBytes,
                                                       actions: actionLog.reduce(0) { $1 == 0x0A ? $0 + 1 : $0 })
        }
        sessions = scannedSessions
    }

    /// Deletes the given sessions' directories and forgets them. Returns bytes freed.
    @discardableResult
    public func delete(_ sessionsToDelete: [SessionUsage]) -> Int64 {
        var freedBytes: Int64 = 0
        for session in sessionsToDelete {
            if (try? FileManager.default.removeItem(at: sessionsDirectory.appendingPathComponent(session.identifier))) != nil {
                freedBytes += session.bytes
                sessions[session.identifier] = nil
            }
        }
        return freedBytes
    }

    /// `20261012-091500` (optionally with a `-N` suffix) → its local start date.
    public static func startDate(of sessionIdentifier: String, calendar: Calendar) -> Date? {
        let formatter = DateFormatter()
        formatter.calendar = calendar
        formatter.timeZone = calendar.timeZone
        formatter.locale = Locale(identifier: "en_US_POSIX")
        formatter.dateFormat = "yyyyMMdd-HHmmss"
        return formatter.date(from: String(sessionIdentifier.prefix(15)))
    }
}
