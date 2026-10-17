"""Small actual restricted-PG check of publication/replay isolation prerequisites."""

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from apps.api.rag_routes import assert_fresh_authority, replay
from packages.domain.runtime import DomainError, assert_role, connection


def main():
    out=Path(os.environ["QA_RAG04_ROOT"])/"transaction-prerequisites"
    out.mkdir(exist_ok=False)
    assert_role();checks=[]
    with connection() as c:
        assert_fresh_authority(c)
        checks.append(dict(name="real_RC_transaction_accepted",passed=True))
    for mode in ["autocommit","repeatable read","serializable"]:
        with connection() as c:
            if mode=="autocommit":c.commit();c.autocommit=True
            else:c.execute("SET TRANSACTION ISOLATION LEVEL "+mode)
            for name,fn in [("authority",lambda:assert_fresh_authority(c)),("replay_first_guard",lambda:replay(c,{},uuid4(),{}))]:
                try:fn()
                except DomainError as error:passed=error.code=="RAG_FRESH_AUTHORITY_REQUIRED"
                else:passed=False
                checks.append(dict(name=mode+"_"+name+"_rejected",passed=passed))
    result=dict(passed=all(c["passed"] for c in checks),checks=checks,paid_generation_calls=0,model_rpc_requests=0,readonly=True,probe_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (out/"executed-probe.py").write_bytes(Path(__file__).read_bytes())
    (out/"result.json").write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
    print(dict(passed=result["passed"],checks=len(checks)))


if __name__=="__main__":main()
