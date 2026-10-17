"""Two new fixture-only login members; passwords are written only to a private local file."""
import json
import os
from pathlib import Path
import secrets

from apps.api.auth import password_hash
from packages.domain.runtime import assert_role,connection,uid

path=Path('/workspace/tender-workbench/.local/assistant_ui01-test-members.json')
assert not path.exists()
assert_role()
members=[{'id':str(uid()),'login':'fixture-'+secrets.token_hex(8),'password':secrets.token_urlsafe(24)} for _ in range(2)]
with connection() as c:
    assert c.execute('SELECT current_database() AS name').fetchone()['name']=='tender_checks_assistant_ui01'
    for index,member in enumerate(members):
        c.execute('INSERT INTO members(id,display_name) VALUES(%s,%s)',(member['id'],f'助手界面夹具成员{index+1}'))
        c.execute('INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)',(member['id'],member['login'],password_hash(member['password'])))
fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
with os.fdopen(fd,'w') as stream:json.dump(members,stream)
Path(__file__).with_name('members-created.json').write_text(json.dumps({'count':2,'new_fixture_members_only':True,'private_member_file':str(path),'passwords_printed':False,'provider_calls':0},indent=2)+'\n')
print({'new_fixture_members':2,'private_member_file':str(path),'credentials_printed':False})
