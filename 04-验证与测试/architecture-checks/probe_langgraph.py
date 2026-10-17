"""隔离实验：图中断持久化、恢复重执行与业务幂等；无模型调用。"""
import json
import os
import sqlite3
import tempfile
from importlib.metadata import version
from pathlib import Path
from typing import TypedDict

os.environ['LANGSMITH_TRACING'] = 'false'
os.environ['OTEL_SDK_DISABLED'] = 'true'
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt


class State(TypedDict):
    operation_id: str
    approved: bool


def main():
    outcomes=[]
    with tempfile.TemporaryDirectory(prefix='graph-proof-') as directory:
        base=Path(directory)
        for guarded in (False,True):
            effects=base/f'effects-{guarded}.sqlite'
            with sqlite3.connect(effects) as c:
                c.execute('create table attempts(id integer primary key)')
                c.execute('create table effects(operation_id text primary key,n int not null)')
            def node(state):
                with sqlite3.connect(effects) as c:
                    c.execute('insert into attempts default values')
                    if guarded:
                        c.execute('insert or ignore into effects values(?,1)',(state['operation_id'],))
                    else:
                        c.execute('insert into effects values(?,1) on conflict(operation_id) do update set n=n+1',(state['operation_id'],))
                approved=interrupt({'operation_id':state['operation_id'],'confirmation_required':True})
                return {'approved':bool(approved)}
            def graph(checkpointer):
                g=StateGraph(State);g.add_node('confirm',node);g.add_edge(START,'confirm');g.add_edge('confirm',END)
                return g.compile(checkpointer=checkpointer)
            checkpoint=str(base/f'checkpoints-{guarded}.sqlite')
            config={'configurable':{'thread_id':f'thread-{guarded}'}}
            with SqliteSaver.from_conn_string(checkpoint) as store:
                first=graph(store).invoke({'operation_id':'action-1','approved':False},config)
                assert '__interrupt__' in first
            # 关闭并重开存储、重新编译图，确认不是进程内内存恢复。
            with SqliteSaver.from_conn_string(checkpoint) as store:
                restored=graph(store)
                assert restored.get_state(config).next == ('confirm',)
                result=restored.invoke(Command(resume=True),config)
                assert result['approved'] is True
            with sqlite3.connect(effects) as c:
                attempts=c.execute('select count(*) from attempts').fetchone()[0]
                count=c.execute('select n from effects where operation_id=?',('action-1',)).fetchone()[0]
            assert attempts==2
            assert count==(1 if guarded else 2)
            outcomes.append(dict(guarded=guarded,persistent_resume=True,node_executions=attempts,business_effects=count,observation='中断前逻辑执行两次；幂等约束另行提供'))
    return dict(scope='LangGraph+SQLite隔离机制实验，不是PostgreSQL checkpointer或产品测试',passed=True,versions={p:version(p) for p in ('langgraph','langgraph-checkpoint-sqlite')},external_model_calls=0,cases=outcomes)


if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=main();args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps(result,ensure_ascii=False))
