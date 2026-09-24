#!/usr/bin/env python3
"""Explicit functional-candidate gate; the observation-only gate is unchanged."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import subprocess

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
BASELINE = '2f21e242df71d63576682f8ac61c50a097f3a5ae'
LAYOUT = 'src/ios/Agent/MessageList/MessageListLayout.swift'
ALLOWED = {LAYOUT}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def git_blob(data):
    return hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def tree(repository, revision):
    raw = subprocess.check_output(['git', '-C', str(repository), 'ls-tree', '-rz', revision, '--', 'src'])
    result = {}
    for row in raw.split(b'\0'):
        if not row:
            continue
        metadata, path = row.split(b'\t', 1)
        mode, kind, digest = metadata.decode().split()
        require(kind == 'blob' and mode in ('100644', '100755'), 'unexpected source object: ' + path.decode())
        result[path.decode()] = digest
    require(LAYOUT in result, 'baseline layout missing')
    return result


def check_tree(root, baseline_tree, baseline_allowed_hashes, profile):
    root = Path(root)
    require(profile.get('schema') == 1 and profile.get('baselineCommit') == BASELINE,
            'candidate profile must name the immutable baseline')
    changes = profile.get('productionChanges', {})
    require(set(changes) == ALLOWED, 'production allowlist must contain only MessageListLayout.swift')
    for name in ALLOWED:
        record = changes[name]
        require(record.get('baselineSHA256') == baseline_allowed_hashes[name], 'approved baseline hash mismatch')
        require(record.get('candidateSHA256') != record.get('baselineSHA256'), 'candidate must not self-compare to baseline')
    changed = []
    for name, digest in baseline_tree.items():
        path = root / name
        require(path.is_file() and not path.is_symlink(), 'source missing or symlinked: ' + name)
        data = path.read_bytes()
        if name in ALLOWED:
            require(sha(data) == changes[name].get('candidateSHA256'), 'unapproved candidate bytes: ' + name)
        else:
            require(git_blob(data) == digest, 'unrelated production source changed: ' + name)
        if git_blob(data) != digest:
            changed.append(name)
    require(set(changed) == ALLOWED, 'actual production diff does not equal the approved allowlist')
    # Generated xcconfig is expected by the existing build, but extra compiler
    # sources are never allowed to evade the tracked-tree comparison.
    source_suffixes = {'.swift', '.m', '.mm', '.c', '.cc', '.cpp', '.h', '.hpp', '.metal'}
    extras = [str(p.relative_to(root)) for p in (root / 'src').rglob('*')
              if p.is_file() and p.suffix in source_suffixes and str(p.relative_to(root)) not in baseline_tree]
    require(not extras, 'untracked compiler sources: ' + ', '.join(extras))
    return {'kind': 'height-repair-functional-source-gate', 'baselineCommit': BASELINE,
            'productionPathsCompared': len(baseline_tree), 'approvedProductionChanges': changes,
            'otherProductionSourcesUnchanged': True, 'verified': True}


def verify(root, repository, profile_path=None, revision='HEAD'):
    repository = Path(repository)
    profile_path = Path(profile_path or HERE / 'candidate-source.json')
    raw_profile = profile_path.read_bytes()
    baseline = tree(repository, BASELINE)
    committed = tree(repository, revision)
    require(set(committed) == set(baseline), 'tracked production paths changed outside allowlist')
    require({name for name in baseline if baseline[name] != committed[name]} == ALLOWED,
            'committed production diff does not equal the approved allowlist')
    untracked = subprocess.check_output(['git', '-C', str(repository), 'ls-files', '--others', '--exclude-standard', '--', 'src'], text=True)
    require(not untracked.strip(), 'untracked production files must not enter candidate build')
    hashes = {name: sha(subprocess.check_output(['git', '-C', str(repository), 'show', BASELINE + ':' + name]))
              for name in ALLOWED}
    result = check_tree(root, baseline, hashes, json.loads(raw_profile))
    for name in ALLOWED:
        require(git_blob((Path(root) / name).read_bytes()) == committed[name],
                'working source differs from the pinned commit: ' + name)
    result['profileSHA256'] = sha(raw_profile)
    result['buildCommit'] = subprocess.check_output(['git', '-C', str(repository), 'rev-parse', revision], text=True).strip()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    parser.add_argument('--repository', type=Path, default=ROOT)
    parser.add_argument('--profile', type=Path)
    parser.add_argument('--commit', default='HEAD')
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = verify(args.root, args.repository, args.profile, args.commit)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
