import json,hashlib,re
from pathlib import Path
from playwright.sync_api import sync_playwright
from pricingf05_user_helpers import *
f=json.load(open('/tmp/pricingf05-user-fixture.json'));r=ROOT;wb=Path('/workspace/tender-workbench/apps/web/qa-previews/pricing-method-08');be=Path('/workspace/tender-docs/04-验证与测试/pricing-development-checks/candidate-05');wm=json.load(open(wb/'frontend-candidate.json'));bm=json.load(open(be/'pricing-candidate-05.json'));out={'frontend_manifest_sha256':hashlib.sha256((wb/'frontend-candidate.json').read_bytes()).hexdigest(),'backend_manifest_sha256':hashlib.sha256((be/'pricing-candidate-05.json').read_bytes()).hexdigest(),'frontend_sources':len(wm['source_files']),'frontend_source_mismatches':[],'frontend_assets':len(wm['compiled_assets']),'frontend_asset_mismatches':[],'backend_sources':len(bm['sources']),'backend_source_mismatches':[]}
for x in wm['source_files']:
 if hashlib.sha256((wb/'frozen-inputs'/x['path']).read_bytes()).hexdigest()!=x['sha256']:out['frontend_source_mismatches'].append(x['path'])
for x in wm['compiled_assets']:
 if hashlib.sha256((wb/'frozen-dist'/x['path']).read_bytes()).hexdigest()!=x['sha256']:out['frontend_asset_mismatches'].append(x['path'])
for x in bm['sources']:
 if hashlib.sha256((be/'runtime'/x['path']).read_bytes()).hexdigest()!=x['sha256']:out['backend_source_mismatches'].append(x['path'])
with sync_playwright()as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':390,'height':844},storage_state='/tmp/pricingf05-user-auth.json');events=[];guarded(c,events);page=c.new_page();page.goto(f['own_url'],wait_until='domcontentloaded');page.get_by_role('link',name='项目设置',exact=True).click();page.get_by_role('switch').first.wait_for();out['auto_review_off']=not page.get_by_role('switch').first.is_checked();out['pricing_enabled_at_end']=page.get_by_role('switch').nth(4).is_checked();capture(page,'19-final-settings-restored-no-auto-review');html=c.request.get(BASE+'/').text();paths=re.findall(r'src="(/assets/[^\"]+\.js)"',html);expected={x['path']:x['sha256']for x in wm['compiled_assets']};out['native_served_entry_assets']=[]
 for path in paths:
  res=c.request.get(BASE+path);sha=hashlib.sha256(res.body()).hexdigest();out['native_served_entry_assets'].append({'path':path,'status':res.status,'sha256':sha,'matches':expected.get(path[1:])==sha})
 out['at']=stamp();out['actual_context']='native5178 frontend08 -> currentAPI8808 F05 dedicated workbench08; no asset overlay for pricing';out['api_contract_sha256']=hashlib.sha256((be/'actual-f05.openapi.json').read_bytes()).hexdigest();out['events']=events;b.close()
(r/'binding-verification.json').write_text(json.dumps(out,ensure_ascii=False,indent=2));print({k:v for k,v in out.items()if k not in ['events']})
