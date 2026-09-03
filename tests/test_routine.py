# -*- coding: utf-8 -*-
"""매수 전 5분 루틴 판정.

가장 중요한 규칙: **자료가 없는 것을 통과로 치지 않는다.** 빈칸을 0으로 읽거나
NA를 통과로 두면, 걸러내려고 만든 필터가 정작 걸러야 할 종목을 남긴다.
"""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import routine


def _row(**kwargs) -> pd.Series:
    """판정에 쓰는 칸만 담은 한 줄. 안 준 칸은 비어 있다."""
    base = {"종목명": "테스트ETF", "분배주기": "월", "전략": "일반",
            "상장시장": "미국", "1년수익률": None, "분배변동계수": None,
            "연간분배율": None, "중앙값연분배율": None,
            "누적_원금반환비율": None}
    base.update(kwargs)
    return pd.Series(base)


# ── ② 가격 잠식 (① Coverage 를 합친 축) ──────────────
def test_가격이_안_빠졌으면_긍정():
    assert routine.erosion(_row(**{"1년수익률": 3.2}))["점수"] == routine.GOOD


def test_10퍼센트_안쪽으로_빠졌으면_중립():
    assert routine.erosion(_row(**{"1년수익률": -7.0}))["점수"] == routine.NEUTRAL


def test_10퍼센트_넘게_빠졌으면_부정():
    assert routine.erosion(_row(**{"1년수익률": -18.4}))["점수"] == routine.BAD


def test_수익률이_없으면_판정하지_않는다():
    """0으로 읽어 '중립' 처리하면 신규 상장이 전부 통과한다."""
    card = routine.erosion(_row())
    assert card["점수"] is routine.NO_DATA


# ── 분배 안정성 — 주기마다 기준이 다르다 ────────────────
def test_같은_변동계수라도_주기에_따라_판정이_갈린다():
    """월 8.2% / 위클리 71.5% 기준. 40% 는 월엔 부정, 위클리엔 긍정이다."""
    월 = routine.stability(_row(분배주기="월", 분배변동계수=40.0))
    위클리 = routine.stability(_row(분배주기="위클리", 분배변동계수=40.0))
    assert 월["점수"] == routine.BAD
    assert 위클리["점수"] == routine.GOOD


def test_지급이_모자라면_판정하지_않는다():
    assert routine.stability(_row(분배주기="반기"))["점수"] is routine.NO_DATA


# ── ④ 특별분배 착시 ───────────────────────────────────
def test_특별분배가_없으면_긍정():
    card = routine.special_distribution(_row(연간분배율=4.0, 중앙값연분배율=3.9))
    assert card["점수"] == routine.GOOD


def test_표시_분배율이_특별분배에_기대면_부정():
    """표시 9.33% 인데 평상은 0.18% — 실제 Direxion Mag7 2X 값."""
    card = routine.special_distribution(_row(연간분배율=9.33, 중앙값연분배율=0.18))
    assert card["점수"] == routine.BAD
    assert "0.18" in card["근거"]


def test_중앙값이_0이면_판정하지_않는다():
    """분모가 0이면 부풀린 비율을 낼 수 없다."""
    card = routine.special_distribution(_row(연간분배율=5.0, 중앙값연분배율=0.0))
    assert card["점수"] is routine.NO_DATA


# ── ⑤ 커버드콜 / ③ 이익초과분배 — 점수를 매기지 않는다 ──
def test_커버드콜은_점수를_매기지_않는다():
    """나쁘다는 뜻이 아니라 사람이 천장을 확인할 항목이라 점수에서 뺀다."""
    card = routine.covered_call(_row(전략="커버드콜"))
    assert card["점수"] is routine.NO_DATA
    assert "직접 확인" in card["해석"]


def test_이익초과분배는_자료없음을_숨기지_않는다():
    """19a 를 못 받은 미국 종목은 판정하지 않는다."""
    card = routine.excess_distribution(_row(상장시장="미국"))
    assert card["점수"] is routine.NO_DATA
    assert "수집" in card["근거"] or "수집" in card["해석"]


# ── ③ 이익초과분배(ROC) — 19a-1 공시값 ──────────────────
def test_원금반환이_대부분이면_부정():
    """QYLD 실제값 — 회계연도 누적 원금반환 99.29%."""
    card = routine.excess_distribution(_row(상장시장="미국", 누적_원금반환비율=99.29))
    assert card["점수"] == routine.BAD
    assert "99.3" in card["근거"] or "99.29" in card["근거"]


def test_원금반환이_적으면_긍정():
    card = routine.excess_distribution(_row(상장시장="미국", 누적_원금반환비율=4.72))
    assert card["점수"] == routine.GOOD


def test_원금반환이_중간이면_중립():
    card = routine.excess_distribution(_row(상장시장="미국", 누적_원금반환비율=38.05))
    assert card["점수"] == routine.NEUTRAL


def test_누적이_없으면_최근_회차로_판정한다():
    """Roundhill·Rex·GraniteShares 는 회계연도 누적을 공시하지 않는다.

    누적이 없다고 판정을 포기하면 **고배당이 몰린 이 발행사들이 통째로 필터를
    빠져나간다.** 대신 무엇으로 판정했는지 근거에 적어야 한다.
    """
    card = routine.excess_distribution(_row(상장시장="미국", 원금반환비율=97.7))
    assert card["점수"] == routine.BAD
    assert "최근 회차" in card["근거"]
    assert "97.7" in card["근거"]


def test_누적이_있으면_누적을_쓴다():
    """둘 다 있으면 누적이 이긴다 — 한 회차는 그 달만 유난했을 수 있다."""
    card = routine.excess_distribution(
        _row(상장시장="미국", 누적_원금반환비율=38.05, 원금반환비율=99.0))
    assert card["점수"] == routine.NEUTRAL       # 누적 38.05 → 중립
    assert "누적" in card["근거"] and "38.0" in card["근거"]


def test_최근_회차로_판정하면_거칠다고_알린다():
    card = routine.excess_distribution(_row(상장시장="미국", 원금반환비율=10.0))
    assert card["점수"] == routine.GOOD
    assert "누적보다 거칩니다" in card["해석"]


def test_최근_회차만_있는_종목도_필터를_통과할_수_있다():
    frame = routine.score_frame(pd.DataFrame([
        _row(종목명="회차낮음", 상장시장="미국", 원금반환비율=5.0),
        _row(종목명="회차높음", 상장시장="미국", 원금반환비율=97.7),
        _row(종목명="공시없음", 상장시장="미국"),
    ]))
    남음 = set(routine.apply_filters(frame, ["원금 반환이 절반 미만"])["종목명"])
    assert 남음 == {"회차낮음"}


def test_국내는_해당_사례가_없다고_말한다():
    """세이브로 전수 확인 결과라, 미국과 다른 문구가 나와야 한다."""
    card = routine.excess_distribution(_row(상장시장="한국"))
    assert card["점수"] is routine.NO_DATA
    assert "국내" in card["근거"]


def test_ROC_필터는_공시_없는_종목을_떨어뜨린다():
    """모르는 것을 통과로 치면 YieldMax 같은 미수집 종목이 그대로 남는다."""
    frame = routine.score_frame(pd.DataFrame([
        _row(종목명="ROC많음", 상장시장="미국", 누적_원금반환비율=99.0),
        _row(종목명="ROC적음", 상장시장="미국", 누적_원금반환비율=5.0),
        _row(종목명="공시없음", 상장시장="미국"),
    ]))
    남음 = set(routine.apply_filters(frame, ["원금 반환이 절반 미만"])["종목명"])
    assert 남음 == {"ROC적음"}


# ── 총점 ─────────────────────────────────────────────
def test_총점은_판정된_항목만_더한다():
    row = _row(**{"1년수익률": 5.0, "분배주기": "월", "분배변동계수": 5.0,
                  "연간분배율": 4.0, "중앙값연분배율": 3.9, "전략": "커버드콜"})
    cards = routine.judge(row)
    assert len(cards) == 5
    assert routine.total_score(cards) == 3      # 커버드콜·이익초과분배는 빠진다


def test_자료가_하나도_없으면_총점_0이지만_통과는_아니다():
    """총점 0은 '중립'이 아니라 '아무것도 못 봤다'일 수 있다 — 필터가 걸러야 한다."""
    cards = routine.judge(_row())
    assert routine.total_score(cards) == 0
    frame = routine.score_frame(pd.DataFrame([_row()]))
    assert frame["루틴점수"].iloc[0] == 0
    assert len(routine.apply_filters(frame, ["가격이 10% 넘게 빠지지 않음"])) == 0


# ── 필터 ─────────────────────────────────────────────
def _frame() -> pd.DataFrame:
    return pd.DataFrame([
        _row(종목명="좋은놈", **{"1년수익률": 4.0, "분배변동계수": 5.0,
                                 "연간분배율": 4.0, "중앙값연분배율": 3.95}),
        _row(종목명="가격빠짐", **{"1년수익률": -30.0, "분배변동계수": 5.0,
                                   "연간분배율": 4.0, "중앙값연분배율": 3.95}),
        _row(종목명="특별분배", **{"1년수익률": 4.0, "분배변동계수": 5.0,
                                   "연간분배율": 9.0, "중앙값연분배율": 0.2}),
        _row(종목명="커버드콜종목", 전략="커버드콜",
             **{"1년수익률": 4.0, "분배변동계수": 5.0,
                "연간분배율": 4.0, "중앙값연분배율": 3.95}),
    ])


def test_필터를_걸면_해당_종목이_빠진다():
    frame = routine.score_frame(_frame())
    남음 = routine.apply_filters(frame, ["가격이 10% 넘게 빠지지 않음"])
    assert "가격빠짐" not in set(남음["종목명"])
    assert "좋은놈" in set(남음["종목명"])

    남음 = routine.apply_filters(frame, ["특별분배로 부풀지 않음"])
    assert "특별분배" not in set(남음["종목명"])


def test_커버드콜_빼기는_따로_동작한다():
    frame = routine.score_frame(_frame())
    assert "커버드콜종목" in set(routine.apply_filters(frame, [])["종목명"])
    남음 = routine.apply_filters(frame, [], exclude_covered_call=True)
    assert "커버드콜종목" not in set(남음["종목명"])


def test_아무것도_안_고르면_그대로다():
    frame = routine.score_frame(_frame())
    assert len(routine.apply_filters(frame, [])) == len(frame)


def test_빈_표에서도_칸이_생긴다():
    """필터를 다 걸어 0개가 돼도 뒤 화면이 KeyError 로 죽으면 안 된다."""
    empty = routine.score_frame(pd.DataFrame())
    for column in ("루틴점수", "루틴_가격잠식", "커버드콜"):
        assert column in empty.columns
    assert len(routine.apply_filters(empty, ["분배가 고름"], True)) == 0
