import AppKit
import Carbon.HIToolbox
import XCTest
@testable import CCDesk

final class HotKeyTests: XCTestCase {
    // MARK: - 全局热键注册（Carbon RegisterEventHotKey，不需要辅助功能授权）

    func testRegisterHotKeyRejectsDuplicateCombo() {
        // 用一个不像日常快捷键的组合（⌥⌘F13）跑测试，避免撞上本机已占用的键位。
        let keyCode = UInt32(kVK_F13)
        let modifiers = UInt32(optionKey | cmdKey)
        defer { unregisterHotKey(keyCode: keyCode, modifiers: modifiers) }

        XCTAssertTrue(registerHotKey(keyCode: keyCode, modifiers: modifiers) {},
                      "首次注册应成功；若这里就 false，下面那条断言的 false 是假绿")
        XCTAssertFalse(registerHotKey(keyCode: keyCode, modifiers: modifiers) {},
                       "同一 keyCode+modifiers 重复注册，RegisterEventHotKey 返回 eventHotKeyExistsErr")
    }

    func testUnregisterAllowsReRegisteringSameCombo() {
        let keyCode = UInt32(kVK_F13)
        let modifiers = UInt32(optionKey | cmdKey)
        XCTAssertTrue(registerHotKey(keyCode: keyCode, modifiers: modifiers) {})
        unregisterHotKey(keyCode: keyCode, modifiers: modifiers)
        XCTAssertTrue(registerHotKey(keyCode: keyCode, modifiers: modifiers) {},
                      "注销后同一组合应可再注册，否则测试之间会互相污染")
        unregisterHotKey(keyCode: keyCode, modifiers: modifiers)
    }

    // MARK: - popover 锚点矩形（热键路径不能锚被 macOS 隐藏的 status item button）

    func testAnchorRectSitsAtTopRightCornerOfScreenFrame() {
        // visibleFrame 已排除菜单栏，其 maxY 即"菜单栏下方"。
        let rect = anchorRect(inScreenFrame: NSRect(x: 0, y: 0, width: 1000, height: 800))
        XCTAssertEqual(rect, NSRect(x: 999, y: 799, width: 1, height: 1))
    }

    func testAnchorRectIsOnePixelAndStaysInsideFrame() {
        let frames = [
            NSRect(x: 0, y: 0, width: 1512, height: 945),        // 内建屏
            NSRect(x: -1920, y: 300, width: 1920, height: 1080), // 左侧外接屏（原点为负）
            NSRect(x: 1512, y: -200, width: 2560, height: 1440), // 右下外接屏
        ]
        for frame in frames {
            let rect = anchorRect(inScreenFrame: frame)
            XCTAssertEqual(rect.size, NSSize(width: 1, height: 1), "frame=\(frame)")
            XCTAssertTrue(frame.contains(rect), "锚点越出屏幕边界：frame=\(frame) rect=\(rect)")
            XCTAssertEqual(rect.maxX, frame.maxX, "应贴右边缘：frame=\(frame)")
            XCTAssertEqual(rect.maxY, frame.maxY, "应贴上边缘（菜单栏下方）：frame=\(frame)")
        }
    }

    // MARK: - 选屏（鼠标所在那块屏；用构造 frame 测，不依赖真实多屏）

    func testScreenFramePicksTheOneContainingPoint() {
        let builtin = NSRect(x: 0, y: 0, width: 1512, height: 945)
        let external = NSRect(x: 1512, y: -200, width: 2560, height: 1440)
        XCTAssertEqual(screenFrame(containing: NSPoint(x: 700, y: 400),
                                   in: [builtin, external]), builtin)
        XCTAssertEqual(screenFrame(containing: NSPoint(x: 2000, y: 900),
                                   in: [builtin, external]), external)
    }

    func testScreenFrameFallsBackToFirstWhenPointIsOffAllScreens() {
        // 拔掉外接屏的瞬间 mouseLocation 可能落在任何屏之外，此时不能返回空矩形。
        let builtin = NSRect(x: 0, y: 0, width: 1512, height: 945)
        XCTAssertEqual(screenFrame(containing: NSPoint(x: 9999, y: 9999), in: [builtin]), builtin)
        XCTAssertEqual(screenFrame(containing: .zero, in: []), .zero)
    }
}
