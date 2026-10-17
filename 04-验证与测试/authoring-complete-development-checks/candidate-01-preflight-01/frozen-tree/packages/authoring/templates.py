"""Immutable directory/layout template catalog. API role only; no model or renderer I/O."""

from uuid import NAMESPACE_URL, UUID, uuid5

from psycopg.types.json import Jsonb

from packages.authoring.structure_model import node, validate_tree
from packages.authoring.structure_schema import OUTLINE, STYLE, validate
from packages.domain.runtime import DomainError, clean, etag, fetch, match, sha, uid

DEFAULT_OUTLINE = {
    "schema_version": 1,
    "nodes": [
        {
            "key": "technical",
            "parent_key": None,
            "ordinal": 0,
            "title": "技术方案",
            "node_type": "chapter",
        }
    ],
}
DEFAULT_STYLE = {
    "schema_version": 1,
    "page": "A4",
    "font": "Noto Sans CJK SC",
    "font_size_halfpoints": 22,
    "margin_top_twips": 1440,
    "margin_right_twips": 1440,
    "margin_bottom_twips": 1440,
    "margin_left_twips": 1440,
    "paragraph_after_twips": 0,
    "cover_enabled": False,
    "cover_title": "投标正文",
    "header_text": "",
    "footer_text": "",
    "page_number": True,
    "chapter_numbering": "decimal",
    "toc_enabled": True,
}
DEFAULT_IDS = {
    kind: uuid5(
        NAMESPACE_URL, "tender-workbench/authoring-complete/template/" + kind + "/" + sha(config)
    )
    for kind, config in [("outline", DEFAULT_OUTLINE), ("layout", DEFAULT_STYLE)]
}


def require_schema(c):
    names = (
        "authoring_template_families",
        "authoring_template_links",
        "authoring_plan_versions",
        "authoring_structure_versions",
        "authoring_structure_nodes",
    )
    for name in names:
        if c.execute("SELECT to_regclass(%s) AS id", ("design." + name,)).fetchone()["id"] is None:
            raise DomainError(
                "AUTHORING_STRUCTURE_NOT_READY", "当前环境尚未初始化章节树/计划/双模板存储", 503
            )


def checked_config(kind, config):
    validate(kind, config)
    if kind == "outline":
        keys = [n["key"] for n in config["nodes"]]
        if not keys or len(set(keys)) != len(keys):
            raise DomainError("AUTHORING_TEMPLATE_INVALID", "目录模板需要不重复的稳定结构key", 422)
        ids = {key: str(uuid5(NAMESPACE_URL, "authoring-outline-key:" + key)) for key in keys}
        nodes = []
        for value in config["nodes"]:
            if value["parent_key"] is not None and value["parent_key"] not in ids:
                raise DomainError("AUTHORING_TEMPLATE_INVALID", "目录模板父key不属于本版本", 422)
            nodes.append(
                node(
                    ids[value["key"]],
                    parent=ids.get(value["parent_key"]),
                    ordinal=value["ordinal"],
                    kind=value["node_type"],
                )
            )
        validate_tree(nodes)
    return config


def config_schema(kind):
    return sha(OUTLINE if kind == "outline" else STYLE)


def insert_version(c, family, kind, name, revision, previous, config, actor, version=None):
    checked_config(kind, config)
    version = version or uid()
    c.execute(
        "INSERT INTO template_versions(id,name,kind,schema_digest,config,fontset_digest) VALUES(%s,%s,%s,%s,%s,%s)",
        (
            version,
            name,
            kind,
            config_schema(kind),
            Jsonb(config),
            sha({"font": config.get("font"), "source": "freeze_actual_font_file_per_build"}),
        ),
    )
    c.execute(
        "INSERT INTO authoring_template_links(family_id,revision,template_version_id,previous_revision,previous_version_id,created_by) VALUES(%s,%s,%s,%s,%s,%s)",
        (family, revision, version, revision - 1 if revision > 1 else None, previous, actor),
    )
    return version


def ensure_defaults(c, actor):
    require_schema(c)
    for kind, config, name in [
        ("outline", DEFAULT_OUTLINE, "内置技术方案目录"),
        ("layout", DEFAULT_STYLE, "内置A4正文版式"),
    ]:
        version = DEFAULT_IDS[kind]
        family = uuid5(version, "family")
        c.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            ("authoring-template:" + str(family),),
        )
        old = c.execute(
            "SELECT id FROM authoring_template_families WHERE id=%s", (family,)
        ).fetchone()
        if old is None:
            c.execute(
                "INSERT INTO authoring_template_families(id,kind,name,head_revision,head_version_id,created_by) VALUES(%s,%s,%s,1,%s,%s)",
                (family, kind, name, version, actor),
            )
            insert_version(c, family, kind, name, 1, None, config, actor, version)
        value = template(c, version, kind)
        if value["config"] != config or value["revision"] != 1:
            raise DomainError("AUTHORING_TEMPLATE_CORRUPT", "内置模板固定身份不匹配", 409)
    return {kind: str(value) for kind, value in DEFAULT_IDS.items()}


def template(c, version, kind=None):
    row = fetch(
        c,
        "SELECT t.*,l.family_id,l.revision,l.previous_version_id,l.created_by,l.created_at FROM template_versions t JOIN authoring_template_links l ON l.template_version_id=t.id WHERE t.id=%s",
        (version,),
    )
    if kind and row["kind"] != kind:
        raise DomainError("AUTHORING_TEMPLATE_KIND_INVALID", "目录和版式模板不能混用", 422)
    checked_config(row["kind"], row["config"])
    if row["schema_digest"] != config_schema(row["kind"]):
        raise DomainError("AUTHORING_TEMPLATE_CORRUPT", "模板格式摘要与当前真实子集不符", 409)
    return row


def view(c, version, kind=None):
    row = template(c, version, kind)
    return clean(
        {
            "id": row["id"],
            "family_id": row["family_id"],
            "revision": row["revision"],
            "name": row["name"],
            "kind": row["kind"],
            "config": row["config"],
            "config_digest": sha(row["config"]),
            "schema_digest": row["schema_digest"],
            "previous_version_id": row["previous_version_id"],
            "created_by": row["created_by"],
            "created_at": row["created_at"],
            "immutable": True,
            "is_builtin": row["id"] in DEFAULT_IDS.values(),
        }
    )


def create(c, actor, body):
    require_schema(c)
    validate("AuthoringTemplateCreate", body)
    checked_config(body["kind"], body["config"])
    family, version = uid(), uid()
    c.execute(
        "INSERT INTO authoring_template_families(id,kind,name,head_revision,head_version_id,created_by) VALUES(%s,%s,%s,1,%s,%s)",
        (family, body["kind"], body["name"], version, actor),
    )
    insert_version(c, family, body["kind"], body["name"], 1, None, body["config"], actor, version)
    return view(c, version)


def append(c, family, actor, body, condition):
    require_schema(c)
    validate("AuthoringTemplateVersion", body)
    row = fetch(c, "SELECT * FROM authoring_template_families WHERE id=%s FOR UPDATE", (family,))
    if row["id"] in {uuid5(v, "family") for v in DEFAULT_IDS.values()}:
        raise DomainError("AUTHORING_BUILTIN_IMMUTABLE", "请另存企业模板，内置模板不可改写", 409)
    match(condition, "authoring-template", family, row["head_revision"])
    if body["base_revision"] != row["head_revision"]:
        raise DomainError(
            "AUTHORING_TEMPLATE_REVISION_CONFLICT", "模板已有新版本，请保留本地修改并核对", 412
        )
    revision = row["head_revision"] + 1
    version = insert_version(
        c,
        family,
        row["kind"],
        body["name"],
        revision,
        row["head_version_id"],
        body["config"],
        actor,
    )
    c.execute(
        "UPDATE authoring_template_families SET head_revision=%s,head_version_id=%s WHERE id=%s",
        (revision, version, family),
    )
    return view(c, version)


def catalog(c, kind=None):
    require_schema(c)
    rows = c.execute(
        "SELECT f.head_version_id FROM authoring_template_families f WHERE (%s::text IS NULL OR f.kind=%s) ORDER BY f.created_at,f.id LIMIT 100",
        (kind, kind),
    ).fetchall()
    return {
        "items": [view(c, row["head_version_id"]) for row in rows],
        "coverage": "partial",
        "notice": "模板只定义结构或样式，不授项目事实/合规资格",
    }


def family_tag(value):
    return etag("authoring-template", UUID(value["family_id"]), value["revision"])
