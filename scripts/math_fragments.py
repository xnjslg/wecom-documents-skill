#!/usr/bin/env python3
"""Make copied native formula upright-text on/off properties explicit offline.

Input is the recorded {samples: [{innerData: JSON-string, ...}], ...} object.
Only mainStory ModifyRunProp.param.property.mRPr.nor == {} becomes {val: true}.
Other properties, operations, fragment fields and sample metadata are preserved.
The output file is created exclusively; stdout contains counts, not content.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
from pathlib import Path


class FragmentError(ValueError):
    pass


def explicit_upright_fragments(value: object) -> tuple[dict, dict]:
    if not isinstance(value, dict) or not isinstance(value.get('samples'), list):
        raise FragmentError('Expected an object with a samples array')
    output = deepcopy(value)
    sample_counts = []
    operation_count = 0
    for index, sample in enumerate(output['samples']):
        if not isinstance(sample, dict) or not isinstance(sample.get('innerData'), str):
            raise FragmentError(f'samples[{index}].innerData must be a JSON string')
        fragment = json.loads(sample['innerData'])
        if not isinstance(fragment, dict) or not isinstance(fragment.get('mainStory'), list):
            raise FragmentError(f'samples[{index}] fragment.mainStory must be an operation array')
        changed = 0
        for operation in fragment['mainStory']:
            operation_count += 1
            if not isinstance(operation, dict) or not isinstance(operation.get('operationType'), str):
                raise FragmentError(f'samples[{index}] has an invalid mainStory operation')
            if operation['operationType'] != 'ModifyRunProp':
                continue
            param = operation.get('param')
            if not isinstance(param, dict) or not isinstance(param.get('property'), dict):
                raise FragmentError(f'samples[{index}] ModifyRunProp.param.property must be an object')
            math_prop = param['property'].get('mRPr')
            if isinstance(math_prop, dict) and type(math_prop.get('nor')) is dict and not math_prop['nor']:
                math_prop['nor'] = {'val': True}
                changed += 1
        if changed:
            sample['innerData'] = json.dumps(fragment, ensure_ascii=False, separators=(',', ':'))
        sample_counts.append(changed)
    return output, {'ok': True, 'samples': len(sample_counts), 'operations_inspected': operation_count,
                    'empty_nor_made_explicit': sum(sample_counts), 'changes_per_sample': sample_counts}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path, help='New local file for the transformed fragments')
    args = parser.parse_args()
    try:
        value = json.loads(args.input.read_text(encoding='utf-8'))
        output, metadata = explicit_upright_fragments(value)
        with args.output.open('x', encoding='utf-8') as handle:
            json.dump(output, handle, ensure_ascii=False, indent=2)
            handle.write('\n')
        print(json.dumps(metadata, ensure_ascii=False))
        return 0
    except (FragmentError, json.JSONDecodeError, OSError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}, ensure_ascii=False))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
