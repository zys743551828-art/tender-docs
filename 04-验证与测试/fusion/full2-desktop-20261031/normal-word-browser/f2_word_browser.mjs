import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createHash,randomUUID} from 'node:crypto';
import {createRequire} from 'node:module';
let input='';for await(const part of process.stdin)input+=part;const cfg=JSON.parse(input);
const require=createRequire(import.meta.url);const {chromium}=require('/work/browser/node_modules/playwright');
let externalOutcome={state:'not_proved'};let browser,page,bid,csrf='',authenticated=false,phase='private-login-binding';const events=[];const pageErrors=[];let writes=Promise.resolve();
const save=async name=>page.screenshot({path:'/evidence/'+name+'.png',fullPage:false});
const digest=bytes=>createHash('sha256').update(bytes).digest('hex');
const write=(name,value)=>fs.writeFile('/evidence/'+name,JSON.stringify(value,null,2));
class ProbeFailure extends Error{constructor(code,status){super(code);this.code=code;this.status=status;}}
async function call(method,path,body,etag,expected=200){const headers={};if(method!=='GET'){headers.Origin=cfg.origin;headers['X-CSRF-Token']=csrf;headers['Idempotency-Key']=randomUUID();if(etag)headers['If-Match']=etag;}const response=await page.request.fetch(cfg.origin+'/api/v1'+path,{method,headers,...(body===undefined?{}:{data:body})});events.push({method,path:path.split('?')[0],status:response.status()});if(response.status()!==expected){let code='HTTP_STATUS';try{const value=await response.json();const candidate=value.code??value.error?.code;if(typeof candidate==='string'&&/^[A-Z][A-Z0-9_]{2,100}$/.test(candidate))code=candidate;}catch{}throw new ProbeFailure(code,response.status());}return {data:await response.json(),etag:response.headers().etag};}
function publicOperation(value){
 const codes=new Set();function collect(v){if(Array.isArray(v))for(const x of v)collect(x);else if(v&&typeof v==='object')for(const [key,x]of Object.entries(v)){if(['code','error_code','failure_code','reason_code'].includes(key)&&typeof x==='string'&&/^[A-Z][A-Z0-9_]{2,100}$/.test(x))codes.add(x);if(x&&typeof x==='object')collect(x);}}collect(value);
 return {id:value.id,state:value.state,fixed_codes:[...codes]};
}
function publicWord(value){return {id:value.id,original_name:value.original_name,conversion:Object.fromEntries(['state','coverage','gaps','error_code','retryable','conversion_attempted','operation_id'].filter(key=>key in value.conversion).map(key=>[key,value.conversion[key]]))};}
async function readWordState(filename){const words=(await call('GET','/bids/'+bid+'/word-originals')).data;const record={items:words.items.map(publicWord)};for(const item of record.items)if(item.conversion.operation_id){const op=(await call('GET','/operations/'+item.conversion.operation_id)).data;item.operation=publicOperation(op);}await write(filename,record);return {words,record};}
async function authoringAPI(){
 let step='catalog';const record={attempted:true,manual_content:true,model_used:false,business_qualification_claimed:false,outputs:[]};
 try{
  let catalog=(await call('GET','/authoring/templates?kind=outline')).data;
  if(!catalog.items.some(item=>item.is_builtin&&item.kind==='outline')){await call('POST','/bids/'+bid+'/drafts',{title:'合成初始化草稿',outline_template_version_id:null},null,201);catalog=(await call('GET','/authoring/templates?kind=outline')).data;}
  const outline=catalog.items.find(item=>item.is_builtin&&item.kind==='outline'&&item.config?.nodes?.length===1);if(!outline)throw new ProbeFailure('BUILTIN_OUTLINE_UNAVAILABLE');
  step='create-structured-draft';let draft=(await call('POST','/bids/'+bid+'/drafts',{title:'合成人工PAGE及RICH接线验证',outline_template_version_id:outline.id},null,201)).data;
  const section=draft.sections[0];assert.ok(section);
  step='read-manual-section';const current=(await call('GET','/sections/'+section.id)).data;const content=structuredClone(current.content);
  content.content=[{type:'paragraph',attrs:{id:randomUUID()},content:[{type:'text',text:'合成人工正文，仅验证正常安装的格式生成接线，不代表模型生成或业务资格通过。'}]}];
  step='save-manual-section';await call('PATCH','/bids/'+bid+'/drafts/'+draft.id,{base_draft_revision:draft.revision,changes:[{section_id:section.id,base_section_revision:current.revision,schema_digest:current.schema_digest,content}]},'"draft:'+draft.id+':'+draft.revision+'"');
  draft=(await call('GET','/bids/'+bid+'/drafts/'+draft.id)).data;const tree=(await call('GET','/bids/'+bid+'/drafts/'+draft.id+'/structure')).data;
  record.draft_id=draft.id;record.draft_revision=draft.revision;record.prerequisites_succeeded=true;
  for(const lane of ['page','rich']){
   const item={lane,state:'not_proved'};record.outputs.push(item);let accepted;
   try{
    const body={draft_id:draft.id,draft_revision:draft.revision,directory_template_version_id:tree.directory_template_version_id,style_template_version_id:tree.style_template_version_id,formats:['docx','pdf'],follow_up:'none',...(lane==='rich'?{pagination_mode:'chapter_start_new_page'}:{})};
    accepted=(await call('POST','/bids/'+bid+'/builds',body,null,202)).data;item.build_id=accepted.resource_id;item.operation_id=accepted.operation.id;
    const deadline=Date.now()+330000;let build;
    while(Date.now()<deadline){
     build=(await call('GET','/builds/'+item.build_id)).data;const operation=(await call('GET','/operations/'+item.operation_id)).data;item.operation=publicOperation(operation);item.formats=build.outputs.map(output=>({format:output.format,state:output.state,document_version_id:output.document_version_id,gaps:output.gaps}));item.layout=build.layout;await write('authoring-api.json',record);
     if(['failed','cancelled'].includes(operation.state)||build.outputs.every(output=>output.state!=='pending'))break;
     await new Promise(resolve=>setTimeout(resolve,1000));
    }
    if(!build?.outputs.length||!build.outputs.every(output=>output.state==='succeeded'))throw new ProbeFailure('AUTHORING_OUTPUT_NOT_SUCCEEDED');
    assert.equal(item.operation.state,'succeeded');item.state='succeeded';
   }catch(error){item.state='not_proved';item.error_type=error.name;item.error_code=error.code??'AUTHORING_API_CHECK_FAILED';if(error.status)item.http_status=error.status;}
   await write('authoring-api.json',record);
  }
  record.state=record.outputs.length===2&&record.outputs.every(item=>item.state==='succeeded')?'succeeded':'not_proved';record.normal_export_queue_proved=record.state==='succeeded';
 }catch(error){record.state='not_proved';record.stage=step;record.error_type=error.name;record.error_code=error.code??'AUTHORING_API_CHECK_FAILED';if(error.status)record.http_status=error.status;}
 await write('authoring-api.json',record);return record;
}
try{
 assert.equal(process.getuid(),65532);assert.equal(cfg.origin,'http://127.0.0.1:8080');
 for(const [path,mode] of [['/validation-login',0o700],['/validation-login/profile.json',0o600]]){const stat=await fs.lstat(path);assert.equal(stat.isSymbolicLink(),false);assert.equal(stat.uid,65532);assert.equal(stat.mode&0o777,mode);}
 const login=JSON.parse(await fs.readFile('/validation-login/profile.json','utf8'));assert.equal(login.origin,cfg.origin);assert.equal(login.version,cfg.installation_version);assert.equal(login.plan_digest,cfg.expected_plan_digest);assert.equal(typeof login.login_name,'string');assert.equal(typeof login.password,'string');assert.ok(login.password.length>=32);
 await write('private-login-binding.json',{uid:process.getuid(),private_file_permissions_verified:true,origin_matches_public_request:true,version_matches_public_request:true,plan_matches_deployment_label:true,contents_exported:false});
 browser=await chromium.launch({headless:true});const context=await browser.newContext({viewport:{width:1440,height:1100},acceptDownloads:true});page=await context.newPage();page.on('pageerror',error=>pageErrors.push({type:error.name}));
 context.on('response',response=>{try{const u=new URL(response.url());if(u.protocol!=='http:'&&u.protocol!=='https:')return;events.push({method:response.request().method(),path:u.pathname,status:response.status()});const text=JSON.stringify(events);writes=writes.then(()=>fs.writeFile('/evidence/public-events.json',text));}catch{}});
 phase='login';await page.goto(cfg.origin+'/projects');await page.getByLabel('账号',{exact:true}).fill(login.login_name);await page.getByLabel('密码',{exact:true}).fill(login.password);await page.getByRole('button',{name:'登录工作区',exact:true}).click();await page.getByRole('button',{name:'退出登录',exact:true}).waitFor();authenticated=true;
 const auth=(await call('GET','/auth/session')).data;csrf=auth.csrf_token;assert.equal(typeof csrf,'string');
 phase='create-synthetic-bid';bid=cfg.resume_bid??(await call('POST','/bids',{title:'合成正常安装Word验收 '+cfg.run_id.slice(0,8)},null,201)).data.id;
 const settings=await call('GET','/bids/'+bid+'/settings');await call('PATCH','/bids/'+bid+'/settings',{auto_review:false},settings.etag);assert.equal((await call('GET','/bids/'+bid+'/settings')).data.settings.auto_review,false);
 try{
 if(!cfg.resume_bid){phase='external-word-upload';await page.goto(cfg.origin+'/bids/'+bid+'/materials');await page.locator('#project-files').setInputFiles({name:'合成正常实例Word.docx',mimeType:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',buffer:Buffer.from(cfg.docx_base64,'base64')});await page.locator('.draft-file select').first().selectOption('body');await page.getByRole('button',{name:'上传并核验原件',exact:true}).click();await page.getByRole('button',{name:/确认 1 份资料入库/}).waitFor({timeout:240000});await page.getByRole('button',{name:/确认 1 份资料入库/}).click();
 }else{await page.goto(cfg.origin+'/bids/'+bid+'/materials');}
 const word=page.locator('.word-original-card').filter({has:page.getByRole('heading',{name:'合成正常实例Word.docx',exact:true})});phase='external-convert-adopt';
 const deadline=Date.now()+240000;let conversionComplete=false;
 while(Date.now()<deadline){
  const current=await readWordState('external-word-state.json');const entry=current.record.items.find(value=>value.original_name==='合成正常实例Word.docx');
  if(entry){if(['failed','cancelled','partial'].includes(entry.conversion.state)||['failed','cancelled'].includes(entry.operation?.state)||(entry.conversion.state==='succeeded'&&entry.conversion.coverage!=='complete'))throw new ProbeFailure('EXTERNAL_CONVERSION_NOT_COMPLETE');if(entry.conversion.state==='succeeded'&&entry.conversion.coverage==='complete'){conversionComplete=true;break;}}
  await new Promise(resolve=>setTimeout(resolve,1000));
 }
 assert.equal(conversionComplete,true);await word.getByText('Word原件已保存／受控转换已完成',{exact:true}).waitFor({timeout:30000});await word.getByText('对应PDF已作为当前材料采用；实际检查状态请回工作台核对。',{exact:true}).waitFor({timeout:30000});
 const words=(await call('GET','/bids/'+bid+'/word-originals')).data;const item=words.items.find(value=>value.original_name==='合成正常实例Word.docx');assert.ok(item?.converted_pdf_action?.document_version_id);const pdfId=item.converted_pdf_action.document_version_id;let document;
 for(let n=0;n<150;n++){document=(await call('GET','/documents/'+pdfId)).data;if(document.parse_coverage==='partial')break;await new Promise(resolve=>setTimeout(resolve,300));}assert.equal(document.parse_coverage,'partial');await page.reload();await word.getByText('对应PDF已作为当前材料采用；实际检查状态请回工作台核对。',{exact:true}).waitFor();await page.locator('.materials-panel tbody tr').filter({hasText:document.filename}).getByText('解析范围不完整',{exact:true}).waitFor();await word.scrollIntoViewIfNeeded();await save('external-desktop');
 phase='external-download';for(const [label,filename] of [['下载Word资料原件','download-original.docx'],['下载此Word转换的PDF','download-converted.pdf']]){const promise=page.waitForEvent('download');await word.getByRole('button',{name:label,exact:true}).click();await(await promise).saveAs('/evidence/'+filename);}
 assert.deepEqual(await fs.readFile('/evidence/download-original.docx'),Buffer.from(cfg.docx_base64,'base64'));assert.equal(digest(await fs.readFile('/evidence/download-converted.pdf')),document.sha256);
 phase='external-original-page';await word.getByRole('button',{name:'查看此版PDF原页',exact:true}).click();const surface=page.locator('.evidence-dialog');await surface.waitFor();const canvas=surface.getByRole('img',{name:document.filename+'，物理页1原页',exact:true});await canvas.waitFor({state:'visible',timeout:30000});await surface.getByText('正在绘制PDF原页…',{exact:true}).waitFor({state:'hidden',timeout:30000});const pixels=await canvas.evaluate(element=>{const bytes=element.getContext('2d').getImageData(0,0,element.width,element.height).data;let dark=0;for(let i=0;i<bytes.length;i+=4)if(bytes[i+3]&&(bytes[i]<235||bytes[i+1]<235||bytes[i+2]<235))dark++;return {width:element.width,height:element.height,nonwhite_pixels:dark};});assert.ok(pixels.nonwhite_pixels>30);await canvas.scrollIntoViewIfNeeded();await save('external-preview-desktop');await page.setViewportSize({width:390,height:844});await canvas.scrollIntoViewIfNeeded();await save('external-preview-390');assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),390);await page.keyboard.press('Escape');await surface.waitFor({state:'detached'});
 phase='external-narrow-refresh';await page.reload();await word.getByText('对应PDF已作为当前材料采用；实际检查状态请回工作台核对。',{exact:true}).waitFor();await page.locator('.materials-panel tbody tr').filter({hasText:document.filename}).getByText('解析范围不完整',{exact:true}).waitFor();await word.scrollIntoViewIfNeeded();
 const narrowButtons={};for(const [label,filename] of [['下载Word资料原件','download-original-390.docx'],['下载此Word转换的PDF','download-converted-390.pdf']]){const button=word.getByRole('button',{name:label,exact:true});await button.scrollIntoViewIfNeeded();const box=await button.boundingBox();assert.ok(box&&box.width>0&&box.x>=0&&box.x+box.width<=390);narrowButtons[label]=box;const pending=page.waitForEvent('download');await button.click();await(await pending).saveAs('/evidence/'+filename);}
 assert.deepEqual(await fs.readFile('/evidence/download-original-390.docx'),Buffer.from(cfg.docx_base64,'base64'));assert.equal(digest(await fs.readFile('/evidence/download-converted-390.pdf')),document.sha256);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth),390);await save('external-390-refresh');
 externalOutcome={state:'passed',same_origin_upload:!cfg.resume_bid,resumed_existing_upload:Boolean(cfg.resume_bid),converted:true,adopted:true,parse_coverage:'partial',refresh_restored:true,original_download_matches_input:true,pdf_download_matches_api_digest:true,preview_canvas:pixels,narrow_refresh_restored:true,narrow_downloads_clicked:true,narrow_download_buttons:narrowButtons,pdf_document_id:pdfId,bid_id:bid};
 }catch(error){externalOutcome={state:'not_proved',phase,error_type:error.name,error_code:error.code??'EXTERNAL_CHECK_FAILED'};
  try{await readWordState('external-word-state.json');await page.locator('.word-original-card').first().screenshot({path:'/evidence/external-word-card.png'});}catch{}
  await write('external-lane-result.json',externalOutcome);
 }
 phase='manual-authoring-api';const authoring=await authoringAPI();await writes;assert.deepEqual(pageErrors,[]);
 await write('browser-result.json',{state:'completed',external_word:externalOutcome,manual_authoring:authoring,browser_version:browser.version(),ui_origin:cfg.origin,model_calls:0,credentials_exported:false,cookies_exported:false,request_bodies_exported:false,page_errors:pageErrors});
 process.stdout.write(JSON.stringify({state:'finished',external_word_state:externalOutcome.state,manual_authoring_state:authoring.state}));
}catch(error){if(authenticated&&bid&&page&&new URL(page.url()).pathname.includes(bid))await save('external-failure').catch(()=>{});await write('browser-failure.json',{state:'failed',phase,error_type:error.name,error_code:error.code??'BROWSER_PROBE_FAILED',...(error.status?{http_status:error.status}:{}),credentials_exported:false});process.stdout.write(JSON.stringify({state:'failed',phase,error_type:error.name,error_code:error.code??'BROWSER_PROBE_FAILED'}));process.exitCode=1;}
finally{if(browser)await browser.close().catch(()=>{});await writes.catch(()=>{});}
