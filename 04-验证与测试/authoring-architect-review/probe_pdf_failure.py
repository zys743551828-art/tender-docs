"""A2候选02损坏PDF处理边界；实际PDFium+显式转换器替身，不访问对象。"""
import json
from pathlib import Path
import sys
from unittest.mock import patch
from uuid import uuid4

HERE=Path(__file__).resolve().parent
APP=Path('/workspace/tender-workbench')
sys.path.insert(0,str(APP))
sys.path.insert(0,str(HERE/'candidate02-observed-inputs'))


def main():
    original=Path.read_text
    def read(path,*args,**kwargs):
        return '' if path.resolve()==APP/'.env' else original(path,*args,**kwargs)
    with patch.object(Path,'read_text',read):
        from packages.authoring import build,build_repository
        from packages.authoring.content import empty_content
    class TruncatedPdfConverter:
        def __init__(self,command,**kwargs):
            target=Path(command[command.index('--outdir')+1])/'body.pdf'
            target.write_bytes(b'%PDF-1.7\nexplicit synthetic truncated output\n')
            self.returncode=0
        def __enter__(self):return self
        def __exit__(self,*args):return False
        def communicate(self,timeout=None):return b'',b''
    snapshot={'snapshot_digest':'a'*64,'sections':[empty_content('合成正文',0)]}
    prepared={'input_id':str(uuid4()),'info':{'draft_snapshot_id':str(uuid4()),'manifest':{'writer_digest':build.WRITER_DIGEST,'formats':['docx','pdf'],'renderer_identity':{}}},'snapshot':snapshot}
    with patch.object(build,'render_identity',return_value={}),patch.object(build.subprocess,'Popen',TruncatedPdfConverter),patch.object(build_repository.storage,'put',side_effect=AssertionError('No object network allowed')):
        try:
            result=build_repository.compute_prepared_build(prepared)
            observed={'returned':True,'outputs':result['outputs']}
        except Exception as error:
            observed={'returned':False,'exception_type':type(error).__name__,'exception_is_runtime_error':isinstance(error,RuntimeError),'docx_output_returned':False}
    output={'scope':'candidate02 error handling with actual PDFium and synthetic converter output; no real LO quality claim','candidate_sha256':json.loads((HERE/'candidate02-inputs.json').read_text())['candidate_sha256'],'observed':observed,'paid_calls':0,'real_object_requests':0,'credentials_read':False}
    (HERE/'candidate02-pdf-failure-counterexample.json').write_text(json.dumps(output,indent=2)+'\n');print(json.dumps(output))


if __name__=='__main__':main()
