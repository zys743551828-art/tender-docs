"""从协议主Schema同步OpenAPI组件；不改动路径或操作定义。"""
import argparse
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent


def convert(value):
    if isinstance(value,dict):
        return {k:(v.replace('#/$defs/','#/components/schemas/') if k=='$ref' else convert(v)) for k,v in value.items()}
    if isinstance(value,list):return [convert(x) for x in value]
    return value


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true');args=parser.parse_args()
    schema=json.loads((HERE/'protocol.schema.json').read_text());api=json.loads((HERE/'openapi.json').read_text())
    generated=convert(schema['$defs'])
    if args.check:
        if api['components']['schemas']!=generated:raise SystemExit('OpenAPI组件与协议Schema不一致')
    else:
        api['components']['schemas']=generated
        (HERE/'openapi.json').write_text(json.dumps(api,ensure_ascii=False,indent=2)+'\n')
