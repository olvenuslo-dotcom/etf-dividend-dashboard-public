# -*- coding: utf-8 -*-
"""화면 표기 규칙 테스트: 운용사 축약 / 국내·해외 / 분배율 / 비고 문구 / 짧은 날짜."""
from datetime import date
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import etf_info
from classify import OUTPUT_COLUMNS, attach_krx_info, classify, short_manager
from calendar_ics import ymd


def _raw(**overrides) -> pd.DataFrame:
    """구 수집본 형태(주당분배금=분배율, 결산과표기준가=금액)의 원본 2행."""
    base = {
        "ISIN": ["KR7152100004", "KR7152100004"],
        "종목명": ["PLUS 200", "PLUS 200"],
        "운용사": ["한화자산운용", "한화자산운용"],
        "유형": ["국내지수", "국내지수"],
        "지급기준일": ["20260130", "20260430"],
        "실지급일": ["20260203", "20260507"],
        "주당분배금": [".22622", ".43433"],      # 실제 의미는 분배율(%)
        "결산과표기준가": ["180", "500"],          # 실제 의미는 주당분배금(원)
        "배당구분": ["이익분배", "이익분배"],
    }
    base.update(overrides)
    return pd.DataFrame(base)


def test_short_manager_removes_company_suffix():
    assert short_manager("미래에셋자산운용") == "미래에셋"
    assert short_manager("한국투자신탁운용") == "한국투자"
    assert short_manager("키움투자자산운용") == "키움투자"
    assert short_manager("한국투자밸류자산운용") == "한국투자밸류"


def test_short_manager_keeps_name_when_nothing_would_remain():
    assert short_manager("자산운용") == "자산운용"


def test_classify_shortens_manager_name():
    assert classify(_raw()).iloc[0]["운용사"] == "한화"


def test_classify_marks_domestic_and_overseas_without_hyeong_suffix():
    domestic = classify(_raw())
    overseas = classify(_raw(유형=["해외지수/해외지수", "해외지수/해외지수"]))

    assert domestic.iloc[0]["국내해외"] == "국내"
    assert overseas.iloc[0]["국내해외"] == "해외"


def test_classify_keeps_bunbe_as_distribution_rate():
    result = classify(_raw())

    # 금액과 분배율이 서로 다른 컬럼에서 정확히 온다
    assert result.iloc[0]["최근_주당분배금"] == 500
    assert result.iloc[0]["최근_분배율"] == 0.43433


def test_classify_reads_rate_column_from_new_collection_format():
    """신 수집본은 분배율/주당분배금이 이름 그대로다."""
    raw = pd.DataFrame({
        "ISIN": ["KR7152100004", "KR7152100004"],
        "종목명": ["PLUS 200", "PLUS 200"],
        "운용사": ["한화자산운용", "한화자산운용"],
        "유형": ["국내지수", "국내지수"],
        "지급기준일": ["20260130", "20260430"],
        "실지급일": ["20260203", "20260507"],
        "분배율": [".22622", ".43433"],
        "주당분배금": ["180", "500"],
        "배당구분": ["이익분배", "이익분배"],
    })

    result = classify(raw)

    assert result.iloc[0]["최근_주당분배금"] == 500
    assert result.iloc[0]["최근_분배율"] == 0.43433


def test_note_says_which_item_needs_checking():
    """지급 5회(=분기)인데 간격은 30일(=월)이라 판정이 갈리는 종목."""
    dates = ["20260130", "20260227", "20260330", "20260430", "20260529"]
    raw = pd.DataFrame({
        "ISIN": ["KR7152100004"] * 5,
        "종목명": ["ACE 신규상장"] * 5,
        "운용사": ["한화자산운용"] * 5,
        "유형": ["국내지수"] * 5,
        "지급기준일": dates,
        "실지급일": dates,
        "주당분배금": [".2"] * 5,
        "결산과표기준가": ["180"] * 5,
        "배당구분": ["이익분배"] * 5,
    })

    row = classify(raw).iloc[0]

    assert row["분배주기"] == "분기"        # 횟수 기준 판정은 그대로
    assert row["비고"] == "주기 확인"        # 표에는 짧게
    assert "확인할 항목: 분배주기" in row["비고상세"]
    assert "상장한 지 얼마 안 돼" in row["비고상세"]


def test_note_is_empty_when_both_judgements_agree():
    """12개월 내내 월배당 → 횟수 기준도 월, 간격 기준도 월이라 비고가 없다."""
    dates = [f"2026{month:02d}28" for month in range(1, 13)]
    raw = pd.DataFrame({
        "ISIN": ["KR7152100004"] * 12,
        "종목명": ["PLUS 200"] * 12,
        "운용사": ["한화자산운용"] * 12,
        "유형": ["국내지수"] * 12,
        "지급기준일": dates,
        "실지급일": dates,
        "주당분배금": [".2"] * 12,
        "결산과표기준가": ["180"] * 12,
        "배당구분": ["이익분배"] * 12,
    })

    row = classify(raw).iloc[0]

    assert row["분배주기"] == "월"
    assert row["비고"] == ""
    assert row["비고상세"] == ""


def test_market_cap_and_fee_sit_left_of_cycle():
    """사용자 요청: 분배주기 왼쪽에 시총·총보수."""
    assert OUTPUT_COLUMNS.index("시가총액") < OUTPUT_COLUMNS.index("분배주기")
    assert OUTPUT_COLUMNS.index("총보수") < OUTPUT_COLUMNS.index("분배주기")


def test_classify_leaves_krx_columns_empty_until_attached():
    row = classify(_raw()).iloc[0]

    assert pd.isna(row["시가총액"])
    assert pd.isna(row["총보수"])


def test_attach_krx_info_fills_by_isin(monkeypatch):
    monkeypatch.setattr(etf_info, "load_latest", lambda: pd.DataFrame({
        "ISIN": ["KR7152100004", "KR7000000000"],
        "시가총액": [420312000000, 1],
        "총보수": [0.07, 9.99],
    }))

    row = attach_krx_info(classify(_raw())).iloc[0]

    assert row["시가총액"] == 420312000000
    assert row["총보수"] == 0.07
    assert list(attach_krx_info(classify(_raw())).columns) == OUTPUT_COLUMNS


def test_attach_krx_info_keeps_rows_without_krx_match(monkeypatch):
    """KRX에 없는 상장폐지 종목도 목록에서 사라지면 안 된다."""
    monkeypatch.setattr(etf_info, "load_latest", lambda: pd.DataFrame({
        "ISIN": ["KR7999999999"], "시가총액": [1], "총보수": [0.5],
    }))

    result = attach_krx_info(classify(_raw()))

    assert len(result) == 1
    assert pd.isna(result.iloc[0]["시가총액"])


def test_attach_krx_info_is_noop_without_collected_file(monkeypatch):
    monkeypatch.setattr(etf_info, "load_latest",
                        lambda: pd.DataFrame(columns=etf_info.OUTPUT_COLUMNS))

    result = attach_krx_info(classify(_raw()))

    assert len(result) == 1
    assert pd.isna(result.iloc[0]["총보수"])


def test_fetch_marketcap_drops_closed_day_rows(monkeypatch):
    """휴장일에는 종목 목록만 오고 값이 비어 있다 → 버려야 한다."""
    import types

    # 이 테스트는 **파싱**을 보는 것이라 KDM 가드를 열어 준다 (실제 조회는
    # 가짜 모듈로 대체된다). 가드 자체는 test_kdm_guard.py 가 지킨다.
    monkeypatch.setenv("ETF_ALLOW_KDM", "1")
    fake_core = types.ModuleType("pykrx.website.krx.etx.core")

    class 전종목시세_ETF:                      # noqa: N801 - KRX 원본 클래스명
        def fetch(self, _date):
            return pd.DataFrame({"ISU_CD": ["KR7152100004"], "MKTCAP": [""]})

    fake_core.전종목시세_ETF = 전종목시세_ETF
    monkeypatch.setitem(sys.modules, "pykrx.website.krx.etx.core", fake_core)

    assert etf_info.fetch_marketcap("20260802").empty


def test_ymd_shortens_dates():
    assert ymd(date(2026, 8, 14)) == "26-8-14"
    assert ymd(date(2026, 12, 1)) == "26-12-1"
    assert ymd(pd.Timestamp("2027-01-05")) == "27-1-5"
