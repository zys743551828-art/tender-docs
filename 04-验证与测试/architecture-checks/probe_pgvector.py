"""真实pgvector负对照：强过滤下HNSW候选不足与迭代扫描；使用合成向量。"""
import argparse
import json
import uuid
from pathlib import Path

import psycopg
from psycopg import sql


def run(admin_dsn):
    name='architecture_vector_'+uuid.uuid4().hex[:12]
    with psycopg.connect(admin_dsn,autocommit=True) as c:c.execute(sql.SQL('create database {}').format(sql.Identifier(name)))
    dsn=psycopg.conninfo.make_conninfo(admin_dsn,dbname=name)
    try:
        with psycopg.connect(dsn) as c:
            c.execute('create extension vector')
            server=c.execute('show server_version').fetchone()[0]
            extension=c.execute("select extversion from pg_extension where extname='vector'").fetchone()[0]
            c.execute('create table candidates(id int primary key,group_tag text,embedding vector(3))')
            c.execute('select setseed(0.42)')
            c.execute("insert into candidates select i,case when i%100=0 then 'eligible' else 'excluded' end,array[random(),random(),random()]::vector from generate_series(1,5000)i")
            c.execute('create index candidates_hnsw on candidates using hnsw(embedding vector_l2_ops)')
            c.execute('analyze candidates')
            query="select id from candidates where group_tag='eligible' order by embedding <-> '[0.5,0.5,0.5]' limit 10"
            c.execute('set local enable_indexscan=off')
            exact=[r[0] for r in c.execute(query)]
            c.execute('set local enable_indexscan=on');c.execute('set local enable_seqscan=off')
            c.execute('set local hnsw.ef_search=20');c.execute("set local hnsw.iterative_scan='off'")
            naive=[r[0] for r in c.execute(query)]
            plan='\n'.join(r[0] for r in c.execute('explain '+query))
            assert 'candidates_hnsw' in plan,plan
            c.execute("set local hnsw.iterative_scan='strict_order'")
            c.execute('set local hnsw.max_scan_tuples=20000')
            iterative=[r[0] for r in c.execute(query)]
            assert len(exact)==10 and len(naive)<10 and len(iterative)==10,(exact,naive,iterative)
            assert all(i%100==0 for i in iterative)
        return dict(scope='5000条合成向量、1%过滤命中；为暴露候选不足而固定查询计划，不是中文语义检索质量或容量测试',passed=True,postgresql=server,pgvector=extension,exact_ids=exact,naive_hnsw_ids=naive,iterative_hnsw_ids=iterative,query_plan=plan,negative_control_reproduced=True,external_model_calls=0)
    finally:
        with psycopg.connect(admin_dsn,autocommit=True) as c:c.execute(sql.SQL('drop database {} with (force)').format(sql.Identifier(name)))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--admin-dsn',default='host=127.0.0.1 port=55437 dbname=postgres user=postgres');p.add_argument('--output',type=Path,required=True);a=p.parse_args();r=run(a.admin_dsn);a.output.write_text(json.dumps(r,ensure_ascii=False,indent=2)+'\n');print(json.dumps(r,ensure_ascii=False))
