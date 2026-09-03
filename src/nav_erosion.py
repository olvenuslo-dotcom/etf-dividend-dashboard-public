# -*- coding: utf-8 -*-
"""ETF NAV 침식 계산의 핵심 함수."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from typing import Callable
import re

import truststore   # 회사 PC 는 프록시가 인증서를 바꿔쳐 pykrx(HTTPS)가 죽는다
truststore.inject_into_ssl()   # → OS 인증서 저장소로 검증. 개인 노트북에서는 무해
import numpy as np
import pandas as pd


def _has_valid_isin_checksum(value: str) -> bool:
    """ISO 6166 ISIN 체크디지트를 Luhn 방식으로 검증한다."""
    expanded = "".join(str(ord(char) - 55) if char.isalpha() else char for char in value)
    total = 0
    for position, digit in enumerate(reversed(expanded)):
        number = int(digit)
        if position % 2 == 1:
            number *= 2
        total += number // 10 + number % 10
    return total % 10 == 0


def isin_to_ticker(isin: str) -> str:
    """국내 상장 증권 ISIN에서 KRX 6자리 종목코드를 추출한다."""
    value = str(isin).strip()
    ticker = value[3:9] if len(value) == 12 else ""
    if (
        len(value) != 12
        or re.fullmatch(r"[A-Z0-9]{12}", value) is None
        or not value.startswith("KR7")
        or len(ticker) != 6
        or re.fullmatch(r"[A-Z0-9]{6}", ticker) is None
        or not _has_valid_isin_checksum(value)
    ):
        raise ValueError(f"KRX 종목코드로 변환할 수 없는 ISIN입니다: {isin}")
    return ticker


def filter_valid_isin_rows(frame: pd.DataFrame) -> pd.DataFrame:
    """유효한 국내 ISIN 행만 남기고 KRX코드를 추가한다.
    입력에 이미 KRX코드가 있으면 덮어쓴다(컬럼이 두 개 생기지 않게)."""
    columns = [c for c in frame.columns if c != "KRX코드"] + ["KRX코드"]
    if "ISIN" not in frame.columns:
        return pd.DataFrame(columns=columns)
    rows = []
    for _, row in frame.iterrows():
        try:
            ticker = isin_to_ticker(row["ISIN"])
        except ValueError:
            continue
        item = row.to_dict()
        item["KRX코드"] = ticker
        rows.append(item)
    return pd.DataFrame(rows, columns=columns)


def public_nav_error_message(_error: Exception) -> str:
    """외부 예외 상세를 노출하지 않는 사용자용 오류 메시지."""
    return ("NAV 조회에 실패했습니다. 잠시 후 다시 시도해 주세요. "
            "(.env 의 KIS_APP_KEY/KIS_APP_SECRET 확인)")


def _validate_nav_frame(frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    """KRX NAV 프레임의 스키마·날짜·숫자 무결성을 검증한다."""
    if frame is None or frame.empty:
        raise ValueError("KRX NAV 데이터가 비어 있습니다.")
    required = ["종가", "NAV", "괴리율"]
    missing = [name for name in required if name not in frame.columns]
    if missing:
        raise ValueError(f"KRX NAV 응답 컬럼이 부족합니다: {', '.join(missing)}")

    clean = frame[required].copy()
    try:
        clean.index = pd.to_datetime(clean.index, errors="raise")
    except (TypeError, ValueError) as exc:
        raise ValueError("KRX NAV 날짜 인덱스가 올바르지 않습니다.") from exc
    if clean.index.isna().any():
        raise ValueError("KRX NAV 날짜 인덱스에 결측값이 있습니다.")
    if clean.index.has_duplicates:
        raise ValueError("KRX NAV 날짜 인덱스가 중복되었습니다.")
    start_ts = pd.to_datetime(start, format="%Y%m%d")
    end_ts = pd.to_datetime(end, format="%Y%m%d")
    if ((clean.index < start_ts) | (clean.index > end_ts)).any():
        raise ValueError("KRX NAV 데이터가 요청 날짜 범위를 벗어났습니다.")

    for column in required:
        clean[column] = pd.to_numeric(clean[column], errors="coerce")
        if clean[column].isna().any() or not np.isfinite(clean[column].to_numpy()).all():
            raise ValueError(f"KRX NAV {column} 값이 숫자형 유한값이 아닙니다.")
    if (clean[["종가", "NAV"]] <= 0).any().any():
        raise ValueError("KRX NAV 종가와 NAV는 0보다 커야 합니다.")
    clean.index.name = None
    return clean.sort_index()


def load_distributions_for_isin(raw_dir: Path, isin: str) -> pd.DataFrame:
    """최신 세이브로 원본에서 선택 ISIN의 이익분배 이력만 읽는다."""
    files = sorted(Path(raw_dir).glob("seibro_분배금_*.csv"))
    if not files:
        raise FileNotFoundError("세이브로 분배금 원본 CSV가 없습니다.")

    raw = pd.read_csv(files[-1], dtype=str)
    required = {"ISIN", "지급기준일", "주당분배금", "배당구분"}
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(f"분배금 원본 컬럼이 부족합니다: {', '.join(sorted(missing))}")

    selected = raw[(raw["ISIN"] == isin) & (raw["배당구분"] == "이익분배")].copy()
    selected["지급기준일"] = pd.to_datetime(
        selected["지급기준일"], format="%Y%m%d", errors="coerce"
    )
    # 2026-07-26 이전 수집본은 ESTM_STDPRC가 '결산과표기준가'로 잘못 명명됐다.
    amount_column = "결산과표기준가" if "결산과표기준가" in selected.columns else "주당분배금"
    selected["주당분배금"] = pd.to_numeric(
        selected[amount_column].str.replace(",", "", regex=False), errors="coerce"
    )
    return selected.dropna(subset=["지급기준일", "주당분배금"]).sort_values("지급기준일")


def fetch_nav_history(
    ticker: str,
    start: str,
    end: str,
    cache_dir: Path,
    fetcher: Callable | None = None,
    force: bool = False,
) -> pd.DataFrame:
    """KRX ETF 종가·NAV·괴리율을 조회하고 요청 기간별 CSV로 캐시한다."""
    if re.fullmatch(r"[A-Z0-9]{6}", ticker) is None:
        raise ValueError("KRX 종목코드는 대문자·숫자 6자리여야 합니다.")
    for label, value in (("시작일", start), ("종료일", end)):
        try:
            parsed = pd.to_datetime(value, format="%Y%m%d", errors="raise")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label}은 YYYYMMDD 형식이어야 합니다.") from exc
        if parsed.strftime("%Y%m%d") != value:
            raise ValueError(f"{label}은 YYYYMMDD 형식이어야 합니다.")
    if start > end:
        raise ValueError("시작일은 종료일보다 늦을 수 없습니다.")
    cache_dir = Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_path = cache_dir / f"{ticker}_{start}_{end}.csv"
    if cache_path.exists() and not force:
        try:
            cached = pd.read_csv(cache_path, index_col=0, parse_dates=True)
            return _validate_nav_frame(cached, start, end)
        except (OSError, ValueError, pd.errors.ParserError):
            pass

    if fetcher is None:
        # 한투 API [0244] NAV 비교추이(일) — 전에는 pykrx 로 KRX 웹을 긁었는데
        # KDM 이 자동화 조회를 약관으로 금지해(44단계-취소) KIS 로 갈아탔다.
        # 2012년까지 내려가는 것을 실측으로 확인했다(45단계-보완).
        import kis_api
        frame = kis_api.nav_history(ticker, start, end).set_index("날짜")
    else:
        with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
            frame = fetcher(start, end, ticker)
    clean = _validate_nav_frame(frame, start, end)
    clean.to_csv(cache_path, encoding="utf-8-sig")
    return clean


def calculate_nav_metrics(nav: pd.DataFrame, distributions: pd.DataFrame) -> dict:
    """NAV 기간 안의 분배금을 합쳐 침식 관련 지표를 계산한다.

    총수익률 대용치는 분배금 재투자를 가정하지 않은 단순 합산값이다.
    """
    if "NAV" not in nav.columns:
        raise ValueError("NAV 컬럼이 필요합니다.")

    clean = nav.copy()
    clean.index = pd.to_datetime(clean.index)
    clean["NAV"] = pd.to_numeric(clean["NAV"], errors="coerce")
    clean = clean.dropna(subset=["NAV"]).sort_index()
    if not np.isfinite(clean["NAV"].to_numpy()).all() or (clean["NAV"] <= 0).any():
        raise ValueError("NAV는 양수인 유한값이어야 합니다.")
    if len(clean) < 2:
        raise ValueError("NAV 관측값이 2개 이상 필요합니다.")

    start_date, end_date = clean.index[0], clean.index[-1]
    nav_start = float(clean["NAV"].iloc[0])
    nav_end = float(clean["NAV"].iloc[-1])
    if nav_start <= 0:
        raise ValueError("시작 NAV는 0보다 커야 합니다.")

    cumulative_distribution = 0.0
    if not distributions.empty and {"지급기준일", "주당분배금"}.issubset(distributions.columns):
        dist = distributions.copy()
        dist["지급기준일"] = pd.to_datetime(dist["지급기준일"], errors="coerce")
        dist["주당분배금"] = pd.to_numeric(dist["주당분배금"], errors="coerce")
        inside = dist[dist["지급기준일"].between(start_date, end_date, inclusive="both")]
        cumulative_distribution = float(inside["주당분배금"].fillna(0).sum())

    nav_change_pct = (nav_end / nav_start - 1) * 100
    distribution_yield_pct = cumulative_distribution / nav_start * 100
    total_return_proxy_pct = (nav_end - nav_start + cumulative_distribution) / nav_start * 100
    max_drawdown_pct = float((clean["NAV"] / clean["NAV"].cummax() - 1).min() * 100)

    if nav_change_pct >= 0:
        status = "양호"
    elif nav_change_pct <= -10:
        status = "침식주의"
    else:
        status = "침식관찰"

    return {
        "start_date": start_date.date(),
        "end_date": end_date.date(),
        "nav_start": nav_start,
        "nav_end": nav_end,
        "nav_change_pct": nav_change_pct,
        "cumulative_distribution": cumulative_distribution,
        "distribution_yield_pct": distribution_yield_pct,
        "total_return_proxy_pct": total_return_proxy_pct,
        "max_drawdown_pct": max_drawdown_pct,
        "status": status,
    }
