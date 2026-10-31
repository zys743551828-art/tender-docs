import json,os,sys,signal,re
from pathlib import Path
os.environ['WORKBENCH_ENV_FILE']='explicit-normal-word-client-no-ambient-env'
os.environ['PYTHONDONTWRITEBYTECODE']='1'
sys.path.insert(0,'/workspace')
packet=json.load(sys.stdin)
from deploy import fusion_word_runtime
base_profile=fusion_word_runtime._private_json(Path('/runtime/profile.json'))
installation=fusion_word_runtime._private_json(Path('/runtime/word-profile.json'))
allowed=fusion_word_runtime.environment(base_profile,'export',installation)
os.environ.clear();os.environ.update(allowed);os.environ['PYTHONDONTWRITEBYTECODE']='1'
assert os.geteuid()==65532 and os.environ.get('WORD_RENDER_EXECUTION_DIRECTORY')=='/tmp/word-execution'
def terminate(*_):raise KeyboardInterrupt
signal.signal(signal.SIGTERM,terminate)

try:
 
 import base64,hashlib,json,os,sys
 from pathlib import Path
 from packages.authoring import generated_docx,prebuilt_dispatch
 from packages.word_intake import execution_transport
 lane=packet['lane']
 assert lane in {'page','rich'} and os.geteuid()==65532
 fixture=packet['fixture']
 assert fixture['profile']==packet['public_profile']==installation['bundle']['profiles'][lane]
 selected={
  'prebuilt_dispatch.py':{'generated','rich'},
  'generated_docx.py':{'assemble','producer_manifest'},
  'rich_docx.py':{'assemble'},
  'generated_verification.py':{'verify','verify_container'},
  'rich_verification.py':{'verify','verify_container','manifest'},
  'prebuilt_generated.py':{'prepare','render','admit','validate_profile'},
  'prebuilt_rich.py':{'prepare','render','render_once','admit','validate_profile','converge_navigation'},
  'prebuilt.py':{'_render_generated','_font_gaps_from_facts','_conversion_facts','validate_profile','inspect_pdf','convert_in_isolation'},
  'execution_transport.py':{'call_conversion','deadline_budget'},
  'generated_renderer.py':{'inspect_pdf'},
  'prebuilt_pdf.py':{'inspect_pdf','inspect_leaders'},
  'rich_table_pdf.py':{'inspect_tables'},
  'rich_caption_pdf.py':{'inspect_captions'},
  'rich_pdf.py':{'inspect_images','inspect_image_sections'},
  'rich_layout.py':{'inspect_layout'},
  'page_navigation.py':{'inspect_navigation','converge'},
 }
 locations={};conversion_inputs=[]
 def observer(frame,event,arg):
  if event!='call':return
  code=frame.f_code;file=code.co_filename;name=code.co_name
  if not file.startswith('/workspace/packages/') or name not in selected.get(file.rsplit('/',1)[-1],set()):return
  key=(file,code.co_qualname,code.co_firstlineno)
  if key not in locations:locations[key]={'filename':file,'function':name,'qualname':code.co_qualname,'firstlineno':code.co_firstlineno,'pid':os.getpid(),'uid':os.geteuid(),'count':0}
  locations[key]['count']+=1
  if name=='call_conversion':
   conversion_inputs.append({'source_docx_digest':hashlib.sha256(frame.f_locals['source']).hexdigest(),'requested_fonts':list(frame.f_locals['requested_fonts']),'pid':os.getpid(),'uid':os.geteuid()})
 def binary(value):
  return {'base64':base64.b64encode(value).decode('ascii'),'byte_size':len(value),'sha256':hashlib.sha256(value).hexdigest()}
 def pack(value):
  value=dict(value)
  for key in ['pdf','docx']:
   if isinstance(value.get(key),bytes):value[key]=binary(value[key])
  if 'render_iterations' in value:
   value['render_iterations']=[item|{'pdf_bytes':binary(item['pdf_bytes']),'docx_bytes':binary(item['docx_bytes'])} for item in value['render_iterations']]
  return value
 sys.setprofile(observer)
 try:
  if lane=='page':
   source=generated_docx.assemble(fixture['snapshot'])
   manifest=generated_docx.producer_manifest(source,fixture['snapshot'])
   result=prebuilt_dispatch.generated(source,fixture['snapshot'],manifest,fixture['profile'])
  else:
   materials=[]
   for item in fixture['materials']:
    item=dict(item);item['bytes']=base64.b64decode(item.pop('bytes_base64'),validate=True);materials.append(item)
   result=prebuilt_dispatch.rich(fixture['snapshot'],materials,fixture['profile'],mode=fixture['mode'])
   source=result['docx']
 finally:sys.setprofile(None)
 for item in locations.values():item['source_sha256']=hashlib.sha256(Path(item['filename']).read_bytes()).hexdigest()
 assert result['resource_receipt']['closed'] is True
 packet={'lane':lane,'result':pack(result),'source_docx':binary(source),'execution_locations':list(locations.values()),'conversion_inputs':conversion_inputs,'runtime':{'python':sys.version,'pid':os.getpid(),'uid':os.geteuid()},'protocol':execution_transport.PROTOCOL}
 packet['installed_export_environment_verified']=True;packet['controlled_format_only']=True;packet['business_authority_claimed']=False
 sys.stdout.buffer.write(json.dumps(packet,ensure_ascii=False,allow_nan=False,separators=(',',':')).encode())
except BaseException as error:
 code=getattr(error,'code',None)
 if not isinstance(code,str) or re.fullmatch('[A-Z][A-Z0-9_]{2,100}',code) is None:code='CONTROLLED_CLIENT_FAILED'
 print(json.dumps({'state':'failed','error_type':type(error).__name__,'error_code':code}))
 raise SystemExit(1)
