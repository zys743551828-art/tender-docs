"""只读传输布局；跨浏览器从真实数据库恢复，绝不暴露原生UploadId或locator。"""

from apps.api.views import upload_row
from packages.domain.runtime import clean


def layout_view(c, upload_id):
    row = upload_row(c, upload_id)
    return clean(
        {
            "upload_id": row["id"],
            "bid_id": row["bid_id"],
            "mode": row["mode"],
            "part_size": row["part_size"],
            "part_count": row["part_count"],
            "byte_size": row["declared_size"],
            "declared_sha256": row["declared_digest"],
            "revision": row["revision"],
            "state": "aborted" if row["aborted_at"] else row["state"],
            "session_expires_at": row["expires_at"],
        }
    )
