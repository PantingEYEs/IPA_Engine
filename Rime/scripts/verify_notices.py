#!/usr/bin/env python3
# Copyright (c) 2026 PantingEYEs. All rights reserved.
"""Check retained native notices against their reviewed source inventory offline."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]

def verify(root=ROOT):
    root = Path(root)
    inventory = json.loads((root / 'licenses/native-sources.json').read_text())
    for record in inventory['notices']:
        path = (root / 'licenses' / record['output']).resolve()
        if not path.is_relative_to((root / 'licenses').resolve()):
            raise ValueError('Unexpected notice location')
        content = path.read_bytes()
        if len(content) != record['sizeBytes'] or hashlib.sha256(content).hexdigest() != record['sha256']:
            raise ValueError('Native notice differs from reviewed source: ' + record['component'])
    for record in inventory['sharedNotices']:
        path = (root / record['pathFromRime']).resolve()
        if not path.is_relative_to((root.parent / 'OpenCC/licenses').resolve()):
            raise ValueError('Unexpected shared notice location')
        if hashlib.sha256(path.read_bytes()).hexdigest() != record['sha256']:
            raise ValueError('Shared notice differs from reviewed source')
    return {'nativeNotices': len(inventory['notices']), 'sharedNotices': len(inventory['sharedNotices'])}

if __name__ == '__main__':
    print(json.dumps(verify()))
