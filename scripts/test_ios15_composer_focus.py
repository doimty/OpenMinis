#!/usr/bin/env python3
"""Dependency-free source guards + behavioral MODEL for the composer focus race.

This does NOT execute Swift/UIKit and is NOT device regression evidence.
Run: python3 scripts/test_ios15_composer_focus.py -v
"""
from dataclasses import dataclass, field
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "src/ios/Views/Chat/ChatInputBar.swift"


def section(text, start, end):
    return text.split(start, 1)[1].split(end, 1)[0]


class SourceGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = SOURCE.read_text()
        cls.representable = cls.source.split("struct PastableTextView:", 1)[1]
        cls.sync = section(cls.representable, "func syncFocus(", "private func withFocusSync(")
        cls.visibility = section(cls.representable, "private func canAcquireFocus(", "func dismantle(")

    def test_update_refreshes_parent_before_sync_without_inline_focus(self):
        update = section(self.representable, "func updateUIView(", "static func dismantleUIView(")
        self.assertLess(update.index("context.coordinator.parent = self"),
                        update.index("context.coordinator.syncFocus(tv)"))
        self.assertNotIn("becomeFirstResponder()", update)
        self.assertNotIn("isSyncingFocus = true", update)

    def test_request_deduplicates_and_uses_weak_captures(self):
        self.assertIn("pendingFocusWorkItem != nil, pendingFocusTarget == wantsFocus", self.sync)
        self.assertLess(self.sync.index("pendingFocusTarget == wantsFocus"),
                        self.sync.index("let generation = focusGeneration"))
        self.assertIn("[weak self, weak textView]", self.sync)
        self.assertNotIn("item.isCancelled", self.sync)  # no self-retaining WorkItem capture
        self.assertIn("DispatchQueue.main.async(execute: item)", self.sync)

    def test_execution_checks_generation_live_binding_and_current_responder(self):
        ordered = ["generation == self.focusGeneration", "self.pendingFocusWorkItem = nil",
                   "self.parent.isFocused == wantsFocus", "wantsFocus != textView.isFirstResponder",
                   "self.canAcquireFocus(for: textView)", "textView.becomeFirstResponder()"]
        # The current-responder check also occurs before enqueue; inspect closure only.
        body = self.sync.split("let item = DispatchWorkItem", 1)[1]
        positions = [body.index(value) for value in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("!self.isDismantled", body)

    def test_cancellation_invalidates_even_already_dequeued_work(self):
        cancel = section(self.representable, "func cancelPendingFocus()", "// [T-ios15-composer-focus-race]")
        for required in ("pendingFocusWorkItem?.cancel()", "pendingFocusWorkItem = nil",
                         "pendingFocusTarget = nil", "focusGeneration &+= 1"):
            self.assertIn(required, cancel)

    def test_sync_guard_is_only_around_synchronous_action(self):
        helper = section(self.representable, "private func withFocusSync(", "private func canAcquireFocus(")
        self.assertIn("isSyncingFocus = true\n            defer { isSyncingFocus = false }\n            action()", helper)
        self.assertEqual(self.representable.count("isSyncingFocus = true"), 1)
        self.assertNotIn("DispatchQueue", helper)
        self.assertEqual(self.sync.count("self.withFocusSync"), 2)

    def test_visibility_is_not_just_window_membership(self):
        for required in ("let window = textView.window", "textView.isEditable",
                         "textView.convert(textView.bounds, to: window)",
                         "frame.intersects(window.bounds)", "frame.intersection(window.bounds)",
                         "!visibleFrame.isNull, !visibleFrame.isEmpty", "!current.isHidden",
                         "current.alpha > 0.01", "current.isUserInteractionEnabled",
                         "current.clipsToBounds", "view = current.superview",
                         "current.isMovingFromParent || current.isBeingDismissed",
                         "current.presentedViewController != nil", "ancestor = current.parent"):
            self.assertIn(required, self.visibility)

    def test_navigation_guard_is_outgoing_only_and_cancellation_aware(self):
        for required in ("current.navigationController?.transitionCoordinator",
                         "!transition.isCancelled", "viewController(forKey: .from) === current",
                         "viewController(forKey: .to)", "destination !== current"):
            self.assertIn(required, self.visibility)
        self.assertNotIn("isDismantled =", self.visibility)
        self.assertNotIn("parent.isFocused =", self.visibility)

    def test_delegate_user_edges_cancel_pending_and_close_gate(self):
        end = section(self.representable, "func textViewDidEndEditing(", "func textViewDidChangeSelection(")
        for required in ("guard !isDismantled", "if !isSyncingFocus", "cancelPendingFocus()",
                         "allowsFirstResponder = false", "parent.isFocused = false"):
            self.assertIn(required, end)
        self.assertLess(end.index("cancelPendingFocus()"), end.index("parent.isFocused = false"))
        begin = section(self.representable, "func textViewDidBeginEditing(", "func textViewDidEndEditing(")
        self.assertLess(begin.index("cancelPendingFocus()"), begin.index("parent.isFocused = true"))

    def test_teardown_is_local_and_never_writes_binding(self):
        self.assertIn("coordinator.dismantle(uiView)", self.representable)
        body = section(self.representable, "func dismantle(", "deinit")
        ordered = ["isDismantled = true", "cancelPendingFocus()", "allowsFirstResponder = false",
                   "textView.delegate = nil", "textView.resignFirstResponder()"]
        positions = [body.index(value) for value in ordered]
        self.assertEqual(positions, sorted(positions))
        self.assertNotIn("parent.", body)
        self.assertNotIn("endEditing(", self.representable)

    def test_touch_gate_and_default_callsite_contract_are_preserved(self):
        subclass = section(self.source, "class PastableUITextView:", "struct PastableTextView:")
        self.assertIn("var allowsFirstResponder = false", subclass)
        self.assertIn("allowsFirstResponder && super.canBecomeFirstResponder", subclass)
        for start, end in (("override func touchesBegan(", "override func touchesEnded("),
                           ("override func hitTest(", "private var dropInteractionInstalled")):
            self.assertIn("allowsFirstResponder = true", section(subclass, start, end))
        self.assertNotIn("isChatViewVisible", self.representable)


def intersection(a, b):
    x, y = max(a[0], b[0]), max(a[1], b[1])
    return (x, y, max(0, min(a[0] + a[2], b[0] + b[2]) - x),
            max(0, min(a[1] + a[3], b[1] + b[3]) - y))


@dataclass
class Binding:
    value: bool = False
    writes: int = 0

    def set(self, value):
        self.value = value
        self.writes += 1


@dataclass
class View:
    first: bool = False
    gate: bool = False
    attached: bool = True
    editable: bool = True
    rect: tuple = (0, 700, 428, 44)
    window: tuple = (0, 0, 428, 926)
    ancestors: list = field(default_factory=list)
    controllers: list = field(default_factory=lambda: [{}])
    accepts_focus: bool = True

    def eligible(self):
        if not self.attached or not self.editable or not self.controllers:
            return False
        visible = intersection(self.rect, self.window)
        for ancestor in [{}] + self.ancestors:
            if (ancestor.get("hidden", False) or ancestor.get("alpha", 1) <= .01
                    or not ancestor.get("enabled", True)):
                return False
            if "clip" in ancestor:
                visible = intersection(visible, ancestor["clip"])
            if not visible[2] or not visible[3]:
                return False
        for controller in self.controllers:
            if any(controller.get(key) for key in ("moving", "dismissed", "modal")):
                return False
            if (controller.get("nav_from_self") and controller.get("different_destination")
                    and not controller.get("cancelled")):
                return False
        return True


@dataclass
class Work:
    generation: int
    target: bool
    cancelled: bool = False


class FocusModel:
    """Deterministic queue simulation, NOT an implementation of UIKit."""
    def __init__(self, focused=False):
        self.binding = Binding(focused)
        self.view = View()
        self.generation = 0
        self.pending = None
        self.queue = []
        self.syncing = False
        self.dismantled = False
        self.calls = []

    def cancel(self):
        if self.pending:
            self.pending.cancelled = True
        self.pending = None
        self.generation += 1

    def update(self):
        if self.dismantled:
            return
        target = self.binding.value
        if not target:
            self.view.gate = False
        if target == self.view.first:
            self.cancel()
            return
        if self.pending and self.pending.target == target:
            return
        self.cancel()
        self.pending = Work(self.generation, target)
        self.queue.append(self.pending)

    def run(self, work):
        # Deliberately run cancelled work too: generation must independently
        # reject a task already dequeued before cancellation.
        if self.dismantled or work.generation != self.generation:
            return
        self.pending = None
        target = work.target
        if self.binding.value != target or self.view.first == target:
            return
        if target and not self.view.eligible():
            self.view.gate = False
            return
        self.view.gate = target
        self.syncing = True
        try:
            self.calls.append(("become" if target else "resign", self.syncing))
            if target:
                if self.view.accepts_focus:
                    self.begin()
                else:
                    self.view.gate = False
            else:
                self.end()
        finally:
            self.syncing = False

    def drain(self):
        queue, self.queue = self.queue, []
        for work in queue:
            self.run(work)

    def begin(self):
        self.view.first = True
        if not self.dismantled and not self.syncing:
            self.cancel()
            self.binding.set(True)

    def end(self):
        self.view.first = False
        if not self.dismantled and not self.syncing:
            self.cancel()
            self.view.gate = False
            self.binding.set(False)

    def tap(self):
        self.view.gate = True
        self.begin()

    def dismantle(self):
        self.dismantled = True
        self.cancel()
        self.view.gate = False
        if self.view.first:
            self.calls.append(("local-teardown-resign", False))
            self.view.first = False  # delegate already detached, no binding write


class BehavioralSimulation(unittest.TestCase):
    def test_default_mount_does_not_autofocus(self):
        m = FocusModel()
        m.update()
        m.drain()
        self.assertFalse(m.view.gate)
        self.assertEqual(m.calls, [])

    def test_repeated_programmatic_updates_acquire_once(self):
        m = FocusModel(True)
        m.update()
        first_work = m.pending
        for _ in range(20):
            m.update()
        self.assertIs(m.pending, first_work)
        self.assertFalse(m.syncing)
        m.drain()
        self.assertEqual(m.calls, [("become", True)])
        self.assertTrue(m.view.first)
        self.assertEqual(m.binding.writes, 0)
        self.assertFalse(m.syncing)

    def test_live_binding_false_without_update_cancels_acquire(self):
        m = FocusModel(True)
        m.update()
        m.binding.value = False
        m.drain()
        self.assertEqual(m.calls, [])
        self.assertFalse(m.view.first)

    def test_latest_parent_binding_replaces_old_binding(self):
        m = FocusModel(True)
        m.update()
        old = m.binding
        m.binding = Binding(False)
        m.drain()
        self.assertTrue(old.value)
        self.assertEqual(m.calls, [])

    def test_live_binding_true_without_update_cancels_resign(self):
        m = FocusModel()
        m.view.first = True
        m.update()
        m.binding.value = True
        m.drain()
        self.assertEqual(m.calls, [])
        self.assertTrue(m.view.first)

    def test_manual_dismiss_between_enqueue_and_execution_wins(self):
        m = FocusModel(True)
        m.update()
        self.assertFalse(m.syncing)
        m.end()
        m.drain()
        self.assertEqual(m.calls, [])
        self.assertFalse(m.binding.value)
        self.assertFalse(m.view.gate)

    def test_navigation_did_end_cannot_reacquire_on_repeated_updates(self):
        m = FocusModel(True)
        m.view.first = True
        m.end()
        m.view.rect = (428, 700, 428, 44)
        for _ in range(20):
            m.update()
            m.drain()
        self.assertFalse(m.binding.value)
        self.assertEqual(m.calls, [])

    def test_false_aligned_state_cancels_acquire_and_closes_gate(self):
        m = FocusModel(True)
        m.update()
        m.binding.value = False
        m.view.gate = True
        m.update()
        m.drain()
        self.assertFalse(m.view.gate)
        self.assertEqual(m.calls, [])

    def test_true_aligned_state_cancels_old_resign(self):
        m = FocusModel()
        m.view.first = True
        m.update()
        m.binding.value = True
        m.update()
        m.drain()
        self.assertTrue(m.view.first)
        self.assertEqual(m.calls, [])

    def test_old_generation_cannot_clear_or_execute_new_request(self):
        m = FocusModel(True)
        m.update()
        old = m.pending
        m.binding.value = False
        m.update()
        m.binding.value = True
        m.update()
        newest = m.pending
        m.run(old)
        self.assertTrue(old.cancelled)
        self.assertIs(m.pending, newest)
        m.drain()
        self.assertEqual(m.calls, [("become", True)])

    def test_detach_after_enqueue_blocks_acquire(self):
        m = FocusModel(True)
        m.update()
        m.view.attached = False
        m.drain()
        self.assertEqual(m.calls, [])

    def test_offscreen_and_zero_area_geometry_blocks_acquire(self):
        for rect in ((428, 700, 428, 44), (429, 700, 428, 44),
                     (-428, 700, 428, 44), (0, 926, 428, 44), (0, 700, 0, 44)):
            with self.subTest(rect=rect):
                m = FocusModel(True)
                m.update()
                m.view.rect = rect
                m.drain()
                self.assertEqual(m.calls, [])
                self.assertTrue(m.binding.value)  # no stale binding write

    def test_ancestor_visibility_clipping_and_editability(self):
        for ancestor in ({"hidden": True}, {"alpha": 0}, {"enabled": False},
                         {"clip": (0, 0, 428, 400)}):
            with self.subTest(ancestor=ancestor):
                m = FocusModel(True)
                m.view.ancestors = [{}, ancestor]
                m.update()
                m.drain()
                self.assertEqual(m.calls, [])
        m = FocusModel(True)
        m.view.editable = False
        m.update()
        m.drain()
        self.assertEqual(m.calls, [])

    def test_navigation_and_modal_ancestor_guards(self):
        for key in ("moving", "dismissed", "modal"):
            with self.subTest(key=key):
                m = FocusModel(True)
                m.view.controllers = [{}, {key: True}]
                m.update()
                m.drain()
                self.assertEqual(m.calls, [])
        m = FocusModel(True)
        m.view.controllers = []
        m.update()
        m.drain()
        self.assertEqual(m.calls, [])

    def test_partial_visibility_is_blocked_for_outgoing_navigation(self):
        m = FocusModel(True)
        m.view.rect = (400, 700, 428, 44)
        m.view.controllers = [{}, {"nav_from_self": True, "different_destination": True}]
        m.update()
        m.drain()
        self.assertEqual(m.calls, [])
        self.assertFalse(m.view.gate)

    def test_incoming_navigation_and_rotation_do_not_block_focus(self):
        for controller in ({"nav_from_self": False, "different_destination": True},
                           {"nav_from_self": True, "different_destination": False}):
            with self.subTest(controller=controller):
                m = FocusModel(True)
                m.view.controllers = [controller]
                m.update()
                m.drain()
                self.assertTrue(m.view.first)

    def test_cancelled_interactive_pop_allows_programmatic_retry(self):
        m = FocusModel(True)
        transition = {"nav_from_self": True, "different_destination": True}
        m.view.controllers = [transition]
        m.update()
        m.drain()
        self.assertFalse(m.view.first)
        transition["cancelled"] = True
        m.update()
        m.drain()
        self.assertTrue(m.view.first)
        self.assertEqual(m.calls, [("become", True)])

    def test_cancelled_interactive_pop_allows_retap_after_did_end(self):
        m = FocusModel(True)
        m.end()
        m.view.controllers = [{}]  # rollback complete, no persistent disable
        m.tap()
        m.update()
        m.drain()
        self.assertTrue(m.view.gate)
        self.assertTrue(m.binding.value)
        self.assertTrue(m.view.first)
        self.assertEqual(m.calls, [])  # no extra programmatic acquire

    def test_teardown_pending_work_never_mutates_binding(self):
        m = FocusModel(True)
        m.update()
        m.dismantle()
        m.drain()
        m.update()
        self.assertTrue(m.binding.value)
        self.assertEqual(m.binding.writes, 0)
        self.assertFalse(m.view.gate)
        self.assertEqual(m.calls, [])

    def test_teardown_resigns_only_owned_view_without_delegate_write(self):
        m = FocusModel(True)
        other = View(first=True)
        m.view.first = True
        m.dismantle()
        self.assertFalse(m.view.first)
        self.assertTrue(other.first)
        self.assertEqual(m.calls, [("local-teardown-resign", False)])
        self.assertTrue(m.binding.value)
        self.assertEqual(m.binding.writes, 0)

    def test_direct_touch_cancels_pending_resign(self):
        m = FocusModel()
        m.view.first = True
        m.update()
        m.view.first = False  # UIKit has already dropped the old responder
        m.tap()
        m.drain()
        self.assertTrue(m.view.first)
        self.assertTrue(m.binding.value)
        self.assertEqual(m.calls, [])

    def test_failed_become_closes_gate_and_clears_sync_flag(self):
        m = FocusModel(True)
        m.view.accepts_focus = False
        m.update()
        m.drain()
        self.assertFalse(m.view.gate)
        self.assertFalse(m.syncing)
        self.assertIsNone(m.pending)
        self.assertTrue(m.binding.value)

    def test_programmatic_resign_has_no_delegate_feedback(self):
        m = FocusModel()
        m.view.first = True
        m.update()
        self.assertFalse(m.syncing)
        m.drain()
        self.assertEqual(m.calls, [("resign", True)])
        self.assertFalse(m.view.first)
        self.assertEqual(m.binding.writes, 0)
        self.assertFalse(m.syncing)


if __name__ == "__main__":
    print("Source guards + behavioral simulation only; Swift/UIKit/device NOT executed.", flush=True)
    unittest.main()
