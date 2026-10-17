"""Trusted fixed local parser process; inherited configuration and secrets excluded."""
import json
from pathlib import Path
import sys

sys.path.insert(0,sys.argv[1])
from packages.parsing.core import LocalParser

parser=LocalParser(assets=Path('/workspace/tender-workbench/.local/parsing-models'))
result=parser.parse(sys.stdin.buffer.read()) if sys.argv[2]=='parse' else dict(parser_digest=parser.parser_digest,config_digest=parser.config_digest)
sys.stdout.write(json.dumps(result,ensure_ascii=False))
