#!/usr/bin/env python3
"""Validate a real-list capture, NOT certify reproduction or a production fix."""
import argparse
import json
from pathlib import Path
from device_report import (BASELINE_COMMIT, HEX40, HEX64, NONE_IDS, _uuid,
                           _validate_event, _frame, _positive_int, _finite,
                           _nonnegative_int)


def identity(value):
    return isinstance(value, str) and value not in NONE_IDS


def digest(value, pattern=HEX64):
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def incomplete(reason):
    return {'status': 'INCOMPLETE', 'reason': reason}


def validate(data, commit=None, input_sha=None):
    if not isinstance(data, dict) or data.get('schema') != 1 or data.get('kind') != 'minis-production-list-replay':
        raise ValueError('wrong capture format')
    source = data.get('sourceCommit')
    if not digest(source, HEX40) or (commit is not None and source != commit):
        raise ValueError('source commit mismatch')
    if data.get('baselineCommit') != BASELINE_COMMIT or not str(data.get('os', '')).startswith('15.'):
        raise ValueError('wrong baseline or nonlegacy OS')
    nonce = data.get('runID')
    if not _uuid(nonce) or not digest(data.get('inputSHA256')) or (input_sha is not None and data['inputSHA256'] != input_sha):
        raise ValueError('run/input provenance mismatch')
    if not all(_positive_int(data.get(k)) for k in ('messageCount', 'textBlockCount', 'rawEntryCount')):
        raise ValueError('empty model/input')
    if not all(_nonnegative_int(data.get(k)) for k in ('gestureBegins', 'gestureEnds', 'reentries', 'omittedMediaCount')):
        raise ValueError('invalid action/input counts')
    raw = data.get('trace')
    if not isinstance(raw, list) or not raw:
        raise ValueError('empty trace')
    events = [_validate_event(e, i, nonce, source) for i, e in enumerate(raw)]
    if [e['seq'] for e in events] != list(range(1, len(events) + 1)):
        raise ValueError('trace gap/duplicate')
    if any(a['t'] > b['t'] for a, b in zip(events, events[1:])):
        raise ValueError('backwards clock')
    if any(not all(_finite(v) for v in e['values'].values()) for e in events):
        raise ValueError('nonfinite/nonscalar trace measurements')
    marks = [e for e in events if e['kind'] == 'replay-marker']
    starts = [e for e in marks if e['phase'] == 'begin']
    ends = [e for e in marks if e['phase'] == 'end']
    if len(starts) != 1 or len(ends) != 1 or starts[0]['seq'] != 1 or ends[0]['seq'] != len(events):
        raise ValueError('capture boundary missing or events outside window')
    start, end = starts[0], ends[0]
    if start['seq'] >= end['seq'] or not _finite(data.get('startedAt')) or not 0 <= data['startedAt'] <= start['t']:
        raise ValueError('invalid capture start')
    if start['values'].get('messages') != data['messageCount'] or start['values'].get('textBlocks') != data['textBlockCount']:
        raise ValueError('model count marker mismatch')
    if any(end['values'].get(k) != data[k] for k in ('gestureBegins', 'gestureEnds')):
        raise ValueError('end count marker mismatch')

    shots = data.get('snapshots')
    if not isinstance(shots, list) or len(shots) < 2 or not all(isinstance(s, dict) for s in shots):
        raise ValueError('passive snapshots missing')
    if shots[0].get('phase') != 'begin' or shots[-1].get('phase') != 'end':
        raise ValueError('passive snapshot boundaries missing')
    initial_viewport = _frame(shots[0].get('viewport'), 'viewport')
    width, viewport_height = initial_viewport[2:4]
    model_hash, vm = shots[0].get('modelSHA256'), shots[0].get('vmID')
    if not digest(model_hash) or not identity(vm):
        raise ValueError('model identity/digest missing')
    owners, row_hashes = set(), {}
    finite_text_seen = False
    previous_time = start['t']
    for i, shot in enumerate(shots):
        if shot.get('phase') not in ('begin', 'before-leave', 'after-reentry', 'end'):
            raise ValueError('unknown snapshot phase')
        if not _finite(shot.get('t')) or shot['t'] < previous_time:
            raise ValueError('snapshot backwards/missing clock')
        if i < len(shots) - 1 and shot['t'] > end['t']:
            raise ValueError('snapshot outside capture')
        previous_time = shot['t']
        if not identity(shot.get('collectionID')) or shot.get('productionCoordinator') is not True:
            raise ValueError('production coordinator/collection missing')
        owners.add(shot['collectionID'])
        if shot.get('vmID') != vm or shot.get('modelSHA256') != model_hash:
            raise ValueError('model identity/content changed')
        box = _frame(shot.get('viewport'), 'viewport')
        if box[2] <= 1 or box[3] <= 1 or not _positive_int(shot.get('items')):
            raise ValueError('empty/nonfinite viewport')
        if abs(box[2] - width) > 1 or abs(box[3] - viewport_height) > 1:
            raise ValueError('viewport dimensions changed')
        if not _finite(shot.get('offset')) or not _finite(shot.get('contentHeight')) or shot['contentHeight'] <= 0:
            raise ValueError('invalid content geometry')
        rows = shot.get('visibleRows')
        if not isinstance(rows, list) or not rows:
            raise ValueError('visible rows missing')
        seen = set()
        positive_rows = 0
        for row in rows:
            if not isinstance(row, dict) or not _nonnegative_int(row.get('index')) or row['index'] >= shot['items'] or row['index'] in seen:
                raise ValueError('invalid/duplicate visible row')
            seen.add(row['index'])
            if not identity(row.get('cellID')) or not _nonnegative_int(row.get('generation')) or row.get('inWindow') is not True:
                raise ValueError('hidden/unidentified visible row')
            frame = _frame(row.get('frame'), 'cell frame')
            # UIKit may list a collapsed non-text row among visible index paths.
            # The production quiet assistantFooter intentionally measures zero.
            # Preserve it as data, but never count it as visible content or allow
            # a zero-height Markdown row through this exception. This does not
            # identify its row type or certify that its layout is correct.
            collapsed_nontext = (frame[3] == 0 and _finite(row.get('cache')) and row['cache'] == 0
                                 and not any(k in row for k in ('textBounds', 'textID', 'textLength', 'markdownSHA256')))
            if frame[2] <= 0 or frame[3] < 0 or (frame[3] == 0 and not collapsed_nontext):
                raise ValueError('empty visible row')
            positive_rows += frame[3] > 0
            if row.get('cache') is not None and (not _finite(row['cache']) or row['cache'] < 0):
                raise ValueError('invalid cache snapshot')
            if 'textBounds' in row:
                text_box = _frame(row['textBounds'], 'text bounds')
                finite_text_seen = finite_text_seen or (text_box[2] > 1 and text_box[3] > 0)
                if not identity(row.get('textID')) or not _positive_int(row.get('textLength')) or not digest(row.get('markdownSHA256')):
                    raise ValueError('text identity/content missing')
                current = (row['markdownSHA256'], row['textLength'])
                if row['index'] in row_hashes and row_hashes[row['index']] != current:
                    raise ValueError('rendered row content changed')
                row_hashes[row['index']] = current
        if not positive_rows:
            raise ValueError('no positive-area visible content')
    if shots[-1]['t'] < end['t'] or start['owner'] != shots[0]['collectionID'] or end['owner'] != shots[-1]['collectionID']:
        raise ValueError('snapshot/marker boundary mismatch')
    if any(m['owner'] not in owners or m['subject'] != start['subject'] or not m['main'] for m in marks):
        raise ValueError('marker ownership mismatch')

    phases = ('begin', 'end', 'finger-began', 'finger-ended', 'leave', 'reentered')
    if any(m['phase'] not in phases for m in marks):
        raise ValueError('unknown replay marker')
    for phase, key, states in (('finger-began', 'gestureBegins', (1,)), ('finger-ended', 'gestureEnds', (3, 4))):
        gestures = [m for m in marks if m['phase'] == phase]
        if len(gestures) != data[key]:
            raise ValueError('gesture count mismatch')
        for ordinal, marker in enumerate(gestures, 1):
            if marker['values'].get('gesture') != ordinal or marker['values'].get('state') not in states:
                raise ValueError('gesture ordinal/state mismatch')
    active_gesture = False
    for marker in marks:
        if marker['phase'] == 'finger-began':
            if active_gesture: raise ValueError('overlapping gesture begins')
            active_gesture = True
        elif marker['phase'] == 'finger-ended':
            if not active_gesture: raise ValueError('orphan gesture end')
            active_gesture = False

    # A timeout/background transition is never success, even if an action was
    # interrupted before its matching reentry snapshot could be taken.
    if data.get('finishReason') != 'user-finished':
        return incomplete(str(data.get('finishReason')))
    if active_gesture:
        return incomplete('capture ended during a gesture')
    if data['reentries'] == 0 and len(owners) != 1:
        raise ValueError('unannounced collection change')
    if [s['phase'] for s in shots] != ['begin'] + ['before-leave', 'after-reentry'] * data['reentries'] + ['end']:
        raise ValueError('unpaired snapshot phases')
    for phase, shot_phase in (('leave', 'before-leave'), ('reentered', 'after-reentry')):
        actions = [m for m in marks if m['phase'] == phase]
        action_shots = [s for s in shots if s['phase'] == shot_phase]
        if len(actions) != data['reentries'] or len(action_shots) != len(actions):
            raise ValueError('reentry marker/snapshot count mismatch')
        for ordinal, (marker, shot) in enumerate(zip(actions, action_shots), 1):
            if marker['values'].get('count') != ordinal or marker['owner'] != shot['collectionID'] or marker['t'] > shot['t']:
                raise ValueError('reentry ordinal/snapshot mismatch')
    action_order = [m['phase'] for m in marks if m['phase'] in ('leave', 'reentered')]
    if action_order != ['leave', 'reentered'] * data['reentries']:
        raise ValueError('unpaired reentry actions')

    motions = [e for e in events if e['kind'] == 'viewport' and e['phase'] == 'did-scroll'
               and e['owner'] in owners and e['subject'] == vm and e['main']
               and (e['values'].get('tracking') == 1 or e['values'].get('decel') == 1)
               and _finite(e['values'].get('offset'))
               and _finite(e['values'].get('width')) and abs(e['values']['width'] - width) <= 1]
    if not data['gestureBegins'] or not data['gestureEnds'] or not motions:
        return incomplete('physical scroll not independently observed on the captured production list')
    if not any(e['values'].get('decel') == 1 for e in motions):
        return incomplete('no native deceleration witness')
    configurations = {(e['owner'], e['subject'], e['generation']) for e in events
                      if e['kind'] == 'configure' and e['owner'] in owners and e['parent'] == vm
                      and e['idx'] >= 0 and e['main']}
    if not configurations:
        return incomplete('no production row configuration in capture window')
    if not finite_text_seen or not row_hashes or not any(e['kind'] == 'text-size' and e['main']
        and (e['owner'], e['parent'], e['generation']) in configurations for e in events):
        return incomplete('no configuration-linked native Markdown measurement')
    return {'status': 'CAPTURE_VALID',
            'reason': 'intact production-list/input/physical-motion capture; bug/repair verdict requires correlated analysis',
            'events': len(events), 'gestureBegins': data['gestureBegins'], 'reentries': data['reentries']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    parser.add_argument('--commit', required=True, help='verified artifact source SHA, not a value read from the report')
    parser.add_argument('--input-sha', required=True, help='SHA256 of the original local input file')
    args = parser.parse_args()
    try:
        result = validate(json.loads(args.report.read_text()), args.commit, args.input_sha)
    except (ValueError, KeyError, TypeError, OSError) as error:
        result = {'status': 'INVALID', 'reason': str(error)}
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['status'] == 'CAPTURE_VALID' else 1 if result['status'] == 'INCOMPLETE' else 2)
