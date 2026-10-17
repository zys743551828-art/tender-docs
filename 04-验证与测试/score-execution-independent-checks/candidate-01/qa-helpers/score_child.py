"""Independent dedicated purpose child. Hard MockTransport only, no credential loader."""
import json,sys,hashlib
from pathlib import Path
from uuid import UUID,uuid4
sys.path.insert(0,sys.argv[1]);mode=sys.argv[2]
import httpx,psycopg
from packages.domain.providers.deepseek import DeepSeekClient
from packages.domain.runtime import DomainError
from packages.model_governance.scoring_runtime import execute_job,score_connection
records=[]
def mock(request):
    body=json.loads(request.content);package=json.loads(body['messages'][-1]['content']);raw=json.dumps(package,ensure_ascii=False)
    assert 'PHYSICAL_PAGE_TWO_INCLUDED' in raw and 'Full engineering response' in raw
    assert len(package['sections'])>=1
    forbidden={'official_score','prediction','post_submission_revision','chat','messages','judges','summary','outcome','score','label'}
    def clean(x):
        if isinstance(x,dict):
            assert not forbidden.intersection(x)
            for v in x.values():clean(v)
        elif isinstance(x,list):
            for v in x:clean(v)
    clean(package)
    with score_connection() as c:assert not c.execute("SELECT pid FROM pg_stat_activity WHERE usename=current_user AND pid<>pg_backend_pid() AND state='idle in transaction'").fetchall()
    records.append(dict(rule=package['rule']['id'],package_digest=package['digest'],messages_sha256=hashlib.sha256(json.dumps(body['messages'],sort_keys=True).encode()).hexdigest(),physical_second_page_included=True,no_idle_score_transaction=True))
    if mode=='timeout':raise httpx.ReadTimeout('Independent hard Mock unknown billing')
    if mode=='hold' and len(records)==1:
        print(json.dumps(dict(event='mock_started',calls=1)),flush=True)
        assert sys.stdin.buffer.read(1)==b'1'
    block=package['sections'][0]['blocks'][0]
    answer=dict(status='valid',coverage='complete',band_id=package['rule']['bands'][0]['id'],reason='独立合成完整章节判断，不代表模型效果',evidence=[dict(evidence_id=block['evidence_id'],quote=block['text'])])
    return httpx.Response(200,json=dict(id='independent-'+uuid4().hex,model='deepseek-flash',choices=[dict(message=dict(role='assistant',content=json.dumps(answer,ensure_ascii=False)),finish_reason='stop')],usage=dict(prompt_tokens=31,completion_tokens=17,total_tokens=48)))
try:
    if mode=='permissions':
        denied=[]
        for q in ['SELECT * FROM design.outcomes','SELECT * FROM model_governance.exports','SELECT * FROM historical_backtest.proofs','SELECT * FROM score_execution.calls','SELECT review_root_content FROM score_execution.inputs','SELECT * FROM score_execution.current_chapters']:
            try:
                with score_connection() as c:c.execute(q)
            except psycopg.Error as e:assert e.sqlstate=='42501';denied.append(dict(query=q,state=e.sqlstate))
        assert len(denied)==6
        try:
            with score_connection() as c:
                c.commit();c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ');c.execute('SELECT score_execution.claim_job(%s,1,%s)',(UUID(sys.argv[3]),uuid4()))
        except psycopg.Error as e:assert e.sqlstate=='55000'
        else:raise AssertionError('RR claim unexpectedly accepted')
        output=dict(ok=True,denied=denied,rr_refused=True)
    elif mode=='disabled':
        execute_job(UUID(sys.argv[3]),1)
        raise AssertionError('No explicit transport unexpectedly accepted')
    else:
        client=DeepSeekClient(api_key='independent-explicit-offline-key',transport=httpx.MockTransport(mock))
        result=execute_job(UUID(sys.argv[3]),int(sys.argv[4]),transport=client)
        output=dict(ok=True,result=result)
except DomainError as e:output=dict(ok=False,code=e.code)
except Exception as e:output=dict(ok=False,code=type(e).__name__,sqlstate=getattr(e,'sqlstate',None))
output.update(mock_calls=len(records),requests=records)
loaded=[]
for name,mod in sorted(sys.modules.items()):
    p=getattr(mod,'__file__',None)
    if p and name.startswith(('apps.','packages.')):
        path=Path(p).resolve();assert path.is_relative_to(Path(sys.argv[1])),name;loaded.append(dict(module=name,path=str(path.relative_to(Path(sys.argv[1]))),sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
output['loaded_modules']=loaded
print(json.dumps(output,ensure_ascii=False),flush=True)
