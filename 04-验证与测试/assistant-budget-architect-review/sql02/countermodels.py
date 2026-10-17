import json,math
# SQL three-valued logic: None denotes UNKNOWN, PL/pgSQL IF runs only on TRUE.
def sql_or(*values):
 return True if any(v is True for v in values) else (None if any(v is None for v in values) else False)
def sql_and(a,b):
 return False if a is False or b is False else (None if a is None or b is None else True)
# Existing held reservation, terminal native run, NULL decision/proof and amount=0.
validation=sql_or(False,None,None,False,False,sql_and(None,False))
assert validation is None
result={'null_reconcile':{'existing_reservation':True,'native_terminal':True,'decision':None,'amount':0,'proof_reference':None,'proof_sha':None,'IF_invalid':validation,'IF_branch_runs':validation is True,'CASE_new_state':'settled','charged_minor':0,'manual_flag_after':False,'reservation_checks_accept':True}}
# All native dispatch predicates in SQL02 pass, but output limits do not agree.
input_ceiling=1000000;input_price=100;output_price=1000000
budget_output=2048;native_output=8192;limit=5000
reserved=math.ceil((input_ceiling*input_price+budget_output*output_price)/1000000)
required=math.ceil((input_ceiling*input_price+native_output*output_price)/1000000)
assert reserved<=limit<required
result['native_output_mismatch']={'same_job_and_call':True,'native_state':'reserved','execution_state':'running','live_lease':True,'cancel':False,'price_valid':True,'reserved_output_upper':budget_output,'native_run_and_call_max_tokens':native_output,'SQL02_dispatch_checked_output_equality':False,'reserved_minor':reserved,'actual_native_worst_case_minor':required,'policy_limit':limit,'scope':'SQL relationship countermodel; not an end-to-end request or PG execution'}
print(json.dumps(result,indent=2))
