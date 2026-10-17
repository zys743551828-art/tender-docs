"""可信选入身份机制，显式材料夹具；尚非共享上传/渲染整链验收。"""

import os
from uuid import uuid4

import pytest
from psycopg.types.json import Jsonb

from packages.domain import word_materials
from packages.domain.runtime import DomainError, connection, fetch, sha
from tests.test_word_intake import make_docx
from tests.test_word_intake_integration import disabled, original
from tests.test_word_intake_integration import world as world

pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_WORD_MATERIALS_INTEGRATION") != "1", reason="Word fresh库的受限PG机制"
)


def select_original(world, word):  # noqa: F811
    bid = world["bid"]
    with connection() as c:
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (bid,))
        c.execute(
            "INSERT INTO document_file_versions(bid_id,file_id,version_id,revision,previous_version_id) VALUES(%s,%s,%s,1,NULL)",
            (bid, word["file"], word["version"]),
        )
        c.execute(
            "UPDATE document_files SET head_version_id=%s,revision=2 WHERE id=%s",
            (word["version"], word["file"]),
        )
        material = uuid4()
        c.execute(
            "INSERT INTO material_snapshots(id,bid_id,revision,manifest,digest) VALUES(%s,%s,1,%s,%s)",
            (material, bid, Jsonb({"synthetic": True}), sha(str(material))),
        )
        c.execute(
            "INSERT INTO material_assignments(snapshot_id,bid_id,document_version_id,role,ordinal) VALUES(%s,%s,%s,'body',0)",
            (material, bid, word["version"]),
        )
        c.execute(
            "UPDATE bid_profiles SET current_material_id=%s,current_body_id=NULL WHERE bid_id=%s",
            (material, bid),
        )
        c.execute("UPDATE bids SET material_revision=1 WHERE id=%s", (bid,))
        command = fetch(
            c,
            "SELECT command_id FROM operations WHERE id=%s",
            (word["published"]["conversion_operation_id"],),
        )["command_id"]
        chosen = word_materials.record_selection(c, bid, word["version"], world["actor"], command)
    return chosen


def test_pending_word_identity_is_current_material_not_parallel_inventory(world):  # noqa: F811
    word = original(world, make_docx("Trusted pending Word selection"), profile=disabled())
    chosen = select_original(world, word)
    with connection() as c:
        pending = word_materials.pending_body(c, world["bid"])
        assert pending["document_version_id"] == word["version"]
        assert chosen["current_body_id"] is None
        assert word_materials.selection(c, world["bid"], word["version"])["payload"][
            "context_digest"
        ] == sha(chosen)
        assert word_materials.candidate(c, world["bid"], word["version"]) is None


def test_old_selection_cannot_cover_new_material_or_closed_project(world):  # noqa: F811
    word = original(world, make_docx("Selection invalidation boundaries"), profile=disabled())
    chosen = select_original(world, word)
    with connection() as c:
        c.execute("SELECT id FROM bids WHERE id=%s FOR UPDATE", (world["bid"],))
        c.execute(
            "UPDATE bids SET material_revision=material_revision+1 WHERE id=%s", (world["bid"],)
        )
        assert sha(word_materials.context(c, world["bid"], word["version"])) != sha(chosen)
        c.execute("UPDATE bid_profiles SET phase='terminated' WHERE bid_id=%s", (world["bid"],))
        with pytest.raises(DomainError) as blocked:
            word_materials.record_selection(c, world["bid"], word["version"], world["actor"], None)
        assert blocked.value.code == "PROJECT_WORK_CLOSED"
