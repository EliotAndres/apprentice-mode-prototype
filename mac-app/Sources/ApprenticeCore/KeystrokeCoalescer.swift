import Foundation

/// A key press after classification, independent of AppKit.
public enum Keystroke: Equatable, Sendable {
    case characters(String)
    case deleteBackward
    case returnKey
    case tab
    case shortcut(String)  // "cmd+shift+t"
    case ignored           // arrows, escape, function keys, control-chords
}

public enum KeystrokeClassifier {
    static let namedKeys: [UInt16: String] = [
        36: "return", 76: "enter", 48: "tab", 49: "space", 51: "delete", 117: "forwarddelete", 53: "escape",
        123: "left", 124: "right", 125: "down", 126: "up", 115: "home", 119: "end", 116: "pageup", 121: "pagedown",
        122: "f1", 120: "f2", 99: "f3", 118: "f4", 96: "f5", 97: "f6", 98: "f7", 100: "f8", 101: "f9", 109: "f10", 103: "f11", 111: "f12",
    ]

    public static func classify(keyCode: UInt16, characters: String, charactersIgnoringModifiers: String,
                                command: Bool, control: Bool, option: Bool, shift: Bool) -> Keystroke {
        if command {
            var parts = ["cmd"]
            if control { parts.append("ctrl") }
            if option { parts.append("alt") }
            if shift { parts.append("shift") }
            parts.append(namedKeys[keyCode] ?? charactersIgnoringModifiers.lowercased())
            return .shortcut(parts.joined(separator: "+"))
        }
        if control { return .ignored }
        switch keyCode {
        case 36, 76: return .returnKey
        case 48: return .tab
        case 51: return .deleteBackward
        default: break
        }
        let printable = !characters.isEmpty && characters.unicodeScalars.allSatisfy { scalar in
            scalar.value >= 0x20 && scalar.value != 0x7F && !(0xF700...0xF8FF).contains(scalar.value)
        }
        return printable ? .characters(characters) : .ignored
    }
}

public struct CoalescedKeyAction<FieldIdentity> {
    public var operation: ActionOperation   // .type, .enter or .key
    public var field: FieldIdentity?
    /// TYPE: text rebuilt from key events ("[secure]" for secure fields). KEY: the shortcut. ENTER: "".
    public var value: String
    public var isSecure: Bool
    public var occurredAtUptime: Double
}

/// Coalesces keystrokes into one TYPE per field. A run ends on a field change, Enter, Tab,
/// a shortcut, a click (`flush`) or `idleTimeoutSeconds` without keys (`tick`).
public struct KeystrokeCoalescer<FieldIdentity: Equatable> {
    public static var idleTimeoutSeconds: Double { 3 }

    private var runField: FieldIdentity?
    private var runText = ""
    private var runIsSecure = false
    private var runStartedAt: Double?
    private var lastKeystrokeAt: Double = 0

    public init() {}

    public var hasOpenRun: Bool { runStartedAt != nil }

    /// `startedRun` tells the caller to keep the "before" screenshot and element for this new TYPE.
    public mutating func receive(_ keystroke: Keystroke, field: FieldIdentity?, fieldIsSecure: Bool,
                                 at uptime: Double) -> (actions: [CoalescedKeyAction<FieldIdentity>], startedRun: Bool) {
        var emittedActions: [CoalescedKeyAction<FieldIdentity>] = []
        if hasOpenRun, field != runField || uptime - lastKeystrokeAt >= Self.idleTimeoutSeconds {
            emittedActions += flush()
        }
        switch keystroke {
        case .ignored:
            return (emittedActions, false)
        case .tab:
            return (emittedActions + flush(), false)
        case .returnKey:
            emittedActions += flush()
            emittedActions.append(CoalescedKeyAction(operation: .enter, field: field, value: "", isSecure: fieldIsSecure, occurredAtUptime: uptime))
            return (emittedActions, false)
        case .shortcut(let shortcutName):
            emittedActions += flush()
            emittedActions.append(CoalescedKeyAction(operation: .key, field: field, value: shortcutName, isSecure: fieldIsSecure, occurredAtUptime: uptime))
            return (emittedActions, false)
        case .characters, .deleteBackward:
            var startedRun = false
            if !hasOpenRun {
                runStartedAt = uptime
                runField = field
                runText = ""
                runIsSecure = false
                startedRun = true
            }
            if fieldIsSecure { runIsSecure = true; runText = "" }
            lastKeystrokeAt = uptime
            if !runIsSecure {
                if case .characters(let typedCharacters) = keystroke { runText += typedCharacters }
                else if !runText.isEmpty { runText.removeLast() }
            }
            return (emittedActions, startedRun)
        }
    }

    /// Focus moved (e.g. after a click): end the run if it was in another field.
    public mutating func focusChanged(to field: FieldIdentity?) -> [CoalescedKeyAction<FieldIdentity>] {
        hasOpenRun && field != runField ? flush() : []
    }

    public mutating func tick(at uptime: Double) -> [CoalescedKeyAction<FieldIdentity>] {
        hasOpenRun && uptime - lastKeystrokeAt >= Self.idleTimeoutSeconds ? flush() : []
    }

    public mutating func flush() -> [CoalescedKeyAction<FieldIdentity>] {
        guard let startedAt = runStartedAt else { return [] }
        defer { runStartedAt = nil; runField = nil; runText = ""; runIsSecure = false }
        return [CoalescedKeyAction(operation: .type, field: runField, value: runIsSecure ? secureValuePlaceholder : runText,
                                   isSecure: runIsSecure, occurredAtUptime: startedAt)]
    }
}

public let secureValuePlaceholder = "[secure]"
