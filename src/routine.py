# -*- coding: utf-8 -*-
"""매수 전 5분 루틴 — 종목마다 항목별 판정(+1 / 0 / -1)과 그 근거를 낸다.

루틴 5개 항목은 사용자가 정한 것이고, 여기서는 **지금 있는 데이터로 낼 수 있는
만큼만** 낸다. 못 내는 항목은 억지로 만들지 않고 '자료 없음'으로 둔다 —
빈칸을 그럴듯한 값으로 채우면 걸러야 할 종목이 통과한다.

| 루틴 항목 | 여기서 |
|---|---|
| ① Coverage ≥ 1.0 | ②와 **같은 축**이라 합쳤다 (아래 설명) |
| ② NAV 12개월 잠식률 -10% 이내 | 1년 **가격** 수익률로 본다 |
| ③ 이익초과분배 반복 여부 | **자료 없음** — 세이브로 배당구분이 이익분배/청산분배 2종뿐 |
| ④ 특별분배 빼고 분배율 재계산 | 중앙값 분배율과 표시 분배율의 차이로 본다 |
| ⑤ 커버드콜이면 천장 확인 | 점수 없이 '직접 확인' 표시 |

**①을 ②에 합친 이유.** 진짜 Coverage(펀드가 벌어들인 순수익 ÷ 분배금)는 공시에서
받아야 하는데 그 자료가 없다. 우리 데이터로 만들 수 있는 대용은
`총수익률 ÷ 분배율` 인데, 총수익률 = 가격변화 + 분배율 이므로 이 값이 1 이상이라는
말은 **가격변화 ≥ 0** 과 같은 뜻이 된다. ② 역시 가격이 얼마나 빠졌나를 보는 값이라,
둘을 따로 두면 같은 조건을 두 번 거는 셈이다. 그래서 하나의 축으로 두고
임계값만 둘로 나눴다 (0% 이상이면 +1, -10% 까지는 0, 그 아래면 -1).

점수는 **매길 수 있는 3개 항목**의 합이라 -3 ~ +3 이다. 종목을 고르는 출발점이지
매수 신호가 아니다.
"""
from __future__ import annotations

import pandas as pd

# ── 임계값 ─────────────────────────────────────────────
# 가격 잠식 — 루틴의 'NAV 12개월 잠식률 -10% 이내'를 1년 가격 수익률로 본다
EROSION_OK = 0.0          # 이 위면 +1 (분배금을 주고도 가격이 안 빠졌다)
EROSION_WARN = -10.0      # 여기까지는 0, 그 아래는 -1

# 분배 안정성 — 30단계 분배변동계수. 주기마다 정상 범위가 달라 **주기별 기준**을 쓴다.
# 값은 2026-08-13 산출물의 주기별 중앙값이다(월 8.2 / 분기 31.2 / 위클리 71.5).
# 중앙값 이하면 +1, 두 배를 넘으면 -1.
CV_BASELINE = {"위클리": 71.5, "월": 8.2, "분기": 31.2}
CV_DEFAULT = 30.0         # 기준이 없는 주기(연3회·반기·연)는 이 값으로 본다

# 특별분배 착시 — 표시 분배율이 중앙값 분배율보다 얼마나 부풀어 있나 (%)
SPECIAL_OK = 10.0         # 이 아래면 +1 (특별분배가 거의 없다)
SPECIAL_WARN = 30.0       # 여기까지는 0, 그 위는 -1

# 이익초과분배(ROC) — 19a-1 공시의 **회계연도 누적** 원금반환 비율(%)
# 절반을 넘으면 '분배금'이라 부르기 어렵다 → 그 선을 -1 로 잡았다.
ROC_OK = 20.0
ROC_WARN = 50.0

GOOD, NEUTRAL, BAD = 1, 0, -1
NO_DATA = None            # 점수를 매길 수 없는 항목


def _num(value) -> float | None:
    """숫자로 못 바꾸면 None. 빈칸을 0으로 읽어 통과시키는 사고를 막는다."""
    got = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return None if pd.isna(got) else float(got)


def erosion(row) -> dict:
    """② 가격 잠식 (① Coverage 대용을 합친 축)."""
    value = _num(row.get("1년수익률"))
    if value is None:
        return {"항목": "가격 잠식", "점수": NO_DATA, "근거": "1년 수익률 없음",
                "해석": "상장한 지 1년이 안 됐거나 수익률 자료가 아직 없습니다."}
    if value >= EROSION_OK:
        score, verdict = GOOD, "분배금을 주고도 가격이 안 빠졌습니다."
    elif value >= EROSION_WARN:
        score, verdict = NEUTRAL, "가격이 빠졌지만 루틴 기준(-10%) 안입니다."
    else:
        score, verdict = BAD, "1년 새 가격이 10% 넘게 빠졌습니다."
    return {
        "항목": "가격 잠식", "점수": score,
        "근거": f"1년 가격 수익률 {value:+.1f}%",
        "해석": f"{verdict} 분배금만큼 가격이 빠지면 받은 돈은 내 원금입니다. "
                "**진짜 Coverage(벌어들인 순수익 ÷ 분배금)는 공시 자료가 없어** "
                "가격 변화로 대신 봅니다.",
    }


def stability(row) -> dict:
    """분배 안정성 — 회차별 금액이 들쭉날쭉한가. 주기별 기준으로 잰다."""
    value = _num(row.get("분배변동계수"))
    cycle = str(row.get("분배주기") or "").strip()
    base = CV_BASELINE.get(cycle, CV_DEFAULT)
    if value is None:
        return {"항목": "분배 안정성", "점수": NO_DATA, "근거": "지급 3회 미만",
                "해석": "최근 12개월 지급이 3회가 안 되면 들쭉날쭉함을 잴 수 없습니다."}
    if value <= base:
        score, verdict = GOOD, f"같은 {cycle} 주기 평균({base:.0f}%)보다 고릅니다."
    elif value <= base * 2:
        score, verdict = NEUTRAL, f"같은 {cycle} 주기 평균({base:.0f}%)보다 출렁입니다."
    else:
        score, verdict = BAD, f"같은 {cycle} 주기 평균({base:.0f}%)의 두 배를 넘습니다."
    return {
        "항목": "분배 안정성", "점수": score,
        "근거": f"분배변동계수 {value:.0f}% (같은 주기 기준 {base:.0f}%)",
        "해석": f"{verdict} 벌어서 주는 돈은 회차마다 비슷하고, 원금을 헐거나 "
                "옵션 프리미엄에 기대는 돈은 출렁입니다. 커버드콜은 정상적으로도 "
                "높으니 같은 주기끼리 견주세요.",
    }


def special_distribution(row) -> dict:
    """④ 특별분배 빼고 분배율 재계산 — 표시 분배율이 얼마나 부풀어 있나."""
    shown = _num(row.get("연간분배율"))
    median = _num(row.get("중앙값연분배율"))
    if shown is None or median is None or median <= 0:
        return {"항목": "특별분배 착시", "점수": NO_DATA, "근거": "분배율 자료 없음",
                "해석": "표시 분배율이나 중앙값 분배율이 없어 견줄 수 없습니다."}
    gap = (shown - median) / median * 100
    if gap <= SPECIAL_OK:
        score, verdict = GOOD, "특별분배로 부풀려진 자리가 거의 없습니다."
    elif gap <= SPECIAL_WARN:
        score, verdict = NEUTRAL, "표시 분배율이 다소 부풀어 있습니다."
    else:
        score, verdict = BAD, "표시 분배율이 특별분배 한두 회에 크게 기대고 있습니다."
    return {
        "항목": "특별분배 착시", "점수": score,
        "근거": f"표시 {shown:.2f}% vs 중앙값 {median:.2f}% ({gap:+.0f}%)",
        "해석": f"{verdict} 중앙값 분배율은 **회차 금액의 중앙값**으로 다시 계산한 "
                "값이라, 한 회차만 크게 준 특별분배에 흔들리지 않습니다. "
                "내년에도 이만큼 나오리라 기대할 수 있는 쪽은 중앙값입니다.",
    }


def covered_call(row) -> dict:
    """⑤ 커버드콜이면 천장 확인 — 점수를 매기지 않는다(사람이 볼 일)."""
    strategy = str(row.get("전략") or "").strip()
    if strategy != "커버드콜":
        return {"항목": "커버드콜 천장", "점수": NO_DATA, "근거": f"전략: {strategy or '미분류'}",
                "해석": "커버드콜이 아니라 이 항목은 해당되지 않습니다."}
    return {
        "항목": "커버드콜 천장", "점수": NO_DATA, "근거": "커버드콜 전략",
        "해석": "**직접 확인하세요.** 커버드콜은 지수가 올라도 옵션을 판 만큼 "
                "위쪽 수익이 잘립니다. 운용사 자료에서 콜 매도 비중과 행사가를 보고 "
                "천장이 어디인지 확인해야 합니다 — 데이터로는 못 냅니다.",
    }


def excess_distribution(row) -> dict:
    """③ 이익초과분배(ROC) — 미국은 19a-1 공시값, 국내는 해당 사례가 없다.

    받아 온 값은 **회계연도 누적** 비율이다. 한 회차만 보면 그 달만 유난했을 수
    있어서, 누적으로 봐야 '계속 원금을 헐고 있나'가 보인다.
    """
    # 누적이 있으면 그것으로, 없으면 **최근 회차**로 판정한다.
    # 발행사마다 내놓는 것이 다르다 — Global X 만 누적을 준다. 누적이 없다고
    # 판정을 포기하면 정작 고배당이 몰린 Roundhill·Rex·GraniteShares 가 통째로
    # 필터를 빠져나간다. 대신 **무엇으로 판정했는지 화면에 적는다.**
    roc = _num(row.get("누적_원금반환비율"))
    기준 = "회계연도 누적"
    if roc is None:
        roc = _num(row.get("원금반환비율"))
        기준 = "최근 회차"
    if roc is not None:
        if roc <= ROC_OK:
            score, verdict = GOOD, "분배금이 대체로 벌어들인 돈에서 나옵니다."
        elif roc <= ROC_WARN:
            score, verdict = NEUTRAL, "분배금의 상당 부분이 원금 반환입니다."
        else:
            score, verdict = BAD, "분배금의 **절반 넘게가 내 원금**을 돌려주는 것입니다."
        덧말 = ("" if 기준 == "회계연도 누적" else
                " 이 발행사는 회계연도 누적을 공시하지 않아 **가장 최근 회차**로 "
                "판정했습니다 — 그 달만 유난했을 수 있어 누적보다 거칩니다.")
        return {
            "항목": "이익초과분배", "점수": score,
            "근거": f"19a 공시 — {기준} 원금반환 {roc:.1f}%",
            "해석": f"{verdict} 원금 반환(ROC)은 펀드가 번 돈이 아니라 내가 넣은 돈을 "
                    "돌려주는 것이라, 분배율이 높아 보여도 수익이 아닙니다. "
                    "운용사 19a-1 통지의 추정치이며 연말 1099-DIV 로 확정됩니다."
                    + 덧말,
        }

    market = str(row.get("상장시장") or "").strip()
    if market == "한국":
        return {
            "항목": "이익초과분배", "점수": NO_DATA, "근거": "국내 — 해당 사례 없음",
            "해석": "세이브로 **펀드별분배금지급내역을 전수 확인**한 결과(5,541건) "
                    "국내 ETF 분배는 세금구분이 전부 `현금(배당소득)` 이었습니다. "
                    "기록상 이익초과분배가 없어 판정할 것이 없습니다. "
                    "ROC 는 본래 미국 ETF 쪽 문제입니다.",
        }
    return {
        "항목": "이익초과분배", "점수": NO_DATA, "근거": "19a 공시 미수집",
        "해석": "이 발행사의 19a-1 공시는 아직 안 받아 왔습니다. "
                "지금 받아 오는 곳은 **Global X · Roundhill · Rex · GraniteShares** "
                "입니다. **YieldMax·Defiance 는 공시 PDF 폴더가 robots.txt 로 "
                "막혀 있어** 받지 않습니다. 그 전까지는 **분배 안정성**이 부분 대용입니다.",
    }


# 화면에 이 순서로 나온다. 루틴 종이의 차례를 그대로 따랐다.
CHECKS = (erosion, stability, excess_distribution, special_distribution, covered_call)
# 점수가 매겨지는 항목. 이익초과분배는 19a 공시를 받아 온 종목에서만 점수가 난다.
SCORED = ("가격 잠식", "분배 안정성", "이익초과분배", "특별분배 착시")
MAX_SCORE = len(SCORED)


def judge(row) -> list[dict]:
    """한 종목의 루틴 판정 5개."""
    return [check(row) for check in CHECKS]


def total_score(cards: list[dict]) -> int:
    """점수가 매겨진 항목만 더한다 (-3 ~ +3)."""
    return sum(c["점수"] for c in cards if c["점수"] is not None)


# ── 표 전체에 한꺼번에 매기기 (필터용) ──────────────────
def score_frame(df: pd.DataFrame) -> pd.DataFrame:
    """표의 모든 종목에 루틴 점수와 항목별 통과 여부를 붙인다.

    붙는 칸: `루틴점수` · `루틴_가격잠식` · `루틴_분배안정` · `루틴_특별분배` · `커버드콜`
    (앞 세 개는 -1/0/1, 값이 없으면 NA — **NA를 통과로 치지 않는다**)
    """
    out = df.copy()
    if out.empty:
        for column in ("루틴점수", "루틴_가격잠식", "루틴_분배안정",
                       "루틴_원금반환", "루틴_특별분배"):
            out[column] = pd.Series(dtype="float64")
        out["커버드콜"] = pd.Series(dtype="bool")
        return out

    rows = [judge(row) for _, row in out.iterrows()]
    by_name = [{card["항목"]: card["점수"] for card in cards} for cards in rows]
    out["루틴_가격잠식"] = [d.get("가격 잠식") for d in by_name]
    out["루틴_분배안정"] = [d.get("분배 안정성") for d in by_name]
    out["루틴_원금반환"] = [d.get("이익초과분배") for d in by_name]
    out["루틴_특별분배"] = [d.get("특별분배 착시") for d in by_name]
    out["루틴점수"] = [total_score(cards) for cards in rows]
    out["커버드콜"] = out.get("전략", pd.Series("", index=out.index)) \
        .astype(str).str.strip().eq("커버드콜")
    return out


# 필터 한 줄 = (화면에 보일 이름, 붙는 칸, 설명)
FILTERS = (
    ("가격이 10% 넘게 빠지지 않음", "루틴_가격잠식",
     "1년 가격 수익률이 -10% 이상. 자료가 없는 종목은 **빠집니다**"),
    ("분배가 고름", "루틴_분배안정",
     "분배변동계수가 같은 주기 평균의 두 배 안. 지급 3회 미만은 **빠집니다**"),
    ("원금 반환이 절반 미만", "루틴_원금반환",
     "19a-1 공시의 원금반환(ROC)이 50% 이하 — 회계연도 누적이 있으면 그것으로, "
     "없으면 가장 최근 회차로 봅니다. **공시를 못 받은 종목은 빠집니다** — "
     "받아 오는 곳은 Global X · Roundhill · Rex · GraniteShares 입니다"),
    ("특별분배로 부풀지 않음", "루틴_특별분배",
     "표시 분배율이 중앙값 분배율보다 30% 넘게 크지 않음"),
)


def apply_filters(df: pd.DataFrame, chosen: list[str],
                  exclude_covered_call: bool = False) -> pd.DataFrame:
    """고른 루틴 항목을 모두 통과한 종목만 남긴다.

    통과 = 점수가 **0 이상**(-1 만 떨어뜨린다). 값이 없는 종목(NA)도 떨어진다 —
    '모르는 것'을 통과로 치면 걸러야 할 종목이 그대로 남는다.
    """
    out = df
    for label, column, _ in FILTERS:
        if label in chosen and column in out.columns:
            out = out[pd.to_numeric(out[column], errors="coerce") >= 0]
    if exclude_covered_call and "커버드콜" in out.columns:
        out = out[~out["커버드콜"].fillna(False)]
    return out
