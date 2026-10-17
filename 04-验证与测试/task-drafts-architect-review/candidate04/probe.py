from pathlib import Path
import hashlib,json,shutil,os,sys
out=Path(__file__).parent
candidate=Path('/workspace/tender-docs/04-验证与测试/task-drafts-development-checks/candidate04/candidate.json')
assert hashlib.sha256(candidate.read_bytes()).hexdigest()=='63a3954f61eadd944c666f5d4d7a20a83b622eefacbef9edb6296c038efdaa66'
manifest=json.loads(candidate.read_text()); source=Path(manifest['runtime_root']); dest=out/'runtime'
verified=[]
for item in manifest['sources']:
 p=source/item['path'];assert hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256'],str(p)
 q=dest/item['path'];q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,q);verified.append(item)
(out/'input-record.json').write_text(json.dumps({'candidate_sha256':hashlib.sha256(candidate.read_bytes()).hexdigest(),'verified_sources':verified},indent=2)+'\n')
sys.path.insert(0,str(dest));os.chdir(dest)
from packages.tasks.draft_interpreter import TaskDraftRequest,interpret
cases=[]
for s in ['每天9点以后核对否决项','每天9点之前核对否决项','工作日9:30核对否决项','明天9:30核对否决项','指定9:30核对否决项','每天9:30核对否决项']:
 v=interpret(TaskDraftRequest(request=s));cases.append({'request':s,'trigger':v.trigger.model_dump(),'unresolved':[r.model_dump() for r in v.unresolved],'ready':v.ready_for_template_form})
(out/'temporal-counterexamples.json').write_text(json.dumps(cases,ensure_ascii=False,indent=2)+'\n')
# Only own test fixture is extended; production and frozen candidate files are unchanged.
fixture=(dest/'tests/test_task_drafts_integration.py').read_text()
fixture+='''\n\ndef test_architect_saved_body_has_same_strong_tag(draft_world):
    from pathlib import Path
    client,bids,actors,headers,save=draft_world
    url,first,_,_=create(draft_world,"核对否决条款")
    url+="/"+first.json()["id"]
    before=client.get(url,headers=headers())
    saved=client.post(url+"/save",json={},headers=headers(tag=before.headers["etag"]))
    assert saved.status_code==201,saved.text
    after=client.get(url,headers=headers())
    assert before.json()["saved_template_id"] is None
    assert after.json()["saved_template_id"]==saved.json()["id"]
    assert before.json()!=after.json()
    assert before.headers["etag"]==after.headers["etag"]
    Path(os.environ["ARCHITECT_RESULT"]).write_text(json.dumps({"before":before.json(),"after":after.json(),"before_etag":before.headers["etag"],"after_etag":after.headers["etag"],"saved_http":saved.status_code},ensure_ascii=False,indent=2))
'''
(dest/'tests/test_task_drafts_integration.py').write_text(fixture)
# Public spec read by exporter is an auxiliary fixture if absent from the candidate tree.
aux=[]
for rel in ['packages/contracts/openapi/openapi.json']:
 q=dest/rel
 if not q.exists():
  p=Path('/workspace/tender-workbench')/rel;q.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,q);aux.append({'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
(out/'auxiliary.json').write_text(json.dumps(aux,indent=2)+'\n')
print(json.dumps({'verified':len(verified),'temporal':cases},ensure_ascii=False))
