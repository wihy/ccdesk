import AppKit
import Carbon.HIToolbox

// 全局热键走 Carbon 的 RegisterEventHotKey：它是唯一**不需要辅助功能
// （Accessibility）授权**的全局热键方案。NSEvent.addGlobalMonitorForEvents 要用户
// 先去系统设置里勾授权，装完还得重启 App，体验差，不用。

private struct HotKeyCombo: Hashable {
    let keyCode: UInt32
    let modifiers: UInt32
}

private struct HotKeyRegistration {
    let ref: EventHotKeyRef
    let id: UInt32
}

private var hotKeyRegistrations: [HotKeyCombo: HotKeyRegistration] = [:]
private var hotKeyHandlers: [UInt32: () -> Void] = [:]
private var nextHotKeyID: UInt32 = 1
private var hotKeyDispatcherInstalled = false

/// 注册全局热键。返回是否注册成功——同一 keyCode+modifiers 重复注册时
/// RegisterEventHotKey 返回 eventHotKeyExistsErr，这里如实返回 false。
@discardableResult
func registerHotKey(keyCode: UInt32, modifiers: UInt32,
                    handler: @escaping () -> Void) -> Bool {
    installHotKeyDispatcherIfNeeded()
    let id = nextHotKeyID
    var ref: EventHotKeyRef?
    let status = RegisterEventHotKey(keyCode, modifiers,
                                     EventHotKeyID(signature: hotKeySignature, id: id),
                                     GetApplicationEventTarget(), 0, &ref)
    guard status == noErr, let ref else { return false }
    nextHotKeyID += 1
    hotKeyRegistrations[HotKeyCombo(keyCode: keyCode, modifiers: modifiers)] =
        HotKeyRegistration(ref: ref, id: id)
    hotKeyHandlers[id] = handler
    return true
}

/// 注销已注册的组合（不然同一组合在同进程内再也注册不上）。
func unregisterHotKey(keyCode: UInt32, modifiers: UInt32) {
    let combo = HotKeyCombo(keyCode: keyCode, modifiers: modifiers)
    guard let registration = hotKeyRegistrations.removeValue(forKey: combo) else { return }
    UnregisterEventHotKey(registration.ref)
    hotKeyHandlers[registration.id] = nil
}

private let hotKeySignature = OSType(0x4343_4447)  // 'CCDG'

private func installHotKeyDispatcherIfNeeded() {
    guard !hotKeyDispatcherInstalled else { return }
    var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard),
                             eventKind: UInt32(kEventHotKeyPressed))
    InstallEventHandler(GetApplicationEventTarget(), { _, event, _ -> OSStatus in
        var pressed = EventHotKeyID()
        let status = GetEventParameter(event, EventParamName(kEventParamDirectObject),
                                       EventParamType(typeEventHotKeyID), nil,
                                       MemoryLayout<EventHotKeyID>.size, nil, &pressed)
        guard status == noErr else { return status }
        hotKeyHandlers[pressed.id]?()
        return noErr
    }, 1, &spec, nil, nil)
    hotKeyDispatcherInstalled = true
}

/// 热键路径的 popover 锚点矩形：贴屏幕 visibleFrame 的右上角（visibleFrame 已排除
/// 菜单栏，其 maxY 即"菜单栏下方"）。菜单栏空间不足时 macOS 会静默隐藏排后面的
/// status item，那个 button 不在屏上，锚它会弹到离屏位置，只能自造 1×1 锚点。
func anchorRect(inScreenFrame frame: NSRect) -> NSRect {
    NSRect(x: frame.maxX - 1, y: frame.maxY - 1, width: 1, height: 1)
}

/// 取包含该点（鼠标位置）的那块屏。点落在所有屏之外（如刚拔掉外接屏）时退回第一块，
/// 不返回空矩形——否则锚点会落到 (0,0) 屏幕左下角。
func screenFrame(containing point: NSPoint, in frames: [NSRect]) -> NSRect {
    frames.first { $0.contains(point) } ?? frames.first ?? .zero
}
