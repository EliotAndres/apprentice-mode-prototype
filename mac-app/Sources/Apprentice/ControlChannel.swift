import Foundation

/// One JSON request line in, one JSON response line out, over a Unix socket in the data root.
enum ControlChannel {
    static func makeAddress(socketPath: String) -> sockaddr_un? {
        var address = sockaddr_un()
        address.sun_family = sa_family_t(AF_UNIX)
        let pathBytes = Array(socketPath.utf8)
        guard pathBytes.count < MemoryLayout.size(ofValue: address.sun_path) else { return nil }
        withUnsafeMutableBytes(of: &address.sun_path) { pathBuffer in
            pathBuffer.copyBytes(from: pathBytes)
            pathBuffer[pathBytes.count] = 0
        }
        return address
    }

    static func readLine(from socketDescriptor: Int32) -> Data {
        var receivedData = Data()
        var readBuffer = [UInt8](repeating: 0, count: 4096)
        while !receivedData.contains(0x0A) {
            let readCount = read(socketDescriptor, &readBuffer, readBuffer.count)
            if readCount <= 0 { break }
            receivedData.append(contentsOf: readBuffer[0..<readCount])
        }
        return receivedData
    }

    static func writeJSON(_ object: [String: Any], to socketDescriptor: Int32) {
        guard var encodedLine = try? JSONSerialization.data(withJSONObject: object, options: [.sortedKeys]) else { return }
        encodedLine.append(0x0A)
        encodedLine.withUnsafeBytes { lineBuffer in _ = write(socketDescriptor, lineBuffer.baseAddress, lineBuffer.count) }
    }
}

/// Recorder side: accepts connections on a background thread, runs each command on the main thread.
final class ControlServer {
    private let socketPath: String
    private let handleRequest: ([String: Any]) -> [String: Any]
    private var listeningDescriptor: Int32 = -1

    init(socketPath: String, handleRequest: @escaping ([String: Any]) -> [String: Any]) {
        self.socketPath = socketPath
        self.handleRequest = handleRequest
    }

    func start() throws {
        unlink(socketPath)  // stale socket from a crash; the pid lock already guarantees we're the only recorder
        guard var address = ControlChannel.makeAddress(socketPath: socketPath) else { throw POSIXError(.ENAMETOOLONG) }
        listeningDescriptor = socket(AF_UNIX, SOCK_STREAM, 0)
        let bindResult = withUnsafePointer(to: &address) { addressPointer in
            addressPointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { bind(listeningDescriptor, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
        }
        guard listeningDescriptor >= 0, bindResult == 0, listen(listeningDescriptor, 8) == 0 else { throw POSIXError(.init(rawValue: errno) ?? .EIO) }
        chmod(socketPath, 0o600)
        let acceptThread = Thread { [self] in acceptLoop() }
        acceptThread.name = "apprentice.control"
        acceptThread.start()
    }

    private func acceptLoop() {
        while true {
            let connectionDescriptor = accept(listeningDescriptor, nil, nil)
            guard connectionDescriptor >= 0 else { continue }
            let requestLine = ControlChannel.readLine(from: connectionDescriptor)
            let request = (try? JSONSerialization.jsonObject(with: requestLine)) as? [String: Any] ?? [:]
            let response = DispatchQueue.main.sync { handleRequest(request) }
            ControlChannel.writeJSON(response, to: connectionDescriptor)
            close(connectionDescriptor)
        }
    }
}

/// CLI side: `Apprentice status | pause [--minutes N] | resume | stop | new-session | prune --keep-gb N`.
enum ControlClient {
    static let usage = "usage: Apprentice [status | pause [--minutes N] | resume | stop | new-session | prune --keep-gb N]"

    /// Returns the process exit code.
    static func run(arguments: [String], socketPath: String) -> Int32 {
        guard let request = parse(arguments) else {
            printJSON(["ok": false, "error": usage])
            return 2
        }
        guard let response = send(request, socketPath: socketPath) else {
            printJSON(["ok": false, "error": "recorder not running", "socket": socketPath])
            return 1
        }
        printJSON(response)
        return response["ok"] as? Bool == false ? 1 : 0
    }

    static func parse(_ arguments: [String]) -> [String: Any]? {
        guard let command = arguments.first else { return nil }
        func numberAfter(_ flag: String) -> Double? {
            guard let flagIndex = arguments.firstIndex(of: flag), flagIndex + 1 < arguments.count else { return nil }
            return Double(arguments[flagIndex + 1])
        }
        switch command {
        case "status", "resume", "stop", "new-session", "debug-ax":
            return ["command": command]
        case "pause":
            var request: [String: Any] = ["command": "pause"]
            if arguments.contains("--minutes") {
                guard let minutes = numberAfter("--minutes"), minutes > 0 else { return nil }
                request["minutes"] = Int(minutes)
            }
            return request
        case "prune":
            guard let keepGigabytes = numberAfter("--keep-gb"), keepGigabytes >= 0 else { return nil }
            return ["command": "prune", "keep_gb": keepGigabytes]
        default:
            return nil
        }
    }

    static func send(_ request: [String: Any], socketPath: String) -> [String: Any]? {
        guard var address = ControlChannel.makeAddress(socketPath: socketPath) else { return nil }
        let socketDescriptor = socket(AF_UNIX, SOCK_STREAM, 0)
        defer { close(socketDescriptor) }
        var timeout = timeval(tv_sec: 10, tv_usec: 0)
        setsockopt(socketDescriptor, SOL_SOCKET, SO_RCVTIMEO, &timeout, socklen_t(MemoryLayout<timeval>.size))
        let connectResult = withUnsafePointer(to: &address) { addressPointer in
            addressPointer.withMemoryRebound(to: sockaddr.self, capacity: 1) { connect(socketDescriptor, $0, socklen_t(MemoryLayout<sockaddr_un>.size)) }
        }
        guard connectResult == 0 else { return nil }
        ControlChannel.writeJSON(request, to: socketDescriptor)
        return (try? JSONSerialization.jsonObject(with: ControlChannel.readLine(from: socketDescriptor))) as? [String: Any]
    }

    static func printJSON(_ object: [String: Any]) {
        let encoded = (try? JSONSerialization.data(withJSONObject: object, options: [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes])) ?? Data()
        print(String(decoding: encoded, as: UTF8.self))
    }
}
