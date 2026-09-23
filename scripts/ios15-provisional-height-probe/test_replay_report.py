#!/usr/bin/env python3
"""Capture protocol tests, not a UIKit bug-reproduction oracle."""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from replay_report import validate, BASELINE_COMMIT

COMMIT = 'a' * 40
RUN = '22222222-3333-4444-5555-666666666666'


def event(seq, kind, phase, values=None, owner='cv', subject='controller',
          parent='none', index=-1, generation=0):
    return dict(schema=1, run=RUN, commit=COMMIT, seq=seq, t=float(seq), main=True,
                kind=kind, owner=owner, subject=subject, parent=parent, idx=index,
                generation=generation, phase=phase, values=values or {})


def fixture():
    row = dict(index=5, cellID='cell', generation=1, frame=[0, 400, 428, 502],
               cache=502, inWindow=True, textID='text', textBounds=[0, 0, 396, 498],
               textLength=100, markdownSHA256='c' * 64)
    shot = dict(collectionID='cv', vmID='vm', productionCoordinator=True,
                modelSHA256='d' * 64, viewport=[0, 0, 428, 801], items=20,
                contentHeight=5000, offset=0, tracking=False, decelerating=False,
                deferred=False, visibleRows=[row])
    begin = dict(copy.deepcopy(shot), phase='begin', t=1.25)
    end = dict(copy.deepcopy(shot), phase='end', t=8.25)
    end['offset'] = 400
    end['viewport'][1] = 400
    return dict(schema=1, kind='minis-production-list-replay', sourceCommit=COMMIT,
                baselineCommit=BASELINE_COMMIT, os='15.1.1', runID=RUN,
                inputSHA256='b' * 64, messageCount=4, rawEntryCount=8,
                textBlockCount=2, omittedMediaCount=0, gestureBegins=1, gestureEnds=1,
                reentries=0, finishReason='user-finished', startedAt=0.9,
                snapshots=[begin, end], trace=[
                    event(1, 'replay-marker', 'begin', {'messages': 4, 'textBlocks': 2}),
                    event(2, 'configure', 'label', {'width': 428, 'height': 502},
                          subject='cell', parent='vm', index=5, generation=1),
                    event(3, 'text-size', 'intrinsic-finite',
                          {'width': 396, 'height': 498, 'length': 100},
                          subject='text', parent='cell', generation=1),
                    event(4, 'replay-marker', 'finger-began', {'state': 1, 'gesture': 1}),
                    event(5, 'viewport', 'did-scroll',
                          {'tracking': 1, 'decel': 0, 'width': 428, 'viewportH': 801, 'offset': 200}, subject='vm'),
                    event(6, 'replay-marker', 'finger-ended', {'state': 3, 'gesture': 1}),
                    event(7, 'viewport', 'did-scroll',
                          {'tracking': 0, 'decel': 1, 'width': 428, 'viewportH': 801, 'offset': 400}, subject='vm'),
                    event(8, 'replay-marker', 'end', {'gestureBegins': 1, 'gestureEnds': 1}),
                ])


def renumber(report):
    for seq, entry in enumerate(report['trace'], 1):
        entry['seq'] = seq
        entry['t'] = float(seq)
    report['snapshots'][-1]['t'] = float(len(report['trace'])) + 0.25


class CaptureTests(unittest.TestCase):
    def test_valid_capture_is_not_a_fix_or_repro_verdict(self):
        self.assertEqual(validate(fixture())['status'], 'CAPTURE_VALID')

    def test_cap_is_invalid(self):
        report = fixture(); report['trace'][-1]['kind'] = 'limit'
        with self.assertRaises(ValueError): validate(report)

    def test_gap_is_invalid(self):
        report = fixture(); report['trace'].pop(2)
        with self.assertRaises(ValueError): validate(report)

    def test_wrong_input_is_invalid(self):
        with self.assertRaises(ValueError): validate(fixture(), input_sha='e' * 64)

    def test_wrong_source_is_invalid(self):
        with self.assertRaises(ValueError): validate(fixture(), commit='e' * 40)

    def test_timeout_is_incomplete(self):
        report = fixture(); report['finishReason'] = 'time-limit'
        self.assertEqual(validate(report)['status'], 'INCOMPLETE')

    def test_driver_gestures_without_native_witness_are_incomplete(self):
        report = fixture()
        for entry in report['trace']:
            if entry['kind'] == 'viewport': entry['values']['tracking'] = entry['values']['decel'] = 0
        self.assertEqual(validate(report)['status'], 'INCOMPLETE')

    def test_missing_snapshot_is_invalid(self):
        report = fixture(); report['snapshots'] = []
        with self.assertRaises(ValueError): validate(report)

    def test_empty_viewport_is_invalid(self):
        report = fixture(); report['snapshots'][0]['viewport'][2] = 0
        with self.assertRaises(ValueError): validate(report)

    def test_forged_gesture_count_is_invalid(self):
        report = fixture(); report['gestureBegins'] = 5
        with self.assertRaises(ValueError): validate(report)

    def test_no_finger_interaction_is_incomplete(self):
        report = fixture(); report['gestureBegins'] = report['gestureEnds'] = 0
        report['trace'] = [e for e in report['trace'] if not e['phase'].startswith('finger-')]
        report['trace'][-1]['values'] = {'gestureBegins': 0, 'gestureEnds': 0}
        renumber(report)
        self.assertEqual(validate(report)['status'], 'INCOMPLETE')

    def test_multiple_identical_state_gestures_are_retained(self):
        report = fixture()
        report['trace'][-1:-1] = [
            event(8, 'replay-marker', 'finger-began', {'state': 1, 'gesture': 2}),
            event(9, 'replay-marker', 'finger-ended', {'state': 3, 'gesture': 2}),
        ]
        report['gestureBegins'] = report['gestureEnds'] = 2
        report['trace'][-1]['values'] = {'gestureBegins': 2, 'gestureEnds': 2}
        renumber(report)
        self.assertEqual(validate(report)['gestureBegins'], 2)
        report['trace'][-2]['values']['gesture'] = 1
        with self.assertRaises(ValueError): validate(report)

    def test_missing_gesture_ordinal_is_invalid(self):
        report = fixture(); del report['trace'][3]['values']['gesture']
        with self.assertRaises(ValueError): validate(report)

    def test_motion_outside_capture_boundaries_is_invalid(self):
        report = fixture(); entry = report['trace'].pop(4); report['trace'].append(entry)
        renumber(report)
        with self.assertRaises(ValueError): validate(report)

    def test_motion_from_unrelated_owner_is_incomplete(self):
        report = fixture()
        for entry in report['trace']:
            if entry['kind'] == 'viewport': entry['owner'] = 'unrelated-cv'
        self.assertEqual(validate(report)['status'], 'INCOMPLETE')

    def test_arbitrary_event_cannot_impersonate_native_viewport(self):
        report = fixture()
        for entry in report['trace']:
            if entry['kind'] == 'viewport': entry['kind'] = 'custom-note'
        self.assertEqual(validate(report)['status'], 'INCOMPLETE')

    def test_missing_production_configuration_is_incomplete(self):
        report = fixture(); report['trace'][1]['kind'] = 'custom-note'
        self.assertEqual(validate(report)['status'], 'INCOMPLETE')

    def test_missing_native_text_path_is_incomplete(self):
        report = fixture(); report['trace'][2]['parent'] = 'another-cell'
        self.assertEqual(validate(report)['status'], 'INCOMPLETE')

    def test_snapshot_cannot_claim_a_different_vm(self):
        report = fixture(); report['snapshots'][-1]['vmID'] = 'another-vm'
        with self.assertRaises(ValueError): validate(report)

    def test_missing_coordinator_attestation_is_invalid(self):
        report = fixture(); del report['snapshots'][0]['productionCoordinator']
        with self.assertRaises(ValueError): validate(report)

    def test_empty_or_hidden_rows_are_invalid(self):
        for mutate in (lambda s: s.update(visibleRows=[]),
                       lambda s: s['visibleRows'][0].update(inWindow=False)):
            report = fixture(); mutate(report['snapshots'][-1])
            with self.assertRaises(ValueError): validate(report)

    def test_changed_model_or_text_is_invalid(self):
        for key in ('modelSHA256', 'markdownSHA256'):
            report = fixture()
            if key == 'modelSHA256': report['snapshots'][-1][key] = 'e' * 64
            else: report['snapshots'][-1]['visibleRows'][0][key] = 'e' * 64
            with self.assertRaises(ValueError): validate(report)

    def test_width_drift_is_invalid(self):
        report = fixture(); report['snapshots'][-1]['viewport'][2] = 390
        with self.assertRaises(ValueError): validate(report)

    def test_snapshot_backwards_clock_is_invalid(self):
        report = fixture(); report['snapshots'][-1]['t'] = 0
        with self.assertRaises(ValueError): validate(report)

    def test_unrecorded_reentry_is_invalid(self):
        report = fixture(); report['reentries'] = 1
        with self.assertRaises(ValueError): validate(report)

    def test_boolean_counts_are_not_integers(self):
        for key in ('gestureBegins', 'gestureEnds', 'reentries'):
            report = fixture(); report[key] = True
            with self.assertRaises(ValueError): validate(report)

    def test_two_real_list_remounts_are_valid(self):
        report = fixture(); end = report['trace'].pop()
        final_shot = report['snapshots'].pop()
        owner = 'cv'
        for ordinal in (1, 2):
            seq = len(report['trace']) + 1
            report['trace'].append(event(seq, 'replay-marker', 'leave', {'count': ordinal}, owner=owner))
            report['snapshots'].append(dict(copy.deepcopy(final_shot), phase='before-leave', t=seq + 0.1, collectionID=owner))
            owner = 'cv' + str(ordinal)
            report['trace'].append(event(seq + 1, 'mount', 'coordinator-attach', owner=owner, subject='vm'))
            report['trace'].append(event(seq + 2, 'replay-marker', 'reentered', {'count': ordinal}, owner=owner))
            shot = dict(copy.deepcopy(final_shot), phase='after-reentry', t=seq + 2.1, collectionID=owner)
            # A transient unmeasured text box is retained, not erased by a
            # settled-only snapshot requirement. Other snapshots are finite.
            shot['visibleRows'][0]['textBounds'] = [0, 0, 0, 0]
            report['snapshots'].append(shot)
        end.update(seq=len(report['trace']) + 1, t=float(len(report['trace']) + 1), owner=owner)
        report['trace'].append(end)
        final_shot.update(collectionID=owner, t=end['t'] + 0.25)
        report['snapshots'].append(final_shot)
        report['reentries'] = 2
        self.assertEqual(validate(report)['status'], 'CAPTURE_VALID')
        report['trace'][-2]['values']['count'] = 1
        with self.assertRaises(ValueError): validate(report)

    def test_zero_size_text_is_not_a_usable_capture(self):
        report = fixture()
        for shot in report['snapshots']: shot['visibleRows'][0]['textBounds'] = [0, 0, 0, 0]
        self.assertEqual(validate(report)['status'], 'INCOMPLETE')

    def test_unannounced_collection_change_is_invalid(self):
        report = fixture(); report['snapshots'][-1]['collectionID'] = 'new-cv'
        report['trace'][-1]['owner'] = 'new-cv'
        with self.assertRaises(ValueError): validate(report)

    def test_viewport_height_drift_is_invalid(self):
        report = fixture(); report['snapshots'][-1]['viewport'][3] = 600
        with self.assertRaises(ValueError): validate(report)

    def test_drag_without_deceleration_is_incomplete(self):
        report = fixture(); report['trace'][6]['values']['decel'] = 0
        self.assertEqual(validate(report)['status'], 'INCOMPLETE')

    def test_cli_requires_external_provenance_pins(self):
        with tempfile.TemporaryDirectory() as folder:
            report = Path(folder) / 'report.json'
            report.write_text(json.dumps(fixture()))
            command = [sys.executable, str(Path(__file__).with_name('replay_report.py')), str(report)]
            unpinned = subprocess.run(command, capture_output=True, text=True)
            self.assertEqual(unpinned.returncode, 2)
            self.assertIn('--commit', unpinned.stderr)
            pinned = subprocess.run(command + ['--commit', COMMIT, '--input-sha', 'b' * 64], capture_output=True, text=True)
            self.assertEqual(pinned.returncode, 0, pinned.stdout + pinned.stderr)
            self.assertEqual(json.loads(pinned.stdout)['status'], 'CAPTURE_VALID')

    def test_unfinished_gesture_is_incomplete(self):
        report = fixture()
        report['trace'] = [e for e in report['trace'] if e['phase'] != 'finger-ended']
        report['gestureEnds'] = 0
        report['trace'][-1]['values']['gestureEnds'] = 0
        renumber(report)
        self.assertEqual(validate(report)['status'], 'INCOMPLETE')


if __name__ == '__main__':
    unittest.main()
