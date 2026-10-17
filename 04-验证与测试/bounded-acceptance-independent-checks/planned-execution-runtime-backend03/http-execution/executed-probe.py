"""Real loopback frozen03 API + isolated PG + native OSS, no broker consumption or model."""

from __future__ import annotations

import hashlib
import http.client
import json
import os
import secrets
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
import uvicorn
from jsonschema import Draft202012Validator, FormatChecker


def large_pdf():
    text = b"BT /F1 12 Tf 40 740 Td (Independent native multipart recovery evidence.) Tj ET"
    padding = b"x" * (8 * 1024 * 1024)
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>", b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>", b"<< /Length "+str(len(text)).encode()+b" >>\nstream\n"+text+b"\nendstream", b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>", b"<< /Length "+str(len(padding)).encode()+b" >>\nstream\n"+padding+b"\nendstream"]
    data, offsets = bytearray(b"%PDF-1.4\n"), []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data)); data.extend(f"{i} 0 obj\n".encode()+obj+b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for offset in offsets: data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(data)


def main():
    root = Path(os.environ["QA_MULTIPART_HTTP_ROOT"])
    out = root / "http-execution"
    out.mkdir(exist_ok=False)
    assert Path(os.environ["WORKBENCH_ENV_FILE"]).name == "rag-checks.env"
    from deploy.initialize_database import file_configuration
    # Load only the authorized object adapter configuration into this process.
    values = file_configuration(Path("/workspace/tender-workbench/.local/providers.env"))
    os.environ.update({k: v for k, v in values.items() if k.startswith("OBJECT_STORE_")})
    own_prefix = "workbench-checks/qa-multipart-http-"+uuid4().hex+"/"
    os.environ.update(OBJECT_STORE_PREFIX=own_prefix, APP_ORIGIN="http://127.0.0.1:8803", DEEPSEEK_API_KEY="")
    from apps.api.main import app
    from apps.worker import engine
    from deploy.review_acceptance import frozen_sources
    from packages.domain.runtime import assert_role, connection
    assert_role(); assert_role(True)
    candidate = root / "runtime-candidate.json"
    expected = "ce01d25d5adb45bf5991b8802d3394d84b2567dce782dd5a193327204db92f91"
    verify = frozen_sources(candidate, expected)
    api = app.openapi()
    assert hashlib.sha256((json.dumps(api, ensure_ascii=False, indent=2)+"\n").encode()).hexdigest() == "760748f52e64db495c1ac60d78f614ba2d5b0f3db77e7cd59abe6c769bddedae"
    origin = os.environ["APP_ORIGIN"]
    with socket.socket() as probe: probe.bind(("127.0.0.1", 8803))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=8803, access_log=False, log_level="critical"))
    thread = threading.Thread(target=server.run, daemon=True); thread.start()
    deadline = time.monotonic()+15
    while not server.started and thread.is_alive() and time.monotonic()<deadline: time.sleep(0.05)
    assert server.started
    login, password = "qa-multipart-"+uuid4().hex, secrets.token_urlsafe(30)
    account = subprocess.run([sys.executable, "-m", "apps.api.bootstrap", "--login", login, "--name", "独立分片测试成员", "--password-stdin"], input=password+"\n", text=True, capture_output=True, check=False)
    assert account.returncode == 0
    client = httpx.Client(base_url=origin, trust_env=False, timeout=60)
    second = httpx.Client(base_url=origin, trust_env=False, timeout=60)
    response = client.post("/api/v1/auth/login", json=dict(login_name=login,password=password),headers={"Origin":origin})
    assert response.status_code == 200
    csrf = response.json()["csrf_token"]
    response = second.post("/api/v1/auth/login", json=dict(login_name=login,password=password),headers={"Origin":origin})
    assert response.status_code == 200
    second_csrf = response.json()["csrf_token"]
    result = dict(passed=False, checks=[], paid_generation_calls=0, real_kafka_consumers_started=0, configured_origin=origin, object_prefix=own_prefix, scope="Dedicated frozen03 TCP8803, isolated rag-checks PG, real native OSS; direct document handler only. Not Kafka or browser acceptance.")
    part_size = 8*1024*1024
    data = large_pdf(); digest = hashlib.sha256(data).hexdigest()
    (out / "synthetic-multipart.pdf").write_bytes(data)
    stage = "setup"

    def check(name, value, observation=None):
        result["checks"].append(dict(name=name, passed=bool(value), observation=observation))

    def call(method, path, body=None, tag=None, key=None):
        response = client.request(method, "/api/v1"+path, json=body, headers={"Origin":origin,"X-CSRF-Token":csrf,"Idempotency-Key":key or uuid4().hex,**({"If-Match":tag} if tag else {})})
        return response

    def put(grant, index, payload, overrides=None, receiver=client):
        return receiver.put(grant["parts"][index]["url"], content=payload, headers={"Origin":origin,"X-CSRF-Token":csrf,"Content-Type":"application/octet-stream",**(overrides or {})})

    def raw(url, payload, *, length, expect=False, extra=""):
        selected = urlsplit(url)
        cookie = "; ".join(k+"="+v for k,v in client.cookies.items())
        headers = (f"PUT {selected.path}?{selected.query} HTTP/1.1\r\nHost: 127.0.0.1:8803\r\nOrigin: {origin}\r\nCookie: {cookie}\r\nX-CSRF-Token: {csrf}\r\nContent-Type: application/octet-stream\r\nContent-Length: {length}\r\n"+extra+("Expect: 100-continue\r\n" if expect else "")+"Connection: close\r\n\r\n").encode()
        sock = socket.create_connection(("127.0.0.1",8803),timeout=30)
        sock.sendall(headers)
        if expect:
            provisional = b""
            while b"\r\n\r\n" not in provisional: provisional += sock.recv(4096)
            assert provisional.startswith(b"HTTP/1.1 100")
        if payload: sock.sendall(payload)
        return sock

    try:
        response=call("POST","/bids",dict(title="独立分片HTTP恢复-"+uuid4().hex[:8])); assert response.status_code == 201
        bid=response.json()["id"]; result["bid_id"]=bid
        settings=call("GET",f"/bids/{bid}/settings")
        assert call("PATCH",f"/bids/{bid}/settings",dict(auto_review=False),settings.headers["etag"]).status_code==200
        batch=call("POST",f"/bids/{bid}/upload-batches",{}).json()["id"]
        request=dict(batch_id=batch,filename="合成分片恢复原件.pdf",role="body",byte_size=len(data),declared_sha256=digest,target=dict(kind="new_file"))
        create_key=uuid4().hex
        created=call("POST",f"/bids/{bid}/uploads",request,key=create_key); assert created.status_code==200
        grant=created.json(); upload=grant["upload_id"]; result["upload_id"]=upload
        schema={"$ref":"#/components/schemas/UploadGrant","components":api["components"]}
        schema_errors=list(Draft202012Validator(schema,format_checker=FormatChecker()).iter_errors(grant))
        check("actual_upload_grant_schema_and_private_two_part_layout",not schema_errors and grant["mode"]=="multipart" and grant["part_count"]==2 and grant["upload_url"] is None and len(grant["parts"])==2 and all(urlsplit(p["url"]).netloc=="127.0.0.1:8803" for p in grant["parts"]))
        repeated=call("POST",f"/bids/{bid}/uploads",request,key=create_key)
        check("create_replay_keeps_exact_grant_and_native_session",repeated.status_code==200 and repeated.json()==grant)
        stage="authorization_and_stream_bounds"
        check("missing_csrf_and_foreign_origin_rejected",put(grant,0,data[:part_size],{"X-CSRF-Token":""}).status_code==403 and put(grant,0,data[:part_size],{"Origin":"https://unapproved.invalid"}).status_code==403)
        check("same_member_other_session_ticket_rejected",put(grant,0,data[:part_size],{"X-CSRF-Token":second_csrf},second).status_code==403)
        check("wrong_media_and_wrong_pdf_magic_rejected",put(grant,0,data[:part_size],{"Content-Type":"application/pdf"}).status_code==415 and put(grant,0,b"WRONG"+data[5:part_size]).status_code==415)
        check("short_declared_part_size_rejected",put(grant,0,data[:100]).status_code==422)
        check("denied_requests_have_no_native_parts",call("GET",f"/uploads/{upload}/parts").json()["completed_parts"]==[])
        stage="refresh_during_real_stream"
        sock=raw(grant["parts"][0]["url"],data[:5],length=part_size,expect=True)
        refreshed=call("POST",f"/uploads/{upload}/authorizations",dict(part_numbers=[1,2]),created.headers["etag"]); assert refreshed.status_code==200
        sock.sendall(data[5:part_size]); stopped=http.client.HTTPResponse(sock); stopped.begin(); status=stopped.status; stopped.read(); sock.close()
        check("refresh_fences_inflight_stream_before_remote_publication",status in (403,412) and call("GET",f"/uploads/{upload}/parts").json()["completed_parts"]==[])
        check("stale_refresh_cas_rejected",call("POST",f"/uploads/{upload}/authorizations",dict(part_numbers=[1]),created.headers["etag"]).status_code==412)
        old_grant=grant;grant=refreshed.json()
        check("old_ticket_revoked_after_refresh",put(old_grant,0,data[:part_size]).status_code==403)
        stage="actual_remote_parts"
        first=put(grant,0,data[:part_size]);assert first.status_code==200
        # The application deliberately forgets this successful response metadata;
        # authoritative ListParts must recover it without re-uploading part1.
        del first
        observed=call("GET",f"/uploads/{upload}/parts").json()["completed_parts"]
        check("discarded_ack_recovered_from_native_list_without_reupload",len(observed)==1 and observed[0]["part_number"]==1 and observed[0]["byte_size"]==part_size)
        with connection() as c:
            c.execute("UPDATE upload_parts SET etag='independent-corrupt-cache' WHERE upload_id=%s AND part_number=1",(upload,))
        actual=call("GET",f"/uploads/{upload}/parts").json()["completed_parts"]
        check("server_list_reconciles_poisoned_db_cache_from_real_oss",actual==observed)
        final=put(grant,1,data[part_size:]);assert final.status_code==200
        actual=call("GET",f"/uploads/{upload}/parts").json()["completed_parts"]
        bad=[dict(p,etag="invalid-receipt") if p["part_number"]==2 else p for p in actual]
        wrong_key=uuid4().hex
        wrong=call("POST",f"/uploads/{upload}/complete",dict(parts=bad,declared_sha256=digest),refreshed.headers["etag"],wrong_key)
        repeated_wrong=call("POST",f"/uploads/{upload}/complete",dict(parts=bad,declared_sha256=digest),refreshed.headers["etag"],wrong_key)
        check("wrong_complete_receipt_reversible_and_exact_failure_replay",wrong.status_code==422 and repeated_wrong.status_code==422 and repeated_wrong.json()==wrong.json() and repeated_wrong.headers.get("etag")==wrong.headers.get("etag"))
        renewed=call("POST",f"/uploads/{upload}/authorizations",dict(part_numbers=[1,2]),wrong.headers["etag"]);assert renewed.status_code==200
        stage="complete_verify_no_broker"
        complete_key=uuid4().hex
        complete=call("POST",f"/uploads/{upload}/complete",dict(parts=actual,declared_sha256=digest),renewed.headers["etag"],complete_key);assert complete.status_code==202
        repeated_complete=call("POST",f"/uploads/{upload}/complete",dict(parts=actual,declared_sha256=digest),renewed.headers["etag"],complete_key)
        operation=complete.json()["operation"]["id"]
        check("valid_full_sha_complete_exact_replay_one_verify_intent",repeated_complete.status_code==202 and repeated_complete.json()==complete.json())
        with connection(True) as c:
            before=c.execute("SELECT verified_version_id,state FROM uploads WHERE id=%s",(upload,)).fetchone()
            events=c.execute("SELECT payload FROM outbox WHERE operation_id=%s ORDER BY available_at,id",(operation,)).fetchall()
        check("complete_has_no_formal_document_before_normal_verifier",before["state"]=="verifying" and before["verified_version_id"] is None)
        for event in events: engine.handle(event["payload"])
        verified=call("GET",f"/uploads/{upload}"); assert verified.status_code==200
        check("ordinary_direct_handler_verifies_large_pdf_exact_original",verified.json()["state"]=="verified" and verified.json()["document_version_id"] is not None)
        with connection(True) as c:
            n=c.execute("SELECT count(*) AS n FROM model_calls m JOIN operations o ON o.id=m.operation_id WHERE o.bid_id=%s",(bid,)).fetchone()["n"]
            doc=c.execute("SELECT b.digest,b.byte_size FROM document_versions v JOIN blobs b ON b.id=v.blob_id WHERE v.id=%s",(verified.json()["document_version_id"],)).fetchone()
        check("actual_persisted_blob_sha_and_size_and_zero_model_calls",doc["digest"]==digest and doc["byte_size"]==len(data) and n==0)
        check("verified_object_cannot_abort_or_new_complete",call("POST",f"/uploads/{upload}/abort",tag=verified.headers["etag"]).status_code==409 and call("POST",f"/uploads/{upload}/complete",dict(parts=actual,declared_sha256=digest),verified.headers["etag"]).status_code==409)
        result["retained_registered_synthetic_objects"]="Staging and content-addressed original remain referenced by this isolated DB fixture; no delete attempted. No other prefix touched."
        result["document_version_id"]=verified.json()["document_version_id"]
        result["original_sha256"]=digest
        verify(); result["passed"]=all(c["passed"] for c in result["checks"])
    except Exception as error:
        result["fatal"]=dict(stage=stage,type=type(error).__name__,message="Independent multipart HTTP probe incomplete; no Cookie/ticket/native UploadId/provider response logged")
    finally:
        client.close();second.close();server.should_exit=True;thread.join(15)
        result["test_api_stopped"]=not thread.is_alive()
    result.update(runtime_candidate_sha256=expected,probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (out/"executed-probe.py").write_bytes(Path(__file__).read_bytes())
    (out/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(dict(passed=result["passed"],checks=len(result["checks"]),failed=[c["name"] for c in result["checks"] if not c["passed"]],fatal=result.get("fatal"),test_api_stopped=result["test_api_stopped"]))
    return 0 if result["passed"] else 1


if __name__=="__main__":raise SystemExit(main())
