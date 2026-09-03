# -*- coding: utf-8 -*-
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from collect import COLS
from classify import classify


def test_seibro_distribution_fields_are_named_by_actual_meaning():
    assert COLS["BUNBE"] == "분배율"
    assert COLS["ESTM_STDPRC"] == "주당분배금"


def test_classify_legacy_raw_uses_estm_stdprc_as_distribution_amount():
    raw = pd.DataFrame(
        {
            "ISIN": ["KR7152100004", "KR7152100004"],
            "종목명": ["PLUS 200", "PLUS 200"],
            "운용사": ["한화자산운용", "한화자산운용"],
            "유형": ["국내지수", "국내지수"],
            "지급기준일": ["20260130", "20260430"],
            "실지급일": ["20260203", "20260507"],
            "주당분배금": [".22622", ".43433"],
            "결산과표기준가": ["180", "500"],
            "배당구분": ["이익분배", "이익분배"],
        }
    )

    result = classify(raw)

    assert result.iloc[0]["최근_주당분배금"] == 500


def test_classify_returns_empty_schema_when_no_profit_distribution_exists():
    raw = pd.DataFrame(
        {
            "ISIN": ["KR7152100004"],
            "종목명": ["PLUS 200"],
            "운용사": ["한화자산운용"],
            "유형": ["국내지수"],
            "지급기준일": ["20260130"],
            "실지급일": ["20260203"],
            "주당분배금": [".22622"],
            "결산과표기준가": ["180"],
            "배당구분": ["청산분배"],
        }
    )

    result = classify(raw)

    assert result.empty
    assert "분배주기" in result.columns
    assert "최근_주당분배금" in result.columns


def test_classify_skips_group_when_all_payment_dates_are_invalid():
    raw = pd.DataFrame(
        {
            "ISIN": ["KR7152100004"],
            "종목명": ["PLUS 200"],
            "운용사": ["한화자산운용"],
            "유형": ["국내지수"],
            "지급기준일": ["invalid"],
            "실지급일": ["invalid"],
            "주당분배금": [".22622"],
            "결산과표기준가": ["180"],
            "배당구분": ["이익분배"],
        }
    )

    result = classify(raw)

    assert result.empty


def test_classify_empty_dataframe_returns_output_schema():
    result = classify(pd.DataFrame())

    assert result.empty
    assert "분배주기" in result.columns


def test_classify_uses_latest_valid_payment_date_not_nat_row():
    raw = pd.DataFrame(
        {
            "ISIN": ["KR7152100004", "KR7152100004"],
            "종목명": ["PLUS 200", "PLUS 200"],
            "운용사": ["한화자산운용", "한화자산운용"],
            "유형": ["국내지수", "국내지수"],
            "지급기준일": ["20260130", "invalid"],
            "실지급일": ["20260203", "invalid"],
            "주당분배금": [".22622", "9.999"],
            "결산과표기준가": ["180", "9999"],
            "배당구분": ["이익분배", "이익분배"],
        }
    )

    result = classify(raw)

    assert result.iloc[0]["최근_주당분배금"] == 180
    assert str(result.iloc[0]["최근_지급기준일"]) == "2026-01-30"
