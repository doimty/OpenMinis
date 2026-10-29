import SwiftUI

struct InputPromptCalls: View {
    @State private var shown = false
    @State private var name = ""
    @State private var detail = ""
    let dynamicTitle: String
    let path: String

    var body: some View {
        Text("Base")
            .compatTextInputAlert(Text("New Group"), isPresented: $shown,
                                  confirmLabel: Text("Create"), onConfirm: {}) {
                TextField("Group name", text: $name)
            } message: { Text("Created inside \(path).") }
            .compatTextInputAlert(Text(verbatim: dynamicTitle), isPresented: $shown,
                                  confirmLabel: Text("Rename"),
                                  onConfirm: {}, onCancel: {}) {
                TextField("Group Name", text: $name)
                TextField("Description (optional)", text: $detail)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
            } message: { EmptyView() }
    }
}
