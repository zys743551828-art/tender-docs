"""工作写入与显式结束串行化；旧命令重放不产生新工作。"""

from packages.domain.runtime import Command, require_read_committed
from packages.lifecycle.service import assert_batch_open, assert_work_writable, lock


def begin(c, actor, kind, target, key, body, condition, *, bid, batch=None, work=True):
    require_read_committed(c)
    lock(c, bid)
    command = Command(c, actor, kind, target, key, body, condition)
    if not command.replay and work:
        assert_work_writable(c, bid)
        if batch is not None:
            assert_batch_open(c, bid, batch)
    return command
