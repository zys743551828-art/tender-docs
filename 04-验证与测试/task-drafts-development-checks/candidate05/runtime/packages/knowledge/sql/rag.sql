-- Candidate new-empty-database extension only. NOT a release database patch.
-- Apply only after reviewed core/domain/retrieval/roles and vector runtime grants.
BEGIN;
CREATE TABLE retrieval.knowledge_indexes (
 id uuid PRIMARY KEY,
 knowledge_version_id uuid NOT NULL REFERENCES retrieval.knowledge_versions(id),
 profile_id uuid NOT NULL REFERENCES retrieval.embedding_profiles(id),
 origin_bid_id uuid NOT NULL REFERENCES design.bids(id),
 operation_id uuid NOT NULL,
 input_manifest jsonb NOT NULL CHECK(jsonb_typeof(input_manifest)='object'),
 input_digest design.sha256 NOT NULL,
 chunk_policy_digest design.sha256 NOT NULL,
 lexical_dictionary_digest design.sha256 NOT NULL,
 requested_head_revision bigint NOT NULL CHECK(requested_head_revision>0),
 created_by uuid NOT NULL REFERENCES design.members(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','ready','failed','superseded')),
 expected_unit_count integer CHECK(expected_unit_count BETWEEN 1 AND 1000),
 published_unit_count integer NOT NULL DEFAULT 0 CHECK(published_unit_count BETWEEN 0 AND 1000),
 unit_manifest_digest design.sha256,
 error_code text CHECK(error_code ~ '^[A-Z][A-Z0-9_]{0,99}$'),
 completed_at timestamptz,
 UNIQUE(id,knowledge_version_id,profile_id),
 UNIQUE(id,knowledge_version_id),
 UNIQUE(id,profile_id),
 FOREIGN KEY(origin_bid_id,operation_id) REFERENCES design.operations(bid_id,id),
 CHECK(state<>'ready' OR (
   expected_unit_count IS NOT NULL AND unit_manifest_digest IS NOT NULL AND completed_at IS NOT NULL
   AND published_unit_count=expected_unit_count AND error_code IS NULL)),
 CHECK(state NOT IN ('failed','superseded') OR (error_code IS NOT NULL AND completed_at IS NOT NULL))
);
CREATE TABLE retrieval.knowledge_index_heads (
 knowledge_version_id uuid NOT NULL REFERENCES retrieval.knowledge_versions(id),
 profile_id uuid NOT NULL REFERENCES retrieval.embedding_profiles(id),
 revision bigint NOT NULL CHECK(revision>0),
 desired_index_id uuid NOT NULL,
 ready_index_id uuid,
 PRIMARY KEY(knowledge_version_id,profile_id),
 FOREIGN KEY(desired_index_id,knowledge_version_id,profile_id)
   REFERENCES retrieval.knowledge_indexes(id,knowledge_version_id,profile_id),
 FOREIGN KEY(ready_index_id,knowledge_version_id,profile_id)
   REFERENCES retrieval.knowledge_indexes(id,knowledge_version_id,profile_id)
);
CREATE TABLE retrieval.knowledge_units (
 id uuid PRIMARY KEY,
 index_id uuid NOT NULL,
 knowledge_version_id uuid NOT NULL,
 ordinal integer NOT NULL CHECK(ordinal>=0 AND ordinal<1000),
 source_id uuid NOT NULL UNIQUE REFERENCES retrieval.source_nodes(id),
 unit_kind text NOT NULL CHECK(unit_kind IN ('knowledge_text','original_quote')),
 ref_ordinal integer,
 span_start integer NOT NULL CHECK(span_start>=0),
 span_end integer NOT NULL CHECK(span_end>span_start),
 text_content text NOT NULL CHECK(length(btrim(text_content))>0),
 text_digest design.sha256 NOT NULL,
 token_count integer NOT NULL CHECK(token_count BETWEEN 1 AND 1200),
 lexemes text NOT NULL,
 lex_tsv tsvector GENERATED ALWAYS AS(to_tsvector('simple'::regconfig,lexemes)) STORED,
 provenance jsonb NOT NULL CHECK(jsonb_typeof(provenance)='object'),
 UNIQUE(index_id,ordinal), UNIQUE(index_id,id), UNIQUE(id,knowledge_version_id),
 FOREIGN KEY(index_id,knowledge_version_id) REFERENCES retrieval.knowledge_indexes(id,knowledge_version_id),
 CHECK((unit_kind='knowledge_text' AND ref_ordinal IS NULL) OR
       (unit_kind='original_quote' AND ref_ordinal IS NOT NULL AND ref_ordinal BETWEEN 0 AND 99))
);
CREATE INDEX knowledge_units_lexical ON retrieval.knowledge_units USING gin(lex_tsv);
CREATE INDEX knowledge_units_index ON retrieval.knowledge_units(index_id,ordinal);
CREATE TABLE retrieval.knowledge_unit_embeddings (
 index_id uuid NOT NULL,
 unit_id uuid NOT NULL,
 profile_id uuid NOT NULL,
 embedding design.vector(1024) NOT NULL,
 PRIMARY KEY(unit_id,profile_id),
 FOREIGN KEY(index_id,unit_id) REFERENCES retrieval.knowledge_units(index_id,id),
 FOREIGN KEY(index_id,profile_id) REFERENCES retrieval.knowledge_indexes(id,profile_id),
 CHECK(abs(design.vector_norm(embedding)-1.0)<0.001)
);
CREATE INDEX knowledge_embeddings_profile ON retrieval.knowledge_unit_embeddings(index_id,profile_id);
CREATE TABLE retrieval.knowledge_retrieval_traces (
 id uuid PRIMARY KEY,
 bid_id uuid NOT NULL REFERENCES design.bids(id),
 actor_id uuid NOT NULL REFERENCES design.members(id),
 purpose text NOT NULL CHECK(purpose IN ('assist','authoring','compliance','history')),
 query_digest design.sha256 NOT NULL,
 profile_id uuid NOT NULL REFERENCES retrieval.embedding_profiles(id),
 policy_digest design.sha256 NOT NULL,
 frozen_scope jsonb NOT NULL CHECK(jsonb_typeof(frozen_scope)='object'),
 scope_digest design.sha256 NOT NULL,
 selected_units jsonb NOT NULL CHECK(jsonb_typeof(selected_units)='array' AND jsonb_array_length(selected_units)<=8),
 route jsonb NOT NULL CHECK(jsonb_typeof(route)='array'),
 observed_reranker_identity jsonb CHECK(observed_reranker_identity IS NULL OR jsonb_typeof(observed_reranker_identity)='object'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(id,bid_id)
);
CREATE INDEX knowledge_trace_project ON retrieval.knowledge_retrieval_traces(bid_id,created_at,id);
CREATE TABLE retrieval.knowledge_unit_references (
 citation_id uuid PRIMARY KEY REFERENCES retrieval.citations(id),
 trace_id uuid NOT NULL,
 bid_id uuid NOT NULL,
 unit_id uuid NOT NULL,
 knowledge_version_id uuid NOT NULL,
 approved_quote text NOT NULL CHECK(length(btrim(approved_quote))>0),
 unit_span_start integer NOT NULL CHECK(unit_span_start>=0),
 unit_span_end integer NOT NULL CHECK(unit_span_end>unit_span_start),
 reference_manifest jsonb NOT NULL CHECK(jsonb_typeof(reference_manifest)='object'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(trace_id,bid_id) REFERENCES retrieval.knowledge_retrieval_traces(id,bid_id),
 FOREIGN KEY(unit_id,knowledge_version_id) REFERENCES retrieval.knowledge_units(id,knowledge_version_id)
);
CREATE INDEX knowledge_reference_trace ON retrieval.knowledge_unit_references(trace_id,citation_id);
CREATE INDEX knowledge_reference_unit ON retrieval.knowledge_unit_references(unit_id,citation_id);
REVOKE ALL ON retrieval.knowledge_indexes,retrieval.knowledge_index_heads,retrieval.knowledge_units,
 retrieval.knowledge_unit_embeddings,retrieval.knowledge_retrieval_traces,retrieval.knowledge_unit_references FROM PUBLIC;
GRANT SELECT ON retrieval.knowledge_indexes,retrieval.knowledge_index_heads,retrieval.knowledge_units,
 retrieval.knowledge_unit_embeddings,retrieval.knowledge_retrieval_traces,retrieval.knowledge_unit_references TO tender_api;
-- API cannot insert or update ready/count/result fields, including by UPDATE(state).
GRANT INSERT(id,knowledge_version_id,profile_id,origin_bid_id,operation_id,input_manifest,input_digest,
 chunk_policy_digest,lexical_dictionary_digest,requested_head_revision,created_by)
 ON retrieval.knowledge_indexes TO tender_api;
GRANT INSERT(knowledge_version_id,profile_id,revision,desired_index_id)
 ON retrieval.knowledge_index_heads TO tender_api;
GRANT UPDATE(revision,desired_index_id) ON retrieval.knowledge_index_heads TO tender_api;
GRANT INSERT(id,bid_id,actor_id,purpose,query_digest,profile_id,policy_digest,frozen_scope,scope_digest,
 selected_units,route,observed_reranker_identity) ON retrieval.knowledge_retrieval_traces TO tender_api;
GRANT INSERT(citation_id,trace_id,bid_id,unit_id,knowledge_version_id,approved_quote,
 unit_span_start,unit_span_end,reference_manifest) ON retrieval.knowledge_unit_references TO tender_api;
GRANT SELECT ON retrieval.knowledge_indexes,retrieval.knowledge_index_heads,
 retrieval.knowledge_units,retrieval.knowledge_unit_embeddings TO tender_worker;
GRANT UPDATE(state,expected_unit_count,published_unit_count,unit_manifest_digest,error_code,completed_at)
 ON retrieval.knowledge_indexes TO tender_worker;
GRANT UPDATE(ready_index_id) ON retrieval.knowledge_index_heads TO tender_worker;
GRANT INSERT ON retrieval.knowledge_units,retrieval.knowledge_unit_embeddings TO tender_worker;
-- No Worker trace/reference/engineering-project/bidder/commands capabilities added.
-- No API/Worker UPDATE/DELETE on immutable units, embeddings, trace or references.
-- Failed operation metadata is read from existing operations; API cannot set index state.
COMMIT;
