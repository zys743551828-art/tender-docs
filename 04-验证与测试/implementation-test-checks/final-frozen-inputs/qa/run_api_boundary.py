"""执行真实HTTP边界验收并输出范围准确的结果，不记录口令/Cookie。"""

import hashlib
import json
import os
import unittest
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main():
    import test_api_boundary

    suite = unittest.defaultTestLoader.loadTestsFromModule(test_api_boundary)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    executed = max(0, result.testsRun - len(result.skipped))
    output = {
        "scope": "实际HTTP认证、CSRF、会话撤销和项目幂等边界；不是完整投标闭环",
        "executed_at": datetime.now(timezone.utc).isoformat(),
        "status": "not_ready"
        if not executed
        else ("passed" if result.wasSuccessful() else "failed"),
        "tests_run": result.testsRun,
        "tests_executed": executed,
        "skipped": len(result.skipped),
        "failed": len(result.failures),
        "errors": len(result.errors),
        "base_url": os.environ.get("QA_BASE_URL"),
        "origin": os.environ.get("QA_ORIGIN"),
        "application_identity": os.environ.get("QA_APP_REVISION"),
        "contract_sha256": os.environ.get("QA_CONTRACT_SHA256"),
        "configuration_sha256": os.environ.get("QA_CONFIG_SHA256"),
        "adapter_mode": os.environ.get("QA_ADAPTER_MODE"),
        "sources": {
            n: hashlib.sha256((HERE / n).read_bytes()).hexdigest()
            for n in ["test_api_boundary.py", "run_api_boundary.py"]
        },
        "not_covered": [
            "PG/Kafka进程恢复",
            "上传/原件/版本",
            "浏览器和真实用户",
            "真实模型与检索效果",
            "生产运行",
        ],
    }
    target = Path(os.environ.get("QA_RESULT_PATH", str(HERE / "api-boundary-result.json")))
    target.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    return 2 if not executed else (0 if result.wasSuccessful() else 1)


if __name__ == "__main__":
    raise SystemExit(main())
