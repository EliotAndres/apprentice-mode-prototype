import Foundation

/// `config.json` in the data root. Missing keys fall back to the defaults.
public struct RecorderConfiguration: Codable, Equatable, Sendable {
    public var excluded_apps: [String]
    public var redaction_patterns: [String: String]
    public var cap_gb: Double
    public var retention_days: Int

    public static let defaults = RecorderConfiguration(
        excluded_apps: [
            "com.1password.1password", "com.agilebits.onepassword7",  // 1Password 8 / 7
            "com.apple.keychainaccess", "com.apple.systempreferences",  // Keychain Access, System Settings
            "com.apple.MobileSMS", "com.apple.FaceTime",                // Messages, FaceTime
        ],
        redaction_patterns: [
            "card": #"\b(?:\d[ -]?){12,18}\d\b"#,
            "iban": #"\b[A-Z]{2}\d{2}(?: ?[A-Z0-9]){11,30}\b"#,
            "ssn": #"\b\d{3}-\d{2}-\d{4}\b"#,
        ],
        cap_gb: 20,
        retention_days: 30)

    public init(excluded_apps: [String], redaction_patterns: [String: String], cap_gb: Double, retention_days: Int) {
        self.excluded_apps = excluded_apps
        self.redaction_patterns = redaction_patterns
        self.cap_gb = cap_gb
        self.retention_days = retention_days
    }

    public init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: CodingKeys.self)
        let fallback = Self.defaults
        excluded_apps = try container.decodeIfPresent([String].self, forKey: .excluded_apps) ?? fallback.excluded_apps
        redaction_patterns = try container.decodeIfPresent([String: String].self, forKey: .redaction_patterns) ?? fallback.redaction_patterns
        cap_gb = try container.decodeIfPresent(Double.self, forKey: .cap_gb) ?? fallback.cap_gb
        retention_days = try container.decodeIfPresent(Int.self, forKey: .retention_days) ?? fallback.retention_days
    }

    public var capBytes: Int64 { Int64(cap_gb * 1_000_000_000) }

    /// Reads `config.json`, writing the defaults first if it doesn't exist. A malformed file keeps the defaults.
    public static func load(from configurationFile: URL) -> (configuration: RecorderConfiguration, error: String?) {
        if !FileManager.default.fileExists(atPath: configurationFile.path) {
            try? defaults.save(to: configurationFile)
            return (defaults, nil)
        }
        do {
            return (try JSONDecoder().decode(RecorderConfiguration.self, from: Data(contentsOf: configurationFile)), nil)
        } catch {
            return (defaults, "config.json unreadable, using defaults: \(error.localizedDescription)")
        }
    }

    public func save(to configurationFile: URL) throws {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        try encoder.encode(self).write(to: configurationFile, options: .atomic)
    }
}

/// Replaces card numbers, IBANs and SSNs in typed values with `[redacted]`.
public struct Redactor: Sendable {
    public static let replacement = "[redacted]"
    private let expressions: [NSRegularExpression]

    public init(patterns: [String: String]) {
        expressions = patterns.keys.sorted().compactMap { patternName in try? NSRegularExpression(pattern: patterns[patternName]!) }
    }

    /// Matches of all patterns are found on the original text and merged, so overlapping
    /// patterns (an IBAN's digits also look like a card number) redact the whole span.
    public func redact(_ text: String) -> String {
        let fullRange = NSRange(text.startIndex..., in: text)
        let matchedRanges = expressions.flatMap { expression in expression.matches(in: text, range: fullRange).map(\.range) }
            .sorted { $0.location < $1.location }
        var mergedRanges: [NSRange] = []
        for matchedRange in matchedRanges {
            if let lastRange = mergedRanges.last, matchedRange.location <= NSMaxRange(lastRange) {
                mergedRanges[mergedRanges.count - 1] = NSUnionRange(lastRange, matchedRange)
            } else {
                mergedRanges.append(matchedRange)
            }
        }
        let redactedText = NSMutableString(string: text)
        for mergedRange in mergedRanges.reversed() { redactedText.replaceCharacters(in: mergedRange, with: Self.replacement) }
        return redactedText as String
    }
}

public enum PrivateBrowsing {
    // Observed and assumed markers, see NOTES.md.
    static let windowTitleMarkers = ["Incognito", "Private Browsing", "InPrivate", "Private Window"]

    public static func isPrivateWindow(appBundleIdentifier: String, windowTitle: String, windowLabels: [String] = []) -> Bool {
        guard SiteNaming.browserBundleIdentifiers.contains(appBundleIdentifier) else { return false }
        return (windowLabels + [windowTitle]).contains { label in
            windowTitleMarkers.contains { marker in label.localizedCaseInsensitiveContains(marker) }
        }
    }
}
