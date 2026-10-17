"""实际API→S3→Kafka→Worker→原页文字，不执行模型或伪造评审成功。"""

from __future__ import annotations

import hashlib
import os
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from packages.domain.runtime import connection
from qa.helpers import Client, ensure, members, pdf_bytes, upload, write_result


def main():
    evidence = {
        "scope": "实际单文件PDF上传/核验/入库/文字解析与未配置模型；非完整A1",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "checks": [],
        "passed": False,
        "software_executed": True,
        "model_executed": False,
    }
    stage = "初始化"
    try:
        first, second = members()
        client, other = Client(first), Client(second)
        bid = client.bid()
        evidence["bid_id"] = bid
        stage = "全成员访问"
        status, _, body = other.call("GET", f"/bids/{bid}")
        ensure(status == 200 and body["id"] == bid, "第二成员可访问项目")
        evidence["checks"].append("两名登录成员真实全项目访问")
        batch = client.batch(bid)
        data = pdf_bytes()
        evidence["pdf_sha256"] = hashlib.sha256(data).hexdigest()
        stage = "真实上传与Kafka核验"
        grant, accepted = upload(client, bid, batch, data)
        upload_id = grant["upload_id"]
        evidence.update(upload_id=upload_id, verify_operation_id=accepted["operation"]["id"])
        headers, view = client.poll(
            f"/uploads/{upload_id}", lambda x: x["state"] in ["verified", "quarantined"], "上传核验"
        )
        ensure(view["state"] == "verified", "正确字节必须核验成功")
        ensure(view["document_version_id"], "核验产生独立原件版本")
        evidence["document_version_id"] = view["document_version_id"]
        evidence["checks"].append("真实签名S3字节与Kafka Worker核验")
        stage = "批次提交"
        status, bid_headers, _ = client.call("GET", f"/bids/{bid}")
        ensure(status == 200, "读取bid ETag")
        payload = dict(
            accepted_upload_ids=[upload_id],
            accepted_binding_ids=[view["binding_id"]],
            explicit_partial_acceptance=False,
        )
        status, _, committed = client.call(
            "POST",
            f"/bids/{bid}/upload-batches/{batch}/commit",
            payload,
            headers={"If-Match": bid_headers["ETag"]},
        )
        evidence["commit_status"] = status
        ensure(status == 202, "首次批次提交应能通过建库约束")
        op = committed["operation"]["id"]
        evidence["parse_operation_id"] = op
        _, done = client.poll(
            f"/operations/{op}", lambda x: x["state"] in ["succeeded", "failed"], "PDF解析"
        )
        ensure(done["state"] == "succeeded", "解析任务应完成且留下真实范围")
        evidence["checks"].append("批次提交与真实Worker文字解析")
        stage = "原页与原件"
        version = view["document_version_id"]
        status, _, parsed = client.call("GET", f"/documents/{version}/evidence")
        ensure(
            status == 200 and parsed["coverage"] == "partial", "文字提取不能冒充完整结构/视觉覆盖"
        )
        ensure(parsed["gaps"] and parsed["items"], "原页文字及结构缺口均可查")
        ensure(all(item["physical_page"] == 1 for item in parsed["items"]), "物理页身份准确")
        status, _, artifact = client.call("GET", f"/artifacts/{version}/download")
        ensure(status == 200, "原件授权可取")
        with urllib.request.urlopen(artifact["download_url"], timeout=15) as response:
            ensure(
                hashlib.sha256(response.read()).hexdigest() == evidence["pdf_sha256"],
                "下载原件字节SHA相等",
            )
        evidence["checks"].append("物理页证据、partial缺口与下载原件SHA")
        stage = "未配置模型"
        status, _, body = client.call("GET", f"/bids/{bid}")
        ensure(
            status == 200
            and body["current_body_id"] == version
            and body["current_review_id"] is None,
            "仅入库解析不制造评审轮次",
        )
        status, _, error = client.call(
            "POST",
            f"/bids/{bid}/reviews",
            dict(
                body_version_id=version,
                material_revision=body["material_revision"],
                trigger="manual",
            ),
        )
        ensure(
            status == 503 and error["code"] == "MODEL_NOT_CONFIGURED", "未接通模型明确503，不假通过"
        )
        evidence["checks"].append("未配置模型诚实失败，不产生虚假评审/分数")
        stage = "服务端SHA负向"
        bad_batch = client.batch(bid)
        wrong = data.replace(b"municipal", b"municipal", 1)[:-1] + b"X"
        bad_grant, _ = upload(client, bid, bad_batch, data, put_data=wrong)
        _, bad = client.poll(
            f"/uploads/{bad_grant['upload_id']}",
            lambda x: x["state"] in ["verified", "quarantined"],
            "错误SHA",
        )
        ensure(
            bad["state"] == "quarantined"
            and bad["document_version_id"] is None
            and bad["error_code"] == "UPLOAD_DIGEST_MISMATCH",
            "错误字节必须隔离且无有效版本",
        )
        evidence["checks"].append("服务器复算SHA拒绝同大小错误内容")
        with connection() as c:
            counts = c.execute(
                "SELECT (SELECT count(*) FROM effect_receipts WHERE operation_id=%s) AS verify_effects,(SELECT count(*) FROM parse_runs WHERE document_version_id=%s) AS parses",
                (evidence["verify_operation_id"], version),
            ).fetchone()
        ensure(counts["verify_effects"] == 1 and counts["parses"] == 1, "真实收据与解析持久化一次")
        evidence["database_observation"] = counts
        evidence["passed"] = True
    except Exception as error:
        evidence["failure"] = {
            "stage": stage,
            "type": type(error).__name__,
            "message": str(error)
            if isinstance(error, AssertionError)
            else "执行异常，详查对应QA进程；不输出私有URL/凭据",
        }
    evidence["finished_at"] = datetime.now(timezone.utc).isoformat()
    evidence["probe_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    path = os.environ.get("QA_RESULT_PATH", "qa/pdf-slice-result.json")
    write_result(path, evidence)
    print(
        {
            "passed": evidence["passed"],
            "checks": len(evidence["checks"]),
            "failure": evidence.get("failure"),
        }
    )
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
