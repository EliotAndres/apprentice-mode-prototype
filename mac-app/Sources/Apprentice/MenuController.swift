import AppKit
import ApprenticeCore
import Carbon.HIToolbox
import ServiceManagement

/// The status item: Claude icon, a short menu, ⌃⌥⌘P hotkey and its HUD.
final class MenuController: NSObject, NSMenuDelegate {
    let engine: RecorderEngine
    let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private var hudPanel: NSPanel?

    init(engine: RecorderEngine) {
        self.engine = engine
        super.init()
        let menu = NSMenu()
        menu.delegate = self
        statusItem.menu = menu
        engine.onStateChange = { [weak self] in self?.refreshStatusItem() }
        // Nothing is recorded while an excluded app is frontmost, so the recording dot goes away then.
        NSWorkspace.shared.notificationCenter.addObserver(forName: NSWorkspace.didActivateApplicationNotification, object: nil,
                                                          queue: .main) { [weak self] _ in self?.refreshStatusItem() }
        registerPauseHotkey { [weak self] in self?.togglePauseFromHotkey() }
        // Open at login by default; once the user turns it off, leave it off.
        if !UserDefaults.standard.bool(forKey: "loginItemConfigured") {
            try? SMAppService.mainApp.register()
            UserDefaults.standard.set(true, forKey: "loginItemConfigured")
        }
        refreshStatusItem()
    }

    func refreshStatusItem() {
        guard let button = statusItem.button else { return }
        let frontmostIsExcluded = NSWorkspace.shared.frontmostApplication?.bundleIdentifier
            .map(engine.configuration.excluded_apps.contains) ?? false
        button.image = engine.stateName == "recording" && !frontmostIsExcluded ? Self.recordingIcon : Self.claudeIcon
        button.toolTip = !engine.permissions.allGranted ? "Apprentice: missing permission"
            : engine.lowDiskPaused || engine.storageFullPaused ? "Apprentice: paused, storage"
            : engine.isPaused ? "Apprentice Mode is off" : "Apprentice Mode is on"
        // Off reads as a dimmed icon; a problem (permission, storage) tints it orange.
        button.appearsDisabled = engine.isPaused && engine.permissions.allGranted
        let needsAttention = !engine.permissions.allGranted || engine.storageLevel != .normal || engine.lowDiskPaused
        button.contentTintColor = needsAttention ? .systemOrange : nil
    }

    /// The Claude spark, as a template image so it follows the menu bar's light/dark appearance.
    private static let claudeIcon: NSImage = {
        let svg = #"<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path fill-rule="evenodd" d="M4.709 15.955l4.72-2.647.08-.23-.08-.128H9.2l-.79-.048-2.698-.073-2.339-.097-2.266-.122-.571-.121L0 11.784l.055-.352.48-.321.686.06 1.52.103 2.278.158 1.652.097 2.449.255h.389l.055-.157-.134-.098-.103-.097-2.358-1.596-2.552-1.688-1.336-.972-.724-.491-.364-.462-.158-1.008.656-.722.881.06.225.061.893.686 1.908 1.476 2.491 1.833.365.304.145-.103.019-.073-.164-.274-1.355-2.446-1.446-2.49-.644-1.032-.17-.619a2.97 2.97 0 01-.104-.729L6.283.134 6.696 0l.996.134.42.364.62 1.414 1.002 2.229 1.555 3.03.456.898.243.832.091.255h.158V9.01l.128-1.706.237-2.095.23-2.695.08-.76.376-.91.747-.492.584.28.48.685-.067.444-.286 1.851-.559 2.903-.364 1.942h.212l.243-.242.985-1.306 1.652-2.064.73-.82.85-.904.547-.431h1.033l.76 1.129-.34 1.166-1.064 1.347-.881 1.142-1.264 1.7-.79 1.36.073.11.188-.02 2.856-.606 1.543-.28 1.841-.315.833.388.091.395-.328.807-1.969.486-2.309.462-3.439.813-.042.03.049.061 1.549.146.662.036h1.622l3.02.225.79.522.474.638-.079.485-1.215.62-1.64-.389-3.829-.91-1.312-.329h-.182v.11l1.093 1.068 2.006 1.81 2.509 2.33.127.578-.322.455-.34-.049-2.205-1.657-.851-.747-1.926-1.62h-.128v.17l.444.649 2.345 3.521.122 1.08-.17.353-.608.213-.668-.122-1.374-1.925-1.415-2.167-1.143-1.943-.14.08-.674 7.254-.316.37-.729.28-.607-.461-.322-.747.322-1.476.389-1.924.315-1.53.286-1.9.17-.632-.012-.042-.14.018-1.434 1.967-2.18 2.945-1.726 1.845-.414.164-.717-.37.067-.662.401-.589 2.388-3.036 1.44-1.882.93-1.086-.006-.158h-.055L4.132 18.56l-1.13.146-.487-.456.061-.746.231-.243 1.908-1.312-.006.006z"/></svg>"#
        let image = NSImage(data: Data(svg.utf8))!
        image.size = NSSize(width: 16, height: 16)
        image.isTemplate = true
        image.accessibilityDescription = "Apprentice"
        return image
    }()

    /// The spark with a small dot bottom right, shown while actively recording. The dot sits in a cleared ring so it reads apart from the rays.
    private static let recordingIcon: NSImage = {
        let image = NSImage(size: NSSize(width: 16, height: 16), flipped: false) { rect in
            claudeIcon.draw(in: rect)
            let dot = NSRect(x: 10.5, y: 0.5, width: 5, height: 5)
            NSGraphicsContext.current?.compositingOperation = .clear
            NSBezierPath(ovalIn: dot.insetBy(dx: -1.5, dy: -1.5)).fill()
            NSGraphicsContext.current?.compositingOperation = .sourceOver
            NSColor.black.setFill()
            NSBezierPath(ovalIn: dot).fill()
            return true
        }
        image.isTemplate = true
        image.accessibilityDescription = "Apprentice, recording"
        return image
    }()

    // MARK: menu

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        @discardableResult
        func addItem(_ title: String, _ action: Selector? = nil, to targetMenu: NSMenu? = nil) -> NSMenuItem {
            let menuItem = (targetMenu ?? menu).addItem(withTitle: title, action: action, keyEquivalent: "")
            menuItem.target = self
            return menuItem
        }
        if engine.isPaused {
            addItem("Turn on Apprentice Mode", #selector(resume))
        } else {
            addItem("Turn off Apprentice Mode", #selector(turnOff))
        }
        addItem("Trigger Workflow Harvesting", #selector(triggerWorkflowHarvesting))
        menu.addItem(.separator())
        addItem("Quit Claude", #selector(quit))  // quits Apprentice
    }

    // MARK: actions

    @objc private func turnOff() {
        engine.pause(minutes: nil)
        showHUD("Apprentice off")
    }

    @objc private func resume() {
        engine.resume()
        showHUD("Apprentice on")
    }

    private func togglePauseFromHotkey() {
        if engine.isPaused { resume() } else { turnOff() }
    }

    @objc private func quit() {
        engine.shutdown()
        NSApp.terminate(nil)
    }

    /// Opens a Claude Code session in the data root with the instructions as the prompt itself: Claude won't take
    /// instructions from a file in a folder it doesn't trust, but it follows what the user sends.
    @objc private func triggerWorkflowHarvesting() {
        let instructions = (try? String(contentsOf: engine.locations.automatePrompt, encoding: .utf8)) ?? ""
        let prompt = instructions + "\n\nIn this session, $D is the working directory. Analyze my sessions from the last 3 days."
        var link = URLComponents(string: "claude://code/new")!
        link.queryItems = [
            URLQueryItem(name: "folder", value: engine.locations.root.path),
            URLQueryItem(name: "q", value: String(prompt.suffix(14_000))),  // the app caps q at 14,336 characters
        ]
        NSWorkspace.shared.open(link.url!)
    }

    // MARK: HUD

    private func showHUD(_ text: String) {
        hudPanel?.orderOut(nil)
        let panelSize = NSSize(width: 180, height: 56)
        let panel = NSPanel(contentRect: NSRect(origin: .zero, size: panelSize), styleMask: [.borderless, .nonactivatingPanel],
                            backing: .buffered, defer: false)
        panel.isOpaque = false
        panel.backgroundColor = .clear
        panel.level = .statusBar
        panel.ignoresMouseEvents = true
        let background = NSVisualEffectView(frame: NSRect(origin: .zero, size: panelSize))
        background.material = .hudWindow
        background.state = .active
        background.wantsLayer = true
        background.layer?.cornerRadius = 12
        let label = NSTextField(labelWithString: text)
        label.font = .systemFont(ofSize: 18, weight: .semibold)
        label.sizeToFit()
        label.setFrameOrigin(NSPoint(x: (panelSize.width - label.frame.width) / 2, y: (panelSize.height - label.frame.height) / 2))
        background.addSubview(label)
        panel.contentView = background
        panel.center()
        panel.orderFrontRegardless()
        hudPanel = panel
        DispatchQueue.main.asyncAfter(deadline: .now() + 1.2) { [weak self] in
            if self?.hudPanel === panel { panel.orderOut(nil) }
        }
    }
}

// Carbon hotkeys need no permission and still fire while recording is paused.
private var pauseHotkeyAction: () -> Void = {}

private func registerPauseHotkey(_ action: @escaping () -> Void) {
    pauseHotkeyAction = action
    var eventSpecification = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
    InstallEventHandler(GetApplicationEventTarget(), { _, _, _ in pauseHotkeyAction(); return noErr }, 1, &eventSpecification, nil, nil)
    var hotkeyReference: EventHotKeyRef?
    RegisterEventHotKey(UInt32(kVK_ANSI_P), UInt32(cmdKey | optionKey | controlKey),
                        EventHotKeyID(signature: 0x41505052, id: 1), GetApplicationEventTarget(), 0, &hotkeyReference)
}
