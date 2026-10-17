"""架构师临时PG夹具；只在新库初始化owner，业务连接为独立非owner。"""
from contextlib import contextmanager
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import psycopg
from psycopg.rows import dict_row

APP=Path('/workspace/tender-workbench')
IMAGE='pgvector/pgvector@sha256:ac08538c6f8b9904c33c8224c5e5706dbe760aca29db1d096972b4052c22a75d'


@contextmanager
def database(*, initializer_fidelity=False):
    environment=dict(os.environ)
    for name in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):
        environment.pop(name,None)
    container='tender-rag-delta-'+uuid4().hex[:10]
    def docker(arguments,text=None):
        result=subprocess.run(['docker','--host=unix:///var/run/docker.sock',*arguments],
            input=text,text=True,capture_output=True,timeout=90,env=environment)
        if result.returncode:
            raise RuntimeError(result.stderr or result.stdout)
        return result.stdout.strip()
    def owner_sql(statement):
        return docker(['exec','-i',container,'psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1',
            '-h','127.0.0.1','-U','postgres'],statement)
    docker(['run','-d','--name',container,'--memory','1g','--tmpfs','/var/lib/postgresql/data:rw',
        '-p','127.0.0.1::5432','-e','POSTGRES_HOST_AUTH_METHOD=trust',IMAGE])
    try:
        for _ in range(100):
            try:
                docker(['exec',container,'pg_isready','-h','127.0.0.1','-U','postgres']);break
            except RuntimeError:
                time.sleep(.2)
        else:
            raise RuntimeError('PG did not start')
        names=('core.sql','domain.sql','retrieval.sql','worker-functions.sql','roles.sql')
        if initializer_fidelity:
            # 实际initialize_database在同一连接执行，SET search_path跨文件保持。
            owner_sql('\n'.join((APP/'packages/contracts/sql'/name).read_text() for name in names))
        else:
            for name in names:
                owner_sql((APP/'packages/contracts/sql'/name).read_text())
        owner_sql('CREATE ROLE architect_rag_api LOGIN INHERIT NOSUPERUSER; GRANT tender_api TO architect_rag_api; ALTER ROLE architect_rag_api SET search_path TO design,public; CREATE ROLE architect_rag_worker LOGIN INHERIT NOSUPERUSER; GRANT tender_worker TO architect_rag_worker; ALTER ROLE architect_rag_worker SET search_path TO design,public;')
        port=docker(['port',container,'5432/tcp']).rsplit(':',1)[1]
        def connect(role='worker',**kwargs):
            return psycopg.connect(f'postgresql://architect_rag_{role}@127.0.0.1:{port}/postgres',
                row_factory=dict_row,**kwargs)
        connect.owner_sql=owner_sql
        connect.container_name=container
        yield connect
    finally:
        docker(['rm','-f','-v',container])
