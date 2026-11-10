"""Validate options for the reviewed formatting templates before generation."""
import math
import re


def obj(value, allowed, required=()):
    if not isinstance(value, dict):
        raise ValueError('Expected an object')
    unknown = set(value) - set(allowed)
    missing = set(required) - set(value)
    if unknown or missing:
        raise ValueError(f'Unknown fields: {sorted(unknown)}; missing fields: {sorted(missing)}')


def string(value, name):
    if not isinstance(value, str) or not value:
        raise ValueError(f'{name} must be a nonempty string')


def integer(value, name, minimum=0, maximum=100000):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f'{name} must be an integer in {minimum}..{maximum}')


def choice(value, choices, name):
    if value not in choices:
        raise ValueError(f'{name} must be one of {choices}')


def boolean(value, name):
    if type(value) is not bool:
        raise ValueError(f'{name} must be boolean')


def validate(kind, options):
    string(options.get('docid'), 'docid')
    if not re.fullmatch(r'[A-Za-z0-9_-]+', options['docid']):
        raise ValueError('docid must be a document ID, not a URL')
    if kind == 'format':
        obj(options, ('docid', 'operations'), ('docid', 'operations'))
        ops = options['operations']
        if not isinstance(ops, list) or not ops:
            raise ValueError('operations must be a nonempty array')
        for op in ops:
            if not isinstance(op, dict):
                raise ValueError('Each operation must be an object')
            typ = op.get('type')
            fields = {
                'heading': ('text', 'level'),
                'font': ('text', 'bold', 'italic', 'underline', 'strike', 'color', 'shading', 'sizePt', 'family', 'verticalAlign'),
                'paragraph': ('text', 'alignment', 'leftIndentTwips', 'spaceBeforeTwips', 'spaceAfterTwips', 'lineSpacing'),
                'quote': ('text',), 'list': ('fromText', 'toText', 'kind'),
                'task-state': ('text', 'state'),
            }
            if not isinstance(typ, str) or typ not in fields:
                raise ValueError(f'Unsupported operation type: {typ}')
            required = fields[typ] if typ not in ('font', 'paragraph') else ('text',)
            obj(op, ('type', *fields[typ]), ('type', *required))
            for key in ('text', 'fromText', 'toText'):
                if key in op:
                    string(op[key], key)
            if typ == 'heading':
                integer(op['level'], 'level', 1, 2)
            elif typ == 'font':
                if len(op) == 2:
                    raise ValueError('font requires at least one format field')
                for key in ('bold', 'italic', 'strike'):
                    if key in op: boolean(op[key], key)
                if 'underline' in op: choice(op['underline'], ('single',), 'underline')
                if 'verticalAlign' in op: choice(op['verticalAlign'], ('superscript', 'subscript'), 'verticalAlign')
                for key in ('color', 'shading'):
                    if key in op and (not isinstance(op[key], str) or not re.fullmatch(r'[0-9A-Fa-f]{6}', op[key])):
                        raise ValueError(f'{key} must be RRGGBB without #')
                if 'sizePt' in op:
                    n = op['sizePt']
                    if type(n) not in (int, float) or not math.isfinite(n) or not 1 <= n <= 200 or n * 20 != int(n * 20):
                        raise ValueError('sizePt must be 1..200 and representable in whole twips')
                if 'family' in op:
                    obj(op['family'], ('ascii', 'hAnsi', 'eastAsia', 'cs'), ('ascii', 'hAnsi', 'eastAsia', 'cs'))
                    for key, value in op['family'].items(): string(value, key)
            elif typ == 'paragraph':
                if len(op) == 2:
                    raise ValueError('paragraph requires at least one format field')
                if 'alignment' in op: choice(op['alignment'], ('center',), 'alignment')
                for key in ('leftIndentTwips', 'spaceBeforeTwips', 'spaceAfterTwips'):
                    if key in op: integer(op[key], key)
                if 'lineSpacing' in op: integer(op['lineSpacing'], 'lineSpacing', 360, 360)
            elif typ == 'list': choice(op['kind'], ('numbered', 'bullet', 'task'), 'kind')
            elif typ == 'task-state': choice(op['state'], ('todo', 'done'), 'state')
    elif kind == 'insert-table':
        obj(options, ('docid', 'anchorText', 'rows', 'cols', 'cells', 'header'), ('docid', 'anchorText', 'rows', 'cols', 'cells'))
        string(options['anchorText'], 'anchorText')
        integer(options['rows'], 'rows', 1, 100)
        integer(options['cols'], 'cols', 1, 100)
        cells = options['cells']
        if not isinstance(cells, list) or len(cells) != options['rows'] or any(not isinstance(row, list) or len(row) != options['cols'] or any(not isinstance(c, str) or any(ord(ch) < 32 for ch in c) for c in row) for row in cells):
            raise ValueError('cells must match rows/cols and contain single-line strings without control characters')
        if 'header' in options: boolean(options['header'], 'header')
    elif kind == 'codeblock':
        action = options.get('action')
        choice(action, ('create', 'update'), 'action')
        common = ('docid', 'action', 'contentId', 'language', 'theme', 'wordWrap')
        extra = ('anchorText', 'code') if action == 'create' else ('gcp',)
        obj(options, (*common, *extra), ('docid', 'action', 'contentId', *extra))
        string(options['contentId'], 'contentId')
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', options['contentId']):
            raise ValueError('contentId must contain 1..128 ASCII letters, digits, _ or -')
        if action == 'create':
            string(options['anchorText'], 'anchorText')
            string(options['code'], 'code')
            if any(ord(c) < 32 and c not in '\n\t' for c in options['code']):
                raise ValueError('code may contain LF/tab, but no other control characters')
        else:
            integer(options['gcp'], 'gcp', 0, 2**31 - 1)
            if not any(k in options for k in ('language', 'theme', 'wordWrap')):
                raise ValueError('update requires at least one codeblock property')
        for key in ('language', 'theme'):
            if key in options: string(options[key], key)
        if 'wordWrap' in options: boolean(options['wordWrap'], 'wordWrap')
    else:
        raise ValueError(f'Unsupported formatting kind: {kind}')
