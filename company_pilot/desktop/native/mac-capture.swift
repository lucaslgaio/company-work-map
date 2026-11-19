// Native observation helper. All commands emit one JSON response; none request TCC access.
import AppKit
import ApplicationServices
import ScreenCaptureKit

enum CaptureError: String, Error {
    case permission_denied, no_focused_window, focus_changed, protected_surface
    case screen_permission_denied, ambiguous_window, screenshot_unavailable, window_too_large
    case secure_field_visible
}

func attribute(_ element: AXUIElement, _ name: String) -> CFTypeRef? {
    var value: CFTypeRef?
    return AXUIElementCopyAttributeValue(element, name as CFString, &value) == .success ? value : nil
}

func stringAttribute(_ element: AXUIElement, _ name: String) -> String {
    let value = attribute(element, name)
    if let url = value as? URL { return url.absoluteString }
    return value as? String ?? ""
}

struct Focus {
    let app: NSRunningApplication
    let window: AXUIElement
    let context: [String: Any]
}

@MainActor
func focus() throws -> Focus {
    guard AXIsProcessTrusted() else { throw CaptureError.permission_denied }
    guard let app = NSWorkspace.shared.frontmostApplication else { throw CaptureError.no_focused_window }
    let axApp = AXUIElementCreateApplication(app.processIdentifier)
    AXUIElementSetMessagingTimeout(axApp, 1)
    guard let raw = attribute(axApp, "AXFocusedWindow"), CFGetTypeID(raw) == AXUIElementGetTypeID() else { throw CaptureError.no_focused_window }
    let window = unsafeBitCast(raw, to: AXUIElement.self)
    var secure = false
    if let focusedRaw = attribute(axApp, "AXFocusedUIElement"), CFGetTypeID(focusedRaw) == AXUIElementGetTypeID() {
        let focused = unsafeBitCast(focusedRaw, to: AXUIElement.self)
        secure = stringAttribute(focused, "AXSubrole") == "AXSecureTextField"
    }
    let context: [String: Any] = [
        "app_id": app.bundleIdentifier ?? "unknown",
        "process_id": Int(app.processIdentifier),
        "window_id": String(CFHash(window)),
        "title": stringAttribute(window, "AXTitle"),
        "record_ref": stringAttribute(window, "AXDocument"),
        "secure_context": secure,
        "evidence_level": "native"
    ]
    return Focus(app: app, window: window, context: context)
}

func same(_ left: [String: Any], _ right: [String: Any]) -> Bool {
    return ["window_id", "process_id", "title", "record_ref"].allSatisfy {
        String(describing: left[$0] ?? "") == String(describing: right[$0] ?? "")
    }
}

func windowText(_ root: AXUIElement) -> String {
    var pending = [root]
    var parts = [String]()
    var visited = Set<CFHashCode>()
    var length = 0
    while let element = pending.popLast(), visited.count < 256, length < 16000 {
        let id = CFHash(element)
        if visited.contains(id) { continue }
        visited.insert(id)
        if stringAttribute(element, "AXSubrole") == "AXSecureTextField" { continue }
        if let hidden = attribute(element, "AXHidden") as? Bool, hidden { continue }
        let role = stringAttribute(element, "AXRole")
        if ["AXStaticText", "AXTextField", "AXTextArea", "AXDocument", "AXLink"].contains(role) {
            let value = stringAttribute(element, "AXValue")
            let title = stringAttribute(element, "AXTitle")
            for part in [title, value] where !part.isEmpty {
                let bounded = String(part.prefix(16000 - length))
                parts.append(bounded)
                length += bounded.count
            }
        }
        if let children = attribute(element, "AXChildren") as? [AXUIElement] {
            pending.append(contentsOf: children.prefix(256 - visited.count).reversed())
        }
    }
    return String(parts.joined(separator: "\n").prefix(16000))
}

func containsSecureField(_ root: AXUIElement) -> Bool {
    var pending = [root]
    var visited = Set<CFHashCode>()
    while let element = pending.popLast(), visited.count < 256 {
        let id = CFHash(element)
        if visited.contains(id) { continue }
        visited.insert(id)
        if stringAttribute(element, "AXSubrole") == "AXSecureTextField" { return true }
        if let children = attribute(element, "AXChildren") as? [AXUIElement] {
            pending.append(contentsOf: children.prefix(256 - visited.count))
        }
    }
    return false
}

func compressedJPEG(_ image: CGImage) -> Data? {
    let maxSide = 1280.0
    let width = Double(image.width)
    let height = Double(image.height)
    let scale = min(1, maxSide / max(width, height))
    let targetW = max(1, Int((width * scale).rounded()))
    let targetH = max(1, Int((height * scale).rounded()))
    guard let context = CGContext(
        data: nil, width: targetW, height: targetH, bitsPerComponent: 8, bytesPerRow: 0,
        space: CGColorSpaceCreateDeviceRGB(),
        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue
    ) else { return nil }
    context.interpolationQuality = .medium
    context.draw(image, in: CGRect(x: 0, y: 0, width: targetW, height: targetH))
    guard let scaled = context.makeImage() else { return nil }
    guard let data = NSBitmapImageRep(cgImage: scaled).representation(
        using: .jpeg, properties: [.compressionFactor: 0.6]
    ) else { return nil }
    return data
}

@main
struct NativeCapture {
    @MainActor
    static func main() async {
        var output: [String: Any]
        do { output = try await execute() }
        catch let error as CaptureError { output = ["error": error.rawValue] }
        catch { output = ["error": "screenshot_unavailable"] }
        let data = try! JSONSerialization.data(withJSONObject: output, options: [.sortedKeys])
        print(String(data: data, encoding: .utf8)!)
        if output["error"] != nil { exit(1) }
    }

    @MainActor
    static func execute() async throws -> [String: Any] {
        let action = CommandLine.arguments.dropFirst().first ?? "probe"
        if action == "probe" {
            return ["platform": "macOS", "accessibility_granted": AXIsProcessTrusted(),
                    "screen_recording_granted": CGPreflightScreenCaptureAccess(),
                    "permission_requested": false, "screen_capture_attempted": false]
        }
        let current = try focus()
        if action == "context" { return current.context }
        let input = FileHandle.standardInput.readDataToEndOfFile()
        guard let expected = try JSONSerialization.jsonObject(with: input) as? [String: Any], same(current.context, expected) else { throw CaptureError.focus_changed }
        guard current.context["secure_context"] as? Bool != true else { throw CaptureError.protected_surface }
        if action == "text" {
            let text = windowText(current.window)
            guard same(current.context, try focus().context) else { throw CaptureError.focus_changed }
            return current.context.merging(["text": text]) { _, new in new }
        }
        guard action == "screenshot", CGPreflightScreenCaptureAccess() else { throw CaptureError.screen_permission_denied }
        if containsSecureField(current.window) { throw CaptureError.secure_field_visible }
        // No display capture or process-wide filter. Resolve only the exact active window.
        let content = try await SCShareableContent.excludingDesktopWindows(true, onScreenWindowsOnly: true)
        guard same(current.context, try focus().context) else { throw CaptureError.focus_changed }
        var position = CGPoint.zero
        var size = CGSize.zero
        if let value = attribute(current.window, "AXPosition"), CFGetTypeID(value) == AXValueGetTypeID() {
            AXValueGetValue(unsafeBitCast(value, to: AXValue.self), .cgPoint, &position)
        }
        if let value = attribute(current.window, "AXSize"), CFGetTypeID(value) == AXValueGetTypeID() {
            AXValueGetValue(unsafeBitCast(value, to: AXValue.self), .cgSize, &size)
        }
        let windows = content.windows.filter {
            $0.owningApplication?.processID == current.app.processIdentifier
                && $0.title == current.context["title"] as? String
                && abs($0.frame.origin.x - position.x) < 3 && abs($0.frame.origin.y - position.y) < 3
                && abs($0.frame.width - size.width) < 3 && abs($0.frame.height - size.height) < 3
        }
        guard windows.count == 1 else { throw CaptureError.ambiguous_window }
        let window = windows[0]
        guard window.frame.width > 1, window.frame.height > 1, window.frame.width <= 4096, window.frame.height <= 4096 else { throw CaptureError.window_too_large }
        let config = SCStreamConfiguration()
        config.width = Int(window.frame.width)
        config.height = Int(window.frame.height)
        config.showsCursor = false
        let image = try await SCScreenshotManager.captureImage(contentFilter: SCContentFilter(desktopIndependentWindow: window), configuration: config)
        guard same(current.context, try focus().context) else { throw CaptureError.focus_changed }
        guard let jpeg = compressedJPEG(image) else { throw CaptureError.screenshot_unavailable }
        return ["image_base64": jpeg.base64EncodedString(), "media_type": "image/jpeg", "single_window": true, "max_edge": 1280]
    }
}
