"""复用独立PG夹具，增加排除组资格变化反例；不访问共享配置。"""
import hashlib
import json
from pathlib import Path
import shutil
import sys
from dataclasses import replace
from uuid import uuid4

import psycopg

import probe_draft_boundaries as fixture

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(fixture.APP))
sys.path.insert(0, str(fixture.SNAPSHOT))
from packages.rag import repository

original = repository.assert_current_sources


def guard_probe(connection, result):
    group = uuid4()
    result['scope'] = replace(result['scope'], excluded_groups=(group,))
    before = repository.source_manifest(connection, result['scope'])
    with psycopg.connect(connection.info.dsn) as concurrent:
        concurrent.execute('SELECT pg_advisory_xact_lock(90127,1)')
        concurrent.execute("INSERT INTO retrieval.source_groups VALUES(%s,'explicit excluded fixture')",(group,))
        concurrent.execute('INSERT INTO retrieval.source_group_memberships VALUES(%s,%s)',
                           (result['scope'].source_ids[0],group))
    original(connection,result)
    after = repository.source_manifest(connection,result['scope'])
    count = connection.execute('SELECT count(*) AS n FROM retrieval.eligible_chunks(%s,%s,%s,false)',
        (list(result['scope'].source_ids),result['scope'].as_of,list(result['scope'].excluded_groups))).fetchone()['n']
    assert before == after and count == 0
    output = {
        'scope':'excluded membership qualification mechanism; nonowner isolated real PG',
        'repository_sha256':hashlib.sha256(Path(repository.__file__).read_bytes()).hexdigest(),
        'source_manifest_unchanged':True,'apply_guard_passed':True,
        'eligible_chunks_after_group_exclusion':count,
        'group_change_used_graph_lock':True,'paid_calls':0,'credentials_read':False,
    }
    (HERE/'draft-group-qualification-counterexample.json').write_text(json.dumps(output,indent=2)+'\n')
    print(json.dumps(output))
    result['scope'] = replace(result['scope'],excluded_groups=())


if __name__ == '__main__':
    repository.assert_current_sources = guard_probe
    # 保存本次扩展运行结果到独立子目录，不覆盖首次三项反例证据。
    folder = HERE/'group-fixture-run'
    folder.mkdir(exist_ok=True)
    shutil.copyfile(HERE/'draft-inputs.json',folder/'draft-inputs.json')
    fixture.HERE = folder
    fixture.main()
