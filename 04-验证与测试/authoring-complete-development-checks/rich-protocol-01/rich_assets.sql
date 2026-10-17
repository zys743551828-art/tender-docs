-- Complete-A2 RICH asset extension. Static review THEN independent fresh only.
-- No ALTER of existing tables, no owner repair, no Worker business identity access.
BEGIN;
CREATE TABLE design.authoring_image_assets (
 id uuid PRIMARY KEY, bid_id uuid NOT NULL REFERENCES design.bids(id),
 title text NOT NULL CHECK(length(title) BETWEEN 1 AND 200),
 head_revision bigint NOT NULL CHECK(head_revision>0), head_version_id uuid NOT NULL,
 created_by uuid NOT NULL REFERENCES design.members(id), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(bid_id,id)
);
CREATE TABLE design.authoring_image_versions (
 id uuid PRIMARY KEY, bid_id uuid NOT NULL, asset_id uuid NOT NULL,
 revision bigint NOT NULL CHECK(revision>0), previous_revision bigint, previous_version_id uuid,
 state text NOT NULL CHECK(state IN ('active','withdrawn')),
 reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 1000),
 original_blob_id uuid NOT NULL REFERENCES design.blobs(id),
 normalized_blob_id uuid NOT NULL REFERENCES design.blobs(id),
 original_media_type text NOT NULL CHECK(original_media_type IN ('image/png','image/jpeg')),
 width_px integer NOT NULL CHECK(width_px BETWEEN 1 AND 10000),
 height_px integer NOT NULL CHECK(height_px BETWEEN 1 AND 10000),
 normalization_proof jsonb NOT NULL CHECK(jsonb_typeof(normalization_proof)='object'),
 source_refs jsonb NOT NULL CHECK(jsonb_typeof(source_refs)='array'),
 digest design.sha256 NOT NULL,
 created_by uuid NOT NULL REFERENCES design.members(id), created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(bid_id,asset_id) REFERENCES design.authoring_image_assets(bid_id,id),
 UNIQUE(asset_id,revision), UNIQUE(bid_id,asset_id,revision,id),
 FOREIGN KEY(bid_id,asset_id,previous_revision,previous_version_id)
 REFERENCES design.authoring_image_versions(bid_id,asset_id,revision,id),
 CHECK(width_px::bigint*height_px<=16000000),
 CHECK((revision=1)=(previous_revision IS NULL AND previous_version_id IS NULL)),
 CHECK(revision=1 OR previous_revision IS NOT NULL AND previous_revision=revision-1 AND previous_version_id IS NOT NULL)
);
ALTER TABLE design.authoring_image_assets ADD CONSTRAINT authoring_image_exact_head
 FOREIGN KEY(bid_id,id,head_revision,head_version_id)
 REFERENCES design.authoring_image_versions(bid_id,asset_id,revision,id)
 DEFERRABLE INITIALLY DEFERRED;
CREATE FUNCTION design.guard_authoring_image_head() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.head_revision<>OLD.head_revision+1 THEN
  RAISE EXCEPTION 'authoring_image_revision' USING ERRCODE='23514';
 END IF;
 IF NOT EXISTS(SELECT 1 FROM design.authoring_image_versions v
  WHERE v.id=NEW.head_version_id AND v.bid_id=OLD.bid_id AND v.asset_id=OLD.id
    AND v.revision=NEW.head_revision AND v.previous_revision=OLD.head_revision AND v.previous_version_id=OLD.head_version_id) THEN
  RAISE EXCEPTION 'authoring_image_head_chain' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER authoring_image_head_step BEFORE UPDATE ON design.authoring_image_assets
 FOR EACH ROW EXECUTE FUNCTION design.guard_authoring_image_head();
CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON design.authoring_image_versions
 FOR EACH ROW EXECUTE FUNCTION design.append_only();
REVOKE ALL ON design.authoring_image_assets,design.authoring_image_versions
 FROM PUBLIC,tender_api,tender_worker,tender_research_worker,tender_score_read,tender_research_read;
GRANT SELECT,INSERT ON design.authoring_image_assets,design.authoring_image_versions TO tender_api;
GRANT UPDATE(head_revision,head_version_id) ON design.authoring_image_assets TO tender_api;
-- Exact current-version/head verification at fenced publish needs only these public asset rows.
-- No new Worker DML, model, project identity, commands, or authoring-tree permissions.
GRANT SELECT ON design.authoring_image_assets,design.authoring_image_versions TO tender_worker;
REVOKE ALL ON FUNCTION design.guard_authoring_image_head() FROM PUBLIC;
COMMIT;
