"""Explicit composition preview: fixed F05 main plus actual draft router and task extender."""
import hashlib
import json
from pathlib import Path

from apps.api.main import app
from apps.api.export_openapi import implemented_openapi
from apps.api.task_draft_routes import router
from packages.tasks.contracts import extend_openapi

app.include_router(router)
actual=implemented_openapi(app)
extend_openapi(actual)
out=Path(__file__).resolve().parent
raw=json.dumps(actual,ensure_ascii=False,indent=2)+'\n'
(out/'actual-task-draft02.openapi.json').write_text(raw)
print({'sha256':hashlib.sha256(raw.encode()).hexdigest(),'paths':len(actual['paths']),
       'operations':sum(1 for methods in actual['paths'].values() for method in methods if method in ('get','post','put','patch','delete')),
       'schemas':len(actual['components']['schemas']),'composition':'fixed main plus registered draft router and task extender; production main remains unchanged'})
