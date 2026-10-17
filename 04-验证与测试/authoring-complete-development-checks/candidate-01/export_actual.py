"""Explicit integration preview: fixed backend07 app plus this new router, no deployed main edit."""
import json
from pathlib import Path
import sys
from jsonschema import Draft202012Validator
HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'frozen-tree'))
from apps.api.main import app
from apps.api.authoring_structure import router
from packages.authoring.structure_contracts import extend_openapi
app.include_router(router)
actual=extend_openapi(app.openapi())
for name,schema in actual['components']['schemas'].items():
    Draft202012Validator.check_schema(schema)
def visit(value):
    if isinstance(value,dict):
        if '$ref' in value:
            reference=value['$ref']
            if reference.startswith('#/'):
                current=actual
                for part in reference[2:].split('/'):
                    current=current[part.replace('~1','/').replace('~0','~')]
        for child in value.values():visit(child)
    elif isinstance(value,list):
        for child in value:visit(child)
visit(actual)
(HERE/'actual.openapi.json').write_text(json.dumps(actual,ensure_ascii=False,indent=2)+'\n')
methods={'get','post','patch','put','delete','head','options'}
print(json.dumps({'paths':len(actual['paths']),'operations':sum(k in methods for p in actual['paths'].values() for k in p),'schemas':len(actual['components']['schemas']),'scope':'fixed backend07 plus explicit new router preview; not live main deployment'}))
