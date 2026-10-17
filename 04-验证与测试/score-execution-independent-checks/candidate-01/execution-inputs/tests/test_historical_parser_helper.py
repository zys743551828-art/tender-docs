"""Local parser test subprocess: fixed argv, isolated environment, no inherited secrets."""

if __name__ == "__main__":
    import json
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from packages.parsing.core import LocalParser

    parser = LocalParser()
    value = (
        parser.parse(sys.stdin.buffer.read())
        if sys.argv[1:] == ["parse"]
        else {
            "parser_digest": parser.parser_digest,
            "config_digest": parser.config_digest,
        }
    )
    sys.stdout.write(json.dumps(value, ensure_ascii=False))
