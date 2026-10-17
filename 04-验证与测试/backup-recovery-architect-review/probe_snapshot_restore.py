"""独立真实PG备份恢复与权限；对象端口为显式内存夹具，绝不连接云桶。"""
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch
from uuid import uuid4

import psycopg
from psycopg.conninfo import make_conninfo

HERE=Path(__file__).resolve().parent
APP=Path('/workspace/tender-workbench')
sys.path.insert(0,str(APP))
sys.path.insert(0,str(HERE.parent/'rag-architect-review'))
from isolated_pg import database

spec=importlib.util.spec_from_file_location('architect_backup_observed',HERE/'observed-inputs/deploy/backups/run.py')
backup=importlib.util.module_from_spec(spec)
spec.loader.exec_module(backup)


def main():
    original=Path.read_text
    def no_private(path,*args,**kwargs):
        return '' if path.resolve()==APP/'.env' else original(path,*args,**kwargs)
    with patch.object(Path,'read_text',no_private):
        from packages.domain import storage
    with database(initializer_fidelity=True) as connect:
        with connect('api') as c:api_dsn=c.info.dsn
        owner_dsn=make_conninfo(api_dsn,user='postgres')
        objects={};operations=[]
        class PrefixPort:
            def __init__(self):self.prefix=os.environ.get('OBJECT_STORE_PREFIX','')
            def get_object(self,*,Bucket,Key):
                operations.append(('get',self.prefix+Key))
                data=objects[self.prefix+Key]
                return {'Body':io.BytesIO(data),'ContentLength':len(data)}
            def put_object(self,*,Bucket,Key,Body,ContentType):
                full=self.prefix+Key;operations.append(('put',full))
                assert full not in objects
                objects[full]=Body
                return {'ETag':backup.digest(Body)}
        prefix='explicit-synthetic-environment/'
        payload=b'explicit synthetic backup object, never a real private document'
        key='blobs/'+backup.digest(payload);objects[prefix+key]=payload
        actor,bid,blob,version,marker=[uuid4() for _ in range(5)]
        with connect('api') as c:
            c.execute("INSERT INTO design.members VALUES(%s,'备份合成成员',true)",(actor,))
            c.execute("INSERT INTO design.bids(id,title,settings,created_by) VALUES(%s,'备份合成工程','{}',%s)",(bid,actor))
            c.execute("INSERT INTO design.blobs(id,digest,object_key,byte_size,media_type) VALUES(%s,%s,%s,%s,'application/pdf')",(blob,backup.digest(payload),key,len(payload)))
            c.execute("INSERT INTO design.document_versions(id,bid_id,blob_id,original_name,obtained_at,artifact_kind,created_by) VALUES(%s,%s,%s,'synthetic.pdf',now(),'source_original',%s)",(version,bid,blob,actor))
            source_write_before=c.execute("SELECT has_table_privilege(current_user,'design.members','INSERT')").fetchone()['has_table_privilege']
        environment={'PATH':os.environ['PATH'],'DOCKER_HOST':'unix:///var/run/docker.sock',
            'API_DATABASE_DSN':api_dsn,'OWNER_DATABASE_DSN':owner_dsn,
            'OBJECT_STORE_BACKEND':'oss','OBJECT_STORE_BUCKET':'explicit-fixture-only',
            'OBJECT_STORE_ENDPOINT':'https://synthetic.invalid','OBJECT_STORE_PREFIX':prefix,
            'OBJECT_STORE_REGION':'synthetic-only'}
        def concurrent_commit():
            with connect('api') as c:c.execute("INSERT INTO design.members VALUES(%s,'备份快照后合成提交',true)",(marker,))
        with tempfile.TemporaryDirectory(prefix='architect-backup-') as temp,patch.dict(os.environ,environment,clear=True),patch.object(storage,'client',PrefixPort):
            root=Path(temp)/'backups';root.mkdir(mode=0o700)
            with patch.object(backup,'ARCHIVE_ROOT',root):
                archive=root/'synthetic'
                manifest=backup.create(environment,archive,connect.container_name,concurrent_commit)
                receipt=backup.restore_verify(environment,archive,connect.container_name)
                assert manifest['tables']['design.members']['rows']==1
                assert receipt['objects_verified']==1 and receipt['tables_verified']==104
                with connect('api') as source:
                    assert source.execute('SELECT count(*) AS n FROM design.members WHERE id=%s',(marker,)).fetchone()['n']==1
                    source_write_after=source.execute("SELECT has_table_privilege(current_user,'design.members','INSERT')").fetchone()['has_table_privilege']
                with psycopg.connect(make_conninfo(api_dsn,dbname=receipt['database'])) as restored:
                    assert restored.execute('SELECT count(*) FROM design.members WHERE id=%s',(marker,)).fetchone()[0]==0
                    readonly=restored.execute('SHOW transaction_read_only').fetchone()[0]
                assert source_write_before and source_write_after and readonly=='on'
                assert receipt['api_write_probe_denied_with_read_only_setting_disabled']
                assert objects[prefix+key]==payload
                put_keys=[object_key for method,object_key in operations if method=='put']
                assert put_keys==[receipt['restored_object_prefix']+key]
                result={'scope':'independent real pg_dump/restore and nonowner privilege/snapshot mechanism; explicit in-memory object port, no actual OSS validation','candidate_sha256':json.loads((HERE/'initial-inputs.json').read_text())['candidate_sha256'],'source_snapshot_export_login_nonowner':True,'snapshot_members':1,'post_snapshot_committed_marker_visible_at_source':True,'post_snapshot_marker_absent_from_actual_restore':True,'restored_tables_compared':104,'restored_objects_compared_with_db_references':1,'source_insert_privilege_before':source_write_before,'source_insert_privilege_after':source_write_after,'restore_transaction_read_only':readonly,'restore_insert_denied_when_default_read_only_disabled':True,'objects_written_only_to_new_drill_prefix':True,'source_object_fixture_unchanged':True,'api_relay_consumer_started':False,'real_object_requests':0,'paid_calls':0,'credentials_read':False,'production_rpo_rto_qualified':False}
        (HERE/'snapshot-restore-mechanism-result.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps({'snapshot_restore_passed':True,'source_privileges_unchanged':True,'restored_tables':104,'real_oss_requests':0}))


if __name__=='__main__':main()
