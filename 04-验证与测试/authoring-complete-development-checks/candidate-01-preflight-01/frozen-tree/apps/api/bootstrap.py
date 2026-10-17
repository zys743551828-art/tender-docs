"""在已初始化的新库创建成员，口令只从终端/标准输入读取。"""

import argparse
import getpass
import sys

from apps.api.auth import password_hash
from packages.domain.runtime import assert_role, connection, uid


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--login", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--password-stdin", action="store_true")
    a = p.parse_args()
    password = (
        sys.stdin.readline().rstrip("\n") if a.password_stdin else getpass.getpass("成员口令: ")
    )
    if len(password) < 12:
        raise SystemExit("口令至少12字符")
    assert_role()
    with connection() as c:
        member = uid()
        c.execute("INSERT INTO members(id,display_name) VALUES(%s,%s)", (member, a.name))
        c.execute(
            "INSERT INTO credentials(member_id,login_name,password_hash) VALUES(%s,%s,%s)",
            (member, a.login, password_hash(password)),
        )
    print("成员已创建")


if __name__ == "__main__":
    main()
