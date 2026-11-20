#!/usr/bin/env python3
"""iOS 15 attachment packing model + source guards (NOT SwiftUI/device tests).

Run: python3 scripts/test_ios15_attachment_grid.py
The old-width fixture reproduces LegacyFlowLayout's self-measurement loop;
packing checks model the existing Swift algorithm, not a Swift interpreter.
"""
from pathlib import Path
import math
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]
BAR = ROOT / "src/ios/Views/Chat/ChatInputBar.swift"
INPUT = ROOT / "src/ios/Views/Chat/AIChatView/AIChatView+InputBar.swift"
CHAT = ROOT / "src/ios/Views/Chat/AIChatView/AIChatView.swift"
STEER = ROOT / "src/ios/Views/Chat/AIChatView/Sheets/SubAgentRunDetail.swift"
SURFACE = ROOT / "src/ios/Views/Chat/AIChatView/Composer/ComposerSurface.swift"


def section(source, start, end):
    return source.split(start, 1)[1].split(end, 1)[0]


def pack(count, width, tile, gap, trailing=False):
    """Fixed-tile mirror of LegacyFlowArrangement.pack with a tile-width floor."""
    width = max(width, tile)
    positions = []
    x = y = 0
    row_start = 0

    def align_row():
        if trailing:
            offset = width - (x - gap)
            for i in range(row_start, len(positions)):
                positions[i] = (positions[i][0] + offset, positions[i][1])

    for _ in range(count):
        if x + tile > width and x > 0:
            align_row()
            row_start = len(positions)
            x = 0
            y += tile + gap
        positions.append((x, y))
        x += tile + gap
    align_row()
    return positions, y + tile if count else 0


class PackingModelTests(unittest.TestCase):
    def test_empty_has_zero_height(self):
        self.assertEqual(pack(0, 308, 64, 6, True), ([], 0))

    def test_input_exact_fit_and_no_trailing_gap(self):
        self.assertEqual(pack(3, 208, 64, 8)[0], [(0, 0), (72, 0), (144, 0)])
        self.assertEqual(pack(3, 207.99, 64, 8)[1], 136)
        self.assertEqual(pack(2, 136, 64, 8)[1], 64)
        self.assertEqual(pack(2, 135.99, 64, 8)[1], 136)

    def test_sent_last_row_stays_trailing_in_original_order(self):
        self.assertEqual(pack(3, 134, 64, 6, True),
                         ([(0, 0), (70, 0), (70, 70)], 134))
        self.assertEqual(pack(1, 308, 64, 6, True), ([(244, 0)], 64))

    def test_render_height_matches_sent_estimator_at_boundaries(self):
        for width in (64, 133.99, 134, 203.99, 204, 274, 308, 428, 768):
            per_row = max(1, int((width + 6) / (64 + 6)))
            for count in range(1, 41):
                with self.subTest(width=width, count=count):
                    positions, height = pack(count, width, 64, 6, True)
                    rows = math.ceil(count / per_row)
                    block_height = rows * 64 + (rows - 1) * 6 + 6
                    self.assertEqual(height + 6, block_height)
                    self.assertTrue(all(x >= 0 and x + 64 <= width for x, _ in positions))

    def test_rotation_recomputes_width_and_height_both_directions(self):
        portrait = pack(10, 204, 64, 6, True)
        landscape = pack(10, 428, 64, 6, True)
        self.assertEqual(portrait[1], 274)
        self.assertEqual(landscape[1], 134)
        self.assertNotEqual(portrait, landscape)  # stale-width negative signal
        self.assertEqual(pack(10, 204, 64, 6, True), portrait)

    def test_remove_reorder_and_video_loading_keep_slot_order(self):
        ids = ["image-a", "document", "video", "loading-video-0", "loading-video-1"]
        slots, height = pack(len(ids), 208, 64, 8)
        self.assertEqual(dict(zip(ids, slots))["loading-video-0"], (0, 72))
        self.assertEqual(height + 6, 142)  # includes composer remove-button overhang
        ids.remove("document")
        ids.insert(0, ids.pop(1))
        self.assertEqual(ids[:2], ["video", "image-a"])
        self.assertEqual(pack(len(ids), 208, 64, 8)[1], 136)

    def test_narrow_zero_width_has_one_tile_without_negative_trailing_offset(self):
        for width in (0, 1, 63.99):
            self.assertEqual(pack(1, width, 64, 6, True), ([(0, 0)], 64))

    def test_old_self_measurement_is_stuck_at_one_column(self):
        available_width = 0
        for _ in range(5):
            layout_width = max(max(available_width, 1), 64)
            available_width = layout_width  # overlay measures the explicit own frame
        old_positions, _ = pack(3, available_width, 64, 8)
        self.assertEqual({x for x, _ in old_positions}, {0})
        self.assertEqual(pack(3, 208, 64, 8)[0], [(0, 0), (72, 0), (144, 0)])


class SourceContractTests(unittest.TestCase):
    def setUp(self):
        self.bar = BAR.read_text(encoding="utf-8")

    def test_all_three_legacy_attachment_routes_use_only_dedicated_grid(self):
        for start, end in (("struct InputAttachmentGridView: View", "struct AttachmentPreviewTarget"),
                           ("struct UserAttachmentList: View", "private struct WebAppAddToHomeMenuModifier"),
                           ("struct QueuedAttachmentPreview: View", "class PastableUITextView")):
            with self.subTest(view=start):
                view = section(self.bar, start, end)
                self.assertIn("AttachmentTileGrid(items:", view)
                self.assertNotIn("LegacyFlowLayout(", view)

    def test_grid_has_natural_adaptive_height_and_live_parent_width(self):
        self.assertIn("private struct AttachmentTileGrid: View", self.bar)
        grid = section(self.bar, "private struct AttachmentTileGrid: View", "struct AttachmentGridHeightKey")
        self.assertNotIn("@State", grid)
        self.assertIn(".adaptive(minimum: tileSize, maximum: tileSize)", grid)
        self.assertIn(".frame(maxWidth: .infinity", grid)
        self.assertIn("GeometryReader { proxy in", grid)
        self.assertIn("width: max(proxy.size.width, tileSize)", grid)
        self.assertIn("LegacyFlowArrangement.pack(", grid)
        self.assertIn("id: \\.element.id", grid)
        self.assertIn(".offset(x: arrangement.positions[index].x", grid)

    def test_sent_metrics_remain_64_6_and_shared_with_estimator(self):
        metrics = section(self.bar, "enum UserAttachmentTileMetrics", "struct UserAttachmentList")
        self.assertRegex(metrics, r"static let tile: CGFloat = 64\b")
        self.assertRegex(metrics, r"static let gap: CGFloat = 6\b")
        self.assertIn("(availableWidth + gap) / (tile + gap)", metrics)
        self.assertIn("rows * tile + (rows - 1) * gap + vStackSpacing", metrics)
        for start, end in (("struct UserAttachmentList", "private struct WebAppAddToHomeMenuModifier"),
                           ("struct QueuedAttachmentPreview", "class PastableUITextView")):
            self.assertIn("gap: UserAttachmentTileMetrics.gap", section(self.bar, start, end))

    def test_input_viewport_cannot_start_or_revert_to_zero(self):
        source = INPUT.read_text(encoding="utf-8")
        self.assertIn("min(max(attachmentGridHeight, InputAttachmentGridView.minimumHeight), 160)", source)
        self.assertIn("guard height.isFinite, height >= InputAttachmentGridView.minimumHeight", source)
        self.assertIn("attachmentGridHeight: CGFloat = InputAttachmentGridView.minimumHeight",
                      CHAT.read_text(encoding="utf-8"))

    def test_single_uuid_sheet_drag_remove_and_loading_are_preserved(self):
        grid = section(self.bar, "struct InputAttachmentGridView: View", "struct AttachmentPreviewTarget")
        grid = re.sub(r"(?m)^\s*//[^\n]*$", "", grid)  # count executable sheet wiring only
        self.assertEqual(grid.count(".sheet("), 1)
        for fragment in (".sheet(item: $previewTarget)",
                         "previewTarget = AttachmentPreviewTarget(id: attachment.id)",
                         "attachments: attachments.filter { $0.loadState == .ready }",
                         "initialID: target.id", "onRemove(attachment)", ".onDrag {",
                         "targetID: attachment.id", "draggingID: $draggingID", "onMove: onMove",
                         'id: "loading-video-\\(index)"', "VideoLoadingChip()"):
            self.assertIn(fragment, grid)
        sent = section(self.bar, "struct UserAttachmentList: View", "private struct WebAppAddToHomeMenuModifier")
        self.assertIn("attachments.filter { $0.isImage }", sent)
        self.assertIn("let items: [GalleryItem] = all.map", sent)
        for fragment in ("else if meta.isVideo", "else if meta.pastedId != nil", "fileTile(meta"):
            self.assertIn(fragment, sent)
        queued = section(self.bar, "struct QueuedAttachmentPreview: View", "class PastableUITextView")
        for kind in ("case .image:", "case .video:", "case .document:"):
            self.assertIn(kind, queued)

    def test_steer_clips_native_white_editor_before_padding(self):
        source = section(STEER.read_text(encoding="utf-8"), "struct SubAgentActionsCard: View", "private var isBlank")
        self.assertIn("ComposerSurface.roundedShape()", source)
        self.assertRegex(source, r"\.focused\(\$focused\)\s*//[^\n]*\n\s*\.clipShape\(editorShape\)\s*\.padding")
        self.assertIn(".background(editorShape.fill(Color(UIColor.tertiarySystemFill)))", source)
        self.assertEqual(source.count(".clipShape(editorShape)"), 2)
        for fragment in ("lineLimit: 1...4", ".font(.system(size: 13))", ".layoutPriority(1)",
                         "SubAgentUserSteer.send(draft, childSessionId: childSessionId)",
                         'draft = ""', "sentCount += 1", "focused = false", ".disabled(isBlank)"):
            self.assertIn(fragment, source)

    def test_shared_shape_keeps_main_radius_and_continuous_style(self):
        source = section(SURFACE.read_text(encoding="utf-8"), "struct ComposerSurface: ViewModifier", "struct ComposerContextGlow")
        self.assertIn("static func roundedShape(cornerRadius: CGFloat = 20)", source)
        self.assertIn("RoundedRectangle(cornerRadius: cornerRadius, style: .continuous)", source)
        self.assertIn("Self.roundedShape()", source)

    def test_sent_parent_width_matches_measurement_and_estimation(self):
        source = (ROOT / "src/ios/Views/Chat/ChatMessageViews.swift").read_text(encoding="utf-8")
        user = source.split("private var userRow:", 1)[1].split("private var", 1)[0]
        for fragment in ("HStack(spacing: 0)", "Spacer(minLength: 60)",
                         ".layoutPriority(1)", ".padding(.horizontal, 16)"):
            self.assertIn(fragment, user)
        base = ROOT / "src/ios/Agent/MessageList/CollectionViewMessageListV3/Coordinator"
        for name in ("HeightMeasurement", "HeightEstimation"):
            estimator = (base / f"MessageListV3Coordinator+{name}.swift").read_text(encoding="utf-8")
            self.assertRegex(estimator, r"availableWidth: [^\n]*32[^\n]*60")


if __name__ == "__main__":
    unittest.main(verbosity=2)
