#!/usr/bin/env python3
"""Inspect a recorded native WeCom document envelope without opening a client.

content/file_path are mutually exclusive JSON-text fields, not filesystem paths.
Default output contains structural metadata, formula signatures and URL hashes.
Use --tree-output explicitly to save the complete parsed document tree.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

NodePath = tuple[str | int, ...]
MISSING = object()
RUN_METADATA_KEYS = frozenset(('author', 'modifyUnix'))


class SnapshotError(ValueError):
    pass


def digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode('utf-8')).hexdigest()


def path_string(path: NodePath) -> str:
    return ''.join('/' + str(part) for part in path)


def walk_nodes(node: dict, path: NodePath = ()):
    yield node, path
    if 'children' in node:
        children = node['children']
        if not isinstance(children, list) or any(not isinstance(child, dict) for child in children):
            raise SnapshotError(f'Invalid native children at {path_string(path) or "/"}')
        for index, child in enumerate(children):
            yield from walk_nodes(child, path + ('children', index))


def load_snapshot(path: Path) -> tuple[dict, dict, str]:
    wrapper = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(wrapper, dict) or type(wrapper.get('errcode')) is not int or wrapper['errcode'] != 0:
        raise SnapshotError('Expected a successful native snapshot envelope with integer errcode=0')
    fields = [key for key in ('content', 'file_path') if key in wrapper]
    if len(fields) != 1 or not isinstance(wrapper[fields[0]], str):
        raise SnapshotError('Require exactly one JSON-text content/file_path field; file_path is not a disk path')
    tree = json.loads(wrapper[fields[0]])
    if not isinstance(tree, dict) or tree.get('type') != 'Document':
        raise SnapshotError('Snapshot payload must be a native Document node tree')
    for node, node_path in walk_nodes(tree):
        if not isinstance(node.get('type'), str) or not isinstance(node.get('property'), dict):
            raise SnapshotError(f'Invalid native node at {path_string(node_path) or "/"}')
        if type(node.get('begin')) is not int or type(node.get('end')) is not int:
            raise SnapshotError(f'Invalid native begin/end at {path_string(node_path) or "/"}')
    for key in ('property', 'numbering', 'styles'):
        if not isinstance(tree.get(key), dict):
            raise SnapshotError(f'Document.{key} must be an object')
    return wrapper, tree, fields[0]


def pointer(value: object, expression: str):
    current = value
    for component in expression.split('/')[1:]:
        if not isinstance(current, dict) or component not in current:
            return MISSING
        current = current[component]
    return current


def native_node_fields(node: dict) -> dict:
    """Normalize node GCPs and only real Math event GCPs, preserving all roles."""
    result = {key: deepcopy(value) for key, value in node.items() if key not in ('begin', 'end', 'children')}
    if node['type'] == 'Math':
        events = result.get('meta', {}).get('math')
        if not isinstance(events, list) or not events:
            raise SnapshotError('Math.meta.math must be a nonempty event list')
        for event in events:
            if not isinstance(event, dict) or type(event.get('pos')) is not int:
                raise SnapshotError('Each Math.meta.math event requires an integer pos')
            event['pos'] -= node['begin']
    return result


def without_positions(node: dict) -> dict:
    result = native_node_fields(node)
    if 'children' in node:
        result['children'] = [without_positions(child) for child in node['children']]
    return result


def document_identity(wrapper: dict) -> tuple[str, str, str] | None:
    value = wrapper.get('url')
    if not isinstance(value, str):
        return None
    try:
        parts = urlsplit(value)
        if parts.scheme not in ('http', 'https') or not parts.hostname or not parts.path:
            return None
        return parts.scheme, parts.netloc.lower(), parts.path
    except ValueError:
        return None


def structural_counts(tree: dict) -> dict:
    nodes = list(walk_nodes(tree))
    counts = Counter(node['type'] for node, _ in nodes)
    math_paths = {path for node, path in nodes if node['type'] == 'Math'}
    outer = sum(not any(path[:length] in math_paths for length in range(0, len(path))) for path in math_paths)
    return {'table': counts['Table'], 'row': counts['TableRow'], 'cell': counts['TableCell'],
            'drawing': counts['Drawing'], 'math_nodes': counts['Math'], 'outer_math': outer,
            'node_types': dict(counts)}


def math_signature(math: dict) -> dict:
    """Preserve native roles/text/mRPr; express each Math event pos from its begin.

    Run author/modifyUnix and other Run formatting are reported separately from
    the semantic signature. No empty-on/off value normalization is performed.
    """
    if math.get('type') != 'Math':
        raise SnapshotError('A formula signature requires a native Math node')

    def visit(node: dict):
        result = native_node_fields(node)
        if node['type'] == 'Run':
            result['property'] = {key: deepcopy(value) for key, value in node['property'].items() if key == 'mRPr'}
        if 'children' in node:
            result['children'] = [visit(child) for child in node['children']]
        return result

    return visit(math)


def redact_urls(value: object) -> object:
    if isinstance(value, str) and '://' in value:
        return {'redacted_url_or_text': True, 'length': len(value), 'sha256': digest(value)}
    if isinstance(value, dict):
        return {key: redact_urls(child) for key, child in value.items()}
    if isinstance(value, list):
        return [redact_urls(child) for child in value]
    return value


def inspection(tree: dict) -> dict:
    drawings, maths = [], []
    outer_paths = []
    for node, path in walk_nodes(tree):
        if node['type'] == 'Drawing':
            entry = {'path': path_string(path), 'gcp': [node['begin'], node['end']]}
            for label, expression in (
                ('docpr_id', '/property/inlineKeyword/docPr/id'),
                ('textbox_id', '/property/inlineKeyword/graphic/graphicData/wsp/txbx/contentId'),
            ):
                value = pointer(node, expression)
                if value is not MISSING:
                    entry[label] = redact_urls(value)
            embed = pointer(node, '/property/inlineKeyword/graphic/graphicData/pic/blipFill/blip/embed')
            entry['has_image_embed'] = isinstance(embed, str) and bool(embed)
            if entry['has_image_embed']:
                entry['image_embed_sha256'] = digest(embed)
            drawings.append(entry)
        elif node['type'] == 'Math' and not any(path[:len(parent)] == parent for parent in outer_paths):
            outer_paths.append(path)
            signature = math_signature(node)
            runs = [run for run, _ in walk_nodes(node) if run['type'] == 'Run']
            styles = [{key: deepcopy(value) for key, value in run['property'].items() if key not in RUN_METADATA_KEYS}
                      for run in runs]
            maths.append({'path': path_string(path), 'gcp': [node['begin'], node['end']],
                          'semantic_sha256': digest(signature), 'semantic_signature': redact_urls(signature),
                          'run_styles': redact_urls(styles),
                          'run_metadata_field_names': [sorted(set(run['property']) & RUN_METADATA_KEYS) for run in runs]})
    return {'counts': structural_counts(tree), 'drawing_candidates': drawings,
            'outer_math_candidates': maths}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path, help='Recorded native success envelope')
    parser.add_argument('--tree-output', type=Path, help='Explicitly save the full parsed tree to a new local file')
    args = parser.parse_args()
    try:
        wrapper, tree, field = load_snapshot(args.input)
        result = inspection(tree)
        identity = document_identity(wrapper)
        result.update(payload_field=field, document_identity_sha256=digest(identity) if identity else None,
                      tree_sha256=digest(tree), tree_saved=False)
        if args.tree_output:
            with args.tree_output.open('x', encoding='utf-8') as handle:
                json.dump(tree, handle, ensure_ascii=False, indent=2)
                handle.write('\n')
            result['tree_saved'] = True
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (SnapshotError, json.JSONDecodeError, OSError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
