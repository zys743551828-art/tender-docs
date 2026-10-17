-- pgvector与来源图；先执行core.sql及domain.sql。固定首批1024维dense profile。
BEGIN;
CREATE EXTENSION IF NOT EXISTS vector;
CREATE SCHEMA retrieval;
SET search_path TO retrieval,design,public;
CREATE TABLE source_nodes(
 id uuid PRIMARY KEY,document_version_id uuid REFERENCES design.document_versions(id),
 kind text NOT NULL CHECK(kind IN ('document','knowledge','summary','feature','label','model')),
 policy_class text NOT NULL CHECK(policy_class IN ('prebid_content','official_label','post_outcome','derived_mixed')),
 known_at timestamptz NOT NULL,digest design.sha256 NOT NULL
);
CREATE TABLE source_heads(source_id uuid PRIMARY KEY REFERENCES source_nodes(id),revision bigint NOT NULL CHECK(revision>0),status text NOT NULL CHECK(status IN ('candidate','published','withdrawn')),valid_from timestamptz NOT NULL,valid_until timestamptz,CHECK(valid_until IS NULL OR valid_until>valid_from));
CREATE TABLE lineage_edges(child_id uuid NOT NULL REFERENCES source_nodes(id),parent_id uuid NOT NULL REFERENCES source_nodes(id),PRIMARY KEY(child_id,parent_id),CHECK(child_id<>parent_id));
CREATE FUNCTION no_lineage_cycle() RETURNS trigger LANGUAGE plpgsql SET search_path=retrieval,pg_temp AS $$
BEGIN
 PERFORM pg_advisory_xact_lock(90127,1);
 IF EXISTS(WITH RECURSIVE ancestors(id) AS (SELECT NEW.parent_id UNION SELECT e.parent_id FROM lineage_edges e JOIN ancestors a ON e.child_id=a.id) SELECT 1 FROM ancestors WHERE id=NEW.child_id) THEN
 RAISE EXCEPTION 'lineage_cycle' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER acyclic BEFORE INSERT ON lineage_edges FOR EACH ROW EXECUTE FUNCTION no_lineage_cycle();
CREATE VIEW source_ancestors AS WITH RECURSIVE walk(source_id,ancestor_id) AS (
 SELECT id,id FROM source_nodes UNION SELECT w.source_id,e.parent_id FROM walk w JOIN lineage_edges e ON w.ancestor_id=e.child_id
) SELECT source_id,ancestor_id FROM walk;
CREATE TABLE source_groups(id uuid PRIMARY KEY,reason text NOT NULL);
CREATE TABLE source_group_memberships(source_id uuid NOT NULL REFERENCES source_nodes(id),group_id uuid NOT NULL REFERENCES source_groups(id),PRIMARY KEY(source_id,group_id));
CREATE TABLE embedding_profiles(
 id uuid PRIMARY KEY,model_name text NOT NULL,model_revision text NOT NULL,weights_digest design.sha256 NOT NULL,
 dimensions integer NOT NULL CHECK(dimensions=1024),distance text NOT NULL CHECK(distance='cosine'),normalized boolean NOT NULL CHECK(normalized),preprocessing_digest design.sha256 NOT NULL
);
CREATE TABLE chunks(
 id uuid PRIMARY KEY,source_id uuid NOT NULL REFERENCES source_nodes(id),parse_id uuid NOT NULL REFERENCES design.parse_runs(id),
 physical_page integer NOT NULL CHECK(physical_page>0),ordinal integer NOT NULL CHECK(ordinal>=0),chapter_path text[] NOT NULL,
 evidence_ids uuid[] NOT NULL CHECK(cardinality(evidence_ids)>0),text_content text NOT NULL,lexemes text NOT NULL,
 lex_tsv tsvector GENERATED ALWAYS AS(to_tsvector('simple'::regconfig,lexemes)) STORED,
 token_count integer NOT NULL CHECK(token_count>0),digest design.sha256 NOT NULL,
 UNIQUE(parse_id,ordinal),FOREIGN KEY(parse_id,physical_page) REFERENCES design.pages(parse_id,physical_page)
);
CREATE INDEX chunks_lexical ON chunks USING gin(lex_tsv);
CREATE INDEX chunks_source ON chunks(source_id,parse_id,ordinal);
CREATE TABLE embeddings(
 chunk_id uuid NOT NULL REFERENCES chunks(id),profile_id uuid NOT NULL REFERENCES embedding_profiles(id),
 embedding vector(1024) NOT NULL,CHECK(abs(vector_norm(embedding)-1.0)<0.001),
 PRIMARY KEY(chunk_id,profile_id)
);
-- 首批小集合走精确距离。HNSW为可显式开启的索引，profile过滤后仍须检查实际候选数。
CREATE INDEX embeddings_cosine_hnsw ON embeddings USING hnsw(embedding vector_cosine_ops);
CREATE TABLE knowledge_items(id uuid PRIMARY KEY,title text NOT NULL,revision bigint NOT NULL CHECK(revision>0),published_version_id uuid);
CREATE TABLE knowledge_versions(id uuid PRIMARY KEY,item_id uuid NOT NULL REFERENCES knowledge_items(id),version_no bigint NOT NULL CHECK(version_no>0),source_id uuid NOT NULL UNIQUE REFERENCES source_nodes(id),content text NOT NULL,categories text[] NOT NULL,applicability jsonb NOT NULL,created_by uuid NOT NULL REFERENCES design.members(id),UNIQUE(item_id,version_no),UNIQUE(item_id,id));
ALTER TABLE knowledge_items ADD CONSTRAINT knowledge_own_version FOREIGN KEY(id,published_version_id) REFERENCES knowledge_versions(item_id,id);
CREATE TABLE citations(id uuid PRIMARY KEY,bid_id uuid NOT NULL REFERENCES design.bids(id),source_id uuid NOT NULL REFERENCES source_nodes(id),source_revision bigint NOT NULL,purpose text NOT NULL CHECK(purpose IN ('assist','authoring','compliance','formal_score','history')),chunk_id uuid REFERENCES chunks(id),evidence_id uuid REFERENCES design.evidence_blocks(id),target_object_id uuid NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),CHECK(chunk_id IS NOT NULL OR evidence_id IS NOT NULL));
CREATE TABLE retrieval_traces(id uuid PRIMARY KEY,bid_id uuid REFERENCES design.bids(id),purpose text NOT NULL,query_digest design.sha256 NOT NULL,allowed_manifest_digest design.sha256 NOT NULL,profile_id uuid NOT NULL REFERENCES embedding_profiles(id),policy_digest design.sha256 NOT NULL,selected_chunk_ids uuid[] NOT NULL,excluded_reasons jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE score_input_sources(review_id uuid NOT NULL REFERENCES design.review_runs(id),source_id uuid NOT NULL REFERENCES source_nodes(id),PRIMARY KEY(review_id,source_id));
CREATE FUNCTION eligible_chunks(allowed_sources uuid[],as_of timestamptz,excluded_groups uuid[],formal_score boolean)
RETURNS SETOF chunks LANGUAGE sql STABLE SET search_path=retrieval,design,public,pg_temp AS $$
 SELECT c.* FROM chunks c
 WHERE c.source_id=ANY(allowed_sources)
 AND NOT EXISTS(
  SELECT 1 FROM source_ancestors a JOIN source_nodes n ON n.id=a.ancestor_id
  LEFT JOIN source_heads h ON h.source_id=n.id
  WHERE a.source_id=c.source_id AND (
   n.known_at>as_of OR h.status IS DISTINCT FROM 'published' OR h.valid_from>as_of OR (h.valid_until IS NOT NULL AND h.valid_until<=as_of)
   OR (formal_score AND n.policy_class<>'prebid_content')
   OR EXISTS(SELECT 1 FROM source_group_memberships g WHERE g.source_id=n.id AND g.group_id=ANY(excluded_groups))
  )
 );
$$;
-- 评分进程只读已由服务端固化的来源集合，不读官方成绩表或会话表。
CREATE VIEW score_context_chunks AS
 SELECT s.review_id,c.* FROM score_input_sources s JOIN design.review_runs r ON r.id=s.review_id
 JOIN design.review_inputs i ON i.id=r.input_id
 CROSS JOIN LATERAL eligible_chunks(ARRAY[s.source_id],i.frozen_at,ARRAY[]::uuid[],true) c;
DO $$ DECLARE n text; BEGIN
 FOREACH n IN ARRAY ARRAY['source_nodes','lineage_edges','source_group_memberships','embedding_profiles','chunks','embeddings','knowledge_versions','citations','retrieval_traces','score_input_sources'] LOOP
 EXECUTE format('CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON retrieval.%I FOR EACH ROW EXECUTE FUNCTION design.append_only()',n);
 END LOOP;
END $$;
COMMIT;
