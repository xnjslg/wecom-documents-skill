#!/usr/bin/env python3
"""Compare native document snapshots outside explicitly excluded node subtrees.

Repeat --before-path and --after-path independently using JSON-pointer node paths
from inspect_snapshot.py. Exclusion counts may differ for insertions/deletions.
Native-node begin/end and absolute Math event pos are normalized; properties,
Math roles and positions relative to each Math begin remain exact.
With no excluded paths it compares the full document. This proves equality
outside the targets, not correctness of the edited targets.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

from inspect_snapshot import (MISSING, NodePath, SnapshotError, digest, document_identity,
                              load_snapshot, native_node_fields, path_string, structural_counts, walk_nodes)


def parse_path(expression: str) -> NodePath:
    if not re.fullmatch(r'(?:/children/(?:0|[1-9][0-9]*))+', expression):
        raise SnapshotError('Excluded paths must identify child nodes, for example /children/0/children/1')
    parts = expression.split('/')[1:]
    return tuple(int(value) if index % 2 else value for index, value in enumerate(parts))


def exclusions(tree: dict, expressions: list[str]) -> tuple[set[NodePath], list[dict]]:
    paths = [parse_path(value) for value in expressions]
    if len(set(paths)) != len(paths):
        raise SnapshotError('Duplicate excluded node path')
    for path in paths:
        if any(other != path and path[:len(other)] == other for other in paths):
            raise SnapshotError('Excluded node paths may not overlap')
    indexed = {path: node for node, path in walk_nodes(tree)}
    entries = []
    for path in paths:
        if path not in indexed:
            raise SnapshotError(f'Excluded node does not exist: {path_string(path)}')
        node = indexed[path]
        entries.append({'path': path_string(path), 'type': node['type'],
                        'gcp': [node['begin'], node['end']], 'subtree_sha256': digest(node)})
    return set(paths), entries


def outside_tree(tree: dict, excluded: set[NodePath]) -> dict:
    def visit(node: dict, path: NodePath):
        result = native_node_fields(node)
        if 'children' in node:
            result['children'] = [visit(child, path + ('children', index))
                                  for index, child in enumerate(node['children'])
                                  if path + ('children', index) not in excluded]
        return result
    return visit(tree, ())


def summary(value: object) -> object:
    if value is MISSING:
        return {'missing': True}
    if isinstance(value, str):
        return {'type': 'string', 'length': len(value), 'sha256': digest(value)}
    if isinstance(value, dict):
        return {'type': 'object', 'keys': list(value), 'sha256': digest(value)}
    if isinstance(value, list):
        return {'type': 'array', 'length': len(value), 'sha256': digest(value)}
    return value


def compare_exact(before: object, after: object, limit: int) -> tuple[int, list[dict]]:
    count, details = 0, []

    def record(path, left, right):
        nonlocal count
        count += 1
        if len(details) < limit:
            details.append({'path': path, 'before': summary(left), 'after': summary(right)})

    def visit(left, right, path=''):
        if type(left) is not type(right):
            record(path, left, right)
        elif isinstance(left, dict):
            for key in sorted(set(left) | set(right)):
                if key not in left or key not in right:
                    record(path + '/' + key, left.get(key, MISSING), right.get(key, MISSING))
                else:
                    visit(left[key], right[key], path + '/' + key)
        elif isinstance(left, list):
            if len(left) != len(right):
                record(path + '/length', len(left), len(right))
            for index, (a, b) in enumerate(zip(left, right)):
                visit(a, b, path + '/' + str(index))
        elif left != right:
            record(path, left, right)

    visit(before, after)
    return count, details


def audit(before: dict, after: dict, before_paths: list[str], after_paths: list[str], limit: int = 20) -> dict:
    old_paths, old_entries = exclusions(before, before_paths)
    new_paths, new_entries = exclusions(after, after_paths)
    old_outside, new_outside = outside_tree(before, old_paths), outside_tree(after, new_paths)
    count, differences = compare_exact(old_outside, new_outside, limit)
    return {'outside_equal': count == 0, 'difference_count': count, 'differences': differences,
            'excluded_before': old_entries, 'excluded_after': new_entries,
            'before_outside_sha256': digest(old_outside), 'after_outside_sha256': digest(new_outside),
            'counts_before': structural_counts(before), 'counts_after': structural_counts(after),
            'outside_counts_before': structural_counts(old_outside), 'outside_counts_after': structural_counts(new_outside),
            'numbering_exactly_preserved': compare_exact(before['numbering'], after['numbering'], 0)[0] == 0,
            'styles_exactly_preserved': compare_exact(before['styles'], after['styles'], 0)[0] == 0,
            'target_correctness_verified': False,
            'scope': 'Only equality outside the explicitly excluded nodes is checked',
            'ignored_fields': ['begin/end on native nodes only; no property fields are ignored'],
            'normalized_fields': ['Math.meta.math[].pos relative to that Math node begin; roles and relative positions remain exact']}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--before', required=True, type=Path)
    parser.add_argument('--after', required=True, type=Path)
    parser.add_argument('--before-path', action='append', default=[], help='Excluded before native-node JSON pointer; repeatable')
    parser.add_argument('--after-path', action='append', default=[], help='Excluded after native-node JSON pointer; repeatable')
    parser.add_argument('--max-differences', type=int, default=20)
    args = parser.parse_args()
    if args.max_differences < 0:
        parser.error('--max-differences must be nonnegative')
    try:
        before_wrapper, before, before_field = load_snapshot(args.before)
        after_wrapper, after, after_field = load_snapshot(args.after)
        result = audit(before, after, args.before_path, args.after_path, args.max_differences)
        before_identity, after_identity = document_identity(before_wrapper), document_identity(after_wrapper)
        same = before_identity is not None and before_identity == after_identity
        result.update(source_identity_matches=same, before_payload_field=before_field, after_payload_field=after_field,
                      outside_audit_pass=result['outside_equal'] and same)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['outside_audit_pass'] else 1
    except (SnapshotError, json.JSONDecodeError, OSError) as error:
        print(json.dumps({'outside_audit_pass': False, 'input_error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
