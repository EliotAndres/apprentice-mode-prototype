import AppKit
import ApprenticeCore

// One binary, two modes: no arguments runs the recorder; a subcommand talks to the running one.
let locations = DataLocations.resolve()
let commandArguments = CommandLine.arguments.dropFirst().filter { !$0.hasPrefix("-psn_") }

if !commandArguments.isEmpty {
    exit(ControlClient.run(arguments: Array(commandArguments), socketPath: locations.controlSocket.path))
}

try? FileManager.default.createDirectory(at: locations.root, withIntermediateDirectories: true)

// Single instance: an flock held for the life of the process (released by the kernel even on a crash).
let lockDescriptor = open(locations.processLock.path, O_CREAT | O_RDWR, 0o600)
if lockDescriptor < 0 || flock(lockDescriptor, LOCK_EX | LOCK_NB) != 0 {
    let runningProcessIdentifier = (try? String(contentsOf: locations.processLock, encoding: .utf8))?.trimmingCharacters(in: .whitespacesAndNewlines)
    ControlClient.printJSON(["ok": false, "error": "already running", "pid": runningProcessIdentifier as Any])
    exit(1)
}
ftruncate(lockDescriptor, 0)
_ = "\(getpid())\n".withCString { write(lockDescriptor, $0, strlen($0)) }

signal(SIGHUP, SIG_IGN)  // survive the terminal / Claude session that started us

let application = NSApplication.shared
application.setActivationPolicy(.accessory)
let engine = RecorderEngine(locations: locations)
let menuController = MenuController(engine: engine)

func handleRequest(_ request: [String: Any]) -> [String: Any] {
    switch request["command"] as? String {
    case "status":
        return engine.statusSnapshot()
    case "pause":
        engine.pause(minutes: request["minutes"] as? Int)
    case "resume":
        engine.resume()
    case "new-session":
        engine.startNewSession()
    case "prune":
        let keepBytes = Int64((request["keep_gb"] as? Double ?? 0) * 1_000_000_000)
        let pruneResult = engine.prune(keepBytes: keepBytes)
        var response = engine.statusSnapshot()
        response["deleted_sessions"] = pruneResult.deletedSessions
        response["freed_bytes"] = pruneResult.freedBytes
        return response
    case "stop":
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.2) {
            engine.shutdown()
            exit(0)
        }
        var response = engine.statusSnapshot()
        response["state"] = "stopping"
        return response
    case "debug-ax":
        return engine.recordingQueue.sync { engine.activityRecorder.describeFrontmostWindow() }
    default:
        return ["ok": false, "error": "unknown command"]
    }
    menuController.refreshStatusItem()
    return engine.statusSnapshot()
}

do { try ControlServer(socketPath: locations.controlSocket.path, handleRequest: handleRequest).start() }
catch { engine.recordError("control socket: \(error)") }

// File-based control for clients that can write files but not run commands (Cowork).
Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { _ in
    guard let data = try? Data(contentsOf: locations.commandRequest) else { return }
    try? FileManager.default.removeItem(at: locations.commandRequest)
    let request = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] ?? [:]
    let response = handleRequest(request)
    try? JSONSerialization.data(withJSONObject: response, options: [.prettyPrinted, .sortedKeys]).write(to: locations.commandResult)
}

// Install (or update) the Claude Code skill shipped in the bundle, so opening the app is the whole install.
// It also seeds AUTOMATE_TASKS.md in the data root, the prompt "Trigger Workflow Harvesting" sends to Claude Code; that copy is
// the user's to edit, so it's only written when missing.
if let bundledSkill = Bundle.main.url(forResource: "SKILL", withExtension: "md"),
   let skillContents = try? Data(contentsOf: bundledSkill) {
    let installedSkill = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent(".claude/skills/apprentice/SKILL.md")
    if (try? Data(contentsOf: installedSkill)) != skillContents {
        try? FileManager.default.createDirectory(at: installedSkill.deletingLastPathComponent(), withIntermediateDirectories: true)
        do { try skillContents.write(to: installedSkill) } catch { engine.recordError("skill install: \(error.localizedDescription)") }
    }
    if !FileManager.default.fileExists(atPath: engine.locations.automatePrompt.path) {
        try? FileManager.default.createDirectory(at: engine.locations.root, withIntermediateDirectories: true)
        try? skillContents.write(to: engine.locations.automatePrompt)
    }
}
// The feed page the skill fills in. App-owned, so it's kept in step with the bundle.
if let bundledFeed = Bundle.main.url(forResource: "feed", withExtension: "html"),
   let feedContents = try? Data(contentsOf: bundledFeed),
   (try? Data(contentsOf: engine.locations.feedPage)) != feedContents {
    try? FileManager.default.createDirectory(at: engine.locations.feedPage.deletingLastPathComponent(), withIntermediateDirectories: true)
    try? feedContents.write(to: engine.locations.feedPage)
}
engine.start()
application.run()
