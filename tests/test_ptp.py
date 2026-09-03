# -*- coding: utf-8 -*-
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import ptp


def test_목록에_없으면_빈칸이다():
    state, _ = ptp.annotate(pd.Series(["SCHD", "VOO"]), today="2026-08-07")

    assert list(state) == ["", ""]


def test_면제표시가_없으면_대상이다():
    # EPD·MPLX 는 면제 칸이 비어 있다 → 언제 팔든 매도대금 10% 원천징수
    state, _ = ptp.annotate(pd.Series(["EPD", "MPLX"]), today="2026-08-07")

    assert list(state) == [ptp.SUBJECT, ptp.SUBJECT]


def test_면제기간_안이면_면제이고_만료일을_알려준다():
    # USO 면제: 2026-07-31 ~ 2026-10-31
    state, expiry = ptp.annotate(pd.Series(["USO"]), today="2026-08-07")

    assert state.iloc[0] == ptp.EXEMPT
    assert expiry.iloc[0] == "2026-10-31"


def test_면제가_만료되면_다시_대상이다():
    # 같은 USO 라도 면제 끝난 뒤에는 대상으로 돌아간다
    state, expiry = ptp.annotate(pd.Series(["USO"]), today="2026-11-01")

    assert state.iloc[0] == ptp.SUBJECT
    assert expiry.iloc[0] == ""


def test_면제_시작_전이면_대상이다():
    state, _ = ptp.annotate(pd.Series(["USO"]), today="2026-07-30")

    assert state.iloc[0] == ptp.SUBJECT


def test_소문자_티커도_찾는다():
    state, _ = ptp.annotate(pd.Series(["uso", " ung "]), today="2026-08-07")

    assert list(state) == [ptp.EXEMPT, ptp.EXEMPT]


def test_거래소가_여러개면_대상을_우선한다():
    # GBLI: 뉴욕(2025 면제, 만료) / 나스닥(2026-06-12~09-12 면제)
    # 놓쳐서 떼이는 쪽이 손해가 크니 '대상'으로 본다
    state, _ = ptp.annotate(pd.Series(["GBLI"]), today="2026-08-07")

    assert state.iloc[0] == ptp.SUBJECT


def test_목록에_상품ETF들이_들어_있다():
    codes = set(ptp.load()["코드"])

    # 한국 투자자가 실제로 많이 사는 원자재·변동성 ETF 들
    assert {"USO", "UNG", "DBC", "UUP", "UVXY", "CORN", "WEAT"} <= codes
