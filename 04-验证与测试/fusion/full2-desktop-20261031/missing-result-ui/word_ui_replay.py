import ast, datetime, hashlib, json, os, pathlib, subprocess, sys
from word_ui_revision import ROOT,APP,WEB,OUT
sys.path.insert(0,str(APP))
from deploy.disposable_docker import DockerTestRun
def main():
 os.environ['PATH']=r'D:\Docker\Docker\resources\bin;'+os.environ['PATH']
 os.environ['DOCKER_HOST']='npipe:////./pipe/dockerDesktopLinuxEngine'
 os.environ['DOCKER_CONFIG']=str(OUT/'.docker')
 replay=OUT/'replay';replay.mkdir(exist_ok=True)
 captured=ROOT/'evidence-local-20261030/f2-normal-word-20261031-125726/external-word-state.json'
 state=json.loads(captured.read_text(encoding='utf-8'))['items'][0]
 test=(WEB/'src/WordOriginals.test.tsx').read_text(encoding='utf-8')
 fixture=test[test.index('const word:'):test.index('function json(')].replace('const word: WordOriginalView','const word')
 code='import React from '+json.dumps((WEB/'node_modules/react/index.js').as_posix())+';\nimport {createRoot} from '+json.dumps((WEB/'node_modules/react-dom/client.js').as_posix())+';\nimport {WordCard} from '+json.dumps((WEB/'src/WordOriginals.tsx').as_posix())+';\nimport '+json.dumps((WEB/'src/styles.css').as_posix())+';\n'+fixture+'\nconst captured='+json.dumps(state,ensure_ascii=False)+';\nconst failed={...word,...captured,conversion:{...word.conversion,...captured.conversion,pdf_document_version_id:null,controlled_render_verified:false},converted_pdf_action:null};\nwindow.fetch=async()=>new Response(JSON.stringify(failed),{status:200,headers:{"Content-Type":"application/json"}});\ncreateRoot(document.getElementById("root")).render(<main style={{maxWidth:1100,margin:"24px auto",padding:16}}><h1>Word原件</h1><WordCard initial={failed} bidId="bid" disabled={false} onPdf={()=>{}} /></main>);'
 entry=OUT/'replay-entry.tsx';entry.write_text(code,encoding='utf-8')
 bundle=OUT/'bundle.cjs';bundle.write_text('require('+json.dumps(str(WEB/'node_modules/esbuild'))+').buildSync('+json.dumps({'entryPoints':[str(entry)],'bundle':True,'outfile':str(replay/'app.js'),'jsx':'automatic','nodePaths':[str(WEB/'node_modules')],'external':['/fonts/*'],'define':{'process.env.NODE_ENV':'"production"'}})+');')
 plugin=r'''plugins:[{name:"vite-url-assets",setup(b){b.onResolve({filter:/\?url$/},a=>({path:a.path,namespace:"url-asset"}));b.onLoad({filter:/.*/,namespace:"url-asset"},()=>({contents:'export default "/pdf.worker.min.mjs"',loader:"js"}));}}],'''
 bundle.write_text(bundle.read_text().replace('buildSync({','build({'+plugin))
 import shutil
 shutil.copy2(WEB/'node_modules/pdfjs-dist/legacy/build/pdf.worker.min.mjs',replay/'pdf.worker.min.mjs')
 subprocess.run([r'D:\tools\nodejs22\node-v22.23.2-win-x64\node.exe',str(bundle)],check=True)
 (replay/'index.html').write_text('<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="stylesheet" href="app.css"><div id="root"></div><script src="app.js"></script></html>',encoding='utf-8')
 import shutil
 shutil.copytree(WEB/'public/fonts',replay/'fonts',dirs_exist_ok=True)
 tree=ast.parse((ROOT/'run_native_browser.py').read_text(encoding='utf-8'))
 prep=next(ast.literal_eval(n.value) for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='PREP' for t in n.targets))
 prep='import hashlib,io,json,os,pathlib,shutil,subprocess,tarfile,urllib.request,zipfile\nfrom concurrent.futures import ThreadPoolExecutor\npathlib.Path("/work").mkdir(exist_ok=True)\n'+prep[prep.index('def run('):prep.index("archive=pathlib.Path('/work/seaweedfs.tar.gz')")]
 prep='\n'.join(line for line in prep.splitlines() if "'npm','ci'" not in line)
 (OUT/'prepare-browser.py').write_text(prep,encoding='utf-8')
 browser=r'''
const {chromium}=require('/work/browser/node_modules/playwright');
const assert=require('node:assert/strict');
(async()=>{let browser;try{
browser=await chromium.launch({executablePath:'/work/browsers/chromium_headless_shell-1161/chrome-linux/headless_shell',headless:true,args:['--no-sandbox']});
assert.match(browser.version(),/^134\./);const results=[];
for(const width of [1440,390]){const context=await browser.newContext({viewport:{width,height:1000}});const page=await context.newPage();await page.goto('http://127.0.0.1:5179');await page.getByText('请按下列范围提示继续；Word原件仍保留。',{exact:true}).waitFor();await page.evaluate(()=>document.fonts.ready);assert.equal(await page.getByText(/尚未实际运行转换/).count(),0);assert.equal(await page.getByText('WORD_CONVERSION_RESULT_MISSING',{exact:true}).isVisible(),false);const summary=page.getByText('查看转换状态详情',{exact:true});await summary.focus();await page.keyboard.press('Enter');assert.equal(await page.getByText('WORD_CONVERSION_RESULT_MISSING',{exact:true}).isVisible(),true);assert.equal(await summary.evaluate(e=>document.activeElement===e),true);await page.screenshot({path:`/evidence/replay-${width}-details.png`,fullPage:true});await page.keyboard.press('Enter');assert.equal(await page.getByText('WORD_CONVERSION_RESULT_MISSING',{exact:true}).isVisible(),false);await page.screenshot({path:`/evidence/replay-${width}.png`,fullPage:true});const dimensions=await page.evaluate(()=>({viewport:innerWidth,scroll:document.documentElement.scrollWidth}));assert.ok(dimensions.scroll<=width,JSON.stringify(dimensions));results.push({width,details_keyboard_open_close:true,focus_retained:true,old_claim_absent:true,no_horizontal_overflow:true});await context.close();}
require('fs').writeFileSync('/evidence/replay-result.json',JSON.stringify({browser:browser.version(),scope:'Captured synthetic state UI replay only, not business E2E',results},null,2));
}finally{if(browser)await browser.close();}})().catch(e=>{console.error(e);process.exitCode=1;});
'''
 (OUT/'browser.cjs').write_text(browser,encoding='utf-8')
 image=json.loads(subprocess.check_output(['docker','image','inspect','tender-fusion-checks:py312-ocr']))[0]['Id']
 summary={'started':datetime.datetime.now(datetime.timezone.utc).isoformat(),'image':image,'captured_state_sha256':hashlib.sha256(captured.read_bytes()).hexdigest(),'fixture_completion':'Remaining required view fields from existing synthetic WordOriginals unit fixture; captured id/name/conversion preserved exactly. No server or business E2E.'}
 try:
  with DockerTestRun(OUT/'resources.json') as run:
   container=run.container(image,['--memory','2g','--cpus','2','--pids-limit','256','--mount',f'type=bind,source={OUT},target=/evidence','--entrypoint','python'],['-c','import time;time.sleep(1800)']);run.start(container)
   result=run._call('exec',container,'python','/evidence/prepare-browser.py',timeout=1200);(OUT/'browser-prepare.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8');assert result.returncode==0,'BROWSER_PREPARE_FAILED'
   result=run._call('network','disconnect','bridge',container);assert result.returncode==0,result.stderr
   result=run._call('exec','--detach',container,'python','-m','http.server','5179','--bind','127.0.0.1','--directory','/evidence/replay');assert result.returncode==0
   result=run._call('exec',container,'/work/node-v22.23.2-linux-x64/bin/node','/evidence/browser.cjs',timeout=120);(OUT/'browser.log').write_text(result.stdout+'\n'+result.stderr,encoding='utf-8');assert result.returncode==0,'BROWSER_REPLAY_FAILED'
   summary['passed']=True
 finally:
  summary['finished']=datetime.datetime.now(datetime.timezone.utc).isoformat();(OUT/'replay-summary.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
 print(json.dumps(summary),flush=True)
if __name__=='__main__':main()
