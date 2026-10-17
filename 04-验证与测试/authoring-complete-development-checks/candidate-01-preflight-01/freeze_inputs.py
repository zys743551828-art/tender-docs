"""Copy reviewed fixed backend07 base plus this slice, recording exact bytes only."""
import hashlib
import json
from pathlib import Path
import shutil

LIVE = Path('/workspace/tender-workbench')
HERE = Path(__file__).resolve().parent
DEST = HERE / 'frozen-tree'
BASE_MANIFEST = LIVE / 'docs/backend07-development-checks/candidate-07.json'
base = json.loads(BASE_MANIFEST.read_text())
base_root = Path(base['runtime_root'])
records = {}
def copy(path, source, layer):
    raw = source.read_bytes()
    target = DEST / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    records[path] = {'path': path, 'sha256': hashlib.sha256(raw).hexdigest(), 'layer': layer}
for item in base['runtime_sources']:
    source = base_root / item['path']
    if hashlib.sha256(source.read_bytes()).hexdigest() != item['sha256']:
        raise RuntimeError('fixed backend07 source changed: ' + item['path'])
    copy(item['path'], source, 'fixed-backend07-base')
own = sorted([str(p.relative_to(LIVE)) for p in (LIVE/'packages/authoring').glob('*.py')] + ['packages/authoring/README.md','packages/authoring/sql/structure.sql','apps/api/authoring.py','apps/api/authoring_structure.py','tests/test_authoring.py','tests/test_authoring_integration.py','tests/test_authoring_structure.py','tests/test_authoring_structure_integration.py'])
for path in own:
    copy(path, LIVE/path, 'authoring-complete-owner')
# Local profile loader is imported by the API. No converter or blocked Word profile is executed here.
for p in sorted((LIVE/'packages/word_intake').glob('*.py')):
    copy(str(p.relative_to(LIVE)), p, 'observed-word-dependency-configuration-only-not-safety-accepted')
manifest = {'backend07': {'path': str(BASE_MANIFEST), 'sha256': hashlib.sha256(BASE_MANIFEST.read_bytes()).hexdigest()}, 'root': str(DEST), 'own_sources': own, 'records': sorted(records.values(), key=lambda r:r['path'])}
(HERE/'inputs.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n')
print(json.dumps({'copied':len(records),'own':len(own),'root':str(DEST)},ensure_ascii=False))
