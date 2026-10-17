"""全合成资产与显式加载替身，仅检查真实草稿的profile身份计算。"""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / 'draft-observed-inputs'))


def main():
    torch = ModuleType('torch')
    transformers = ModuleType('transformers')
    class Model:
        def __init__(self, config):
            self.config = SimpleNamespace(**config)
        def eval(self):
            return self
    class AutoModel:
        @staticmethod
        def from_pretrained(directory, **kwargs):
            return Model(json.loads((Path(directory) / 'config.json').read_text()))
    class AutoTokenizer:
        @staticmethod
        def from_pretrained(directory, **kwargs):
            return SimpleNamespace(is_fast=True)
    transformers.AutoModel = AutoModel
    transformers.AutoTokenizer = AutoTokenizer
    with tempfile.TemporaryDirectory() as directory, patch.dict(sys.modules, {'torch':torch,'transformers':transformers}):
        from packages.rag.local_models import BgeDense
        models=[]
        for index, epsilon in enumerate((1e-5,1e-3)):
            root = Path(directory) / str(index)
            root.mkdir()
            bodies = {
                'config.json': json.dumps({'hidden_size':1024,'layer_norm_eps':epsilon}).encode(),
                'tokenizer.json': b'{}', 'tokenizer_config.json':b'{}',
                'pytorch_model.bin':b'explicit synthetic mechanism fixture, not model weights',
            }
            files={}
            for name, body in bodies.items():
                (root/name).write_bytes(body)
                files[name]={'sha256':hashlib.sha256(body).hexdigest(),'size':len(body)}
            (root/'asset-manifest.json').write_text(json.dumps({
                'model':'BAAI/bge-m3','revision':'1'*40,'files':files}))
            models.append(BgeDense(root))
        result={
            'scope':'profile mechanism only; explicit fake loader/assets; no real embedding inference',
            'source_path':str(HERE/'draft-observed-inputs/packages/rag/local_models.py'),
            'source_sha256':hashlib.sha256((HERE/'draft-observed-inputs/packages/rag/local_models.py').read_bytes()).hexdigest(),
            'config_file_digests_differ':models[0].manifest['files']['config.json']['sha256'] != models[1].manifest['files']['config.json']['sha256'],
            'loaded_config_epsilons':[model.model.config.layer_norm_eps for model in models],
            'embedding_identities_equal':models[0].identity == models[1].identity,
            'real_model_loads':0,'network_calls':0,'credentials_read':False,
        }
        assert result['config_file_digests_differ'] and result['embedding_identities_equal']
        (HERE/'draft-profile-identity-counterexample.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result))


if __name__ == '__main__':
    main()
