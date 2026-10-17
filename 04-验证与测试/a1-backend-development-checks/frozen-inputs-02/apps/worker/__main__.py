import argparse
import signal

from apps.worker.engine import STOP, consume, reconcile, relay


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "mode", choices=["relay", "consume", "consume-review", "consume-export", "reconcile"]
    )
    p.add_argument("--once", action="store_true")
    a = p.parse_args()
    for sig in [signal.SIGINT, signal.SIGTERM]:
        signal.signal(sig, lambda *_: STOP.set())
    if a.mode in ("consume", "consume-review", "consume-export"):
        consume(
            {"consume": "document", "consume-review": "review", "consume-export": "export"}[a.mode]
        )
    elif a.mode == "relay":
        relay(a.once)
    else:
        reconcile(a.once)


if __name__ == "__main__":
    main()
