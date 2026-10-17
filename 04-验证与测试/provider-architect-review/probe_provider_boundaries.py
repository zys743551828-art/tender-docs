"""架构师独立合成反例；不读取私有配置、不访问网络、不付费。"""

import hashlib
import json
import os
import pathlib
import sys
import tempfile
from unittest.mock import patch

import httpx

ROOT = pathlib.Path('/workspace/tender-workbench')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'deploy'))

from configure_external import configure
from initialize_database import file_configuration
from packages.domain.providers.deepseek import DeepSeekClient
from packages.domain.providers.oss import OssClient
from packages.domain.runtime import DomainError


def profile_probe():
    values = {
        'DEEPSEEK_API_KEY': 'synthetic-only',
        'DEEPSEEK_BASE_URL': 'https://api.deepseek.com/v1',
        'DEEPSEEK_MODEL': 'deepseek-flash',
        'OBJECT_STORE_BACKEND': 'oss',
        'OBJECT_STORE_ENDPOINT': 'https://oss-cn-hangzhou.aliyuncs.com',
        'OBJECT_STORE_BUCKET': 'tender-base',
        'OBJECT_STORE_REGION': 'cn-hangzhou',
        'OBJECT_STORE_ACCESS_KEY': 'synthetic-only',
        'OBJECT_STORE_SECRET_KEY': 'synthetic-only',
        'OBJECT_STORE_BUCKET_URL': 'https://tender-base.oss-cn-hangzhou.aliyuncs.com',
    }
    with tempfile.TemporaryDirectory() as temp:
        source = pathlib.Path(temp) / 'providers.env'
        output = pathlib.Path(temp) / 'external.env'
        source.write_text(''.join(f'{key}={value}\n' for key, value in values.items()))
        configure(source, output)
        generated = file_configuration(output)
        with patch.dict(os.environ, generated, clear=True):
            try:
                client = OssClient.from_env()
                outcome = 'constructed_without_network'
            except DomainError as error:
                client = None
                outcome = error.code
        return {
            'input_has_bucket_url': True,
            'output_has_bucket_url': 'OBJECT_STORE_BUCKET_URL' in generated,
            'oss_client_result': outcome,
            'configuration_mode': oct(output.stat().st_mode & 0o777),
            'independent_ports': generated.get('POSTGRES_PORT') == '55441'
            and generated.get('KAFKA_EXTERNAL_PORT') == '19095',
            'prefix_isolated': bool(client and client.prefix.startswith('workbench-environments/')),
        }


def schema_probe():
    schema = {
        'type': 'object',
        '$defs': {'truth': {'const': True}},
        'properties': {
            'ok': {'$id': 'https://synthetic.invalid/nested', '$ref': '#/$defs/truth'}
        },
    }
    seen = []

    def handler(request):
        seen.append(True)
        return httpx.Response(200, json={
            'id': 'synthetic-response', 'model': 'deepseek-flash',
            'choices': [{'message': {'role': 'assistant', 'content': '{"ok":true}'},
                         'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},
        })

    try:
        DeepSeekClient(api_key='synthetic-only', transport=httpx.MockTransport(handler)).generate(
            messages=[{'role': 'user', 'content': 'synthetic'}], json_schema=schema)
        result = {'outcome': 'accepted'}
    except Exception as error:
        result = {
            'outcome': 'exception', 'exception_type': type(error).__name__,
            'domain_error': isinstance(error, DomainError), 'code': getattr(error, 'code', None),
            'billing_state': getattr(error, 'details', {}).get('billing_state'),
        }
    return {**result, 'schema': schema, 'mock_external_attempts': len(seen)}


def runtime_reference_failure_probe():
    from jsonschema import Draft202012Validator
    from jsonschema.exceptions import _WrappedReferencingError
    from referencing.exceptions import Unresolvable

    seen = []
    marker = 'synthetic-private-schema-marker'

    def handler(request):
        seen.append(True)
        return httpx.Response(200, json={
            'id': 'synthetic-response', 'model': 'deepseek-flash',
            'choices': [{'message': {'role': 'assistant', 'content': '{"ok":true}'},
                         'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 1, 'completion_tokens': 1, 'total_tokens': 2},
        })

    with patch.object(Draft202012Validator, 'is_valid', side_effect=
                      _WrappedReferencingError(Unresolvable(ref=marker))):
        try:
            DeepSeekClient(api_key='synthetic-only', transport=httpx.MockTransport(handler)).generate(
                messages=[{'role': 'user', 'content': 'synthetic'}],
                json_schema={'type': 'object'})
            result = {'outcome': 'accepted'}
        except Exception as error:
            details = getattr(error, 'details', {})
            result = {
                'exception_type': type(error).__name__,
                'domain_error': isinstance(error, DomainError),
                'code': getattr(error, 'code', None),
                'billing_state': details.get('billing_state'),
                'usage': details.get('usage'),
                'retryable': getattr(error, 'retryable', None),
                'marker_not_leaked': marker not in str(error) + str(details),
            }
    return {**result, 'mock_external_attempts': len(seen)}


if __name__ == '__main__':
    result = {
        'scope': 'synthetic deployment/Schema boundaries; no real provider validation',
        'credentials_read': False, 'real_network_requests': 0, 'paid_requests': 0,
        'inputs': {
            name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest()
            for name in ('deploy/configure_external.py', 'packages/domain/providers/deepseek.py')
        },
        'external_profile': profile_probe(), 'nested_schema_resource': schema_probe(),
        'runtime_reference_failure': runtime_reference_failure_probe(),
    }
    target = pathlib.Path(sys.argv[1])
    target.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False))
