import asyncio,json
from pathlib import Path
from uuid import uuid4
from deploy.application_recovery.application import PausedApplication


def test_post_create_after_new_uuid_bound_still_reaches_inner_app():
    forwarded=[];output=[]
    async def actual_stub(scope,receive,send):
        forwarded.append({'path':scope['path'],'method':scope['method'],'headers':[(k.decode(),v.decode()) for k,v in scope['headers']]})
        await send({'type':'http.response.start','status':201,'headers':[]})
        await send({'type':'http.response.body','body':b'{}'})
    fixture={'ids':{k:str(uuid4()) for k in ['bid','knowledge','report']}}
    paused=PausedApplication(actual_stub,fixture)
    new_id=str(uuid4());paused.bind_new_bid(new_id)
    async def receive():return {'type':'http.request','body':json.dumps(paused.create_body).encode(),'more_body':False}
    async def send(message):output.append(message)
    scope={'type':'http','method':'POST','path':'/api/v1/bids','headers':[(b'idempotency-key',b'new-independent-command-after-first-project')]}
    asyncio.run(paused(scope,receive,send))
    assert len(forwarded)==0
    assert next(x['status'] for x in output if x['type']=='http.response.start')==423
    Path(__file__).with_name('pause-counterexample.json').write_text(json.dumps({'fixture':'original code02 ASGI counterexample rerun against code03, no DB','new_bid_already_bound':new_id,'request':'POST same allowlisted body with a fresh command key','forwarded':forwarded,'observed_status':423,'expected_boundary':'423 for a second project creation; old accepted key replay may remain allowed'},indent=2)+'\n')
