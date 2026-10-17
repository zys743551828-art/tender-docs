-- Complete A2 text-first structure/plan/template extension. Fresh database only, AFTER base roles.
BEGIN;
CREATE TABLE design.authoring_template_families (
 id uuid PRIMARY KEY, kind text NOT NULL CHECK(kind IN ('outline','layout')),
 name text NOT NULL CHECK(length(name) BETWEEN 1 AND 200),
 head_revision bigint NOT NULL CHECK(head_revision>0), head_version_id uuid NOT NULL,
 created_by uuid NOT NULL REFERENCES design.members(id), created_at timestamptz NOT NULL DEFAULT clock_timestamp()
);
CREATE TABLE design.authoring_template_links (
 family_id uuid NOT NULL REFERENCES design.authoring_template_families(id),
 revision bigint NOT NULL CHECK(revision>0), template_version_id uuid NOT NULL UNIQUE REFERENCES design.template_versions(id),
 previous_revision bigint, previous_version_id uuid, created_by uuid NOT NULL REFERENCES design.members(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(family_id,revision), UNIQUE(family_id,revision,template_version_id), UNIQUE(family_id,template_version_id),
 FOREIGN KEY(family_id,previous_revision,previous_version_id) REFERENCES design.authoring_template_links(family_id,revision,template_version_id),
 CHECK((revision=1)=(previous_revision IS NULL AND previous_version_id IS NULL)),
 CHECK(revision=1 OR previous_revision IS NOT NULL AND previous_revision=revision-1 AND previous_version_id IS NOT NULL)
);
ALTER TABLE design.authoring_template_families ADD CONSTRAINT authoring_template_head
 FOREIGN KEY(id,head_revision,head_version_id) REFERENCES design.authoring_template_links(family_id,revision,template_version_id)
 DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE design.authoring_plan_versions (
 id uuid PRIMARY KEY, bid_id uuid NOT NULL, draft_id uuid NOT NULL, section_id uuid NOT NULL,
 revision bigint NOT NULL CHECK(revision>0), base_section_revision bigint NOT NULL,
 state text NOT NULL CHECK(state IN ('not_started','drafting','needs_basis','ready')),
 planned_pages integer CHECK(planned_pages BETWEEN 1 AND 10000),
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'), digest design.sha256 NOT NULL,
 created_by uuid NOT NULL REFERENCES design.members(id), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(bid_id,draft_id,section_id) REFERENCES design.sections(bid_id,draft_id,id),
 FOREIGN KEY(section_id,base_section_revision) REFERENCES design.section_revisions(section_id,revision),
 UNIQUE(section_id,revision), UNIQUE(bid_id,draft_id,section_id,id)
);
CREATE TABLE design.authoring_structure_versions (
 id uuid PRIMARY KEY, bid_id uuid NOT NULL, draft_id uuid NOT NULL, draft_revision bigint NOT NULL CHECK(draft_revision>0),
 snapshot_id uuid NOT NULL UNIQUE, directory_template_version_id uuid NOT NULL REFERENCES design.template_versions(id),
 style_template_version_id uuid NOT NULL REFERENCES design.template_versions(id),
 metadata jsonb NOT NULL CHECK(jsonb_typeof(metadata)='object'), digest design.sha256 NOT NULL,
 created_by uuid NOT NULL REFERENCES design.members(id), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(bid_id,draft_id) REFERENCES design.drafts(bid_id,id),
 FOREIGN KEY(bid_id,draft_id,snapshot_id) REFERENCES design.draft_snapshots(bid_id,draft_id,id),
 FOREIGN KEY(draft_id,draft_revision) REFERENCES design.draft_snapshots(draft_id,revision),
 UNIQUE(draft_id,draft_revision), UNIQUE(id,bid_id,draft_id)
);
CREATE TABLE design.authoring_structure_nodes (
 structure_id uuid NOT NULL, bid_id uuid NOT NULL, draft_id uuid NOT NULL, section_id uuid NOT NULL,
 parent_id uuid, ordinal integer NOT NULL CHECK(ordinal BETWEEN 0 AND 1999),
 node_type text NOT NULL CHECK(node_type IN ('chapter','appendix')), deleted boolean NOT NULL,
 section_revision bigint NOT NULL, plan_version_id uuid,
 template_family_id uuid REFERENCES design.authoring_template_families(id), template_key text CHECK(length(template_key) BETWEEN 1 AND 128),
 PRIMARY KEY(structure_id,section_id),
 FOREIGN KEY(structure_id,bid_id,draft_id) REFERENCES design.authoring_structure_versions(id,bid_id,draft_id),
 FOREIGN KEY(bid_id,draft_id,section_id) REFERENCES design.sections(bid_id,draft_id,id),
 FOREIGN KEY(section_id,section_revision) REFERENCES design.section_revisions(section_id,revision),
 FOREIGN KEY(bid_id,draft_id,section_id,plan_version_id) REFERENCES design.authoring_plan_versions(bid_id,draft_id,section_id,id),
 FOREIGN KEY(structure_id,parent_id) REFERENCES design.authoring_structure_nodes(structure_id,section_id) DEFERRABLE INITIALLY DEFERRED,
 CHECK(section_id IS DISTINCT FROM parent_id), CHECK((template_family_id IS NULL)=(template_key IS NULL))
);
CREATE UNIQUE INDEX authoring_active_sibling_ordinal ON design.authoring_structure_nodes(structure_id,parent_id,ordinal) NULLS NOT DISTINCT WHERE NOT deleted;
CREATE UNIQUE INDEX authoring_active_template_key ON design.authoring_structure_nodes(structure_id,template_family_id,template_key) WHERE NOT deleted AND template_key IS NOT NULL;
CREATE FUNCTION design.guard_authoring_structure_binding() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM design.draft_snapshots s WHERE s.id=NEW.snapshot_id AND s.bid_id=NEW.bid_id AND s.draft_id=NEW.draft_id AND s.revision=NEW.draft_revision) THEN
  RAISE EXCEPTION 'authoring_snapshot_identity' USING ERRCODE='23514';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM design.template_versions t WHERE t.id=NEW.directory_template_version_id AND t.kind='outline') OR NOT EXISTS(SELECT 1 FROM design.template_versions t WHERE t.id=NEW.style_template_version_id AND t.kind='layout') THEN
  RAISE EXCEPTION 'authoring_template_kind' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER authoring_structure_binding BEFORE INSERT ON design.authoring_structure_versions FOR EACH ROW EXECUTE FUNCTION design.guard_authoring_structure_binding();
CREATE FUNCTION design.guard_authoring_template_chain() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME='authoring_template_families' THEN
  IF NEW.head_revision<>OLD.head_revision+1 THEN RAISE EXCEPTION 'authoring_template_revision' USING ERRCODE='23514'; END IF;
 ELSE
  IF NOT EXISTS(SELECT 1 FROM design.authoring_template_families f JOIN design.template_versions v ON v.id=NEW.template_version_id WHERE f.id=NEW.family_id AND f.kind=v.kind) THEN
   RAISE EXCEPTION 'authoring_template_kind' USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER authoring_template_head_step BEFORE UPDATE ON design.authoring_template_families FOR EACH ROW EXECUTE FUNCTION design.guard_authoring_template_chain();
CREATE TRIGGER authoring_template_link_kind BEFORE INSERT ON design.authoring_template_links FOR EACH ROW EXECUTE FUNCTION design.guard_authoring_template_chain();
DO $$ DECLARE item text; BEGIN
 FOREACH item IN ARRAY ARRAY['authoring_template_links','authoring_plan_versions','authoring_structure_versions','authoring_structure_nodes'] LOOP
  EXECUTE format('CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON design.%I FOR EACH ROW EXECUTE FUNCTION design.append_only()',item);
 END LOOP;
END $$;
REVOKE ALL ON design.authoring_template_families,design.authoring_template_links,design.authoring_plan_versions,design.authoring_structure_versions,design.authoring_structure_nodes FROM PUBLIC,tender_api,tender_worker,tender_research_worker,tender_score_read,tender_research_read;
GRANT SELECT,INSERT ON design.authoring_template_families,design.authoring_template_links,design.authoring_plan_versions,design.authoring_structure_versions,design.authoring_structure_nodes TO tender_api;
GRANT UPDATE(head_revision,head_version_id) ON design.authoring_template_families TO tender_api;
REVOKE ALL ON FUNCTION design.guard_authoring_structure_binding(),design.guard_authoring_template_chain() FROM PUBLIC;
COMMIT;
