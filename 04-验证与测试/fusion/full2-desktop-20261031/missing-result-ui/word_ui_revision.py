import datetime, hashlib, json, os, subprocess, pathlib, sys
ROOT=pathlib.Path(__file__).resolve().parent
APP=ROOT/'tender-app-dev/workbench'
WEB=APP/'apps/web'
OUT=ROOT/'evidence-local-20261030/word-missing-result-ui-20261031'
OUT.mkdir(exist_ok=True)
def inventory(root):
 return [{'path':p.relative_to(root).as_posix(),'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'bytes':p.stat().st_size} for p in sorted(root.rglob('*')) if p.is_file()]
def main():
 env={k:v for k,v in os.environ.items() if k.upper() in ['PATH','SYSTEMROOT','WINDIR','TEMP','TMP','USERPROFILE','APPDATA','LOCALAPPDATA']}
 env['WORKBENCH_ENV_FILE']=str(OUT/'explicit-absent.env')
 node=r'D:\tools\nodejs22\node-v22.23.2-win-x64\node.exe'
 before=inventory(WEB/'dist'); (OUT/'active-dist-before.json').write_text(json.dumps(before,indent=2))
 config=OUT/'vitest.config.mjs'
 config.write_text('import react from '+json.dumps((WEB/'node_modules/@vitejs/plugin-react/dist/index.mjs').as_uri())+';\nexport default '+json.dumps({'root':str(WEB),'envDir':False,'test':{'environment':'jsdom','setupFiles':[str(WEB/'src/test-setup.ts')],'clearMocks':True}}).replace('};','}')+';\n',encoding='utf-8')
 # React JSX plugin assigned separately so the config contains no env loader.
 config.write_text(config.read_text().replace('export default ','const config = ')+ '\nconfig.plugins=[react()];export default config;\n',encoding='utf-8')
 results={}
 for name,args in [('focused',[str(WEB/'node_modules/vitest/vitest.mjs'),'run','src/WordOriginals.test.tsx','--config',str(config),'--reporter=default','--reporter=junit','--outputFile.junit='+str(OUT/'junit.xml')]),('prettier',[str(WEB/'node_modules/prettier/bin/prettier.cjs'),'--check','src/WordOriginals.tsx','src/WordOriginals.test.tsx']),('eslint',[str(WEB/'node_modules/eslint/bin/eslint.js'),'src/WordOriginals.tsx','src/WordOriginals.test.tsx'])]:
  with (OUT/(name+'.log')).open('w',encoding='utf-8') as log: results[name]=subprocess.run([node,*args],cwd=WEB,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=180).returncode
 print(json.dumps(results),flush=True)
 (OUT/'focused-summary.json').write_text(json.dumps(results,indent=2))
 assert not any(results.values()),results
 env['DOCKER_HOST']='npipe:////./pipe/dockerDesktopLinuxEngine';env['DOCKER_CONFIG']=str(OUT/'.docker');pathlib.Path(env['DOCKER_CONFIG']).mkdir(exist_ok=True)
 command=[r'D:\Docker\Docker\resources\cli-plugins\docker-buildx.exe','build','--builder','default','--platform','linux/amd64','--file',str(APP/'deploy/Dockerfile.web-build'),'--output','type=local,dest='+str(OUT/'dist'),'--progress','plain','--provenance=false',str(WEB)]
 start=datetime.datetime.now(datetime.timezone.utc).isoformat()
 with (OUT/'build.log').open('w',encoding='utf-8') as log: rc=subprocess.run(command,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=900).returncode
 summary={'start':start,'end':datetime.datetime.now(datetime.timezone.utc).isoformat(),'exit':rc,'command':command,'source':{name:hashlib.sha256((WEB/'src'/name).read_bytes()).hexdigest() for name in ['WordOriginals.tsx','WordOriginals.test.tsx']},'recipe_sha256':hashlib.sha256((APP/'deploy/Dockerfile.web-build').read_bytes()).hexdigest(),'active_dist_count':len(before),'active_dist_unchanged':before==inventory(WEB/'dist'),'files':inventory(OUT/'dist')}
 (OUT/'build-summary.json').write_text(json.dumps(summary,indent=2))
 print(json.dumps({k:v for k,v in summary.items() if k!='files'}),flush=True)
 assert rc==0 and summary['active_dist_unchanged']
if __name__=='__main__':main()
