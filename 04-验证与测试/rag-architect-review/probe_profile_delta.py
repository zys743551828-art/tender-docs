"""本地模型身份整改delta：合成资产/加载替身，不作真实推理。"""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType,SimpleNamespace
from unittest.mock import patch

HERE=Path(__file__).resolve().parent
sys.path.insert(0,str(HERE/'delta-observed-inputs'))


def main():
    torch=ModuleType('torch');transformers=ModuleType('transformers');loads=[]
    class Loader:
        @staticmethod
        def from_pretrained(directory,**kwargs):
            loads.append(kwargs)
            config=SimpleNamespace(**json.loads((Path(directory)/'config.json').read_text()))
            return SimpleNamespace(config=config,eval=lambda:SimpleNamespace(config=config))
    class Tokenizer:
        @staticmethod
        def from_pretrained(directory,**kwargs):return SimpleNamespace(is_fast=True)
    transformers.AutoModel=Loader
    transformers.AutoModelForSequenceClassification=Loader
    transformers.AutoTokenizer=Tokenizer
    def version(name):
        assert name in ('torch','transformers')
        return 'explicit-fixture-version'
    with tempfile.TemporaryDirectory() as directory,patch.dict(sys.modules,{'torch':torch,'transformers':transformers}),patch('importlib.metadata.version',version):
        from packages.rag.local_models import BgeDense,BgeReranker
        def assets(name,changed=None,reranker=False):
            root=Path(directory)/name;root.mkdir()
            weight='model.safetensors' if reranker else 'pytorch_model.bin'
            files={}
            for filename in ('config.json','tokenizer.json','tokenizer_config.json','sentencepiece.bpe.model','special_tokens_map.json',weight):
                body=(json.dumps({'hidden_size':1024,'layer_norm_eps':1e-3 if changed=='config.json' else 1e-5}).encode()
                    if filename=='config.json' else b'explicit fixture '+(b'changed' if changed==filename else b'base'))
                (root/filename).write_bytes(body)
                files[filename]={'sha256':hashlib.sha256(body).hexdigest(),'size':len(body)}
            (root/'asset-manifest.json').write_text(json.dumps({'model':'BAAI/bge-reranker-v2-m3' if reranker else 'BAAI/bge-m3','revision':'1'*40,'files':files}))
            return root
        base=BgeDense(assets('base'));changed={}
        for name in ('config.json','tokenizer.json','tokenizer_config.json','sentencepiece.bpe.model','special_tokens_map.json'):
            changed[name]=BgeDense(assets(name,name)).identity != base.identity
        extra=assets('extra');(extra/'model.safetensors').write_bytes(b'unverified fixture')
        try:BgeDense(extra);raise AssertionError('Extra unverified weight accepted')
        except ValueError:pass
        BgeReranker(assets('rerank',reranker=True))
        assert all(changed.values())
        assert all(call.get('use_safetensors') is False and call.get('weights_only') is True for call in loads[:-1])
        assert loads[-1].get('use_safetensors') is True
        assert all(call.get('local_files_only') is True and call.get('trust_remote_code') is False for call in loads)
        output={'scope':'unfrozen delta profile mechanism; fake assets/loaders, no real inference','source_sha256':hashlib.sha256((HERE/'delta-observed-inputs/packages/rag/local_models.py').read_bytes()).hexdigest(),'each_asset_change_changes_profile':changed,'unverified_extra_weight_rejected':True,'dense_bin_weights_only_and_rerank_safetensors_forced':True,'local_only_and_no_remote_code':True,'paid_calls':0,'real_model_inference':False,'credentials_read':False,'final_pass':False}
        (HERE/'delta-profile-result.json').write_text(json.dumps(output,indent=2)+'\n');print(json.dumps(output))


if __name__=='__main__':main()
