"""仅测试进程的受控故障注入，调用真实Worker和Kafka，不改生产实现。"""

import json
import os
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

from apps.worker import engine

MODE = os.environ["QA_FAULT_MODE"]
OP = os.environ["QA_FAULT_OPERATION"]
MARKERS = Path(os.environ["QA_FAULT_MARKERS"])


def mark(kind, **data):
    with MARKERS.open("a") as f:
        f.write(
            json.dumps(
                dict(
                    kind=kind,
                    at=datetime.now(timezone.utc).isoformat(),
                    monotonic=time.monotonic(),
                    operation_id=OP,
                    **data,
                ),
                default=str,
            )
            + "\n"
        )


if MODE == "before-offset-exit":
    original = engine.Consumer

    class CrashBeforeOffset:
        def __init__(self, options):
            self.inner = original(options)

        def __getattr__(self, name):
            return getattr(self.inner, name)

        def commit(self, *args, **kwargs):
            message = kwargs.get("message")
            if message and json.loads(message.value())["operation_id"] == OP:
                mark(
                    "pg_committed_before_offset",
                    partition=message.partition(),
                    offset=message.offset(),
                )
                os._exit(73)
            return self.inner.commit(*args, **kwargs)

    engine.Consumer = CrashBeforeOffset
elif MODE in ["transient-two", "transient-always"]:
    original_compute = engine.compute
    attempt = 0

    def transient(message):
        global attempt
        if message["operation_id"] == OP:
            attempt += 1
            mark("compute_attempt", attempt=attempt, generation=message["generation"])
            if MODE == "transient-always" or attempt <= 2:
                raise RuntimeError("QA controlled transient dependency failure")
        return original_compute(message)

    engine.compute = transient
elif MODE == "hold-every-generation":
    original_compute = engine.compute

    def hold_generation(message):
        if message["operation_id"] == OP:
            release = MARKERS.with_suffix(".generation-" + str(message["generation"]) + ".continue")
            mark("holding_generation", message=message, generation=message["generation"])
            while not release.exists():
                time.sleep(0.1)
        return original_compute(message)

    engine.compute = hold_generation
elif MODE == "hold-compute":
    original_compute = engine.compute

    def hold(message):
        if message["operation_id"] == OP and not MARKERS.with_suffix(".continue").exists():
            mark("holding_compute", message=message)
            while not MARKERS.with_suffix(".continue").exists():
                time.sleep(0.1)
            if os.environ.get("QA_RELEASE_TRANSIENT") == "1":
                raise RuntimeError("QA released transient failure")
        return original_compute(message)

    engine.compute = hold
elif MODE == "after-acquire-exit":
    original_acquire = engine.acquire

    def exit_after_acquire(message):
        lease = original_acquire(message)
        if message["operation_id"] == OP and lease:
            mark("acquired_before_compute", lease=lease, message=message)
            os._exit(74)
        return lease

    engine.acquire = exit_after_acquire
else:
    raise SystemExit("unknown QA fault mode")

for sig in [signal.SIGTERM, signal.SIGINT]:
    signal.signal(sig, lambda *_: engine.STOP.set())
engine.consume()
