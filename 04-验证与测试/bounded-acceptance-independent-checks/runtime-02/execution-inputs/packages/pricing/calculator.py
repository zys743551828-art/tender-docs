"""受限公式解释器；不执行表达式代码，不猜测尚未支持的招标计分规则。"""

from __future__ import annotations

import re
from decimal import ROUND_HALF_EVEN, ROUND_HALF_UP, Decimal, localcontext

NUMBER = re.compile(r"(?:0|[1-9][0-9]{0,23})(?:\.[0-9]{1,12})?\Z")
ROUNDING = {"half_up_2dp": ROUND_HALF_UP, "half_even_2dp": ROUND_HALF_EVEN}
FORMULA_KEYS = {
    "method",
    "maximum",
    "minimum_price",
    "maximum_price",
    "minimum_valid_bids",
    "above_coefficient",
    "below_coefficient",
}


def number(value: str) -> Decimal:
    if not isinstance(value, str) or not NUMBER.fullmatch(value):
        raise ValueError("金额和系数须为有界非负十进制字符串")
    return Decimal(value)


def decimal_text(value: Decimal) -> str:
    return format(value, "f")


def validate_formula(formula: dict) -> None:
    if not isinstance(formula, dict) or set(formula) != FORMULA_KEYS:
        raise ValueError("公式字段不完整或包含未支持字段")
    if formula["method"] not in ("lowest_valid_ratio", "mean_valid_deviation"):
        raise ValueError("暂不支持该价格公式，不能生成估算分数")
    if number(formula["maximum"]) <= 0:
        raise ValueError("满分须大于零")
    bounds = [
        number(formula[key]) if formula[key] is not None else None
        for key in ("minimum_price", "maximum_price")
    ]
    if bounds[0] is not None and bounds[1] is not None and bounds[0] > bounds[1]:
        raise ValueError("报价资格区间无效")
    count = formula["minimum_valid_bids"]
    if type(count) is not int or not 1 <= count <= 201:
        raise ValueError("有效报价数量要求须明确且有界")
    for key in ("above_coefficient", "below_coefficient"):
        number(formula[key])
    if formula["method"] == "lowest_valid_ratio" and any(
        number(formula[k]) != 0 for k in ("above_coefficient", "below_coefficient")
    ):
        raise ValueError("最低价比例法不接受额外偏离系数")


def eligible(price: Decimal, formula: dict) -> bool:
    lower, upper = formula["minimum_price"], formula["maximum_price"]
    return (
        price > 0
        and (lower is None or price >= number(lower))
        and (upper is None or price <= number(upper))
    )


def evaluate_point(
    price: Decimal, competitors: list[Decimal], formula: dict, rounding: str
) -> tuple[dict, list[str]]:
    if not eligible(price, formula):
        return {
            "price": decimal_text(price),
            "score": None,
            "eligible": False,
            "notes": ["报价不满足已登记的价格资格区间，价格分未知"],
        }, []
    valid = [value for value in [price, *competitors] if eligible(value, formula)]
    if len(valid) < formula["minimum_valid_bids"]:
        return {
            "price": decimal_text(price),
            "score": None,
            "eligible": True,
            "notes": ["有效报价假设数量不足，不能计算价格分"],
        }, []
    maximum = number(formula["maximum"])
    if formula["method"] == "lowest_valid_ratio":
        base = min(valid)
        raw = maximum * base / price
        explanation = "满分×最低有效报价÷本方报价"
    else:
        base = sum(valid) / Decimal(len(valid))
        coefficient = number(
            formula["above_coefficient"] if price >= base else formula["below_coefficient"]
        )
        raw = maximum * (1 - abs(price - base) / base * coefficient)
        explanation = "满分×(1−偏离比例×对应方向系数)，结果限制在0至满分"
    score = min(maximum, max(Decimal(0), raw)).quantize(
        Decimal("0.01"), rounding=ROUNDING[rounding]
    )
    # Rounding must never manufacture a value beyond the declared maximum.
    score = min(maximum, score)
    steps = [
        f"有效报价数量：{len(valid)}；不合格的对手假设已排除",
        f"基准价：{decimal_text(base)}",
        explanation,
        f"价格分：{decimal_text(score)}；取整：{rounding}",
    ]
    return {
        "price": decimal_text(price),
        "score": decimal_text(score),
        "eligible": True,
        "notes": [],
    }, steps


def simulate(formula: dict, request: dict) -> dict:
    validate_formula(formula)
    if request["rounding_policy"] not in ROUNDING:
        raise ValueError("不支持该取整策略，请按原文明确取整规则")
    if len(request["competitor_price_assumptions"]) > 200 or len(request["candidate_prices"]) > 50:
        raise ValueError("报价候选或对手假设数量超出当前上限")
    main = number(request["candidate_price"])
    competitors = [number(v) for v in request["competitor_price_assumptions"]]
    candidates = list(dict.fromkeys([main, *(number(v) for v in request["candidate_prices"])]))
    with localcontext() as context:
        context.prec = 80
        point, steps = evaluate_point(main, competitors, formula, request["rounding_policy"])
        points = [
            evaluate_point(p, competitors, formula, request["rounding_policy"])[0]
            for p in candidates
        ]
        curve = []
        interval = request["curve_range"]
        if interval is not None:
            lower, upper, count = (
                number(interval["lower"]),
                number(interval["upper"]),
                interval["points"],
            )
            if lower >= upper or type(count) is not int or not 2 <= count <= 101:
                raise ValueError("曲线区间须递增且包含2至101个点")
            for index in range(count):
                price = (
                    upper if index == count - 1 else lower + (upper - lower) * index / (count - 1)
                )
                curve.append(
                    evaluate_point(price, competitors, formula, request["rounding_policy"])[0]
                )
    return {
        "main": point,
        "candidates": points,
        "curve": curve,
        "calculation_steps": steps,
        "assumptions": [
            "对手报价均为用户情景假设，不是已知实际成交价",
            "每个候选和曲线点均重新计算包含本方报价的基准价",
            "金额单位沿用公式出处，未进行货币或金额单位转换",
        ],
        "technical_exchange_note": "价格分仅在同一量尺、权重和资格条件下可与技术分比较；本试算不提供必中标或成本利润结论。",
    }
