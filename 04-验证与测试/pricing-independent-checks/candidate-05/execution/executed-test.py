"""Only affected rounding-clause acceptance/regression over frozen pricing05."""
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
from apps.api.main import app
from packages.domain.runtime import connection,sha
from packages.rag.indexing import ensure_parse_sources

ROOT=Path(os.environ['QA_ASSISTANT_ROOT'])
spec=importlib.util.spec_from_file_location('pricing05_independent_fixture',ROOT/'qa-helpers/assistant_fixture.py');helper=importlib.util.module_from_spec(spec);spec.loader.exec_module(helper)
FIELDS=['method','maximum','minimum_price','maximum_price','minimum_valid_bids','amount_unit','currency','rounding_policy','baseline_set','exclusions','intermediate_rounding','clamping']
OBS=[]


@pytest.fixture
def world(monkeypatch):
    gen=helper.s.__wrapped__(monkeypatch)
    try:yield next(gen)
    finally:gen.close()


def test_original_counterexample_and_affirmative_ambiguous_clauses(world):
    s=world;bid,actor=s['bids'][0],s['actors'][0];entries=json.loads((ROOT/'fixture-inputs/manifest.json').read_text());quotes={}
    assert entries[0]['sha256']=='930c1515697239ce3b8ea860f4bf745beced4b1098b1044194ec01b09502d7d7'
    with connection() as c:
        material=uuid4();c.execute("INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,1,'{}',%s)",(material,bid,sha(str(material))));c.execute('UPDATE bid_profiles SET current_material_id=%s WHERE bid_id=%s',(material,bid));c.execute("UPDATE bids SET material_revision=1,settings='{\"pricing\":true,\"auto_review\":false}' WHERE id=%s",(bid,))
        for ordinal,entry in enumerate(entries):
            raw=(ROOT/'fixture-inputs'/entry['path']).read_bytes();assert sha(raw)==entry['sha256'];pdf=pypdfium2.PdfDocument(raw);assert len(pdf)==1;page=pdf[0];tp=page.get_textpage();text=tp.get_text_bounded();width,height=page.get_size();tp.close();page.close();pdf.close();assert text.replace('\r\n','\n')=='\n'.join(entry['declared_lines']) and '系数' not in text
            doc,parse,ev,blob=[uuid4() for _ in range(4)];key='qa-pricing05/'+str(blob);c.execute("INSERT INTO blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,'application/pdf') ON CONFLICT(digest) DO NOTHING",(blob,sha(raw),key,len(raw)));b=c.execute('SELECT id,object_key FROM blobs WHERE digest=%s',(sha(raw),)).fetchone();s['objects'][b['object_key']]=raw;c.execute("INSERT INTO document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,%s,now(),'source_original',%s)",(doc,bid,b['id'],entry['path'],actor));c.execute("INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,'tender',%s)",(material,bid,doc,ordinal));c.execute("INSERT INTO parse_runs(id,bid_id,document_version_id,parser_digest,config_digest,page_count,coverage,gaps) VALUES(%s,%s,%s,%s,%s,1,'complete','[]')",(parse,bid,doc,sha('independent-exact-pdfium-page'),sha({})));c.execute("INSERT INTO pages(bid_id,parse_id,physical_page,width,height,rotation,quality,gaps) VALUES(%s,%s,1,%s,%s,0,'usable','[]')",(bid,parse,width,height));c.execute("INSERT INTO evidence_blocks(id,bid_id,parse_id,physical_page,ordinal,kind,text_content,digest) VALUES(%s,%s,%s,1,0,'text',%s,%s)",(ev,bid,parse,text,sha(text.encode())));_,source=ensure_parse_sources(c,bid,parse,publish_readable=True);revision=c.execute('SELECT revision FROM retrieval.source_heads WHERE source_id=%s',(source,)).fetchone()['revision'];quotes[entry['path']]=dict(source_id=str(source),source_revision=revision,document_version_id=str(doc),parse_id=str(parse),physical_page=1,evidence_id=str(ev),quote=text,supported_fields=FIELDS.copy())
    with TestClient(app) as api:
        for entry in entries:
            request=dict(title='独立报价否定范围'+entry['path'],formula=dict(method='lowest_valid_ratio',maximum='40',minimum_price=None,maximum_price=None,minimum_valid_bids=3,above_coefficient='0',below_coefficient='0'),amount_unit='元',currency='CNY',rounding_policy='half_up_2dp',interpretation=dict(baseline_set='own_and_all_numerically_eligible_assumptions',exclusions='none',random_parameters='none',intermediate_rounding='none',clamping='zero_to_maximum'),terms_complete_confirmed=True,matches_source_confirmed=True,evidence_quotes=[quotes[entry['path']]],reason='真实合成原页逐字对应，测试否定范围不绕过不支持条款')
            context=api.get(f'/api/v1/bids/{bid}/pricing-context',headers=s['h']());assert context.status_code==200
            before=len(s['objects']);r=api.post(f'/api/v1/bids/{bid}/pricing-formulas',json=request,headers=s['h'](tag=context.headers['etag']));value=r.json();row=dict(name=entry['path'],expected=entry['expected'],actual_status=r.status_code,result=value,pdf_sha256=entry['sha256']);OBS.append(row)
            assert r.status_code==entry['expected'],row
            if entry['expected']==422:
                assert value['code']=='PRICING_FORMULA_UNSUPPORTED' and len(s['objects'])==before
            else:
                scenario=dict(formula_version_id=value['formula_version_id'],candidate_price='125',competitor_price_assumptions=['100','100','0'],rounding_policy='half_up_2dp',candidate_prices=[],curve_range=None,amount_unit='元',currency='CNY')
                sim=api.post(f'/api/v1/bids/{bid}/pricing-simulations',json=scenario,headers=s['h'](tag=r.headers['etag']));row.update(simulation_status=sim.status_code,simulation_score=sim.json().get('price_score'));assert sim.status_code==201 and sim.json()['price_score']=='32.00'
        with connection() as c:
            assert c.execute("SELECT count(*) AS n FROM decisions WHERE bid_id=%s AND object_kind='pricing_formula'",(bid,)).fetchone()['n']==3
            assert c.execute("SELECT count(*) AS n FROM decisions WHERE bid_id=%s AND object_kind='pricing_plan'",(bid,)).fetchone()['n']==0


@pytest.fixture(scope='session',autouse=True)
def evidence():
    manifest=json.loads((ROOT/'runtime-manifest.json').read_text())
    for x in manifest:assert hashlib.sha256((ROOT/'execution-inputs'/x['path']).read_bytes()).hexdigest()==x['sha256']
    yield
    for x in manifest:assert hashlib.sha256((ROOT/'execution-inputs'/x['path']).read_bytes()).hexdigest()==x['sha256']
    encoded=(json.dumps(app.openapi(),ensure_ascii=False,indent=2)+'\n').encode();assert hashlib.sha256(encoded).hexdigest()=='3249fec969b04a3bc298bfd77a2f0dda7d1a4f3a1f1213a1a8d6648db6998fe7'
    loaded=[]
    for name,module in sorted(sys.modules.items()):
        file=getattr(module,'__file__',None)
        if file and name.startswith(('apps.','packages.')):
            file=Path(file).resolve();assert file.is_relative_to(ROOT/'execution-inputs'),name;loaded.append(dict(module=name,path=str(file.relative_to(ROOT/'execution-inputs')),sha256=hashlib.sha256(file.read_bytes()).hexdigest()))
    out=ROOT/'execution';out.mkdir(exist_ok=True);(out/'actual.openapi.json').write_bytes(encoded);(out/'executed-test.py').write_bytes(Path(__file__).read_bytes());(out/'result.json').write_text(json.dumps(dict(passed=len(OBS)==7 and all(x['actual_status']==x['expected'] for x in OBS),cases=OBS,loaded_modules=loaded,paid_calls=0,oss_requests=0,consumers_started=0),ensure_ascii=False,indent=2)+'\n')
