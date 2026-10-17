import argparse
import signal

from apps.worker.engine import STOP, consume, reconcile, relay


def main():
    p = argparse.ArgumentParser()
    p.add_argument("mode", choices=["relay", "consume", "reconcile"])
    p.add_argument("--once", action="store_true")
    a = p.parse_args()
    for sig in [signal.SIGINT, signal.SIGTERM]:
        signal.signal(sig, lambda *_: STOP.set())
    if a.mode == "consume":
        consume()
    elif a.mode == "relay":
        relay(a.once)
    else:
        reconcile(a.once)


if __name__ == "__main__":
    main()
