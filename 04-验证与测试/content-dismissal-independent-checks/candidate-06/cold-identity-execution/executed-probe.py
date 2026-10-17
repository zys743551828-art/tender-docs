"""Freeze exact loaded-source and actual OpenAPI identity without starting service."""
from pathlib import Path
import hashlib
import json
import os
import sys

from apps.api.export_openapi import implemented_openapi
from apps.api.main import app


def main():
    root = Path(os.environ['QA_CONTENT_ROOT'])
    execution = (root / 'execution-inputs').resolve()
    candidate = json.loads((root / 'candidate-06.json').read_text())
    assert hashlib.sha256((root / 'candidate-06.json').read_bytes()).hexdigest() == 'b86d5c3c818c18b09ad42720f318b61852a99737a82bac017637d7aec08cd60f'
    assert len(candidate['runtime_sources']) == 266 and len(candidate['sources']) == 11
    assert all(hashlib.sha256((execution / item['path']).read_bytes()).hexdigest() == item['sha256'] for item in candidate['runtime_sources'])
    assert all(hashlib.sha256((root / 'own-source-inputs' / item['path']).read_bytes()).hexdigest() == item['sha256'] for item in candidate['sources'])
    loaded = []
    for name, module in sorted(sys.modules.items()):
        path = getattr(module, '__file__', None)
        if not path or not name.startswith(('apps.', 'packages.')):
            continue
        resolved = Path(path).resolve()
        assert resolved.is_relative_to(execution), name
        loaded.append(dict(module=name, path=str(resolved.relative_to(execution)), sha256=hashlib.sha256(resolved.read_bytes()).hexdigest()))
    actual = implemented_openapi(app)
    encoded = (json.dumps(actual, ensure_ascii=False, indent=2) + '\n').encode()
    actual_sha = hashlib.sha256(encoded).hexdigest()
    assert actual_sha == candidate['actual_openapi']['sha256']
    out = root / 'cold-identity-execution'
    out.mkdir(exist_ok=False)
    (out / 'actual.openapi.json').write_bytes(encoded)
    result = dict(passed=True, candidate_sha256=hashlib.sha256((root / 'candidate-06.json').read_bytes()).hexdigest(),
                  runtime_verified_source_count=266, own_verified_source_count=11,
                  actual_openapi_sha256=actual_sha, paths=len(actual['paths']),
                  operations=sum(method in ('get', 'post', 'put', 'delete', 'patch', 'head', 'options') for methods in actual['paths'].values() for method in methods),
                  schemas=len(actual['components']['schemas']), loaded_sources=loaded,
                  service_started=False, provider_calls=0, database_connections=0,
                  probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (out / 'executed-probe.py').write_bytes(Path(__file__).read_bytes())
    (out / 'result.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print({k: v for k, v in result.items() if k != 'loaded_sources'})


if __name__ == '__main__':
    main()
