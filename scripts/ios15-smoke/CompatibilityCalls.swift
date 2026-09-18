import SwiftUI
import UIKit
import WebKit
import Speech
import FileProvider
import UserNotifications
import AVFoundation

// Exercise production adapters and real call shapes, not copied backports.
// This proves type/availability contracts only, not on-device rendering.
struct CompatibilityCalls: View {
    let deviceName: String
    let count: Int
    let cancelled: Bool
    @State private var text = ""
    @State private var path: [String] = []
    @State private var selected: [CompatPhotoPickerItem] = []
    @State private var showPhotos = false
    @FocusState private var editing: Bool

    var body: some View {
        CompatPathNavigationStack(path: $path) {
            Form {
                CompatLabeledContent("Created", value: "2026-09-18")
                CompatLabeledContent("From", value: "\(deviceName) · iOS 15")
                CompatLabeledContent("Name") { TextField("Name", text: $text) }
                CompatLabeledContent { Text("Content") } label: { Text("Label") }
                CompatLabeledContent(deviceName) { Text("Dynamic label") }
                if count > 0 {
                    CompatLabeledContent("Updated", value: "\(count)")
                }
                CompatMultilineTextField("Description", text: $text, lineLimit: 1...4)
                CompatMultilineTextField(deviceName, text: $text)
                    .focused($editing)
                    .frame(maxHeight: 200)
                CompatValueNavigationLink(value: "server", path: $path) { Text("Server") }
                Button("Create") {}.compatFontWeight(.semibold)
            }
            .compatHiddenScrollBackground()
            .compatNavigationBarHidden(cancelled)
        } destination: { Text($0) }
        .compatPresentationDetentHeight(240)
        .compatPresentationDragIndicator(.visible)
        .compatPersistentSystemOverlays(.hidden)
        .compatPhotosPicker(isPresented: $showPhotos, selection: $selected, maxSelectionCount: 4)
        .interactiveDismissDisabled(true)
        .disabled(cancelled)
    }
}

struct ExistingSheetAdapters: View {
    var body: some View {
        CompatNavigationStack {
            CompatSplitNavigationView {
                VStack {
                    Text("Medium / large").compatPresentationDetentsMediumLarge()
                    Text("Fraction").compatPresentationDetentsFraction75()
                    Text("Lines").compatLineLimit(1...4)
                }
            } detail: {
                CompatAnyShape(CompatUnevenRoundedRectangle(
                    bottomLeadingRadius: 16, bottomTrailingRadius: 16))
                    .compatFillGradient(.blue)
                    .compatOnGeometryChange(for: CGFloat.self, of: { $0.size.width }) { _ in }
            }
        }
    }
}

func thumbnailAndImageDiagnosticsCalls(generator: AVAssetImageGenerator) async throws {
    _ = try await ThumbnailCache.videoFrame(using: generator, at: .zero)
    _ = MarkdownStripper.imageSyntaxMatches(in: "中文😀 ![图](x.png)")
}

struct DragDropCompatibilityCalls: View {
    let sessionIds: [String]
    @State private var isTargeted = false

    var body: some View {
        Text("Sidebar")
            .compatDraggable(sessionIds.first ?? "")
            .compatDropDestination(for: String.self,
                action: { values in
                    guard !values.isEmpty else { return false }
                    return true
                },
                isTargeted: { isTargeted = $0 }
            )
            .compatNavigationSplitViewColumnWidth(min: 140, ideal: 160, max: 240)
    }
}

struct SecondaryToolbarCalls: View {
    var body: some View {
        Text("Group").toolbar {
            ToolbarItem(placement: .compatSecondaryAction) { Button("Copy") {} }
        }
    }
}

struct MessageMenuCalls: View {
    var body: some View {
        Color.clear.frame(width: 0, height: 0)
            .compatContextMenu {
                Button("Copy All") {}
                if Bool.random() { Button("Read from Start") {} }
                Button(role: .destructive) {} label: { Label("Compact", systemImage: "trash") }
            } preview: { Text("Message text") }
    }
}

@MainActor
func hostingAndWebKitCalls() {
    let parent = UIViewController()
    let configuration = LegacyHostingConfiguration(content: AnyView(MessageMenuCalls()),
                                                    parent: WeakHostingParent(parent), onSizeChange: {})
    _ = configuration.makeContentView()
    _ = LegacyFlowLayout(items: [LegacyFlowItem(id: "image") { Text("Attachment") }])
    _ = LegacyFlowArrangement.pack(sizes: [CGSize(width: 20, height: 10)], width: 100,
                                   hSpacing: 8, vSpacing: 8, trailing: true)
    let webConfiguration = WKWebViewConfiguration()
    if #available(iOS 15.4, *) { webConfiguration.preferences.isElementFullscreenEnabled = true }
    if #available(iOS 16.0, *) { _ = UITextView(usingTextLayoutManager: true) }
    else { _ = UITextView() }
}

@MainActor
func speechRequestCalls(url: URL) {
    let request = SFSpeechURLRecognitionRequest(url: url)
    request.shouldReportPartialResults = true
    if #available(iOS 16.0, *) { request.addsPunctuation = true }
    request.taskHint = .dictation
}

func cancellableTimerCalls() async {
    try? await Task.sleep(nanoseconds: 200_000_000)
    guard !Task.isCancelled else { return }
    let settleMillis = 1200
    try? await Task.sleep(nanoseconds: UInt64(settleMillis) * 1_000_000)
    guard !Task.isCancelled else { return }
}

struct FileProviderAvailabilityCalls {
    @available(iOS 16.0, *)
    static let domain = NSFileProviderDomain(identifier: NSFileProviderDomainIdentifier("smoke"), displayName: "Smoke")

    static func signalAndRemove() {
        guard #available(iOS 16.0, *) else { return }
        NSFileProviderManager(for: domain)?.signalEnumerator(for: .rootContainer) { _ in }
        NSFileProviderManager.remove(domain, mode: .removeAll) { _, _ in }
    }
}

@MainActor
func badgeAvailabilityCalls(enabled: Bool, count: Int) {
    guard #available(iOS 16.0, *) else {
        UIApplication.shared.applicationIconBadgeNumber = enabled ? count : 0
        return
    }
    UNUserNotificationCenter.current().setBadgeCount(enabled ? count : 0) { _ in }
}

struct ConditionalToolbarCalls: View {
    let dirty: Bool
    let multi: Bool
    let emptySelection: Bool
    let isLive: Bool

    var body: some View {
        Text("Editor").toolbar {
            ToolbarItem(placement: .navigationBarLeading) {
                if multi { Button("Cancel") {} }
            }
            ToolbarItem(placement: .navigationBarTrailing) {
                if dirty { Button("Save") {} }
            }
            ToolbarItem(placement: .confirmationAction) {
                if multi { Button("Add") {}.disabled(emptySelection) }
                else { Button("Done") {} }
            }
            ToolbarItem(placement: .bottomBar) {
                if !isLive { Button("Delete", role: .destructive) {} }
            }
        }
    }
}
