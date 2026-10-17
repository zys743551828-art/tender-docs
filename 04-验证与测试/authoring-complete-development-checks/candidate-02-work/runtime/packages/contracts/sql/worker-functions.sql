-- 执行辅助函数，先执行core.sql与domain.sql；实际远程调用必须在事务外。
BEGIN;
CREATE FUNCTION design.claim_outbox(batch_limit integer,token uuid)
RETURNS SETOF design.outbox LANGUAGE sql AS $$
 WITH due AS (
  SELECT id FROM design.outbox WHERE published_at IS NULL AND available_at<=clock_timestamp()
  AND (lease_until IS NULL OR lease_until<clock_timestamp()) ORDER BY available_at,id
  FOR UPDATE SKIP LOCKED LIMIT greatest(1,least(batch_limit,100))
 ) UPDATE design.outbox o SET claim_token=token,lease_until=clock_timestamp()+interval '30 seconds',publish_attempts=publish_attempts+1
 FROM due WHERE o.id=due.id RETURNING o.*;
$$;
CREATE FUNCTION design.ack_outbox(event_id uuid,token uuid) RETURNS boolean LANGUAGE sql AS $$
 WITH changed AS (UPDATE design.outbox SET published_at=clock_timestamp(),lease_until=NULL
 WHERE id=event_id AND claim_token=token AND published_at IS NULL RETURNING 1)
 SELECT EXISTS(SELECT 1 FROM changed);
$$;
CREATE FUNCTION design.renew_outbox(event_id uuid,token uuid) RETURNS boolean LANGUAGE sql AS $$
 WITH changed AS (UPDATE design.outbox SET lease_until=clock_timestamp()+interval '30 seconds'
 WHERE id=event_id AND claim_token=token AND published_at IS NULL RETURNING 1)
 SELECT EXISTS(SELECT 1 FROM changed);
$$;
CREATE FUNCTION design.renew_step(op uuid,key text,input design.sha256,expected_fence bigint,owner uuid)
RETURNS boolean LANGUAGE sql AS $$
 WITH changed AS (UPDATE design.step_receipts s SET heartbeat_at=clock_timestamp(),lease_until=clock_timestamp()+interval '120 seconds'
 WHERE operation_id=op AND step_key=key AND input_digest=input AND fence=expected_fence AND owner_token=owner AND state='running'
 AND EXISTS(SELECT 1 FROM design.operations o WHERE o.id=op AND o.state='running') RETURNING 1)
 SELECT EXISTS(SELECT 1 FROM changed);
$$;
-- 只授权固定行锁，不授予Worker改写bid身份或业务头的UPDATE能力。
CREATE FUNCTION design.lock_worker_bid(target_bid uuid) RETURNS void
LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,design,pg_temp AS $$
BEGIN
 PERFORM 1 FROM design.bids WHERE id=target_bid FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'bid_not_found' USING ERRCODE='P0002'; END IF;
END $$;
REVOKE ALL ON FUNCTION design.lock_worker_bid(uuid) FROM PUBLIC;
-- 费用与调用账本在同一数据库事务；本函数不发远程请求。
CREATE FUNCTION design.reserve_research_call(
 target_run uuid,op uuid,key text,input design.sha256,expected_generation bigint,
 expected_fence bigint,owner uuid,call_id uuid,call_key text,call_attempt integer,
 request_hash design.sha256,profile_hash design.sha256,amount design.nonnegative_amount
) RETURNS jsonb LANGUAGE plpgsql SET search_path=pg_catalog,design,pg_temp AS $$
DECLARE bid uuid; existing design.model_calls; r design.research_runs;
BEGIN
 SELECT bid_id INTO bid FROM design.operations WHERE id=op;
 PERFORM design.lock_worker_bid(bid);
 PERFORM 1 FROM design.operations WHERE id=op AND state='running' AND cancel_requested_at IS NULL FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'operation_not_running' USING ERRCODE='55000'; END IF;
 PERFORM 1 FROM design.operation_steps WHERE operation_id=op AND step_key=key AND input_digest=input AND dispatch_generation=expected_generation FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'generation_stale' USING ERRCODE='55000'; END IF;
 PERFORM 1 FROM design.step_receipts WHERE operation_id=op AND step_key=key AND input_digest=input
 AND state='running' AND fence=expected_fence AND owner_token=owner AND lease_until>clock_timestamp() FOR UPDATE;
 IF NOT FOUND THEN RAISE EXCEPTION 'execution_fence_stale' USING ERRCODE='55000'; END IF;
 IF NOT EXISTS(SELECT 1 FROM design.research_run_attempts a WHERE a.run_id=target_run AND a.operation_id=op AND a.bid_id=bid) THEN
 RAISE EXCEPTION 'run_operation_mismatch' USING ERRCODE='23514'; END IF;
 SELECT * INTO r FROM design.research_runs WHERE id=target_run FOR UPDATE;
 IF r.state<>'running' OR r.cancel_requested_at IS NOT NULL THEN RAISE EXCEPTION 'research_not_running' USING ERRCODE='55000'; END IF;
 SELECT * INTO existing FROM design.model_calls WHERE operation_id=op AND logical_call_key=call_key AND attempt_no=call_attempt;
 IF FOUND THEN
  IF existing.request_digest IS DISTINCT FROM request_hash OR existing.profile_digest IS DISTINCT FROM profile_hash OR existing.research_run_id IS DISTINCT FROM target_run OR existing.reserved_amount IS DISTINCT FROM amount THEN
   RAISE EXCEPTION 'call_identity_conflict' USING ERRCODE='23514';
  END IF;
  RETURN jsonb_build_object('call_id',existing.id,'new_reservation',false,'state',existing.state);
 END IF;
 IF NOT design.reserve_research_fee(target_run,amount) THEN RAISE EXCEPTION 'research_budget_or_state_blocked' USING ERRCODE='55000'; END IF;
 INSERT INTO design.model_calls(id,operation_id,logical_call_key,attempt_no,request_digest,profile_digest,state,research_run_id,reserved_amount)
 VALUES(call_id,op,call_key,call_attempt,request_hash,profile_hash,'reserved',target_run,amount);
 RETURN jsonb_build_object('call_id',call_id,'new_reservation',true,'state','reserved');
END $$;
REVOKE ALL ON FUNCTION design.reserve_research_call(uuid,uuid,text,design.sha256,bigint,bigint,uuid,uuid,text,integer,design.sha256,design.sha256,design.nonnegative_amount) FROM PUBLIC;
-- acquire与finish由领域应用服务完成：先调用lock_worker_bid，再锁operation、step。
-- finish将收据、领域结果、当前指针与下一阶段Outbox合并在一次事务，不能分开调用提交。
COMMIT;
