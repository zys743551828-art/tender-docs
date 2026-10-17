"""Independent assistant/A2 acceptance: restricted PG, ASGI and explicit offline ports."""
import copy
import hashlib
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path
from threading import Barrier
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from jsonschema import Draft202012Validator, FormatChecker
from psycopg.types.json import Jsonb

from apps.api.assistant_routes import router
from apps.worker import engine
from packages.assistant import coordinator, global_runtime, jobs, tasks
from packages.authoring.content import SCHEMA_DIGEST
from packages.authoring.patches import apply_proposal
from packages.authoring.repository import create_draft, save_draft
from packages.domain import storage
from packages.domain.providers.deepseek import DeepSeekClient
from packages.domain.runtime import Command, DomainError, assert_role, clean, connection, etag, now, secret_hash, sha
from packages.identity.repository import create_association, record_binding
from packages.rag.indexing import ensure_parse_sources

ROOT = Path(os.environ['QA_ASSISTANT_ROOT'])
OBSERVATIONS = []


def record(name, **observation):
    OBSERVATIONS.append(dict(name=name, **observation))


def expect(response, status):
    assert response.status_code == status, (response.status_code, response.json())
    return response.json()


@pytest.fixture
def s(monkeypatch):
    assert_role(); assert_role(True)
    assert Path(os.environ['WORKBENCH_ENV_FILE']).name == 'score_execution02-runtime.env'
    objects, calls = {}, []
    monkeypatch.setattr(storage, 'put', lambda key, data, media: objects.__setitem__(key, data))
    monkeypatch.setattr(storage, 'get', lambda key: objects[key])
    response = dict(answer='仅合成资料说明，未执行任何领域操作。', citations=[], issue_suggestions=[], tool_proposals=[], gaps=['非模型效果验收'])
    hook = {'fn': None}
    def transport(request):
        calls.append(json.loads(request.content))
        if hook['fn']:
            hook['fn'](request)
        return httpx.Response(200, json=dict(id='offline-' + uuid4().hex, model='deepseek-flash', choices=[dict(message=dict(role='assistant', content=json.dumps(response, ensure_ascii=False)), finish_reason='stop')], usage=dict(prompt_tokens=19, completion_tokens=23, total_tokens=42)))
    client = DeepSeekClient(api_key='explicit-independent-offline-only', transport=httpx.MockTransport(transport))
    monkeypatch.setattr(DeepSeekClient, 'from_env', classmethod(lambda cls: client))
    actors, tokens = [uuid4(), uuid4()], [uuid4().hex, uuid4().hex]
    bids = []
    with connection() as c:
        for actor, token in zip(actors, tokens):
            c.execute("INSERT INTO members(id,display_name) VALUES(%s,'独立助手合成成员')", (actor,))
            c.execute('INSERT INTO sessions(id,member_id,token_digest,csrf_digest,expires_at) VALUES(%s,%s,%s,%s,%s)', (uuid4(), actor, secret_hash(token), hashlib.sha256(b'independent-csrf').hexdigest(), now()+timedelta(hours=2)))
        for title in ['独立助手目标项目', '独立助手另一项目']:
            bid=uuid4();c.execute("INSERT INTO bids(id,title,settings,created_by) VALUES(%s,%s,'{\"auto_review\":false}',%s)",(bid,title,actors[0])); association=create_association(c,dict(title=title,region='浙江',engineering_type='市政'))
            c.execute("INSERT INTO bid_profiles(bid_id,round_id,bidder_id,phase) VALUES(%s,%s,%s,'working')",(bid,association['round_id'],association['bidder_id']));record_binding(c,bid,actors[0],None,reason='仅独立夹具登记待核主体',confirmed=False)
            c.execute('INSERT INTO stream_heads(id,bid_id) VALUES(%s,%s)',(uuid4(),bid));c.execute('INSERT INTO review_slots(bid_id,revision) VALUES(%s,1)',(bid,));bids.append(bid)
    app=FastAPI();app.include_router(router)
    @app.exception_handler(DomainError)
    async def error(request, exc):
        return JSONResponse(dict(code=exc.code,details=clean(exc.details)),status_code=exc.status)
    def headers(actor=0, tag=None, key=None):
        return dict(Cookie='session='+tokens[actor], Origin=os.environ['APP_ORIGIN'], **{'X-CSRF-Token':'independent-csrf','Idempotency-Key':key or uuid4().hex}) | ({'If-Match':tag} if tag else {})
    with TestClient(app) as api:
        yield dict(api=api,h=headers,actors=actors,bids=bids,response=response,calls=calls,hook=hook,objects=objects)


def main_conversation(s, actor=0):
    return s['api'].post('/api/v1/assistant/global-conversations/main',json={},headers=s['h'](actor))


def turn(s, *, actor=0, text=None, refs=None, budget=192):
    current=main_conversation(s,actor);conversation=expect(current,200)['id']
    body=dict(client_message_id=str(uuid4()),text=text or '独立问题'+uuid4().hex,source_selection=refs or [],max_tokens=budget)
    r=s['api'].post(f'/api/v1/assistant/global-conversations/{conversation}/turns',json=body,headers=s['h'](actor,tag=current.headers['etag']))
    return r,body,conversation


def message(accepted):
    with connection() as c:
        return c.execute('SELECT payload FROM assistant_global_outbox WHERE run_id=%s',(UUID(accepted['id']),)).fetchone()['payload']


def job_view(s, identity, actor=0):
    return s['api'].get('/api/v1/assistant/jobs/'+str(identity),headers=s['h'](actor))


def document(s,bid=None):
    bid=bid or s['bids'][0];version,parse,ev=uuid4(),uuid4(),uuid4();raw=(ROOT/'external-inputs/synthetic-body.pdf').read_bytes();digest=sha(raw);text='独立原页依据：只描述本项目施工措施，不授事实资格。'
    with connection() as c:
        c.execute("INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,'application/pdf') ON CONFLICT(digest) DO NOTHING",(uuid4(),digest,'independent-assistant/'+digest,len(raw)));blob=c.execute('SELECT id FROM blobs WHERE digest=%s',(digest,)).fetchone()['id']
        c.execute("INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'独立助手原页.pdf',now(),'source_original',%s)",(version,bid,blob,s['actors'][0]));c.execute("INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'complete','[]')",(parse,bid,version,sha('explicit-independent-parse'),sha({})));c.execute("INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,1,595,842,0,'usable','[]')",(bid,parse));c.execute("INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,0,'text',%s,%s)",(ev,bid,parse,text,sha(text.encode())));root,source=ensure_parse_sources(c,bid,parse,publish_readable=True)
        m=uuid4();n=c.execute('SELECT material_revision FROM bids WHERE id=%s',(bid,)).fetchone()['material_revision']+1;c.execute("INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,%s,'{}',%s)",(m,bid,n,sha(str(m))));c.execute("INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,'tender',0)",(m,bid,version));c.execute('UPDATE bids SET material_revision=%s WHERE id=%s',(n,bid));c.execute('UPDATE bid_profiles SET current_material_id=%s WHERE bid_id=%s',(m,bid))
    return dict(bid=bid,version=version,parse=parse,evidence=ev,root=root,source=source,text=text)


def choose(s,kind,bid,identity=None,**kwargs):
    r=s['api'].get('/api/v1/assistant/sources',params=dict(kind=kind,bid_id=str(bid))|kwargs,headers=s['h']());value=expect(r,200)
    return next(x['reference'] for x in value['items'] if x['available'] and x['bid_id']==str(bid) and (identity is None or x['reference']['id']==str(identity)))


def withdraw(doc):
    with connection() as c:
        c.execute('SELECT pg_advisory_xact_lock(90127,1)');c.execute("UPDATE retrieval.source_heads SET revision=revision+1,status='withdrawn' WHERE source_id=%s",(doc['root'],))


def answer_card(s):
    doc=document(s);refs=[choose(s,'project',doc['bid']),choose(s,'document',doc['bid'],doc['version'],physical_page=1)]
    accepted,_,_=turn(s,refs=refs);v=expect(accepted,202)
    with connection() as c:
        source=next(x for x in jobs.job(c,UUID(v['id']))['input_manifest']['context']['sources'] if x['selection']['kind']=='document')
    s['response']['citations']=[dict(source_key=source['source_key'],quote=doc['text'])]
    s['response']['tool_proposals']=[dict(tool='knowledge.candidate',target_bid_id=str(doc['bid']),arguments=dict(title='独立明确确认待核资料'+uuid4().hex,content='仅人工待核候选，不代表已发布或项目事实',scope='待人工核验',source_version_ids=[str(doc['version'])]))]
    assert global_runtime.handle(message(v));published=coordinator.publish(UUID(v['id']));assert published['publication'] is not None and published['publication']['state']=='published',published
    with connection() as c:aid=c.execute('SELECT action_id FROM assistant_action_links WHERE job_id=%s',(UUID(v['id']),)).fetchone()['action_id']
    card=s['api'].get(f'/api/v1/assistant/actions/{aid}',headers=s['h']());expect(card,200)
    return doc,aid,card,v


def test_global_member_private_null_scope_and_no_text_execution(s):
    with connection() as c:before=c.execute('SELECT count(*) AS n FROM bids').fetchone()['n']
    a,_,ca=turn(s,text='请保存私密QA标记'+uuid4().hex);av=expect(a,202);b,_,cb=turn(s,actor=1,text='同成员隔离对照');bv=expect(b,202)
    assert ca!=cb
    with connection() as c:
        assert c.execute('SELECT count(*) AS n FROM bids').fetchone()['n']==before
        for identity in [av['id'],bv['id']]:
            row=jobs.job(c,UUID(identity));assert row['bid_id'] is None and row['operation_id'] is None
    for path in [f'/assistant/global-conversations/{ca}/messages',f'/assistant/global-conversations/{ca}/events',f'/assistant/jobs/{av["id"]}',f'/assistant/jobs?scope=global&conversation_id={ca}']:
        expect(s['api'].get('/api/v1'+path,headers=s['h'](1)),404)
    for path,body in [(f'/assistant/jobs/{av["id"]}/cancel',{}),(f'/assistant/global-conversations/{ca}/turns',dict(client_message_id=str(uuid4()),text='伪造另一成员消息',source_selection=[])),(f'/assistant/global-conversations/{ca}/state',dict(state='closed'))]:
        expect(s['api'].post('/api/v1'+path,json=body,headers=s['h'](1,tag=a.headers.get('etag'))),404)
    assert global_runtime.handle(message(av));coordinator.publish(UUID(av['id']))
    page=expect(s['api'].get(f'/api/v1/assistant/global-conversations/{ca}/messages',headers=s['h']()),200)
    assert [m['role'] for m in page['items']]==['user','assistant'] and all(m['bid_id'] is None for m in page['items'])
    yes,_,_=turn(s,text='好的，请执行、同意所有建议');yesv=expect(yes,202)
    with connection() as c:
        assert c.execute('SELECT count(*) AS n FROM assistant_action_links WHERE job_id=%s',(UUID(yesv['id']),)).fetchone()['n']==0
    record('global_member_isolation',forbidden_reads=4,forbidden_writes=3,null_scope=True,no_surrogate_bid=True,free_text_not_action=True)


@pytest.mark.parametrize('race',['confirm_confirm','confirm_reject'])
def test_cross_member_actions_one_real_effect(s,race):
    doc,aid,card,v=answer_card(s)
    with connection() as c:before=c.execute('SELECT count(*) AS n FROM retrieval.knowledge_items').fetchone()['n']
    barrier=Barrier(2)
    def resolve(actor):
        barrier.wait(timeout=10)
        return s['api'].post(f'/api/v1/assistant/actions/{aid}/resolve',json=dict(decision='confirm' if actor==0 or race=='confirm_confirm' else 'reject'),headers=s['h'](actor,tag=card.headers['etag']))
    with ThreadPoolExecutor(max_workers=2) as pool:rs=list(pool.map(resolve,[0,1]))
    bodies=[expect(r,200) for r in rs];assert bodies[0]==bodies[1];state=bodies[0]['state'];assert state in ['executed','rejected']
    with connection() as c:
        assert c.execute('SELECT count(*) AS n FROM action_consumptions WHERE action_id=%s',(aid,)).fetchone()['n']==1
        assert c.execute('SELECT count(*) AS n FROM retrieval.knowledge_items').fetchone()['n']==before+(state=='executed')
        assert c.execute('SELECT count(*) AS n FROM assistant_publications WHERE job_id=%s',(UUID(v['id']),)).fetchone()['n']==1
    opposite=s['api'].post(f'/api/v1/assistant/actions/{aid}/resolve',json=dict(decision='reject' if state=='executed' else 'confirm'),headers=s['h'](1,tag=card.headers['etag']));assert expect(opposite,200)==bodies[0]
    record('cross_member_'+race,winning_state=state,global_receipts=1,actual_domain_creations=int(state=='executed'))


def test_source_selection_staleness_before_call_after_call_and_action(s):
    d=document(s);ref=choose(s,'document',d['bid'],d['version'],physical_page=1)
    # Selection token is server-owned; a different version/page cannot borrow its authority.
    forged=copy.deepcopy(ref);forged['physical_page']=2
    rejected,_,_=turn(s,refs=[forged]);assert rejected.status_code in [409,412]
    accepted,_,_=turn(s,refs=[ref]);v=expect(accepted,202);withdraw(d)
    calls=len(s['calls']);assert global_runtime.handle(message(v));result=expect(job_view(s,v['id']),200)
    assert len(s['calls'])==calls and result['operation']['state']=='failed' and result['candidate'] is None
    late,_,_=turn(s,refs=[ref]);assert late.status_code in [409,412]
    # A completed call is a paid-like result, but its now-stale source cannot publish an answer.
    d2=document(s);ref2=choose(s,'document',d2['bid'],d2['version'],physical_page=1);a2,_,_=turn(s,refs=[ref2]);v2=expect(a2,202)
    s['hook']['fn']=lambda request:withdraw(d2)
    assert global_runtime.handle(message(v2));s['hook']['fn']=None;result2=coordinator.publish(UUID(v2['id']))
    assert result2['model_call']['state']=='succeeded' and result2['publication']['state']=='stale' and result2['publication']['global_message_id'] is None
    # An already published Action must revalidate at its later confirmation, too.
    doc,aid,card,_=answer_card(s);withdraw(doc)
    with connection() as c:before=c.execute('SELECT count(*) AS n FROM retrieval.knowledge_items').fetchone()['n']
    r=s['api'].post(f'/api/v1/assistant/actions/{aid}/resolve',json=dict(decision='confirm'),headers=s['h'](tag=card.headers['etag']));stale=expect(r,409);assert stale['state']=='stale'
    with connection() as c:assert c.execute('SELECT count(*) AS n FROM retrieval.knowledge_items').fetchone()['n']==before
    record('selection_and_source_staleness',bad_physical_page_rejected=True,before_dispatch_calls=0,after_call_billing_retained=True,stale_publication=True,action_side_effects=0)


def test_unknown_billing_private_history_and_cancel(s):
    def timeout(request):raise httpx.ReadTimeout('independent-explicit-offline-timeout')
    s['hook']['fn']=timeout
    a,body,conv=turn(s,text='独立未知费用'+uuid4().hex);v=expect(a,202);assert global_runtime.handle(message(v));result=coordinator.publish(UUID(v['id']))
    assert len(s['calls'])==1 and result['model_call']['state']=='unknown' and result['requires_billing_reconciliation'] and not result['automatic_retry_authorized'] and result['candidate'] is None
    again,_,_=turn(s,text=body['text'],budget=256);assert again.status_code==409 and again.json()['code']=='MODEL_CALL_REQUIRES_RECONCILIATION' and len(s['calls'])==1
    history=expect(s['api'].get(f'/api/v1/assistant/global-conversations/{conv}/messages',headers=s['h']()),200);assert [m['role'] for m in history['items']]==['user']
    s['hook']['fn']=None
    # Identical text from the other member is a separate private logical intent.
    other,_,_=turn(s,actor=1,text=body['text']);ov=expect(other,202);assert global_runtime.handle(message(ov));assert coordinator.publish(UUID(ov['id']))['publication']['state']=='published'
    # Cancel before acquire must remain a request until the real runtime acknowledges it.
    pending,_,_=turn(s);pv=expect(pending,202);cancel=s['api'].post(f'/api/v1/assistant/jobs/{pv["id"]}/cancel',json={},headers=s['h']());requested=expect(cancel,202);assert requested['cancel_requested'] and not requested['cancellation_terminal']
    n=len(s['calls']);assert global_runtime.acquire(message(pv)) is None;terminal=expect(job_view(s,pv['id']),200);assert terminal['cancellation_terminal'] and terminal['operation']['state']=='cancelled' and len(s['calls'])==n
    record('unknown_billing_and_cancel',timeout_invocations=1,changed_budget_does_not_repay=True,other_member_independent=True,no_fabricated_assistant_history=True,cancellation_has_real_terminal=True)


def test_late_fence_and_cancel_after_response_preserve_billing(s):
    accepted,_,_=turn(s);v=expect(accepted,202);m=message(v);lease1=global_runtime.acquire(m);outcome1=global_runtime.compute(m,lease1)
    with connection(True) as c:c.execute("UPDATE assistant_global_runs SET lease_until=clock_timestamp()-interval '1 second' WHERE id=%s",(UUID(v['id']),))
    lease2=global_runtime.acquire(m);assert lease2['fence']>lease1['fence'] and global_runtime.renew(m,lease1) is False and global_runtime.finish(m,lease1,outcome1) is False
    outcome2=global_runtime.compute(m,lease2);assert outcome1['call_id']==outcome2['call_id'] and len(s['calls'])==1 and global_runtime.finish(m,lease2,outcome2)
    assert coordinator.publish(UUID(v['id']))['publication']['state']=='published';assert coordinator.publish(UUID(v['id']))['publication']['state']=='published'
    cancelled,_,_=turn(s);cv=expect(cancelled,202)
    def cancel_on_response(request):
        expect(s['api'].post(f'/api/v1/assistant/jobs/{cv["id"]}/cancel',json={},headers=s['h']()),202)
    s['hook']['fn']=cancel_on_response;assert global_runtime.handle(message(cv));s['hook']['fn']=None;final=coordinator.publish(UUID(cv['id']))
    assert final['operation']['state']=='cancelled' and final['model_call']['state']=='succeeded' and final['publication'] is None and final['candidate'] is None
    record('late_fence_and_cancelled_response',old_owner_cannot_renew_publish=True,known_result_reused_without_second_invocation=True,cancelled_reply_not_published=True,known_usage_preserved=True)


def generation(s,doc=None):
    bid=s['bids'][0]
    with connection() as c:
        draft,_=create_draft(c,bid,s['actors'][0],'独立选区编制')
        sec=c.execute('SELECT * FROM section_revisions WHERE draft_id=%s',(draft,)).fetchone();content=copy.deepcopy(sec['content']);node=content['content'][0]
        node['content']=[dict(type='text',text='保留前缀',marks=[dict(type='bold')]),dict(type='text',text='旧选区'),dict(type='text',text='保留后缀',marks=[dict(type='italic')])]
        save_draft(c,bid,draft,s['actors'][0],dict(base_draft_revision=1,changes=[dict(section_id=str(sec['section_id']),base_section_revision=1,schema_digest=SCHEMA_DIGEST,content=content)]),etag('draft',draft,1))
    s['response'].clear();s['response'].update(replacement_text='仅新选区',evidence_ids=[str(doc['evidence'])] if doc else [])
    body=dict(section_id=str(sec['section_id']),base_section_revision=2,instruction='只修改明确选区，不应用正文',source_version_ids=[str(doc['version'])] if doc else [],node_id=node['attrs']['id'],selection_start=4,selection_end=7)
    r=s['api'].post(f'/api/v1/bids/{bid}/authoring/generations',json=body,headers=s['h'](tag=etag('draft',draft,2)));v=expect(r,202)
    with connection() as c:m=c.execute('SELECT payload FROM outbox WHERE operation_id=%s',(UUID(v['operation']['id']),)).fetchone()['payload']
    return bid,draft,sec,content,v,m


def project_execute(s,m):
    # Explicit adapter only: the candidate fixes old core engine, not backend07 registration.
    from unittest.mock import patch
    with patch.object(engine,'ORDINARY_HANDLERS',engine.ORDINARY_HANDLERS+('assistant/1','authoring-generate/1')),patch.object(engine,'publish',tasks.publish):
        lease=engine.acquire(m);assert lease;outcome=tasks.compute(m,lease);assert engine.finish(m,lease,outcome);assert engine.finish(m,lease,outcome) is False


def test_authoring_selection_requires_original_action_and_native_reject_guard(s):
    bid,draft,sec,content,v,m=generation(s);project_execute(s,m);published=coordinator.publish(UUID(v['id']));assert published['publication']['state']=='published';aid=UUID(published['publication']['action_id'])
    with connection() as c:
        before=c.execute('SELECT revision FROM drafts WHERE id=%s',(draft,)).fetchone()['revision'];assert before==2
        profile=c.execute('SELECT profile_digest FROM model_calls WHERE operation_id=%s',(UUID(v['operation']['id']),)).fetchone()['profile_digest'];assert profile==sha(dict(provider='deepseek',model='deepseek-flash',port='generation/1',purpose='authoring',formal_scoring_eligible=False))
    card=s['api'].get(f'/api/v1/assistant/actions/{aid}',headers=s['h']());value=expect(card,200)
    after=value['preview']['after']['content'][0]['content'];assert after==[content['content'][0]['content'][0],dict(type='text',text='仅新选区'),content['content'][0]['content'][2]]
    rejected=s['api'].post(f'/api/v1/assistant/actions/{aid}/resolve',json=dict(decision='reject'),headers=s['h'](1,tag=card.headers['etag']));assert expect(rejected,200)['state']=='rejected'
    with pytest.raises(DomainError) as e,connection() as c:
        cmd=Command(c,s['actors'][0],'independent-native-reject',aid,uuid4().hex,{})
        apply_proposal(c,bid,aid,s['actors'][0],cmd.id,etag('draft',draft,2))
    assert e.value.code=='ACTION_CONSUMED'
    with connection() as c:assert c.execute('SELECT revision FROM drafts WHERE id=%s',(draft,)).fetchone()['revision']==before
    record('authoring_selection_and_reject',outside_marks_preserved=True,candidate_not_applied=True,native_cannot_bypass_rejection=True,purpose='authoring')


def test_authoring_identity_change_blocks_native_and_card(s):
    bid,draft,sec,content,v,m=generation(s);project_execute(s,m);published=coordinator.publish(UUID(v['id']));aid=UUID(published['publication']['action_id'])
    with connection() as c:
        record_binding(c,bid,s['actors'][0],None,reason='独立反例同UUID重核产生新主体权威',confirmed=False)
    with pytest.raises(DomainError) as e,connection() as c:
        cmd=Command(c,s['actors'][1],'independent-native-identity',aid,uuid4().hex,{})
        apply_proposal(c,bid,aid,s['actors'][1],cmd.id,etag('draft',draft,2))
    assert e.value.code=='AUTHORING_CONTEXT_CHANGED'
    card=s['api'].get(f'/api/v1/assistant/actions/{aid}',headers=s['h']());expect(card,200)
    stale=s['api'].post(f'/api/v1/assistant/actions/{aid}/resolve',json=dict(decision='confirm'),headers=s['h'](1,tag=card.headers['etag']));assert expect(stale,409)['state']=='stale'
    with connection() as c:assert c.execute('SELECT revision FROM drafts WHERE id=%s',(draft,)).fetchone()['revision']==2
    record('native_and_action_identity_change',same_ids_new_identity_authority_rejected=True,section_revision_unchanged=True)


def test_authoring_actual_confirm_once_then_native_cross_entry_block(s):
    bid,draft,sec,content,v,m=generation(s);project_execute(s,m);published=coordinator.publish(UUID(v['id']));aid=UUID(published['publication']['action_id']);card=s['api'].get(f'/api/v1/assistant/actions/{aid}',headers=s['h']());expect(card,200)
    result=s['api'].post(f'/api/v1/assistant/actions/{aid}/resolve',json=dict(decision='confirm'),headers=s['h'](1,tag=card.headers['etag']));rv=expect(result,200);assert rv['state']=='executed'
    with connection() as c:
        assert c.execute('SELECT revision FROM drafts WHERE id=%s',(draft,)).fetchone()['revision']==3
        row=c.execute('SELECT content FROM section_revisions WHERE section_id=%s AND revision=3',(sec['section_id'],)).fetchone()['content'];assert row['content'][0]['content'][0]==content['content'][0]['content'][0] and row['content'][0]['content'][-1]==content['content'][0]['content'][-1]
        assert c.execute('SELECT count(*) AS n FROM action_consumptions WHERE action_id=%s',(aid,)).fetchone()['n']==1
    with pytest.raises(DomainError) as e,connection() as c:
        cmd=Command(c,s['actors'][0],'independent-native-after-confirm',aid,uuid4().hex,{})
        apply_proposal(c,bid,aid,s['actors'][0],cmd.id,etag('draft',draft,3))
    assert e.value.code=='ACTION_CONSUMED'
    record('authoring_confirm_once',draft_revision=3,global_receipts=1,native_second_entry_rejected=True)


def test_native_apply_first_preserves_exact_source_then_assistant_replay(s):
    doc=document(s);bid,draft,sec,content,v,m=generation(s,doc);project_execute(s,m);published=coordinator.publish(UUID(v['id']));aid=UUID(published['publication']['action_id'])
    with connection() as c:
        cmd=Command(c,s['actors'][1],'independent-native-first',aid,uuid4().hex,{})
        applied=apply_proposal(c,bid,aid,s['actors'][1],cmd.id,etag('draft',draft,2));cmd.finish(applied)
    with connection() as c:
        row=c.execute('SELECT content FROM section_revisions WHERE section_id=%s AND revision=3',(sec['section_id'],)).fetchone()['content'];refs=row['attrs']['source_refs']
        assert len(refs)==1 and refs[0]['evidence_id']==str(doc['evidence']) and refs[0]['source_id']==str(doc['source']) and refs[0]['document_version_id']==str(doc['version'])
        assert c.execute("SELECT count(*) AS n FROM retrieval.citations WHERE evidence_id=%s AND source_id=%s AND purpose='authoring'",(doc['evidence'],doc['source'])).fetchone()['n']>0
    card=s['api'].get(f'/api/v1/assistant/actions/{aid}',headers=s['h']());assert expect(card,200)['state']=='executed'
    replay=s['api'].post(f'/api/v1/assistant/actions/{aid}/resolve',json=dict(decision='reject'),headers=s['h'](tag=card.headers['etag']));assert expect(replay,200)['state']=='executed'
    with connection() as c:
        assert c.execute('SELECT revision FROM drafts WHERE id=%s',(draft,)).fetchone()['revision']==3
        assert c.execute('SELECT count(*) AS n FROM action_consumptions WHERE action_id=%s',(aid,)).fetchone()['n']==1
    record('native_apply_first_and_source_lineage',exact_current_parse_evidence_preserved=True,citation_written=True,draft_revision=3,assistant_opposite_decision_replays_original_effect=True)


def test_native_apply_rejects_source_withdrawal_without_section_change(s):
    doc=document(s);bid,draft,sec,content,v,m=generation(s,doc);project_execute(s,m);published=coordinator.publish(UUID(v['id']));aid=UUID(published['publication']['action_id']);withdraw(doc)
    with pytest.raises(DomainError) as e,connection() as c:
        cmd=Command(c,s['actors'][0],'independent-native-withdraw',aid,uuid4().hex,{})
        apply_proposal(c,bid,aid,s['actors'][0],cmd.id,etag('draft',draft,2))
    assert e.value.status in [409,412]
    with connection() as c:
        assert c.execute('SELECT revision FROM drafts WHERE id=%s',(draft,)).fetchone()['revision']==2
        assert c.execute('SELECT count(*) AS n FROM action_consumptions WHERE action_id=%s',(aid,)).fetchone()['n']==0
        assert c.execute('SELECT count(*) AS n FROM section_revisions WHERE section_id=%s AND revision>2',(sec['section_id'],)).fetchone()['n']==0
    record('native_source_withdrawal_guard',exact_source_authority_error=e.value.code,no_new_section_revision=True,no_false_consumption=True)


def test_authoring_unknown_call_blocks_new_command_and_new_client_budget(s):
    from unittest.mock import patch
    bid,draft,sec,content,v,m=generation(s)
    def timeout(request):raise httpx.ReadTimeout('independent-authoring-offline-timeout')
    s['hook']['fn']=timeout
    with patch.object(engine,'ORDINARY_HANDLERS',engine.ORDINARY_HANDLERS+('assistant/1','authoring-generate/1')):
        lease=engine.acquire(m);assert lease
        with pytest.raises(DomainError) as error:tasks.compute(m,lease)
        assert engine.finish(m,lease,None,error.value)
    view=expect(job_view(s,v['id']),200)
    assert view['operation']['state']=='failed' and view['model_call']['state']=='unknown' and view['requires_billing_reconciliation'] and view['candidate'] is None and len(s['calls'])==1
    with connection() as c:body=jobs.job(c,UUID(v['id']))['input_manifest']['request']
    again=s['api'].post(f'/api/v1/bids/{bid}/authoring/generations',json=body,headers=s['h'](1,tag=etag('draft',draft,2)))
    assert again.status_code==409 and again.json()['code']=='MODEL_CALL_REQUIRES_RECONCILIATION' and len(s['calls'])==1
    with connection() as c:assert c.execute('SELECT revision FROM drafts WHERE id=%s',(draft,)).fetchone()['revision']==2
    record('authoring_unknown_billing',mock_invocations=1,new_member_command_cannot_repay=True,no_candidate=True,draft_unchanged=True)


@pytest.mark.parametrize('cancel',[False,True])
def test_project_answer_actual_ledger_and_cancelled_reply_history(s,cancel):
    from unittest.mock import patch
    bid=s['bids'][0];main=s['api'].post(f'/api/v1/bids/{bid}/conversations/main',json={},headers=s['h']());conv=expect(main,200)['id']
    # A selected foreign project's file cannot be silently injected into this project's turn.
    foreign=document(s,s['bids'][1]);foreign_ref=choose(s,'document',foreign['bid'],foreign['version'])
    denied=s['api'].post(f'/api/v1/bids/{bid}/conversations/{conv}/turns',json=dict(client_message_id=str(uuid4()),text='越项目来源应拒绝',source_selection=[foreign_ref]),headers=s['h'](tag=main.headers['etag']))
    assert denied.status_code in [409,422]
    accepted=s['api'].post(f'/api/v1/bids/{bid}/conversations/{conv}/turns',json=dict(client_message_id=str(uuid4()),text='解释当前项目，取消结果需诚实显示'+uuid4().hex,source_selection=[]),headers=s['h'](tag=main.headers['etag']));v=expect(accepted,202)
    with connection() as c:m=c.execute('SELECT payload FROM outbox WHERE operation_id=%s',(UUID(v['operation']['id']),)).fetchone()['payload']
    if cancel:
        def request_cancel(request):
            rr=s['api'].post(f'/api/v1/assistant/jobs/{v["id"]}/cancel',json={},headers=s['h'](1));value=expect(rr,202);assert value['cancel_requested'] and not value['cancellation_terminal']
        s['hook']['fn']=request_cancel
    with patch.object(engine,'ORDINARY_HANDLERS',engine.ORDINARY_HANDLERS+('assistant/1','authoring-generate/1')),patch.object(engine,'publish',tasks.publish):
        lease=engine.acquire(m);assert lease;outcome=tasks.compute(m,lease);finished=engine.finish(m,lease,outcome)
    result=coordinator.publish(UUID(v['id']));assert result['model_call']['state']=='succeeded' and len(s['calls'])==1
    history=expect(s['api'].get(f'/api/v1/bids/{bid}/conversations/{conv}/messages',headers=s['h']()),200)
    if cancel:
        assert finished is False and result['operation']['state']=='cancel_requested' and not result['cancellation_terminal'] and result['publication'] is None and [x['role'] for x in history['items']]==['user']
    else:
        assert finished and result['publication']['state']=='published' and [x['role'] for x in history['items']]==['user','assistant']
        reopened=s['api'].post(f'/api/v1/bids/{bid}/conversations/main',json={},headers=s['h'](1));assert expect(reopened,200)['id']==conv
    with connection() as c:
        profile=c.execute('SELECT profile_digest FROM model_calls WHERE operation_id=%s',(UUID(v['operation']['id']),)).fetchone()['profile_digest'];assert profile==sha(dict(provider='deepseek',model='deepseek-flash',port='generation/1',purpose='assistant',formal_scoring_eligible=False))
    record('project_answer_cancelled' if cancel else 'project_answer_ledger',foreign_source_rejected=True,assistant_purpose=True,known_usage_retained=True,history_roles=[x['role'] for x in history['items']],explicit_old_engine_adapter=True)


@pytest.fixture(scope='session',autouse=True)
def evidence_session():
    manifest=json.loads((ROOT/'runtime-manifest.json').read_text())
    for row in manifest:assert hashlib.sha256((ROOT/'execution-inputs'/row['path']).read_bytes()).hexdigest()==row['sha256']
    yield
    for row in manifest:assert hashlib.sha256((ROOT/'execution-inputs'/row['path']).read_bytes()).hexdigest()==row['sha256']
    loaded=[]
    for name,module in sorted(sys.modules.items()):
        p=getattr(module,'__file__',None)
        if p and name.startswith(('apps.','packages.')):
            p=Path(p).resolve();assert p.is_relative_to(ROOT/'execution-inputs'),name
            loaded.append(dict(module=name,path=str(p.relative_to(ROOT/'execution-inputs')),sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    out=ROOT/os.environ.get('QA_ASSISTANT_EXECUTION','execution');out.mkdir(exist_ok=True)
    (out/'observations.json').write_text(json.dumps(dict(scope='Independent restrictedPG + actual assistant routers ASGI; synthetic page/response fixture and memory object port; explicit adapter for old engine project hooks. No real model/OSS/Kafka/shared API or frontend acceptance.',observations=OBSERVATIONS,loaded_modules=loaded,paid_calls=0,oss_requests=0,consumers_started=0),ensure_ascii=False,indent=2)+'\n')
    (out/'executed-test.py').write_bytes(Path(__file__).read_bytes())
