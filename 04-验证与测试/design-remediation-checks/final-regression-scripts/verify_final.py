"""核对候选02、独立结果和实际输入身份；不将机制检查提升为应用验收。"""
from pathlib import Path
import hashlib,json
HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[1]
DESIGN=ROOT/'05-技术与设计/detailed-design'
CANDIDATE=ROOT/'04-验证与测试/design-remediation/technical-candidate-02.json'
EXPECTED='d9e53659504a3f5dcb527266582495294de738fc92c036c97987e669d12175e4'
RESULTS=['final-worker-login-result.json','final-identity-upload-result.json','score-asof-result.json','final-identity-contract-result.json','final-contract-regression-result.json','final-retained-contract-result.json','final-database-regression-result.json','final-rule-origin-database-result.json','final-product-spec-result.json']
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def main():
    assert digest(CANDIDATE)==EXPECTED
    candidate=json.loads(CANDIDATE.read_text())
    for row in candidate['sources']:assert digest(ROOT/row['path'])==row['sha256'],row['path']
    results=[]
    for name in RESULTS:
        p=HERE/name;result=json.loads(p.read_text());assert result['passed'],name
        for key in ['input_sha256','inputs','sql_sha256']:
            for source,want in result.get(key,{}).items():
                target=(DESIGN/source) if '/' not in source else (ROOT/source)
                assert digest(target)==want,(name,source)
        results.append(dict(path=name,sha256=digest(p),scope=result['scope'],checks=len(result['checks']) if isinstance(result.get('checks'),list) else result.get('checks')))
    pending=json.loads((HERE/'application-acceptance.json').read_text())
    assert pending['software_executed']==0 and all(c['status']=='pending_application' for c in pending['cases'])
    result=dict(passed=True,decision='设计层独立验证通过，可进入实现；应用与真实模型/生产验收尚未执行',technical_candidate_sha256=EXPECTED,technical_source_count=len(candidate['sources']),counts=candidate['counts'],results=results,application_cases_pending=len(pending['cases']),software_end_to_end_executed=False,not_verified=['真实业务API与Kafka Worker','真实对象存储上传/补传/重绑定','规则补充命令与重提取合并实际应用','真实浏览器及真人试用','OCR/Embedding/RAG效果与正式评分精度','生产恢复与多节点故障'],verifier_sha256=digest(Path(__file__)))
    (HERE/'final-result.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(dict(passed=True,results=len(results),sources=len(candidate['sources']),pending_application_cases=len(pending['cases'])),ensure_ascii=False))
if __name__=='__main__':main()
