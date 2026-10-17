import json
from pathlib import Path
from tests.test_application_composition_integration import headers
pytest_plugins=["tests.test_application_composition_integration"]
def test_original_ac01_counterexample_changes_tag_and_stale_write_fails(application):
 w=application;web=w["client"];url=f"/api/v1/bids/{w['bid']}/lifecycle"
 first=web.get(url);assert first.status_code==200
 batch=web.post(f"/api/v1/bids/{w['bid']}/upload-batches",json={},headers=headers(w));assert batch.status_code==201
 later=web.get(url);assert later.status_code==200 and first.json()!=later.json()
 assert first.headers["etag"]!=later.headers["etag"]
 rejected=web.post(url,json={"action":"terminate","reason":"独立原场景旧tag"},headers=headers(w,first.headers["etag"]));assert rejected.status_code==412
 assert web.get(url).json()["phase"]=="working"
 Path(__file__).resolve().parents[2].joinpath("original-counterexample-delta.json").write_text(json.dumps({"first":first.json(),"later":later.json(),"first_etag":first.headers["etag"],"later_etag":later.headers["etag"],"stale_write_status":rejected.status_code,"original_candidate01_evidence_preserved":True},ensure_ascii=False,indent=2)+"\n")
