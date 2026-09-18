import SwiftUI

/// Typed route storage exists on iOS 15 and avoids putting NavigationPath in
/// a View's stored properties. Native iOS 16+ retains NavigationStack routing.
struct CompatPathNavigationStack<Element: Hashable, Root: View, Destination: View>: View {
    @Binding private var path: [Element]
    private let root: Root
    private let destination: (Element) -> Destination

    init(path: Binding<[Element]>, @ViewBuilder root: () -> Root,
         @ViewBuilder destination: @escaping (Element) -> Destination) {
        self._path = path
        self.root = root()
        self.destination = destination
    }

    var body: some View {
        if #available(iOS 16.0, *) {
            NavigationStack(path: $path) {
                root.navigationDestination(for: Element.self, destination: destination)
            }
        } else {
            NavigationView {
                LegacyNavigationLevel(path: $path, depth: 0,
                                      content: AnyView(root),
                                      destination: { AnyView(destination($0)) })
            }
            .navigationViewStyle(StackNavigationViewStyle())
        }
    }
}

/// A value link shares the exact route binding with the legacy stack. Native
/// platforms still use NavigationLink(value:), including its list styling.
struct CompatValueNavigationLink<Element: Hashable, Label: View>: View {
    let value: Element
    @Binding var path: [Element]
    private let label: Label

    init(value: Element, path: Binding<[Element]>, @ViewBuilder label: () -> Label) {
        self.value = value
        self._path = path
        self.label = label()
    }

    var body: some View {
        if #available(iOS 16.0, *) {
            NavigationLink(value: value) { label }
        } else {
            Button { path.append(value) } label: { label }
                .buttonStyle(.plain)
        }
    }
}

private struct LegacyNavigationLevel<Element: Hashable>: View {
    @Binding var path: [Element]
    let depth: Int
    let content: AnyView
    let destination: (Element) -> AnyView
    @State private var appeared = false

    var body: some View {
        let next = path.indices.contains(depth) ? path[depth] : nil
        let expectedPrefix = Array(path.prefix(depth + 1))
        content
            .background(
                NavigationLink(isActive: Binding(
                    get: { appeared && path.count > depth },
                    set: { active in
                        // A stale back callback from [A] must not erase a
                        // replacement route [B] or a newer foreground intent.
                        guard appeared, !active, path.count > depth,
                              path.starts(with: expectedPrefix) else { return }
                        path = Array(path.prefix(depth))
                    }
                )) {
                    if let next {
                        // Erasure bounds the recursive generic type, while
                        // .id(next) preserves the app's session-VM identity.
                        AnyView(LegacyNavigationLevel(
                            path: $path, depth: depth + 1,
                            content: AnyView(destination(next).id(next)),
                            destination: destination
                        ).id(next))
                    }
                } label: { EmptyView() }
                .hidden()
                .id(next)
            )
            .onAppear {
                // Materialize deep links one mounted level at a time; pushing
                // two hidden links before their NavigationView is mounted can
                // otherwise drop the second level on iOS 15.
                DispatchQueue.main.async { appeared = true }
            }
    }
}

struct CompatSplitNavigationView<Sidebar: View, Detail: View>: View {
    private let sidebar: Sidebar
    private let detail: Detail

    init(@ViewBuilder sidebar: () -> Sidebar, @ViewBuilder detail: () -> Detail) {
        self.sidebar = sidebar()
        self.detail = detail()
    }

    var body: some View {
        if #available(iOS 16.0, *) {
            NativeSplitNavigationView(sidebar: sidebar, detail: detail)
        } else {
            NavigationView {
                sidebar
                detail
            }
            .navigationViewStyle(DoubleColumnNavigationViewStyle())
        }
    }
}

@available(iOS 16.0, *)
private struct NativeSplitNavigationView<Sidebar: View, Detail: View>: View {
    let sidebar: Sidebar
    let detail: Detail
    @State private var columnVisibility: NavigationSplitViewVisibility = .automatic

    var body: some View {
        NavigationSplitView(columnVisibility: $columnVisibility) {
            sidebar
        } detail: {
            detail
        }
    }
}
