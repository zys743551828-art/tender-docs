"""Independent main04 gate/fact/report adversarial assertions on own synthetic project."""
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from io import BytesIO
from pathlib import Path
from uuid import uuid4
import hashlib
import json
import sys
import zipfile
from unittest.mock import patch
import pypdfium2 as pdfium
import pypdfium2.raw as pdfraw
from psycopg.types.json import Jsonb
from apps.worker import engine
from packages.domain import storage
from packages.domain.runtime import connection, now, sha
from packages.domain.submission_gate import evaluate


def run_cases(*,client,bid,headers,check,calls,objects,out,body,review_id,member,manifest):
    (out/'executed-submission-cases.py').write_bytes(Path(__file__).read_bytes())
    before=len(calls)
    def get(path,**kw):
        r=client.get('/api/v1'+path,**kw);assert r.status_code==200,(path,r.status_code,r.json().get('code'));return r
    def bidtag():return get(f'/bids/{bid}').headers['etag']
    def gate():return get(f'/bids/{bid}/submission-gate')
    def checkresp(name,r,status,code=None):check(name,r.status_code==status and (code is None or r.json().get('code')==code),dict(status=r.status_code,code=r.json().get('code')))
    def post(path,payload,tag=None,key=None):return client.post('/api/v1'+path,json=payload,headers=headers(tag,key))
    def docrow(version):
        with connection() as c:return c.execute('SELECT b.* FROM blobs b JOIN document_versions v ON v.blob_id=b.id WHERE v.id=%s',(version,)).fetchone()
    initial=gate();check('complete_core_still_requires_explicit_human_rule_confirmation',not initial.json()['allowed'] and {x['code'] for x in initial.json()['blockers']}=={'CORE_RULE_CONFIRMATION_REQUIRED'})
    rows=get(f'/bids/{bid}/rules').json()['rules']
    for rule in rows:
        current=get(f"/rules/{rule['id']}")
        answer=post(f"/rules/{rule['id']}/decisions",dict(decision='confirmed',reason='独立测试逐字核对合成条款',evidence_ids=[]),current.headers['etag']);assert answer.status_code==200
    baseline=gate();check('pure_scoring_not_run_does_not_block_complete_core_and_confirmed_rules',baseline.json()['allowed'] and not baseline.json()['blockers'] and get(f'/reviews/{review_id}').json()['coverage_layers']['scoring']['state']=='not_run')
    # The real original bytes are memory-backed explicitly, not a cloud storage claim.
    class MemoryS3:
        def get_object(self,*,Bucket,Key):return {'Body':BytesIO(objects[Key])}
    def clock():
        with connection() as c:return c.execute('SELECT clock_timestamp() AS at').fetchone()['at']
    with patch.object(storage,'client',return_value=MemoryS3()):
        with connection() as c:revision=c.execute('SELECT material_revision FROM bids WHERE id=%s',(bid,)).fetchone()['material_revision']
        payload=dict(body_version_id=str(body),material_revision=revision,review_id=review_id,submitted_at=clock().isoformat(),evidence_ref=str(body))
        path=f'/bids/{bid}/submissions'
        checkresp('standard_missing_cas_rejected',post(path,payload),428,'PRECONDITION_REQUIRED')
        checkresp('standard_stale_cas_rejected',post(path,payload,'"stale"'),412)
        checkresp('future_actual_submission_is_not_recordable',post(path,payload|dict(submitted_at=(clock()+timedelta(days=1)).isoformat()),bidtag()),422,'SUBMISSION_FUTURE_TIME')
        checkresp('post_fact_completed_checks_cannot_be_rewritten_as_pre_fact',post(path,payload|dict(submitted_at='2000-01-01T00:00:00Z'),bidtag()),409,'SUBMISSION_CHECKS_POSTDATE_FACT')
        checkresp('wrong_selected_review_cannot_borrow_current_gate',post(path,payload|dict(review_id=str(uuid4())),bidtag()),412,'SUBMISSION_INPUT_CHANGED')
        row=docrow(body);original=objects[row['object_key']];objects[row['object_key']]=original+b'CORRUPTION'
        checkresp('actual_original_sha_is_verified_before_standard_fact',post(path,payload,bidtag()),409,'SUBMISSION_ORIGINAL_DIGEST_MISMATCH');objects[row['object_key']]=original
        # Reports are requested via real API and direct restricted Worker execution.
        report_path=f'/reviews/{review_id}/report-exports'
        def request_report(kind,fmt):
            context=get(f'/reviews/{review_id}/report-context');value=context.json();data=dict(artifact_kind=kind,format=fmt,presentation_revision=value['presentation_revision'],source_impact_revision=value['source_impact_revision']);response=post(report_path,data,context.headers['etag']);assert response.status_code==202,(response.status_code,response.json().get('code'));return response,context,data
        def message(response):
            with connection(True) as c:return c.execute('SELECT payload FROM outbox WHERE operation_id=%s ORDER BY available_at,id LIMIT 1',(response.json()['operation']['id'],)).fetchone()['payload']
        delayed,old_context,old_request=request_report('review_report','docx');msg=message(delayed);lease=engine.acquire(msg);assert lease;output=engine.run_compute(msg,lease)
        settings=get(f'/bids/{bid}/settings');toggle=client.patch('/api/v1'+f'/bids/{bid}/settings',json={'show_prediction':False},headers=headers(settings.headers['etag']));assert toggle.status_code==200
        try:engine.finish(msg,lease,output);late=False
        except Exception as error:
            late=getattr(error,'code',None)=='REPORT_PRESENTATION_CHANGED'
            assert late,type(error).__name__
            assert engine.finish(msg,lease,None,error)
        failed=get(f"/report-exports/{delayed.json()['resource_id']}").json()
        check('computed_report_cannot_publish_after_visibility_policy_change',late and failed['state']=='failed' and failed['document_version_id'] is None and failed['error_code']=='REPORT_PRESENTATION_CHANGED')
        checkresp('old_report_context_cas_still_rejected',post(report_path,old_request,old_context.headers['etag']),412)
        versions=[]
        for kind,fmt in [('review_report','docx'),('review_report','pdf'),('annotated_review_copy','pdf')]:
            response,context,data=request_report(kind,fmt);engine.handle(message(response));rv=get(f"/report-exports/{response.json()['resource_id']}").json();assert rv['state']=='succeeded',rv
            version=rv['document_version_id'];versions.append(version);object_row=docrow(version);raw=objects[object_row['object_key']];(out/f'{kind}.{fmt}').write_bytes(raw)
            meta=get(f'/documents/{version}').json()
            check(f'real_{kind}_{fmt}_bytes_have_distinct_purpose_and_do_not_replace_body',sha(raw)==object_row['digest'] and len(raw)==object_row['byte_size'] and meta['artifact_kind']==kind and get(f'/bids/{bid}').json()['current_body_id']==str(body))
            if fmt=='docx':
                with zipfile.ZipFile(BytesIO(raw)) as z:xml=z.read('word/document.xml').decode()
                check('docx_contains_frozen_review_all_four_layers_and_truthful_hidden_prediction',all(x in xml for x in ['不是提交正文',review_id,'source_scope','core_extraction','judgment','scoring','已隐藏']) and '正式预测分尚不可用' not in xml)
            else:
                with closing(pdfium.PdfDocument(raw)) as pdf:
                    if kind=='annotated_review_copy':
                        with closing(pdf[0]) as pg:
                            annotations=pdfraw.FPDFPage_GetAnnotCount(pg)
                            with closing(pg.get_textpage()) as text:page1=text.get_text_range()
                        check('annotated_pdf_keeps_all_physical_pages_and_original_text_with_page_notes',len(pdf)==3 and annotations>=1 and 'Construction period will be 90 days.' in page1 and sha(raw)!=sha(original))
                    else:
                        rendered_text=''
                        for page in pdf:
                            with closing(page),closing(page.get_textpage()) as text:rendered_text+=text.get_text_range()
                        check('libreoffice_pdf_is_readable_and_has_review_purpose',len(pdf)>0 and '评审报告' in rendered_text and review_id in rendered_text)
            replay=post(report_path,data,context.headers['etag'],None)
            check(f'{kind}_{fmt}_same_context_reuses_successful_artifact',replay.status_code==202 and replay.json()['resource_id']==response.json()['resource_id'])
        annotated=versions[-1]
        unsupported_context=get(f'/reviews/{review_id}/report-context');u=unsupported_context.json()
        checkresp('docx_annotation_is_honestly_unsupported',post(report_path,dict(artifact_kind='annotated_review_copy',format='docx',presentation_revision=u['presentation_revision'],source_impact_revision=u['source_impact_revision']),unsupported_context.headers['etag']),422,'REPORT_FORMAT_UNSUPPORTED')
        checkresp('report_or_annotated_copy_cannot_be_standard_actual_body',post(path,payload|dict(body_version_id=annotated),bidtag()),409,'SUBMISSION_BODY_KIND_INVALID')
        # Simultaneous first facts share one current head; response loss is exact replay.
        payload['submitted_at']=clock().isoformat();tag=bidtag();key=uuid4().hex
        with ThreadPoolExecutor(max_workers=2) as pool:
            answers=list(pool.map(lambda k:post(path,payload,tag,k),[key,uuid4().hex]))
        check('concurrent_first_standard_fact_has_exactly_one_head_winner',sorted(r.status_code for r in answers)==[201,412])
        winner=next(r for r in answers if r.status_code==201);record=winner.json();assert record['gate_checked'] and record['evidence_ref']==str(body)
        winner_key=key if answers[0].status_code==201 else None
        if winner_key:
            replay=post(path,payload,tag,winner_key);check('lost_standard_response_replays_original_body_and_never_duplicates',replay.status_code==201 and replay.json()==record and replay.headers.get('Idempotent-Replayed')=='true')
        check('self_original_is_sufficient_declared_evidence_without_fake_platform_receipt',record['platform_submission_verified'] is False and record['checks_label']=='核心提交检查已通过' and record['identity']['binding_status']=='confirmed')
        # Explicitly correct the actual fact to a previously uploaded annotated file.
        corrected_payload=dict(kind='offline_fact',submission=dict(actual_document_version_id=annotated,submitted_at=clock().isoformat(),evidence_ref=annotated,explanation='独立合成事实：确实错传批注稿，不能伪装完整检查'),reason='独立更正，保存原标准事实与原件')
        correction_path=f"/submissions/{record['id']}/corrections";tag=bidtag();ck=uuid4().hex
        correction=post(correction_path,corrected_payload,tag,ck);assert correction.status_code==201,correction.json().get('code');new=correction.json()
        replay=post(correction_path,corrected_payload,tag,ck)
        history=get(f'/bids/{bid}/submissions',params={'limit':1}).json();second=get(f'/bids/{bid}/submissions',params={'limit':1,'cursor':history['next_cursor']}).json()
        check('offline_wrong_upload_correction_keeps_immutable_old_fact_and_truthful_unknown',not new['gate_checked'] and new['artifact_kind']=='annotated_review_copy' and new['supersedes_id']==record['id'] and new['open_risk_snapshot']['open'] is None and new['platform_submission_verified'] is False and replay.json()==new and history['items'][0]['id']==new['id'] and second['items'][0]['id']==record['id'] and not get(f"/submissions/{record['id']}").json()['current'])
        checkresp('old_fact_correction_cannot_replace_new_head',post(correction_path,corrected_payload,bidtag()),412,'SUBMISSION_HEAD_CONFLICT')
        # Pending identity may record truthful past facts, with its own project original.
        pending=post('/bids',dict(title='独立待确认主体事实补录')).json()['id'];pending_version=uuid4()
        with connection() as c:c.execute("INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'待确认主体原稿.pdf',now(),'bid_body',%s)",(pending_version,pending,row['id'],member))
        pending_gate=get(f'/bids/{pending}/submission-gate');check('pending_identity_explicitly_blocks_standard_gate','BID_IDENTITY_UNCONFIRMED' in {x['code'] for x in pending_gate.json()['blockers']})
        pending_fact=dict(actual_document_version_id=str(pending_version),evidence_ref=str(pending_version),submitted_at='2000-01-01T00:00:00Z',explanation='只据实登记早年合成事实，未补造提交前评审')
        wrong=post(f'/bids/{pending}/submission-facts',pending_fact|dict(evidence_ref=str(body)),get(f'/bids/{pending}').headers['etag']);checkresp('cross_project_attachment_is_rejected',wrong,404)
        saved=post(f'/bids/{pending}/submission-facts',pending_fact,get(f'/bids/{pending}').headers['etag']);assert saved.status_code==201,saved.json().get('code')
        check('unknown_identity_past_offline_fact_is_allowed_but_not_laundered',saved.json()['identity']['binding_status']=='pending' and not saved.json()['gate_checked'] and saved.json()['open_risk_snapshot']['open'] is None and saved.json()['checks_label']=='未经过系统完整检查')
    # Server collection can be rediscovered without any local upload IDs.
    batches=[]
    for i in range(3):
        response=post(f'/bids/{bid}/upload-batches',{});assert response.status_code==201;batches.append(response.json()['id'])
    first=get(f'/bids/{bid}/upload-batches',params={'limit':2,'state':'open'}).json();second=get(f'/bids/{bid}/upload-batches',params={'limit':2,'state':'open','cursor':first['next_cursor']}).json()
    check('server_batch_discovery_paginates_without_duplicate_or_local_ids',set(x['id'] for x in first['items']+second['items'])==set(batches) and first['has_more'] and not second['has_more'])
    mixed=client.get('/api/v1'+f'/bids/{pending}/upload-batches',params={'limit':2,'state':'open','cursor':first['next_cursor']})
    filtered=client.get('/api/v1'+f'/bids/{bid}/upload-batches',params={'limit':2,'state':'failed','cursor':first['next_cursor']})
    check('batch_cursor_cannot_cross_project_or_filter',mixed.status_code==422 and filtered.status_code==422)
    # Append a new partial parse on the *same* body, preserve old accepted fact.
    with connection() as c:
        old=c.execute('SELECT * FROM parse_runs WHERE document_version_id=%s ORDER BY created_at DESC,id DESC LIMIT 1',(body,)).fetchone()
        p=uuid4();c.execute("INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,3,'partial','[\"INDEPENDENT_PARTIAL_PARSE\"]')",(p,bid,body,old['parser_digest'],old['config_digest']))
    blocked=gate();check('new_partial_parse_invalidates_old_review_authority_without_rewriting_fact','REVIEW_INPUT_STALE' in {x['code'] for x in blocked.json()['blockers']} and get(f"/submissions/{record['id']}").json()['gate_checked'])
    check('all_new_gate_report_submission_operations_use_no_additional_model_calls',len(calls)==before)
    check('excluded_unmatched_knowledge_controller_never_imported','packages.knowledge.rag_coordinator' not in sys.modules)
    return dict(new_submission_id=new['id'],old_submission_id=record['id'],pending_identity_bid=pending,report_document_version_ids=versions,submission_cases_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
