"""评审身份的可执行协议参考；不连接业务系统，不代替RequestReview实现。"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json

CONTENT_FIELDS = (
    'bid_id','body_version_id','material_snapshot_id','material_revision','rule_set_id',
    'fact_set_id','parse_run_ids','method_digest','policy_digest','purpose','source_refs',
    'content_checks','as_of','evaluation_context_digest','evidence_manifest_digest',
)

def canonical(value):
    # 本协议子集仅含字符串、整数、布尔、null和容器；无float及非BMP键。
    def check(v):
        if isinstance(v,float): raise ValueError('float_forbidden')
        if isinstance(v,int) and abs(v)>9007199254740991: raise ValueError('unsafe_integer')
        if isinstance(v,dict):
            if any(any(ord(c)>0xffff for c in k) for k in v): raise ValueError('non_bmp_key')
            for x in v.values():check(x)
        elif isinstance(v,list):
            for x in v:check(x)
    check(value)
    return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')

def digest(value):return hashlib.sha256(canonical(value)).hexdigest()

def utc(value):
    return datetime.fromisoformat(value.replace('Z','+00:00')).astimezone(timezone.utc).isoformat(timespec='microseconds').replace('+00:00','Z')

def identities(manifest,rerun_nonce=None):
    m=deepcopy(manifest)
    if set(m)!=set(CONTENT_FIELDS)|{'frozen_at'}:raise ValueError('manifest_fields_mismatch')
    for key in ('as_of','frozen_at'):m[key]=utc(m[key])
    if len(set(m['parse_run_ids']))!=len(m['parse_run_ids']):raise ValueError('duplicate_parse')
    m['parse_run_ids']=sorted(m['parse_run_ids'])
    m['source_refs']=sorted(m['source_refs'],key=lambda x:x['id'])
    if len({x['id'] for x in m['source_refs']})!=len(m['source_refs']):raise ValueError('duplicate_source')
    for source in m['source_refs']:source['known_at']=utc(source['known_at'])
    content={'identity_version':'review-content/1','input':{k:m[k] for k in CONTENT_FIELDS}}
    input_digest=digest(content)
    execution={'identity_version':'review-execution/1','input_digest':input_digest,'rerun_nonce':rerun_nonce}
    return {'manifest_digest':digest(m),'input_digest':input_digest,'execution_digest':digest(execution)}
