import ApplicationServices
import ApprenticeCore

/// Thin wrapper over AXUIElement. Every read goes through the 250 ms global messaging timeout
/// (set on the system-wide element at startup), so a hung app costs at most that per call.
struct AXElement: AccessibilityNode, Hashable {
    let reference: AXUIElement

    static let systemWide = AXElement(reference: AXUIElementCreateSystemWide())

    static func application(processIdentifier: pid_t) -> AXElement {
        AXElement(reference: AXUIElementCreateApplication(processIdentifier))
    }

    static func == (left: AXElement, right: AXElement) -> Bool { CFEqual(left.reference, right.reference) }
    func hash(into hasher: inout Hasher) { hasher.combine(CFHash(reference)) }

    func attributeValue(_ attributeName: String) -> CFTypeRef? {
        var value: CFTypeRef?
        return AXUIElementCopyAttributeValue(reference, attributeName as CFString, &value) == .success ? value : nil
    }

    func stringAttribute(_ attributeName: String) -> String? { attributeValue(attributeName) as? String }

    func elementAttribute(_ attributeName: String) -> AXElement? {
        guard let value = attributeValue(attributeName), CFGetTypeID(value) == AXUIElementGetTypeID() else { return nil }
        return AXElement(reference: value as! AXUIElement)
    }

    func elementsAttribute(_ attributeName: String) -> [AXElement] {
        (attributeValue(attributeName) as? [AXUIElement])?.map(AXElement.init(reference:)) ?? []
    }

    var parentNode: AXElement? { elementAttribute(kAXParentAttribute) }
    var role: String { stringAttribute(kAXRoleAttribute) ?? "" }
    var isSecureTextField: Bool { stringAttribute(kAXSubroleAttribute) == kAXSecureTextFieldSubrole }

    var processIdentifier: pid_t? {
        var processIdentifier: pid_t = 0
        return AXUIElementGetPid(reference, &processIdentifier) == .success ? processIdentifier : nil
    }

    /// Global frame, top-left origin, in points (same space as CGEvent locations and CGDisplayBounds).
    var frame: CGRect? {
        guard let positionValue = attributeValue(kAXPositionAttribute), let sizeValue = attributeValue(kAXSizeAttribute) else { return nil }
        var position = CGPoint.zero
        var size = CGSize.zero
        guard AXValueGetValue(positionValue as! AXValue, .cgPoint, &position),
              AXValueGetValue(sizeValue as! AXValue, .cgSize, &size) else { return nil }
        return CGRect(origin: position, size: size)
    }

    func element(atScreenPoint point: CGPoint) -> AXElement? {
        var hitElement: AXUIElement?
        guard AXUIElementCopyElementAtPosition(reference, Float(point.x), Float(point.y), &hitElement) == .success,
              let hitElement else { return nil }
        return AXElement(reference: hitElement)
    }

    @discardableResult
    func setAttribute(_ attributeName: String, to value: CFTypeRef) -> Bool {
        AXUIElementSetAttributeValue(reference, attributeName as CFString, value) == .success
    }

    /// Breadth-first search, bounded so a huge tree (a long web page) can't stall the recorder queue.
    func firstDescendant(maximumVisitedNodes: Int = 400, where matches: (AXElement) -> Bool) -> AXElement? {
        var pendingElements = elementsAttribute(kAXChildrenAttribute)
        var visitedCount = 0
        while !pendingElements.isEmpty && visitedCount < maximumVisitedNodes {
            let candidateElement = pendingElements.removeFirst()
            visitedCount += 1
            if matches(candidateElement) { return candidateElement }
            pendingElements += candidateElement.elementsAttribute(kAXChildrenAttribute)
        }
        return nil
    }
}
