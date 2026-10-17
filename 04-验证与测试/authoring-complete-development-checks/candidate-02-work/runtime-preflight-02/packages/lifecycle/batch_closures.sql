-- Fresh initialization extension only. A closure preserves staging; it is not a failed upload.
BEGIN;
CREATE TABLE design.upload_batch_closures (
 id uuid PRIMARY KEY,
 bid_id uuid NOT NULL REFERENCES design.bids(id),
 batch_id uuid NOT NULL UNIQUE,
 actor_id uuid NOT NULL REFERENCES design.members(id),
 command_id uuid NOT NULL UNIQUE REFERENCES design.commands(id),
 reason text NOT NULL CHECK(length(btrim(reason)) BETWEEN 1 AND 4000),
 created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(bid_id,batch_id) REFERENCES design.upload_batches(bid_id,id)
);
CREATE TRIGGER immutable_guard BEFORE UPDATE OR DELETE ON design.upload_batch_closures
 FOR EACH ROW EXECUTE FUNCTION design.append_only();
REVOKE ALL ON design.upload_batch_closures FROM PUBLIC,tender_api,tender_worker,tender_research_worker,tender_score_read,tender_research_read;
GRANT SELECT,INSERT ON design.upload_batch_closures TO tender_api;
GRANT SELECT ON design.upload_batch_closures TO tender_worker;
COMMIT;
