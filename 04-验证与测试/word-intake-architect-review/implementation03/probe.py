"""Exact original W01/W03 bytes, pure checks, and one own non-field local Docker conversion."""
from pathlib import Path
import hashlib,json,sys,os,importlib.util
from unittest.mock import patch
HERE=Path(__file__).resolve().parent;DOCS=HERE.parents[1];APP=Path('/workspace/tender-workbench');BASE=DOCS/'word-intake-development-checks/candidate-03';p=BASE/'candidate.json';assert hashlib.sha256(p.read_bytes()).hexdigest()=='b12e2e456ead217296510ae6fde3f7747198d75adffe42706bb3692c28406fa6';m=json.loads(p.read_text());ROOT=BASE/'frozen-inputs';DEPS=DOCS/'assistant-development-checks/implementation-01/frozen-tree'
def verify():
    for e in m['sources']:assert hashlib.sha256((BASE/e['frozen_path']).read_bytes()).hexdigest()==e['sha256']
verify();sys.path[:0]=[str(ROOT),str(ROOT/'tests'),str(DEPS)];os.environ['PYTEST_DISABLE_PLUGIN_AUTOLOAD']='1'
from packages.word_intake import inspection
from test_word_intake import make_docx
from packages.domain import storage,runtime
import httpx,pytest
original=[]
for folder,names in [('implementation01',['header1.xml.docx','header1.XML.docx','header1.dat.docx']),('implementation02',['field-runs-0.docx','field-runs-1.docx','field-runs-2.docx'])]:
    for name in names:
        raw=(HERE.parent/folder/name).read_bytes();v=inspection.inspect_docx(raw);assert not v['conversion_eligible'] and 'FIELD_EVALUATION_UNSUPPORTED' in v['active_content_gaps'];original.append({'path':folder+'/'+name,'sha256':hashlib.sha256(raw).hexdigest(),'conversion_eligible':v['conversion_eligible'],'active_content_gaps':v['active_content_gaps']})
plain=make_docx('普通文本 PAGE TOC DDEAUTO 施工工期120天，质量合格。');v=inspection.inspect_docx(plain);assert v['conversion_eligible'] and not v['active_content_gaps']
(HERE/'original-delta.json').write_text(json.dumps({'candidate_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'original_six':original,'plain_keyword_text_eligible':True},ensure_ascii=False,indent=2)+'\n')
TESTROOT=HERE/'unit-runtime';testfile=TESTROOT/'tests/test_word_intake_fixed03.py';testfile.parent.mkdir(parents=True,exist_ok=True);testfile.write_bytes((ROOT/'tests/test_word_intake.py').read_bytes());schema=TESTROOT/'packages/contracts/openapi/openapi.json';schema.parent.mkdir(parents=True,exist_ok=True);schema.write_bytes((DEPS/'packages/contracts/openapi/openapi.json').read_bytes())
def forbidden(*a,**k):raise AssertionError('DB/object/remote port forbidden')
with patch.object(runtime,'connection',forbidden),patch.object(storage,'client',forbidden),patch.object(storage,'put',forbidden),patch.object(storage,'get',forbidden),patch.object(httpx.HTTPTransport,'handle_request',forbidden):
    code=pytest.main([str(testfile),'-q','--assert=plain','--tb=short','-c','/dev/null','--junitxml='+str(HERE/'pure-junit.xml')]);assert code==0
    # The installed immutable renderer profile binds its original absolute runner mount.
    # Load only these exact source bytes from their original paths, with fixed domain deps.
    installed=[]
    for path in ['packages/word_intake/renderer.py','packages/word_intake/inspection.py','packages/word_intake/sandbox_runner.py']:
        entry=next(e for e in m['sources'] if e['path']==path);actual=APP/path;assert hashlib.sha256(actual.read_bytes()).hexdigest()==entry['sha256'];installed.append({'path':str(actual),'sha256':entry['sha256']})
    spec=importlib.util.spec_from_file_location('architect_verified_word_renderer03',APP/'packages/word_intake/renderer.py');renderer=importlib.util.module_from_spec(spec);spec.loader.exec_module(renderer)
    os.environ['WORD_RENDER_PROFILE_PATH']=m['renderer']['private_profile_path'];profile=renderer.configured_profile();assert profile['profile_digest']==m['renderer']['profile_digest']
    source=make_docx('独立复核施工工期120天，质量合格。');(HERE/'own-source.docx').write_bytes(source)
    outcome=renderer.render(source,profile);pdf=outcome.pop('pdf');assert outcome['state']=='succeeded' and outcome['coverage']=='complete' and outcome['gaps']==[],outcome
    assert all(value is True for value in outcome['isolation'].values()) and outcome['isolation']['own_job_cleanup_confirmed']
    assert pdf and pdf.startswith(b'%PDF-') and hashlib.sha256(pdf).hexdigest()==outcome['chain']['pdf_digest'];(HERE/'own-output.pdf').write_bytes(pdf)
    for entry in installed:assert hashlib.sha256(Path(entry['path']).read_bytes()).hexdigest()==entry['sha256']
verify()
(HERE/'real-render-result.json').write_text(json.dumps({'candidate_sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'profile_digest':profile['profile_digest'],'installed_mount_sources_verified_before_after':installed,'public_schema_auxiliary_sha256':hashlib.sha256(schema.read_bytes()).hexdigest(),'outcome':outcome,'pdf_bytes':len(pdf),'actual_Docker_jobs':1,'PG_queries':0,'paid_calls':0,'object_requests':0,'consumers':0},ensure_ascii=False,indent=2)+'\n')
print({'real_Docker_success':True,'coverage':outcome['coverage'],'pdf_bytes':len(pdf),'cleanup':outcome['isolation']['own_job_cleanup_confirmed']})
