#!/usr/bin/env python3
"""检查 PRD 追踪与抽象业务约束；不调用应用、不构成产品或模型验收。"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import re
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
PRD = ROOT / '02-业务与产品/AI投标工作台PRD.md'
SCENARIOS = HERE / 'scenarios.json'
FIXTURES = HERE / 'design-fixtures.json'
REVIEW = ROOT / '04-验证与测试/PRD逻辑检查与场景验证.md'
REQUIREMENTS = {f'FR-{n:03}' for n in range(1, 41)} | {f'NFR-{n:03}' for n in range(1, 15)}
FAILURES: list[str] = []


def check(condition: bool, message: str) -> None:
    if not condition:
        FAILURES.append(message)


def decision(kind: str, d: dict[str, Any]) -> bool | str:
    """PRD 的最小抽象，用于检查互相冲突的业务结论，不模拟服务端实现。"""
    if kind == 'submission':
        return all(d[k] for k in ('authenticated', 'current', 'body_matches', 'complete')) and (not d['unconfirmed'] or d['accept_as_written']) and not d['blockers']
    if kind == 'submission_fact':
        return all(d[k] for k in ('authenticated', 'body_known', 'submission_evidence'))
    if kind == 'archive':
        return d['phase'] in {'closed', 'terminated'} and d['all_tasks_terminal']
    if kind == 'knowledge':
        return d['status'] == 'published' and not d['superseded'] and d['applicable'] and d['effective']
    if kind == 'score_mode':
        if not d['show_score']:
            return 'hidden'
        if not d['run_ok']:
            return 'failed'
        if not d['coverage_complete'] or not d['scale_valid']:
            return 'partial'
        if not d['model_accepted'] or not d['scope_matches']:
            return 'raw_uncalibrated'
        return 'total_with_interval' if d['interval_validated'] else 'total_without_interval'
    if kind == 'main_sample':
        return d['target'] == 'technical_total' and d['source'] == 'official_original' and not d['conflict'] and all(d[k] for k in ('verified', 'body_matches', 'inputs_complete', 'scale_valid', 'group_known', 'time_known'))
    if kind == 'online_pair':
        return all(d[k] for k in ('sample_admitted', 'blind_prediction', 'prediction_before_known_result', 'prediction_body_matches', 'prediction_before_submission'))
    if kind == 'model_publish':
        return all(d[k] for k in ('protocol_frozen', 'isolation_proven', 'metrics_pass', 'scope_validated', 'deployable', 'observation_complete', 'rollback_ready'))
    if kind == 'comparison':
        if not d['rounds_exist'] or not d['valid_comparable_results']:
            return 'unavailable'
        return 'comparable' if d['method_same'] and d['rubric_same'] else 'not_attributable'
    if kind == 'score_input':
        # 普通参考或锚点不是通过此通道临时放进来的；有独立用途和实验约束。
        return set(d['fields']) <= {'tender', 'body', 'confirmed_facts'}
    if kind == 'scale':
        try:
            value, maximum = Decimal(d['value']), Decimal(d['maximum'])
            return value.is_finite() and maximum.is_finite() and maximum > 0 and 0 <= value <= maximum
        except (InvalidOperation, ValueError):
            return False
    raise ValueError('unknown design decision: ' + kind)


def scenario_markdown() -> str:
    rows = json.loads(SCENARIOS.read_text())['scenarios']
    lines = ['<!-- BEGIN_GENERATED_SCENARIOS -->', '', '## 6. 需求至场景追踪', '', '| 需求 | 关联验收场景 |', '| --- | --- |']
    for req in sorted(REQUIREMENTS):
        related = [row['id'] for row in rows if req in row['requirements']]
        lines.append('| ' + req + ' | ' + '、'.join(related) + ' |')
    lines += ['', '## 7. 完整验收场景', '', '以下内容由场景主数据生成；所有软件执行状态均为未执行。', '']
    for row in rows:
        lines += ['### ' + row['id'] + ' ' + row['title'], '', '关联需求：' + '、'.join(row['requirements']) + '；PRD章节：' + '、'.join(row['sections']) + '。', '', '- 前置：' + row['given'] + '。', '- 操作／事件：' + row['action'] + '。', '- 期望：' + row['expected'] + '。', '- 禁止：' + row['forbidden'] + '。', '- 软件执行：' + row['software_execution'] + '。', '']
    lines += ['<!-- END_GENERATED_SCENARIOS -->']
    return '\n'.join(lines)


def static_checks() -> tuple[int, int, int]:
    text = PRD.read_text()
    definitions = re.findall(r'^\| ((?:FR|NFR)-\d{3}) \|', text, re.M)
    check(set(definitions) == REQUIREMENTS, 'FR/NFR 定义不完整或有未知编号')
    check(len(definitions) == len(set(definitions)), 'FR/NFR 索引定义重复')
    sections = set(re.findall(r'^## (\d+)\.', text, re.M))
    check(sections == {str(n) for n in range(1, 21)}, 'PRD 主章节必须完整且为1至20')
    all_mentions = set(re.findall(r'\b(?:FR|NFR)-\d{3}\b', text))
    check(all_mentions <= REQUIREMENTS, 'PRD 正文含未定义需求')
    scenarios = json.loads(SCENARIOS.read_text())['scenarios']
    ids: set[str] = set()
    covered: set[str] = set()
    for index, row in enumerate(scenarios, 1):
        check(row['id'] == f'AC-{index:03}', '场景编号不连续: ' + row['id'])
        check(row['id'] not in ids, '场景编号重复: ' + row['id'])
        ids.add(row['id'])
        for field in ('title', 'given', 'action', 'expected', 'forbidden', 'requirements', 'sections'):
            check(bool(row.get(field)), row['id'] + ' 缺少字段: ' + field)
        check(set(row['requirements']) <= REQUIREMENTS, row['id'] + ' 含未知需求')
        check(set(row['sections']) <= sections, row['id'] + ' 含不存在章节')
        check(row['software_execution'] == '未执行', row['id'] + ' 不得把推演冒充软件执行')
        covered.update(row['requirements'])
    check(covered == REQUIREMENTS, '需求缺少场景追踪: ' + ','.join(sorted(REQUIREMENTS - covered)))
    if REVIEW.exists():
        review_text = REVIEW.read_text()
        generated = re.search(r'<!-- BEGIN_GENERATED_SCENARIOS -->.*?<!-- END_GENERATED_SCENARIOS -->', review_text, re.S)
        check(generated is not None and generated.group() == scenario_markdown(), '可读场景与JSON主数据不一致，请重新生成')
        for sid in ids:
            check(f'### {sid} ' in review_text, '审查文档缺少场景: ' + sid)
    else:
        check(False, '缺少可读逻辑审查文档')
    # 仅检查本次主文档和导航记录的文件目标；不声称验证远端链接可访问。
    link_count = 0
    files = [PRD, REVIEW, ROOT/'README.md', ROOT/'02-业务与产品/AI投标工作台产品化方案.md']
    for file in files:
        if not file.exists():
            continue
        for target in re.findall(r'!?\[[^\]]*\]\(([^)]+)\)', file.read_text()):
            if '://' in target or target.startswith(('#', 'mailto:')):
                continue
            target_path = target.split('#', 1)[0].strip('<>')
            check((file.parent / target_path).exists(), f'本地链接失效: {file.name}: {target}')
            link_count += 1
    return len(scenarios), len(covered), link_count


def model_checks() -> dict[str, int]:
    fixtures = json.loads(FIXTURES.read_text())['fixtures']
    for case in fixtures:
        actual = decision(case['kind'], case['input'])
        check(type(actual) is type(case['expected']) and actual == case['expected'], '反例与预期冲突: ' + case['name'])
    gate_keys = ('authenticated', 'current', 'body_matches', 'complete', 'unconfirmed', 'accept_as_written', 'blockers')
    gate_combinations = 0
    for values in itertools.product((False, True), repeat=len(gate_keys)):
        d = dict(zip(gate_keys, values))
        allowed = decision('submission', d)
        if allowed:
            check(d['authenticated'] and d['current'] and d['body_matches'] and d['complete'] and not d['blockers'], '标准提交错误放行核心前提')
            check(not d['unconfirmed'] or d['accept_as_written'], '标准提交错误放行规则确认')
        # 按原文接受不能消除其他阻断；普通建议不增加阻断。
        check(not decision('submission', d | {'accept_as_written': True, 'blockers': True}), '规则开关绕过关键问题')
        check(decision('submission', d | {'ordinary_suggestions': True}) == allowed, '普通建议错误改变闸门')
        gate_combinations += 1
    score_keys = ('show_score', 'run_ok', 'coverage_complete', 'scale_valid', 'model_accepted', 'scope_matches', 'interval_validated')
    score_combinations = 0
    for values in itertools.product((False, True), repeat=len(score_keys)):
        d = dict(zip(score_keys, values))
        mode = decision('score_mode', d)
        if str(mode).startswith('total_'):
            check(all(d[k] for k in score_keys[:-1]), '无资格仍显示技术总分')
        if mode == 'total_with_interval':
            check(d['interval_validated'], '未验证的区间被显示')
        check(decision('score_mode', d | {'show_score': False}) == 'hidden', '关闭展示仍显示数值')
        score_combinations += 1
    knowledge_combinations = 0
    for status, superseded, applicable, effective in itertools.product(('candidate', 'pending', 'published', 'disabled', 'expired'), (False, True), (False, True), (False, True)):
        d = dict(status=status, superseded=superseded, applicable=applicable, effective=effective)
        if decision('knowledge', d):
            check(status == 'published' and not superseded and applicable and effective, '不合格知识进入正式引用')
        knowledge_combinations += 1
    # 所有任务完成顺序与成功/失败组合：当前版本不能被晚完成的旧任务倒写。
    task_schedules = 0
    for order in itertools.permutations((1, 2)):
        for outcomes in itertools.product(('succeeded', 'failed'), repeat=2):
            latest_input, current_result = 2, None
            history: list[tuple[int, str]] = []
            for version in order:
                result = (version, outcomes[version - 1])
                history.append(result)
                if version == latest_input:
                    current_result = result
            check(current_result == (2, outcomes[1]) and len(history) == 2, '任务乱序覆盖最新输入或丢失历史')
            task_schedules += 1
    # 主动注入错误规则，确认手工反例能够识别常见的危险简化。
    # 这是对检查本身的敏感性验证，仍然不是应用测试。
    mutations = [
        ('标准提交忽略正文版本', 'submission', lambda d: decision('submission', d | {'body_matches': True})),
        ('按原文接受跳过全部检查', 'submission', lambda d: d['authenticated'] if d['accept_as_written'] else decision('submission', d)),
        ('部分覆盖外推总分', 'score_mode', lambda d: decision('score_mode', d | {'coverage_complete': True})),
        ('20组在线数据授予模型验收', 'score_mode', lambda d: decision('score_mode', d | {'model_accepted': True}) if d.get('online_pairs', 0) >= 20 else decision('score_mode', d)),
        ('候选知识直接作为已发布', 'knowledge', lambda d: decision('knowledge', d | {'status': 'published'}) if d['status'] == 'candidate' else decision('knowledge', d)),
        ('忽略知识有效期', 'knowledge', lambda d: decision('knowledge', d | {'effective': True})),
        ('待核验成绩进入主验收', 'main_sample', lambda d: decision('main_sample', d | {'verified': True})),
        ('新冲突回退旧已核验分', 'main_sample', lambda d: decision('main_sample', d | {'conflict': False}) if d.get('previous_verified') else decision('main_sample', d)),
        ('回测混入事前误差', 'online_pair', lambda d: decision('online_pair', d | {'blind_prediction': True, 'prediction_before_known_result': True, 'prediction_before_submission': True})),
        ('发布忽略独立测试', 'model_publish', lambda d: decision('model_publish', d | {'metrics_pass': True})),
        ('方法变化仍归因改稿', 'comparison', lambda d: decision('comparison', d | {'method_same': True})),
        ('带分聊天混入评分输入', 'score_input', lambda d: decision('score_input', {'fields': [x for x in d['fields'] if x != 'chat_with_score']})),
        ('负分也算有效量尺', 'scale', lambda d: True if d['value'] == '-1' else decision('scale', d)),
    ]
    mutations_detected = 0
    for name, kind, mutant in mutations:
        caught = any(mutant(case['input']) != case['expected'] for case in fixtures if case['kind'] == kind)
        check(caught, '反例未能识别故意注入的错误规则: ' + name)
        mutations_detected += int(caught)
    # 只检查下列对象同一版本/尝试的状态图。保护条件仍需实现验收。
    graphs = {
        'project': {'prepare': {'working', 'terminated'}, 'working': {'submitted', 'terminated'}, 'submitted': {'closed', 'terminated'}, 'closed': {'working'}, 'terminated': {'working'}},
        'conversation': {'active': {'ended', 'archived'}, 'ended': {'active', 'archived'}, 'archived': {'ended'}},
        'knowledge_version': {'candidate': {'pending'}, 'pending': {'candidate', 'published'}, 'published': {'disabled', 'expired'}, 'disabled': set(), 'expired': set()},
        'task_attempt': {'queued': {'running', 'cancelled'}, 'running': {'succeeded', 'failed', 'cancelled'}, 'succeeded': set(), 'failed': set(), 'cancelled': set()},
    }
    graph_pairs = 0
    for name, graph in graphs.items():
        check(all(dest in graph for ds in graph.values() for dest in ds), name + ' 包含未知目标状态')
        reached = {next(iter(graph))}
        while True:
            more = reached | {dest for state in reached for dest in graph[state]}
            if more == reached:
                break
            reached = more
        check(reached == set(graph), name + ' 存在不可达状态')
        for source, dest in itertools.product(graph, repeat=2):
            allowed = dest in graph[source]
            if name == 'knowledge_version' and source in {'disabled', 'expired'}:
                check(not allowed, '已失效/停用版本直接重新发布')
            if name == 'task_attempt' and source in {'succeeded', 'failed', 'cancelled'}:
                check(not allowed, '同一次尝试被重开而改写终态')
            graph_pairs += 1
    return dict(explicit_fixture_cases=len(fixtures), submission_combinations=gate_combinations, score_display_combinations=score_combinations, knowledge_combinations=knowledge_combinations, task_completion_schedules=task_schedules, intentional_rule_errors=len(mutations), intentional_rule_errors_detected=mutations_detected, lifecycle_graphs=len(graphs), lifecycle_state_pairs=graph_pairs)


def review_round_checks() -> tuple[dict[str, Any], list[Path]]:
    record_path = HERE / 'fusion-review-rounds.json'
    data = json.loads(record_path.read_text())
    rounds = data['rounds']
    check([row['round'] for row in rounds] == [1, 2, 3], '三轮核对编号或数量不完整')
    scenarios = {row['id'] for row in json.loads(SCENARIOS.read_text())['scenarios']}
    headings = set(re.findall(r'^#{2,3} (\d+(?:\.\d+)?)(?:\.\s|\s)', PRD.read_text(), re.M))
    findings: set[str] = set()
    files = [record_path]
    previous = None
    for row in rounds:
        number = row['round']
        check(row['status'] == '已完成规格核对', f'第{number}轮未完成')
        check(row.get('software_scenarios_executed') == 0, '规格核对不得冒充软件执行')
        if previous:
            check(previous['after_hashes'] == row['before_hashes'], '三轮输入快照衔接不一致')
        check(row['before_hashes'] != row['after_hashes'], f'第{number}轮缺少细化修正的输入身份')
        result_path = (ROOT / row['check_result']).resolve()
        safe = result_path.is_relative_to(ROOT.resolve())
        check(safe and result_path.exists(), '轮次检查结果不存在或路径越界')
        if safe and result_path.exists():
            files.append(result_path)
            check(hashlib.sha256(result_path.read_bytes()).hexdigest() == row['check_sha256'], '轮次检查结果摘要不一致')
            result = json.loads(result_path.read_text())
            check(result['passed'] and not result['failures'], f'第{number}轮检查失败')
            check(result['acceptance_scenarios_defined'] == row['scenarios_defined'], '轮次场景数与快照不符')
            check(result['software_scenarios_executed'] == 0, '轮次结果的软件执行身份不符')
        for finding in row['findings']:
            identity = finding['id']
            check(identity.startswith(f'R{number}-') and identity not in findings, '核对问题编号重复或归属错误')
            findings.add(identity)
            check(bool(finding['problem']) and bool(finding['resolution']), '核对问题缺少问题或处理内容')
            check(finding['status'] == '已在规格修正' and finding['software_verification'] == '未执行', '核对问题的验证身份错误')
            check(bool(finding['scenario_ids']) and set(finding['scenario_ids']) <= scenarios, '核对问题缺少有效场景')
            check(bool(finding['prd_sections']) and set(finding['prd_sections']) <= headings, '核对问题引用了不存在的PRD章节')
        previous = row
    check([len(row['findings']) for row in rounds] == [5, 6, 6], '三轮问题数与可读核对报告不符')
    expected_hashes = rounds[-1]['after_hashes'] if rounds else {}
    followup_count = 0
    followups_path = HERE / 'spec-followups.json'
    if followups_path.exists():
        files.append(followups_path)
        updates = json.loads(followups_path.read_text())['updates']
        seen_updates: set[str] = set()
        for update in updates:
            identity = update['id']
            check(identity not in seen_updates, '后续变更编号重复')
            seen_updates.add(identity)
            check(update['authorization_source'] == 'user_request' and bool(update['request']), '后续变更缺少明确用户请求')
            check(update['before_hashes'] == expected_hashes, '用户后续变更与历史快照链不衔接')
            check(set(update['after_hashes']) == set(expected_hashes), '后续变更不得删除受核对输入')
            check(update['before_hashes'] != update['after_hashes'], '后续变更没有输入变化')
            expected_hashes = update['after_hashes']
            followup_count += 1
    historical_snapshot_current = True
    for relative, expected in expected_hashes.items():
        path = (ROOT / relative).resolve()
        safe = path.is_relative_to(ROOT.resolve())
        check(safe and path.exists(), '当前输入路径无效')
        if safe and path.exists():
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            check(actual == expected, '当前规格未在历史审查或用户后续变更链中记录: ' + relative)
            historical_snapshot_current &= actual == rounds[-1]['after_hashes'][relative]
    return dict(scope='核对历史三轮及显式用户后续变更链；不将历史三轮冒充当前规格重新三轮审查，也不证明软件正确', rounds_checked=len(rounds), findings_linked=len(findings), user_followups_checked=followup_count, historical_snapshot_current=historical_snapshot_current, software_scenarios_executed=0), files



def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--render-scenarios', action='store_true', help='从场景JSON更新审查文档的生成区')
    parser.add_argument('--check-review-rounds', action='store_true', help='核对三轮历史记录、显式用户后续变更链与当前规格身份')
    args = parser.parse_args()
    if args.render_scenarios:
        original = REVIEW.read_text()
        replaced, count = re.subn(r'<!-- BEGIN_GENERATED_SCENARIOS -->.*?<!-- END_GENERATED_SCENARIOS -->', lambda _: scenario_markdown(), original, flags=re.S)
        if count != 1:
            raise ValueError('审查文档应有且仅有一个场景生成区')
        REVIEW.write_text(replaced)
    scenarios, requirements, links = static_checks()
    model = model_checks()
    files = [PRD, REVIEW, SCENARIOS, FIXTURES, Path(__file__).resolve()]
    round_result = None
    if args.check_review_rounds:
        round_result, round_files = review_round_checks()
        files.extend(round_files)
    result = dict(scope='文档静态检查与抽象业务规则验证；不连接应用，不代表软件、真实评分精度或生产验收', passed=not FAILURES, functional_requirements=40, nonfunctional_requirements=14, requirements_with_scenarios=requirements, acceptance_scenarios_defined=scenarios, software_scenarios_executed=0, local_file_links_checked=links, design_model=model, failures=FAILURES, inputs={str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files if p.exists()})
    if round_result is not None:
        result['review_rounds_check'] = round_result
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.write_text(rendered)
    print(rendered, end='')
    return int(bool(FAILURES))


if __name__ == '__main__':
    raise SystemExit(main())
