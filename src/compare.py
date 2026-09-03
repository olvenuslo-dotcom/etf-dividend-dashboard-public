# -*- coding: utf-8 -*-
"""대안 비교 — 후보를 좁히고 **최대 4개를 나란히** 놓고 견준다.

여기서 가장 중요한 칸은 **겹침**이다. 이미 가진 것과 사실상 같은 ETF 로 갈아타면
비용만 들고 분산은 그대로다. 그래서 종목을 고르기 전에 '이미 가진 것과 얼마나
같은가'를 먼저 보여 준다.

겹침은 두 단계다 (40단계 실측 겹침 v1):
- **실측** — 세이브로 보유자산명세(compst.py 수집분)의 **FICS 섹터 비중**으로
  잰다: 두 ETF 의 섹터별 비중에서 **겹치는 부분(min)의 합**. 전체 포트폴리오를
  덮지만 섹터 수준이라, 같은 섹터의 다른 종목도 겹침으로 본다.
  TOP10 실제 보유종목의 min-합은 **확인된 하한**으로 따로 낸다
  (세이브로 상세는 TOP10 이 전부다 — 화면에 그렇게 명시돼 있다).
- **분류 근사(폴백)** — 실측 자료가 없는 종목(미국 상장 포함)은 종전처럼
  지역·섹터·자산·전략 분류로 근사한다.

네트워크를 쓰지 않는다. 이미 표에 있는 값만으로 비교한다 —
국내 전용인 `NAV 비교표`(KRX 조회)와 달리 **국내·미국을 한 표에서** 견주려면
공통으로 가진 값만 써야 한다.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

MAX_PICK = 4        # 나란히 놓고 읽을 수 있는 한계

# ── 겹침 ────────────────────────────────────────────────
# 축마다 무게가 다르다. 같은 지수를 따라가면 그 자체로 사실상 같은 물건이다.
#
# **섹터에 무게를 크게 준 이유**: 같은 시장·같은 자산군이면 지역·자산·시장 축이
# 자동으로 다 맞아서 겹침의 바닥값이 높아진다. 처음에 섹터를 지역과 같은 무게로
# 뒀더니 `KODEX 200` 과 `KODEX 반도체` 가 70% 로 나왔다 — 둘은 노출이 꽤 다른데도
# '많이 겹침' 경고에 걸렸다. 섹터가 갈리면 그 사실이 점수에 제대로 반영돼야 한다.
OVERLAP_AXES = (("테마섹터", 5.0), ("투자지역", 2.5),
                ("자산분류", 1.5), ("전략", 1.0), ("시장분류", 0.5))
SAME_INDEX = 100.0      # 기초지수가 같으면 더 볼 것이 없다
OVERLAP_WARN = 80.0     # 이 위면 '갈아타도 분산이 안 는다'고 알린다


def _text(value) -> str:
    text = str(value or "").strip()
    return "" if text.lower() in {"nan", "none", "nat"} else text


# ── 실측 겹침 (세이브로 보유자산명세) ────────────────────
BASE = Path(__file__).resolve().parent.parent
RAW_DIR = BASE / "data" / "raw"

# FICS 섹터 비중은 **국내 주식 부분에만** 채워진다 (채권·해외주식형은 비거나 0 —
# 실수집에서 727/1,160 종목이 그랬다). 합이 이 밑이면 포트폴리오 대부분을 못 본
# 것이라 실측이라 부르지 않는다 → 분류 폴백. 0 을 그대로 쓰면 채권 ETF 둘이
# '실측 0% 겹침'으로 나온다 — 실제로는 많이 겹칠 수 있는데도.
MIN_SECTOR_COVER = 50.0


def _weights(path: Path, key_column: str, value_column: str,
             min_cover: float = 0.0) -> dict[str, dict[str, float]]:
    """CSV → {ISIN: {키: 비중}}. 파일이 없으면 빈 dict — 자연히 분류 폴백이 된다.

    비중 합이 min_cover 미만인 종목은 **넣지 않는다** (모르는 것을 0 으로 안 센다).
    """
    if not path.exists():
        return {}
    frame = pd.read_csv(path, dtype=str)
    frame["_w"] = pd.to_numeric(frame[value_column], errors="coerce")
    frame = frame.dropna(subset=["ISIN", key_column, "_w"])
    out: dict[str, dict[str, float]] = {}
    for isin, group in frame.groupby("ISIN"):
        if group["_w"].sum() >= min_cover:
            out[str(isin)] = dict(zip(group[key_column], group["_w"]))
    return out


def _pdf_weights(path: Path, min_cover: float) -> dict[str, dict[str, float]]:
    """KRX PDF(전체 구성종목) CSV → {ISIN: {구성종목ISIN: 비중}}.

    주식형은 '비중' 칸이 오지만 **채권형은 비중이 비고 평가금액만 온다** —
    그 경우 평가금액 비율로 비중을 만든다(전체 구성이라 합이 100이 된다).
    """
    if not path.exists():
        return {}
    frame = pd.read_csv(path, dtype=str)
    frame["_비중"] = pd.to_numeric(frame.get("비중"), errors="coerce")
    frame["_평가"] = pd.to_numeric(frame.get("평가금액"), errors="coerce")
    frame = frame.dropna(subset=["ISIN", "구성종목ISIN"])
    out: dict[str, dict[str, float]] = {}
    for isin, group in frame.groupby("ISIN"):
        weights = group["_비중"]
        if weights.isna().all() and group["_평가"].notna().any():
            total = group["_평가"].sum()
            weights = group["_평가"] / total * 100 if total > 0 else weights
        pairs = {code: float(w) for code, w
                 in zip(group["구성종목ISIN"], weights) if pd.notna(w)}
        if sum(pairs.values()) >= min_cover:
            out[str(isin)] = pairs
    return out


def load_compst(raw_dir: Path | None = None) -> tuple[dict, dict, dict]:
    """가장 최근 수집분의 (섹터 비중, TOP10 비중, 전체 PDF 비중). 없으면 빈 dict.

    PDF(KRX, 전체 구성)가 제일 정확하다 — 있으면 겹침이 그걸 쓰고,
    없으면 섹터(세이브로) → 분류 순으로 내려간다.
    """
    raw = raw_dir or RAW_DIR
    def latest(pattern: str) -> Path:
        files = sorted(raw.glob(pattern))
        return files[-1] if files else raw / "없음"
    sectors = _weights(latest("seibro_구성섹터_*.csv"), "섹터코드", "ETF비중",
                       min_cover=MIN_SECTOR_COVER)
    top10 = _weights(latest("seibro_구성TOP10_*.csv"), "구성종목코드", "비중")
    pdf = _pdf_weights(latest("krx_PDF_*.csv"), min_cover=MIN_SECTOR_COVER)
    return sectors, top10, pdf


def load_kis30(raw_dir: Path | None = None) -> dict[str, dict[str, float]]:
    """한투 [0245] TOP30 수집분(kis_top30.py) → {ISIN: {구성종목코드: 비중}}.

    KRX PDF 는 8/13 스냅샷에서 갱신이 막혔다(44단계-취소). 이 층은 상위 30개뿐
    이지만 **언제든 새로 받을 수 있어** PDF 다음 순위로 선다. 비중합 50% 미만은
    싣지 않는다(같은 가드) — 대형주 ETF 는 TOP30 이 80%대를 덮는다.

    **비중을 100% 로 정규화한다.** 원본 그대로 쓰면 TOP30 이 84% 만 덮는 탓에
    같은 지수를 따르는 두 ETF 도 84% 로 나와, PDF 로 잰 값(99.3)과 한 표에서
    비교가 안 된다. 실측 대조(PDF 가 있는 5쌍)에서 평균 오차가
    **4.1%p → 2.3%p** 로 줄었다(KODEX 200 ↔ TIGER 200: 84.4 → 99.9).
    남은 비중이 상위 30개와 비슷하게 퍼져 있다는 가정이라 근사치다.
    """
    raw = raw_dir or RAW_DIR
    files = sorted(raw.glob("kis_TOP30_*.csv"))
    if not files:
        return {}
    loaded = _weights(files[-1], "구성종목코드", "비중",
                      min_cover=MIN_SECTOR_COVER)
    out = {}
    for isin, weights in loaded.items():
        total = sum(weights.values())
        out[isin] = ({code: value / total * 100
                      for code, value in weights.items()}
                     if total > 0 else weights)
    return out


def _min_sum(a: dict[str, float], b: dict[str, float]) -> float:
    """두 비중표에서 겹치는 부분의 합 (0~100). 겹침의 표준 정의다."""
    return round(sum(min(a[k], b[k]) for k in a.keys() & b.keys()), 1)


def real_overlap(isin_a, isin_b, sectors: dict,
                 pdf: dict | None = None,
                 kis30: dict | None = None) -> float | None:
    """실측 겹침 — 한쪽이라도 자료가 없으면 None.

    사다리: **전체 PDF(진짜 보유종목) min-합** → **KIS TOP30 min-합** →
    섹터(FICS) min-합. 같은 층의 자료끼리만 견준다 — PDF 는 구성종목ISIN,
    KIS30 은 단축코드가 키라 층을 섞으면 겹침이 0 으로 왜곡된다.
    0 으로 채우지 않는다 — '안 겹친다'와 '모른다'가 섞이면 실측이라 부를 수 없다.
    """
    if pdf:
        a, b = pdf.get(_text(isin_a)), pdf.get(_text(isin_b))
        if a and b:
            return _min_sum(a, b)
    if kis30:
        a, b = kis30.get(_text(isin_a)), kis30.get(_text(isin_b))
        if a and b:
            return _min_sum(a, b)
    a, b = sectors.get(_text(isin_a)), sectors.get(_text(isin_b))
    if not a or not b:
        return None
    return _min_sum(a, b)


def top10_overlap(isin_a, isin_b, top10: dict) -> float | None:
    """TOP10 실제 보유종목 겹침 — **확인된 하한**이다 (11위 밖은 안 보인다)."""
    a, b = top10.get(_text(isin_a)), top10.get(_text(isin_b))
    if not a or not b:
        return None
    return _min_sum(a, b)


def overlap(left: pd.Series, right: pd.Series) -> float:
    """두 ETF 가 분류상 얼마나 겹치나 (0~100).

    기초지수명이 같으면 100. 그 외에는 축별로 값이 같은지 보고 무게를 더한다.
    한쪽이라도 값이 비어 있는 축은 **분모에서 뺀다** — 모르는 축을 '다르다'로
    세면 자료가 부실한 종목이 무조건 안 겹치는 것처럼 보인다.
    """
    index_left, index_right = _text(left.get("기초지수명")), _text(right.get("기초지수명"))
    if index_left and index_left == index_right:
        return SAME_INDEX

    total = matched = 0.0
    for column, weight in OVERLAP_AXES:
        a, b = _text(left.get(column)), _text(right.get(column))
        if not a or not b:
            continue
        total += weight
        if a == b:
            matched += weight
    if total == 0:
        return 0.0
    return round(matched / total * 100, 1)


def pair_overlap(left: pd.Series, right: pd.Series,
                 sectors: dict | None = None,
                 pdf: dict | None = None,
                 kis30: dict | None = None) -> float:
    """두 종목의 겹침 — **실측이 있으면 실측, 없으면 분류 근사**.

    같은 기초지수면 100 (더 볼 것이 없다). 실측은 real_overlap
    (PDF 전체 구성 → KIS TOP30 → 섹터 min-합 순).
    """
    index_left, index_right = _text(left.get("기초지수명")), _text(right.get("기초지수명"))
    if index_left and index_left == index_right:
        return SAME_INDEX
    if sectors or pdf or kis30:
        got = real_overlap(left.get("ISIN"), right.get("ISIN"),
                           sectors or {}, pdf, kis30)
        if got is not None:
            return got
    return overlap(left, right)


def overlap_with_holdings(candidates: pd.DataFrame, holdings: pd.DataFrame,
                          sectors: dict | None = None,
                          pdf: dict | None = None,
                          kis30: dict | None = None) -> pd.Series:
    """후보마다 **내 보유종목 중 가장 많이 겹치는 것**과의 겹침(%).

    평균이 아니라 최댓값을 쓴다 — 열 개 중 하나와 90% 겹치면 그것만으로
    '새로 살 이유'가 흔들린다. 평균을 내면 그 하나가 묻힌다.
    """
    if candidates.empty:
        return pd.Series(dtype="float64")
    if holdings.empty:
        return pd.Series([None] * len(candidates), index=candidates.index,
                         dtype="float64")
    values = []
    for _, row in candidates.iterrows():
        best = max((pair_overlap(row, mine, sectors, pdf, kis30)
                    for _, mine in holdings.iterrows()), default=0.0)
        values.append(best)
    return pd.Series(values, index=candidates.index, dtype="float64")


def best_match_name(row: pd.Series, holdings: pd.DataFrame,
                    sectors: dict | None = None,
                    pdf: dict | None = None,
                    kis30: dict | None = None) -> str:
    """겹침이 가장 큰 내 보유종목 이름 (어느 것과 겹치는지 알아야 판단이 된다)."""
    if holdings.empty:
        return ""
    scored = [(pair_overlap(row, mine, sectors, pdf, kis30),
               _text(mine.get("종목명")))
              for _, mine in holdings.iterrows()]
    scored.sort(key=lambda item: -item[0])
    return scored[0][1] if scored and scored[0][0] > 0 else ""


# ── 뷰 ──────────────────────────────────────────────────
# (표시 이름, 원본 칸, 단위, 클 수록 좋은가) — None 은 좋고 나쁨을 안 따진다
Metric = tuple[str, str, str, bool | None]

VIEWS: dict[str, tuple[Metric, ...]] = {
    "개요": (
        ("자산군", "자산분류", "", None),
        ("시장", "상장시장", "", None),
        ("주기", "분배주기", "", None),
        ("총보수", "총보수", "%", False),
        ("순자산(억)", "시가총액", "억", True),
        ("최근분배율(시장가)", "최근분배율_시장가", "%", True),
        ("연분배율", "연간분배율", "%", True),
        ("YTD", "YTD수익률", "%", True),
        ("1년", "1년수익률", "%", True),
    ),
    "비용·규모": (
        ("총보수", "총보수", "%", False),
        ("순자산(억)", "시가총액", "억", True),
        ("일평균거래대금", "일평균거래대금", "원", True),
        ("운용기간(년)", "운용기간_년", "", True),
        ("청산위험", "청산위험", "", None),
    ),
    "성과": (
        ("YTD", "YTD수익률", "%", True),
        ("1년", "1년수익률", "%", True),
        ("3년(연평균)", "3년평균수익률", "%", True),
        ("5년(연평균)", "5년평균수익률", "%", True),
        ("총수익(1년)", "_총수익1년", "%", True),
    ),
    "리스크": (
        ("베타", "베타", "", None),
        ("괴리율", "괴리율", "%", None),
        ("분배변동", "분배변동계수", "%", False),
        ("청산위험", "청산위험", "", None),
        ("PTP", "PTP", "", None),
    ),
    "배당": (
        ("주기", "분배주기", "", None),
        ("연분배율", "연간분배율", "%", True),
        ("평상분배율", "중앙값연분배율", "%", True),
        ("분배성장", "분배금성장률", "%", True),
        ("원금반환(ROC)", "누적_원금반환비율", "%", False),
        ("분배변동", "분배변동계수", "%", False),
    ),
}

# 억원으로 줄여 보여 줄 칸
IN_HUNDRED_MILLION = {"시가총액"}


def add_total_return(frame: pd.DataFrame) -> pd.DataFrame:
    """총수익(1년) = 1년 가격 수익률 + 연분배율. 비교표에서만 쓰는 파생 칸."""
    out = frame.copy()
    out["_총수익1년"] = (pd.to_numeric(out.get("1년수익률"), errors="coerce")
                         + pd.to_numeric(out.get("연간분배율"), errors="coerce"))
    return out


def side_by_side(picked: pd.DataFrame, view: str) -> pd.DataFrame:
    """지표를 행, 종목을 열로 돌려놓은 비교표 (사람이 읽는 방향)."""
    metrics = VIEWS[view]
    names = [_text(row.get("종목명")) or _text(row.get("종목코드"))
             for _, row in picked.iterrows()]
    table = {}
    for label, column, unit, _ in metrics:
        values = []
        for _, row in picked.iterrows():
            raw = row.get(column)
            if unit in {"%", "억", "원", ""} and unit != "":
                number = pd.to_numeric(pd.Series([raw]), errors="coerce").iloc[0]
                if column in IN_HUNDRED_MILLION and pd.notna(number):
                    number = number / 1e8
                values.append(number if pd.notna(number) else None)
            else:
                number = pd.to_numeric(pd.Series([raw]), errors="coerce").iloc[0]
                values.append(number if pd.notna(number) else (_text(raw) or None))
        table[label] = values
    return pd.DataFrame(table, index=names).T


def winners(picked: pd.DataFrame, view: str) -> dict[str, str]:
    """지표마다 가장 유리한 종목 이름. 좋고 나쁨을 안 따지는 지표는 뺀다."""
    frame = side_by_side(picked, view)
    best = {}
    for label, _, _, higher in VIEWS[view]:
        if higher is None or label not in frame.index:
            continue
        row = pd.to_numeric(frame.loc[label], errors="coerce").dropna()
        if row.empty:
            continue
        best[label] = str(row.idxmax() if higher else row.idxmin())
    return best
