"""实际启动并销毁专用Compose开发栈，执行文档指定建库及topic初始化。"""
import argparse
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import uuid

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
DESIGN=ROOT/'05-技术与设计/detailed-design'


def run():
    project='tender-implementation-'+uuid.uuid4().hex[:8]
    env=dict(os.environ,POSTGRES_PASSWORD=secrets.token_urlsafe(24))
    base=['docker','compose','-p',project,'-f',str(DESIGN/'deploy/compose.yaml')]
    checks=[]
    def command(args,stdin=None,timeout=90):
        p=subprocess.run(args,input=stdin,text=True,capture_output=True,env=env,timeout=timeout)
        if p.returncode:raise RuntimeError(p.stderr.strip() or p.stdout.strip())
        return p.stdout.strip()
    def sql(statement):return command(base+['exec','-T','postgres','psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1','-U','tender_owner','-d','tender'],statement)
    try:
        command(base+['config','--quiet']);checks.append('Compose配置合法')
        command(base+['up','-d','--wait','--wait-timeout','60'],timeout=80);checks.append('PG与Kafka按开发编排真实启动并healthy')
        for name in ['core.sql','domain.sql','retrieval.sql','worker-functions.sql','roles.sql']:
            sql((DESIGN/name).read_text())
        tables=int(sql("SELECT count(*) FROM information_schema.tables WHERE table_schema IN ('design','retrieval') AND table_type='BASE TABLE';"))
        if tables!=87:raise AssertionError('unexpected table count')
        checks.append('文档指定顺序以tender_owner完成87表初始化')
        command([str(Path(__import__('sys').executable)),str(DESIGN/'deploy/init_topics.py'),'--bootstrap','127.0.0.1:19092']);checks.append('宿主通过EXTERNAL listener初始化6类topic')
        command(base+['exec','-T','kafka','/opt/kafka/bin/kafka-topics.sh','--bootstrap-server','kafka:9092','--list']);checks.append('容器内通过INTERNAL listener可查询topic')
        inputs=[DESIGN/'deploy/compose.yaml',DESIGN/'deploy/init_topics.py']+[DESIGN/n for n in ['core.sql','domain.sql','retrieval.sql','worker-functions.sql','roles.sql']]+[Path(__file__)]
        return {'passed':True,'scope':'真实开发Compose基础设施、初始化及内外listener；不包含业务服务镜像或生产认证／容灾','executed_at':datetime.now(timezone.utc).isoformat(),'checks':checks,'table_count':tables,'inputs':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},'business_services_started':False}
    except Exception as exc:
        try:
            diagnostics=command(base+['logs','--no-color','--tail','35','kafka'])
        except Exception:
            diagnostics='Kafka日志不可用'
        raise RuntimeError(str(exc)+'\nKafka diagnostics:\n'+diagnostics) from exc
    finally:
        command(base+['down','--volumes','--remove-orphans'],timeout=60)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=run();args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'passed':True,'checks':len(result['checks']),'tables':result['table_count']},ensure_ascii=False))
