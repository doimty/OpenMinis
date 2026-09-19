import UIKit
import SwiftUI

@MainActor
final class ProbeState: ObservableObject {
    @Published var requested = false
    @Published var outer = false
    @Published var name = ""
    @Published var detail = ""
    @Published var subject: String?
    @Published var followup = false
    @Published var lifecycle = InputPromptLifecycle()
    var saved: [(String, String)] = []
    var cancelled = 0
    var ownerAliveAtSubmit = false

    var subjectBinding: Binding<Bool> {
        Binding(get: { self.subject != nil }, set: { if !$0 { self.subject = nil } })
    }
}

struct LegacyOneField: View {
    @ObservedObject var state: ProbeState
    var body: some View {
        Text("Underlying browser stays open")
            .modifier(LegacyTextInputAlert(
                title: Text("New Group"), isPresented: $state.requested,
                lifecycle: $state.lifecycle, confirmLabel: Text("Create"),
                onConfirm: {
                    state.ownerAliveAtSubmit = state.requested
                    state.saved.append((state.name, state.detail))
                }, onCancel: { state.cancelled += 1 },
                fields: TextField("Group name", text: $state.name),
                message: Text("Created inside \("/家庭/模型").")))
    }
}

struct LegacyTwoFields: View {
    @ObservedObject var state: ProbeState
    var body: some View {
        Text("Rename subject / collision test")
            .modifier(LegacyTextInputAlert(
                title: Text("Rename Group"), isPresented: state.subjectBinding,
                lifecycle: $state.lifecycle, confirmLabel: Text("Rename"),
                onConfirm: {
                    state.ownerAliveAtSubmit = state.subject == "folder-A"
                    state.saved.append((state.name, state.detail))
                    state.followup = true
                }, onCancel: { state.cancelled += 1 },
                fields: VStack {
                    TextField("Group name", text: $state.name)
                    TextField("Description", text: $state.detail)
                }, message: EmptyView()))
            .alert("Group Already Exists", isPresented: $state.followup) {
                Button("Change Name") {}
                Button("Cancel", role: .cancel) {}
            }
    }
}

struct NestedPrompt: View {
    @ObservedObject var state: ProbeState
    var body: some View {
        Text("Outer flow")
            .sheet(isPresented: $state.outer) { LegacyOneField(state: state) }
    }
}

struct ModernPrompt: View {
    @ObservedObject var state: ProbeState
    var body: some View {
        Text("Native modern alert")
            .compatTextInputAlert(Text("Modern Prompt"), isPresented: $state.requested,
                                  confirmLabel: Text("Create"), onConfirm: {}) {
                TextField("Group name", text: $state.name)
            } message: { Text("Keep modern native alert fields.") }
    }
}

@main
@MainActor
final class ProbeApp: UIResponder, UIApplicationDelegate {
    var window: UIWindow?
    var phases: [String] = []
    let directory = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        .appendingPathComponent("input-probe", isDirectory: true)

    func application(_ application: UIApplication, didFinishLaunchingWithOptions options: [UIApplication.LaunchOptionsKey: Any]? = nil) -> Bool {
        UIView.setAnimationsEnabled(false)
        let window = UIWindow(frame: UIScreen.main.bounds)
        self.window = window
        window.rootViewController = UIViewController()
        window.makeKeyAndVisible()
        try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        Task { await run() }
        return true
    }

    func host<V: View>(_ view: V) {
        window?.rootViewController = UIHostingController(rootView: view)
    }

    func descendants(_ view: UIView) -> [UIView] {
        [view] + view.subviews.flatMap(descendants)
    }

    func fields() -> [UITextField] {
        guard let window else { return [] }
        return descendants(window).compactMap { $0 as? UITextField }
            .filter { ["Group name", "Description"].contains($0.placeholder ?? "") }
    }

    func alert() -> UIAlertController? {
        var vc = window?.rootViewController
        while let presented = vc?.presentedViewController { vc = presented }
        return vc as? UIAlertController
    }

    func wait(_ label: String, until condition: () -> Bool) async throws {
        for _ in 0..<160 {
            if condition() { return }
            try await Task.sleep(nanoseconds: 25_000_000)
        }
        throw NSError(domain: "InputProbe", code: 1, userInfo: [NSLocalizedDescriptionKey: "timeout: \(label)"])
    }

    func require(_ condition: Bool, _ label: String) throws {
        if !condition { throw NSError(domain: "InputProbe", code: 2, userInfo: [NSLocalizedDescriptionKey: label]) }
    }

    func enter(_ placeholder: String, _ text: String) throws {
        guard let field = fields().first(where: { $0.placeholder == placeholder }) else {
            throw NSError(domain: "InputProbe", code: 3, userInfo: [NSLocalizedDescriptionKey: "no field: \(placeholder)"])
        }
        try require(field.isEnabled && field.becomeFirstResponder(), "field not editable: \(placeholder)")
        field.text = text
        field.sendActions(for: .editingChanged)
    }

    func snapshot(_ name: String) {
        guard let window else { return }
        let renderer = UIGraphicsImageRenderer(bounds: window.bounds)
        let image = renderer.image { _ in window.drawHierarchy(in: window.bounds, afterScreenUpdates: true) }
        try? image.pngData()?.write(to: directory.appendingPathComponent(name + ".png"))
    }

    func run() async {
        do {
            let one = ProbeState()
            host(LegacyOneField(state: one))
            one.requested = true
            try await wait("legacy one field") { self.fields().count == 1 }
            try enter("Group name", "家庭模型组 🐱")
            try await wait("CJK binding update") { one.name == "家庭模型组 🐱" }
            snapshot("legacy-editable-name")
            phases.append("legacy-field-visible-editable-cjk-binding")
            // This is the production close-action seam, not an XCUITest tap.
            one.lifecycle.requestClose(.confirm)
            try await wait("confirm callback after sheet dismissal") { one.saved.count == 1 && !one.requested }
            try require(one.ownerAliveAtSubmit && one.saved[0].0 == "家庭模型组 🐱", "owner cleared before confirmed text consumed")
            try require(window?.rootViewController?.presentedViewController == nil, "sheet still presented at completion")
            phases.append("confirm-once-owner-held-until-dismissal")

            one.requested = true
            try await wait("reopened legacy field") { self.fields().count == 1 }
            one.lifecycle.requestClose(.cancel)
            try await wait("cancel callback") { one.cancelled == 1 && !one.requested }
            try require(one.saved.count == 1, "cancel saved")
            phases.append("cancel-without-save")

            one.requested = true
            try await wait("external-cancel field") { self.fields().count == 1 }
            one.requested = false
            try await wait("external cancellation dismissed") { self.window?.rootViewController?.presentedViewController == nil }
            try require(one.saved.count == 1 && one.cancelled == 1, "external cancellation dispatched user action")
            phases.append("external-cancel-no-action")

            let two = ProbeState()
            two.name = "Original"
            two.detail = "Old description"
            host(LegacyTwoFields(state: two))
            two.subject = "folder-A"
            try await wait("legacy two fields") { self.fields().count == 2 }
            try enter("Group name", "重复分组")
            try enter("Description", "描述仍然保留")
            try await wait("two bindings") { two.name == "重复分组" && two.detail == "描述仍然保留" }
            snapshot("legacy-two-fields")
            two.lifecycle.requestClose(.confirm)
            try await wait("follow-up collision alert") { self.alert()?.title == "Group Already Exists" }
            try require(two.ownerAliveAtSubmit && two.subject == nil && two.saved.count == 1, "optional subject/callback broken")
            try require(two.saved[0].0 == "重复分组" && two.saved[0].1 == "描述仍然保留", "description/name lost")
            phases.append("two-fields-optional-subject-and-followup")
            two.followup = false
            try await wait("followup closed") { self.window?.rootViewController?.presentedViewController == nil }
            two.subject = "folder-A"
            try await wait("rename reopened") { self.fields().count == 2 }
            try require(two.name == "重复分组" && two.detail == "描述仍然保留", "reopen lost drafts")
            two.subject = nil
            try await wait("rename closed") { self.window?.rootViewController?.presentedViewController == nil }
            phases.append("rename-reopen-preserves-drafts")

            let nested = ProbeState()
            host(NestedPrompt(state: nested))
            nested.outer = true
            try await wait("outer sheet") { self.window?.rootViewController?.presentedViewController != nil }
            nested.requested = true
            try await wait("nested input field") { self.fields().count == 1 }
            try enter("Group name", "备份目录")
            try await wait("nested edit") { nested.name == "备份目录" }
            nested.lifecycle.requestClose(.confirm)
            try await wait("nested confirmed") { nested.saved.count == 1 && !nested.requested }
            try require(nested.outer && window?.rootViewController?.presentedViewController != nil, "input dismissal closed parent browser")
            nested.outer = false
            try await wait("outer dismissed") { self.window?.rootViewController?.presentedViewController == nil }
            phases.append("nested-prompt-preserves-parent-flow")

            let modern = ProbeState()
            host(ModernPrompt(state: modern))
            modern.requested = true
            try await wait("modern native alert text field") { self.alert()?.textFields?.count == 1 }
            try enter("Group name", "现代系统仍可输入")
            try await wait("modern binding") { modern.name == "现代系统仍可输入" }
            modern.requested = false
            try await wait("modern dismissed") { self.window?.rootViewController?.presentedViewController == nil }
            phases.append("modern-native-alert-keeps-field")
            finish(error: nil)
        } catch {
            snapshot("failure")
            finish(error: error.localizedDescription)
        }
    }

    func finish(error: String?) {
        var report: [String: Any] = [
            "os": UIDevice.current.systemVersion,
            "passed": error == nil,
            "phases": phases,
            "limits": "Production legacy component forced on iOS26.2; editing is native UIKit, close uses production lifecycle seam; not iOS15 runtime or full-app XCUITest."
        ]
        if let error { report["error"] = error }
        if let data = try? JSONSerialization.data(withJSONObject: report, options: [.prettyPrinted, .sortedKeys]) {
            try? data.write(to: directory.appendingPathComponent("report.json"))
        }
        print(error.map { "FAIL: \($0)" } ?? "PASS: input prompt native component matrix")
        exit(error == nil ? 0 : 1)
    }
}
