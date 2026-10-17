"""Frozen02 unit mechanisms and exact original W01 bytes; no Docker/PG/remote ports."""
from pathlib import Path
import json,hashlib,os,sys
from unittest.mock import patch
HERE=Path(__file__).resolve().parent;BASE=HERE.parents[1]/'word-intake-development-checks/candidate-02';p=BASE/'candidate.json';assert hashlib.sha256(p.read_bytes()).hexdigest()=='5dcc4dfad4ee05df0ae326c9bb1f64eff9faaae2abd41e4aa40bb4717e7cb2db';m=json.loads(p.read_text());ROOT=BASE/'frozen-inputs'
def verify():
    for e in m['sources']:assert hashlib.sha256((BASE/e['frozen_path']).read_bytes()).hexdigest()==e['sha256']
verify();DEPS=HERE.parents[1]/'assistant-development-checks/implementation-01/frozen-tree';sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(DEPS)];os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
TESTROOT=HERE/'unit-runtime';testfile=TESTROOT/'tests/test_word_intake.py';testfile.parent.mkdir(parents=True,exist_ok=True);testfile.write_bytes((ROOT/'tests/test_word_intake.py').read_bytes())
schema=TESTROOT/'packages/contracts/openapi/openapi.json';schema.parent.mkdir(parents=True,exist_ok=True);schema.write_bytes((DEPS/'packages/contracts/openapi/openapi.json').read_bytes())
(HERE/'public-schema-auxiliary.json').write_text(json.dumps({'test_copy_sha256':hashlib.sha256(testfile.read_bytes()).hexdigest(),'public_schema_source':str(DEPS/'packages/contracts/openapi/openapi.json'),'public_schema_sha256':hashlib.sha256(schema.read_bytes()).hexdigest(),'reason':'Frozen12 omitted public base OpenAPI resource referenced relative to test.__file__; auxiliary copy only in architect-owned execution tree'},ensure_ascii=False,indent=2)+'\n')
from packages.word_intake import inspection,renderer
from packages.domain import storage,runtime
old=HERE.parent/'implementation01';records=[]
for name in ['header1.xml.docx','header1.XML.docx','header1.dat.docx']:
    raw=(old/name).read_bytes();result=inspection.inspect_docx(raw)
    assert result['conversion_eligible'] is False and 'EXTERNAL_OR_DYNAMIC_FIELD' in result['active_content_gaps']
    records.append({'name':name,'sha256':hashlib.sha256(raw).hexdigest(),'conversion_eligible':result['conversion_eligible'],'active_content_gaps':result['active_content_gaps']})
(HERE/'original-W01-delta.json').write_text(json.dumps({'candidate_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'exact_original_bytes':records},ensure_ascii=False,indent=2)+'\n')
import pytest,httpx
def forbidden(*a,**kw):raise AssertionError('Docker/database/object/remote port forbidden in pure Word02 review')
with patch.object(renderer,'_docker',forbidden),patch.object(storage,'client',forbidden),patch.object(storage,'put',forbidden),patch.object(storage,'get',forbidden),patch.object(runtime,'connection',forbidden),patch.object(httpx.HTTPTransport,'handle_request',forbidden):
    code=pytest.main([str(testfile),'-q','--assert=plain','--tb=short','--junitxml='+str(HERE/'pure-independent-junit.xml')])
verify();assert code==0
