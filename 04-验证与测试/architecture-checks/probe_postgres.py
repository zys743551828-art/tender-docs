"""独立PostgreSQL库验证事务、并发、事件游标和参考用途隔离；不导入旧应用。"""
import argparse
import json
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from pathlib import Path

import psycopg
from psycopg import sql


def run(admin_dsn):
    name='architecture_data_'+uuid.uuid4().hex[:12]
    with psycopg.connect(admin_dsn,autocommit=True) as c:c.execute(sql.SQL('create database {}').format(sql.Identifier(name)))
    dsn=psycopg.conninfo.make_conninfo(admin_dsn,dbname=name)
    cases=[]
    try:
        with psycopg.connect(dsn) as c:
            version=c.execute('show server_version').fetchone()[0]
            c.execute('create table objects(id text primary key,revision int not null)')
            c.execute("insert into objects values('project',1)")
            c.execute('create table commands(id text primary key,request_hash text not null,object_id text references objects(id),expected_revision int,result jsonb)')
            c.execute('create table effects(command_id text primary key references commands(id))')
            c.execute('create table outbox(id text primary key references commands(id),payload jsonb not null)')
            c.execute('create table naive_events(seq bigint generated always as identity,event text)')
            c.execute('create table streams(id text primary key,last_seq bigint not null)')
            c.execute("insert into streams values('conversation',0)")
            c.execute('create table safe_events(stream_id text references streams(id),seq bigint,event text,primary key(stream_id,seq))')
            c.execute('create table knowledge(id text primary key,revision int,status text)')
            c.execute("insert into knowledge values('k1',1,'published')")
            c.execute('create table citations(id text primary key,knowledge_id text references knowledge(id),revision int)')
            c.execute('create table documents(id int primary key,kind text,status text,group_id text)')
            c.execute('create table lineage(child int references documents(id),parent int references documents(id),primary key(child,parent))')
            c.execute("insert into documents values(1,'body','published','train'),(2,'body','published','test'),(3,'official_score','published','test'),(4,'knowledge','pending','train'),(5,'knowledge','published',null),(6,'knowledge','published','train'),(7,'knowledge','disabled','train')")
            c.execute('insert into lineage values(5,2)')
            c.execute('create table results(seq int primary key,status text,value numeric)')
            c.execute("insert into results values(1,'verified',52.1),(2,'conflict',54.1)")
        def command(key,request_hash='hash-a',expected_revision=1,fail=False):
            with psycopg.connect(dsn) as c:
                inserted=c.execute('insert into commands values(%s,%s,%s,%s,null) on conflict do nothing returning id',(key,request_hash,'project',expected_revision)).fetchone()
                if not inserted:
                    row=c.execute('select request_hash,result from commands where id=%s',(key,)).fetchone()
                    if row[0]!=request_hash:raise ValueError('idempotency_conflict')
                    return row[1]
                revision=c.execute("select revision from objects where id='project' for update").fetchone()[0]
                if revision!=expected_revision:raise ValueError('stale_revision')
                c.execute('insert into effects values(%s)',(key,))
                c.execute('insert into outbox values(%s,%s::jsonb)',(key,json.dumps({'command_id':key})))
                c.execute("update objects set revision=revision+1 where id='project'")
                if fail:raise ValueError('injected_failure_before_commit')
                result={'applied':True,'revision':revision+1}
                c.execute('update commands set result=%s::jsonb where id=%s',(json.dumps(result),key))
                return result
        with ThreadPoolExecutor(max_workers=8) as pool:responses=list(pool.map(lambda _:command('same-command'),range(32)))
        with psycopg.connect(dsn) as c:
            totals=[c.execute('select count(*) from '+table).fetchone()[0] for table in ('commands','effects','outbox')]
            revision=c.execute("select revision from objects where id='project'").fetchone()[0]
        assert totals==[1,1,1] and revision==2 and all(r==responses[0] for r in responses)
        cases.append(dict(name='32次并发同键确认',counts=totals,revision=revision,passed=True))
        for key,request_hash,expected,error in [('same-command','different',1,'idempotency_conflict'),('stale-command','new',1,'stale_revision'),('rollback-command','new',2,'injected_failure_before_commit')]:
            try:
                command(key,request_hash,expected,key=='rollback-command');raise AssertionError('expected rejection')
            except ValueError as e:assert str(e)==error
            with psycopg.connect(dsn) as c:
                assert c.execute('select count(*) from commands').fetchone()[0]==1
                assert c.execute('select count(*) from outbox').fetchone()[0]==1
                assert c.execute("select revision from objects where id='project'").fetchone()[0]==2
            cases.append(dict(name=error,passed=True))
        # 负对照：自增ID是分配顺序，不等于提交顺序，游标可能跳过晚提交的小ID。
        first=psycopg.connect(dsn)
        try:
            low=first.execute("insert into naive_events(event) values('late commit') returning seq").fetchone()[0]
            with psycopg.connect(dsn) as second:high=second.execute("insert into naive_events(event) values('early commit') returning seq").fetchone()[0]
            first.commit()
            with psycopg.connect(dsn) as c:missing=c.execute('select seq from naive_events where seq>%s',(high,)).fetchall()
            assert low<high and missing==[]
            cases.append(dict(name='负对照_全局自增游标漏晚提交事件',missed_seq=low,client_cursor=high,failure_reproduced=True))
        finally:first.close()
        # 修正：同stream行锁+事务内递增，使分配与提交顺序对该stream一致。
        first=psycopg.connect(dsn)
        try:
            seq=first.execute("update streams set last_seq=last_seq+1 where id='conversation' returning last_seq").fetchone()[0]
            first.execute("insert into safe_events values('conversation',%s,'first')",(seq,))
            entered=threading.Event()
            def append_second():
                with psycopg.connect(dsn) as c:
                    entered.set()
                    seq=c.execute("update streams set last_seq=last_seq+1 where id='conversation' returning last_seq").fetchone()[0]
                    c.execute("insert into safe_events values('conversation',%s,'second')",(seq,))
                    return seq
            with ThreadPoolExecutor(max_workers=1) as pool:
                future=pool.submit(append_second);assert entered.wait(2)
                try:future.result(timeout=.1);raise AssertionError('stream lock not respected')
                except TimeoutError:pass
                first.commit();assert future.result(timeout=5)==2
            with psycopg.connect(dsn) as c:ordered=c.execute("select seq from safe_events where stream_id='conversation' order by seq").fetchall()
            assert ordered==[(1,),(2,)]
            cases.append(dict(name='同会话串行事件序号',sequences=[r[0] for r in ordered],passed=True))
        finally:first.close()
        # 索引/cache拿到旧知识版本；真实数据库状态决定能否提交新引用。
        with psycopg.connect(dsn) as c:c.execute("update knowledge set status='disabled',revision=2 where id='k1'")
        with psycopg.connect(dsn) as c:
            row=c.execute("select revision,status from knowledge where id='k1' for share").fetchone()
            if row==(1,'published'):c.execute("insert into citations values('new','k1',1)")
            assert c.execute('select count(*) from citations').fetchone()[0]==0
        cases.append(dict(name='旧缓存引用经主库复核被拒绝',passed=True))
        with psycopg.connect(dsn) as c:
            naive=[r[0] for r in c.execute("select id from documents where status='published' and kind in ('body','knowledge') and (group_id is null or group_id<>'test') order by id")]
            safe=[r[0] for r in c.execute("""with recursive ancestors(child,ancestor) as (
                select id,id from documents union select a.child,l.parent from ancestors a join lineage l on l.child=a.ancestor)
                select d.id from documents d where d.status='published' and d.kind in ('body','knowledge') and not exists(
                select 1 from ancestors a join documents source on source.id=a.ancestor where a.child=d.id and source.group_id='test') order by d.id""")]
            assert 5 in naive and safe==[1,6]
            old=c.execute("select value from results where status<>'conflict' order by seq desc limit 1").fetchone()[0]
            strict=c.execute("select value from results where status='verified' and not exists(select 1 from results where status='conflict') order by seq desc limit 1").fetchall()
            assert str(old)=='52.1' and strict==[]
        cases.append(dict(name='负对照_只看直接group遗漏派生知识',naive=naive,corrected=safe,failure_reproduced=True))
        cases.append(dict(name='冲突成绩不回退旧已核验分',naive_old_score=str(old),corrected_count=len(strict),passed=True))
        return dict(scope='全新隔离SQL模型，不导入既有应用；不是正式迁移或完整业务实现',passed=True,postgresql=version,external_model_calls=0,cases=cases)
    finally:
        with psycopg.connect(admin_dsn,autocommit=True) as c:c.execute(sql.SQL('drop database {} with (force)').format(sql.Identifier(name)))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--admin-dsn',default='host=127.0.0.1 port=5432 dbname=postgres user=postgres');parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=run(args.admin_dsn);args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
