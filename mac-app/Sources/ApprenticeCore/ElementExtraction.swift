import CoreGraphics
import Foundation

/// Read-only view of an accessibility element, so extraction can be tested on fixtures.
public protocol AccessibilityNode {
    func stringAttribute(_ attributeName: String) -> String?
    var parentNode: Self? { get }
}

public enum ElementExtraction {
    public static let maximumTextLength = 200
    static let ancestorSearchDepth = 5

    /// Clicks usually land on a label or image inside the control; these roles defer to an actionable ancestor.
    static let passThroughRoles: Set<String> = ["AXStaticText", "AXImage", "AXGroup", "AXUnknown", "AXGenericElement"]
    static let actionableRoles: Set<String> = [
        "AXButton", "AXLink", "AXMenuItem", "AXMenuBarItem", "AXCheckBox", "AXRadioButton", "AXPopUpButton",
        "AXMenuButton", "AXComboBox", "AXTextField", "AXTextArea", "AXSlider", "AXDisclosureTriangle", "AXTab",
        "AXCell", "AXRow",
    ]
    static let containerRoles: Set<String> = ["AXWindow", "AXApplication", "AXWebArea", "AXScrollArea", "AXSplitGroup"]

    /// The element the action is really about: the hit element, or its nearest actionable ancestor.
    public static func actionTarget<Node: AccessibilityNode>(forHitElement hitElement: Node) -> Node {
        guard passThroughRoles.contains(hitElement.stringAttribute("AXRole") ?? "AXUnknown") else { return hitElement }
        var candidateAncestor = hitElement.parentNode
        for _ in 0..<ancestorSearchDepth {
            guard let ancestor = candidateAncestor else { break }
            let ancestorRole = ancestor.stringAttribute("AXRole") ?? ""
            if containerRoles.contains(ancestorRole) { break }
            if actionableRoles.contains(ancestorRole) { return ancestor }
            candidateAncestor = ancestor.parentNode
        }
        return hitElement
    }

    /// "AXButton" → "button".
    public static func role<Node: AccessibilityNode>(of element: Node) -> String {
        let rawRole = element.stringAttribute("AXRole") ?? "AXUnknown"
        return (rawRole.hasPrefix("AX") ? String(rawRole.dropFirst(2)) : rawRole).lowercased()
    }

    public static func tag<Node: AccessibilityNode>(of element: Node) -> String {
        element.stringAttribute("AXSubrole") ?? ""
    }

    /// First non-empty of title, description, placeholder, help; then the text of the element that was
    /// actually hit (a static text's value); then the title of the nearest labelled ancestor.
    public static func text<Node: AccessibilityNode>(of target: Node, hitElement: Node? = nil) -> String {
        func ownLabel(_ node: Node) -> String? {
            for attributeName in ["AXTitle", "AXDescription", "AXPlaceholderValue", "AXHelp"] {
                if let label = cleaned(node.stringAttribute(attributeName)) { return label }
            }
            if node.stringAttribute("AXRole") == "AXStaticText" { return cleaned(node.stringAttribute("AXValue")) }
            return nil
        }
        if let label = ownLabel(target) { return truncated(label) }
        if let hitElement, let label = ownLabel(hitElement) { return truncated(label) }
        var candidateAncestor = target.parentNode
        for _ in 0..<ancestorSearchDepth {
            guard let ancestor = candidateAncestor else { break }
            if containerRoles.contains(ancestor.stringAttribute("AXRole") ?? "") { break }
            if let label = cleaned(ancestor.stringAttribute("AXTitle")) ?? cleaned(ancestor.stringAttribute("AXDescription")) {
                return truncated(label)
            }
            candidateAncestor = ancestor.parentNode
        }
        return ""
    }

    public static func record<Node: AccessibilityNode>(for target: Node, hitElement: Node? = nil, bbox: [Double]?) -> ElementRecord {
        ElementRecord(tag: tag(of: target), role: role(of: target), text: text(of: target, hitElement: hitElement), bbox: bbox)
    }

    /// Global (top-left origin, points) frame → `[x, y, w, h]` in pixels of a screenshot of `displayBounds`.
    public static func boundingBoxInScreenshotPixels(elementFrame: CGRect, displayBounds: CGRect, screenshotPixelSize: CGSize) -> [Double]? {
        guard displayBounds.width > 0, displayBounds.height > 0, !elementFrame.isNull, !elementFrame.isInfinite else { return nil }
        let horizontalScale = screenshotPixelSize.width / displayBounds.width
        let verticalScale = screenshotPixelSize.height / displayBounds.height
        func oneDecimal(_ number: CGFloat) -> Double { (Double(number) * 10).rounded() / 10 }
        return [oneDecimal((elementFrame.minX - displayBounds.minX) * horizontalScale),
                oneDecimal((elementFrame.minY - displayBounds.minY) * verticalScale),
                oneDecimal(elementFrame.width * horizontalScale),
                oneDecimal(elementFrame.height * verticalScale)]
    }

    static func cleaned(_ rawText: String?) -> String? {
        guard let rawText else { return nil }
        let collapsed = rawText.split(whereSeparator: { $0.isWhitespace || $0.isNewline }).joined(separator: " ")
        return collapsed.isEmpty ? nil : collapsed
    }

    static func truncated(_ text: String) -> String { String(text.prefix(maximumTextLength)) }
}
