"""本地解析端口身份；只在配置后选择新处理器，不改写已派发的旧任务。"""

import os

from packages.domain.runtime import DomainError
from packages.parsing.http_port import ParsingHttp, ParsingUnavailable


def configured_parser():
    if not os.environ.get("PARSING_RUNTIME_TOKEN"):
        return None
    try:
        return ParsingHttp()
    except ParsingUnavailable as error:
        raise DomainError(
            "PARSING_RUNTIME_UNAVAILABLE",
            "解析服务暂不可用；原件和未完成范围已保留",
            503,
            retryable=True,
        ) from error


def profile(parser):
    return {
        "runtime": "local-parsing/1",
        "parser_digest": parser.parser_digest,
        "config_digest": parser.config_digest,
    }
