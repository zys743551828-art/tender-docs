"""Independent OPS05 one-target orchestration. Prepare is offline; execute needs PM window."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
import shutil
import socket
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

DOC = Path('/workspace/tender-docs/04-验证与测试/application-recovery-independent-checks/code-05-single-target-plan')
DEV = Path('/workspace/tender-docs/04-验证与测试/application-recovery-development-checks')
SOURCE = Path('/workspace/tender-workbench/.local/backups/application/drill04-20261017-c23d8a71')
PROTOCOL = DOC/'单目标独立恢复协议.md'
PROTOCOL_SHA = 'd4ad0221362a05746c6d892ae06149b56a9f804e3c6127f1b2bc48069d9c94a3'
MINIMUM = 1536*1048576
FLOOR = 256*1048576

def sha(p):
    with Path(p).open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()

def read(p):
    return json.loads(Path(p).read_bytes())

def save(p,value):
    fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:
        json.dump(value,f,ensure_ascii=False,indent=2)

def check(value,code):
    if not value:
        raise RuntimeError(code)

def verify_originals():
    check(sha(PROTOCOL)==PROTOCOL_SHA,'PROTOCOL_CHANGED')
    data=read(DOC/'readonly-result.json')
    for item in data['inputs']:
        check(sha(item['path'])==item['sha256'],'ORIGINAL_INPUT_CHANGED')

def prepare():
    verify_originals()
    token=uuid4().hex
    root=Path('/workspace/tender-workbench/.local')/('qa-recovery05-'+token)
    root.mkdir(mode=0o700)
    candidate=read(DEV/'code-05/candidate.json')
    copied=[]
    for item in candidate['owned_sources']+candidate['observed_dependencies']:
        if not item['path'].startswith('deploy/'):
            continue
        target=root/item['path']; target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(Path(item['archive']).read_bytes())
        check(sha(target)==item['sha256'],'EXECUTION_COPY_CHANGED')
        copied.append({'path':str(target),'sha256':item['sha256']})
    (root/'.venv').symlink_to('/workspace/tender-workbench/.venv',target_is_directory=True)
    archive=root/'.local/backups/application'/('single-target-'+token)/'archive'
    archive.mkdir(mode=0o700,parents=True)
    # All ancestors considered by security.private_path must be private.
    for p in [root/'.local',root/'.local/backups',root/'.local/backups/application',archive.parent]:
        p.chmod(0o700)
    files=['manifest.json','manifest.sha256','database.dump']+[o['file'] for o in read(SOURCE/'archive/manifest.json')['objects']]
    for relative in files:
        target=archive/relative; target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        target.write_bytes((SOURCE/'archive'/relative).read_bytes()); target.chmod(0o600)
        check(sha(target)==sha(SOURCE/'archive'/relative),'ARCHIVE_COPY_CHANGED')
        copied.append({'path':str(target),'sha256':sha(target)})
    cfg={'format':'independent-recovery-single-target/1','token':token,'root':str(root),'directory':str(archive.parent),'backend':candidate['backend'],'target_port':55531,'protocol_sha256':PROTOCOL_SHA,'inputs':copied,'driver_sha256':sha(__file__),'created_at':datetime.now(timezone.utc).isoformat(),'state':'prepared_no_docker_or_cloud_calls'}
    save(root/'prepared.json',cfg)
    # Only references/hashes, no credentials, are public.
    save(DOC/('prepared-'+token+'.json'),cfg)
    print(json.dumps({'state':cfg['state'],'prepared_file':str(root/'prepared.json'),'sha256':sha(root/'prepared.json'),'free_MiB':shutil.disk_usage('/workspace').free//1048576}))

def execute(path,approved):
    check(approved==PROTOCOL_SHA,'EXPLICIT_APPROVED_PROTOCOL_ID_REQUIRED')
    verify_originals()
    cfg=read(path); root=Path(cfg['root']); directory=Path(cfg['directory'])
    check(Path(path)==root/'prepared.json' and cfg['driver_sha256']==sha(__file__),'PREPARED_EXECUTION_IDENTITY_CHANGED')
    for item in cfg['inputs']:
        check(sha(item['path'])==item['sha256'],'PREPARED_INPUT_CHANGED')
    check(shutil.disk_usage('/workspace').free>=MINIMUM,'SINGLE_TARGET_1536_MIB_REQUIRED')
    check(not (directory/'execution-intent.json').exists(),'ONE_SHOT_EXECUTION_ALREADY_ATTEMPTED')
    with socket.socket() as probe:
        probe.bind(('127.0.0.1',cfg['target_port']))
    sys.path.insert(0,str(root))
    from deploy.application_recovery import archive,binding,cluster,probes,security as s
    from deploy.application_recovery import application
    import psycopg
    from psycopg import sql
    check(s.ROOT==root,'RECOVERY_ROOT_CHANGED')
    cluster.validate_inputs(DEV/'protocol-02/baseline-inputs.json')
    binding.install(cfg['backend'])
    manifest=archive.validate(directory/'archive')
    values={k:v for k,v in read(SOURCE/'source-objects.json').items() if k in s.OBJECT_ENV}
    values['OBJECT_STORE_PREFIX']=values['OBJECT_STORE_PREFIX'].rstrip('/').rsplit('/',1)[0]+'/independent05-'+cfg['token']+'-target/'
    check(values['OBJECT_STORE_PREFIX'] not in [manifest['object_store']['prefix'],read(SOURCE/'target-objects.json')['OBJECT_STORE_PREFIX']],'OBJECT_PREFIX_NOT_DISTINCT')
    intent={'scenario':'immutable_archive_to_one_fresh_target','nonce':cfg['token'],'backend':cfg['backend'],'target_port':cfg['target_port'],'protocol_sha256':PROTOCOL_SHA,'initial_free_bytes':shutil.disk_usage('/workspace').free,'started_at':datetime.now(timezone.utc).isoformat()}
    save(directory/'execution-intent.json',intent)
    target=None; report={'status':'running','scenario':intent['scenario'],'protocol_sha256':PROTOCOL_SHA,'prepared_sha256':sha(path),'provider_calls':0,'consumers':0,'objects_listed':0,'objects_deleted':0,'stages':[]}
    def stage(name):
        free=shutil.disk_usage('/workspace').free
        check(free>=FLOOR,'DISK_FREE_BELOW_256_MIB')
        report['stages'].append({'name':name,'free_bytes':free,'at':datetime.now(timezone.utc).isoformat()})
    def rows(connection):
        result={}
        for item in manifest['tables']:
            key=(item['schema'],item['name'])
            values=Counter(row[0] for row in connection.execute(sql.SQL('SELECT row_to_json(t)::text FROM {}.{} t').format(sql.Identifier(key[0]),sql.Identifier(key[1]))))
            result[key]=values
        return result
    try:
        stage('before_create')
        target=cluster.create(directory,intent,'target',owner_label=manifest['owner_label'],backup_group=manifest['backup_group'])
        stage('after_create')
        # TOC is dump metadata only. No object-store LIST or source connection.
        with (directory/'archive/database.dump').open('rb') as source, (directory/'dump-toc.txt').open('xb') as toc:
            os.chmod(toc.name,0o600)
            s.pg_tool(target,'pg_restore',['--list'],stdin=source,stdout=toc,role='ddl')
        lines=(directory/'dump-toc.txt').read_text().splitlines()
        check(sum(' TABLE DATA ' in line for line in lines)==164,'DUMP_TOC_TABLE_COUNT_CHANGED')
        stage('before_restore')
        save(directory/'object-put-intent.json',{'locator':archive.safe.locator(values),'objects':[{'id':x['id'],'key':x['key'],'sha256':x['sha256'],'bytes':x['bytes'],'media_type':x['media_type']} for x in manifest['objects']]})
        report['restore']=archive.restore(target,manifest,directory/'archive',values)
        stage('after_restore')
        report['permissions']=probes.verify(target)
        with psycopg.connect(s.connection_dsn(target,'reader')) as c:
            archive.reader_guard(c,target)
            before=rows(c)
            check(len(before)==164,'READER_TABLE_COUNT_CHANGED')
            refs=c.execute('SELECT id::text,digest::text,object_key,byte_size,media_type FROM design.blobs ORDER BY id').fetchall()
            check(refs==[(o['id'],o['sha256'],o['key'],o['bytes'],o['media_type']) for o in manifest['objects']],'TARGET_BLOB_REFERENCE_MISMATCH')
        # Independent explicit GETs after the package restore's own readbacks.
        port=archive.object_port(values)
        for o in manifest['objects']:
            raw=port.get_object(Bucket=values['OBJECT_STORE_BUCKET'],Key=o['key'])['Body'].read()
            check(len(raw)==o['bytes'] and hashlib.sha256(raw).hexdigest()==o['sha256'],'NATIVE_TARGET_BYTES_MISMATCH')
        stage('before_api')
        report['application']=application.check(target,{'backend':cfg['backend']},read(SOURCE/'fixture.json'),read(SOURCE/'member-credential.json'))
        with psycopg.connect(s.connection_dsn(target,'reader')) as c:
            after=rows(c)
        check(all(not(counter-after[k]) for k,counter in before.items()),'ORIGINAL_HISTORY_ROW_CHANGED')
        check(all(before[('design',name)]==after[('design',name)] for name in ['operations','outbox','model_calls']),'OLD_WORK_OR_UNKNOWN_FEE_CHANGED')
        check(sum(after[('design','bids')].values())-sum(before[('design','bids')].values())==1,'NEW_BID_COUNT_NOT_ONE')
        check(all(sha(x['path'])==x['sha256'] for x in cfg['inputs']),'EXECUTION_INPUT_CHANGED')
        verify_originals()
        report['loaded_backend_modules']=binding.require().origins()
        report['independent_postconditions']={'all_original_rows_preserved':True,'queued_outbox_unknown_fees_identical':True,'exactly_one_new_bid':True,'seven_native_objects_exact_sha':True,'old_source_and_developer_archive_files_still_exact':True}
        report['status']='single_fresh_target_recovery_passed_bounded_07_scope'
    except Exception as exc:
        # No raw provider, SQL, response, credential or traceback in public output.
        report['status']='failed_preserved_no_retry'
        report['error_type']=type(exc).__name__
        report['error_code']=str(exc) if isinstance(exc,(s.RecoveryError,RuntimeError)) else 'PRIVATE_DETAILS_SUPPRESSED'
    finally:
        if target is not None:
            try:
                cluster.assert_resources(target)
                s.docker(['stop','--time','30',target['container']],timeout=40)
                cluster.assert_resources(target)
                s.docker(['rm',target['container']])
                report['cleanup']={'own_container_id':target['container_id'],'own_container_removed':True,'volume_retained':target['volume'],'source_resources_touched':False}
            except Exception:
                report['cleanup']={'status':'identity_or_cleanup_failed_manual_inspection_required_no_broad_cleanup'}
                report['status']='failed_cleanup_pending'
        else:
            report['cleanup']={'status':'create_not_returned_inspect_exact_creating_receipt_before_any_cleanup'}
        report['finished_at']=datetime.now(timezone.utc).isoformat()
        report['free_bytes_after']=shutil.disk_usage('/workspace').free
        report['limits']=['Not a rerun of original full source-target-source scenario','No production RPO/RTO, power loss, inflight resume, newest A2/Score/Scale or real business materials qualification','API uses real ASGI with dedicated API LOGIN; no public server, Kafka, parser/Word/RAG execution or model']
        save(directory/'qa-result.json',report)
        save(DOC/('actual-'+cfg['token']+'.json'),report)
        print(json.dumps({'state':report['status'],'result_path':str(directory/'qa-result.json'),'result_sha256':sha(directory/'qa-result.json')}))

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('phase',choices=['prepare','execute'])
    parser.add_argument('--prepared',type=Path)
    parser.add_argument('--approved-protocol-sha256')
    args=parser.parse_args()
    if args.phase=='prepare':
        prepare()
    else:
        check(args.prepared is not None,'PREPARED_FILE_REQUIRED')
        execute(args.prepared,args.approved_protocol_sha256)

if __name__=='__main__':
    main()
