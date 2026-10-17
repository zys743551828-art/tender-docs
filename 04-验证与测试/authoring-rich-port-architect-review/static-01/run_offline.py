import os,sys
from pathlib import Path
root=Path("/workspace/tender-docs/04-验证与测试/authoring-complete-development-checks/rich-port-static-01/frozen-tree")
sys.path.insert(0,str(root))
os.environ["WORKBENCH_ENV_FILE"]="/nonexistent/architect-rich-no-private-profile"
import requests,httpx,psycopg,subprocess
def deny(*args,**kwargs): raise AssertionError("Architect rich static run forbids DB/HTTP/subprocess")
requests.sessions.Session.request=deny
httpx.HTTPTransport.handle_request=deny
httpx.AsyncHTTPTransport.handle_async_request=deny
psycopg.connect=deny
subprocess.Popen=deny
subprocess.run=deny
from packages.authoring import rich_renderer
rich_renderer.sandbox._docker=deny
from packages.domain.providers.deepseek import DeepSeekClient
DeepSeekClient.from_env=deny
import pytest
raise SystemExit(pytest.main(["-c","/dev/null","--rootdir",str(root),"-p","no:cacheprovider","--assert=plain","--junitxml=/workspace/tender-docs/04-验证与测试/authoring-rich-port-architect-review/static-01/offline-junit.xml",str(root/"tests/test_authoring_rich.py"),str(root/"tests/test_authoring_rich_renderer.py"),str(root/"tests/test_authoring_page_navigation.py"),"-q"]))
