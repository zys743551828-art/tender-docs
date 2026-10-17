"""传输仅检查服务器文件格式与前缀，完整OOXML检查由核验Worker执行。"""

from packages.domain.runtime import DomainError
from packages.word_intake.inspection import DOCX, filename_type


def check_prefix(filename, data):
    expected = b"PK\x03\x04" if filename_type(filename) == DOCX else b"%PDF-"
    if not data.startswith(expected):
        raise DomainError("UPLOAD_FORMAT_MISMATCH", "实际原件格式与文件名不一致", 415)
