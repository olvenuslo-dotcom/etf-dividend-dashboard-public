# -*- coding: utf-8 -*-
"""금융소득종합과세 — 연 2,000만원 선까지 얼마나 남았나.

지금까지는 **경고 문구만** 있었다("2,000만원을 넘으면 종합과세로 더 낼 수 있습니다").
숫자가 없으면 그 문구는 아무것도 못 한다. 여기서 실제로 센다. **추가 수집 0** —
이미 있는 보유종목·분배 이력으로 계산한다.

### 무엇이 세어지나 (핵심)
| 계좌유형 | 종합과세 대상 | 왜 |
|---|---|---|
| 일반(위탁) | ✅ 센다 | 배당소득으로 15.4% 원천징수되고 합산 대상이다 |
| 연금저축 · 퇴직연금(IRP/DC) | ❌ 안 센다 | **과세이연** — 받을 때 소득이 아니다 |
| ISA | ❌ 안 센다 | 만기에 **분리과세** — 종합소득에 합산되지 않는다 |

미국 직접 상장분은 현지에서 15% 떼이지만 **국내 금융소득에는 들어간다**(조세조약으로
국내에서 더 떼지 않을 뿐이다). 그래서 일반 계좌에 있으면 센다.

### 2,000만원은 세전 기준이다
원천징수 전 금액으로 잰다. 세후로 재면 실제보다 낮게 나와 안심하게 된다.

### 이 계산이 못 하는 것 (화면에도 적는다)
- **분배금만 센다.** 예금 이자·다른 증권사 배당·펀드 분배금은 우리 자료에 없다.
  실제 금융소득은 여기 값보다 **크다**. 아래 '다른 금융소득' 칸에 직접 넣어야 한다.
- 매매차익은 안 센다(국내주식형 ETF 는 비과세, 미국은 양도소득세로 따로 간다).
- 세액은 **어림값**이다. 실제는 비교과세(종합과세와 분리과세 중 큰 쪽)로 매겨진다.
  세무 상담을 대신하지 않는다.
"""
from __future__ import annotations

from datetime import date

import pandas as pd

import portfolio

# 연 금융소득이 이 선을 넘으면 넘는 만큼이 다른 소득과 합산된다 (세전 기준)
THRESHOLD = 20_000_000

# 종합과세로 합산되는 계좌유형. 나머지는 과세이연(연금)이거나 분리과세(ISA)다.
COMBINED_ACCOUNTS = ("일반(위탁)",)

# 이미 떼인 배당소득세 중 **소득세분**(14%). 지방소득세 1.4% 는 소득세의 10% 라
# 아래에서 따로 1.1 배로 얹는다.
WITHHELD_INCOME_TAX = 0.14
LOCAL_TAX_MULTIPLIER = 1.1

# 종합소득세 과세표준 구간 (2026년 기준, 지방소득세 별도) — (상한, 세율)
BRACKETS = (
    (14_000_000, 0.06),
    (50_000_000, 0.15),
    (88_000_000, 0.24),
    (150_000_000, 0.35),
    (300_000_000, 0.38),
    (500_000_000, 0.40),
    (1_000_000_000, 0.42),
    (float("inf"), 0.45),
)


def is_combined(account_type) -> bool:
    """이 계좌의 분배금이 금융소득종합과세에 합산되나."""
    name = str(account_type or "").strip() or portfolio.DEFAULT_ACCOUNT_TYPE
    return name in COMBINED_ACCOUNTS


def marginal_rate(taxable_base: float) -> float:
    """과세표준에 걸리는 한계세율 (지방소득세 제외)."""
    for ceiling, rate in BRACKETS:
        if taxable_base <= ceiling:
            return rate
    return BRACKETS[-1][1]


def received_this_year(evaluated: pd.DataFrame,
                       today: date | None = None) -> float:
    """올해 **이미 받은** 분배금 중 종합과세 대상 (세전).

    `portfolio.load_distributions()` 의 실제 지급 이력을 쓴다. 매수일 이후 것만
    센다(사기 전 분배는 내 소득이 아니다) — `evaluate()` 와 같은 규칙.
    """
    today = today or date.today()
    if evaluated is None or evaluated.empty:
        return 0.0
    dist = portfolio.load_distributions()
    if dist.empty:
        return 0.0

    start = pd.Timestamp(year=today.year, month=1, day=1)
    end = pd.Timestamp(today)
    total = 0.0
    for _, row in evaluated.iterrows():
        if not is_combined(row.get("계좌유형")):
            continue
        isin, quantity = str(row.get("ISIN", "") or ""), row.get("수량")
        if not isin or not quantity:
            continue
        mine = dist[(dist["ISIN"] == isin)
                    & (dist["지급기준일"] >= start)
                    & (dist["지급기준일"] <= end)]
        bought = row.get("매수일")
        if pd.notna(bought) and bought:
            mine = mine[mine["지급기준일"] >= pd.Timestamp(bought)]
        if not mine.empty:
            total += float(mine["주당분배금"].sum()) * float(quantity)
    return total


def expected_rest_of_year(evaluated: pd.DataFrame,
                          today: date | None = None) -> float:
    """올해 **남은 기간**에 받을 것으로 보이는 분배금 (세전, 종합과세 대상만).

    `portfolio.monthly_cashflow()` 를 그대로 쓴다 — 예상 규칙이 화면과 어긋나면
    안 되기 때문이다. 대상 계좌만 남긴 표를 넘겨서 계산한다.
    """
    today = today or date.today()
    if evaluated is None or evaluated.empty:
        return 0.0
    mine = evaluated[evaluated["계좌유형"].apply(is_combined)] \
        if "계좌유형" in evaluated.columns else evaluated
    if mine.empty:
        return 0.0

    months_left = 12 - today.month + 1        # 이번 달 포함
    flow = portfolio.monthly_cashflow(mine, months=months_left, today=today)
    if flow.empty:
        return 0.0
    this_year = flow[flow["월"].str.startswith(str(today.year))]
    return float(this_year["세전"].sum())


def summary(evaluated: pd.DataFrame, other_income: float = 0.0,
            other_taxable_base: float = 0.0,
            today: date | None = None) -> dict:
    """한 해 금융소득과 2,000만원 선까지의 거리.

    other_income        — 우리 자료에 없는 금융소득(예금 이자·타 증권사 배당 등)
    other_taxable_base  — 근로소득 등 **다른 종합소득 과세표준**. 한계세율을 잡는 데만 쓴다
    """
    today = today or date.today()
    got = received_this_year(evaluated, today)
    coming = expected_rest_of_year(evaluated, today)
    total = got + coming + max(float(other_income or 0.0), 0.0)
    excess = max(total - THRESHOLD, 0.0)

    # 초과분은 다른 소득 위에 얹혀 한계세율을 맞는다. 이미 낸 14% 는 빼 준다.
    rate = marginal_rate(max(float(other_taxable_base or 0.0), 0.0) + excess)
    extra = max(rate - WITHHELD_INCOME_TAX, 0.0) * excess * LOCAL_TAX_MULTIPLIER

    return {
        "올해_받은": got,
        "올해_예상": coming,
        "다른_금융소득": max(float(other_income or 0.0), 0.0),
        "연간_합계": total,
        "기준": float(THRESHOLD),
        "남은_여유": max(THRESHOLD - total, 0.0),
        "초과액": excess,
        "넘었나": excess > 0,
        "소진율": (total / THRESHOLD * 100) if THRESHOLD else 0.0,
        "한계세율": rate,
        "추가세금_어림": extra,
    }


def by_account(evaluated: pd.DataFrame,
               today: date | None = None) -> pd.DataFrame:
    """계좌별로 올해 받은 분배금과 합산 여부 — **왜 안 세어졌는지**가 보여야 한다."""
    columns = ["계좌", "계좌유형", "올해_받은", "합산대상"]
    if evaluated is None or evaluated.empty:
        return pd.DataFrame(columns=columns)

    today = today or date.today()
    rows = []
    for (account, kind), group in evaluated.groupby(
            [evaluated.get("계좌", pd.Series("", index=evaluated.index)),
             evaluated.get("계좌유형", pd.Series("", index=evaluated.index))],
            dropna=False):
        # 이 계좌만 담은 표로 같은 계산을 돌린다 (규칙이 갈리지 않게)
        combined = is_combined(kind)
        forced = group.copy()
        if not combined:
            forced["계좌유형"] = portfolio.DEFAULT_ACCOUNT_TYPE
        rows.append({
            "계좌": str(account or ""),
            "계좌유형": str(kind or "") or portfolio.DEFAULT_ACCOUNT_TYPE,
            "올해_받은": received_this_year(forced, today),
            "합산대상": combined,
        })
    return pd.DataFrame(rows, columns=columns).sort_values(
        ["합산대상", "올해_받은"], ascending=[False, False]).reset_index(drop=True)
