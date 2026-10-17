"""核对产品交付计划的静态引用；不执行应用或模型。"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


HERE = Path(__file__).resolve().parent
DOCS = HERE.parent.parent
CONTRACTS = DOCS / "05-技术与设计/detailed-design"


def read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    plan = read_json(HERE / "delivery-plan.json")
    acceptance = read_json(HERE / "a1-acceptance.json")
    original_path = DOCS / acceptance["original_source"]
    original = read_json(original_path)
    schema = read_json(CONTRACTS / "protocol.schema.json")["$defs"]
    api = read_json(CONTRACTS / "openapi.json")
    operation_ids = {
        operation["operationId"]
        for item in api["paths"].values()
        for operation in item.values()
        if isinstance(operation, dict) and "operationId" in operation
    }
    checks: list[dict] = []

    def check(name: str, valid: bool, detail: str) -> None:
        checks.append({"name": name, "passed": valid, "detail": detail})

    check(
        "原验收输入绑定",
        hashlib.sha256(original_path.read_bytes()).hexdigest()
        == acceptance["original_source_sha256"],
        "只校验原记录摘要，不更改历史状态。",
    )
    original_cases = original["cases"]
    cases = acceptance["cases"]
    check(
        "原13项ID完整且顺序一致",
        len(cases) == 13
        and [case["id"] for case in cases]
        == [case["id"] for case in original_cases],
        "执行拆解不得增删、改名或覆盖原13项。",
    )
    check(
        "原场景语义逐字保留",
        all(
            case["original_setup"] == source["setup"]
            and case["original_expected"] == source["expected"]
            and case["original_requires"] == source["requires"]
            and source["status"] == "pending_application"
            for case, source in zip(cases, original_cases, strict=True)
        ),
        "原文中的旧表述仅作原证据；实现按当前PRD及交接执行。",
    )
    tasks = cases + acceptance["supporting_tasks"]
    check(
        "未来执行状态仍待应用验证",
        all(item["status"] == "pending_application" for item in tasks)
        and all(stage["status"] == "pending_application" for stage in plan["stages"])
        and all(
            record["software_executed"] == 0
            and record["paid_calls"] == 0
            and record["credentials_read"] is False
            for record in (plan, acceptance)
        )
        and all(
            all(value == 0 for value in item["execution_evidence"].values())
            for item in cases
        ),
        "本检查无软件运行、凭据读取、真人或收费调用。",
    )
    stage_ids = {stage["id"] for stage in plan["stages"]}
    gap_ids = {gap["id"] for gap in plan["contract_gaps"]}
    for item in tasks:
        name = item["id"]
        check(
            f"{name}执行任务齐备",
            all(
                item.get(field)
                for field in (
                    "execution_steps",
                    "ui_assertions",
                    "backend_assertions",
                    "target_operation_ids",
                    "schema_refs",
                )
            )
            and set(item["delivery_stage"].split("+")) <= stage_ids,
            "场景须含动作、界面、后端及交付批次。",
        )
        check(
            f"{name}目标接口引用",
            set(item["target_operation_ids"]) <= operation_ids,
            "仅证明目标operation存在，不证明当前实现可调用。",
        )
        for ref in item["schema_refs"]:
            properties = schema.get(ref["schema"], {}).get("properties", {})
            check(
                f"{name}:{ref['schema']}字段引用",
                bool(properties) and set(ref["fields"]) <= set(properties),
                ", ".join(ref["fields"]),
            )
        check(
            f"{name}契约缺口追踪",
            set(item["actual_contract_gaps"]) <= gap_ids,
            "缺口由下一批实际契约闭合，不假造目标字段。",
        )
    for gap in plan["contract_gaps"]:
        check(
            f"{gap['id']}已有接口引用",
            set(gap["target_exists"]) <= operation_ids
            and gap["actual_contract_needed"] is True,
            gap["need"],
        )
    required_frs = {f"FR-{number:03}" for number in range(1, 41)}
    fr_records = plan["functional_requirements"]
    check(
        "FR-001至040全量追踪",
        {record["id"] for record in fr_records} == required_frs
        and len(fr_records) == 40
        and all(record["name"] and record["stages"] for record in fr_records),
        "每条需求保留既有编号与名称。",
    )
    check(
        "需求与批次双向一致",
        all(
            set(record["stages"])
            == {
                stage["id"]
                for stage in plan["stages"]
                if record["id"] in stage["functional_requirements"]
            }
            for record in fr_records
        ),
        "拆批不会遗失需求或默认为已完成。",
    )
    check(
        "阶段依赖及退出门槛",
        all(
            set(stage["dependencies"]) <= stage_ids | {"PDF_SLICE_ACCEPTED"}
            and stage["id"] not in stage["dependencies"]
            and stage["exit_conditions"]
            for stage in plan["stages"]
        ),
        "现有资料片与未来研究/模型/完整产品保持独立完成边界。",
    )
    check(
        "所有来源摘要绑定",
        all(
            (DOCS / source["path"]).is_file()
            and hashlib.sha256((DOCS / source["path"]).read_bytes()).hexdigest()
            == source["sha256"]
            for source in plan["source_bindings"]
        ),
        "后续源材料变化应做影响评估，不能覆盖旧执行证据。",
    )
    for path in sorted(HERE.glob("*.md")):
        links = re.findall(r"\]\(([^)]+)\)", path.read_text(encoding="utf-8"))
        local_paths = [link.split("#")[0] for link in links if "://" not in link]
        check(
            f"{path.name}本地链接存在",
            all(not link or (path.parent / link).exists() for link in local_paths),
            "只检查文件链接，不把引用数量当验证覆盖。",
        )
    result = {
        "scope": "产品计划静态完整性与引用校验，非软件验收",
        "static_plan_validation_only": True,
        "software_runs": 0,
        "browser_runs": 0,
        "human_participants": 0,
        "paid_calls": 0,
        "credentials_read": False,
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
        "original_cases": len(cases),
        "supporting_tasks": len(acceptance["supporting_tasks"]),
        "functional_requirements": len(fr_records),
        "input_bindings": [
            {
                "path": path.name,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in (
                HERE / "README.md",
                HERE / "A1验收与界面数据交接.md",
                HERE / "a1-acceptance.json",
                HERE / "delivery-plan.json",
                Path(__file__),
            )
        ],
    }
    (HERE / "plan-check-result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    failures = [item["name"] for item in checks if not item["passed"]]
    print(json.dumps({"static_plan_passed": result["passed"], "failures": failures}, ensure_ascii=False))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
