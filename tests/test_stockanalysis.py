# -*- coding: utf-8 -*-
"""stockanalysis.com 응답 파싱 테스트 (네트워크 없이 값 변환만)."""
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import us_issuers


def test_금액_단위를_숫자로_바꾼다():
    assert us_issuers._sa_money("$1.11B") == 1_110_000_000.0
    assert us_issuers._sa_money("$146.17M") == 146_170_000.0
    assert us_issuers._sa_money("$1.03T") == 1_030_000_000_000.0
    assert us_issuers._sa_money("$500") == 500.0
    assert us_issuers._sa_money(None) is None
    assert us_issuers._sa_money("n/a") is None


def test_퍼센트를_숫자로_바꾼다():
    assert us_issuers._sa_percent("0.99%") == 0.99
    assert us_issuers._sa_percent("40.06%") == 40.06
    assert us_issuers._sa_percent(None) is None
    assert us_issuers._sa_percent("-") is None


def test_인덱스_참조를_실제_값으로_푼다():
    # SvelteKit 은 data 배열의 '자리 번호'로 값을 가리킨다
    data = ["placeholder", "NEOS Bitcoin High Income ETF", "0.99%"]
    assert us_issuers._sa_resolve(data, 1) == "NEOS Bitcoin High Income ETF"
    assert us_issuers._sa_resolve(data, 2) == "0.99%"
    # 범위를 벗어나거나 숫자가 아니면 그대로 둔다
    assert us_issuers._sa_resolve(data, 99) == 99
    assert us_issuers._sa_resolve(data, "0.5%") == "0.5%"
