// One real current-App Word journey. No route interception or fake API outcomes.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createRequire} from 'node:module';
import {createHash} from 'node:crypto';
let input='';for await(const part of process.stdin)input+=part;
const config=JSON.parse(input);const require=createRequire(import.meta.url);
const {chromium}=require('/work/browser/node_modules/playwright');
let browser,page;const events=[];const errors=[];const resourceResponses=[];const requests=[];const workerPaths=[];const bodyTasks=[];let pdfCaptured=false;
const save=async name=>page.screenshot({path:'/evidence/'+name+'.png',fullPage:true});
const readAPI=async path=>{const response=await page.request.get(config.base_url+'/api/v1'+path);assert.equal(response.status(),200);return response.json();};
async function noOverflow(){return page.evaluate(()=>({viewport:window.innerWidth,scroll:document.documentElement.scrollWidth}));}
async function reachable(button){await button.scrollIntoViewIfNeeded();const bounds=await button.evaluate(element=>{const r=element.getBoundingClientRect();return {left:r.left,right:r.right,top:r.top,bottom:r.bottom,width:window.innerWidth,height:window.innerHeight};});assert.ok(bounds.left>=0&&bounds.right<=bounds.width+1&&bounds.top>=0&&bounds.bottom<=bounds.height+1,JSON.stringify(bounds));assert.equal(await button.isEnabled(),true);return bounds;}
try{
 browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1100},acceptDownloads:true});
 context.on('request',request=>{const u=new URL(request.url());if(u.protocol==='http:'||u.protocol==='https:')requests.push({path:u.pathname,method:request.method()});});
 context.on('response',response=>{const u=new URL(response.url());if(u.protocol!=='http:'&&u.protocol!=='https:')return;resourceResponses.push({path:u.pathname,status:response.status(),type:response.request().resourceType()});if(response.status()===200&&response.request().method()==='GET'&&u.pathname.startsWith('/api/v1/object-transfers/')&&!pdfCaptured)bodyTasks.push((async()=>{const bytes=await response.body();if(!pdfCaptured&&bytes.subarray(0,5).equals(Buffer.from('%PDF-'))){pdfCaptured=true;await fs.writeFile('/evidence/preview-converted.pdf',bytes);}})());});
 await context.addInitScript(()=>{window.__previewLoading=[];new MutationObserver(()=>{for(const element of document.querySelectorAll('.pdf-canvas-container'))if(element.textContent.includes('正在绘制PDF原页')&&element.getClientRects().length&&window.__previewLoading.length<100)window.__previewLoading.push({at:Date.now(),text:'正在绘制PDF原页…'});}).observe(document,{childList:true,subtree:true,characterData:true});});
 page=await context.newPage();page.on('worker',worker=>{workerPaths.push(new URL(worker.url()).pathname);});page.on('pageerror',e=>errors.push(e.message));
 page.on('response',r=>{if(r.url().includes('/api/v1/')){events.push({path:new URL(r.url()).pathname,status:r.status(),method:r.request().method()});fs.writeFile('/evidence/browser-events.json',JSON.stringify(events,null,2)).catch(()=>{});}});
 await page.goto(config.base_url+'/projects');
 const indexResponse=await page.request.get(config.base_url+'/index.html');assert.equal(indexResponse.status(),200);
 const indexSha=createHash('sha256').update(await indexResponse.body()).digest('hex');assert.equal(indexSha,config.index_sha256);
 const productionClient=await page.evaluate(()=>({secure:window.isSecureContext,uuid:typeof crypto.randomUUID,modules:[...document.querySelectorAll('script[type="module"][src]')].map(element=>new URL(element.src).pathname)}));
 assert.equal(productionClient.secure,true);assert.equal(productionClient.uuid,'function');assert.ok(productionClient.modules.length&&productionClient.modules.every(path=>path.startsWith('/assets/')&&!path.includes('@vite')));
 await fs.writeFile('/evidence/production-http.json',JSON.stringify({status:indexResponse.status(),server:indexResponse.headers().server,index_sha256:indexSha,index_equals_built_artifact:true,client:productionClient,vite_server_started:false},null,2));
 await page.getByLabel('账号',{exact:true}).fill(config.login);
 await page.getByLabel('密码',{exact:true}).fill(config.password);
 await page.getByRole('button',{name:'登录工作区',exact:true}).click();
 await page.getByRole('button',{name:'退出登录',exact:true}).waitFor();
 const settingsResponse=await page.request.get(config.base_url+'/api/v1/bids/'+config.bid+'/settings');
 assert.equal(settingsResponse.status(),200,'synthetic settings fixture must satisfy real SettingsView before upload');
 const settingsView=await settingsResponse.json();
 assert.equal(settingsView.settings.auto_review,false);
 for(const key of ['auto_review','consistency','scope_residue','show_prediction','pricing','result_reminder','accept_rules_as_written'])assert.equal(typeof settingsView.settings[key],'boolean');
 await fs.writeFile('/evidence/seed-precondition.json',JSON.stringify({settings_http_status:200,settings:settingsView.settings,creation_entry_tested:false},null,2));
 await page.goto(config.base_url+'/bids/'+config.bid+'/materials');
 await page.locator('#project-files').waitFor({state:'attached'});
 await page.locator('#project-files').setInputFiles({name:'合成施工方案.docx',mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',buffer:Buffer.from(config.docx,'base64')});
 await page.locator('.draft-file select').first().selectOption('body');
 await save('word-selected');
 await page.getByRole('button',{name:'上传并核验原件',exact:true}).click();
 await page.getByRole('button',{name:/确认 1 份资料入库/}).waitFor({timeout:180000});
 await save('word-verified');
 await page.getByRole('button',{name:/确认 1 份资料入库/}).click();
 const word=page.locator('.word-original-card').filter({has:page.getByRole('heading',{name:'合成施工方案.docx',exact:true})});
 await word.getByText('Word原件已保存／受控转换已完成',{exact:true}).waitFor({timeout:180000});
 await word.getByText('对应PDF已作为当前材料采用；实际检查状态请回工作台核对。',{exact:true}).waitFor({timeout:180000});
 const wordPage=await readAPI('/bids/'+config.bid+'/word-originals');
 const currentWord=wordPage.items.find(item=>item.original_name==='合成施工方案.docx');
 assert.ok(currentWord?.converted_pdf_action?.document_version_id);
 const pdfId=currentWord.converted_pdf_action.document_version_id;
 let parsedDocument;
 for(let attempt=0;attempt<150;attempt++){parsedDocument=await readAPI('/documents/'+pdfId);if(parsedDocument.parse_coverage==='partial')break;await new Promise(resolve=>setTimeout(resolve,200));}
 assert.equal(parsedDocument.id,pdfId);assert.equal(parsedDocument.parse_coverage,'partial');
 const pdfRow=page.locator('.materials-panel tbody tr').filter({hasText:parsedDocument.filename});
 await pdfRow.getByText('解析范围不完整',{exact:true}).waitFor({timeout:15000});

 const previewTrigger=word.getByRole('button',{name:'查看此版PDF原页',exact:true});
 const surface=page.locator('.evidence-dialog');
 const previewRequests=()=>requests.filter(item=>item.method==='GET'&&(item.path==='/api/v1/documents/'+pdfId||item.path==='/api/v1/documents/'+pdfId+'/evidence'||item.path==='/api/v1/artifacts/'+pdfId+'/download'||item.path.startsWith('/api/v1/object-transfers/'))).length;
 const observed=[];
 async function openPreview(label){
  await previewTrigger.scrollIntoViewIfNeeded();await previewTrigger.focus();await previewTrigger.click();await surface.waitFor();
  const close=surface.getByRole('button',{name:'关闭原件',exact:true});await close.waitFor();
  assert.equal(await close.evaluate(element=>element===document.activeElement),true);
  if(await surface.getByText('正在绘制PDF原页…',{exact:true}).isVisible().catch(()=>false))await page.screenshot({path:'/evidence/preview-'+label+'-loading.png',fullPage:false});
  const canvas=surface.getByRole('img',{name:parsedDocument.filename+'，物理页1原页',exact:true});await canvas.waitFor({state:'visible',timeout:30000});
  await surface.getByText('正在绘制PDF原页…',{exact:true}).waitFor({state:'hidden',timeout:30000});
  const pixels=await canvas.evaluate(element=>{const {width,height}=element;const bytes=element.getContext('2d').getImageData(0,0,width,height).data;let dark=0;for(let offset=0;offset<bytes.length;offset+=4)if(bytes[offset+3]>0&&(bytes[offset]<235||bytes[offset+1]<235||bytes[offset+2]<235))dark++;return {width,height,nonwhite_pixels:dark};});
  assert.ok(pixels.width>100&&pixels.height>100&&pixels.nonwhite_pixels>30,JSON.stringify(pixels));
  await fs.writeFile('/evidence/preview-'+label+'-canvas-proof.json',JSON.stringify({pixels,actual_aria_label:await canvas.getAttribute('aria-label'),loading_hidden:true},null,2));
  const selector=surface.getByRole('combobox',{name:/物理页/});assert.equal(await selector.count(),1);assert.equal(await selector.inputValue(),'1');assert.deepEqual(await selector.locator('option').allTextContents(),['1']);
  await surface.getByText('解析范围不完整',{exact:true}).waitFor();await surface.getByText('以下范围尚未完整核对',{exact:true}).waitFor();
  await canvas.scrollIntoViewIfNeeded();const layout=await noOverflow();assert.ok(layout.scroll<=layout.viewport+1);
  await page.screenshot({path:'/evidence/preview-'+label+'-canvas.png',fullPage:false});
  const textButton=surface.getByRole('button',{name:'解析文本',exact:true});if(await textButton.isVisible().catch(()=>false))await textButton.click();
  const quote=surface.locator('.evidence-quote p').filter({hasText:'施工工期'}).first();await quote.waitFor({state:'visible'});await quote.scrollIntoViewIfNeeded();
  assert.match(await quote.innerText(),/120/);await page.screenshot({path:'/evidence/preview-'+label+'-text.png',fullPage:false});
  const record={label,page_select_labels:await selector.evaluate(element=>[...element.labels].map(label=>label.textContent.trim())),role:await surface.getAttribute('role'),canvas:pixels,page:'1',page_options:['1'],actual_text:await quote.innerText(),partial_notice:true,layout};observed.push(record);return record;
 }
 async function closeAndCheck(kind,label){
  if(kind==='button')await surface.getByRole('button',{name:'关闭原件',exact:true}).click();else await page.keyboard.press('Escape');
  await surface.waitFor({state:'detached'});await page.waitForFunction(()=>document.activeElement?.textContent?.trim()==='查看此版PDF原页',null,{timeout:5000});assert.equal(await previewTrigger.evaluate(element=>element===document.activeElement),true);
  const before=previewRequests();await new Promise(resolve=>setTimeout(resolve,2300));assert.equal(previewRequests(),before,'preview initiated more requests after close');
  await page.screenshot({path:'/evidence/preview-'+label+'-focus-return.png',fullPage:false});return {kind,focus_returned:true,requests_at_close:before,requests_after_idle:previewRequests(),idle_ms:2300};
 }

 const cdp=await context.newCDPSession(page);await cdp.send('Network.enable');await cdp.send('Network.setCacheDisabled',{cacheDisabled:true});
 let phase='warm-desktop';const transfers=new Map();const pwFailures=[];let netWrites=Promise.resolve();
 const saveNetwork=()=>{const text=JSON.stringify({phase,transfers:[...transfers.values()],request_failures:pwFailures},null,2);netWrites=netWrites.then(()=>fs.writeFile('/evidence/real-network.json',text));return netWrites;};
 cdp.on('Network.requestWillBeSent',event=>{const u=new URL(event.request.url);if(event.request.method==='GET'&&u.pathname.startsWith('/api/v1/object-transfers/')){transfers.set(event.requestId,{request_id:event.requestId,path:u.pathname,phase,started_timestamp:event.timestamp,request_event_wall_ms:Date.now(),data_length:0,encoded_data_length:0});void saveNetwork();}});
 cdp.on('Network.responseReceived',event=>{const value=transfers.get(event.requestId);if(value){value.status=event.response.status;value.mime_type=event.response.mimeType;value.response_timestamp=event.timestamp;value.response_event_wall_ms=Date.now();void saveNetwork();}});
 cdp.on('Network.dataReceived',event=>{const value=transfers.get(event.requestId);if(value){value.data_length+=event.dataLength;value.encoded_data_length+=event.encodedDataLength;value.last_data_timestamp=event.timestamp;void saveNetwork();}});
 cdp.on('Network.loadingFinished',event=>{const value=transfers.get(event.requestId);if(value){value.finished=true;value.finished_timestamp=event.timestamp;value.total_encoded_length=event.encodedDataLength;void saveNetwork();}});
 cdp.on('Network.loadingFailed',event=>{const value=transfers.get(event.requestId);if(value){value.failure={canceled:event.canceled===true,error_text:event.errorText,timestamp:event.timestamp};void saveNetwork();}});
 page.on('requestfailed',request=>{const u=new URL(request.url());if(u.pathname.startsWith('/api/v1/object-transfers/')){pwFailures.push({path:u.pathname,error_text:request.failure()?.errorText,phase});void saveNetwork();}});
 async function until(check,code,timeout=60000){const deadline=Date.now()+timeout;while(Date.now()<deadline){const value=await check();if(value)return value;await new Promise(resolve=>setTimeout(resolve,50));}await saveNetwork();throw new Error(code);}
 async function focusSnapshot(){return page.evaluate(()=>{const e=document.activeElement;if(!window.__focusIds){window.__focusIds=new WeakMap();window.__focusNext=1;}if(!window.__focusIds.has(e))window.__focusIds.set(e,window.__focusNext++);const r=e.getBoundingClientRect();const style=getComputedStyle(e);return {id:window.__focusIds.get(e),tag:e.tagName,text:(e.getAttribute('aria-label')||e.textContent||'').trim().slice(0,90),inside_panel:!!e.closest('.evidence-dialog'),inside_surface:!!e.closest('[data-workspace-auxiliary-surface]'),visible:r.width>0&&r.height>0&&style.visibility!=='hidden'&&style.display!=='none'&&!e.closest('[hidden],[inert]'),focus_visible:e.matches(':focus-visible'),bounds:{left:r.left,right:r.right,top:r.top,bottom:r.bottom}};});}
 const focusResults=[];
 async function desktopKeyboard(){assert.equal(await surface.getAttribute('role'),'complementary');const close=surface.getByRole('button',{name:'关闭原件',exact:true});await close.focus();const initial=await focusSnapshot();const outbound=[];for(let n=0;n<6;n++){await page.keyboard.press('Shift+Tab');const state=await focusSnapshot();assert.ok(state.visible);outbound.push(state);if(!state.inside_surface)break;}assert.equal(outbound.at(-1).inside_surface,false,'desktop side panel trapped keyboard');await page.screenshot({path:'/evidence/focus-desktop-exit.png',fullPage:false});const inbound=[];for(let n=0;n<outbound.length;n++){await page.keyboard.press('Tab');inbound.push(await focusSnapshot());}assert.equal(inbound.at(-1).id,initial.id,'Tab did not return through natural desktop order');await page.screenshot({path:'/evidence/focus-desktop-return.png',fullPage:false});const result={viewport:'desktop',expected:'nonmodal keyboard exit and return',initial,outbound,inbound};focusResults.push(result);await fs.writeFile('/evidence/focus-sequences.json',JSON.stringify(focusResults,null,2));}
 async function compactCycle(mode){assert.equal(await surface.getAttribute('role'),'dialog');await surface.getByRole('button',{name:mode==='original'?'原页':'解析文本',exact:true}).click();const close=surface.getByRole('button',{name:'关闭原件',exact:true});await close.focus();const initial=await focusSnapshot();const forward=[initial];for(let n=0;n<30;n++){await page.keyboard.press('Tab');const state=await focusSnapshot();assert.ok(state.inside_panel&&state.visible,'Tab reached hidden or outside modal control');forward.push(state);if(state.id===initial.id)break;}assert.equal(forward.at(-1).id,initial.id,'Tab did not wrap');assert.ok(forward.length>2);await page.screenshot({path:'/evidence/focus-390-'+mode+'-forward-wrap.png',fullPage:false});const reverse=[await focusSnapshot()];for(let n=0;n<30;n++){await page.keyboard.press('Shift+Tab');const state=await focusSnapshot();assert.ok(state.inside_panel&&state.visible,'ShiftTab reached hidden or outside modal control');reverse.push(state);if(state.id===initial.id)break;}assert.equal(reverse.at(-1).id,initial.id,'ShiftTab did not wrap');assert.deepEqual(reverse.slice(1,-1).map(x=>x.id),forward.slice(1,-1).map(x=>x.id).reverse());await page.screenshot({path:'/evidence/focus-390-'+mode+'-reverse-wrap.png',fullPage:false});focusResults.push({viewport:'390',mode,expected:'modal bidirectional wrap',forward,reverse});await fs.writeFile('/evidence/focus-sequences.json',JSON.stringify(focusResults,null,2));}
 const cancelResults=[];
 async function slowCancel(label,kind){phase='slow-'+label;await cdp.send('Network.emulateNetworkConditions',{offline:false,latency:50,downloadThroughput:1024,uploadThroughput:-1});await previewTrigger.scrollIntoViewIfNeeded();await previewTrigger.focus();await previewTrigger.click();await surface.waitFor();
  const pending=await until(()=>[...transfers.values()].find(value=>value.phase===phase&&value.status===200&&value.mime_type?.includes('pdf')&&value.response_timestamp&&!value.finished&&!value.failure),'NO_UNFINISHED_REAL_PDF_RESPONSE');
  assert.equal(await surface.getByText('正在绘制PDF原页…',{exact:true}).isVisible(),true);const before={...pending,response_headers_received:true,body_transfer_unfinished:true,partial_body_bytes_claimed:pending.data_length>0&&pending.data_length<parsedDocument.byte_size};await page.screenshot({path:'/evidence/slow-'+label+'-pending.png',fullPage:false});assert.ok(!pending.finished&&!pending.failure,'PDF finished before close');
  const closed=await closeAndCheck(kind,'slow-'+label);await until(()=>pending.failure,'PDF_REQUEST_NOT_ABORTED',5000);assert.equal(pending.failure.canceled,true);assert.ok(pending.failure.error_text.includes('ERR_ABORTED'));assert.ok(!pending.finished);await until(()=>pwFailures.some(value=>value.path===pending.path&&value.phase===pending.phase&&value.error_text?.includes('ERR_ABORTED')),'PLAYWRIGHT_ABORT_EVENT_MISSING',5000);
  await cdp.send('Network.emulateNetworkConditions',{offline:false,latency:0,downloadThroughput:-1,uploadThroughput:-1});phase='recover-'+label;const recovered=await openPreview('recover-'+label);const record={label,download_limit_bytes_per_second:1024,expected_pdf_bytes:parsedDocument.byte_size,before_close:before,after_close:{...pending},closed,recovered};cancelResults.push(record);await saveNetwork();await fs.writeFile('/evidence/slow-cancel-results.json',JSON.stringify(cancelResults,null,2));await closeAndCheck(kind,'recover-'+label);
 }
 await openPreview('keyboard-desktop');await Promise.all(bodyTasks);assert.equal(pdfCaptured,true);await desktopKeyboard();await closeAndCheck('button','keyboard-desktop');
 await page.setViewportSize({width:390,height:844});phase='warm-390';await openPreview('keyboard-390');await compactCycle('original');await compactCycle('text');await closeAndCheck('escape','keyboard-390');
 await page.setViewportSize({width:1440,height:1100});await slowCancel('desktop','button');await page.setViewportSize({width:390,height:844});await slowCancel('390','escape');
 await saveNetwork();await netWrites;assert.equal(cancelResults.length,2);const expectedAborts=[...transfers.values()].filter(value=>value.phase.startsWith('slow-')&&value.failure?.canceled);assert.equal(expectedAborts.length,2);
 const loginIndex=events.findIndex(event=>event.path==='/api/v1/auth/login'&&event.method==='POST'&&event.status===200);assert.ok(loginIndex>=0);assert.deepEqual(events.filter((event,index)=>event.status>=400&&!(index<loginIndex&&event.path==='/api/v1/auth/session'&&event.method==='GET'&&event.status===401)),[]);
 assert.ok(!resourceResponses.some(item=>item.status===404||item.status>=500||((item.path.startsWith('/assets/')||item.path.startsWith('/pdfjs/'))&&item.status>=400)));assert.deepEqual(errors,[]);
 await fs.writeFile('/evidence/browser-result.json',JSON.stringify({passed:true,url:page.url(),browser_version:browser.version(),scope:'keyboard loops and true in-flight PDF abort/recovery only',preview_document_id:pdfId,focus_results:focusResults,cancel_results:cancelResults,network:[...transfers.values()],request_failures:pwFailures,observed,parse_api_partial:true,no_mocked_responses:true,cdp_throttling_page_scoped:true,events,resources:resourceResponses,page_errors:errors},null,2));console.log(JSON.stringify({passed:true,focus_checks:focusResults.length,real_aborts:cancelResults.length}));
}catch(error){if(page)await save('word-failure').catch(()=>{});await fs.writeFile('/evidence/browser-failure.json',JSON.stringify({type:error.name,message:error.message,events,page_errors:errors,resources:resourceResponses,requests,worker_paths:workerPaths},null,2));throw error;}
finally{if(browser)await browser.close();}
