-- 全新系统领域DDL；先执行core.sql。design为目标原型schema，不连接旧系统。
BEGIN;
SET search_path TO design,public;
CREATE TABLE engineering_projects(id uuid PRIMARY KEY,title text NOT NULL,region text,engineering_type text);
CREATE TABLE bid_rounds(id uuid PRIMARY KEY,engineering_project_id uuid NOT NULL REFERENCES engineering_projects(id),lot text,round_no integer NOT NULL CHECK(round_no>0),UNIQUE(engineering_project_id,lot,round_no));
CREATE TABLE bidders(id uuid PRIMARY KEY,name text NOT NULL,company_facts jsonb NOT NULL DEFAULT '{}');
CREATE TABLE bid_profiles(
 bid_id uuid PRIMARY KEY REFERENCES bids(id),round_id uuid NOT NULL REFERENCES bid_rounds(id),bidder_id uuid NOT NULL REFERENCES bidders(id),
 phase text NOT NULL CHECK(phase IN ('preparation','working','submitted','closed','terminated')),archived_at timestamptz,deadline timestamptz,
 current_body_id uuid,current_material_id uuid,current_review_id uuid,current_draft_id uuid,
 FOREIGN KEY(bid_id,current_body_id) REFERENCES document_versions(bid_id,id),
 FOREIGN KEY(bid_id,current_material_id) REFERENCES material_snapshots(bid_id,id),
 FOREIGN KEY(bid_id,current_review_id) REFERENCES review_runs(bid_id,id),FOREIGN KEY(bid_id,current_draft_id) REFERENCES drafts(bid_id,id)
);
CREATE TABLE credentials(member_id uuid PRIMARY KEY REFERENCES members(id),login_name text NOT NULL UNIQUE,password_hash text NOT NULL,disabled_at timestamptz);
CREATE TABLE sessions(id uuid PRIMARY KEY,member_id uuid NOT NULL REFERENCES members(id),token_digest sha256 NOT NULL UNIQUE,csrf_digest sha256 NOT NULL,expires_at timestamptz NOT NULL,revoked_at timestamptz);
CREATE INDEX active_sessions ON sessions(member_id,expires_at) WHERE revoked_at IS NULL;
CREATE TABLE upload_batches(id uuid PRIMARY KEY,bid_id uuid NOT NULL REFERENCES bids(id),revision bigint NOT NULL CHECK(revision>0),state text NOT NULL CHECK(state IN ('open','verifying','committed','failed')),created_by uuid NOT NULL REFERENCES members(id),UNIQUE(bid_id,id));
CREATE TABLE uploads(
 id uuid PRIMARY KEY,bid_id uuid NOT NULL,batch_id uuid NOT NULL,filename text NOT NULL,role text NOT NULL,
 declared_digest sha256 NOT NULL,declared_size bigint NOT NULL CHECK(declared_size>0),staging_key text NOT NULL UNIQUE,
 state text NOT NULL CHECK(state IN ('authorized','uploaded','verifying','verified','quarantined','expired')),expires_at timestamptz NOT NULL,
 verified_version_id uuid,error_code text,FOREIGN KEY(bid_id,batch_id) REFERENCES upload_batches(bid_id,id),
 FOREIGN KEY(bid_id,verified_version_id) REFERENCES document_versions(bid_id,id),UNIQUE(bid_id,id)
);
CREATE TABLE parse_runs(
 id uuid PRIMARY KEY,bid_id uuid NOT NULL,document_version_id uuid NOT NULL,parser_digest sha256 NOT NULL,config_digest sha256 NOT NULL,
 page_count integer NOT NULL CHECK(page_count>=0),coverage text NOT NULL CHECK(coverage IN ('complete','partial','failed')),
 gaps jsonb NOT NULL CHECK(jsonb_typeof(gaps)='array'),created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(bid_id,document_version_id) REFERENCES document_versions(bid_id,id),UNIQUE(bid_id,id),UNIQUE(bid_id,id,document_version_id)
);
CREATE TABLE pages(
 bid_id uuid NOT NULL,parse_id uuid NOT NULL,physical_page integer NOT NULL CHECK(physical_page>0),printed_label text,
 width numeric NOT NULL CHECK(width>0 AND width<>'NaN'),height numeric NOT NULL CHECK(height>0 AND height<>'NaN'),
 rotation integer NOT NULL CHECK(rotation IN (0,90,180,270)),quality text NOT NULL CHECK(quality IN ('usable','partial','failed')),gaps jsonb NOT NULL,
 PRIMARY KEY(parse_id,physical_page),UNIQUE(bid_id,parse_id,physical_page),FOREIGN KEY(bid_id,parse_id) REFERENCES parse_runs(bid_id,id)
);
CREATE TABLE evidence_blocks(
 id uuid PRIMARY KEY,bid_id uuid NOT NULL,parse_id uuid NOT NULL,physical_page integer NOT NULL,ordinal integer NOT NULL CHECK(ordinal>=0),
 kind text NOT NULL CHECK(kind IN ('text','table','visual_observation')),text_content text NOT NULL,bbox jsonb,structure jsonb,
 digest sha256 NOT NULL,UNIQUE(bid_id,id),UNIQUE(parse_id,physical_page,ordinal),
 FOREIGN KEY(bid_id,parse_id,physical_page) REFERENCES pages(bid_id,parse_id,physical_page)
);
CREATE TABLE rule_sets(id uuid PRIMARY KEY,bid_id uuid NOT NULL,material_id uuid NOT NULL,revision bigint NOT NULL CHECK(revision>0),digest sha256 NOT NULL,coverage text NOT NULL CHECK(coverage IN ('complete','partial','conflict')),FOREIGN KEY(bid_id,material_id) REFERENCES material_snapshots(bid_id,id),UNIQUE(bid_id,id));
CREATE TABLE rule_items(
 id uuid PRIMARY KEY,bid_id uuid NOT NULL,rule_set_id uuid NOT NULL,kind text NOT NULL CHECK(kind IN ('disqualification','mandatory','scoring','advisory')),
 statement text NOT NULL,conditions jsonb NOT NULL,exceptions jsonb NOT NULL,max_points numeric,
 evidence_ids uuid[] NOT NULL CHECK(cardinality(evidence_ids)>0),bands jsonb NOT NULL,
 CHECK(max_points IS NULL OR (max_points>0 AND max_points<>'NaN' AND max_points<>'Infinity')),
 FOREIGN KEY(bid_id,rule_set_id) REFERENCES rule_sets(bid_id,id),UNIQUE(bid_id,id)
);
CREATE TABLE fact_sets(id uuid PRIMARY KEY,bid_id uuid NOT NULL REFERENCES bids(id),digest sha256 NOT NULL,UNIQUE(bid_id,id));
CREATE TABLE fact_items(id uuid PRIMARY KEY,bid_id uuid NOT NULL,fact_set_id uuid NOT NULL,subject text NOT NULL,scope text NOT NULL,name text NOT NULL,value jsonb NOT NULL,unit text,evidence_ids uuid[] NOT NULL,FOREIGN KEY(bid_id,fact_set_id) REFERENCES fact_sets(bid_id,id),UNIQUE(bid_id,id));
CREATE TABLE decisions(
 id uuid PRIMARY KEY,bid_id uuid NOT NULL REFERENCES bids(id),object_kind text NOT NULL,object_id uuid NOT NULL,object_revision bigint NOT NULL,
 decision text NOT NULL,reason text NOT NULL,evidence_ids uuid[] NOT NULL,actor_id uuid NOT NULL REFERENCES members(id),created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX decisions_current ON decisions(bid_id,object_kind,object_id,created_at DESC,id DESC);
CREATE TABLE findings(
 id uuid PRIMARY KEY,bid_id uuid NOT NULL,review_id uuid NOT NULL,rule_id uuid,
 category text NOT NULL CHECK(category IN ('disqualification','response','consistency','scope','improvement')),
 severity text NOT NULL CHECK(severity IN ('critical','major','minor','info')),
 verdict text NOT NULL CHECK(verdict IN ('satisfied','violated','not_found','unable_to_judge','not_applicable')),
 statement text NOT NULL,searched_ranges jsonb NOT NULL,evidence_ids uuid[] NOT NULL,carried_from_id uuid,
 FOREIGN KEY(bid_id,review_id) REFERENCES review_runs(bid_id,id),FOREIGN KEY(bid_id,rule_id) REFERENCES rule_items(bid_id,id),
 UNIQUE(bid_id,id),FOREIGN KEY(bid_id,carried_from_id) REFERENCES findings(bid_id,id)
);
CREATE INDEX findings_review ON findings(bid_id,review_id,severity,id);
CREATE TABLE template_versions(id uuid PRIMARY KEY,name text NOT NULL,kind text NOT NULL CHECK(kind IN ('outline','layout')),schema_digest sha256 NOT NULL,config jsonb NOT NULL,fontset_digest sha256 NOT NULL);
CREATE TABLE writing_plans(id uuid PRIMARY KEY,bid_id uuid NOT NULL,section_id uuid NOT NULL,base_section_revision bigint NOT NULL,rule_ids uuid[] NOT NULL,fact_set_id uuid,source_version_ids uuid[] NOT NULL,plan jsonb NOT NULL,FOREIGN KEY(bid_id,section_id) REFERENCES sections(bid_id,id),FOREIGN KEY(bid_id,fact_set_id) REFERENCES fact_sets(bid_id,id));
CREATE TABLE conversations(id uuid PRIMARY KEY,bid_id uuid NOT NULL REFERENCES bids(id),state text NOT NULL CHECK(state IN ('active','closed','hidden')),revision bigint NOT NULL CHECK(revision>0),created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(bid_id,id));
CREATE TABLE messages(id uuid PRIMARY KEY,bid_id uuid NOT NULL,conversation_id uuid NOT NULL,client_message_id uuid,role text NOT NULL CHECK(role IN ('user','assistant','tool')),parts jsonb NOT NULL CHECK(jsonb_typeof(parts)='array'),created_by uuid REFERENCES members(id),created_at timestamptz NOT NULL DEFAULT now(),FOREIGN KEY(bid_id,conversation_id) REFERENCES conversations(bid_id,id),UNIQUE(conversation_id,client_message_id));
CREATE TABLE outcomes(
 id uuid PRIMARY KEY,bid_id uuid NOT NULL,submission_id uuid NOT NULL,score_kind text NOT NULL CHECK(score_kind IN ('technical_total','comprehensive_total','unknown')),
 value numeric,maximum numeric,source_version_id uuid NOT NULL,known_at timestamptz NOT NULL,supersedes_id uuid,created_by uuid NOT NULL REFERENCES members(id),
 CHECK((value IS NULL AND maximum IS NULL) OR (value IS NOT NULL AND maximum IS NOT NULL AND value>=0 AND value<>'NaN' AND value<>'Infinity' AND maximum>0 AND maximum<>'NaN' AND maximum<>'Infinity' AND value<=maximum)),
 FOREIGN KEY(bid_id,submission_id) REFERENCES submission_records(bid_id,id),FOREIGN KEY(bid_id,source_version_id) REFERENCES document_versions(bid_id,id),
 UNIQUE(bid_id,id),FOREIGN KEY(bid_id,supersedes_id) REFERENCES outcomes(bid_id,id)
);
CREATE TABLE outcome_heads(submission_id uuid PRIMARY KEY,bid_id uuid NOT NULL,outcome_id uuid NOT NULL,revision bigint NOT NULL CHECK(revision>0),FOREIGN KEY(bid_id,submission_id) REFERENCES submission_records(bid_id,id),FOREIGN KEY(bid_id,outcome_id) REFERENCES outcomes(bid_id,id));
CREATE TABLE task_definitions(id uuid PRIMARY KEY,bid_id uuid NOT NULL REFERENCES bids(id),revision bigint NOT NULL CHECK(revision>0),name text NOT NULL,purpose text NOT NULL CHECK(purpose IN ('assist','compliance','export')),instructions text NOT NULL,paused boolean NOT NULL DEFAULT false,UNIQUE(bid_id,id));
CREATE TABLE schedules(id uuid PRIMARY KEY,bid_id uuid NOT NULL,definition_id uuid NOT NULL,definition_revision bigint NOT NULL,cron text NOT NULL,timezone text NOT NULL,next_fire_at timestamptz NOT NULL,paused boolean NOT NULL DEFAULT false,FOREIGN KEY(bid_id,definition_id) REFERENCES task_definitions(bid_id,id));
CREATE TABLE schedule_receipts(schedule_id uuid NOT NULL REFERENCES schedules(id),scheduled_at timestamptz NOT NULL,operation_id uuid NOT NULL REFERENCES operations(id),PRIMARY KEY(schedule_id,scheduled_at));
CREATE TABLE operation_steps(operation_id uuid NOT NULL REFERENCES operations(id),step_key text NOT NULL,input_digest sha256 NOT NULL,handler_version text NOT NULL,required boolean NOT NULL,depends_on text[] NOT NULL,dispatch_generation bigint NOT NULL CHECK(dispatch_generation>0),PRIMARY KEY(operation_id,step_key,input_digest));
ALTER TABLE outbox ADD COLUMN claim_token uuid,ADD COLUMN lease_until timestamptz;
ALTER TABLE step_receipts ADD COLUMN owner_token uuid,ADD COLUMN lease_until timestamptz,ADD COLUMN retry_at timestamptz,ADD COLUMN failure_code text;
CREATE TABLE model_calls(id uuid PRIMARY KEY,operation_id uuid NOT NULL REFERENCES operations(id),logical_call_key text NOT NULL,attempt_no integer NOT NULL CHECK(attempt_no BETWEEN 1 AND 3),request_digest sha256 NOT NULL,profile_digest sha256 NOT NULL,state text NOT NULL CHECK(state IN ('reserved','succeeded','failed','unknown')),provider_request_id text,usage jsonb,response_ref uuid REFERENCES blobs(id),created_at timestamptz NOT NULL DEFAULT now(),UNIQUE(operation_id,logical_call_key,attempt_no));
CREATE TABLE chapter_packages(id uuid PRIMARY KEY,bid_id uuid NOT NULL,review_id uuid NOT NULL,rule_id uuid NOT NULL,parse_id uuid NOT NULL,required_ranges jsonb NOT NULL,read_ranges jsonb NOT NULL,unread_ranges jsonb NOT NULL,manifest_blob_id uuid NOT NULL REFERENCES blobs(id),digest sha256 NOT NULL,FOREIGN KEY(bid_id,review_id) REFERENCES review_runs(bid_id,id),FOREIGN KEY(bid_id,rule_id) REFERENCES rule_items(bid_id,id),FOREIGN KEY(bid_id,parse_id) REFERENCES parse_runs(bid_id,id),UNIQUE(bid_id,id));
CREATE TABLE item_judgments(id uuid PRIMARY KEY,bid_id uuid NOT NULL,chapter_package_id uuid NOT NULL,replicate integer NOT NULL CHECK(replicate BETWEEN 1 AND 3),status text NOT NULL CHECK(status IN ('valid','invalid','unable_to_judge')),payload jsonb NOT NULL,model_call_id uuid NOT NULL REFERENCES model_calls(id),FOREIGN KEY(bid_id,chapter_package_id) REFERENCES chapter_packages(bid_id,id),UNIQUE(chapter_package_id,replicate));
CREATE TABLE model_artifacts(id uuid PRIMARY KEY,artifact_blob_id uuid NOT NULL REFERENCES blobs(id),method_digest sha256 NOT NULL,scope jsonb NOT NULL,training_manifest_digest sha256 NOT NULL,created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE model_acceptances(id uuid PRIMARY KEY,model_id uuid NOT NULL REFERENCES model_artifacts(id),protocol_digest sha256 NOT NULL,dataset_digest sha256 NOT NULL,report_blob_id uuid NOT NULL REFERENCES blobs(id),decision text NOT NULL CHECK(decision IN ('accepted','rejected')),actor_id uuid NOT NULL REFERENCES members(id));
CREATE TABLE model_routes(route_key text PRIMARY KEY,model_id uuid REFERENCES model_artifacts(id),acceptance_id uuid REFERENCES model_acceptances(id),revision bigint NOT NULL CHECK(revision>0),rollback_model_id uuid REFERENCES model_artifacts(id));
ALTER TABLE model_acceptances ADD CONSTRAINT acceptance_identity UNIQUE(model_id,id,decision);
ALTER TABLE model_routes ADD CONSTRAINT route_model_pair CHECK((model_id IS NULL AND acceptance_id IS NULL) OR (model_id IS NOT NULL AND acceptance_id IS NOT NULL));
ALTER TABLE model_routes ADD COLUMN acceptance_decision text NOT NULL DEFAULT 'accepted' CHECK(acceptance_decision='accepted');
ALTER TABLE model_routes ADD CONSTRAINT accepted_model_route FOREIGN KEY(model_id,acceptance_id,acceptance_decision) REFERENCES model_acceptances(model_id,id,decision);
CREATE TABLE predictions(id uuid PRIMARY KEY,bid_id uuid NOT NULL,review_id uuid NOT NULL,model_id uuid REFERENCES model_artifacts(id),method_digest sha256 NOT NULL,mode text NOT NULL CHECK(mode IN ('partial','raw_uncalibrated','accepted','unavailable')),value numeric,maximum numeric,lower_bound numeric,upper_bound numeric,reasons jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),FOREIGN KEY(bid_id,review_id) REFERENCES review_runs(bid_id,id),CHECK(value IS NULL OR (value IS NOT NULL AND maximum IS NOT NULL AND value>=0 AND value<>'NaN' AND value<>'Infinity' AND maximum>0 AND maximum<>'NaN' AND maximum<>'Infinity' AND value<=maximum)),CHECK((lower_bound IS NULL AND upper_bound IS NULL) OR (lower_bound IS NOT NULL AND upper_bound IS NOT NULL AND maximum IS NOT NULL AND lower_bound>=0 AND upper_bound>=lower_bound AND upper_bound<=maximum AND lower_bound<>'NaN' AND upper_bound<>'NaN')),UNIQUE(bid_id,id));
CREATE TABLE samples(id uuid PRIMARY KEY,bid_id uuid NOT NULL,submission_id uuid NOT NULL,outcome_id uuid NOT NULL,source_group_id uuid NOT NULL,known_at timestamptz NOT NULL,admission text NOT NULL CHECK(admission IN ('pending','admitted','rejected')),reasons jsonb NOT NULL,FOREIGN KEY(bid_id,submission_id) REFERENCES submission_records(bid_id,id),FOREIGN KEY(bid_id,outcome_id) REFERENCES outcomes(bid_id,id),UNIQUE(submission_id,outcome_id));
CREATE TABLE experiments(id uuid PRIMARY KEY,protocol_digest sha256 NOT NULL,dataset_manifest_blob_id uuid NOT NULL REFERENCES blobs(id),split_manifest_blob_id uuid NOT NULL REFERENCES blobs(id),method_digest sha256 NOT NULL,seed bigint NOT NULL,state text NOT NULL CHECK(state IN ('created','running','succeeded','failed')),report_blob_id uuid REFERENCES blobs(id));
CREATE TABLE import_batches(id uuid PRIMARY KEY,manifest_blob_id uuid NOT NULL REFERENCES blobs(id),mode text NOT NULL CHECK(mode IN ('dry_run','confirmed_import')),revision bigint NOT NULL CHECK(revision>0),created_by uuid NOT NULL REFERENCES members(id));
CREATE TABLE import_records(batch_id uuid NOT NULL REFERENCES import_batches(id),external_record_id text NOT NULL,source_digest sha256 NOT NULL,state text NOT NULL CHECK(state IN ('pending','imported','conflict','failed')),bid_id uuid REFERENCES bids(id),errors jsonb NOT NULL,PRIMARY KEY(batch_id,external_record_id),UNIQUE(batch_id,source_digest,external_record_id));
CREATE TABLE pricing_plans(id uuid PRIMARY KEY,bid_id uuid NOT NULL REFERENCES bids(id),formula_digest sha256 NOT NULL,input_manifest jsonb NOT NULL,result jsonb NOT NULL,created_by uuid NOT NULL REFERENCES members(id),created_at timestamptz NOT NULL DEFAULT now());
CREATE TABLE artifact_presentations(id uuid PRIMARY KEY,bid_id uuid NOT NULL,review_id uuid NOT NULL,artifact_kind text NOT NULL CHECK(artifact_kind IN ('review_report','annotated_review_copy')),format text NOT NULL CHECK(format IN ('docx','pdf')),presentation_revision bigint NOT NULL CHECK(presentation_revision>0),source_impact_revision bigint NOT NULL CHECK(source_impact_revision>0),blob_id uuid NOT NULL REFERENCES blobs(id),FOREIGN KEY(bid_id,review_id) REFERENCES review_runs(bid_id,id),UNIQUE(review_id,artifact_kind,format,presentation_revision,source_impact_revision));
ALTER TABLE decisions ADD CONSTRAINT decisions_bid_identity UNIQUE(bid_id,id);
CREATE TABLE decision_heads(bid_id uuid NOT NULL REFERENCES bids(id),object_kind text NOT NULL,object_id uuid NOT NULL,revision bigint NOT NULL CHECK(revision>0),decision_id uuid,PRIMARY KEY(bid_id,object_kind,object_id),FOREIGN KEY(bid_id,decision_id) REFERENCES decisions(bid_id,id));
CREATE TABLE task_definition_versions(id uuid PRIMARY KEY,bid_id uuid NOT NULL,definition_id uuid NOT NULL,revision bigint NOT NULL CHECK(revision>0),instructions text NOT NULL,config jsonb NOT NULL,digest sha256 NOT NULL,FOREIGN KEY(bid_id,definition_id) REFERENCES task_definitions(bid_id,id),UNIQUE(definition_id,revision),UNIQUE(bid_id,id));
CREATE TABLE task_runs(id uuid PRIMARY KEY,bid_id uuid NOT NULL,definition_version_id uuid NOT NULL,operation_id uuid NOT NULL,frozen_manifest jsonb NOT NULL,FOREIGN KEY(bid_id,definition_version_id) REFERENCES task_definition_versions(bid_id,id),FOREIGN KEY(bid_id,operation_id) REFERENCES operations(bid_id,id));
CREATE TABLE pricing_simulations(id uuid PRIMARY KEY,bid_id uuid NOT NULL REFERENCES bids(id),formula_digest sha256 NOT NULL,input_manifest jsonb NOT NULL,result jsonb NOT NULL,created_at timestamptz NOT NULL DEFAULT now());
ALTER TABLE pricing_plans ADD COLUMN simulation_id uuid REFERENCES pricing_simulations(id);
CREATE DOMAIN nonnegative_amount AS numeric CHECK(VALUE>=0 AND VALUE NOT IN ('NaN','Infinity','-Infinity'));
CREATE TABLE document_files(
 id uuid PRIMARY KEY,bid_id uuid NOT NULL REFERENCES bids(id),role text NOT NULL CHECK(role IN ('tender','supplement','drawing','boq','body','official_result','other')),
 revision bigint NOT NULL CHECK(revision>0),head_version_id uuid,UNIQUE(bid_id,id)
);
CREATE TABLE document_file_versions(
 bid_id uuid NOT NULL,file_id uuid NOT NULL,version_id uuid NOT NULL,revision bigint NOT NULL CHECK(revision>0),previous_version_id uuid,
 PRIMARY KEY(file_id,revision),UNIQUE(bid_id,file_id,version_id),UNIQUE(bid_id,version_id),
 FOREIGN KEY(bid_id,file_id) REFERENCES document_files(bid_id,id),FOREIGN KEY(bid_id,version_id) REFERENCES document_versions(bid_id,id),
 FOREIGN KEY(bid_id,file_id,previous_version_id) REFERENCES document_file_versions(bid_id,file_id,version_id),
 CHECK((revision=1)=(previous_version_id IS NULL)),CHECK(version_id IS DISTINCT FROM previous_version_id)
);
ALTER TABLE document_files ADD CONSTRAINT file_head_belongs_to_file FOREIGN KEY(bid_id,id,head_version_id) REFERENCES document_file_versions(bid_id,file_id,version_id) DEFERRABLE INITIALLY DEFERRED;
CREATE TABLE upload_transfers(
 upload_id uuid PRIMARY KEY,bid_id uuid NOT NULL,file_id uuid NOT NULL,expected_file_revision bigint NOT NULL CHECK(expected_file_revision>0),
 mode text NOT NULL CHECK(mode IN ('single','multipart')),provider_upload_id text,part_size bigint NOT NULL CHECK(part_size>0),part_count integer NOT NULL CHECK(part_count BETWEEN 1 AND 10000),
 revision bigint NOT NULL CHECK(revision>0),aborted_at timestamptz,FOREIGN KEY(bid_id,upload_id) REFERENCES uploads(bid_id,id),FOREIGN KEY(bid_id,file_id) REFERENCES document_files(bid_id,id),
 CHECK(mode<>'single' OR part_count=1)
);
CREATE TABLE upload_parts(
 upload_id uuid NOT NULL REFERENCES upload_transfers(upload_id),part_number integer NOT NULL CHECK(part_number BETWEEN 1 AND 10000),
 byte_size bigint NOT NULL CHECK(byte_size>0),etag text NOT NULL CHECK(length(etag)>0),observed_at timestamptz NOT NULL,PRIMARY KEY(upload_id,part_number)
);
CREATE TABLE fact_heads(
 bid_id uuid PRIMARY KEY REFERENCES bids(id),fact_set_id uuid,revision bigint NOT NULL CHECK(revision>=0),
 FOREIGN KEY(bid_id,fact_set_id) REFERENCES fact_sets(bid_id,id),CHECK((revision=0)=(fact_set_id IS NULL))
);
CREATE TABLE fact_set_changes(
 id uuid PRIMARY KEY,bid_id uuid NOT NULL,new_set_id uuid NOT NULL,previous_set_id uuid,changed_fact_id uuid,kind text NOT NULL CHECK(kind IN ('add','replace','withdraw')),
 reason text NOT NULL CHECK(length(reason)>0),actor_id uuid NOT NULL REFERENCES members(id),created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(bid_id,new_set_id) REFERENCES fact_sets(bid_id,id),FOREIGN KEY(bid_id,previous_set_id) REFERENCES fact_sets(bid_id,id),FOREIGN KEY(bid_id,changed_fact_id) REFERENCES fact_items(bid_id,id),
 CHECK(new_set_id IS DISTINCT FROM previous_set_id),CHECK(kind='add' OR changed_fact_id IS NOT NULL)
);
CREATE TABLE rule_carryovers(
 bid_id uuid NOT NULL,new_rule_id uuid PRIMARY KEY,from_rule_id uuid NOT NULL,from_decision_id uuid NOT NULL,equivalence_digest sha256 NOT NULL,adopted_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(bid_id,new_rule_id) REFERENCES rule_items(bid_id,id),FOREIGN KEY(bid_id,from_rule_id) REFERENCES rule_items(bid_id,id),FOREIGN KEY(bid_id,from_decision_id) REFERENCES decisions(bid_id,id),CHECK(new_rule_id<>from_rule_id)
);
CREATE TABLE matrix_checks(
 bid_id uuid NOT NULL,review_id uuid NOT NULL,rule_id uuid NOT NULL,state text NOT NULL CHECK(state IN ('pending','running','succeeded','failed','cancelled')),
 verdict text CHECK(verdict IN ('satisfied','violated','not_found','unable_to_judge','not_applicable')),error_code text,evidence_ids uuid[] NOT NULL,revision bigint NOT NULL CHECK(revision>0),
 PRIMARY KEY(review_id,rule_id),FOREIGN KEY(bid_id,review_id) REFERENCES review_runs(bid_id,id),FOREIGN KEY(bid_id,rule_id) REFERENCES rule_items(bid_id,id),
 CHECK((state='succeeded')=(verdict IS NOT NULL)),CHECK((state='failed')=(error_code IS NOT NULL))
);
CREATE TABLE validation_diagnostics(
 id uuid PRIMARY KEY,bid_id uuid NOT NULL,operation_id uuid NOT NULL,step_key text NOT NULL,code text NOT NULL CHECK(code IN ('quote_not_found','source_not_allowed','unsupported_quantity','unit_mismatch','scope_mismatch','invalid_structure','incomplete_range')),
 field_path text NOT NULL,evidence_ids uuid[] NOT NULL,action text NOT NULL CHECK(action IN ('rejected','needs_review')),message text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),FOREIGN KEY(bid_id,operation_id) REFERENCES operations(bid_id,id)
);
CREATE INDEX diagnostics_operation ON validation_diagnostics(operation_id,id);
CREATE TABLE experiment_previews(
 id uuid PRIMARY KEY,experiment_id uuid NOT NULL REFERENCES experiments(id),revision bigint NOT NULL CHECK(revision>0),manifest_digest sha256 NOT NULL,
 request jsonb NOT NULL CHECK(jsonb_typeof(request)='object'),price_config_digest sha256 NOT NULL,estimated_calls bigint NOT NULL CHECK(estimated_calls>=0),estimated_tokens bigint NOT NULL CHECK(estimated_tokens>=0),
 estimated_fee nonnegative_amount NOT NULL,fee_limit nonnegative_amount NOT NULL,currency text NOT NULL CHECK(currency IN ('CNY','USD')),expires_at timestamptz NOT NULL,created_by uuid NOT NULL REFERENCES members(id),UNIQUE(experiment_id,id)
);
CREATE TABLE research_runs(
 id uuid PRIMARY KEY,experiment_id uuid NOT NULL,preview_id uuid NOT NULL,revision bigint NOT NULL CHECK(revision>0),manifest_digest sha256 NOT NULL,
 state text NOT NULL CHECK(state IN ('queued','running','paused','succeeded','failed','cancelled')),fee_limit nonnegative_amount NOT NULL,spent_fee nonnegative_amount NOT NULL DEFAULT 0,reserved_fee nonnegative_amount NOT NULL DEFAULT 0,
 currency text NOT NULL CHECK(currency IN ('CNY','USD')),cancel_requested_at timestamptz,FOREIGN KEY(experiment_id,preview_id) REFERENCES experiment_previews(experiment_id,id),UNIQUE(preview_id),
 CHECK(state<>'cancelled' OR cancel_requested_at IS NOT NULL)
);
ALTER TABLE samples ADD CONSTRAINT samples_bid_identity UNIQUE(bid_id,id);
CREATE TABLE research_run_items(
 run_id uuid NOT NULL REFERENCES research_runs(id),sample_id uuid NOT NULL,bid_id uuid NOT NULL,repeat_index integer NOT NULL CHECK(repeat_index BETWEEN 1 AND 10),
 state text NOT NULL CHECK(state IN ('pending','running','succeeded','failed','cancelled')),operation_id uuid,error_code text,result_blob_id uuid REFERENCES blobs(id),
 PRIMARY KEY(run_id,sample_id,repeat_index),UNIQUE(run_id,sample_id,repeat_index,bid_id),FOREIGN KEY(bid_id,sample_id) REFERENCES samples(bid_id,id),FOREIGN KEY(bid_id,operation_id) REFERENCES operations(bid_id,id)
);
CREATE TABLE research_run_attempts(
 run_id uuid NOT NULL,sample_id uuid NOT NULL,repeat_index integer NOT NULL,bid_id uuid NOT NULL,attempt_no integer NOT NULL CHECK(attempt_no BETWEEN 1 AND 3),operation_id uuid NOT NULL,
 created_at timestamptz NOT NULL DEFAULT now(),PRIMARY KEY(run_id,sample_id,repeat_index,attempt_no),UNIQUE(operation_id),
 FOREIGN KEY(run_id,sample_id,repeat_index,bid_id) REFERENCES research_run_items(run_id,sample_id,repeat_index,bid_id),FOREIGN KEY(bid_id,operation_id) REFERENCES operations(bid_id,id)
);
ALTER TABLE model_calls ADD COLUMN research_run_id uuid REFERENCES research_runs(id);
ALTER TABLE model_calls ADD COLUMN reserved_amount nonnegative_amount;
ALTER TABLE model_calls ADD CONSTRAINT research_call_cost CHECK((research_run_id IS NULL)=(reserved_amount IS NULL));
CREATE TABLE shadow_comparisons(
 id uuid PRIMARY KEY,bid_id uuid NOT NULL,review_id uuid NOT NULL,operation_id uuid NOT NULL,business_prediction_id uuid NOT NULL,candidate_model_id uuid NOT NULL REFERENCES model_artifacts(id),
 input_digest sha256 NOT NULL,method_digest sha256 NOT NULL,comparison_protocol_digest sha256 NOT NULL,state text NOT NULL CHECK(state IN ('queued','running','succeeded','failed','cancelled')),
 result_blob_id uuid REFERENCES blobs(id),error_code text,FOREIGN KEY(bid_id,review_id) REFERENCES review_runs(bid_id,id),FOREIGN KEY(bid_id,operation_id) REFERENCES operations(bid_id,id),FOREIGN KEY(bid_id,business_prediction_id) REFERENCES predictions(bid_id,id),
 CHECK((state='succeeded')=(result_blob_id IS NOT NULL)),CHECK((state='failed')=(error_code IS NOT NULL))
);
CREATE FUNCTION reserve_research_fee(target_run uuid,amount nonnegative_amount) RETURNS boolean LANGUAGE plpgsql AS $$
BEGIN
 IF amount IS NULL THEN RAISE EXCEPTION 'amount required' USING ERRCODE='22004'; END IF;
 UPDATE design.research_runs SET reserved_fee=reserved_fee+amount,revision=revision+1 WHERE id=target_run AND state='running' AND cancel_requested_at IS NULL AND spent_fee+reserved_fee+amount<=fee_limit;
 RETURN FOUND;
END $$;
DO $$ DECLARE n text; BEGIN
 FOREACH n IN ARRAY ARRAY['parse_runs','pages','evidence_blocks','rule_sets','rule_items','fact_sets','fact_items','decisions','findings','template_versions','writing_plans','outcomes','schedule_receipts','chapter_packages','item_judgments','model_artifacts','model_acceptances','task_definition_versions','task_runs','pricing_simulations','pricing_plans','artifact_presentations'] LOOP
 EXECUTE format('CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON design.%I FOR EACH ROW EXECUTE FUNCTION design.append_only()',n);
 END LOOP;
END $$;
DO $$ DECLARE n text; BEGIN
 FOREACH n IN ARRAY ARRAY['document_file_versions','fact_set_changes','rule_carryovers','validation_diagnostics','experiment_previews','research_run_attempts'] LOOP
 EXECUTE format('CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON design.%I FOR EACH ROW EXECUTE FUNCTION design.append_only()',n);
 END LOOP;
END $$;
-- 内容去重时点是bid锁内稳定上下文，不使用每次HTTP请求时间。
ALTER TABLE review_slots ADD COLUMN context_fingerprint sha256;
ALTER TABLE review_slots ADD COLUMN context_as_of timestamptz;
ALTER TABLE review_slots ADD COLUMN context_valid_until timestamptz;
ALTER TABLE review_slots ADD CONSTRAINT review_context_shape CHECK((context_fingerprint IS NULL)=(context_as_of IS NULL));
ALTER TABLE review_slots ADD CONSTRAINT review_context_window CHECK(context_valid_until IS NULL OR context_valid_until>context_as_of);
ALTER TABLE review_inputs ADD COLUMN evidence_manifest_blob_id uuid NOT NULL REFERENCES blobs(id);
ALTER TABLE review_inputs ADD COLUMN manifest_digest sha256 NOT NULL;
ALTER TABLE review_inputs ADD COLUMN as_of timestamptz NOT NULL;
ALTER TABLE review_inputs ADD COLUMN evaluation_context_digest sha256 NOT NULL;
ALTER TABLE review_results ADD COLUMN coverage_layers jsonb NOT NULL CHECK(jsonb_typeof(coverage_layers)='object');
ALTER TABLE uploads ADD COLUMN verified_retain_until timestamptz;
CREATE TABLE upload_bindings(
 id uuid PRIMARY KEY,bid_id uuid NOT NULL,upload_id uuid NOT NULL,file_id uuid NOT NULL,
 expected_file_revision bigint NOT NULL CHECK(expected_file_revision>0),previous_binding_id uuid,
 reason text NOT NULL CHECK(length(reason)>0),command_id uuid NOT NULL REFERENCES commands(id),created_by uuid NOT NULL REFERENCES members(id),created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(bid_id,upload_id) REFERENCES uploads(bid_id,id),FOREIGN KEY(bid_id,file_id) REFERENCES document_files(bid_id,id),
 UNIQUE(bid_id,upload_id,id),FOREIGN KEY(bid_id,upload_id,previous_binding_id) REFERENCES upload_bindings(bid_id,upload_id,id)
);
ALTER TABLE upload_transfers ADD COLUMN current_binding_id uuid;
ALTER TABLE upload_transfers ADD COLUMN committed_binding_id uuid;
ALTER TABLE upload_transfers ADD CONSTRAINT upload_current_binding FOREIGN KEY(bid_id,upload_id,current_binding_id) REFERENCES upload_bindings(bid_id,upload_id,id);
ALTER TABLE upload_transfers ADD CONSTRAINT upload_committed_binding FOREIGN KEY(bid_id,upload_id,committed_binding_id) REFERENCES upload_bindings(bid_id,upload_id,id);
CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON upload_bindings FOR EACH ROW EXECUTE FUNCTION design.append_only();
CREATE TABLE document_relations(
 bid_id uuid NOT NULL,document_version_id uuid PRIMARY KEY,source_document_version_id uuid NOT NULL,
 kind text NOT NULL CHECK(kind IN ('user_declared_same_draft','controlled_render')),
 verification text NOT NULL CHECK(verification IN ('unverified','verified','rejected')),evidence_ids uuid[] NOT NULL,
 FOREIGN KEY(bid_id,document_version_id) REFERENCES document_versions(bid_id,id),FOREIGN KEY(bid_id,source_document_version_id) REFERENCES document_versions(bid_id,id),
 CHECK(document_version_id<>source_document_version_id),CHECK(kind<>'controlled_render' OR verification='verified')
);
CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON document_relations FOR EACH ROW EXECUTE FUNCTION design.append_only();
COMMIT;

