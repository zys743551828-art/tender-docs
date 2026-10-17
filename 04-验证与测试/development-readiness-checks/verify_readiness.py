"""聚合当前设计检查并核验输入摘要；不替代应用验收。"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DESIGN = ROOT / '05-技术与设计/detailed-design'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    names = ['contract-result.json','retained-contract-result.json','database-result.json',
             'retained-database-result.json','product-result.json']
    results = {name:json.loads((HERE/name).read_text()) for name in names}
    verified_inputs = 0
    for name,result in results.items():
        assert result['passed'],name
        for relative,expected in result.get('inputs',{}).items():
            assert sha(ROOT/relative)==expected,(name,relative)
            verified_inputs += 1
        for filename,expected in result.get('sql_sha256',{}).items():
            assert sha(DESIGN/filename)==expected,(name,filename)
            verified_inputs += 1
    old_db_probe = ROOT/'04-验证与测试/implementation-design-checks/probe_database.py'
    assert sha(old_db_probe)==results['database-result.json']['probe_sha256']
    contract = results['contract-result.json']
    assert (contract['schema_definitions'],contract['api_paths'],contract['api_operations'])==(127,84,91)
    assert results['database-result.json']['table_count']==101
    assert results['retained-database-result.json']['tables']==101
    product=results['product-result.json']
    assert product['software_scenarios_executed']==0
    assert product['review_rounds_check']['user_followups_checked']==3
    assert results['retained-contract-result.json']['followups']==10

    documents=[ROOT/'README.md',ROOT/'02-业务与产品/AI投标工作台PRD.md',
               ROOT/'02-业务与产品/GitHub实现功能保留对照.md',
               ROOT/'05-技术与设计/AI投标工作台详细技术设计.md',
               ROOT/'04-验证与测试/feature-preservation-checks/README.md',HERE/'README.md']
    documents += list(DESIGN.glob('*.md'))
    source_paths=documents+list(DESIGN.glob('*.json'))+list(DESIGN.glob('*.sql'))+list(HERE.glob('*.py'))
    source_paths += [ROOT/'04-验证与测试/prd-checks/spec-followups.json']
    source_paths += [ROOT/'04-验证与测试/feature-preservation-checks/retention-matrix.json']
    result = {
        'passed':True,'decision':'development_can_start','authorization_request':'对比的结论有了，详细设计完善了吗，是否能开始开发',
        'scope':'目标设计与开发准入；不是完整业务实现、生产上线或模型发布验收',
        'executed_at':datetime.now(timezone.utc).isoformat(),
        'design_followups_closed':10,'schema_definitions':127,'api_paths':84,'api_operations':91,'tables':101,
        'checks':{'contract':contract['checks'],'retained_contract':results['retained-contract-result.json']['checks'],
                  'database':len(results['database-result.json']['checks']),
                  'retained_database':results['retained-database-result.json']['checks']},
        'result_hashes':{name:sha(HERE/name) for name in names},
        'source_hashes':{str(p.relative_to(ROOT)):sha(p) for p in sorted(set(source_paths))},
        'input_hashes_verified':verified_inputs,
        'software_scenarios_executed':0,'production_accepted':False,'formal_model_accepted':False,
        'allowed_first_work':['T0基础工程与契约生成','空库初始化与登录','命令与Kafka固定阶段','最小PDF评审闭环','RAG及受控评分研究'],
        'remaining_acceptance':['真实存储与模型适配','业务API及浏览器端到端','真实文档OCR/RAG及正式评分效果','多节点Kafka与生产恢复容量'],
        'historical_evidence_rewritten':False,
    }
    output=HERE/'result.json'
    # 先建立结果文件，随后核对包括本文件入口的本地链接。
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    links=0
    try:
        for document in documents:
            for link in re.findall(r'\]\(([^)]+)\)',document.read_text()):
                if re.match(r'^(https?:|app:|#)',link):
                    continue
                assert (document.parent/link.split('#')[0]).resolve().exists(),(document,link)
                links+=1
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    result['local_links_checked']=links
    output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k not in ['source_hashes','result_hashes']},ensure_ascii=False))


if __name__=='__main__':
    main()
