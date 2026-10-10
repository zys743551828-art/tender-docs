"""模型调用费用情景计算。

只估算评审流水线向模型服务发出的调用费用，不涉及其他任何口径。每轮评审的调用次数由实现决定：

* 规则提取：招标文件与补遗的证据块每 20 块一次调用（ai/handlers.py 的 RULES_BATCH），招标文件不变时各轮评审共用同一份规则；
* 规则核对：每条参与评审的规则一次调用（废标／否决、强制、评分、建议、例外五类都包括；被驳回或暂缓的规则不参与）；
* 评分预测：每轮一次条款与章节的对应调用，加每条带分值的评分条款（至多 40 条）各判断 3 次（BIDWB_PREDICTION_REPEATS）；
* 比较式锚定：登记了锚点包时，满分合计占七成的前几条评分条款（至多 6 条）各与 6 份锚点（BIDWB_PREDICTION_ANCHORS）比较一次；
* 改稿建议：每个所选问题一次调用，由人触发，不属于自动评审。

每次调用在输出不合结构时最多修复重发 2 次（ai/llm.py 的 MAX_REPAIRS），故给出“名义”和“全部用满重试”两个上限。
令牌数是按提示词结构（证据块至多 400 字）做的上界估计，不是实测用量；模型单价取自公开价目，不是预算承诺。

项目级功能开关不改变调用次数：前后矛盾与范围残留检测不调用模型；关闭“显示预测分数”时预测照常运行；
关闭“自动开始评审”只推迟调用，不减少调用。

用法：``python3 calculate_costs.py`` 重新生成 cost-scenarios.json；``--check`` 只比对，不一致时以非零状态退出。
"""

from __future__ import annotations

import json
import math
import sys
from decimal import ROUND_HALF_UP
from decimal import Decimal as D
from pathlib import Path

RATES = {"flash": {"hit": D("0.006"), "miss": D("0.30"), "output": D("1.20")}, "pro": {"hit": D("0.044"), "miss": D("1.32"), "output": D("3.96")}}
RULES_BATCH = 20  # 每次规则提取调用的证据块数
CHUNKS_PER_PAGE = D("2.5")  # 假设：每页招标文件约切成 2.5 个证据块（每块至多 400 字）
MAX_ATTEMPTS_PER_CALL = 3  # 一次正常调用加最多 2 次结构修复重发
MAX_SCORING_ITEMS = 40  # 评分预测每轮至多评估的评分条款数（ai/predict.py 的 MAX_ITEMS）
PREDICTION_REPEATS = 3  # 每条评分条款的判断次数（BIDWB_PREDICTION_REPEATS 默认值）
ANCHOR_ITEMS = 6  # 比较式锚定每轮至多比较的评分条款数（ai/anchors.py 的 MAX_ITEMS）
ANCHORS = 6  # 每轮挑选的锚点数（BIDWB_PREDICTION_ANCHORS 默认值）

# 每类调用的令牌估计（输入, 输出）。输入按 1 个令牌不少于 1 个汉字的上界估计。
CALL_TOKENS = {
    "extraction": (9_000, 4_096),  # 20 个证据块（至多 8,000 字）加提示词；输出受 max_tokens=4096 限制
    "check": (6_000, 1_500),  # 提示词、规则、规则出处至多 5 块、检索到的正文 5 块、已确认事实
    "mapping": (12_000, 1_500),  # 正文目录（至多 60 节，每节标题与开头文字）与全部评分条款
    "judgement": (16_000, 1_500),  # 评分标准与对应章节全文（至多 14,000 字）；输出档次、要点逐条判断与说明
    "comparison": (14_000, 400),  # 评分标准与两份标书各至多 6,000 字的节选
    "revision": (3_000, 1_200),
}

# (名称, 招标文件页数, 参与评审的规则数, 其中带分值的评分条款数, 每个项目的评审轮数, 每个项目请求改稿建议的问题数)
SCENARIOS = [
    ("轻量", 100, 30, 10, 2, 5),
    ("常规", 220, 60, 20, 3, 15),
    ("大型", 400, 120, 40, 6, 30),
]
PROJECTS_PER_MONTH = 20


CENT = D("0.01")


def money(value: D) -> str:
    """金额保留到分（四舍五入），输出为十进制字符串。"""
    return str(value.quantize(CENT, rounding=ROUND_HALF_UP))


def usd(calls: int, kind: str, model: str) -> D:
    tokens_in, tokens_out = CALL_TOKENS[kind]
    rate = RATES[model]
    return D(calls) * (D(tokens_in) * rate["miss"] + D(tokens_out) * rate["output"]) / D(1_000_000)


def scenario_row(name: str, pages: int, rules: int, scoring: int, rounds: int, revision_findings: int) -> dict[str, object]:
    chunks = int(D(pages) * CHUNKS_PER_PAGE)
    calls = {
        "extraction": math.ceil(chunks / RULES_BATCH),
        "check": rules * rounds,
        "mapping": rounds,
        "judgement": min(scoring, MAX_SCORING_ITEMS) * PREDICTION_REPEATS * rounds,
        "comparison": min(scoring, ANCHOR_ITEMS) * ANCHORS * rounds,
        "revision": revision_findings,
    }
    per_project = sum(calls.values())
    monthly = {model: sum((usd(n, kind, model) for kind, n in calls.items()), D(0)) * PROJECTS_PER_MONTH for model in RATES}
    cost = {model: {"nominal": money(v), "all_retries_used": money(v * MAX_ATTEMPTS_PER_CALL)} for model, v in monthly.items()}
    return {
        "scenario": name,
        "tender_pages": pages,
        "tender_chunks": chunks,
        "rules_per_round": rules,
        "scoring_rules_per_round": min(scoring, MAX_SCORING_ITEMS),
        "rounds_per_project": rounds,
        "revision_findings_per_project": revision_findings,
        "calls_per_project": {**calls, "total": per_project},
        "calls_per_round_after_rules": rules + 1 + min(scoring, MAX_SCORING_ITEMS) * PREDICTION_REPEATS + min(scoring, ANCHOR_ITEMS) * ANCHORS,
        "projects_per_month": PROJECTS_PER_MONTH,
        "calls_per_month": per_project * PROJECTS_PER_MONTH,
        "usd_per_month_without_cache": cost,
    }


def build_report() -> dict[str, object]:
    return {
        "record_id": "cost-scenarios",
        "type": "model_call_cost_scenarios",
        "sources_verified_on": "2026-10-07",
        "currency": "USD",
        "pricing_basis": "DeepSeek 峰时价，每百万令牌；全部按输入未命中缓存计（hit 单价列出备查，不参与计算）；金额四舍五入到 0.01 美元；单价仅用于情景算术，不预测价格变化",
        "rates": {m: {k: str(v) for k, v in r.items()} for m, r in RATES.items()},
        "sources": {"deepseek": "https://api-docs.deepseek.com/quick_start/pricing/"},
        "call_model": {
            "extraction": "招标文件与补遗每 20 个证据块一次调用；各轮评审共用同一份规则，招标文件或补遗更新后才重新提取",
            "check": "每轮评审对每条参与评审的规则一次调用；被驳回或暂缓的规则不调用",
            "mapping": "每轮评审一次：把评分条款对应到正文章节",
            "judgement": "每轮评审对每条带分值的评分条款判断 3 次（第 1 次温度 0，其余 0.7），至多 40 条",
            "comparison": "登记了锚点包时，每轮评审对满分合计占七成的前几条评分条款（至多 6 条）各与 6 份锚点比较一次；未登记锚点包时为 0",
            "revision": "每个所选问题一次调用，由人触发",
            "retry": "输出不合结构时最多修复重发 2 次，所以每次逻辑调用最多 3 次请求",
            "project_settings": "项目级功能开关不改变调用次数：两类确定性检测不调用模型，关闭显示预测分数时预测照常运行，关闭自动开始评审只推迟调用",
        },
        "assumptions": {
            "chunks_per_page": str(CHUNKS_PER_PAGE),
            "tokens_per_call_input_output": {k: list(v) for k, v in CALL_TOKENS.items()},
            "note": "令牌数是依据提示词结构的上界估计，没有在真实模型上度量过实际用量；真实用量由任务产物里记录的 usage 汇总得到",
        },
        "scenarios": [scenario_row(*s) for s in SCENARIOS],
        "object_storage_volume_scenario": {
            "gb_per_project_assumption": "0.3",
            "projects_per_month": PROJECTS_PER_MONTH,
            "growth_gb_per_month": "6",
            "months": 36,
            "retained_gb_end": "216",
            "price_status": "阿里云 OSS 单价未核实，本记录只给容量",
        },
        "not_included": [
            "阿里云 OSS 存储、请求与流量费用（单价未核实）",
            "主机、磁盘与备份",
            "请求失败但仍被计费的未知费用",
            "税费、汇率和采购报价",
        ],
    }


def main() -> None:
    text = json.dumps(build_report(), ensure_ascii=False, indent=2) + "\n"
    target = Path(__file__).with_name("cost-scenarios.json")
    if "--check" in sys.argv[1:]:
        if not target.exists() or target.read_text(encoding="utf-8") != text:
            print("cost-scenarios.json 与计算结果不一致，请运行 python3 calculate_costs.py 重新生成", file=sys.stderr)
            sys.exit(1)
        print("cost-scenarios.json 与计算结果一致")
        return
    target.write_text(text, encoding="utf-8")
    for row in json.loads(text)["scenarios"]:
        cost = row["usd_per_month_without_cache"]
        print(
            f"{row['scenario']}：每项目 {row['calls_per_project']['total']} 次调用，每月 {row['calls_per_month']} 次；"
            f"flash {cost['flash']['nominal']}（重试用满 {cost['flash']['all_retries_used']}）美元／月，"
            f"pro {cost['pro']['nominal']}（重试用满 {cost['pro']['all_retries_used']}）美元／月"
        )


if __name__ == "__main__":
    main()
