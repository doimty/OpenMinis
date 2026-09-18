import Foundation
import PhotosUI
import SwiftUI
import UniformTypeIdentifiers

/// Immutable import jobs keep unavailable PhotosPickerItem out of iOS-15 view
/// state. Both backends preserve encoded image bytes and return an owned copy
/// of a video file, never the provider's callback-scoped temporary URL.
struct CompatPhotoPickerItem: Equatable, @unchecked Sendable {
    private let id = UUID()
    let itemIdentifier: String?
    let supportedContentTypes: [UTType]
    private let imageLoader: () async throws -> Data?
    private let videoLoader: () async throws -> URL?

    static func == (lhs: Self, rhs: Self) -> Bool { lhs.id == rhs.id }

    func loadImageData() async throws -> Data? { try await imageLoader() }
    func loadVideoFile() async throws -> URL? { try await videoLoader() }

    @available(iOS 16.0, *)
    init(_ item: PhotosPickerItem) {
        itemIdentifier = item.itemIdentifier
        supportedContentTypes = item.supportedContentTypes
        imageLoader = { try await item.loadTransferable(type: Data.self) }
        videoLoader = { try await item.loadTransferable(type: VideoFileTransferable.self)?.url }
    }

    init(_ result: PHPickerResult) {
        let provider = result.itemProvider
        let types = provider.registeredTypeIdentifiers.compactMap { UTType($0) }
        itemIdentifier = result.assetIdentifier
        supportedContentTypes = types
        let imageType = types.first(where: { $0.conforms(to: .image) })?.identifier ?? UTType.image.identifier
        let videoType = types.first(where: { $0.conforms(to: .movie) })?.identifier ?? UTType.movie.identifier
        imageLoader = {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Data?, Error>) in
                provider.loadDataRepresentation(forTypeIdentifier: imageType) { data, error in
                    if let error { continuation.resume(throwing: error) }
                    else { continuation.resume(returning: data) }
                }
            }
        }
        videoLoader = {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<URL?, Error>) in
                provider.loadFileRepresentation(forTypeIdentifier: videoType) { url, error in
                    if let error {
                        continuation.resume(throwing: error)
                    } else if let url {
                        do {
                            // PHPicker invalidates its URL when this callback
                            // returns. Copy here, before resuming the task.
                            let owned = FileManager.default.temporaryDirectory
                                .appendingPathComponent(UUID().uuidString + "_" + url.lastPathComponent)
                            try FileManager.default.copyItem(at: url, to: owned)
                            continuation.resume(returning: owned)
                        } catch {
                            continuation.resume(throwing: error)
                        }
                    } else {
                        continuation.resume(returning: nil)
                    }
                }
            }
        }
    }
}

@available(iOS 16.0, *)
struct VideoFileTransferable: Transferable, @unchecked Sendable {
    let url: URL

    static var transferRepresentation: some TransferRepresentation {
        FileRepresentation(contentType: .movie) { video in
            SentTransferredFile(video.url)
        } importing: { received in
            let owned = FileManager.default.temporaryDirectory
                .appendingPathComponent(UUID().uuidString + "_" + received.file.lastPathComponent)
            try FileManager.default.copyItem(at: received.file, to: owned)
            return Self(url: owned)
        }
    }
}

extension View {
    @ViewBuilder
    func compatPhotosPicker(isPresented: Binding<Bool>, selection: Binding<[CompatPhotoPickerItem]>,
                            maxSelectionCount: Int, imagesOnly: Bool = false) -> some View {
        if #available(iOS 16.0, *) {
            modifier(NativePhotosPickerModifier(isPresented: isPresented, selection: selection,
                                                maxSelectionCount: maxSelectionCount, imagesOnly: imagesOnly))
        } else {
            sheet(isPresented: isPresented) {
                LegacyPhotosPicker(maxSelectionCount: maxSelectionCount, imagesOnly: imagesOnly) { results in
                    // Empty means cancel. Do not replay an earlier selection.
                    selection.wrappedValue = results.map { CompatPhotoPickerItem($0) }
                    isPresented.wrappedValue = false
                }
            }
        }
    }
}

@available(iOS 16.0, *)
private struct NativePhotosPickerModifier: ViewModifier {
    @Binding var isPresented: Bool
    @Binding var selection: [CompatPhotoPickerItem]
    let maxSelectionCount: Int
    let imagesOnly: Bool
    @State private var nativeSelection: [PhotosPickerItem] = []

    func body(content: Content) -> some View {
        content
            .photosPicker(isPresented: $isPresented, selection: $nativeSelection,
                          maxSelectionCount: maxSelectionCount,
                          matching: imagesOnly ? .images : .any(of: [.images, .videos]))
            .onChange(of: nativeSelection) { items in
                guard !items.isEmpty else { return }
                selection = items.map { CompatPhotoPickerItem($0) }
            }
            .onChange(of: selection) { items in
                if items.isEmpty { nativeSelection = [] }
            }
    }
}

private struct LegacyPhotosPicker: UIViewControllerRepresentable {
    let maxSelectionCount: Int
    let imagesOnly: Bool
    let onSelection: ([PHPickerResult]) -> Void

    func makeUIViewController(context: Context) -> PHPickerViewController {
        var configuration = PHPickerConfiguration(photoLibrary: .shared())
        configuration.filter = imagesOnly ? .images : .any(of: [.images, .videos])
        configuration.selectionLimit = maxSelectionCount
        configuration.selection = .ordered
        configuration.preferredAssetRepresentationMode = .current
        let picker = PHPickerViewController(configuration: configuration)
        picker.delegate = context.coordinator
        return picker
    }

    func updateUIViewController(_ picker: PHPickerViewController, context: Context) {
        context.coordinator.onSelection = onSelection
    }

    func makeCoordinator() -> Coordinator { Coordinator(onSelection: onSelection) }

    final class Coordinator: NSObject, PHPickerViewControllerDelegate {
        var onSelection: ([PHPickerResult]) -> Void
        init(onSelection: @escaping ([PHPickerResult]) -> Void) { self.onSelection = onSelection }
        func picker(_ picker: PHPickerViewController, didFinishPicking results: [PHPickerResult]) {
            onSelection(results)
        }
    }
}
