import CoreImage
import Foundation
import ScreenCaptureKit

/// The latest captured frame plus the geometry needed to map AX frames onto it.
struct FrameSnapshot: @unchecked Sendable {
    let pixelBuffer: CVPixelBuffer
    let displayBounds: CGRect   // global points, top-left origin
    let pixelSize: CGSize
}

/// An SCStream on the display under the cursor at 1 fps (4 fps for 2 s after input), keeping only the
/// latest frame. Actions take the frame from *before* their input; JPEG encoding happens only then.
final class ScreenFrameSource: NSObject, SCStreamOutput, SCStreamDelegate, @unchecked Sendable {
    static let maximumScreenshotWidth = 1280
    static let restingFrameInterval = CMTime(value: 1, timescale: 1)
    static let boostedFrameInterval = CMTime(value: 1, timescale: 4)
    static let boostDuration: TimeInterval = 2

    var onError: (String) -> Void = { _ in }

    private let stateLock = NSLock()
    private var latestFrame: FrameSnapshot?
    private var framesSuppressed = false
    private var streamDisplayBounds = CGRect.zero
    private var streamPixelSize = CGSize.zero

    // Main-thread state.
    private var stream: SCStream?
    private var streamDisplayIdentifier: CGDirectDisplayID?
    private var streamConfiguration: SCStreamConfiguration?
    private var excludedBundleIdentifiers: Set<String> = []
    private var isRunning = false
    private var boostedUntil = Date.distantPast
    private var isBoosted = false
    private var restartGeneration = 0

    private let sampleHandlerQueue = DispatchQueue(label: "apprentice.screen-frames")
    private let imageContext = CIContext(options: [.cacheIntermediates: false])

    // MARK: main thread

    func start() {
        isRunning = true
        restartStream(onDisplay: Self.displayUnderCursor())
    }

    func stop() {
        isRunning = false
        restartGeneration += 1
        let stoppingStream = stream
        stream = nil
        streamDisplayIdentifier = nil
        Task { try? await stoppingStream?.stopCapture() }
        stateLock.withLock { latestFrame = nil }
    }

    /// While an excluded app or a private window is frontmost, frames are dropped and the held one discarded.
    func setFramesSuppressed(_ suppressed: Bool) {
        stateLock.withLock {
            framesSuppressed = suppressed
            if suppressed { latestFrame = nil }
        }
    }

    func setExcludedApplications(_ bundleIdentifiers: Set<String>) {
        guard bundleIdentifiers != excludedBundleIdentifiers else { return }
        excludedBundleIdentifiers = bundleIdentifiers
        if isRunning { restartStream(onDisplay: streamDisplayIdentifier ?? Self.displayUnderCursor()) }
    }

    /// Called on every input: follow the cursor to its display and capture faster for a moment.
    func noteInput() {
        guard isRunning else { return }
        let cursorDisplay = Self.displayUnderCursor()
        if cursorDisplay != streamDisplayIdentifier { return restartStream(onDisplay: cursorDisplay) }
        boostedUntil = Date().addingTimeInterval(Self.boostDuration)
        guard !isBoosted, let stream, let streamConfiguration else { return }
        isBoosted = true
        streamConfiguration.minimumFrameInterval = Self.boostedFrameInterval
        stream.updateConfiguration(streamConfiguration) { _ in }
        scheduleBoostEnd()
    }

    func takeLatestFrame() -> FrameSnapshot? { stateLock.withLock { latestFrame } }

    // MARK: any thread

    func jpegData(for snapshot: FrameSnapshot) -> Data? {
        let image = CIImage(cvPixelBuffer: snapshot.pixelBuffer)
        return imageContext.jpegRepresentation(
            of: image, colorSpace: CGColorSpace(name: CGColorSpace.sRGB)!,
            options: [CIImageRepresentationOption(rawValue: kCGImageDestinationLossyCompressionQuality as String): 0.6])
    }

    static func displayUnderCursor() -> CGDirectDisplayID {
        let cursorLocation = CGEvent(source: nil)?.location ?? .zero
        var displayIdentifier: CGDirectDisplayID = 0
        var displayCount: UInt32 = 0
        CGGetDisplaysWithPoint(cursorLocation, 1, &displayIdentifier, &displayCount)
        return displayCount > 0 ? displayIdentifier : CGMainDisplayID()
    }

    // MARK: private

    private func scheduleBoostEnd() {
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.5) { [weak self] in
            guard let self, isBoosted else { return }
            if Date() < boostedUntil { return scheduleBoostEnd() }
            isBoosted = false
            guard let stream, let streamConfiguration else { return }
            streamConfiguration.minimumFrameInterval = Self.restingFrameInterval
            stream.updateConfiguration(streamConfiguration) { _ in }
        }
    }

    private func restartStream(onDisplay displayIdentifier: CGDirectDisplayID) {
        restartGeneration += 1
        let generation = restartGeneration
        let previousStream = stream
        stream = nil
        streamDisplayIdentifier = displayIdentifier
        isBoosted = false
        let excludedBundleIdentifiers = excludedBundleIdentifiers
        Task { @MainActor in
            try? await previousStream?.stopCapture()
            do {
                let shareableContent = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
                guard generation == restartGeneration else { return }
                guard let display = shareableContent.displays.first(where: { $0.displayID == displayIdentifier })
                    ?? shareableContent.displays.first else { return onError("no display to capture") }
                // Our own UI and excluded apps never reach a frame.
                let applicationsToExclude = shareableContent.applications.filter { application in
                    application.processID == getpid() || excludedBundleIdentifiers.contains(application.bundleIdentifier)
                }
                let contentFilter = SCContentFilter(display: display, excludingApplications: applicationsToExclude, exceptingWindows: [])
                let displayMode = CGDisplayCopyDisplayMode(display.displayID)
                let nativePixelWidth = displayMode?.pixelWidth ?? display.width
                let screenshotWidth = min(Self.maximumScreenshotWidth, nativePixelWidth)
                let screenshotHeight = Int((Double(screenshotWidth) * Double(display.height) / Double(display.width)).rounded())
                let configuration = SCStreamConfiguration()
                configuration.width = screenshotWidth
                configuration.height = screenshotHeight
                configuration.minimumFrameInterval = Self.restingFrameInterval
                configuration.queueDepth = 3
                configuration.showsCursor = true
                let newStream = SCStream(filter: contentFilter, configuration: configuration, delegate: self)
                try newStream.addStreamOutput(self, type: .screen, sampleHandlerQueue: sampleHandlerQueue)
                stateLock.withLock {
                    latestFrame = nil
                    streamDisplayBounds = CGDisplayBounds(display.displayID)
                    streamPixelSize = CGSize(width: screenshotWidth, height: screenshotHeight)
                }
                try await newStream.startCapture()
                guard generation == restartGeneration else { try? await newStream.stopCapture(); return }
                stream = newStream
                streamConfiguration = configuration
            } catch {
                onError("screen capture: \(error.localizedDescription)")
            }
        }
    }

    // MARK: SCStreamOutput / SCStreamDelegate (sample handler queue)

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer, of outputType: SCStreamOutputType) {
        guard outputType == .screen, sampleBuffer.isValid,
              let attachments = CMSampleBufferGetSampleAttachmentsArray(sampleBuffer, createIfNecessary: false) as? [[SCStreamFrameInfo: Any]],
              let statusValue = attachments.first?[.status] as? Int, SCFrameStatus(rawValue: statusValue) == .complete,
              let pixelBuffer = sampleBuffer.imageBuffer else { return }
        stateLock.withLock {
            guard !framesSuppressed else { return }
            latestFrame = FrameSnapshot(pixelBuffer: pixelBuffer, displayBounds: streamDisplayBounds, pixelSize: streamPixelSize)
        }
    }

    func stream(_ stoppedStream: SCStream, didStopWithError error: Error) {
        DispatchQueue.main.async { [weak self] in
            guard let self, stoppedStream === stream else { return }
            stream = nil
            streamDisplayIdentifier = nil
            stateLock.withLock { self.latestFrame = nil }
            onError("screen capture stopped: \(error.localizedDescription)")
        }
    }

    /// The permission poll calls this: restart a stream that died (e.g. after a revoked grant came back).
    func restartIfStopped() {
        if isRunning && stream == nil { restartStream(onDisplay: Self.displayUnderCursor()) }
    }
}
