"""同连接真实初始化语义+新增精确授权下复核原三个PG整改组。"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import shutil

import isolated_pg
import probe_remediation_delta as probe

HERE=Path(__file__).resolve().parent
GRANT=isolated_pg.APP/'deploy/sql/vector-runtime-grants.sql'


@contextmanager
def exact_database():
    with isolated_pg.database(initializer_fidelity=True) as connect:
        connect.owner_sql(GRANT.read_text())
        yield connect


if __name__=='__main__':
    output=HERE/'initializer-fidelity-run'
    output.mkdir(exist_ok=True)
    shutil.copyfile(HERE/'delta-inputs.json',output/'delta-inputs.json')
    probe.HERE=output
    probe.database=exact_database
    probe.main()
    result=json.loads((output/'delta-pg-result.json').read_text())
    result['initialization_semantics']='same owner connection for all five reviewed SQL files; vector extension in design; exact runtime grant after role revocation'
    result['runtime_grant_sha256']=hashlib.sha256(GRANT.read_bytes()).hexdigest()
    result['source_sql_sha256']={name:hashlib.sha256((isolated_pg.APP/'packages/contracts/sql'/name).read_bytes()).hexdigest() for name in ('core.sql','domain.sql','retrieval.sql','worker-functions.sql','roles.sql')}
    (output/'delta-pg-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
