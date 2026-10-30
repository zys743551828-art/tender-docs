// One real current-App Word journey. No route interception or fake API outcomes.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import {createRequire} from 'node:module';
import {createServer} from 'vite';
let input='';for await(const part of process.stdin)input+=part;
const config=JSON.parse(input);const require=createRequire(import.meta.url);
const {chromium}=require('/work/browser/node_modules/playwright');
let server,browser,page;const events=[];const errors=[];
const save=async name=>page.screenshot({path:'/evidence/'+name+'.png',fullPage:true});
try{
 server=await createServer({configFile:'vite.config.ts',server:{host:'127.0.0.1',port:5179,strictPort:true,proxy:{'/api':{target:'http://127.0.0.1:8800',changeOrigin:false}}}});
 await server.listen();browser=await chromium.launch({headless:true});
 const context=await browser.newContext({viewport:{width:1440,height:1100},acceptDownloads:true});
 page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 page.on('response',r=>{if(r.url().includes('/api/v1/')){events.push({path:new URL(r.url()).pathname,status:r.status(),method:r.request().method()});fs.writeFile('/evidence/browser-events.json',JSON.stringify(events,null,2)).catch(()=>{});}});
 await page.goto('http://127.0.0.1:5179/projects');
 await page.getByLabel('账号',{exact:true}).fill(config.login);
 await page.getByLabel('密码',{exact:true}).fill(config.password);
 await page.getByRole('button',{name:'登录工作区',exact:true}).click();
 await page.getByRole('button',{name:'退出登录',exact:true}).waitFor();
 const settingsResponse=await page.request.get('http://127.0.0.1:5179/api/v1/bids/'+config.bid+'/settings');
 assert.equal(settingsResponse.status(),200,'synthetic settings fixture must satisfy real SettingsView before upload');
 const settingsView=await settingsResponse.json();
 assert.equal(settingsView.settings.auto_review,false);
 for(const key of ['auto_review','consistency','scope_residue','show_prediction','pricing','result_reminder','accept_rules_as_written'])assert.equal(typeof settingsView.settings[key],'boolean');
 await fs.writeFile('/evidence/seed-precondition.json',JSON.stringify({settings_http_status:200,settings:settingsView.settings,creation_entry_tested:false},null,2));
 await page.goto('http://127.0.0.1:5179/bids/'+config.bid+'/materials');
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
 await save('word-converted-adopted');
 const originalDownload=context.waitForEvent('page',{timeout:1000}).catch(()=>null);
 // ArtifactActions initiates a browser download; verify the actual bytes on disk.
 const originalEvent=page.waitForEvent('download');
 await word.getByRole('button',{name:'下载Word资料原件',exact:true}).click();
 const original=await originalEvent;await original.saveAs('/evidence/download-original.docx');
 const pdfEvent=page.waitForEvent('download');
 await word.getByRole('button',{name:'下载此Word转换的PDF',exact:true}).click();
 const pdf=await pdfEvent;await pdf.saveAs('/evidence/download-converted.pdf');
 await originalDownload;
 assert.deepEqual(await fs.readFile('/evidence/download-original.docx'),Buffer.from(config.docx,'base64'));
 assert.ok((await fs.readFile('/evidence/download-converted.pdf')).subarray(0,5).equals(Buffer.from('%PDF-')));
 assert.ok(events.some(e=>e.method==='PUT'&&e.path.startsWith('/api/v1/')&&e.status===200),'same-origin API byte PUT missing');
 assert.ok(!events.some(e=>e.status>=500),'server error observed');
 assert.deepEqual(errors,[]);
 await fs.writeFile('/evidence/browser-result.json',JSON.stringify({passed:true,url:page.url(),same_origin_upload:true,word_conversion_complete:true,current_pdf_adopted:true,downloaded_original_exact:true,pdf_downloaded:true,events,page_errors:errors},null,2));
 console.log(JSON.stringify({passed:true,event_count:events.length}));
}catch(error){if(page)await save('word-failure').catch(()=>{});await fs.writeFile('/evidence/browser-failure.json',JSON.stringify({type:error.name,message:error.message,events,page_errors:errors},null,2));throw error;}
finally{if(browser)await browser.close();if(server)await server.close();}
