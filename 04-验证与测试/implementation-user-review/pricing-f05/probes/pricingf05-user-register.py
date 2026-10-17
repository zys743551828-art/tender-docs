import json
from playwright.sync_api import sync_playwright
from pricingf05_user_helpers import *
f=json.load(open('/tmp/pricingf05-user-fixture.json'));events=[];result={}
with sync_playwright()as p:
 b=p.chromium.launch(executable_path='/usr/bin/chromium',headless=True,args=['--no-sandbox']);c=b.new_context(viewport={'width':1440,'height':1100},storage_state='/tmp/pricingf05-user-auth.json');guarded(c,events);page=c.new_page();page.set_default_timeout(20000)
 try:
  page.goto(f['pricing_url'],wait_until='domcontentloaded');page.get_by_role('button',name='登记新的有据公式',exact=True).click();page.get_by_label('公式标题',exact=True).fill('用户F05合法最低报价比例-'+f['uuid']);page.get_by_label('原文基准价方法',exact=False).select_option('lowest_valid_ratio');page.get_by_label('价格满分',exact=True).fill('40');page.get_by_label('最少有效报价数（包含本方）',exact=True).fill('3');page.get_by_role('checkbox',name='已核对原文无最低价格边界',exact=True).check();page.get_by_role('checkbox',name='已核对原文无最高价格边界',exact=True).check();page.get_by_label('完整原文金额单位',exact=False).select_option('元');page.get_by_label('原文币种',exact=False).select_option('CNY');page.get_by_label('原文最终取整规则',exact=False).select_option('half_up_2dp');sources=page.get_by_role('checkbox',name='使用此原文出处 · original-ratio.pdf · 物理页 1',exact=True);sources.first.wait_for()
  for i in range(sources.count()):sources.nth(i).check()
  page.get_by_role('checkbox',name='已核对完整条款：本方及所有数值区间内对手假设共同形成基准集合，不去极值、不含随机参数、不作中间取整，最终限制于零至满分',exact=True).check();page.get_by_role('checkbox',name='原文与受限参数完全一致，金额单位有完整声明，币种明确或据实记未知，未使用近似解释',exact=True).check();page.get_by_label('参数对应原文的解释',exact=True).fill('按完整同页原文登记：最低有效报价作基准；满分40；本方及所有有效数值对手至少3项，保留同价；报价大于0无额外上下界；人民币元；基准及计算过程不取整；最终四舍五入两位并限0至40。');page.get_by_role('button',name='确认并登记公式',exact=True).click();page.wait_for_timeout(300);capture(page,'04-explicit-missing-field-guidance');result['missing_body']=page.locator('body').inner_text()
  mappings=[('独立合成计分原页',['价格满分','完整金额单位声明','币种声明']),('有效报价须大于0',['最低价格边界','最高价格边界','最少有效报价数']),('本方及全部数值有效',['基准价有效集合']),('不排除任何同价对手',['不作去极值等排除','最终取整规则']),('计算过程与基准价不取整',['不作中间取整','分数限制于零至满分']),('取最低有效报价B作基准',['基准价方法'])]
  for prefix,fields in mappings:
   excerpts=page.get_by_label('逐字引文（保留完整计分及量尺声明）',exact=False);selected=next(excerpts.nth(i)for i in range(excerpts.count())if excerpts.nth(i).input_value().startswith(prefix));article=selected.locator('xpath=ancestor::article[1]')
   for field in fields:article.get_by_role('checkbox',name=field,exact=True).check()
  result['checked_coefficients']=page.get_by_role('checkbox',name='高于基准价系数',exact=True).is_checked()if page.get_by_role('checkbox',name='高于基准价系数',exact=True).count()==1 else 'not_single_or_not_rendered';capture(page,'05-real-operands-mapped-no-unused-coefficients');page.get_by_role('button',name='确认并登记公式',exact=True).click();page.wait_for_timeout(800);capture(page,'06-legal-formula-registered');result['body']=page.locator('body').inner_text();print(result['body']);print('FIELDS',page.locator('input,textarea,select').evaluate_all('(es)=>es.map(x=>({type:x.type,label:x.labels?.[0]?.innerText,aria:x.getAttribute("aria-label"),value:x.value,html:x.tagName=="SELECT"?x.outerHTML:null}))'));print('BUTTONS',page.get_by_role('button').all_text_contents())
 except Exception as e:result['exception']=str(e);capture(page,'register-probe-current');print(str(e));print(page.locator('body').inner_text())
 finally:
  result.update({'at':stamp(),'events':events});(ROOT/'formula-register-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2));c.storage_state(path='/tmp/pricingf05-user-auth.json');b.close()
