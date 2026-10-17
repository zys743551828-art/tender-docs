"""核对固定Git对象和文档引用；不执行应用测试。"""

from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import subprocess
from urllib.parse import unquote


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    folder = Path(__file__).resolve().parent
    root = folder.parents[1]
    manifest_path = folder / "retention-matrix.json"
    manifest = json.loads(manifest_path.read_text())
    report_path = root / "02-业务与产品/GitHub实现功能保留对照.md"
    report = report_path.read_text()
    features = manifest["features"]
    assert [f["id"] for f in features] == [f"R{i:02}" for i in range(1, 27)]
    assert Counter(f["design_coverage"] for f in features) == {
        "covered": 15, "detail_gap": 10, "optional": 1
    }
    assert {f["followup"] for f in features if f["followup"]} == {
        f"D{i:02}" for i in range(1, 11)
    }
    assert manifest["application_tests_run"] is False
    assert manifest["target_contract_status"] == "recommendations_pending_contract_design"
    sources = []
    target_hashes = {}
    for feature in features:
        assert feature["tests_executed_in_this_review"] is False
        assert f"| {feature['id']} |" in report
        if feature["followup"]:
            assert f"| {feature['followup']} " in report
        target = root / feature["target"]["path"]
        assert any(line.startswith(feature["target"]["section_prefix"])
                   for line in target.read_text().splitlines()), feature["target"]
        target_hashes[feature["target"]["path"]] = digest(target.read_bytes())
        sources.extend(feature["code"] + feature["test_definitions"]
                       + feature.get("supporting_sources", []))
    sources.extend(item["source"] for item in manifest["caveats"])
    blobs = {}
    for source in sources:
        identity = manifest["repositories"][source["repo"]]
        key = (identity["name"], identity["commit"], source["path"])
        if key not in blobs:
            blobs[key] = subprocess.check_output([
                "git", "-C", str(root.parent / identity["name"]),
                "show", f"{identity['commit']}:{source['path']}"
            ])
        data = blobs[key]
        assert digest(data) == source["sha256"], source["path"]
        lines = data.decode().splitlines()
        assert source["anchor"] in lines[source["line"] - 1], source
        expected = (f"https://github.com/zys743551828-art/{identity['name']}/blob/"
                    f"{identity['commit']}/{source['path']}#L{source['line']}")
        assert source["url"] == expected
    definitions = dict(re.findall(r"^\[(R\d{2}-[CT])\]: (\S+)$", report, re.M))
    usages = re.findall(r"\]\[(R\d{2}-[CT])\]", report)
    assert set(usages) == set(definitions)
    for feature in features:
        assert definitions[feature["id"] + "-C"] == feature["code"][0]["url"]
        if feature["test_definitions"]:
            assert definitions[feature["id"] + "-T"] == feature["test_definitions"][0]["url"]
    local_links = 0
    for document in (report_path, folder / "README.md"):
        for link in re.findall(r"\]\(([^)]+)\)", document.read_text()):
            if "://" in link or link.startswith("#"):
                continue
            target = (document.parent / unquote(link.split("#")[0])).resolve()
            # 本脚本成功结束时创建结果文件，首次执行允许它尚不存在。
            assert target.exists() or target == folder / "result.json", link
            local_links += 1
    contracts = root / "05-技术与设计/detailed-design"
    for filename in ("protocol.schema.json", "openapi.json", "domain.sql"):
        path = contracts / filename
        target_hashes[str(path.relative_to(root))] = digest(path.read_bytes())
    result = {
        "status": "passed",
        "verification_scope": "static_git_sources_and_document_references_only",
        "feature_groups": len(features), "source_locations": len(sources),
        "unique_git_blobs": len(blobs), "local_links": local_links,
        "design_coverage": dict(Counter(f["design_coverage"] for f in features)),
        "followups": 10, "application_tests_run": False,
        "report_sha256": digest(report_path.read_bytes()),
        "manifest_sha256": digest(manifest_path.read_bytes()),
        "target_documents_sha256": target_hashes,
    }
    (folder / "result.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
