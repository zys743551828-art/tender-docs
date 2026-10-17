import json,hashlib
from pathlib import Path
from playwright.sync_api import sync_playwright
from web05_user_helpers import *
f=json.loads(Path('/tmp/web05-user-fixture.json').read_text());events=[];result={}
with sync_playwright() as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(storage_state='/tmp/web05-user-auth-knowledge.json');guarded(c,events);page=c.new_page()
 for k in ['source','target','region_mismatch']:
  page.goto(f[k+'_url'],wait_until='domcontentloaded');page.get_by_role('link',name='项目设置',exact=True).click();page.get_by_text('本项目身份已明确绑定',exact=True).wait_for();result[k]={'company_visible':page.get_by_text(f['company_name'],exact=True).count()>0,'auto_review':page.get_by_role('switch').nth(0).is_checked()};capture(page,'47-final-identity-'+k)
 result['original_sha256']=hashlib.sha256((ROOT/'fixtures/user05-large-three-pages.pdf').read_bytes()).hexdigest();result['download_sha256']=hashlib.sha256((ROOT/'fixtures/downloaded-original.pdf').read_bytes()).hexdigest();result['at']=stamp();result['events']=events;(ROOT/'final-readonly-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));print({k:v for k,v in result.items() if k!='events'});b.close()
