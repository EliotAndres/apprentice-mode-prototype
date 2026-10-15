import Foundation

public let recorderVersion = "0.1.0"
public let manifestSchemaVersion = "0.2"

public enum ActionOperation: String, Codable, Sendable {
    case click = "CLICK", type = "TYPE", select = "SELECT", hover = "HOVER", enter = "ENTER", key = "KEY"
}

public struct ElementRecord: Codable, Equatable, Sendable {
    public var tag: String
    public var role: String
    public var text: String
    /// `[x, y, width, height]` in screenshot pixels.
    public var bbox: [Double]?

    public init(tag: String = "", role: String, text: String, bbox: [Double]? = nil) {
        self.tag = tag
        self.role = role
        self.text = text
        self.bbox = bbox
    }
}

/// What the recorder knows about an action before the session writer assigns `seq` and `t`.
public struct ActionDraft: Sendable {
    public var operation: ActionOperation
    public var value: String
    public var site: String
    public var url: String?
    public var appBundleIdentifier: String
    public var windowTitle: String
    public var element: ElementRecord
    /// Monotonic clock (seconds since boot) at which the input happened.
    public var occurredAtUptime: Double

    public init(operation: ActionOperation, value: String, site: String, url: String?, appBundleIdentifier: String,
                windowTitle: String, element: ElementRecord, occurredAtUptime: Double) {
        self.operation = operation
        self.value = value
        self.site = site
        self.url = url
        self.appBundleIdentifier = appBundleIdentifier
        self.windowTitle = windowTitle
        self.element = element
        self.occurredAtUptime = occurredAtUptime
    }
}

/// One line of `traces/dom.jsonl`.
public struct ActionRecord: Encodable, Sendable {
    public var seq: Int
    public var t: Double
    public var op: ActionOperation
    public var value: String
    public var site: String
    public var url: String?
    public var app: String
    public var window: String
    public var element: ElementRecord
    public var screenshot: String?

    enum CodingKeys: String, CodingKey { case seq, t, type, op, value, site, url, app, window, element, screenshot, dom_snapshot }

    public func encode(to encoder: Encoder) throws {
        var container = encoder.container(keyedBy: CodingKeys.self)
        try container.encode(seq, forKey: .seq)
        try container.encode(t, forKey: .t)
        try container.encode("action", forKey: .type)
        try container.encode(op, forKey: .op)
        try container.encode(value, forKey: .value)
        try container.encode(site, forKey: .site)
        try container.encode(url, forKey: .url)         // explicit null when absent
        try container.encode(app, forKey: .app)
        try container.encode(window, forKey: .window)
        try container.encode(element, forKey: .element)
        try container.encode(screenshot, forKey: .screenshot)
        try container.encodeNil(forKey: .dom_snapshot)
    }
}

public enum ContextEventKind: String, Codable, Sendable {
    case appActivated = "app_activated"
    case windowChanged = "window_changed"
    case urlChanged = "url_changed"
    case idleStart = "idle_start"
    case idleEnd = "idle_end"
    case screenLocked = "screen_locked"
    case screenUnlocked = "screen_unlocked"
    case excludedAppActive = "excluded_app_active"
}

/// One line of `traces/context.jsonl`. For `excluded_app_active` every optional field stays empty.
public struct ContextDraft: Sendable {
    public var event: ContextEventKind
    public var appBundleIdentifier: String?
    public var appName: String?
    public var windowTitle: String?
    public var url: String?
    public var site: String?
    public var occurredAtUptime: Double

    public init(event: ContextEventKind, appBundleIdentifier: String? = nil, appName: String? = nil, windowTitle: String? = nil,
                url: String? = nil, site: String? = nil, occurredAtUptime: Double) {
        self.event = event
        self.appBundleIdentifier = appBundleIdentifier
        self.appName = appName
        self.windowTitle = windowTitle
        self.url = url
        self.site = site
        self.occurredAtUptime = occurredAtUptime
    }
}

struct ContextRecord: Encodable {
    var t: Double
    var event: ContextEventKind
    var app: String?
    var app_name: String?
    var window: String?
    var url: String?
    var site: String?
}

public struct DisplayDescription: Codable, Equatable, Sendable {
    public var id: Int
    public var width_px: Int
    public var height_px: Int
    public var scale: Double

    public init(id: Int, widthPixels: Int, heightPixels: Int, scale: Double) {
        self.id = id
        self.width_px = widthPixels
        self.height_px = heightPixels
        self.scale = scale
    }
}

public struct SessionManifest: Codable, Sendable {
    public struct Source: Codable, Sendable { public var kind: String; public var recorder_version: String; public var host_app: String }
    public struct Timing: Codable, Sendable { public var kind: String }
    public struct Stream: Codable, Sendable { public var path: String; public var format: String; public var count: Int }
    public struct Streams: Codable, Sendable { public var dom: Stream; public var screenshots: Stream; public var accessibility: Stream }
    public struct Counts: Codable, Sendable { public var actions: Int; public var sites: Int }

    public var schema_version: String
    public var session_id: String
    public var source: Source
    public var started_at: String
    public var duration_s: Double
    public var timing: Timing
    public var streams: Streams
    public var counts: Counts
    public var displays: [DisplayDescription]
}

public func roundedToMilliseconds(_ seconds: Double) -> Double { (seconds * 1000).rounded() / 1000 }

let jsonLineEncoder: JSONEncoder = {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
    return encoder
}()
