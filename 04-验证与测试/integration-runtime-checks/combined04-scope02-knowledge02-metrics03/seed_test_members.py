"""Seed two isolated integration members; save secrets only in ignored private config."""

import json
import os
import secrets
from pathlib import Path

from apps.api.auth import password_hash
from packages.domain.runtime import assert_role, connection, uid

destination = Path('/workspace/tender-workbench/.local/combined_delivery01-test-members.json')
if destination.exists():
    raise SystemExit('Private member receipt already exists; refusing to duplicate.')
assert_role()
members = []
with connection() as c:
    assert c.execute('SELECT count(*) AS n FROM members').fetchone()['n'] == 0
    for number in (1, 2):
        member = uid()
        login = 'combined-qa-' + str(number) + '-' + secrets.token_hex(4)
        password = secrets.token_urlsafe(30)
        c.execute('INSERT INTO members(id,display_name) VALUES(%s,%s)',
                  (member, '组合验收成员' + str(number)))
        c.execute('INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)',
                  (member, login, password_hash(password)))
        members.append({'id': str(member), 'login': login, 'password': password})
    fd = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(members, stream)
print('Created two isolated integration members; private receipt saved without printing credentials.')
