"""独立API角色控制面：完成构建的受控PDF接入；不调用生成模型。"""

import argparse
import json
import signal
import threading

import psycopg

from packages.domain.build_followups import EVENT, tick
from packages.domain.runtime import DomainError, assert_role, connection

STOP = threading.Event()


def run(once=False):
    assert_role()
    while not STOP.is_set():
        with connection() as c:
            rows = c.execute(
                "SELECT b.id FROM builds b JOIN build_inputs i ON i.id=b.input_id JOIN operations o ON o.id=b.operation_id WHERE i.manifest->>'follow_up'='review' AND o.state IN('succeeded','failed','cancelled') AND NOT EXISTS(SELECT 1 FROM audit_entries a WHERE a.event_type=%s AND a.payload->>'build_id'=b.id::text) ORDER BY o.created_at,b.id LIMIT 100",
                (EVENT,),
            ).fetchall()
        for row in rows:
            try:
                tick(row["id"])
            except DomainError as error:
                print(
                    json.dumps({"event": "build_follow_up_blocked", "code": error.code}), flush=True
                )
            except psycopg.Error:
                print(json.dumps({"event": "build_controller_dependency_failure"}), flush=True)
        if once:
            return
        STOP.wait(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: STOP.set())
    run(args.once)


if __name__ == "__main__":
    main()
