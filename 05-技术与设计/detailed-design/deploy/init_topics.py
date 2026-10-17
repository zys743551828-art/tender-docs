"""按目标执行策略初始化Kafka topic；配置不匹配时失败，不自动改生产集群。"""
import argparse
import json
import time
from pathlib import Path
from confluent_kafka import KafkaException,KafkaError
from confluent_kafka.admin import AdminClient, ConfigResource, NewTopic


def run(bootstrap,partitions,replicas,min_isr):
    if partitions<1 or replicas<1 or min_isr<1 or min_isr>replicas:
        raise ValueError('invalid partition/replica/minISR configuration')
    policy=json.loads((Path(__file__).resolve().parents[1]/'execution-policy.json').read_text())
    names=[x['topic'] for x in policy['topic_bindings'].values()]+[policy['domain_event_topic'],policy['dead_letter_topic']]
    admin=AdminClient({'bootstrap.servers':bootstrap})
    existing=admin.list_topics(timeout=20).topics
    created=[]
    for name in names:
        days=30 if name==policy['dead_letter_topic'] else 14 if name==policy['domain_event_topic'] else 7
        config={'cleanup.policy':'delete','retention.ms':str(days*86400000),'min.insync.replicas':str(min_isr),'max.message.bytes':'262144'}
        if name not in existing:
            admin.create_topics([NewTopic(name,num_partitions=partitions,replication_factor=replicas,config=config)])[name].result(timeout=30)
            created.append(name)
        else:
            meta=existing[name]
            if len(meta.partitions)!=partitions or any(len(x.replicas)!=replicas for x in meta.partitions.values()):
                raise RuntimeError('topic partition or replica mismatch: '+name)
        resource=ConfigResource(ConfigResource.Type.TOPIC,name)
        deadline=time.monotonic()+20
        while True:
            try:
                actual=admin.describe_configs([resource])[resource].result(timeout=10)
                break
            except KafkaException as exc:
                if exc.args[0].code()!=KafkaError.UNKNOWN_TOPIC_OR_PART or time.monotonic()>=deadline:
                    raise
                time.sleep(.2)
        for key,value in config.items():
            if actual[key].value!=value:
                raise RuntimeError('topic config mismatch: '+name+':'+key)
    return {'topics_verified':len(names),'topics_created':created,'scope':'topic元数据核验，不代表生产容灾或业务消费验收'}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bootstrap',default='localhost:19092')
    parser.add_argument('--partitions',type=int,default=6)
    parser.add_argument('--replicas',type=int,default=1)
    parser.add_argument('--min-isr',type=int,default=1)
    args=parser.parse_args()
    print(json.dumps(run(args.bootstrap,args.partitions,args.replicas,args.min_isr),ensure_ascii=False))
