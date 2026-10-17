"""Temporal真实开发服务+独立PostgreSQL库；验证恢复、重试与副作用边界。"""
import asyncio
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from importlib.metadata import version
from pathlib import Path

from temporalio import activity, workflow
with workflow.unsafe.imports_passed_through():
    import psycopg
    from psycopg import sql
    from temporalio.client import Client, WorkflowFailureError
    from temporalio.common import RetryPolicy
    from temporalio.exceptions import ApplicationError, CancelledError
    from temporalio.worker import Worker


@activity.defn
def apply_effect(data: dict) -> dict:
    attempt=activity.info().attempt
    with psycopg.connect(os.environ['ARCH_POC_DB_DSN']) as c:
        current=c.execute('select revision from project where id=1 for update').fetchone()[0]
        if current != data.get('expected_revision',1):
            return {'status':'stale_rejected','attempt':attempt}
        if data.get('guarded',True):
            c.execute('insert into effects values(%s,1) on conflict do nothing',(data['operation'],))
        else:
            c.execute('insert into effects values(%s,1) on conflict(operation) do update set n=effects.n+1',(data['operation'],))
        c.execute('insert into attempts values(%s,%s)',(data['operation'],attempt))
        value=c.execute('select n from effects where operation=%s',(data['operation'],)).fetchone()[0]
    # 故障点在业务事务已提交、Activity尚未确认完成之间。
    if data.get('fail_after_commit') and attempt==1:
        raise ApplicationError('injected failure after DB commit')
    return {'status':'applied','attempt':attempt,'effect_count':value}


@workflow.defn
class ReviewFlow:
    def __init__(self):
        self.decision=None
        self.phase='created'

    @workflow.signal
    async def confirm(self,approved: bool):
        self.decision=approved

    @workflow.query
    def status(self) -> str:
        return self.phase

    @workflow.run
    async def run(self,data: dict) -> dict:
        if data.get('wait'):
            self.phase='awaiting_confirmation'
            await workflow.wait_condition(lambda:self.decision is not None)
            if not self.decision:
                return {'status':'declined'}
        self.phase='executing'
        result=await workflow.execute_activity(apply_effect,data,start_to_close_timeout=timedelta(seconds=10),retry_policy=RetryPolicy(maximum_attempts=2,initial_interval=timedelta(milliseconds=100)))
        self.phase='finished'
        return result


async def run_suite(address: str,admin_dsn: str):
    client=await Client.connect(address)
    database='architecture_probe_'+uuid.uuid4().hex[:12]
    with psycopg.connect(admin_dsn,autocommit=True) as c:
        c.execute(sql.SQL('create database {}').format(sql.Identifier(database)))
    db_dsn=psycopg.conninfo.make_conninfo(admin_dsn,dbname=database)
    os.environ['ARCH_POC_DB_DSN']=db_dsn
    queue='architecture-probe-'+uuid.uuid4().hex[:8]
    outcomes=[]
    try:
        with psycopg.connect(db_dsn) as c:
            c.execute('create table project(id int primary key,revision int not null)')
            c.execute('insert into project values(1,1)')
            c.execute('create table effects(operation text primary key,n int not null)')
            c.execute('create table attempts(operation text,attempt int,primary key(operation,attempt))')
        with ThreadPoolExecutor(max_workers=4) as executor:
            def worker():return Worker(client,task_queue=queue,workflows=[ReviewFlow],activities=[apply_effect],activity_executor=executor)
            async with worker():
                for guarded in (False,True):
                    result=await client.execute_workflow(ReviewFlow.run,{'operation':'retry-'+str(guarded),'guarded':guarded,'fail_after_commit':True},id='probe-'+uuid.uuid4().hex,task_queue=queue)
                    expected=1 if guarded else 2
                    assert result['attempt']==2 and result['effect_count']==expected,result
                    outcomes.append(dict(name='提交后故障重试_'+str(guarded),result=result,expected_business_effects=expected))
                handle=await client.start_workflow(ReviewFlow.run,{'operation':'resume','wait':True},id='probe-'+uuid.uuid4().hex,task_queue=queue)
                for _ in range(50):
                    if await handle.query(ReviewFlow.status)=='awaiting_confirmation':break
                    await asyncio.sleep(.05)
                assert await handle.query(ReviewFlow.status)=='awaiting_confirmation'
            # Worker停机期间信号进入真实服务历史；新的Worker恢复处理。
            await handle.signal(ReviewFlow.confirm,True)
            async with worker():
                result=await handle.result()
                assert result['status']=='applied'
                outcomes.append(dict(name='人工等待后Worker重启恢复',result=result,signal_sent_without_worker=True))
                with psycopg.connect(db_dsn) as c:c.execute('update project set revision=2 where id=1')
                result=await client.execute_workflow(ReviewFlow.run,{'operation':'stale','expected_revision':1},id='probe-'+uuid.uuid4().hex,task_queue=queue)
                assert result['status']=='stale_rejected'
                outcomes.append(dict(name='恢复后重新检查对象版本',result=result))
                cancel=await client.start_workflow(ReviewFlow.run,{'operation':'cancel','wait':True},id='probe-'+uuid.uuid4().hex,task_queue=queue)
                await cancel.cancel()
                try:
                    await cancel.result()
                    raise AssertionError('cancelled workflow unexpectedly succeeded')
                except WorkflowFailureError as error:
                    assert isinstance(error.cause,CancelledError),error
                with psycopg.connect(db_dsn) as c:
                    count=c.execute("select count(*) from effects where operation in ('cancel','stale')").fetchone()[0]
                assert count==0
                outcomes.append(dict(name='等待中取消不产生业务写入',effects=count))
        return dict(scope='Temporal CLI开发服务及隔离PostgreSQL机制实测；不代表生产Temporal部署或整套产品验收',passed=True,sdk_version=version('temporalio'),external_model_calls=0,cases=outcomes)
    finally:
        with psycopg.connect(admin_dsn,autocommit=True) as c:
            c.execute(sql.SQL('drop database {} with (force)').format(sql.Identifier(database)))
        os.environ.pop('ARCH_POC_DB_DSN',None)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--address',default='127.0.0.1:17233');parser.add_argument('--admin-dsn',default='host=127.0.0.1 port=5432 dbname=postgres user=postgres');parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=asyncio.run(run_suite(args.address,args.admin_dsn));args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
