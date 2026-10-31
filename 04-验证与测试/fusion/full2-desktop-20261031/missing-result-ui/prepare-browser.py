import hashlib,io,json,os,pathlib,shutil,subprocess,tarfile,urllib.request,zipfile
from concurrent.futures import ThreadPoolExecutor
pathlib.Path("/work").mkdir(exist_ok=True)
def run(args,log,timeout=420,env=None):
    with open('/evidence/'+log,'wb') as output:
        p=subprocess.run(args,stdout=output,stderr=subprocess.STDOUT,timeout=timeout,env=env)
    if p.returncode: raise RuntimeError('PREPARATION_FAILED:'+log)
for source in pathlib.Path('/etc/apt/sources.list.d').glob('*'):
    if source.is_file(): source.write_text(source.read_text().replace('http://deb.debian.org','https://deb.debian.org'))
node_name='node-v22.23.2-linux-x64.tar.xz'
with urllib.request.urlopen('https://nodejs.org/dist/v22.23.2/SHASUMS256.txt',timeout=30) as response:
    sums=response.read().decode()
expected=next(line.split()[0] for line in sums.splitlines() if line.split()[-1]==node_name)
node_archive=pathlib.Path('/work')/node_name
urllib.request.urlretrieve('https://nodejs.org/dist/v22.23.2/'+node_name,node_archive)
assert hashlib.sha256(node_archive.read_bytes()).hexdigest()==expected
with tarfile.open(node_archive) as stream: stream.extractall('/work',filter='data')
os.symlink('/work/node-v22.23.2-linux-x64/bin/node','/usr/local/bin/node')
os.symlink('/work/node-v22.23.2-linux-x64/lib/node_modules/npm/bin/npm-cli.js','/usr/local/bin/npm')
run(['apt-get','update'],'apt-update.log')
run(['npm','install','--prefix','/work/browser','--ignore-scripts','--no-audit','--no-fund','playwright@1.51.1'],'npm-browser.log',600)
env=os.environ|{'PLAYWRIGHT_BROWSERS_PATH':'/work/browsers'}
run(['/work/browser/node_modules/.bin/playwright','install-deps','chromium'],'browser-dependencies.log',600,env)
browser_url='https://playwright.download.prss.microsoft.com/dbazure/download/playwright/builds/chromium/1161/chromium-headless-shell-linux.zip'
browser_size=105788305
def fetch(index):
    start=browser_size*index//8; end=browser_size*(index+1)//8-1
    request=urllib.request.Request(browser_url,headers={'Range':f'bytes={start}-{end}'})
    with urllib.request.urlopen(request,timeout=90) as response:
        assert response.status==206 and response.headers['Content-Range']==f'bytes {start}-{end}/{browser_size}'
        data=response.read()
    assert len(data)==end-start+1
    return data
with ThreadPoolExecutor(max_workers=8) as pool: browser_data=b''.join(pool.map(fetch,range(8)))
browser_root=pathlib.Path('/work/browsers/chromium_headless_shell-1161')
browser_root.mkdir(parents=True)
with zipfile.ZipFile(io.BytesIO(browser_data)) as bundle:
    assert bundle.testzip() is None
    for item in bundle.infolist():
        destination=browser_root/item.filename
        assert browser_root.resolve() in destination.resolve().parents
        bundle.extract(item,browser_root)
        mode=item.external_attr>>16
        if mode and not item.is_dir(): os.chmod(destination,mode&0o777)
(browser_root/'INSTALLATION_COMPLETE').touch()
browser_executable=browser_root/'chrome-linux/headless_shell'
assert browser_executable.is_file()
browser_version=subprocess.check_output([str(browser_executable),'--version']).decode().strip()
assert '134.0.6998.35' in browser_version
pathlib.Path('/evidence/browser-download.json').write_text(json.dumps({'url':browser_url,'bytes':len(browser_data),'sha256':hashlib.sha256(browser_data).hexdigest(),'zip_crc_verified':True,'parallel_ranges':8,'version':browser_version},indent=2))