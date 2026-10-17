"""按固定清单取回官方资料并核验摘要；不执行下载内容、不改写清单。"""
import argparse
import hashlib
import json
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE=Path(__file__).resolve().parent


def fetch(source,file,cache):
    path=cache/source['id']/file['path']
    expected=file['sha256']
    if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==expected:
        return {'source':source['id'],'path':file['path'],'status':'verified_cache'}
    request=urllib.request.Request(file['url'],headers={'User-Agent':'tender-architecture-evidence/1.0'})
    try:
        with urllib.request.urlopen(request,timeout=30) as response:
            body=response.read(3_000_001)
            if response.status!=200 or len(body)>3_000_000:
                raise ValueError('response status/size invalid')
        if hashlib.sha256(body).hexdigest()!=expected:
            raise ValueError('source digest mismatch')
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_bytes(body)
        return {'source':source['id'],'path':file['path'],'status':'downloaded_verified'}
    except Exception as error:
        return {'source':source['id'],'path':file['path'],'status':'failed','error':type(error).__name__+': '+str(error)[:180]}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',type=Path,required=True)
    parser.add_argument('--manifest',type=Path,default=HERE/'official-sources.json')
    args=parser.parse_args()
    manifest=json.loads(args.manifest.read_text())
    if 'sources' in manifest:
        jobs=[(source,file,args.cache) for source in manifest['sources'] for file in source['files'] if file.get('status')==200]
    else:
        jobs=[({'id':file['source']},file,args.cache) for file in manifest['files'] if file.get('status')==200]
    with ThreadPoolExecutor(max_workers=5) as pool:
        results=list(pool.map(lambda job:fetch(*job),jobs))
    failures=[r for r in results if r['status']=='failed']
    print(json.dumps({'requested':len(jobs),'verified_cache':sum(r['status']=='verified_cache' for r in results),'downloaded_verified':sum(r['status']=='downloaded_verified' for r in results),'failures':failures},ensure_ascii=False,indent=2))
    raise SystemExit(int(bool(failures)))
