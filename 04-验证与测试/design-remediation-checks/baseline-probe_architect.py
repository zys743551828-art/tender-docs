"""Independent synthetic counterexamples; no application or historical results modified."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import time
import uuid

ROOT = Path('/workspace/tender-docs')
DESIGN = Path('/workspace/tender-docs/04-验证与测试/design-remediation-checks/baseline-inputs')
HERE = Path('/workspace/tender-docs/04-验证与测试/design-remediation-checks') / 'baseline-results'
IMAGE = 'pgvector/pgvector@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d'
FILES = ['core.sql', 'domain.sql', 'retrieval.sql', 'worker-functions.sql', 'roles.sql']


def main():
    env = dict(os.environ)
    for key in ['DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH']:
        env.pop(key, None)
    name = 'tender-architect-' + uuid.uuid4().hex[:10]
    inputs = {name: (DESIGN / name).read_bytes() for name in FILES}
    checks = []

    def command(args, text=None, must_succeed=True):
        p = subprocess.run(['docker', '--host=unix:///var/run/docker.sock', *args], input=text, text=True,
                           capture_output=True, timeout=90, env=env)
        if must_succeed and p.returncode:
            raise RuntimeError(p.stderr or p.stdout)
        return p

    def sql(text, must_succeed=True):
        return command(['exec', '-i', name, 'psql', '-X', '-q', '-A', '-t', '-v',
                        'ON_ERROR_STOP=1', '-v', 'VERBOSITY=verbose', '-U', 'postgres'], text, must_succeed)

    def observe(label, statement, expected):
        p = sql(statement, False)
        found = p.returncode != 0 and expected in p.stderr
        checks.append(dict(name=label, reproduced=found, expected=expected,
                           exit_code=p.returncode, stdout=p.stdout.strip(), stderr=p.stderr.strip(), sql=statement))
        if not found:
            raise AssertionError(checks[-1])

    command(['run', '-d', '--name', name, '--network', 'none', '--memory', '1g',
             '--tmpfs', '/var/lib/postgresql/data:rw', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', IMAGE])
    try:
        for _ in range(100):
            p = command(['exec', name, 'pg_isready', '-U', 'postgres'], must_succeed=False)
            if not p.returncode:
                break
            time.sleep(.2)
        else:
            raise RuntimeError('PG not ready')
        for data in inputs.values():
            sql(data.decode())
        sql("INSERT INTO design.members VALUES('00000000-0000-0000-0000-000000000001','合成成员',true);"
            "INSERT INTO design.bids(id,title,settings,created_by) VALUES('00000000-0000-0000-0000-000000000010','合成项目','{}','00000000-0000-0000-0000-000000000001');")
        observe('普通worker不能执行acquire所要求的bid行锁',
                "BEGIN; SET LOCAL ROLE tender_worker; SELECT id FROM design.bids WHERE id='00000000-0000-0000-0000-000000000010' FOR UPDATE; COMMIT;", '42501')
        observe('研究worker也不能执行统一bid行锁',
                "BEGIN; SET LOCAL ROLE tender_research_worker; SELECT id FROM design.bids WHERE id='00000000-0000-0000-0000-000000000010' FOR UPDATE; COMMIT;", '42501')
        observe('研究worker不能写费用预留所需model_calls',
                "BEGIN; SET LOCAL ROLE tender_research_worker; INSERT INTO design.model_calls(id,operation_id,logical_call_key,attempt_no,request_digest,profile_digest,state) SELECT NULL::uuid,NULL::uuid,'fixture',1,repeat('a',64)::design.sha256,repeat('b',64)::design.sha256,'reserved' WHERE false; COMMIT;", '42501')
        observe('研究worker不能读取阶段收据',
                'BEGIN; SET LOCAL ROLE tender_research_worker; SELECT * FROM design.step_receipts LIMIT 0; COMMIT;', '42501')
        observe('普通worker不能反过来代理研究费用预留',
                "BEGIN; SET LOCAL ROLE tender_worker; SELECT design.reserve_research_fee('00000000-0000-0000-0000-000000000099',1); COMMIT;", '42501')
        result = dict(scope='独立架构审查：实际PG角色正向执行反例；不是产品端到端测试',
                      image=IMAGE, postgres_version=sql('SHOW server_version;').stdout.strip(),
                      input_sha256={k: hashlib.sha256(v).hexdigest() for k,v in inputs.items()},
                      counterexamples=checks, software_end_to_end_executed=False)
        (HERE/'role-counterexamples.json').write_text(json.dumps(result, ensure_ascii=False, indent=2)+'\n')
        print(json.dumps(dict(counterexamples=len(checks), all_reproduced=True), ensure_ascii=False))
    finally:
        command(['rm', '-f', '-v', name])


if __name__ == '__main__':
    main()
