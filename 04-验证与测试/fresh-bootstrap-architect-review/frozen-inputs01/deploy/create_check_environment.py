"""Create one fresh isolated check DB from ordered, digest-verified repository SQL."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Direct script invocation must import the package from the fixed repository root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from deploy.bootstrap_fresh import BootstrapError, bootstrap  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-init-file", type=Path, required=True)
    parser.add_argument("--name", required=True)
    parser.add_argument(
        "--reviewed-sql", nargs=2, action="append", default=[], metavar=("PATH", "SHA256")
    )
    parser.add_argument("--extension-sql", type=Path)
    parser.add_argument("--extension-sha256")
    parser.add_argument("--model-purposes", action="store_true")
    parser.add_argument("--kafka-bootstrap")
    parser.add_argument("--kafka-port", type=int)
    parser.add_argument("--compose-project")
    parser.add_argument("--app-origin")
    args = parser.parse_args()
    if bool(args.extension_sql) != bool(args.extension_sha256):
        parser.error("extension requires an exact reviewed SQL SHA256")
    extensions = list(args.reviewed_sql)
    if args.extension_sql:
        extensions.append((str(args.extension_sql), args.extension_sha256))
    try:
        result = bootstrap(
            base_profile=args.base_init_file,
            name=args.name,
            extensions=extensions,
            model_purposes=args.model_purposes,
            kafka_bootstrap=args.kafka_bootstrap,
            kafka_port=args.kafka_port,
            compose_project=args.compose_project,
            app_origin=args.app_origin,
        )
        print(json.dumps(result, ensure_ascii=False))
    except Exception as error:
        # psycopg and provider exception text may contain connection credentials or raw SQL.
        print(
            json.dumps(
                {
                    "status": "failed",
                    "code": str(error)
                    if isinstance(error, BootstrapError)
                    else type(error).__name__,
                }
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
