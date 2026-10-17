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
-- acquire与finish必须由领域应用服务完成：先锁bid，再锁operation，再锁step。
-- finish将收据、领域结果、当前指针与下一阶段Outbox合并在一次事务，不能分开调用提交。
COMMIT;
