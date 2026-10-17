"""真实单节点Kafka与PG收据重放实验；不是产品worker或生产容灾验收。"""
import argparse
from datetime import datetime,timezone
import hashlib
import importlib.util
from importlib.metadata import version
import json
from pathlib import Path
import socket
import subprocess
import time
import uuid
from confluent_kafka import Consumer,Producer,TopicPartition

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
DESIGN=ROOT/'05-技术与设计/detailed-design'
KAFKA_IMAGE='apache/kafka@sha256:5cc2a2fd93fa2687b44015eee04fb2c3edd9e526bd64bf8bec5ff1e268772e0e'


def run():
    suffix=uuid.uuid4().hex[:8];kafka='tender-kafka-proof-'+suffix;pg='tender-kafka-pg-'+suffix
    created=[];consumers=[];checks=[]
    def command(args,stdin=None):
        p=subprocess.run(args,input=stdin,text=True,capture_output=True,timeout=90)
        if p.returncode:raise RuntimeError(p.stderr.strip() or p.stdout.strip())
        return p.stdout.strip()
    def sql(statement):return command(['docker','exec','-i',pg,'psql','-X','-q','-A','-t','-v','ON_ERROR_STOP=1','-U','postgres'],statement)
    def check(name,condition):
        if not condition:raise AssertionError(name)
        checks.append({'name':name,'passed':True})
    def ready(name,kafka_mode=False):
        deadline=time.monotonic()+55
        while time.monotonic()<deadline:
            try:
                if kafka_mode:command(['docker','exec',name,'/opt/kafka/bin/kafka-broker-api-versions.sh','--bootstrap-server','localhost:9092'])
                else:command(['docker','exec',name,'pg_isready','-U','postgres'])
                return
            except RuntimeError:time.sleep(.5)
        raise RuntimeError('container not ready: '+name)
    def uid(n):return str(uuid.UUID(int=n))
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1',0));port=reservation.getsockname()[1]
    try:
        command(['docker','run','-d','--name',pg,'--network','none','--memory','512m','--tmpfs','/var/lib/postgresql/data:rw','-e','POSTGRES_HOST_AUTH_METHOD=trust','postgres:17']);created.append(pg);ready(pg)
        sql((DESIGN/'core.sql').read_text());h='a'*64
        sql(f"INSERT INTO design.members VALUES('{uid(1)}','合成成员',true); INSERT INTO design.bids(id,title,settings,created_by) VALUES('{uid(10)}','合成项目','{{}}','{uid(1)}'); INSERT INTO design.commands VALUES('{uid(50)}','{uid(1)}','review','{uid(10)}','fixture','{h}',null,now()); INSERT INTO design.operations(id,bid_id,command_id,kind,input_digest,state,handler_version) VALUES('{uid(60)}','{uid(10)}','{uid(50)}','review','{h}','running','handler-1');")
        env={'CLUSTER_ID':'MkU3OEVBNTcwNTJENDM2Qk','KAFKA_NODE_ID':'1','KAFKA_PROCESS_ROLES':'broker,controller','KAFKA_LISTENERS':'INTERNAL://:9092,EXTERNAL://:9094,CONTROLLER://:9093','KAFKA_ADVERTISED_LISTENERS':f'INTERNAL://127.0.0.1:9092,EXTERNAL://127.0.0.1:{port}','KAFKA_LISTENER_SECURITY_PROTOCOL_MAP':'INTERNAL:PLAINTEXT,EXTERNAL:PLAINTEXT,CONTROLLER:PLAINTEXT','KAFKA_INTER_BROKER_LISTENER_NAME':'INTERNAL','KAFKA_CONTROLLER_LISTENER_NAMES':'CONTROLLER','KAFKA_CONTROLLER_QUORUM_VOTERS':'1@localhost:9093','KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR':'1','KAFKA_TRANSACTION_STATE_LOG_REPLICATION_FACTOR':'1','KAFKA_TRANSACTION_STATE_LOG_MIN_ISR':'1','KAFKA_AUTO_CREATE_TOPICS_ENABLE':'false','KAFKA_HEAP_OPTS':'-Xms512m -Xmx512m'}
        args=['docker','run','-d','--name',kafka,'--memory','1536m','-p',f'127.0.0.1:{port}:9094']
        for k,v in env.items():args.extend(['-e',k+'='+v])
        command(args+[KAFKA_IMAGE]);created.append(kafka);ready(kafka,True)
        bootstrap=f'127.0.0.1:{port}'
        spec=importlib.util.spec_from_file_location('design_init_topics',DESIGN/'deploy/init_topics.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        first=module.run(bootstrap,6,1,1);second=module.run(bootstrap,6,1,1)
        check('六类任务／领域／死信topic真实创建并核对配置',first['topics_verified']==6 and len(first['topics_created'])==6)
        check('topic初始化可重跑且检查已有配置',second['topics_verified']==6 and second['topics_created']==[])
        topic='tender.task.review.v1';group='proof-'+suffix
        envelope={'schema_version':1,'event_id':uid(100),'event_type':'step.requested','bid_id':uid(10),'operation_id':uid(60),'step_key':'publish','input_digest':h,'generation':1,'handler_version':'handler-1','payload_ref':uid(20),'created_at':datetime.now(timezone.utc).isoformat()}
        from jsonschema import Draft202012Validator,FormatChecker
        shapes=json.loads((DESIGN/'protocol.schema.json').read_text())
        Draft202012Validator({'$ref':'#/$defs/TaskEnvelope','$defs':shapes['$defs']},format_checker=FormatChecker()).validate(envelope)
        payload=json.dumps(envelope)
        producer=Producer({'bootstrap.servers':bootstrap,'enable.idempotence':True,'acks':'all','log_level':0})
        deliveries=[]
        for _ in range(2):producer.produce(topic,payload,key=uid(60)+':publish',partition=0,on_delivery=lambda err,msg:deliveries.append(err))
        remaining=producer.flush(20)
        check('幂等Producer收到两份重复事件的真实delivery回执',remaining==0 and len(deliveries)==2 and all(x is None for x in deliveries))
        config={'bootstrap.servers':bootstrap,'group.id':group,'enable.auto.commit':False,'enable.auto.offset.store':False,'auto.offset.reset':'earliest','log_level':0}
        def consumer():
            c=Consumer(config);c.subscribe([topic]);consumers.append(c);return c
        def receive(c,timeout=25):
            deadline=time.monotonic()+timeout
            while time.monotonic()<deadline:
                m=c.poll(.5)
                if m is not None:
                    if m.error():raise RuntimeError(str(m.error()))
                    return m
            raise RuntimeError('message timeout')
        def effect():sql(f"INSERT INTO design.effect_receipts VALUES('{uid(60)}','publish','{{\"synthetic\":true}}',now()) ON CONFLICT DO NOTHING;")
        c1=consumer();m1=receive(c1);check('首次消费从未提交位点读取offset0',m1.offset()==0)
        effect();c1.close();consumers.remove(c1)
        c2=consumer();m2=receive(c2);check('PG已提交但未commit offset，消费者重启重放同一消息',m2.offset()==m1.offset())
        effect();c2.commit(message=m2,asynchronous=False)
        m3=receive(c2);check('下一条重复事件仍有独立Kafka offset',m3.offset()==1)
        effect();c2.commit(message=m3,asynchronous=False)
        check('重复消费只产生一份PG业务效果收据',sql('SELECT count(*) FROM design.effect_receipts;')=='1')
        check('完成后提交下一消费位点2',c2.committed([TopicPartition(topic,0)],timeout=10)[0].offset==2)
        c2.close();consumers.remove(c2)
        command(['docker','restart',kafka]);ready(kafka,True)
        c3=consumer();check('单节点broker重启后group位点仍为2',c3.committed([TopicPartition(topic,0)],timeout=15)[0].offset==2)
        check('重启后PG正式收据仍只有一份',sql('SELECT count(*) FROM design.effect_receipts;')=='1')
        return {'passed':True,'scope':'真实单节点Kafka及PG效果收据，验证topic初始化、delivery、未提交offset重放和broker重启；非产品worker及多节点容灾','executed_at':datetime.now(timezone.utc).isoformat(),'kafka_image':KAFKA_IMAGE,'python_client':version('confluent-kafka'),'checks':checks,'inputs':{str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [DESIGN/'core.sql',DESIGN/'execution-policy.json',DESIGN/'protocol.schema.json',DESIGN/'deploy/init_topics.py',Path(__file__)]},'real_models_executed':False,'product_end_to_end_executed':False,'multi_broker_failure_executed':False}
    finally:
        for c in consumers:
            try:c.close()
            except Exception:pass
        for name in reversed(created):command(['docker','rm','-f','-v',name])


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);args=parser.parse_args()
    result=run();args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n');print(json.dumps({'passed':True,'checks':len(result['checks']),'client':result['python_client']},ensure_ascii=False))
