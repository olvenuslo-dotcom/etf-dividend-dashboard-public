# -*- coding: utf-8 -*-
"""실측 겹침 v1 — 세이브로 섹터 비중의 min-합, 없으면 분류 폴백."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import compare

A, B, C = "KR7000000001", "KR7000000002", "KR7000000003"

SECTORS = {
    A: {"FGSC.45": 60.0, "FGSC.40": 40.0},
    B: {"FGSC.45": 30.0, "FGSC.10": 70.0},   # A 와 겹치는 건 45 섹터 30 뿐
}
TOP10 = {
    A: {"005930": 30.0, "000660": 20.0},
    B: {"005930": 10.0, "035420": 50.0},     # 공통은 삼성전자 10
}
PDF = {
    A: {"KR7005930003": 40.0, "KR7000660001": 60.0},
    B: {"KR7005930003": 15.0, "KR7035420009": 85.0},   # 공통은 삼성전자 15
}


# ── min-합 ──────────────────────────────────────────────
def test_섹터_겹침은_겹치는_부분의_합이다():
    assert compare.real_overlap(A, B, SECTORS) == 30.0


def test_같은_비중이면_100이다():
    assert compare.real_overlap(A, A, SECTORS) == 100.0


def test_자료가_없으면_None이다():
    """0 으로 채우면 '안 겹친다'와 '모른다'가 섞인다."""
    assert compare.real_overlap(A, C, SECTORS) is None
    assert compare.real_overlap(A, B, {}) is None


def test_TOP10_겹침은_확인된_하한이다():
    assert compare.top10_overlap(A, B, TOP10) == 10.0
    assert compare.top10_overlap(A, C, TOP10) is None


# ── v2: 전체 PDF 가 섹터보다 우선한다 ───────────────────
def test_PDF가_있으면_섹터보다_PDF를_쓴다():
    """섹터로는 30인데 진짜 보유종목으로는 15 — 같은 섹터의 다른 종목을
    겹침으로 세지 않으니 PDF 가 더 정확하다."""
    assert compare.real_overlap(A, B, SECTORS, PDF) == 15.0


def test_PDF가_없으면_섹터로_내려간다():
    assert compare.real_overlap(A, B, SECTORS, {}) == 30.0
    assert compare.real_overlap(A, B, SECTORS, None) == 30.0


# ── 실측 우선, 분류 폴백 ────────────────────────────────
def _row(isin, **kw):
    base = {"ISIN": isin, "종목명": f"ETF{isin[-1]}", "기초지수명": "",
            "테마섹터": "반도체", "투자지역": "한국", "자산분류": "주식",
            "전략": "일반", "시장분류": "KOSPI"}
    base.update(kw)
    return pd.Series(base)


def test_실측이_있으면_실측을_쓴다():
    got = compare.pair_overlap(_row(A), _row(B), SECTORS)
    assert got == 30.0            # 분류로는 100% 일 조합인데 실측이 이긴다


def test_실측이_없으면_분류로_폴백한다():
    got = compare.pair_overlap(_row(A), _row(C), SECTORS)
    assert got == 100.0           # 분류 축이 전부 같아서


def test_기초지수가_같으면_언제나_100이다():
    left = _row(A, 기초지수명="코스피 200")
    right = _row(B, 기초지수명="코스피 200")
    assert compare.pair_overlap(left, right, SECTORS) == 100.0


def test_보유종목_겹침도_실측을_쓴다():
    candidates = pd.DataFrame([_row(A)])
    holdings = pd.DataFrame([_row(B)])
    got = compare.overlap_with_holdings(candidates, holdings, SECTORS)
    assert list(got) == [30.0]


# ── 파일 읽기 ───────────────────────────────────────────
def test_수집_CSV를_비중표로_읽는다(tmp_path):
    pd.DataFrame({
        "ISIN": [A, A, B],
        "섹터코드": ["FGSC.45", "FGSC.40", "FGSC.45"],
        "ETF비중": ["60.0", "40.0", "30.0"],
    }).to_csv(tmp_path / "seibro_구성섹터_20260814.csv",
              index=False, encoding="utf-8-sig")
    sectors, top10, pdf = compare.load_compst(tmp_path)
    assert sectors[A] == {"FGSC.45": 60.0, "FGSC.40": 40.0}
    assert top10 == {}            # TOP10 파일이 없으면 빈 dict — 폴백으로 이어진다
    assert pdf == {}
    # B 는 합 30% — FICS 가 포트폴리오 대부분을 못 본 것이라 실측에서 뺀다.
    # 채권 ETF 둘을 '실측 0% 겹침'으로 내보내는 사고를 막는 가드다 (실수집에서
    # 727/1,160 종목이 섹터 비중이 비어 있었다)
    assert B not in sectors


def test_채권형_PDF는_평가금액_비율로_비중을_만든다(tmp_path):
    """KRX PDF 의 '비중' 칸은 주식형만 온다 — 채권형은 평가금액으로 환산한다."""
    pd.DataFrame({
        "ISIN": [A, A, B, B],
        "구성종목ISIN": ["KR1", "KR2", "KR1", "KR3"],
        "비중": ["", "", "", ""],                        # 채권형: 비중 없음
        "평가금액": ["750", "250", "500", "500"],
    }).to_csv(tmp_path / "krx_PDF_20260813.csv",
              index=False, encoding="utf-8-sig")
    _, _, pdf = compare.load_compst(tmp_path)
    assert pdf[A] == {"KR1": 75.0, "KR2": 25.0}
    # 겹침 = min(75, 50) = 50 — 채권끼리도 이제 실측으로 잰다
    assert compare.real_overlap(A, B, {}, pdf) == 50.0
