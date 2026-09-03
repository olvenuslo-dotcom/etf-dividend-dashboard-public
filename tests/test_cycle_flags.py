# -*- coding: utf-8 -*-
"""'주기 확인' 플래그의 원인 가르기 — 추측 대신 상장일로 판정한다.

전에는 두 판정의 **순위만 보고** "상장한 지 얼마 안 된 것으로 보임"이라고 적었다.
상장일이 이미 수집돼 있는데 추측할 이유가 없다. 실제로 갈라 보니 셋이 섞여 있었다
(신규상장 25 · 실제 불규칙 95 · 상장일 없음 67).

핵심 규칙: 12개월 창의 지급 **횟수는 적게 나올 수만 있고 많이 나올 수는 없다.**
그래서 신규상장이면서 간격 기준이 **더 잦을 때만** 그것을 따른다.
"""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import classify

TODAY = pd.Timestamp("2026-08-13")


def _row(**kw):
    base = {"종목명": "테스트ETF", "비고": "주기 확인", "비고상세": "확인할 항목: 분배주기.",
            "분배주기": "분기", "지급횟수_12개월": 6, "중앙값간격_일": 31.0,
            "설정일": "2026-01-15", "운용기간_년": None}
    base.update(kw)
    return base


def _run(**kw):
    got = classify.refine_cycle_flags(pd.DataFrame([_row(**kw)]), today=TODAY)
    return got.iloc[0]


# ── 신규상장 (핵심) ─────────────────────────────────────
def test_신규상장이면_그렇게_적는다():
    got = _run(설정일="2026-01-15")          # 약 7개월
    assert got["비고사유"] == classify.CAUSE_NEW
    assert "12개월치가 아직 안 찼" in got["비고상세"]


def test_신규상장이고_간격이_더_잦으면_주기를_바로잡는다():
    """상장 7개월인 월배당은 12개월 창에 6회뿐이라 '분기'로 잘못 잡힌다.

    실제 사례: ACE 리츠부동산인프라액티브 — 26-02~07 매월 줬는데 분기로 분류됐다.
    """
    got = _run(설정일="2026-01-15", 분배주기="분기", 지급횟수_12개월=6,
               중앙값간격_일=31.0)
    assert got["분배주기"] == "월"
    assert "분기 → 월" in got["비고상세"]


def test_신규상장이어도_간격이_덜_잦으면_손대지_않는다():
    """횟수는 적게 나올 수만 있다 — 간격이 더 뜸하다면 횟수 탓이 아니다.

    실제 사례: ACE 유럽방산TOP10 — 3회·간격 106일이라 간격 기준(반기)이 오히려 과하다.
    """
    got = _run(설정일="2025-09-20", 분배주기="분기", 지급횟수_12개월=3,
               중앙값간격_일=106.5)
    assert got["분배주기"] == "분기"          # 반기로 바뀌면 안 된다
    assert got["비고사유"] == classify.CAUSE_NEW
    assert "바로잡" not in got["비고상세"]


# ── 실제 불규칙 ─────────────────────────────────────────
def test_오래된_종목은_신규상장_탓으로_돌리지_않는다():
    """상장 10년이면 12개월치가 안 찼을 리 없다. 전에는 이것도 신규상장이라 적었다."""
    got = _run(설정일="2015-09-01", 분배주기="분기", 중앙값간격_일=136.5,
               지급횟수_12개월=3)
    assert got["비고사유"] == classify.CAUSE_IRREGULAR
    assert "신규상장 탓이 아닙니다" in got["비고상세"]
    assert got["분배주기"] == "분기"          # 오래된 종목은 주기를 안 고친다


def test_연3회_설명이_들어간다():
    """국내 지수형은 4·7·10월만 주는 연 3회가 흔하다 — 분기도 반기도 아니다."""
    got = _run(설정일="2015-09-01", 중앙값간격_일=136.5)
    # 두 해 확인이 된 종목은 '연3회'로 빠졌고, 남은 것엔 그 사실을 알려준다
    assert "'연3회'로 따로 분류" in got["비고상세"]


# ── 상장일 없음 ─────────────────────────────────────────
def test_상장일이_없으면_모른다고_적는다():
    """미국 66건이 여기 해당한다. 모르는 것을 신규상장으로 몰면 안 된다."""
    got = _run(설정일=None, 운용기간_년=None)
    assert got["비고사유"] == classify.CAUSE_UNKNOWN
    assert "가르지 못했" in got["비고상세"]
    assert got["분배주기"] == "분기"          # 모르면 주기를 고치지 않는다


def test_설정일이_없어도_운용기간_년으로_판정한다():
    got = _run(설정일=None, 운용기간_년=0.5, 분배주기="분기", 중앙값간격_일=31.0)
    assert got["비고사유"] == classify.CAUSE_NEW
    assert got["분배주기"] == "월"


# ── 플래그가 없는 행은 건드리지 않는다 ───────────────────
def test_플래그가_없으면_손대지_않는다():
    """신규상장 월배당이어도 플래그가 없으면(두 판정이 같으면) 고칠 것이 없다."""
    frame = pd.DataFrame([_row(비고="", 분배주기="분기", 중앙값간격_일=31.0)])
    got = classify.refine_cycle_flags(frame, today=TODAY)
    assert got["분배주기"].iloc[0] == "분기"
    assert "비고사유" not in got.columns          # 플래그가 없으면 칸도 안 생긴다


def test_빈_표를_넣어도_안_죽는다():
    assert classify.refine_cycle_flags(pd.DataFrame()).empty
    frame = pd.DataFrame({"종목명": ["x"]})       # 비고 칸이 아예 없는 경우
    assert len(classify.refine_cycle_flags(frame)) == 1


# ── 경계 ────────────────────────────────────────────────
def test_딱_12개월이면_신규상장이_아니다():
    """12개월을 채웠으면 창이 다 찼다 — 횟수가 모자랄 이유가 없다."""
    got = _run(설정일="2025-08-01", 분배주기="분기", 중앙값간격_일=31.0)
    assert got["비고사유"] == classify.CAUSE_IRREGULAR
    assert got["분배주기"] == "분기"
