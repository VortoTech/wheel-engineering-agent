"""Read a copied v2 data directory and verify a one-time migration without touching live data."""
import json
import sqlite3
import sys
from pathlib import Path

from wheelcam.models import WheelSpec
from wheelcam.storage import Store

root = Path(sys.argv[1]).resolve()
with sqlite3.connect(root / 'wheelcam.sqlite3') as db:
    before = db.execute('SELECT id,spec,sources,revision FROM projects ORDER BY id').fetchall()
    history = db.execute('SELECT * FROM jobs ORDER BY id').fetchall()
Store(root)
with sqlite3.connect(root / 'wheelcam.sqlite3') as db:
    after = db.execute('SELECT id,spec,sources,revision FROM projects ORDER BY id').fetchall()
    assert db.execute('SELECT * FROM jobs ORDER BY id').fetchall() == history
for original, migrated in zip(before, after, strict=True):
    old_spec, new_spec = json.loads(original[1]), json.loads(migrated[1])
    assert WheelSpec.model_validate(new_spec)
    assert all(new_spec[k] == v for k, v in old_spec.items() if k in WheelSpec.model_fields)
    changed = set(old_spec) != set(WheelSpec.model_fields)
    assert migrated[3] == original[3] + int(changed)
Store(root)
with sqlite3.connect(root / 'wheelcam.sqlite3') as db:
    assert db.execute('SELECT id,spec,sources,revision FROM projects ORDER BY id').fetchall() == after
print(json.dumps({'projects': len(after), 'history_jobs_preserved': len(history), 'one_time_migration': True}))
