-- Fresh schema extension only, AFTER reviewed base roles.sql; never runtime repair.
-- Global work has no synthetic bid. Project calls stay in design.operations/model_calls.
BEGIN;
CREATE TABLE design.assistant_global_conversations (
 id uuid PRIMARY KEY,
 created_by uuid NOT NULL UNIQUE REFERENCES design.members(id),
 state text NOT NULL DEFAULT 'active' CHECK(state IN ('active','closed','hidden')),
 revision bigint NOT NULL DEFAULT 1 CHECK(revision>0),
 last_seq bigint NOT NULL DEFAULT 0 CHECK(last_seq>=0),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(id,created_by)
);
CREATE TABLE design.assistant_global_messages (
 id uuid PRIMARY KEY,
 conversation_id uuid NOT NULL REFERENCES design.assistant_global_conversations(id),
 client_message_id uuid,
 role text NOT NULL CHECK(role IN ('user','assistant','tool')),
 parts jsonb NOT NULL CHECK(jsonb_typeof(parts)='array'),
 created_by uuid NOT NULL REFERENCES design.members(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(conversation_id,created_by) REFERENCES design.assistant_global_conversations(id,created_by),
 UNIQUE(conversation_id,client_message_id), UNIQUE(conversation_id,id)
);
CREATE TABLE design.assistant_jobs (
 id uuid PRIMARY KEY,
 kind text NOT NULL CHECK(kind IN ('answer','authoring')),
 scope text NOT NULL CHECK(scope IN ('project','global')),
 bid_id uuid REFERENCES design.bids(id),
 project_conversation_id uuid,
 project_message_id uuid REFERENCES design.messages(id),
 global_conversation_id uuid REFERENCES design.assistant_global_conversations(id),
 global_message_id uuid,
 writing_plan_id uuid REFERENCES design.writing_plans(id),
 operation_id uuid UNIQUE REFERENCES design.operations(id),
 actor_id uuid NOT NULL REFERENCES design.members(id),
 origin_action_id uuid REFERENCES design.proposed_actions(id),
 input_manifest jsonb NOT NULL CHECK(jsonb_typeof(input_manifest)='object'),
 input_digest design.sha256 NOT NULL,
 intent_digest design.sha256 NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(bid_id,project_conversation_id) REFERENCES design.conversations(bid_id,id),
 FOREIGN KEY(bid_id,operation_id) REFERENCES design.operations(bid_id,id),
 FOREIGN KEY(global_conversation_id,global_message_id) REFERENCES design.assistant_global_messages(conversation_id,id),
 FOREIGN KEY(global_conversation_id,actor_id) REFERENCES design.assistant_global_conversations(id,created_by),
 UNIQUE(id,scope,kind), UNIQUE(id,global_conversation_id,actor_id), UNIQUE(id,input_digest),
 UNIQUE(project_message_id), UNIQUE(global_message_id),
 CHECK(
  (kind='answer' AND scope='global' AND bid_id IS NULL AND operation_id IS NULL
    AND global_conversation_id IS NOT NULL AND global_message_id IS NOT NULL
    AND project_conversation_id IS NULL AND project_message_id IS NULL AND writing_plan_id IS NULL)
  OR (kind='answer' AND scope='project' AND bid_id IS NOT NULL AND operation_id IS NOT NULL
    AND project_conversation_id IS NOT NULL AND project_message_id IS NOT NULL
    AND global_conversation_id IS NULL AND global_message_id IS NULL AND writing_plan_id IS NULL)
  OR (kind='authoring' AND scope='project' AND bid_id IS NOT NULL AND operation_id IS NOT NULL
    AND writing_plan_id IS NOT NULL AND project_conversation_id IS NULL AND project_message_id IS NULL
    AND global_conversation_id IS NULL AND global_message_id IS NULL)
 )
);
CREATE INDEX assistant_jobs_project ON design.assistant_jobs(bid_id,created_at,id);
CREATE TABLE design.assistant_global_runs (
 id uuid PRIMARY KEY,
 conversation_id uuid NOT NULL,
 actor_id uuid NOT NULL,
 command_id uuid NOT NULL REFERENCES design.commands(id),
 input_digest design.sha256 NOT NULL,
 max_tokens integer NOT NULL CHECK(max_tokens BETWEEN 1 AND 8192),
 state text NOT NULL DEFAULT 'queued' CHECK(state IN ('queued','running','cancel_requested','succeeded','failed','cancelled')),
 fence bigint NOT NULL DEFAULT 0 CHECK(fence>=0),
 owner_token uuid,
 lease_until timestamptz,
 cancel_requested_at timestamptz,
 finished_at timestamptz,
 error_code text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(id,conversation_id,actor_id) REFERENCES design.assistant_jobs(id,global_conversation_id,actor_id),
 FOREIGN KEY(id,input_digest) REFERENCES design.assistant_jobs(id,input_digest),
 UNIQUE(id,max_tokens),
 CHECK((state IN ('succeeded','failed','cancelled'))=(finished_at IS NOT NULL)),
 CHECK(state NOT IN ('cancel_requested','cancelled') OR cancel_requested_at IS NOT NULL),
 CHECK(state<>'running' OR fence>0 AND owner_token IS NOT NULL AND lease_until IS NOT NULL)
);
CREATE TABLE design.assistant_global_calls (
 id uuid PRIMARY KEY,
 run_id uuid NOT NULL UNIQUE,
 intent_digest design.sha256 NOT NULL,
 request_digest design.sha256 NOT NULL,
 profile_digest design.sha256 NOT NULL,
 max_tokens integer NOT NULL,
 state text NOT NULL DEFAULT 'reserved' CHECK(state IN ('reserved','succeeded','failed','unknown')),
 provider_request_id text,
 usage jsonb CHECK(usage IS NULL OR jsonb_typeof(usage)='object'),
 response_ref uuid REFERENCES design.blobs(id),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(run_id,max_tokens) REFERENCES design.assistant_global_runs(id,max_tokens),
 UNIQUE(run_id,id),
 CHECK(state<>'succeeded' OR response_ref IS NOT NULL)
);
CREATE INDEX assistant_global_unresolved ON design.assistant_global_calls(intent_digest,request_digest,profile_digest,state);
CREATE TABLE design.assistant_global_outbox (
 id uuid PRIMARY KEY,
 run_id uuid NOT NULL UNIQUE REFERENCES design.assistant_global_runs(id),
 topic text NOT NULL DEFAULT 'tender.task.assistant-global.v1' CHECK(topic='tender.task.assistant-global.v1'),
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
 payload_digest design.sha256 NOT NULL,
 available_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 published_at timestamptz,
 claim_token uuid,
 lease_until timestamptz,
 publish_attempts integer NOT NULL DEFAULT 0 CHECK(publish_attempts>=0)
);
CREATE TABLE design.assistant_global_events (
 conversation_id uuid NOT NULL REFERENCES design.assistant_global_conversations(id),
 seq bigint NOT NULL CHECK(seq>0),
 event_id uuid NOT NULL UNIQUE,
 event_type text NOT NULL,
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(conversation_id,seq)
);
CREATE TABLE design.assistant_outputs (
 job_id uuid PRIMARY KEY REFERENCES design.assistant_jobs(id),
 project_call_id uuid REFERENCES design.model_calls(id),
 global_call_id uuid,
 payload jsonb NOT NULL CHECK(jsonb_typeof(payload)='object'),
 digest design.sha256 NOT NULL,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(job_id,global_call_id) REFERENCES design.assistant_global_calls(run_id,id),
 CHECK((project_call_id IS NULL)<>(global_call_id IS NULL))
);
CREATE TABLE design.assistant_publications (
 job_id uuid PRIMARY KEY REFERENCES design.assistant_outputs(job_id),
 scope text NOT NULL,
 kind text NOT NULL,
 state text NOT NULL CHECK(state IN ('published','stale','failed')),
 project_message_id uuid REFERENCES design.messages(id),
 global_message_id uuid REFERENCES design.assistant_global_messages(id),
 action_id uuid REFERENCES design.proposed_actions(id),
 error_code text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(job_id,scope,kind) REFERENCES design.assistant_jobs(id,scope,kind),
 CHECK(
  (state<>'published' AND project_message_id IS NULL AND global_message_id IS NULL AND action_id IS NULL AND error_code IS NOT NULL)
  OR (state='published' AND error_code IS NULL AND kind='authoring' AND action_id IS NOT NULL AND project_message_id IS NULL AND global_message_id IS NULL)
  OR (state='published' AND error_code IS NULL AND kind='answer' AND action_id IS NULL
      AND ((scope='project' AND project_message_id IS NOT NULL AND global_message_id IS NULL)
           OR (scope='global' AND global_message_id IS NOT NULL AND project_message_id IS NULL)))
 )
);
CREATE TABLE design.assistant_action_links (
 action_id uuid PRIMARY KEY REFERENCES design.proposed_actions(id),
 job_id uuid NOT NULL REFERENCES design.assistant_outputs(job_id),
 tool text NOT NULL CHECK(tool IN ('review.request','authoring.generate','authoring.apply_patch','knowledge.candidate')),
 state text NOT NULL DEFAULT 'pending' CHECK(state IN ('pending','executed','rejected','expired','stale','failed')),
 revision bigint NOT NULL DEFAULT 1 CHECK(revision>0),
 resolved_by uuid REFERENCES design.members(id),
 resolved_at timestamptz,
 error_code text,
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 CHECK((state='pending')=(resolved_at IS NULL))
);
CREATE FUNCTION design.guard_assistant_action_terminal() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.state<>'pending' AND NEW IS DISTINCT FROM OLD THEN
  RAISE EXCEPTION 'assistant_action_terminal' USING ERRCODE='55000';
 END IF;
 IF NEW.revision<>OLD.revision+1 THEN
  RAISE EXCEPTION 'assistant_action_revision' USING ERRCODE='55000';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER assistant_action_terminal_guard BEFORE UPDATE ON design.assistant_action_links
 FOR EACH ROW EXECUTE FUNCTION design.guard_assistant_action_terminal();
DO $$ DECLARE table_name text; BEGIN
 FOREACH table_name IN ARRAY ARRAY['assistant_global_messages','assistant_jobs','assistant_global_events','assistant_outputs','assistant_publications'] LOOP
  EXECUTE format('CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON design.%I FOR EACH ROW EXECUTE FUNCTION design.append_only()',table_name);
 END LOOP;
END $$;
REVOKE ALL ON design.assistant_global_conversations,design.assistant_global_messages,design.assistant_jobs,
 design.assistant_global_runs,design.assistant_global_calls,design.assistant_global_outbox,design.assistant_global_events,
 design.assistant_outputs,design.assistant_publications,design.assistant_action_links
 FROM PUBLIC,tender_api,tender_worker,tender_research_worker,tender_score_read,tender_research_read;
REVOKE ALL ON FUNCTION design.guard_assistant_action_terminal() FROM PUBLIC;
GRANT SELECT ON design.assistant_global_conversations,design.assistant_global_messages,design.assistant_jobs,
 design.assistant_global_runs,design.assistant_global_calls,design.assistant_global_outbox,design.assistant_global_events,
 design.assistant_outputs,design.assistant_publications,design.assistant_action_links TO tender_api;
GRANT INSERT(id,created_by) ON design.assistant_global_conversations TO tender_api;
GRANT UPDATE(state,revision,last_seq) ON design.assistant_global_conversations TO tender_api;
GRANT INSERT ON design.assistant_global_messages,design.assistant_jobs,design.assistant_global_events,
 design.assistant_publications TO tender_api;
GRANT INSERT(id,conversation_id,actor_id,command_id,input_digest,max_tokens) ON design.assistant_global_runs TO tender_api;
GRANT UPDATE(cancel_requested_at) ON design.assistant_global_runs TO tender_api;
GRANT INSERT(id,run_id,payload,payload_digest) ON design.assistant_global_outbox TO tender_api;
GRANT INSERT(action_id,job_id,tool) ON design.assistant_action_links TO tender_api;
GRANT UPDATE(state,revision,resolved_by,resolved_at,error_code) ON design.assistant_action_links TO tender_api;
-- Ordinary Worker can only read already frozen job inputs and its own ledger/transport/results.
-- No global conversation/message/event, Action, commands, bidder or engineering identity access added.
GRANT SELECT ON design.assistant_jobs,design.assistant_global_runs,design.assistant_global_calls,
 design.assistant_global_outbox,design.assistant_outputs TO tender_worker;
GRANT UPDATE(state,fence,owner_token,lease_until,finished_at,error_code) ON design.assistant_global_runs TO tender_worker;
GRANT INSERT(id,run_id,intent_digest,request_digest,profile_digest,max_tokens) ON design.assistant_global_calls TO tender_worker;
GRANT UPDATE(state,provider_request_id,usage,response_ref) ON design.assistant_global_calls TO tender_worker;
GRANT UPDATE(published_at,claim_token,lease_until,publish_attempts) ON design.assistant_global_outbox TO tender_worker;
GRANT INSERT ON design.assistant_outputs TO tender_worker;
COMMIT;
