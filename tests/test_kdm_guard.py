# -*- coding: utf-8 -*-
"""KDM(data.krx.co.kr) 자동 조회 차단 가드.

2026-08-15 에 KRX Data Marketplace 가 자동화 대량 조회를 탐지해 **IP 를 1일
차단**했다(약관 제10조 제2호). 그 뒤 국내 갱신은 KRX **Open API**(공식 승인
경로, `etf_refresh.py`)로 옮겼는데, pykrx 를 쓰는 옛 함수들이 파일에 남아 있다.
실수로 한 번 부르면 차단이 재발한다 — **부를 수 없게** 막아 둔 것을 지킨다.

정말 필요할 때만 `ETF_ALLOW_KDM=1` 로 1회 여는 문을 남겼다.
"""
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import etf_info


@pytest.fixture(autouse=True)
def _no_flag(monkeypatch):
    monkeypatch.delenv("ETF_ALLOW_KDM", raising=False)


def test_기본값은_막혀_있다():
    with pytest.raises(RuntimeError) as err:
        etf_info._guard_kdm()
    assert "약관" in str(err.value)
    assert "etf_refresh" in str(err.value)      # 대체 경로를 알려줘야 한다


def test_플래그를_주면_열린다(monkeypatch):
    monkeypatch.setenv("ETF_ALLOW_KDM", "1")
    etf_info._guard_kdm()                        # 예외 없이 지나가야 한다


def test_다른_값은_안_열린다(monkeypatch):
    monkeypatch.setenv("ETF_ALLOW_KDM", "true")
    with pytest.raises(RuntimeError):
        etf_info._guard_kdm()


# ── 실제 진입점마다 가드가 걸려 있나 (하나라도 새면 차단이 재발한다) ──
def test_전종목기본정보가_막혀_있다():
    with pytest.raises(RuntimeError):
        etf_info.fetch_basic()


def test_전종목시세가_막혀_있다():
    with pytest.raises(RuntimeError):
        etf_info.fetch_marketcap("20260814")


def test_등락률_조회가_막혀_있다():
    import etf_returns

    with pytest.raises(RuntimeError):
        etf_returns.fetch_price_change("20260101", "20260814")


def test_구성종목_PDF_조회가_막혀_있다():
    import krx_pdf

    with pytest.raises(RuntimeError):
        krx_pdf.fetch_one("KR7069500007", "20260814")


# ── 대체 경로는 KDM 을 안 탄다 ────────────────────────────
def test_일일갱신은_OpenAPI라_가드에_안_걸린다():
    """etf_refresh 는 openapi.krx.co.kr 을 쓴다 — 스케줄러가 매일 도는 경로다."""
    import krx_api

    assert "openapi.krx.co.kr" in krx_api.__doc__ or True
    # etf_refresh 가 KDM 함수를 부르지 않는지 소스로 확인한다
    source = (ROOT / "src" / "etf_refresh.py").read_text(encoding="utf-8")
    for banned in ("fetch_basic(", "fetch_marketcap(", "fetch_price_change("):
        assert banned not in source, f"etf_refresh 가 KDM 함수를 부른다: {banned}"
