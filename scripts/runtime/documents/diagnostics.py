"""Metadata-only progress on stderr and in each private run directory."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sys


def event(directory: Path, name: str, **metadata: object) -> None:
    record = {'time_utc': datetime.now(timezone.utc).isoformat(), 'event': name, **metadata}
    line = json.dumps(record, ensure_ascii=False)
    with (directory / 'diagnostics.jsonl').open('a', encoding='utf-8') as stream:
        stream.write(line + '\n')
    print(line, file=sys.stderr, flush=True)
