"""Real synthetic Chinese original pages, PDFium text, restricted PG and actual HTTP handlers."""
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
from uuid import uuid4

import pypdfium2
import pytest
from fastapi.testclient import TestClient

ROOT=Path(os.environ['QA_ASSISTANT_ROOT'])
spec=importlib.util.spec_from_file_location('pricing_independent_fixture',ROOT/'qa-helpers/assistant_fixture.py');helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
from apps.api.main import app
from packages.domain.runtime import connection,sha,clean
from packages.rag.indexing import ensure_parse_sources

FIELDS=['method','maximum','minimum_price','maximum_price','minimum_valid_bids','amount_unit','currency','rounding_policy','baseline_set','exclusions','intermediate_rounding','clamping']
CHECKS=[]
def check(name,condition,**detail):
    CHECKS.append(dict(name=name,passed=bool(condition),**detail));assert condition,name


def test_actual_ratio_original_without_coefficients_and_mean_requirements(monkeypatch):
    gen=helper.s.__wrapped__(monkeypatch);s=next(gen);actor,bid=s['actors'][0],s['bids'][0]
    inputs=json.loads((ROOT/'fixture-inputs/manifest.json').read_text());sources={}
    with connection() as c:
        material=uuid4();c.execute("INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,1,'{}',%s)",(material,bid,sha(str(material))));c.execute("UPDATE bid_profiles SET current_material_id=%s WHERE bid_id=%s",(material,bid));c.execute("UPDATE bids SET material_revision=1,settings='{\"pricing\":true,\"auto_review\":false}' WHERE id=%s",(bid,))
        for ordinal,entry in enumerate(inputs):
            raw=(ROOT/'fixture-inputs'/entry['path']).read_bytes();assert hashlib.sha256(raw).hexdigest()==entry['sha256']
            pdf=pypdfium2.PdfDocument(raw);assert len(pdf)==1;page=pdf[0];tp=page.get_textpage();text=tp.get_text_bounded();width,height=page.get_size();tp.close();page.close();pdf.close()
            assert text.replace('\r\n','\n')=='\n'.join(entry['declared_lines'])
            if entry['path']=='ratio.pdf':check('actual_ratio_original_no_coefficient_clause','系数' not in text,pdf_sha256=sha(raw),physical_pages=1,actual_text=text)
            doc,parse,ev,blob=uuid4(),uuid4(),uuid4(),uuid4();key='qa-pricing-original/'+str(blob);s['objects'][key]=raw
            c.execute("INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,'application/pdf')",(blob,sha(raw),key,len(raw)));c.execute("INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,%s,now(),'source_original',%s)",(doc,bid,blob,entry['path'],actor));c.execute("INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,'tender',%s)",(material,bid,doc,ordinal))
            c.execute("INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'complete','[]')",(parse,bid,doc,sha('independent-actual-pdfium-synthetic-text'),sha(dict(actual_full_page=True))));c.execute("INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,1,%s,%s,0,'usable','[]')",(bid,parse,width,height));c.execute("INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,0,'text',%s,%s)",(ev,bid,parse,text,sha(text.encode())));_,source=ensure_parse_sources(c,bid,parse,publish_readable=True);revision=c.execute('SELECT revision FROM retrieval.source_heads WHERE source_id=%s',(source,)).fetchone()['revision']
            sources[entry['path']]=dict(source_id=str(source),source_revision=revision,document_version_id=str(doc),parse_id=str(parse),physical_page=1,evidence_id=str(ev),quote=text,supported_fields=FIELDS.copy())
    def body(name='ratio.pdf'):
        mean=name=='mean.pdf';q=copy.deepcopy(sources[name]);q['supported_fields']=FIELDS+(['above_coefficient','below_coefficient'] if mean else [])
        return dict(title='独立原页最低价试算' if not mean else '独立均值原页试算',formula=dict(method='mean_valid_deviation' if mean else 'lowest_valid_ratio',maximum='40',minimum_price=None,maximum_price=None,minimum_valid_bids=3,above_coefficient='1' if mean else '0',below_coefficient='2' if mean else '0'),amount_unit='元',currency='CNY',rounding_policy='half_up_2dp',interpretation=dict(baseline_set='own_and_all_numerically_eligible_assumptions',exclusions='none',random_parameters='none',intermediate_rounding='none',clamping='zero_to_maximum'),terms_complete_confirmed=True,matches_source_confirmed=True,evidence_quotes=[q],reason='按真实合成原页明确人工登记，仅验证受控协议，不代表真实投标或正式评分')
    with TestClient(app) as api:
        s['api']=api
        def register(value,key=None):
            context=api.get(f'/api/v1/bids/{bid}/pricing-context',headers=s['h']());assert context.status_code==200
            return api.post(f'/api/v1/bids/{bid}/pricing-formulas',json=value,headers=s['h'](tag=context.headers['etag'],key=key))
        good=body();first=register(good);check('http_ratio_register_with_twelve_real_fields_no_coefficient_mapping',first.status_code==201,status=first.status_code,body=first.json())
        formula=first.json();identity=formula['formula_version_id'];request=dict(formula_version_id=identity,candidate_price='125',competitor_price_assumptions=['100','100','0'],rounding_policy='half_up_2dp',candidate_prices=['100'],curve_range=None,amount_unit='元',currency='CNY')
        simulation=api.post(f'/api/v1/bids/{bid}/pricing-simulations',json=request,headers=s['h'](tag=first.headers['etag']));check('http_ratio_simulation_manual_expected_32',simulation.status_code==201 and simulation.json()['price_score']=='32.00',status=simulation.status_code,result=simulation.json())
        with connection() as c:
            check('simulation_does_not_implicitly_save_plan',c.execute("SELECT count(*) AS n FROM decisions WHERE bid_id=%s AND object_kind='pricing_plan'",(bid,)).fetchone()['n']==0)
        before_objects=len(s['objects'])
        for field in FIELDS:
            bad=body();bad['evidence_quotes'][0]['supported_fields'].remove(field);r=register(bad)
            check('missing_real_field_'+field,r.status_code==422 and r.json()['code']=='PRICING_FORMULA_INCOMPLETE' and field in r.json()['details']['missing_fields'])
        for coefficient in ['above_coefficient','below_coefficient']:
            bad=body();bad['formula'][coefficient]='1';r=register(bad)
            check('ratio_nonzero_'+coefficient+'_rejected',r.status_code==422 and r.json()['code']=='PRICING_FORMULA_UNSUPPORTED')
        bad=body();bad['formula']['maximum']='41';r=register(bad);check('unquoted_real_parameter_value_rejected',r.status_code==422 and r.json()['code']=='PRICING_PARAMETER_WITHOUT_EVIDENCE')
        for place in ['formula','supported_fields']:
            bad=body()
            if place=='formula':bad['formula']['invented_coefficient']='0'
            else:bad['evidence_quotes'][0]['supported_fields'].append('invented_coefficient')
            r=register(bad);check('unknown_'+place+'_field_rejected',r.status_code==422)
        for field in ['above_coefficient','below_coefficient']:
            bad=body('mean.pdf');bad['evidence_quotes'][0]['supported_fields'].remove(field);r=register(bad)
            check('mean_requires_real_'+field,r.status_code==422 and r.json()['code']=='PRICING_FORMULA_INCOMPLETE' and field in r.json()['details']['missing_fields'])
        check('all_rejected_registrations_no_staged_objects',len(s['objects'])==before_objects)
        mean=register(body('mean.pdf'));check('mean_with_both_actual_coefficient_clauses_remains_usable',mean.status_code==201,status=mean.status_code)
        with connection() as c:
            count=c.execute("SELECT count(*) AS n FROM decisions WHERE bid_id=%s AND object_kind='pricing_formula'",(bid,)).fetchone()['n'];check('exactly_two_actual_formula_registrations',count==2,count=count)
    try:next(gen)
    except StopIteration:pass


@pytest.fixture(scope='session',autouse=True)
def record_evidence():
    rows=json.loads((ROOT/'runtime-manifest.json').read_text())
    for x in rows:assert hashlib.sha256((ROOT/'execution-inputs'/x['path']).read_bytes()).hexdigest()==x['sha256']
    yield
    for x in rows:assert hashlib.sha256((ROOT/'execution-inputs'/x['path']).read_bytes()).hexdigest()==x['sha256']
    actual=app.openapi();encoded=(json.dumps(actual,ensure_ascii=False,indent=2)+'\n').encode();assert hashlib.sha256(encoded).hexdigest()=='3249fec969b04a3bc298bfd77a2f0dda7d1a4f3a1f1213a1a8d6648db6998fe7'
    loaded=[]
    for name,module in sorted(sys.modules.items()):
        p=getattr(module,'__file__',None)
        if p and name.startswith(('apps.','packages.')):
            p=Path(p).resolve();assert p.is_relative_to(ROOT/'execution-inputs'),name;loaded.append(dict(module=name,path=str(p.relative_to(ROOT/'execution-inputs')),sha256=hashlib.sha256(p.read_bytes()).hexdigest()))
    out=ROOT/os.environ.get('QA_PRICING04_EXECUTION','execution');out.mkdir(exist_ok=True);(out/'actual.openapi.json').write_bytes(encoded);(out/'executed-test.py').write_bytes(Path(__file__).read_bytes());(out/'result.json').write_text(json.dumps(dict(passed=bool(CHECKS) and all(c['passed'] for c in CHECKS),checks=CHECKS,loaded_sources=loaded,scope='Actual synthetic original Chinese PDF text extracted by PDFium, then explicit full-page parse publication fixture; actual nonowner PG + production pricing HTTP ASGI, memory formula-object I/O. No original upload/production parser-quality claim.',paid_calls=0,oss_requests=0,consumers_started=0),ensure_ascii=False,indent=2)+'\n')
