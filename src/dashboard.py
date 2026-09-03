# -*- coding: utf-8 -*-
"""
ETF 분배·포트폴리오 대시보드 (Streamlit)
- 입력: output/분배ETF목록.xlsx (1단계) + output/분배캘린더.ics (2단계)  ※ 새 수집 없음
- 구성: 요약 지표 / 사이드바 필터 / 종목 목록 / 다가오는 일정 / NAV 침식 / NAV 비교표
- 실행: venv\\Scripts\\streamlit run src\\dashboard.py
- NAV 탭: KRX 종가·NAV·괴리율과 세이브로 이익분배를 결합해 기간별 침식 지표 표시
"""
from pathlib import Path
from datetime import date
from html import escape          # 종목명을 달력 HTML 에 넣기 전에 이스케이프
import sys

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

BASE = Path(__file__).resolve().parent.parent
# `streamlit run` 은 CWD가 아니라 이 파일이 있는 src 폴더를 sys.path 에 넣는다.
# 그래서 프로젝트 루트를 직접 넣어 줘야 아래 `from src.…` 임포트가 된다.
if str(BASE) not in sys.path:
    sys.path.insert(0, str(BASE))

from src.nav_erosion import (        # noqa: E402 - 위 경로 설정 뒤에 와야 한다
    calculate_nav_metrics,
    fetch_nav_history,
    filter_valid_isin_rows,
    isin_to_ticker,
    load_distributions_for_isin,
    public_nav_error_message,
)
from src import compare                # noqa: E402
from src import etf_returns            # noqa: E402
from src import investor_flow         # noqa: E402
from src import portfolio              # noqa: E402
from src import routine                # noqa: E402
from src import watch                  # noqa: E402
from src import whitelist              # noqa: E402
XLSX = BASE / "output" / "분배ETF목록.xlsx"
ICS = BASE / "output" / "분배캘린더.ics"
RAW_DIR = BASE / "data" / "raw"
NAV_CACHE_DIR = BASE / "data" / "nav"

NO_CYCLE = "미분배"      # 분배 이력이 아예 없는 종목 (classify.NO_CYCLE 과 같은 값)
# 연3회 = 두 해 연속 같은 3개 달(예: 4·7·10월)에만 지급 — 국내 지수형에 흔하다
CYCLE_ORDER = ["위클리", "월", "분기", "연3회", "반기", "연", NO_CYCLE]
# 화면에 짧게 보여줄 이름 (데이터 값은 '위클리' 그대로 두고 표시만 '주')
CYCLE_LABEL = {"위클리": "주", "월": "월", "분기": "분기", "연3회": "연3회",
               "반기": "반기", "연": "연", NO_CYCLE: NO_CYCLE}
PERIOD_MONTHS = {"3개월": 3, "6개월": 6, "1년": 12}
# 분배주기별 색 — **순서를 고정**해 두어서 필터로 종목이 줄어도 같은 주기는
# 같은 색을 유지한다. light 는 종이 톤(38단계 디자인 기준)에 맞춰 눌러 놓은
# 색인데, **색 계열은 종전 그대로**다(주=파랑, 월=주홍, 분기=초록…) — 색을
# 기억하는 사용자가 헤매지 않게 계열은 두고 채도만 종이에 맞췄다.
CYCLE_COLORS = {
    "light": {"주": "#2c4a73", "월": "#a14b3a", "분기": "#5f7d4f", "연3회": "#7b5b8f",
              "반기": "#b8893a", "연": "#c0748f", NO_CYCLE: "#8a8175"},
    "dark": {"주": "#3987e5", "월": "#d95926", "분기": "#199e70", "연3회": "#9a78e0",
             "반기": "#c98500", "연": "#d55181", NO_CYCLE: "#898781"},
}
# 일정 종류(매수마감·배당락) 색 — 위 팔레트의 1·2번을 그대로 쓴다
KIND_COLORS = {"light": {"매수": "#2c4a73", "배당": "#a14b3a"},
               "dark": {"매수": "#3987e5", "배당": "#d95926"}}

# 색을 따로 정하지 않은 차트(px.bar·px.pie 등)의 기본 색 순서 — 종이 톤 팔레트.
# 명시적으로 색을 준 차트(CYCLE_COLORS 등)에는 영향이 없다.
px.defaults.color_discrete_sequence = ["#2c4a73", "#a14b3a", "#5f7d4f", "#b8893a",
                                       "#7b5b8f", "#c0748f", "#8a8175"]
# NAV 비교표에서 '무엇부터' 조회할지 — (정렬 컬럼, 오름차순 여부, 설명)
COMPARE_ORDER = {
    "시총 큰 순": ("시가총액", False,
                  "시가총액이 큰 순서. 거래가 활발하고 오래된 대표 ETF부터 봅니다."),
    "시총 작은 순": ("시가총액", True,
                   "시가총액이 작은 순서. 소형·신규 ETF부터 봅니다(청산 위험 점검용)."),
    "분배율 높은 순": ("최근_분배율", False,
                    "직전 회차 분배율(1회 분배금 ÷ 기준가, %)이 높은 순서. "
                    "연 환산이 아니라 **직전 1회분**이라 주기가 다르면 그대로 비교되지 않습니다."),
    "총보수 낮은 순": ("총보수", True,
                    "KRX 공시 연 총보수(%)가 낮은 순서. 장기 보유 비용이 적은 쪽부터."),
    "지급 횟수 많은 순": ("지급횟수_12개월", False,
                      "최근 12개월 분배금 지급 횟수가 많은 순서(월 12회 > 분기 4회)."),
    "YTD 수익률 높은 순": ("YTD수익률", False,
                       "올해 첫 개장일 대비 가격 변화율(%)이 높은 순서. **분배금 제외**."),
    "1년 수익률 높은 순": ("1년수익률", False,
                       "1년 전 대비 가격 변화율(%)이 높은 순서. **분배금 제외**."),
    "종목명 순": (None, None,
                "표에 보이는 순서 그대로(주기 → 종목명 가나다순). 특별한 기준 없음."),
}

비고_설명 = (
    "**비고 '주기 확인'이 뜨는 이유** — 확인할 항목은 **분배주기 한 가지**입니다.\n\n"
    "분배주기를 두 가지 방법으로 판정하는데, 그 결과가 서로 다른 종목에 표시됩니다.\n"
    "- **횟수 기준(주 판정)**: 최근 12개월 동안 분배금을 몇 번 줬는지\n"
    "- **간격 기준(교차검증)**: 분배와 분배 사이가 평균 며칠인지\n\n"
    "예를 들어 12개월간 5회만 줘서 횟수로는 '분기'인데 간격은 31일이라 '월'이 나오는 식입니다.\n\n"
    "**상장일로 원인을 갈라 보면 셋입니다** (전에는 '대부분 신규상장'이라고 적어 뒀는데 "
    "실제로 세어 보니 틀렸습니다):\n"
    "- **신규상장** — 12개월치가 아직 안 찼습니다. 횟수는 **적게 나올 수만 있으므로**, "
    "간격 기준이 더 잦으면 그쪽이 맞습니다. **이런 종목은 주기를 자동으로 바로잡았습니다.**\n"
    "- **실제 불규칙** — 상장 10년짜리도 있습니다. 신규상장 탓이 아닙니다. "
    "국내 지수형에 흔한 **4·7·10월 연 3회** 지급은 두 해 연속 같은 달이 확인되면 "
    "**'연3회' 주기로 따로 분류**했고, 여기 남은 것은 그 확인이 안 된 종목입니다.\n"
    "- **상장일 없음** — 대부분 미국 종목입니다. 모르는 것을 신규상장으로 몰지 않았습니다.\n\n"
    "'실제 불규칙'·'상장일 없음' 종목은 캘린더의 예상 날짜가 어긋날 수 있으니 "
    "운용사 공시로 확인하세요."
)


def theme_mode() -> str:
    """지금 화면이 어두운 테마인지. 못 알아내면 밝은 테마로 본다."""
    try:
        return "dark" if st.context.theme.type == "dark" else "light"
    except Exception:
        return "light"


def ymd(value) -> str:
    """날짜를 짧게: 2026-08-14 → 26-8-14 (값이 없으면 빈 칸)"""
    if value is None or pd.isna(value):
        return ""
    return f"{value.year % 100:02d}-{value.month}-{value.day}"


def ptp_label(state, expiry) -> pd.Series:
    """PTP 칸 글자. 면제는 만료일까지 같이 보여 준다 — 면제는 영구가 아니다."""
    if state is None:
        return pd.Series(dtype=str)
    state = state.fillna("").astype(str).str.strip()
    expiry = (expiry.fillna("").astype(str).str.strip()
              if expiry is not None else pd.Series("", index=state.index))
    return pd.Series([
        "⚠ 대상" if s == "대상" else (f"면제~{e[2:]}" if s == "면제" else "")
        for s, e in zip(state, expiry)
    ], index=state.index)


def multiple_label(values) -> pd.Series:
    """배수 칸 글자 — `1.0` → `1배`, `2.0` → `2배`, `-1.0` → `-1배`.

    **빈칸은 '자료 없음' 하나만 뜻한다**(미국 상장분과 메타가 없는 국내 31종목).
    전에는 1배도 비웠는데, 그러면 '평범한 1배'와 '모름'이 같은 빈칸이 돼서
    구분이 안 됐다.
    """
    if values is None:
        return pd.Series(dtype=str)
    numbers = pd.to_numeric(values, errors="coerce")
    return pd.Series([
        "" if pd.isna(v) else
        (f"{v:+.0f}배" if v < 0 else f"{v:.0f}배") if float(v).is_integer()
        else f"{v:+.1f}배" if v < 0 else f"{v:.1f}배"
        for v in numbers
    ], index=numbers.index)


# ── 데이터 로드 ────────────────────────────────────────────
@st.cache_data
def load_data() -> pd.DataFrame:
    df = pd.read_excel(XLSX, sheet_name="전체")
    for col in ("최근_지급기준일", "최근_실지급일", "최근_매수마감일"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    for col in ("비고", "비고상세", "비고사유"):   # 예전 산출물에는 없을 수 있음
        df[col] = df[col].fillna("") if col in df.columns else ""
    for col in ("최근_분배율", "시가총액", "총보수"):
        if col not in df.columns:
            df[col] = pd.NA
    # 6단계에서 추가된 분류 칸 — 옛 산출물에는 없을 수 있다
    for col in ("자산분류", "시장분류", "투자지역", "테마섹터", "전략",
                "운용방식", "과세유형", "기초지수명"):
        df[col] = df[col].fillna("") if col in df.columns else ""
    # 화이트리스트 · 보유 여부. 파일이 없으면 전부 False 라 그 필터가 안 뜬다
    marked = watch.mark(portfolio.mark_holdings(whitelist.mark(attach_returns(df))))
    # 루틴 판정은 **1년수익률·ROC 가 붙은 뒤**라야 한다
    return routine.score_frame(attach_roc(marked))


def _ticker_or_blank(isin) -> str:
    """ISIN → KRX 6자리 코드. 변환 안 되는 행(만기·해외물)은 빈 문자열."""
    try:
        return isin_to_ticker(isin)
    except (ValueError, TypeError):
        return ""


@st.cache_data
def load_returns() -> pd.DataFrame:
    """data/raw 의 최근 KRX 수익률 CSV. 없으면 빈 표(수익률 칸만 빈다)."""
    return etf_returns.load_latest()


def attach_returns(df: pd.DataFrame) -> pd.DataFrame:
    """YTD·1년 수익률을 KRX코드로 붙인다. 파일이 없어도 죽지 않는다."""
    df = df.copy()
    # 목록에 '종목코드'가 있으면 그걸 쓴다(미국은 티커라 ISIN 변환이 안 된다).
    # 옛 산출물처럼 없을 때만 ISIN 을 6자리 코드로 바꾼다.
    if "종목코드" in df.columns:
        df["KRX코드"] = df["종목코드"].fillna("").astype(str).str.strip()
    else:
        df["KRX코드"] = df["ISIN"].map(_ticker_or_blank) if "ISIN" in df.columns else ""
    returns = load_returns()
    if returns.empty:
        df["YTD수익률"] = pd.NA
        df["1년수익률"] = pd.NA
    else:
        df = df.merge(returns[["KRX코드", "YTD수익률", "1년수익률"]],
                      on="KRX코드", how="left")
    return attach_us_returns(df)


@st.cache_data
def load_us_returns() -> pd.DataFrame:
    """미국 ETF 수익률 — us_etf 수집분에서 티커·YTD·1년만."""
    from src import us_etf

    us = us_etf.load_latest()
    if us.empty or "YTD수익률" not in us.columns:
        return pd.DataFrame(columns=["KRX코드", "YTD수익률", "1년수익률"])
    return pd.DataFrame({
        "KRX코드": us["티커"].astype(str).str.strip(),
        "YTD수익률": pd.to_numeric(us["YTD수익률"], errors="coerce"),
        "1년수익률": pd.to_numeric(us["1년수익률"], errors="coerce"),
    })


def attach_us_returns(df: pd.DataFrame) -> pd.DataFrame:
    """KRX 수익률이 없는 미국 종목 칸을 야후 계산값으로 채운다."""
    us = load_us_returns()
    if us.empty:
        return df
    lookup = us.drop_duplicates("KRX코드").set_index("KRX코드")
    for column in ("YTD수익률", "1년수익률"):
        filled = df["KRX코드"].map(lookup[column])
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(filled)
    return df


@st.cache_data
def load_roc() -> pd.DataFrame:
    """미국 19a-1 공시(원금반환 비율). 파일이 없으면 빈 표."""
    from src import roc as roc_module

    return roc_module.load_latest()


@st.cache_data
def load_long_shares() -> pd.DataFrame:
    """분배 이력에서 되짚은 **2년치 상장주식수** (추가 수집 없음).

    날 단위 캐시는 쌓기 시작한 날부터라 60일뿐이다. 그 앞 구간을 이걸로 메운다.
    """
    from src.flow import long_shares

    return long_shares.load()


def attach_roc(df: pd.DataFrame) -> pd.DataFrame:
    """ROC 비율을 티커로 붙인다. 받아 온 발행사만 채워지고 나머지는 빈칸이다."""
    df = df.copy()
    table = load_roc()
    columns = ["누적_원금반환비율", "원금반환비율", "공시일"]
    if table.empty or "티커" not in table.columns:
        for column in columns:
            df[column] = pd.NA
        return df
    hit = table[["티커", *[c for c in columns if c in table.columns]]].copy()
    hit = hit.rename(columns={"티커": "KRX코드"}).drop_duplicates("KRX코드")
    merged = df.merge(hit, on="KRX코드", how="left")
    for column in columns:
        if column not in merged.columns:
            merged[column] = pd.NA
    return merged


def returns_asof() -> str:
    """수익률 기준일(YYYYMMDD). 파일이 없으면 빈 문자열."""
    returns = load_returns()
    return "" if returns.empty else str(returns["기준일자"].iloc[0])


@st.cache_data
def load_calendar() -> pd.DataFrame:
    """분배캘린더.ics 를 읽어 이벤트 표로 변환 (icalendar 사용)."""
    if not ICS.exists():
        return pd.DataFrame(columns=["날짜", "종류", "종목명", "설명"])
    from icalendar import Calendar
    cal = Calendar.from_ical(ICS.read_bytes())
    rows = []
    for ev in cal.walk("VEVENT"):
        summary = str(ev.get("SUMMARY", ""))
        # "[매수마감] 종목명" / "[배당락] 종목명" 분리
        kind, name = "", summary
        if summary.startswith("[") and "]" in summary:
            kind = summary[1:summary.index("]")]
            name = summary[summary.index("]") + 1:].strip()
        desc = str(ev.get("DESCRIPTION", ""))
        rows.append({
            "날짜": ev.get("DTSTART").dt,     # date 객체
            "종류": "매수" if kind == "매수마감" else "배당",   # 표에는 두 글자로만
            "종목명": name,
            "설명": desc,
            **_split_description(desc),       # 긴 설명을 짧은 칸으로 쪼갠다
        })
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("날짜").reset_index(drop=True)
    return out


def _split_description(desc: str) -> dict:
    """'주기: 월 | 직전 분배율: 0.21% | 예상 기준일: 26-8-31 | …' 을 칸별로 나눈다.
    가로 스크롤이 생기지 않게 표에는 긴 설명 대신 이 값들을 쓴다."""
    parts = {}
    for chunk in desc.split("|"):
        if ":" in chunk:
            key, _, value = chunk.partition(":")
            parts[key.strip()] = value.strip()
    return {
        "주기": parts.get("주기", ""),
        "분배율": pd.to_numeric(parts.get("직전 분배율", "").rstrip("%"), errors="coerce"),
        "예상기준일": parts.get("예상 기준일", ""),
    }


# ── 화면 조각들 ────────────────────────────────────────────
# 막대 두께 — '칸 하나에서 차지하는 비율'. 두 차트에 같은 값을 쓴다.
BAR_WIDTH = 0.6


MIN_CHART_SLOTS = 2       # 이보다 좁으면 제목·축 글자가 뭉갠다


def chart_column_ratio(left_slots: int, right_slots: int) -> list[int]:
    """두 차트의 **가로 폭을 칸 수에 비례**시킨다.

    폭을 반반으로 두면 칸이 적은 쪽(국내/해외 2칸)은 한 칸이 넓어져 막대가
    두껍고 서로 멀찍이 떨어진다. 막대 폭만 줄이면 두께는 맞아도 간격이 남는다.
    **폭 자체를 칸 수에 맞추면** 한 칸의 픽셀이 같아져 두께·간격이 다 맞는다.
    """
    left = max(int(left_slots), MIN_CHART_SLOTS)
    right = max(int(right_slots), MIN_CHART_SLOTS)
    return [left, right]


def render_kpis(view: pd.DataFrame, total: int) -> tuple[list[str], list[str]]:
    """필터 결과 기준 요약. total은 필터 전 전체 종목 수.

    돌려주는 값 = 차트에서 고른 (분배주기, 국내/해외). **탭을 좁히는 데 쓴다** —
    차트 자체는 늘 사이드바 필터 결과로 그린다(고른 칸만 남으면 되돌릴 수 없다).
    """
    c1, c2, c3 = st.columns(3)
    c1.metric("종목 수", f"{len(view):,}개", help=f"필터 전 전체 {total:,}개")
    c2.metric("월·주 분배", f"{view['분배주기'].isin(['월', '위클리']).sum():,}개")
    c3.metric("비고(주기 확인)", f"{(view['비고'] != '').sum():,}개")
    st.caption(f"아래 지표·차트·표는 모두 **현재 필터 결과({len(view):,}개)** 기준입니다. "
               f"필터 전 전체는 {total:,}개.")

    if view.empty:
        st.info("필터 조건에 맞는 종목이 없습니다. 사이드바에서 조건을 넓혀 보세요.")
        return [], []

    # 표를 먼저 만든다 — 칸 수를 알아야 두 차트의 **가로 폭**을 정할 수 있다
    cyc = (view["분배주기"].value_counts()
           .reindex(CYCLE_ORDER).fillna(0).astype(int)
           .rename_axis("분배주기").reset_index(name="종목수"))
    cyc["분배주기"] = cyc["분배주기"].map(CYCLE_LABEL)       # 위클리 → 주
    dom = view["국내해외"].value_counts().rename_axis("구분").reset_index(name="종목수")

    col_a, col_b = st.columns(chart_column_ratio(len(cyc), len(dom)))
    with col_a:
        fig = px.bar(cyc, x="분배주기", y="종목수", text="종목수",
                     title="분배주기별 종목 수")
        fig.update_traces(textposition="outside", width=BAR_WIDTH)
        # `clickmode` 를 안 주면 막대를 눌러도 **선택 이벤트가 안 난다**
        # (plotly 기본은 클릭이 확대/이동용이다). 실제 브라우저에서 확인했다.
        fig.update_layout(clickmode="event+select")
        cycle_pick = st.plotly_chart(fig, width="stretch", key="kpi_cycle",
                                     on_select="rerun",
                                     selection_mode=("points",))
    with col_b:
        # **도넛이 아니라 막대다.** plotly 파이는 클릭해도 선택 이벤트가 안 나서
        # (실제 브라우저에서 확인) 누를 수가 없었다. 같은 색을 그대로 쓴다.
        # 폭을 칸 수에 맞췄으니 막대 폭은 왼쪽과 **같은 값**이면 된다.
        fig2 = px.bar(dom, x="구분", y="종목수", text="종목수", title="국내 / 해외",
                      color="구분",
                      color_discrete_map={"해외": "#2c4a73", "국내": "#b8893a"})
        fig2.update_traces(textposition="outside", width=BAR_WIDTH)
        fig2.update_layout(clickmode="event+select", showlegend=False)
        dom_pick = st.plotly_chart(fig2, width="stretch", key="kpi_domestic",
                                   on_select="rerun",
                                   selection_mode=("points",))

    cycles, domestics = chart_picks(cycle_pick, dom_pick)
    if cycles or domestics:
        parts = []
        if cycles:
            # 배너는 **화면 이름**으로 (데이터 값 '위클리'가 아니라 '주')
            parts.append("분배주기 **"
                         + "·".join(CYCLE_LABEL.get(c, c) for c in cycles) + "**")
        if domestics:
            parts.append("국내/해외 **" + "·".join(domestics) + "**")
        left, right = st.columns([5, 1])
        left.success("🔎 " + " + ".join(parts) + " 로 좁혔습니다 — "
                     "**아래 탭 전체**가 이 기준으로 나옵니다.")
        if right.button("선택 해제", key="clear_chart_pick"):
            for key in ("kpi_cycle", "kpi_domestic"):
                st.session_state.pop(key, None)
            st.rerun()
    else:
        st.caption("💡 막대를 **누르면** 아래 탭 전체가 그 기준으로 좁혀집니다 "
                   "(둘 다 고르면 겹치는 것만). 같은 칸을 다시 누르면 풀립니다.")
    return cycles, domestics


def _dig(source, key):
    """dict 든 객체든 같은 방식으로 꺼낸다.

    Streamlit 이 주는 선택 이벤트는 `AttributeDictionary` 라 **dict 가 아닐 수
    있다** — `isinstance(x, dict)` 로 거르면 항상 빈 값이 나온다.
    """
    if source is None:
        return None
    try:
        return source[key]
    except (TypeError, KeyError, IndexError):
        return getattr(source, key, None)


def chart_selection_labels(event) -> list[str]:
    """plotly 선택 이벤트에서 고른 칸의 이름만 뽑는다.

    막대는 x 축 값(`x`), 파이 조각은 이름(`label`)에 들어 있다 — 차트 종류마다
    자리가 달라서 둘 다 본다. 아무것도 안 고르면 빈 목록.
    """
    points = _dig(_dig(event, "selection"), "points") or []
    labels = []
    for point in points:
        value = _dig(point, "label")
        if value is None:
            value = _dig(point, "x")
        if value is not None and str(value).strip():
            labels.append(str(value).strip())
    return list(dict.fromkeys(labels))          # 중복 제거, 고른 순서 유지


def chart_picks(cycle_pick, dom_pick) -> tuple[list[str], list[str]]:
    """두 차트에서 고른 값. 분배주기는 **데이터 값으로 되돌려** 준다.

    차트는 짧은 이름(`주`)으로 그리는데 데이터는 `위클리` 다 — 안 되돌리면
    걸러도 0개가 나온다.
    """
    back = {label: value for value, label in CYCLE_LABEL.items()}
    cycles = [back.get(c, c) for c in chart_selection_labels(cycle_pick)]
    return cycles, chart_selection_labels(dom_pick)


def apply_chart_picks(view: pd.DataFrame, cycles: list[str],
                      domestics: list[str]) -> pd.DataFrame:
    """차트에서 고른 기준으로 좁힌다 — **탭에 들어갈 표에만** 건다.

    차트 자체는 늘 사이드바 필터 결과로 그린다. 고른 칸만 남게 그리면
    다른 칸이 사라져 되돌릴 수 없다.
    """
    out = view
    if cycles:
        out = out[out["분배주기"].isin(cycles)]
    if domestics:
        out = out[out["국내해외"].isin(domestics)]
    return out


# 아무것도 안 고르면 '전체'로 두는 필터들 (고른 게 있을 때만 좁힌다)
OPTIONAL_FILTERS = [
    ("테마·섹터", "테마섹터", "기초지수명으로 **추정**한 값입니다. 규칙에 안 걸리면 '기타'."),
    ("자산분류", "자산분류", "KRX 값 그대로 (주식·채권·원자재·부동산·통화·혼합)"),
    ("전략", "전략", "기초지수명 기준. 레버리지·인버스는 펀드 구조라 지수명에 안 나올 수 있습니다."),
    ("과세유형", "과세유형", "KRX 값 그대로. 비과세 / 배당소득세 / 해외주식투자전용 등"),
    ("운용사", "운용사", None),
]


# 화이트리스트 · 보유종목 — 서로 **따로** 고르는 범위 선택.
# (컬럼, 라벨, '~만' 라벨, '~제외' 라벨, 도움말)
SCOPE_FILTERS = [
    ("화이트리스트", "화이트리스트", "화이트리스트만", "화이트리스트 제외",
     "회사 계좌로 매매 가능한 목록 (data/reference/whitelist.csv)"),
    ("보유", "내 보유종목", "보유종목만", "보유종목 제외",
     "지금 들고 있는 종목 (data/portfolio/보유종목.csv)"),
    ("관심", "관심종목", "관심종목만", "관심종목 제외",
     "수급·관심종목 탭에서 관리하는 목록 (data/reference/관심종목.csv). "
     "'관심종목만' 을 고르면 📅 다가오는 일정 탭이 관심종목 배당 캘린더가 됩니다"),
]


def pick_scopes(df: pd.DataFrame) -> list[tuple[str, str, str, str]]:
    """사이드바 맨 위에 범위 선택을 그리고 고른 값만 돌려준다.

    실제 걸러내기는 다른 필터를 다 그린 뒤 apply_scopes 에서 한다.
    자료가 아예 없는 항목(파일이 없거나 하나도 안 맞물림)은 아예 안 보여준다.
    """
    chosen = []
    for column, label, only, without, help_text in SCOPE_FILTERS:
        if column not in df.columns or not df[column].any():
            continue
        count = int(df[column].sum())
        pick = st.sidebar.radio(label, ["전체", only, without], key=f"scope_{column}",
                                help=f"{help_text}. 현재 {count}개가 맞물립니다")
        chosen.append((column, pick, only, without))
    if chosen:
        st.sidebar.caption("둘 다 고르면 **겹치는 것만** 남습니다 "
                           "(예: 화이트리스트만 + 보유종목 제외 = 아직 안 산 매매가능 종목).")
        st.sidebar.divider()
    return chosen


def apply_scopes(out: pd.DataFrame,
                 chosen: list[tuple[str, str, str, str]]) -> pd.DataFrame:
    for column, pick, only, without in chosen:
        if pick == only:
            out = out[out[column]]
        elif pick == without:
            out = out[~out[column]]
    return out


ALL_ACCOUNTS = "전체 (계좌 안 가림)"


def pick_markets(df: pd.DataFrame) -> list[str] | None:
    """상장시장(한국/미국) 필터 + '이 계좌로 살 수 있는 것만' 고르기."""
    if "상장시장" not in df.columns:
        return None
    counts = df["상장시장"].astype(str).str.strip().value_counts()
    options = [m for m in ("한국", "미국") if m in counts.index]
    options += [m for m in counts.index if m and m not in options]
    if len(options) < 2:
        return None

    # 연금·ISA 계좌로는 미국 직접 상장 ETF 를 못 산다 → 계좌를 고르면 그만큼 좁힌다
    account = st.sidebar.selectbox(
        "어느 계좌로 살 건가요?", [ALL_ACCOUNTS] + list(portfolio.ACCOUNT_TAX),
        key="filter_계좌유형",
        help="**연금저축·IRP/DC·ISA 에서는 미국 직접 상장 ETF를 못 삽니다.** "
             "그 계좌를 고르면 미국 종목은 목록에서 빠집니다.")
    if account in portfolio.US_BLOCKED_ACCOUNTS:
        st.sidebar.caption(f"↳ **{account}** 은(는) 국내 상장분만 살 수 있어 "
                           f"미국 {counts.get('미국', 0):,}개를 뺐습니다.")
        return ["한국"]

    chosen = st.sidebar.multiselect(
        "상장시장", options, default=options,
        format_func=lambda m: f"{m} ({counts.get(m, 0)})",
        key="filter_상장시장",
        help="**어느 거래소에 상장**됐는지입니다. 아래 '투자지역'(무엇에 투자하는지)과 다릅니다. "
             "미국 상장 종목의 금액은 **원화로 환산**해 둔 값입니다.")
    return list(chosen) if chosen else None


# 처음 화면에 켜 둘 투자지역. 나머지(중국·일본·인도…)는 직접 고르면 나온다
DEFAULT_REGIONS = ["한국", "미국"]


def region_options(df: pd.DataFrame) -> list[str]:
    """투자지역 선택지 — 한국·미국을 앞에 두고 나머지는 종목 많은 순."""
    if "투자지역" not in df.columns:
        return []
    counts = df["투자지역"].astype(str).str.strip().value_counts()
    counts = counts[counts.index != ""]
    head = [r for r in DEFAULT_REGIONS if r in counts.index]
    return head + [r for r in counts.index if r not in head]


def pick_regions(df: pd.DataFrame) -> list[str] | None:
    """투자지역 필터. 고른 게 없으면 None(=전체)."""
    options = region_options(df)
    if not options:
        return None
    counts = df["투자지역"].astype(str).str.strip().value_counts()
    chosen = st.sidebar.multiselect(
        "투자지역", options,
        default=[r for r in DEFAULT_REGIONS if r in options],
        format_func=lambda r: f"{r} ({counts.get(r, 0)})",
        key="filter_투자지역",
        help="처음에는 **한국·미국만** 켜 둡니다. 다 지우면 전체가 나옵니다. "
             "기초지수명으로 **추정**한 값이라 KRX 공식 필드가 아닙니다.")
    return list(chosen) if chosen else None


def routine_filters(out: pd.DataFrame, total: int) -> pd.DataFrame:
    """매수 전 5분 루틴으로 후보군을 줄인다. 고른 항목을 **모두** 지나야 남는다."""
    if "루틴점수" not in out.columns:
        return out
    with st.sidebar.expander("✅ 매수 전 5분 루틴으로 줄이기"):
        st.caption("체크할수록 후보가 줄어듭니다. **자료가 없는 종목도 빠집니다** — "
                   "모르는 것을 통과로 치면 걸러야 할 종목이 그대로 남습니다.")
        chosen = []
        for label, _, help_text in routine.FILTERS:
            if st.checkbox(label, key=f"routine_{label}", help=help_text):
                chosen.append(label)
        drop_cc = st.checkbox(
            "커버드콜 빼기", key="routine_cc",
            help="커버드콜은 지수가 올라도 위쪽 수익이 잘립니다. 나쁘다는 뜻이 아니라 "
                 "**천장을 직접 확인해야** 하는 종목이라, 그럴 시간이 없으면 빼고 봅니다")
        before = len(out)
        out = routine.apply_filters(out, chosen, drop_cc)
        if chosen or drop_cc:
            st.success(f"**{total:,} → {len(out):,}개** "
                       f"(이 단계에서 {before - len(out):,}개 제외)")
    return out


def sidebar_filters(df: pd.DataFrame) -> pd.DataFrame:
    st.sidebar.header("필터")
    st.sidebar.caption("고르면 화면 전체가 바로 다시 그려집니다. "
                       "아래 항목은 **아무것도 안 고르면 전체**입니다.")
    scopes = pick_scopes(df)          # 위젯은 맨 위에, 걸러내기는 맨 아래에서
    markets = pick_markets(df)        # 어느 거래소에 상장된 종목인지
    regions = pick_regions(df)        # 첫 필터는 투자지역 — 기본 한국·미국
    have = set(df["분배주기"].unique())
    counts = df["분배주기"].value_counts()
    cycles = st.sidebar.multiselect(
        "분배주기", [c for c in CYCLE_ORDER if c in have],
        # '미분배'(분배 이력 없는 상장 ETF)는 처음에는 꺼 둔다 — 켜면 후보로 같이 본다
        default=[c for c in CYCLE_ORDER if c in have and c != NO_CYCLE],
        format_func=lambda c: f"{CYCLE_LABEL.get(c, c)} ({counts.get(c, 0)})",
        help="**미분배**는 상장은 돼 있는데 세이브로에 분배 이력이 없는 종목입니다. "
             "신규 상장이라 아직 안 준 것과 원래 분배를 안 하는 것이 섞여 있습니다.")
    dom_opts = list(df["국내해외"].dropna().unique())
    doms = st.sidebar.multiselect("국내/해외", dom_opts, default=dom_opts)

    out = df[df["분배주기"].isin(cycles) & df["국내해외"].isin(doms)]
    if markets:
        out = out[out["상장시장"].astype(str).str.strip().isin(markets)]
    if regions:
        out = out[out["투자지역"].astype(str).str.strip().isin(regions)]
    for label, column, help_text in OPTIONAL_FILTERS:
        if column not in df.columns:
            continue
        options = sorted(str(v) for v in df[column].dropna().unique() if str(v).strip())
        if not options:
            continue
        chosen = st.sidebar.multiselect(label, options, help=help_text,
                                        key=f"filter_{column}")
        if chosen:
            out = out[out[column].astype(str).isin(chosen)]

    out = apply_scopes(out, scopes)      # 맨 위에서 고른 화이트리스트·보유 범위

    out = routine_filters(out, len(df))

    keyword = st.sidebar.text_input("종목명 검색")
    only_flag = st.sidebar.checkbox("비고(주기 확인)만 보기")
    if keyword:
        out = out[out["종목명"].str.contains(keyword, case=False, na=False, regex=False)]
    if only_flag:
        out = out[out["비고"] != ""]

    if "투자지역" in df.columns:
        st.sidebar.caption("ℹ **투자지역·테마섹터·전략은 기초지수명으로 추정한 값**입니다. "
                           "KRX에 해당 필드가 없어 규칙으로 뽑았고, 못 뽑은 건 '기타'입니다.")
    return out


# 종목명은 항상 맨 앞에 고정으로 두고, 나머지만 켜고 끈다
NO_SCROLL_LIMIT = 9      # 종목명 포함 이 정도까지가 가로 스크롤 없이 들어간다


def pick_columns(key: str, options: list[str], default: list[str],
                 fixed: list[str] | None = None) -> list[str]:
    """표에 보일 칸을 고르게 한다. 기본은 가로 스크롤이 안 생기는 만큼만.

    fixed: 종목명 바로 뒤에 **항상** 붙는 칸(예: 코드). 끄고 켜지 못한다.
    """
    fixed = fixed or []
    head = ["종목명"] + fixed
    with st.expander(f"표시할 항목 고르기 (기본 {len(default) + len(head)}칸)"):
        chosen = st.pills("표시할 항목", options, selection_mode="multi",
                          default=default, key=key, label_visibility="collapsed")
        st.caption(f"**{' · '.join(head)}**은(는) 항상 표시됩니다. "
                   f"이 칸들까지 합쳐 **{NO_SCROLL_LIMIT}칸 정도**면 "
                   "보통 화면에서 가로 스크롤 없이 들어갑니다. "
                   "더 켜면 표를 옆으로 밀어서 봐야 할 수 있습니다.")
    chosen = list(chosen) if chosen else []
    picked = head + [c for c in options if c in chosen]
    if len(picked) > NO_SCROLL_LIMIT:
        st.caption(f"⚠ 지금 {len(picked)}칸입니다. 가로 스크롤이 생기면 위에서 몇 개 꺼 보세요.")
    return picked


def picked_row_index(event) -> int | None:
    """표에서 고른 행 번호. 체크박스(rows)든 셀 클릭(cells)이든 받는다."""
    rows = list(getattr(event.selection, "rows", []) or [])
    if rows:
        return int(rows[0])
    cells = list(getattr(event.selection, "cells", []) or [])
    if cells:
        return int(cells[0][0])       # (행 번호, 컬럼명)
    return None


def render_table(view: pd.DataFrame) -> None:
    st.subheader(f"종목 목록 ({len(view):,}개)")
    if view.empty:
        st.write("표시할 종목이 없습니다.")
        return

    # 가로 스크롤이 생기지 않게 컬럼명을 짧게 바꾸고 날짜도 26-8-14 형식으로
    # 국내/해외는 사이드바 필터에 있어 표에서는 뺐다(가로 폭 확보)
    show = pd.DataFrame({
        "종목명": view["종목명"],
        # 증권사 앱에서 찾을 때 쓰는 값 — 국내는 6자리, 미국은 티커
        "코드": view["KRX코드"] if "KRX코드" in view.columns else "",
        "운용사": view["운용사"],
        "지역": view["투자지역"],
        "섹터": view["테마섹터"],
        "전략": view["전략"],
        # 레버리지·인버스 배수 (2배 / -1배 …). 국내만 채워진다 — 아래 설명 참조
        "배수": multiple_label(view.get("배수")),
        "자산": view["자산분류"],
        "과세": view["과세유형"],
        "시총(억)": (pd.to_numeric(view["시가총액"], errors="coerce") / 1e8).round(0),
        "총보수": pd.to_numeric(view["총보수"], errors="coerce"),
        "시장": view["상장시장"] if "상장시장" in view.columns else "",
        "주기": view["분배주기"].map(CYCLE_LABEL),
        "횟수": view["지급횟수_12개월"],
        "분배금": pd.to_numeric(view["최근_주당분배금"], errors="coerce"),
        "분배율": pd.to_numeric(view["최근_분배율"], errors="coerce"),
        # 직전 회차 분배금 ÷ 지금 시장가 — 위 '분배율'(기준가 기준)과 다르다
        "분배율(시장가)": pd.to_numeric(view.get("최근분배율_시장가"), errors="coerce"),
        "연분배율": pd.to_numeric(view.get("연간분배율"), errors="coerce"),
        "분배성장": pd.to_numeric(view.get("분배금성장률"), errors="coerce"),
        # 회차별 금액이 얼마나 들쭉날쭉한가 — 원금 반환(ROC) 대용 신호
        "분배변동": pd.to_numeric(view.get("분배변동계수"), errors="coerce"),
        # 특별분배를 뺀 평상 분배율 — 표시 분배율과 견주면 착시가 보인다
        "평상분배율": pd.to_numeric(view.get("중앙값연분배율"), errors="coerce"),
        # 19a-1 공시의 회계연도 누적 원금반환 비율 — 받아 온 발행사만 채워진다
        "원금반환": pd.to_numeric(view.get("누적_원금반환비율"), errors="coerce"),
        "루틴": pd.to_numeric(view.get("루틴점수"), errors="coerce"),
        "YTD": pd.to_numeric(view["YTD수익률"], errors="coerce"),
        "1년": pd.to_numeric(view["1년수익률"], errors="coerce"),
        # 가격은 분배한 만큼 빠진다 → 둘을 더해야 실제로 번 돈에 가까워진다
        "총수익(1년)": (pd.to_numeric(view["1년수익률"], errors="coerce")
                        + pd.to_numeric(view.get("연간분배율"), errors="coerce")),
        # 1년만 보면 사이클을 못 본다. 미국 상장분만 채워진다.
        "3년": pd.to_numeric(view.get("3년평균수익률"), errors="coerce"),
        "5년": pd.to_numeric(view.get("5년평균수익률"), errors="coerce"),
        "베타": pd.to_numeric(view.get("베타"), errors="coerce"),
        "괴리율": pd.to_numeric(view.get("괴리율"), errors="coerce"),
        "운용기간": pd.to_numeric(view.get("운용기간_년"), errors="coerce"),
        # 팔 때 매도대금의 10% 를 떼는 종목. 총보수(연 1% 미만)와 자릿수가 다르다.
        "PTP": ptp_label(view.get("PTP"), view.get("PTP면제만료")),
        # 규모가 작으면 접힌다 → 원치 않는 시점에 강제로 현금화된다
        "청산위험": (view["청산위험"].fillna("").astype(str)
                     if "청산위험" in view.columns else ""),
        "화이트": view["화이트리스트"] if "화이트리스트" in view.columns else False,
        "보유": view["보유"] if "보유" in view.columns else False,
        "기준일": view["최근_지급기준일"].map(ymd),
        "매수마감": view["최근_매수마감일"].map(ymd),
        "비고": view["비고"],
    })
    # 화이트리스트·보유종목 파일이 없으면 그 칸은 아예 고를 수 없게 뺀다
    options = ["운용사", "지역", "섹터", "전략", "배수", "자산", "과세", "시총(억)", "총보수",
               "시장", "주기", "횟수", "분배금", "분배율", "분배율(시장가)",
               "연분배율", "분배성장",
               "분배변동", "평상분배율", "원금반환", "루틴",
               "YTD", "1년", "총수익(1년)", "3년", "5년", "베타", "괴리율",
               "운용기간",
               "PTP", "청산위험", "기준일", "매수마감", "비고"]
    if show["시장"].nunique() < 2:      # 국내만 있으면 굳이 안 보여준다
        options.remove("시장")
    # 연분배율만 보면 착시가 생긴다(분배한 만큼 가격이 빠져서) → 총수익을 나란히 켜 둔다
    default = ["운용사", "섹터", "전략", "배수", "총보수", "주기", "분배율(시장가)",
               "연분배율", "YTD", "1년", "총수익(1년)"]
    if bool(show["보유"].any()):
        options.insert(0, "보유")
    if bool(show["화이트"].any()):
        options.insert(0, "화이트")
        default = ["화이트"] + default[:-1]     # 9칸 유지 — 마지막 하나를 밀어낸다
    columns = pick_columns("table_columns", options, default, fixed=["코드"])
    st.dataframe(
        show[columns], width="stretch", hide_index=True,
        column_config={
            "종목명": st.column_config.TextColumn(width="medium", pinned=True),
            "코드": st.column_config.TextColumn(
                width=75, help="증권사 앱에서 찾을 때 쓰는 값 — 국내는 6자리, 미국은 티커"),
            "운용사": st.column_config.TextColumn(width="small"),
            "지역": st.column_config.TextColumn(width="small",
                                               help="기초지수명으로 추정한 투자지역"),
            "섹터": st.column_config.TextColumn(width="small",
                                               help="기초지수명으로 추정한 테마·섹터"),
            "전략": st.column_config.TextColumn(width="small",
                                               help="커버드콜·혼합 등 (기초지수명 기준)"),
            "배수": st.column_config.TextColumn(
                width="small",
                help="레버리지·인버스 **배수**. 한투 API 의 추적수익률 배수 "
                     "(`etf_trc_ert_mltp`)로, 이름이 아니라 데이터 값입니다.\n\n"
                     "**빈칸은 '자료 없음' 뜻입니다** (1배도 `1배` 로 적습니다).\n\n"
                     "⚠ **국내 상장분만 채워집니다.** 미국 상장분은 이 API 대상이 "
                     "아니고, 야후는 카테고리에 Leveraged/Inverse 라는 **방향만** "
                     "주고 배수(2X·3X)는 안 줍니다 — 미국은 `전략` 칸을 보세요. "
                     "국내 중에도 메타가 없는 31종목은 빈칸입니다"),
            "자산": st.column_config.TextColumn(width="small",
                                               help="KRX 자산분류 (주식·채권·원자재…)"),
            "과세": st.column_config.TextColumn(width="small", help="KRX 과세유형"),
            "시총(억)": st.column_config.NumberColumn(
                width="small", format="localized",
                help="국내는 KRX 시가총액(억원, 직전 개장일). 미국은 야후 순자산(AUM)을 "
                     "원화로 환산한 값이라 같은 펀드의 다른 클래스까지 합쳐진 경우가 있습니다"),
            "총보수": st.column_config.NumberColumn(width="small", format="%.3f%%",
                                                   help="KRX 공시 ETF 총보수(연, %)"),
            "시장": st.column_config.TextColumn(
                width="small", help="상장 거래소. '미국'은 금액이 원화 환산값입니다"),
            "주기": st.column_config.TextColumn(width="small", help="최근 12개월 지급 횟수로 판정"),
            "횟수": st.column_config.NumberColumn(width="small", format="%d",
                                                 help="최근 12개월 분배금 지급 횟수"),
            "분배금": st.column_config.NumberColumn(width="small", format="%.0f",
                                                   help="직전 회차 주당분배금(원)"),
            "분배율": st.column_config.NumberColumn(
                width="small", format="%.2f%%",
                help="**직전 1회분** 분배율(세이브로 값 — **분배 당시 기준가**로 나눈 것). "
                     "주기가 다르면 그대로 비교하면 안 됩니다"),
            "분배율(시장가)": st.column_config.NumberColumn(
                width="small", format="%.2f%%",
                help="직전 1회분 분배금 ÷ **지금 시장가**. 왼쪽 '분배율'은 분배 당시 "
                     "기준가로 나눈 값이라, 가격이 많이 움직였으면 둘이 벌어집니다. "
                     "주기가 다르면 그대로 비교하면 안 됩니다"),
            "연분배율": st.column_config.NumberColumn(
                width="small", format="%.2f%%",
                help="최근 12개월 분배금 합 ÷ 현재가. **주기가 달라도 이 값끼리는 비교됩니다** "
                     "(월 12회든 연 1회든 1년치라서). 과거 실적이라 앞으로도 같다는 뜻은 아닙니다"),
            "분배성장": st.column_config.NumberColumn(
                width="small", format="%+.1f%%",
                help="직전 12개월 분배금 합이 그 앞 12개월보다 얼마나 늘었나. "
                     "⚠ **상장 첫 해에는 지급 횟수 자체가 적어** 수백 %로 찍힐 수 있습니다 "
                     "(분배금이 늘었다기보다 횟수가 는 것). 주기와 함께 보세요"),
            "분배변동": st.column_config.NumberColumn(
                width="small", format="%.0f%%",
                help="최근 12개월 **회차별 분배금**이 얼마나 들쭉날쭉한가 "
                     "(표준편차 ÷ 평균). 벌어서 주는 돈은 회차마다 비슷하고, "
                     "**원금을 헐어 주는 돈(ROC)** 은 출렁입니다 — 공시(19a-1)를 "
                     "직접 받을 수 없어 쓰는 **대용 신호**입니다.\n\n"
                     "주기마다 정상 범위가 다릅니다 (지금 데이터 중앙값): "
                     "**월 8% · 분기 31% · 위클리 71%**. 커버드콜은 시장 변동성에 따라 "
                     "프리미엄이 달라져 정상적으로도 높습니다. 같은 주기끼리 비교하세요.\n\n"
                     "연분배율이 높은데 이 값도 높고 총수익(1년)이 마이너스면 "
                     "원금을 돌려받고 있을 가능성이 큽니다. 지급이 3회 미만이면 빈칸입니다"),
            "평상분배율": st.column_config.NumberColumn(
                width="small", format="%.2f%%",
                help="**특별분배를 뺀** 연분배율 — 회차 금액의 **중앙값** × 지급 횟수 ÷ 현재가. "
                     "한 회차만 크게 준 특별분배는 합계(연분배율)를 부풀리지만 중앙값은 "
                     "흔들지 못합니다.\n\n"
                     "**연분배율보다 많이 낮으면 그 분배율은 한두 회에 기댄 값**입니다 "
                     "(예: Direxion Daily Magnificent 7 Bull 2X — 표시 9.33%, 평상 0.18%). "
                     "내년에도 기대할 수 있는 쪽은 이 값입니다"),
            "원금반환": st.column_config.NumberColumn(
                width="small", format="%.0f%%",
                help="**19a-1 공시**의 **원금 반환(ROC)** 비율. 분배금 중 펀드가 번 "
                     "돈이 아니라 **내가 넣은 원금을 돌려주는** 몫입니다. 이 값이 크면 "
                     "분배율이 높아 보여도 수익이 아닙니다 "
                     "(예: QYLD 99.3%, XYLD 94.7%).\n\n"
                     "**발행사마다 내놓는 것이 다릅니다.** Global X 는 회계연도 누적을 "
                     "주고, Roundhill·Rex·GraniteShares 는 **가장 최근 회차**만 줍니다 "
                     "— 누적이 없으면 최근 회차로 판정하며 그 달만 유난했을 수 있어 "
                     "누적보다 거칩니다.\n\n"
                     "**받아 온 발행사만 채워집니다** — Global X · Roundhill · Rex · "
                     "GraniteShares. YieldMax·Defiance 는 공시 PDF 폴더가 robots.txt 로 "
                     "막혀 있어 받지 않습니다. 국내 ETF 는 세이브로 전수 확인 결과 해당 "
                     "사례가 없어 빈칸입니다. 운용사 추정치이며 연말 1099-DIV 로 확정됩니다"),
            "루틴": st.column_config.NumberColumn(
                width="small", format="%+d",
                help="**매수 전 5분 루틴** 점수(-4 ~ +4). 가격 잠식 · 분배 안정성 · "
                     "이익초과분배 · 특별분배 착시 네 항목의 판정(+1/0/-1) 합입니다.\n\n"
                     "자료가 없는 항목은 점수에 안 들어갑니다(커버드콜 천장은 사람이 "
                     "볼 일이라 애초에 점수가 없습니다). **매수 신호가 아니라 들여다볼 "
                     "종목을 고르는 값**입니다 — 아래 루틴 화면에서 항목별 근거를 보세요"),
            "YTD": st.column_config.NumberColumn(
                width="small", format="%+.2f%%",
                help="올해 첫 개장일 대비 가격 변화율. 분배금은 포함되지 않습니다."),
            "1년": st.column_config.NumberColumn(
                width="small", format="%+.2f%%",
                help="1년 전 대비 가격 변화율. 분배금은 포함되지 않습니다."),
            "총수익(1년)": st.column_config.NumberColumn(
                width=95, format="%+.1f%%",
                help="1년 가격 변화율 + 연간 분배율. **분배율만 보면 착시가 생깁니다** — "
                     "분배한 만큼 가격이 빠지기 때문입니다. 연 400% 분배인데 이 값이 마이너스면 "
                     "원금을 헐어서 나눠주고 있다는 뜻입니다. 재투자·세금은 안 넣은 대용치입니다"),
            "3년": st.column_config.NumberColumn(
                width="small", format="%+.2f%%",
                help="최근 3년 **연평균** 수익률(분배금 재투자 기준). 미국 상장분만 있습니다. "
                     "비어 있으면 운용기간을 함께 보세요 — 아직 3년이 안 됐을 수 있습니다"),
            "5년": st.column_config.NumberColumn(
                width="small", format="%+.2f%%",
                help="최근 5년 **연평균** 수익률(분배금 재투자 기준). 미국 상장분만 있습니다"),
            "베타": st.column_config.NumberColumn(
                width="small", format="%.2f",
                help="시장이 1% 움직일 때 이 종목이 몇 % 움직였나(3년 기준). "
                     "1보다 크면 시장보다 많이 흔들립니다"),
            "괴리율": st.column_config.NumberColumn(
                width="small", format="%+.2f%%",
                help="종가가 NAV(순자산가치)보다 얼마나 비싼가. **+면 웃돈을 주고 사는 것**입니다. "
                     "거래가 뜸한 종목일수록 크게 벌어져, 그만큼이 매매 손실로 붙습니다"),
            "운용기간": st.column_config.NumberColumn(
                width="small", format="%.1f년",
                help="설정일부터 지금까지. 짧으면 장기 성과를 못 봅니다"),
            "PTP": st.column_config.TextColumn(
                width="small",
                help="PTP 종목은 팔 때 **매도 대금의 10%** 가 원천징수됩니다. 차익이 아니라 "
                     "판 금액 전체가 기준이라 **손실을 보고 팔아도 떼입니다**. "
                     "`면제~날짜` 는 그날까지만 빠져 있다는 뜻입니다 — 만료되면 다시 대상입니다. "
                     "목록은 수시로 바뀌니 매매 전 거래 증권사 공지로 확인하세요"),
            "청산위험": st.column_config.TextColumn(
                width="small",
                help="규모가 작으면 ETF가 접히고, 원치 않는 시점에 강제로 현금화됩니다.\n\n"
                     "**두 시장은 기준의 성격이 다릅니다.**\n\n"
                     "· **국내** — `상폐요건`(순자산 50억원 미만)은 **실제 상장폐지 규정**입니다. "
                     "`주의`는 100억원 미만.\n\n"
                     "· **미국** — `위험`(순자산 $50M 미만)은 규정이 아니라 **운용사가 접는 관행선**입니다. "
                     "거래소 상장유지 요건은 $1M 로 훨씬 낮고, 거기 걸리면 `상폐요건`으로 표시됩니다. "
                     "`주의`는 $100M 미만.\n\n"
                     "참고 신호이지 예측이 아닙니다."),
            "화이트": st.column_config.CheckboxColumn(
                width=65, help="회사 계좌로 매매 가능한 종목(화이트리스트)"),
            "보유": st.column_config.CheckboxColumn(
                width=60, help="지금 들고 있는 종목 (보유종목.csv 기준)"),
            "기준일": st.column_config.TextColumn(width="small", help="직전 분배 지급기준일"),
            "매수마감": st.column_config.TextColumn(width="small",
                                                   help="지급기준일 2영업일 전 (이날까지 사야 받음)"),
            "비고": st.column_config.TextColumn(width="small",
                                               help="'주기 확인' = 분배주기 판정이 갈린 종목"),
        },
    )

    # 분배는 많은데 총수익이 마이너스인 종목 — 원금을 헐어 나눠주는 구조다
    risky = show[(show["연분배율"] > 20) & (show["총수익(1년)"] < 0)]
    if not risky.empty:
        st.warning(
            f"⚠ **연분배율 20%가 넘는데 1년 총수익이 마이너스인 종목이 {len(risky):,}개** 있습니다. "
            "분배금을 원금에서 헐어 나눠주고 있다는 뜻입니다 "
            f"(예: {' · '.join(risky.nlargest(3, '연분배율')['종목명'].head(3))}). "
            "**연분배율만 보고 고르지 마세요.**")

    # PTP — 팔 때 매도대금 10% 가 날아간다. 총보수 0.1% 를 따지다 이걸 놓치면 무의미하다.
    ptp_state = view.get("PTP")
    if ptp_state is not None:
        ptp_state = ptp_state.fillna("").astype(str).str.strip()
        subject = view[ptp_state == "대상"]
        exempt = view[ptp_state == "면제"]
        if not subject.empty:
            st.error(
                f"🚨 **PTP 종목 {len(subject):,}개** — 팔 때 **매도 대금의 10%** 가 "
                "원천징수됩니다. 차익이 아니라 판 금액 전체 기준이라 **손실을 보고 팔아도 떼입니다** "
                f"({' · '.join(subject['종목명'].head(3))}).")
        if not exempt.empty:
            expiry = (exempt["PTP면제만료"].astype(str).str.strip()
                      .replace("", pd.NA).dropna())
            soonest = expiry.min() if not expiry.empty else "?"
            st.warning(
                f"⏳ **PTP 면제 종목 {len(exempt):,}개** — 지금은 10% 원천징수에서 빠져 있지만 "
                f"**면제가 {soonest}부터 차례로 만료**됩니다. 만료 뒤에 팔면 떼입니다. "
                "매매 전 거래 증권사 공지로 최신 상태를 확인하세요.")

    # 청산 위험 — 국내 '상폐요건'은 실제 규정선이라 따로 띄운다
    if "청산위험" in view.columns:
        level = view["청산위험"].fillna("").astype(str).str.strip()
        delist = view[(level == "상폐요건") & (view["상장시장"] == "한국")]
        us_risk = view[(level == "위험") & (view["상장시장"] == "미국")]
        if not delist.empty:
            st.error(
                f"🚨 **순자산 50억원 미만인 국내 ETF {len(delist):,}개** — "
                "이 상태가 이어지면 **상장폐지 요건**에 걸립니다(실제 규정). "
                f"예: {' · '.join(delist['종목명'].head(3))}")
        if not us_risk.empty:
            st.warning(
                f"⚠ **순자산 $50M 미만인 미국 ETF {len(us_risk):,}개** — 규정 위반은 아니지만 "
                "운용사가 접기 쉬운 규모입니다. 청산되면 원치 않는 시점에 현금화됩니다.")

    flagged = view[view["비고"] != ""]
    with st.expander(f"⚠ 비고 '주기 확인'이 무슨 뜻인가요? (현재 {len(flagged):,}개)"):
        st.markdown(비고_설명)
        if not flagged.empty and "비고상세" in flagged.columns:
            사유 = (flagged["비고사유"].fillna("")
                    if "비고사유" in flagged.columns else pd.Series("", index=flagged.index))
            if (사유 != "").any():
                st.caption("원인별 — " + " · ".join(
                    f"**{name}** {count}개"
                    for name, count in 사유[사유 != ""].value_counts().items()))
            detail = pd.DataFrame({
                "종목명": flagged["종목명"],
                "사유": 사유,
                "주기": flagged["분배주기"].map(CYCLE_LABEL),
                "횟수": flagged["지급횟수_12개월"],
                "평균간격(일)": flagged["중앙값간격_일"],
                "확인할 내용": flagged["비고상세"],
            })
            st.dataframe(detail, width="stretch", hide_index=True, column_config={
                "사유": st.column_config.TextColumn(
                    width="small",
                    help="상장일로 가른 원인. **신규상장**은 주기를 자동으로 바로잡았고, "
                         "**실제 불규칙**·**상장일 없음**은 그대로 뒀습니다"),
            })


ROUTINE_CSS = """
<style>
.etf-rt{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:10px;}
.etf-rt .card{border-radius:10px;padding:10px 12px;background:rgba(128,128,128,.10);}
.etf-rt .top{display:flex;align-items:baseline;gap:8px;}
.etf-rt .no{font-size:.7rem;font-weight:700;opacity:.55;
  background:rgba(128,128,128,.22);border-radius:5px;padding:1px 6px;}
.etf-rt .ttl{font-weight:700;font-size:.93rem;}
.etf-rt .vd{margin-left:auto;font-size:.74rem;font-weight:700;
  border-radius:999px;padding:1px 9px;color:#fff;white-space:nowrap;}
.etf-rt .basis{font-size:.8rem;opacity:.85;margin:5px 0 6px;}
.etf-rt .note{font-size:.76rem;line-height:1.45;opacity:.9;
  background:rgba(128,128,128,.10);border-radius:6px;padding:6px 8px;}
.etf-rt .gauge{height:5px;border-radius:3px;margin-top:8px;
  background:rgba(128,128,128,.22);position:relative;}
.etf-rt .gauge i{position:absolute;top:0;height:5px;border-radius:3px;}
.etf-rt .scale{display:flex;justify-content:space-between;font-size:.62rem;
  opacity:.55;margin-top:2px;}
</style>
"""

# 판정 색 — 밝은/어두운 테마 모두에서 글자(흰색)가 읽히는 채도로 골랐다
VERDICT_STYLE = {
    1: ("+1 · 긍정", "#2f8f46"),
    0: ("0 · 중립", "#8a8f98"),
    -1: ("-1 · 부정", "#cf4040"),
    None: ("판정 없음", "#8a8f98"),
}


def routine_card_html(index: int, card: dict) -> str:
    """판정 카드 하나. 게이지는 -1(왼쪽) 0(가운데) +1(오른쪽) 자리를 칠한다."""
    label, color = VERDICT_STYLE[card["점수"]]
    if card["점수"] is None:
        bar = ""                       # 점수를 못 매기면 게이지도 비운다
    else:
        left = {1: "66%", 0: "33%", -1: "0"}[card["점수"]]
        bar = f'<i style="left:{left};width:34%;background:{color}"></i>'
    # 해석 문구의 **굵게** 만 통과시키고 나머지는 이스케이프한다
    note = escape(card["해석"]).replace("**", "\x00")
    parts = note.split("\x00")
    note = "".join(p if i % 2 == 0 else f"<b>{p}</b>" for i, p in enumerate(parts))
    return (
        '<div class="card">'
        f'<div class="top"><span class="no">{index}</span>'
        f'<span class="ttl">{escape(card["항목"])}</span>'
        f'<span class="vd" style="background:{color}">{label}</span></div>'
        f'<div class="basis">{escape(card["근거"])}</div>'
        f'<div class="note">{note}</div>'
        f'<div class="gauge">{bar}</div>'
        '<div class="scale"><span>-1 부정</span><span>0 중립</span>'
        '<span>+1 긍정</span></div>'
        "</div>"
    )


def render_routine(view: pd.DataFrame) -> None:
    """매수 전 5분 루틴 — 종목 하나를 골라 항목별 판정을 본다."""
    st.subheader("✅ 매수 전 5분 루틴")
    if view.empty:
        st.write("표시할 종목이 없습니다.")
        return

    names = list(view["종목명"].astype(str))
    picked = st.selectbox("어느 종목을 볼까요?", names, key="routine_pick",
                          help="위 필터를 지난 종목 중에서 고릅니다")
    row = view[view["종목명"].astype(str) == picked].iloc[0]
    cards = routine.judge(row)
    score = routine.total_score(cards)

    scored = [c for c in cards if c["점수"] is not None]
    c1, c2, c3 = st.columns([1, 1, 2])
    c1.metric("루틴 점수", f"{score:+d} / {routine.MAX_SCORE}",
              help=f"점수를 매길 수 있는 {routine.MAX_SCORE}개 항목의 합입니다 "
                   f"(-{routine.MAX_SCORE} ~ +{routine.MAX_SCORE}). "
                   "**매수 신호가 아니라 들여다볼 자리를 고르는 값**입니다")
    c2.metric("판정한 항목", f"{len(scored)} / {len(cards)}",
              help="자료가 없어 판정하지 못한 항목은 점수에 안 들어갑니다")
    c3.metric("연분배율 (표시 / 평상)",
              f'{row.get("연간분배율") or 0:.2f}% / '
              f'{row.get("중앙값연분배율") or 0:.2f}%',
              help="평상 = 회차 금액의 **중앙값**으로 다시 계산한 분배율. "
                   "특별분배 한 회에 흔들리지 않습니다")

    st.markdown(ROUTINE_CSS + '<div class="etf-rt">'
                + "".join(routine_card_html(i, c) for i, c in enumerate(cards, 1))
                + "</div>", unsafe_allow_html=True)
    st.caption("루틴 5개 항목 중 **③ 이익초과분배는 자료가 없어 판정하지 않습니다**. "
               "①Coverage 는 ②가격 잠식과 같은 축이라 합쳤습니다 — "
               "자세한 이유는 각 카드의 해석을 보세요.")


COMPARE_GRID_LIMIT = 300      # 그리드에 올릴 후보 수 — 더 좁히려면 사이드바 필터를 쓴다

# 대안 비교에서 **무엇부터 올릴지**. 전에는 정렬 없이 앞 300개를 잘라서
# '주기→가나다 앞부분'이 올라왔다 — 5단계에서 NAV 비교표에 있던 것과 같은 함정이다.
# (정렬 컬럼, 오름차순, 설명). `_겹침`·`루틴점수` 는 이 탭에서 만든 값이다.
COMPARE_PICK_ORDER = {
    "겹침 낮은 순": ("_겹침", True,
                   "내 보유종목과 **덜 겹치는** 순서. 이 탭의 본래 목적 — "
                   "많이 겹치는 것으로 갈아타면 비용만 들고 분산은 그대로입니다. "
                   "보유종목이 없으면 겹침이 비어 있어 이 기준은 의미가 없습니다."),
    "루틴 점수 높은 순": ("루틴점수", False,
                       "매수 전 5분 루틴 점수가 높은 순서(-4~+4). "
                       "가격 잠식·분배 안정성·원금 반환·특별분배 판정의 합입니다."),
    "시총 큰 순": ("시가총액", False,
                  "순자산이 큰 순서. 거래가 활발하고 청산 위험이 낮은 쪽부터."),
    "연분배율 높은 순": ("연간분배율", False,
                     "최근 12개월 분배금 합 ÷ 현재가. **높다고 좋은 게 아니라** "
                     "원금 반환일 수 있으니 원금반환·분배변동과 같이 보세요."),
    "평상분배율 높은 순": ("중앙값연분배율", False,
                       "특별분배를 뺀 **평상시** 분배율. 내년에도 기대할 수 있는 쪽."),
    "총보수 낮은 순": ("총보수", True, "매년 빠져나가는 비용이 적은 순서."),
    "1년 수익 높은 순": ("1년수익률", False,
                     "1년 가격 수익률(분배금 미포함)."),
}


@st.cache_data(ttl=3600)
def load_compst_cached():
    """실측 겹침 재료(섹터·TOP10·PDF·KIS30 비중). 없으면 빈 dict → 분류 폴백."""
    return (*compare.load_compst(), compare.load_kis30())


def render_compare(view: pd.DataFrame, df: pd.DataFrame) -> None:
    """대안 비교 — 후보를 좁히고 최대 4개를 나란히 놓고 견준다."""
    st.subheader("⚖ 대안 비교")
    st.caption(
        f"보고 싶은 기준을 골라 후보를 좁히고, **최대 {compare.MAX_PICK}개**를 체크해 "
        "나란히 비교하세요. **겹침 칸을 꼭 보세요** — 이미 가진 것과 많이 겹치는 "
        "ETF 는 갈아타도 분산이 늘지 않습니다.")
    if view.empty:
        st.write("표시할 종목이 없습니다.")
        return

    lens = st.pills("무엇을 기준으로 볼까요?", list(compare.VIEWS),
                    default="개요", key="compare_view")
    lens = lens or "개요"

    holdings = df[df["보유"]] if "보유" in df.columns else df.iloc[0:0]
    sectors, top10, pdf, kis30 = load_compst_cached()

    order_label = st.pills(
        "무엇부터 올릴까요?", list(COMPARE_PICK_ORDER),
        default="겹침 낮은 순", key="compare_pick_order",
        help="후보가 많으면 앞에서 자르기 때문에 **무엇부터 올리느냐가 곧 결과**입니다.",
    ) or "겹침 낮은 순"
    column, ascending, _ = COMPARE_PICK_ORDER[order_label]

    scored = compare.add_total_return(view).copy()
    if column == "_겹침":
        # 겹침으로 정렬하려면 **자르기 전에** 전체를 계산해야 한다.
        # 300개만 재고 나서 정렬하면 그 300개가 이미 엉뚱한 앞부분이다.
        if holdings.empty:
            st.info("보유종목이 없어 겹침을 잴 수 없습니다 — 다른 기준을 고르세요.")
        scored["_겹침"] = compare.overlap_with_holdings(scored, holdings,
                                                        sectors, pdf, kis30)
    candidates = sort_by_column(scored, column, ascending).head(
        COMPARE_GRID_LIMIT).copy()
    if "_겹침" not in candidates.columns:
        candidates["_겹침"] = compare.overlap_with_holdings(
            candidates, holdings, sectors, pdf, kis30)

    with st.expander("❓ 기준이 무슨 뜻인가요?"):
        st.markdown("\n".join(
            f"- **{label}** — {desc}"
            for label, (_, _, desc) in COMPARE_PICK_ORDER.items()))
    if len(view) > COMPARE_GRID_LIMIT:
        st.caption(f"후보 {len(view):,}개 중 **{order_label}으로 앞 "
                   f"{COMPARE_GRID_LIMIT}개**만 올렸습니다. "
                   "사이드바 필터로 더 좁힐 수 있습니다.")

    grid = pd.DataFrame({
        "고르기": False,
        "종목명": candidates["종목명"],
        "코드": candidates["KRX코드"] if "KRX코드" in candidates.columns else "",
        "보유": candidates["보유"] if "보유" in candidates.columns else False,
    })
    for label, column, unit, _ in compare.VIEWS[lens]:
        values = candidates.get(column)
        if values is None:
            grid[label] = None
            continue
        number = pd.to_numeric(values, errors="coerce")
        if column in compare.IN_HUNDRED_MILLION:
            number = number / 1e8
        grid[label] = number if number.notna().any() else values.astype(str)
    grid["겹침"] = candidates["_겹침"]

    edited = st.data_editor(
        grid, width="stretch", hide_index=True, key=f"compare_grid_{lens}",
        disabled=[c for c in grid.columns if c != "고르기"],
        column_config={
            "고르기": st.column_config.CheckboxColumn(width=60),
            "종목명": st.column_config.TextColumn(width="medium", pinned=True),
            "코드": st.column_config.TextColumn(width=75),
            "보유": st.column_config.CheckboxColumn(width=55, help="지금 들고 있는 종목"),
            "겹침": st.column_config.NumberColumn(
                width=70, format="%.0f%%",
                help="**내 보유종목 중 가장 많이 겹치는 것**과의 겹침입니다. "
                     "평균이 아니라 최댓값입니다 — 열 개 중 하나와 90% 겹치면 "
                     "그것만으로 새로 살 이유가 흔들리기 때문입니다.\n\n"
                     "**실측(v2)**: KRX PDF(**전체 구성종목**)에서 두 ETF 의 "
                     "종목별 비중이 겹치는 부분의 합입니다 — 진짜 보유종목 "
                     "기준이라 같은 섹터의 다른 종목은 겹침으로 안 봅니다.\n\n"
                     "⚠ PDF 가 없는 종목은 섹터 비중(세이브로)으로, 그것도 "
                     "없으면(미국 상장 등) 분류로 근사합니다. 기초지수가 같으면 "
                     "100%로 봅니다.\n\n"
                     "보유종목.csv 가 없으면 빈칸입니다"),
        },
    )

    picked_names = list(edited[edited["고르기"]]["종목명"])
    if not picked_names:
        st.info(f"왼쪽 **고르기**를 체크하면 아래에서 나란히 비교합니다 "
                f"(최대 {compare.MAX_PICK}개).")
        return
    if len(picked_names) > compare.MAX_PICK:
        st.warning(f"{len(picked_names)}개를 골랐습니다 — "
                   f"앞 {compare.MAX_PICK}개만 비교합니다.")
        picked_names = picked_names[:compare.MAX_PICK]

    picked = candidates[candidates["종목명"].isin(picked_names)]
    st.divider()
    st.write(f"**나란히 비교** ({len(picked)}/{compare.MAX_PICK} 선택 · 기준: {lens})")

    table = compare.side_by_side(picked, lens)
    best = compare.winners(picked, lens)

    def mark(row):
        """지표마다 유리한 쪽에 ◆ 를 붙인다. 좋고 나쁨을 안 따지는 지표는 그대로 둔다."""
        winner = best.get(row.name)
        return [f"{v}  ◆" if winner and column == winner and v is not None else v
                for column, v in row.items()]

    shown = table.apply(lambda r: pd.Series(mark(r), index=r.index), axis=1)
    st.dataframe(shown.astype(str).replace({"None": "—", "nan": "—"}),
                 width="stretch")
    st.caption("◆ = 그 지표에서 유리한 쪽입니다. 총보수·분배변동·원금반환은 **낮을수록**, "
               "나머지는 높을수록 유리한 것으로 봤습니다. "
               "베타·괴리율·주기처럼 좋고 나쁨을 따질 수 없는 값에는 안 붙입니다.")

    # 겹침 — 어느 보유종목과 겹치는지까지 말해 줘야 판단이 된다
    if not holdings.empty:
        lines = []
        for _, row in picked.iterrows():
            score = row.get("_겹침")
            partner = compare.best_match_name(row, holdings, sectors, pdf, kis30)
            if score and score >= compare.OVERLAP_WARN and partner:
                line = f"**{row['종목명']}** ↔ 보유 중인 **{partner}** ({score:.0f}%)"
                # TOP10 실제 보유종목이 확인해 주는 하한 (세이브로 상세는 TOP10 뿐)
                mine = holdings[holdings["종목명"].astype(str) == partner]
                floor = compare.top10_overlap(
                    row.get("ISIN"),
                    mine.iloc[0].get("ISIN") if not mine.empty else None, top10)
                if floor is not None:
                    line += f" — 상위 10종목만으로도 **{floor:.0f}%** 확인"
                lines.append(line)
        if lines:
            st.warning("⚠ 이미 가진 것과 많이 겹칩니다 — 갈아타도 분산이 늘지 않습니다.\n\n"
                       + "\n\n".join(lines))

    # 성장 비교 — 가격 시계열을 저장하지 않아 구간 수익률 막대로 대신한다
    periods = [("YTD", "YTD수익률"), ("1년", "1년수익률"),
               ("3년(연평균)", "3년평균수익률"), ("5년(연평균)", "5년평균수익률")]
    rows = []
    for _, row in picked.iterrows():
        for label, column in periods:
            value = pd.to_numeric(pd.Series([row.get(column)]), errors="coerce").iloc[0]
            if pd.notna(value):
                rows.append({"구간": label, "종목명": row["종목명"], "수익률": value})
    if rows:
        st.write("**구간별 수익률 비교**")
        fig = px.bar(pd.DataFrame(rows), x="구간", y="수익률", color="종목명",
                     barmode="group")
        fig.update_layout(xaxis_title="", yaxis_title="%", legend_title_text="",
                          hovermode="x unified", bargap=0.3)
        st.plotly_chart(fig, width="stretch")
        st.caption("가격 시계열을 저장하지 않아 **구간 수익률 막대**로 대신합니다"
                   "(기준 100 겹쳐 그리기는 종목별 일별 시세가 있어야 합니다). "
                   "**분배금은 빠진 가격 기준**이라, 분배가 큰 종목은 실제보다 낮게 보입니다 "
                   "— 배당 기준의 `연분배율`과 함께 보세요.")


def render_calendar(events: pd.DataFrame, view: pd.DataFrame) -> None:
    """다가오는 일정 — 목록 / 달력 / 월별 그래프 세 가지로 본다."""
    st.subheader("다가오는 일정 (매수마감 · 배당락)")
    if events.empty:
        st.info("분배캘린더.ics 가 없습니다. 먼저 `python src/calendar_ics.py` 를 실행하세요.")
        return

    today = date.today()
    mine = events[(events["날짜"] >= today)
                  & (events["종목명"].isin(set(view["종목명"])))].copy()
    if mine.empty:
        st.info("현재 필터 조건에 해당하는 앞으로의 일정이 없습니다.")
        return

    last = mine["날짜"].max()
    st.caption(f"현재 필터 {len(view):,}개 종목 · 앞으로 **{len(mine):,}건** "
               f"(캘린더 파일에 든 마지막 일정 {ymd(last)}). "
               "일정이 짧으면 `python src/calendar_ics.py` 를 다시 실행하세요 — "
               "실행 시점부터 6개월치를 만듭니다.")

    t1, t2, t3 = st.tabs(["📋 목록", "🗓 달력", "📊 월별 건수"])
    with t1:
        render_calendar_list(mine, view, today)
    with t2:
        render_calendar_grid(mine, today)
    with t3:
        render_calendar_months(mine)


def render_calendar_list(up: pd.DataFrame, view: pd.DataFrame, today: date) -> None:
    """날짜순 목록 표 (기본 30일)."""
    up = up.copy()
    days = st.slider("앞으로 며칠까지 볼까요?", 7, 180, 30, step=7)
    horizon = pd.Timestamp(today) + pd.Timedelta(days=days)
    up = up[up["날짜"] <= horizon.date()]
    asof = returns_asof()
    st.caption(f"{ymd(today)} ~ {ymd(horizon.date())} · {len(up):,}건 "
               "· 종류 '매수'=매수마감일, '배당'=배당락일"
               + (f" · 수익률 기준일 {asof}" if asof else ""))
    if up.empty:
        st.write("해당 기간에 예정된 이벤트가 없습니다.")
        return

    # 종목별 수익률을 종목명으로 붙인다 (일정은 .ics, 수익률은 xlsx+KRX 라 키가 종목명뿐)
    perf = (view[["종목명", "YTD수익률", "1년수익률"]]
            .drop_duplicates("종목명"))
    up = up.merge(perf, on="종목명", how="left")
    up["날짜"] = up["날짜"].map(ymd)

    show = up[["날짜", "종류", "종목명", "주기", "분배율", "YTD수익률", "1년수익률", "예상기준일"]]
    show = show.rename(columns={"YTD수익률": "YTD", "1년수익률": "1년"})
    st.dataframe(
        show, width="stretch", hide_index=True,
        column_config={
            "날짜": st.column_config.TextColumn(width="small"),
            "종류": st.column_config.TextColumn(width="small",
                                               help="매수=매수마감일(T-2), 배당=배당락일(T-1)"),
            "종목명": st.column_config.TextColumn(width="medium", pinned=True),
            "주기": st.column_config.TextColumn(width="small", help="분배주기"),
            "분배율": st.column_config.NumberColumn(width="small", format="%.2f%%",
                                                   help="직전 회차 분배율(1회분)"),
            "YTD": st.column_config.NumberColumn(
                width="small", format="%+.2f%%",
                help="올해 첫 개장일 대비 가격 변화율. 분배금은 포함되지 않습니다."),
            "1년": st.column_config.NumberColumn(
                width="small", format="%+.2f%%",
                help="1년 전 대비 가격 변화율. 분배금은 포함되지 않습니다."),
            "예상기준일": st.column_config.TextColumn(
                width="small", help="예상 지급기준일 — 공시 전이라 추정치입니다"),
        },
    )
    st.caption("**YTD·1년은 가격(종가) 수익률입니다.** 분배금을 받아 재투자한 총수익률이 아니라, "
               "분배한 만큼 가격이 빠진 결과가 그대로 들어 있습니다. "
               "분배금까지 감안한 값은 📊 NAV 비교표의 '총수익'을 보세요.")
    if show[["YTD", "1년"]].isna().all(axis=None):
        st.info("수익률이 비어 있습니다. `python src/etf_returns.py` 를 실행하면 채워집니다.")


# ── 달력 보기 ──────────────────────────────────────────────
CAL_CSS = """
<style>
.etf-cal{display:grid;grid-template-columns:repeat(7,1fr);gap:4px;margin-top:4px;}
.etf-cal .hd{text-align:center;font-size:.8rem;font-weight:600;opacity:.75;padding:2px 0;}
.etf-cal .hd.sun{color:#d03b3b;} .etf-cal .hd.sat{color:#2a78d6;}
.etf-cal .cell{min-height:84px;border-radius:8px;padding:4px 6px;
  background:rgba(128,128,128,.10);overflow:hidden;}
.etf-cal .cell.blank{background:transparent;}
.etf-cal .cell.today{box-shadow:inset 0 0 0 2px #2a78d6;}
.etf-cal .d{font-size:.78rem;font-weight:700;opacity:.85;}
.etf-cal .b{display:inline-block;font-size:.68rem;line-height:1.5;color:#fff;
  padding:0 6px;border-radius:999px;margin:2px 3px 0 0;white-space:nowrap;}
.etf-cal .nm{display:block;font-size:.66rem;opacity:.8;white-space:nowrap;
  overflow:hidden;text-overflow:ellipsis;}
</style>
"""
WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"]


def month_options(events: pd.DataFrame) -> list[tuple[int, int]]:
    """일정이 들어 있는 달 목록 [(년, 월), …] — 이른 달부터."""
    return sorted({(d.year, d.month) for d in events["날짜"]})


def calendar_cells(events: pd.DataFrame, year: int, month: int) -> dict:
    """그 달의 날짜별 요약: {일(day): {'매수': [종목…], '배당': [종목…]}}"""
    part = events[[d.year == year and d.month == month for d in events["날짜"]]]
    cells: dict[int, dict[str, list[str]]] = {}
    for day, kind, name in zip((d.day for d in part["날짜"]),
                               part["종류"], part["종목명"]):
        cells.setdefault(day, {}).setdefault(kind, []).append(name)
    return cells


def calendar_html(cells: dict, year: int, month: int,
                  today: date, colors: dict, kinds: list[str]) -> str:
    """달력 격자 HTML. 칸마다 종류별 건수 배지와 종목명 몇 개를 넣는다."""
    import calendar as cal_mod

    parts = [CAL_CSS, '<div class="etf-cal">']
    for i, name in enumerate(WEEKDAYS):
        css = "hd sun" if i == 0 else ("hd sat" if i == 6 else "hd")
        parts.append(f'<div class="{css}">{name}</div>')

    weeks = cal_mod.Calendar(firstweekday=6).monthdayscalendar(year, month)
    for week in weeks:
        for day in week:
            if day == 0:                      # 지난달·다음달 자리
                parts.append('<div class="cell blank"></div>')
                continue
            box = cells.get(day, {})
            is_today = (year, month, day) == (today.year, today.month, today.day)
            chunk = [f'<div class="cell{" today" if is_today else ""}">',
                     f'<span class="d">{day}</span>']
            names: list[str] = []
            for kind in kinds:                # '매수' → '배당' 순서 고정
                hit = box.get(kind, [])
                if not hit:
                    continue
                chunk.append(f'<span class="b" style="background:{colors[kind]}">'
                             f'{kind} {len(hit)}</span>')
                names += hit
            # 종목명은 두 개까지만. 나머지는 칸에 마우스를 올리면 툴팁으로 보인다
            for name in names[:2]:
                chunk.append(f'<span class="nm" title="{escape(name)}">'
                             f'{escape(name)}</span>')
            if len(names) > 2:
                chunk.append(f'<span class="nm" title="{escape(" · ".join(names))}">'
                             f'외 {len(names) - 2}개</span>')
            chunk.append("</div>")
            parts.append("".join(chunk))
    parts.append("</div>")
    return "".join(parts)


def render_calendar_grid(up: pd.DataFrame, today: date) -> None:
    """달력 격자로 보기 — 한 번에 한 달."""
    months = month_options(up)
    if not months:
        st.write("표시할 일정이 없습니다.")
        return

    c1, c2 = st.columns([1.2, 2])
    picked = c1.selectbox("어느 달을 볼까요?", months,
                          format_func=lambda ym: f"{ym[0]}년 {ym[1]}월",
                          key="cal_month")
    kinds = c2.pills("무엇을 볼까요?", ["매수", "배당"], selection_mode="multi",
                     default=["매수", "배당"], key="cal_kinds",
                     help="매수=매수마감일(T-2, 이날까지 사야 받음) / 배당=배당락일(T-1)")
    kinds = [k for k in ("매수", "배당") if k in (kinds or [])] or ["매수", "배당"]

    colors = KIND_COLORS[theme_mode()]
    year, month = picked
    cells = calendar_cells(up[up["종류"].isin(kinds)], year, month)
    st.markdown(calendar_html(cells, year, month, today, colors, kinds),
                unsafe_allow_html=True)

    legend = " · ".join(
        f'<span style="display:inline-block;width:10px;height:10px;border-radius:3px;'
        f'background:{colors[k]};margin-right:4px"></span>'
        f'{k}{"마감(T-2)" if k == "매수" else "락(T-1)"}' for k in kinds)
    st.markdown(f'<div style="font-size:.8rem;opacity:.8;margin-top:6px">{legend}'
                " · 칸에 마우스를 올리면 그날 종목이 다 보입니다</div>",
                unsafe_allow_html=True)

    total = sum(len(v) for box in cells.values() for v in box.values())
    st.caption(f"{year}년 {month}월 · {total:,}건. 날짜는 **예상치**라 공시로 바뀔 수 있습니다.")


def monthly_counts(up: pd.DataFrame, kind: str) -> pd.DataFrame:
    """월별·분배주기별 예정 건수. 컬럼: 월 / 주기 / 건수 (월 오름차순)"""
    part = up[up["종류"] == kind].copy()
    if part.empty:
        return pd.DataFrame(columns=["월", "주기", "건수"])
    part["월"] = [f"{d.year % 100:02d}-{d.month:02d}" for d in part["날짜"]]
    part["주기"] = part["주기"].map(lambda c: CYCLE_LABEL.get(c, c or "미상"))
    return (part.groupby(["월", "주기"]).size().reset_index(name="건수")
            .sort_values("월").reset_index(drop=True))


def monthly_counts_wide(agg: pd.DataFrame, order: list[str]) -> pd.DataFrame:
    """위 집계를 '월 × 주기' 표로 편다.

    주기 이름에 '월'이 있어서 기간 칸(월)과 이름이 부딪힌다.
    그래서 주기 칸은 '월 분배'처럼 뒤에 말을 붙여 구분한다.
    """
    wide = (agg.pivot(index="월", columns="주기", values="건수")
            .reindex(columns=[c for c in order if c in set(agg["주기"])])
            .fillna(0).astype(int))
    wide.columns = [f"{c} 분배" for c in wide.columns]
    wide = wide.rename_axis(index=None, columns=None).reset_index(names="월")
    wide["합계"] = wide.drop(columns="월").sum(axis=1)
    return wide


def render_calendar_months(up: pd.DataFrame) -> None:
    """월별로 몇 건이 예정돼 있는지 세로 막대그래프."""
    kind = st.radio("무엇을 셀까요?", ["배당", "매수"], horizontal=True, key="cal_bar_kind",
                    help="배당=배당락일 기준(분배 회차 수) / 매수=매수마감일 기준. "
                         "같은 회차의 이틀이라 건수는 거의 같습니다")
    agg = monthly_counts(up, kind)
    if agg.empty:
        st.write("표시할 일정이 없습니다.")
        return

    order = [CYCLE_LABEL[c] for c in CYCLE_ORDER] + ["미상"]
    fig = px.bar(agg, x="월", y="건수", color="주기", barmode="stack", text="건수",
                 category_orders={"주기": order},
                 color_discrete_map=CYCLE_COLORS[theme_mode()],
                 title=f"월별 예정 {kind} 건수 (분배주기별)")
    fig.update_traces(textposition="inside", textfont_size=11,
                      marker_line_width=1, marker_line_color="rgba(0,0,0,0)")
    fig.update_layout(xaxis_title="", yaxis_title="건수", hovermode="x unified",
                      bargap=0.35, legend_title_text="주기")
    st.plotly_chart(fig, width="stretch")

    first, last = agg["월"].iloc[0], agg["월"].iloc[-1]
    st.caption(f"{first} ~ {last} · 총 {int(agg['건수'].sum()):,}건. "
               "**맨 앞·맨 뒤 달은 일부만 들어 있어** 낮게 보일 수 있습니다 "
               "(오늘부터 세고, 캘린더 파일도 6개월에서 끊깁니다).")

    st.dataframe(monthly_counts_wide(agg, order), width="stretch", hide_index=True)


def _run_nav_analysis(isin: str, ticker: str, period_label: str, force: bool) -> dict:
    """한 종목의 KRX NAV를 조회해 침식 지표까지 계산한다."""
    end_date = pd.Timestamp(date.today())
    start_date = end_date - pd.DateOffset(months=PERIOD_MONTHS[period_label])
    nav = fetch_nav_history(
        ticker, start_date.strftime("%Y%m%d"), end_date.strftime("%Y%m%d"),
        NAV_CACHE_DIR, force=force,
    )
    distributions = load_distributions_for_isin(RAW_DIR, isin)
    return {
        "nav": nav,
        "distributions": distributions,
        "metrics": calculate_nav_metrics(nav, distributions),
    }


def render_nav_detail(name: str, nav: pd.DataFrame,
                      distributions: pd.DataFrame, metrics: dict) -> None:
    """지표 5개 + 정규화 차트 + 기간 내 분배 이력 (두 탭이 함께 씀)."""
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("판정", metrics["status"])
    c2.metric("NAV 변화율", f'{metrics["nav_change_pct"]:+.2f}%')
    c3.metric("누적 분배금", f'{metrics["cumulative_distribution"]:,.0f}원')
    c4.metric("총수익률 대용치", f'{metrics["total_return_proxy_pct"]:+.2f}%')
    c5.metric("NAV 최대낙폭", f'{metrics["max_drawdown_pct"]:.2f}%')

    chart = nav[["NAV", "종가"]].copy()
    chart = chart / chart.iloc[0] * 100
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=chart.index, y=chart["NAV"], name="NAV (시작=100)"))
    fig.add_trace(go.Scatter(x=chart.index, y=chart["종가"], name="종가 (시작=100)"))
    fig.update_layout(
        title=f"{name} · NAV와 종가 추이",
        xaxis_title="날짜", yaxis_title="시작일=100", hovermode="x unified",
    )
    st.plotly_chart(fig, width="stretch")

    d1, d2 = st.columns(2)
    with d1:
        st.write("**분석 기간 지표**")
        summary = pd.DataFrame({
            "항목": ["기간", "시작 NAV", "종료 NAV", "NAV 변화율", "분배율", "총수익률 대용치"],
            "값": [
                f'{ymd(metrics["start_date"])} ~ {ymd(metrics["end_date"])}',
                f'{metrics["nav_start"]:,.2f}',
                f'{metrics["nav_end"]:,.2f}',
                f'{metrics["nav_change_pct"]:+.2f}%',
                f'{metrics["distribution_yield_pct"]:.2f}%',
                f'{metrics["total_return_proxy_pct"]:+.2f}%',
            ],
        })
        st.dataframe(summary, width="stretch", hide_index=True)
    with d2:
        st.write("**기간 내 분배 이력**")
        start = pd.Timestamp(metrics["start_date"])
        end = pd.Timestamp(metrics["end_date"])
        inside = distributions[distributions["지급기준일"].between(start, end, inclusive="both")]
        if inside.empty:
            st.write("기간 내 이익분배 기록이 없습니다.")
        else:
            shown = pd.DataFrame({
                "기준일": inside["지급기준일"].dt.date.map(ymd),
                "주당분배금": inside["주당분배금"],
            })
            st.dataframe(shown, width="stretch", hide_index=True)

    st.warning(
        "총수익률 대용치는 `(종료 NAV - 시작 NAV + 누적 분배금) / 시작 NAV`의 단순 계산이며 "
        "분배금 재투자·세금·보수·거래비용을 반영한 공식 총수익률이 아닙니다."
    )


def render_nav_erosion(view: pd.DataFrame) -> None:
    """종목 하나를 골라 NAV 침식을 자세히 본다."""
    st.subheader("📉 NAV 침식 분석 (종목 1개)")
    st.caption("KRX 공식 종가·NAV·괴리율과 세이브로 이익분배 이력을 결합합니다.")

    candidates = filter_valid_isin_rows(
        view.dropna(subset=["ISIN"]).drop_duplicates("ISIN").copy()
    )
    if candidates.empty:
        st.warning("현재 필터 조건에 분석 가능한 ETF가 없습니다.")
        return

    labels = candidates.set_index("ISIN")["종목명"].to_dict()
    tickers = candidates.set_index("ISIN")["KRX코드"].to_dict()
    selected_isin = st.selectbox(
        "ETF 선택", options=list(labels),
        format_func=lambda value: f"{labels[value]} ({tickers[value]})",
    )
    period_label = st.selectbox("분석 기간", list(PERIOD_MONTHS), index=2)
    force = st.checkbox("KRX 데이터 새로고침", value=False)

    if st.button("KRX NAV 조회·분석", type="primary"):
        try:
            with st.spinner("KRX NAV 데이터를 조회하고 있습니다..."):
                result = _run_nav_analysis(selected_isin, tickers[selected_isin],
                                           period_label, force)
            st.session_state["nav_analysis"] = {
                "isin": selected_isin, "period": period_label, **result,
            }
        except Exception as exc:  # UI 경계: 외부 라이브러리 상세는 사용자에게 노출하지 않음
            st.session_state.pop("nav_analysis", None)
            st.error(public_nav_error_message(exc))

    result = st.session_state.get("nav_analysis")
    if not result or result["isin"] != selected_isin or result["period"] != period_label:
        st.info("종목과 기간을 선택한 뒤 **KRX NAV 조회·분석**을 누르세요.")
        return

    render_nav_detail(labels[selected_isin], result["nav"],
                      result["distributions"], result["metrics"])


def sort_by_column(candidates: pd.DataFrame, column: str,
                   ascending: bool) -> pd.DataFrame:
    """숫자 칸으로 정렬. **값이 없는 종목은 항상 뒤로** 보낸다.

    오름차순에서 결측이 1등이 되면 '총보수가 제일 싼 것'을 물었는데 자료가
    없는 종목이 올라온다 — 5단계에서 잡았던 것과 같은 함정이다.
    """
    if not column or column not in candidates.columns:
        return candidates
    key = pd.to_numeric(candidates[column], errors="coerce")
    return candidates.assign(_key=key).sort_values(
        "_key", ascending=ascending, na_position="last").drop(columns="_key")


def sort_candidates(candidates: pd.DataFrame, order_label: str) -> pd.DataFrame:
    """비교표에서 먼저 조회할 순서로 정렬. 값이 없는 종목은 뒤로 보낸다."""
    column, ascending, _ = COMPARE_ORDER[order_label]
    return sort_by_column(candidates, column, ascending)


def render_nav_compare(view: pd.DataFrame) -> None:
    """여러 ETF의 침식 지표를 한 표로 비교하고, 행을 클릭하면 상세를 편다."""
    st.subheader("📊 NAV 비교표 (여러 종목)")

    candidates = filter_valid_isin_rows(
        view.dropna(subset=["ISIN"]).drop_duplicates("ISIN").copy()
    )
    if candidates.empty:
        st.warning("현재 필터 조건에 분석 가능한 ETF가 없습니다.")
        return

    st.markdown("**무엇부터 조회할까요?** — 기준을 눌러 고르세요.")
    order_label = st.pills(
        "조회 기준", list(COMPARE_ORDER), default="시총 큰 순",
        key="compare_order", label_visibility="collapsed",
        help="각 기준의 뜻은 아래 '❓ 기준이 무슨 뜻인가요?' 를 펼쳐 보세요.",
    ) or "시총 큰 순"

    with st.expander("❓ 기준이 무슨 뜻인가요?"):
        st.markdown("\n".join(
            f"- **{label}** — {desc}" for label, (_, _, desc) in COMPARE_ORDER.items()))
        st.caption("여기서 고른 기준은 **어떤 종목을 KRX에 조회할지 고르는 용도**입니다. "
                   "아래 결과표는 어느 열이든 머리글을 눌러 다시 정렬할 수 있습니다.")

    c1, c2, c3 = st.columns(3)
    count = c1.number_input("조회 종목 수", 1, 50,
                            value=min(10, len(candidates)), step=1,
                            help="KRX 조회가 종목당 1회씩 필요해 한 번에 최대 50개까지만 받습니다.")
    period_label = c2.selectbox("분석 기간", list(PERIOD_MONTHS), index=2,
                                key="compare_period")
    force = c3.checkbox("KRX 데이터 새로고침", value=False, key="compare_force")

    target = sort_candidates(candidates, order_label).head(int(count))
    st.caption(f"현재 필터 {len(candidates):,}개 중 **{order_label} {len(target)}개**를 조회합니다. "
               f"기준: {COMPARE_ORDER[order_label][2]}")
    st.caption("조회 대상: " + " · ".join(target["종목명"].head(5))
               + (" …" if len(target) > 5 else ""))

    if st.button("KRX 일괄 조회", type="primary", key="compare_run"):
        rows, failed = [], []
        progress = st.progress(0.0, text="KRX 조회 중...")
        for i, (_, r) in enumerate(target.iterrows(), start=1):
            try:
                result = _run_nav_analysis(r["ISIN"], r["KRX코드"], period_label, force)
                rows.append({"종목명": r["종목명"],
                             "주기": CYCLE_LABEL.get(r["분배주기"], r["분배주기"]),
                             "YTD": r.get("YTD수익률"), "1년": r.get("1년수익률"),
                             **result})
            except Exception:   # 한 종목 실패가 전체를 막지 않게
                failed.append(r["종목명"])
            progress.progress(i / len(target), text=f"KRX 조회 중... {i}/{len(target)}")
        progress.empty()
        st.session_state["nav_compare"] = {"period": period_label, "rows": rows,
                                           "failed": failed}

    compare = st.session_state.get("nav_compare")
    if not compare or not compare["rows"]:
        if compare and compare["failed"]:
            st.error(f"조회에 모두 실패했습니다: {', '.join(compare['failed'][:5])}")
        else:
            st.info("종목 수와 기간을 정한 뒤 **KRX 일괄 조회**를 누르세요.")
        return

    if compare["failed"]:
        st.warning(f"{len(compare['failed'])}개 종목은 조회하지 못해 표에서 빠졌습니다: "
                   f"{', '.join(compare['failed'][:5])}"
                   + (" 외" if len(compare["failed"]) > 5 else ""))

    table = pd.DataFrame([{
        "종목명": row["종목명"],
        "주기": row["주기"],
        "판정": row["metrics"]["status"],
        "NAV변화": row["metrics"]["nav_change_pct"],
        "분배율": row["metrics"]["distribution_yield_pct"],
        "총수익": row["metrics"]["total_return_proxy_pct"],
        "최대낙폭": row["metrics"]["max_drawdown_pct"],
        "YTD": row.get("YTD"),
        "1년": row.get("1년"),
    } for row in compare["rows"]])

    st.caption(f"{compare['period']} 기준 · {len(table):,}개 · "
               "**아무 칸이나 클릭하면 아래에 그 종목 상세**가 나옵니다(체크박스도 됩니다).")
    columns = pick_columns(
        "compare_columns",
        ["주기", "판정", "NAV변화", "분배율", "총수익", "최대낙폭", "YTD", "1년"],
        default=["주기", "NAV변화", "분배율", "총수익", "YTD", "1년"],
    )
    event = st.dataframe(
        table[columns], width="stretch", hide_index=True,
        on_select="rerun", selection_mode=["single-row", "single-cell"],
        key="nav_compare_table",
        column_config={
            "종목명": st.column_config.TextColumn(width="medium", pinned=True),
            "주기": st.column_config.TextColumn(width="small"),
            "판정": st.column_config.TextColumn(width="small",
                                               help="NAV 변화율 기준: 0%↑ 양호 / -10%↓ 침식주의"),
            "NAV변화": st.column_config.NumberColumn(width="small", format="%+.2f%%"),
            "분배율": st.column_config.NumberColumn(width="small", format="%.2f%%",
                                                   help="기간 누적 분배금 ÷ 시작 NAV"),
            "총수익": st.column_config.NumberColumn(width="small", format="%+.2f%%",
                                                   help="(NAV 증감 + 누적 분배금) ÷ 시작 NAV"),
            "최대낙폭": st.column_config.NumberColumn(width="small", format="%.2f%%"),
            "YTD": st.column_config.NumberColumn(
                width="small", format="%+.2f%%",
                help="올해 첫 개장일 대비 가격 변화율 (분배금 제외)"),
            "1년": st.column_config.NumberColumn(
                width="small", format="%+.2f%%",
                help="1년 전 대비 가격 변화율 (분배금 제외)"),
        },
    )

    picked = picked_row_index(event)
    if picked is None:
        st.info("표에서 종목 한 줄을 클릭하면 그 종목의 차트와 분배 이력이 여기에 나옵니다.")
        return
    st.divider()
    row = compare["rows"][picked]
    render_nav_detail(row["종목명"], row["nav"], row["distributions"], row["metrics"])


def render_portfolio(df: pd.DataFrame, events: pd.DataFrame) -> None:
    """내 보유종목 평가. 사이드바 필터와 무관하게 항상 전체 ETF 목록을 기준으로 본다."""
    st.subheader("💼 내 포트폴리오")

    holdings = st.session_state.get("holdings")
    if holdings is None:
        holdings = portfolio.load_holdings()
        st.session_state["holdings"] = holdings

    with st.expander("📷 계좌 사진으로 채우는 법", expanded=holdings.empty):
        st.markdown(
            "1. 증권사 앱의 **잔고 화면을 캡처**해서 클로드 코드 대화창에 올리세요.\n"
            "2. 이때 **어떤 계좌인지 알려주세요** — 일반(위탁)·연금저축·퇴직연금(IRP/DC)·ISA.\n"
            "   계좌유형에 따라 분배금 **원천징수 여부가 달라서** 세후 금액이 바뀝니다.\n"
            f"3. `{portfolio.HOLDINGS_CSV.relative_to(BASE)}` 로 만들어 드립니다.\n"
            "4. 이 화면의 표에서 직접 고치거나 줄을 추가해도 됩니다 (아래 **저장** 누르면 반영).\n\n"
            "**매수일**을 넣으면 그 뒤에 받은 분배금만 세고, 비우면 최근 12개월 전체로 계산합니다."
        )
        st.markdown("**계좌유형별 분배금 과세**")
        st.dataframe(
            pd.DataFrame([
                {"계좌유형": t, "받을 때": f"{w:.1%}", "최종": f"{f:.1%}", "설명": note}
                for t, (w, f, note) in portfolio.ACCOUNT_TAX.items()
            ]), width="stretch", hide_index=True,
            column_config={
                "계좌유형": st.column_config.TextColumn(width="small"),
                "받을 때": st.column_config.TextColumn(width="small", help="바로 떼는 원천징수"),
                "최종": st.column_config.TextColumn(width="small", help="결국 내게 되는 세율"),
                "설명": st.column_config.TextColumn(width="large"),
            })
        st.caption("⚠ 나이·인출방식·금융소득종합과세 여부에 따라 달라집니다. 참고용입니다.")

    edited = st.data_editor(
        holdings, num_rows="dynamic", width="stretch", hide_index=True,
        key="holdings_editor",
        column_config={
            # 8칸이 가로 스크롤 없이 다 보이도록 픽셀로 맞춘 너비.
            # 맨 앞 행선택 칸(약 40px)까지 감안한 값이다.
            "계좌": st.column_config.TextColumn(width=115, help="계좌 이름 (예: 미래에셋 연금)"),
            "계좌유형": st.column_config.SelectboxColumn(
                width=130, options=list(portfolio.ACCOUNT_TAX),
                default=portfolio.DEFAULT_ACCOUNT_TYPE,
                help="분배금 원천징수 여부가 달라집니다. 비우면 일반(위탁)으로 봅니다"),
            "종목명": st.column_config.TextColumn(width=250),
            "KRX코드": st.column_config.TextColumn(width=75,
                                                  help="비우면 종목명으로 찾습니다"),
            "수량": st.column_config.NumberColumn(width=60, format="%d"),
            "매수단가": st.column_config.NumberColumn(width=85, format="%.0f"),
            "매수일": st.column_config.DateColumn(width=100,
                                                 help="비우면 최근 12개월로 계산합니다"),
            # 메모는 길어서 다 못 보여준다. 칸이 화면 밖으로 나가지만 않게 한다
            "메모": st.column_config.TextColumn(width=150),
        },
    )
    c1, c2, c3 = st.columns([1, 1.4, 3])
    if c1.button("저장", type="primary", key="save_holdings"):
        path = portfolio.save_holdings(edited)
        st.session_state["holdings"] = portfolio.load_holdings()
        c3.success(f"저장했습니다 → {path.relative_to(BASE)}")
    if c2.button("🔄 시세 새로고침", key="refresh_prices"):
        try:
            prices, trade_date = portfolio.fetch_live_prices()
            st.session_state["live_prices"] = (prices, trade_date)
            c3.success(f"KRX에서 다시 받았습니다 (기준 {trade_date})")
        except Exception:
            c3.error("KRX 시세를 받지 못했습니다. 잠시 뒤 다시 눌러 주세요.")

    usable = edited.dropna(subset=["수량", "매수단가"])
    usable = usable[usable["종목명"].astype(str).str.strip() != ""]
    if usable.empty:
        st.info("보유종목을 넣으면 평가금액·평가손익·분배금 손익이 여기에 나옵니다.")
        return

    try:
        ev = portfolio.evaluate(usable, df)
    except Exception:
        st.error("평가에 실패했습니다. 수량·매수단가가 숫자인지, 종목명이 맞는지 확인해 주세요.")
        return

    live = st.session_state.get("live_prices")
    if live:
        prices, trade_date = live
        ev = portfolio.apply_live_prices(ev, prices)
        st.caption(f"🔄 **새로고침한 시세** 기준 (KRX {trade_date}). "
                   "KRX 제공값이라 실시간 체결가가 아니라 **약 20분 지연**입니다.")
    else:
        asof = ev["기준일자"].dropna().astype(str).iloc[0] if "기준일자" in ev.columns \
            and ev["기준일자"].notna().any() else ""
        st.caption(f"현재가 기준일 {asof or '미상'} — **직전 개장일 종가**입니다. "
                   "위 **🔄 시세 새로고침**을 누르면 지금 시점으로 다시 받아옵니다.")

    missing = ev[ev["현재가"].isna()]
    if not missing.empty:
        st.warning("현재가를 못 찾은 종목: " + ", ".join(missing["종목명"].head(5))
                   + " — 종목명이 정확한지 보시고, `python src/etf_info.py` 를 한 번 돌려 보세요.")

    conflicts = portfolio.account_conflicts(ev)
    if not conflicts.empty:
        st.error("**계좌유형으로는 못 사는 종목이 있습니다.** "
                 "연금저축·IRP/DC·ISA 계좌에서는 미국 직접 상장 ETF를 매수할 수 없습니다: "
                 + ", ".join(f"{r['종목명']}({r['계좌']})"
                             for _, r in conflicts.head(5).iterrows())
                 + " — 계좌나 종목코드를 잘못 넣으신 것 같습니다.")

    total = portfolio.summarize(ev)
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("평가금액", f"{total['평가금액']:,.0f}원",
              help=f"매수원금 {total['매수원금']:,.0f}원")
    k2.metric("평가손익", f"{total['평가손익']:+,.0f}원", f"{total['평가수익률']:+.2f}%")
    k3.metric("받은 분배금", f"{total['받은분배금']:,.0f}원",
              help=f"세전. 세후 {total['받은분배금_세후']:,.0f}원 "
                   "(매수일이 없는 종목은 최근 12개월 기준)")
    k4.metric("총손익 (분배금 포함)", f"{total['총손익']:+,.0f}원", f"{total['총수익률']:+.2f}%")

    k5, k6, k7, k8 = st.columns(4)
    k5.metric("분배금 회수율", f"{total['분배금회수율']:.2f}%",
              help="받은 분배금 ÷ 매수원금 — 원금을 얼마나 돌려받았나")
    k6.metric("절세 효과", f"{total['절세효과']:,.0f}원",
              help="연금·ISA 라서 원천징수를 안 당한 금액 "
                   "(일반 계좌였다면 15.4% 뗐을 돈). 나중에 인출할 때는 과세됩니다")
    k7.metric("연간 총보수", f"{total['연간보수']:,.0f}원",
              help="평가금액 × 총보수(%). 매년 빠져나가는 비용")
    k8.metric("종목 / 계좌", f"{total['종목수']}개 / {total['계좌수']}개")

    p1, p2, p3, p4, p5 = st.tabs(["📌 보유 현황", "💰 분배금 현금흐름", "⚖ 리밸런싱",
                                  "🧾 실현손익", "🧮 금융소득 2천만"])
    with p1:
        render_portfolio_holdings(ev, events)
    with p2:
        render_cashflow(ev)
    with p3:
        render_rebalance(ev)
    with p4:
        render_realized()
    with p5:
        render_fin_income(ev)

    st.caption("현재가는 KRX 제공 시세입니다(실시간 체결가 아님). "
               "분배금 세금은 계좌유형별 **보통의 경우**를 적용한 참고값입니다.")
    if "상장시장" in ev.columns and (ev["상장시장"] == "미국").any():
        st.caption("🇺🇸 **미국 상장 종목**은 야후 종가를 수집 당시 환율로 원화 환산한 값이라 "
                   "지금 환율과 다릅니다. 분배금은 현지에서 **15% 원천징수**된 것으로 계산했고, "
                   "국내에서 더 떼지는 않지만 금융소득종합과세 대상에는 들어갑니다. "
                   "매매차익은 **양도소득세 22%**(연 250만원 공제) 대상이라 여기 계산에 없습니다.")


def render_portfolio_holdings(ev: pd.DataFrame, events: pd.DataFrame) -> None:
    """보유 종목 상세 표 + 비중 도넛 + 다가오는 일정."""
    show = pd.DataFrame({
        "계좌": ev["계좌"],
        "유형": ev["계좌유형"],
        "종목명": ev["종목명"],
        "코드": ev["KRX코드"] if "KRX코드" in ev.columns else "",
        "수량": ev["수량"],
        "매수단가": ev["매수단가"],
        "현재가": ev["현재가"],
        "평가금액": ev["평가금액"],
        "평가손익": ev["평가손익"],
        "수익률": ev["평가수익률"],
        "분배금": ev["받은분배금"],
        "분배금(세후)": ev["받은분배금_세후"],
        "총수익률": ev["총수익률"],
        "YoC(매수가대비)": ev["매수가대비분배율"],
        "현재가대비": ev["현재가대비분배율"],
        "비중": ev["비중"],
    })
    columns = pick_columns(
        "portfolio_columns",
        ["계좌", "유형", "수량", "매수단가", "현재가", "평가금액", "평가손익", "수익률",
         "분배금", "분배금(세후)", "총수익률", "YoC(매수가대비)", "현재가대비", "비중"],
        default=["계좌", "수량", "현재가", "평가금액", "평가손익", "수익률", "총수익률"],
        fixed=["코드"],
    )
    st.dataframe(
        show[columns], width="stretch", hide_index=True,
        column_config={
            # 너비는 픽셀로 직접 준다. "small"(75px)은 한글 값이 잘려서 못 쓴다.
            # 기본 9칸 합계가 화면 폭 안에 들어가도록 맞춘 값이다.
            "종목명": st.column_config.TextColumn(width=250, pinned=True),
            "코드": st.column_config.TextColumn(
                width=75, help="국내는 KRX 6자리, 미국은 티커"),
            "계좌": st.column_config.TextColumn(width=115),
            "유형": st.column_config.TextColumn(width=115, help="계좌유형 (과세 방식)"),
            "수량": st.column_config.NumberColumn(width=65, format="%d"),
            "매수단가": st.column_config.NumberColumn(width=85, format="%.0f"),
            "현재가": st.column_config.NumberColumn(width=80, format="%.0f",
                                                   help="KRX 제공 시세 (실시간 아님)"),
            "평가금액": st.column_config.NumberColumn(width=100, format="localized"),
            "평가손익": st.column_config.NumberColumn(width=100, format="localized"),
            "수익률": st.column_config.NumberColumn(width=80, format="%+.2f%%"),
            "분배금": st.column_config.NumberColumn(
                width=85, format="localized",
                help="세전 누적 분배금 (매수일 이후, 없으면 최근 12개월)"),
            "분배금(세후)": st.column_config.NumberColumn(
                width=100, format="localized",
                help="계좌유형별 원천징수를 뺀 금액. 연금·ISA 는 안 떼므로 세전과 같습니다"),
            "총수익률": st.column_config.NumberColumn(
                width=85, format="%+.2f%%", help="(평가손익 + 분배금) ÷ 매수원금"),
            "YoC(매수가대비)": st.column_config.NumberColumn(
                width=125, format="%.2f%%",
                help="YoC = Yield on Cost(매수원가 대비 수익률). "
                     "연 주당분배금 ÷ **내 매수단가**. 지금 사는 사람의 분배율이 아니라 "
                     "내 원가 기준이라, 오래 들고 분배금이 늘수록 올라갑니다"),
            "현재가대비": st.column_config.NumberColumn(
                width=90, format="%.2f%%",
                help="연 주당분배금 ÷ 현재가 — 지금 새로 사는 사람 기준 분배율"),
            "비중": st.column_config.NumberColumn(width=65, format="%.1f%%"),
        },
    )

    if (ev["기준"] == "최근 12개월(매수일 미입력)").any():
        st.caption("⚠ 매수일이 비어 있는 종목은 **최근 12개월 분배금을 다 받은 것으로** 계산했습니다. "
                   "실제보다 클 수 있으니 매수일을 넣어 주세요.")

    st.divider()
    b1, b2, b3 = st.columns(3)
    for column, container, title in [("투자지역", b1, "지역별"), ("테마섹터", b2, "섹터별"),
                                     ("계좌", b3, "계좌별")]:
        part = portfolio.breakdown(ev, column)
        with container:
            if part.empty:
                st.caption(f"{title} 자료 없음")
                continue
            fig = px.pie(part, names=column, values="평가금액", hole=0.5, title=f"{title} 비중")
            st.plotly_chart(fig, width="stretch")

    st.divider()
    st.write("**내 종목의 다가오는 일정**")
    if events.empty:
        st.caption("분배캘린더.ics 가 없습니다.")
    else:
        mine = events[events["종목명"].isin(set(ev["종목명"]))].copy()
        today = date.today()
        mine = mine[(mine["날짜"] >= today)
                    & (mine["날짜"] <= (pd.Timestamp(today) + pd.Timedelta(days=60)).date())]
        if mine.empty:
            st.caption("앞으로 60일 안에 예정된 일정이 없습니다.")
        else:
            per_share = ev.set_index("종목명")["최근_주당분배금"].to_dict()
            qty = ev.groupby("종목명")["수량"].sum().to_dict()
            # 세율은 종목이 든 계좌유형별로 다르다
            rate = {n: portfolio.account_tax_rate(t)
                    for n, t in zip(ev["종목명"], ev["계좌유형"])}
            mine["예상분배금"] = [
                (per_share.get(n) or 0) * (qty.get(n) or 0) for n in mine["종목명"]]
            mine["세후"] = [a * (1 - rate.get(n, portfolio.DIVIDEND_TAX_RATE))
                           for a, n in zip(mine["예상분배금"], mine["종목명"])]
            mine["날짜"] = mine["날짜"].map(ymd)
            st.dataframe(
                mine[["날짜", "종류", "종목명", "예상분배금", "세후"]],
                width="stretch", hide_index=True,
                column_config={
                    "날짜": st.column_config.TextColumn(width="small"),
                    "종류": st.column_config.TextColumn(width="small"),
                    "종목명": st.column_config.TextColumn(width="medium", pinned=True),
                    "예상분배금": st.column_config.NumberColumn(
                        width="small", format="localized",
                        help="직전 회차 주당분배금 × 내 수량 (세전, 추정)"),
                    "세후": st.column_config.NumberColumn(
                        width="small", format="localized",
                        help="계좌유형별 원천징수를 뺀 금액 "
                             "(연금·ISA 는 안 떼므로 세전과 같습니다)"),
                },
            )


def render_cashflow(ev: pd.DataFrame) -> None:
    """월별 분배금 — 실제 받은 것과 앞으로 받을 예상."""
    st.write("**월별 분배금 현금흐름**")
    months = st.slider("앞으로 몇 개월까지 볼까요?", 6, 24, 12, step=6, key="cashflow_months")

    future = portfolio.monthly_cashflow(ev, months)
    past = portfolio.received_by_month(ev, 12)
    if future.empty and past.empty:
        st.info("분배 이력이나 분배주기를 알 수 있는 종목이 없어 현금흐름을 그릴 수 없습니다.")
        return

    frames = []
    if not past.empty:
        frames.append(past.rename(columns={"받은금액": "금액"}).assign(구분="실제 받음"))
    if not future.empty:
        frames.append(future[["월", "세후"]].rename(columns={"세후": "금액"})
                      .assign(구분="예상(세후)"))
    flow = pd.concat(frames, ignore_index=True)

    fig = px.bar(flow, x="월", y="금액", color="구분", barmode="group",
                 title="월별 분배금 (실제 vs 예상)")
    fig.update_layout(xaxis_title="", yaxis_title="원", hovermode="x unified")
    st.plotly_chart(fig, width="stretch")

    c1, c2, c3 = st.columns(3)
    if not future.empty:
        c1.metric(f"앞으로 {months}개월 예상(세전)", f"{future['세전'].sum():,.0f}원")
        c2.metric(f"앞으로 {months}개월 예상(세후)", f"{future['세후'].sum():,.0f}원",
                  help="계좌유형별 원천징수 반영. 연금·ISA 는 지금 안 떼는 것뿐이고 "
                       "나중에 인출할 때 과세됩니다")
        monthly = future["세후"].sum() / max(months, 1)
        c3.metric("월 평균(세후)", f"{monthly:,.0f}원")

    st.dataframe(
        future.rename(columns={"세전": "예상(세전)", "세후": "예상(세후)"}),
        width="stretch", hide_index=True,
        column_config={
            "월": st.column_config.TextColumn(width="small"),
            "예상(세전)": st.column_config.NumberColumn(width="small", format="localized"),
            "예상(세후)": st.column_config.NumberColumn(width="small", format="localized"),
        },
    )
    st.caption("예상은 **직전 회차 주당분배금이 그대로 유지된다**고 보고, 직전 지급기준일에 "
               "분배주기를 더해가며 잡은 값입니다. 공시 전 추정이라 금액·날짜 모두 달라질 수 있습니다. "
               "위클리는 월 단위로 묶어서 표시합니다.")


def render_rebalance(ev: pd.DataFrame) -> None:
    """목표 비중 대비 얼마나 벌어졌는지, 얼마를 사고팔면 되는지."""
    st.write("**목표 비중 대비 리밸런싱**")
    c1, c2 = st.columns(2)
    basis = c1.selectbox("무엇을 기준으로 맞출까요?",
                         ["종목명", "투자지역", "테마섹터", "자산분류", "계좌"],
                         key="rebalance_basis")
    band = c2.slider("허용 오차(%p)", 1.0, 15.0, 5.0, step=0.5, key="rebalance_band",
                     help="이 범위 안이면 '유지'. 넘으면 '더 사기' 또는 '덜기'로 표시합니다")

    targets = st.session_state.get("targets")
    if targets is None or st.session_state.get("targets_basis") != basis:
        saved = portfolio.load_targets()
        current = portfolio.breakdown(ev, basis).rename(columns={basis: "구분"})
        if not saved.empty and set(saved["구분"]) & set(current["구분"]):
            targets = saved
        else:   # 저장된 게 없으면 현재 비중을 출발점으로
            targets = current[["구분", "비중"]].rename(columns={"비중": "목표비중"})
            targets["목표비중"] = targets["목표비중"].round(1)
        st.session_state["targets"] = targets
        st.session_state["targets_basis"] = basis

    edited = st.data_editor(
        st.session_state["targets"], num_rows="dynamic", width="stretch", hide_index=True,
        key=f"targets_editor_{basis}",
        column_config={
            "구분": st.column_config.TextColumn(width="medium"),
            "목표비중": st.column_config.NumberColumn(width="small", format="%.1f%%",
                                                     help="합이 100%가 되게 넣으세요"),
        },
    )
    total_target = pd.to_numeric(edited["목표비중"], errors="coerce").sum()
    b1, b2 = st.columns([1, 4])
    if b1.button("목표 저장", key="save_targets"):
        path = portfolio.save_targets(edited)
        st.session_state["targets"] = edited
        b2.success(f"저장했습니다 → {path.relative_to(BASE)}")
    if abs(total_target - 100) > 0.5:
        st.warning(f"목표 비중 합이 **{total_target:.1f}%** 입니다. 100%로 맞춰 주세요 "
                   "(합이 안 맞으면 조정금액이 틀어집니다).")

    plan = portfolio.rebalance(ev, edited, column=basis, band=band)
    if plan.empty:
        st.info("목표 비중을 넣으면 얼마를 사고팔면 되는지 계산해 드립니다.")
        return

    need = plan[plan["판정"] != "유지"]
    if need.empty:
        st.success(f"모두 허용 오차 ±{band:.1f}%p 안에 있습니다. 지금은 손댈 것 없습니다.")
    else:
        st.warning(f"**{len(need)}개 항목이 목표에서 벗어났습니다.** "
                   + " · ".join(f"{r['구분']} {r['차이']:+.1f}%p({r['판정']})"
                                for _, r in need.head(5).iterrows()))

    st.dataframe(
        plan, width="stretch", hide_index=True,
        column_config={
            "구분": st.column_config.TextColumn(width="medium", pinned=True),
            "목표비중": st.column_config.NumberColumn(width="small", format="%.1f%%"),
            "현재비중": st.column_config.NumberColumn(width="small", format="%.1f%%"),
            "차이": st.column_config.NumberColumn(width="small", format="%+.1f%%",
                                                 help="현재 − 목표. +면 너무 많다는 뜻"),
            "조정금액": st.column_config.NumberColumn(
                width="small", format="localized",
                help="+면 그만큼 더 사고, −면 그만큼 팔면 목표에 맞습니다"),
            "판정": st.column_config.TextColumn(width="small"),
        },
    )
    st.caption("조정금액은 **현재 평가금액 총액을 그대로 두고** 비중만 맞추는 기준입니다. "
               "세금·수수료·거래단위(1주 단위)는 고려하지 않았습니다.")


def render_fin_income(ev: pd.DataFrame) -> None:
    """금융소득종합과세 — 연 2,000만원 선까지 얼마나 남았나.

    지금까지 화면에는 "2,000만원을 넘으면 더 낼 수 있습니다"라는 **문구만** 있었다.
    숫자가 없으면 그 문구로는 아무 판단도 못 한다.
    """
    from src import fin_income

    st.write("**금융소득종합과세 — 연 2,000만원 선**")
    st.caption("연 금융소득(이자+배당)이 **2,000만원(세전)** 을 넘으면 넘는 만큼이 "
               "근로소득 등과 합산돼 누진세율을 맞습니다. "
               "**연금·ISA 계좌는 여기 안 들어갑니다** — 과세이연·분리과세라서요.")

    columns = st.columns(2)
    with columns[0]:
        other = st.number_input(
            "다른 금융소득 (원)", min_value=0, step=1_000_000, value=0,
            help="예금·적금 이자, 다른 증권사 배당, 펀드 분배금 등 **이 도구가 모르는** "
                 "금융소득. 0 으로 두면 실제보다 적게 나옵니다")
    with columns[1]:
        base = st.number_input(
            "다른 종합소득 과세표준 (원)", min_value=0, step=1_000_000, value=0,
            help="근로소득 등의 **과세표준**(수입이 아니라 공제 뒤 금액). "
                 "초과분에 걸리는 한계세율을 잡는 데만 씁니다")

    got = fin_income.summary(ev, other_income=other, other_taxable_base=base)

    k1, k2, k3 = st.columns(3)
    k1.metric("올해 받은 분배금", f"{got['올해_받은']:,.0f}원",
              help="일반(위탁) 계좌에서 올해 실제로 받은 세전 분배금")
    k2.metric("올해 남은 예상", f"{got['올해_예상']:,.0f}원",
              help="남은 달에 받을 것으로 보이는 세전 분배금(공시 전 추정)")
    k3.metric("연간 합계", f"{got['연간_합계']:,.0f}원",
              f"{got['소진율']:.0f}% 소진", delta_color="off")

    # 진행 막대 — 한눈에 '얼마나 찼나'
    st.progress(min(got["소진율"] / 100, 1.0))
    if got["넘었나"]:
        st.warning(
            f"⚠ 기준을 **{got['초과액']:,.0f}원 초과**할 것으로 보입니다. "
            f"초과분은 한계세율 **{got['한계세율'] * 100:.0f}%** 구간에 들어가고, "
            f"이미 낸 14% 를 빼면 대략 **{got['추가세금_어림']:,.0f}원**을 더 낼 수 있습니다.")
    else:
        st.success(f"✅ 기준까지 **{got['남은_여유']:,.0f}원** 남았습니다.")

    table = fin_income.by_account(ev)
    if not table.empty:
        st.write("**계좌별 — 무엇이 세어졌나**")
        st.dataframe(table, width="stretch", hide_index=True, column_config={
            "올해_받은": st.column_config.NumberColumn(
                "올해 받은", format="localized"),
            "합산대상": st.column_config.CheckboxColumn(
                "합산", help="체크된 계좌의 분배금만 2,000만원 계산에 들어갑니다. "
                            "연금·ISA 는 과세이연·분리과세라 빠집니다"),
        })

    st.caption("⚠ **분배금만 셉니다.** 예금 이자·다른 증권사 배당은 위 칸에 직접 넣어야 "
               "합니다. 매매차익은 국내주식형 ETF 면 비과세, 미국 상장분은 양도소득세로 "
               "따로 가므로 여기 없습니다. 미국 분배금은 현지 15% 원천징수와 별개로 "
               "**국내 금융소득에는 포함**됩니다.")
    st.caption("⚠ 추가세금은 **어림값**입니다. 실제는 비교과세(종합과세와 분리과세 중 "
               "큰 쪽)로 매겨지고 각종 공제에 따라 달라집니다. 세무 상담을 대신하지 않습니다.")


def render_realized() -> None:
    """매도한 것들의 실현손익 (평가손익과 구분)."""
    st.write("**실현손익 (실제로 팔아서 확정된 손익)**")
    st.caption(f"`{portfolio.TRADE_CSV.relative_to(BASE)}` 에 매매내역을 넣으면 계산합니다. "
               "거래내역 화면을 캡처해 대화창에 올리셔도 만들어 드립니다.")

    trades = st.session_state.get("trades")
    if trades is None:
        trades = portfolio.load_trades()
        st.session_state["trades"] = trades

    edited = st.data_editor(
        trades if not trades.empty else pd.DataFrame(columns=portfolio.TRADE_COLUMNS),
        num_rows="dynamic", width="stretch", hide_index=True, key="trades_editor",
        column_config={
            "계좌": st.column_config.TextColumn(width="small"),
            "종목명": st.column_config.TextColumn(width="medium"),
            "KRX코드": st.column_config.TextColumn(width="small"),
            "일자": st.column_config.DateColumn(width="small"),
            "구분": st.column_config.SelectboxColumn(width="small", options=["매수", "매도"]),
            "수량": st.column_config.NumberColumn(width="small", format="%d"),
            "단가": st.column_config.NumberColumn(width="small", format="%.0f"),
            "수수료": st.column_config.NumberColumn(width="small", format="%.0f"),
        },
    )
    c1, c2 = st.columns([1, 4])
    if c1.button("매매내역 저장", key="save_trades"):
        path = portfolio.save_trades(edited)
        st.session_state["trades"] = portfolio.load_trades()
        c2.success(f"저장했습니다 → {path.relative_to(BASE)}")

    ready = edited.dropna(subset=["일자", "수량", "단가"])
    ready = ready[ready["구분"].isin(["매수", "매도"])]
    if ready.empty:
        st.info("매수·매도 내역을 넣으면 **판 것만 골라** 실현손익을 계산합니다.")
        return

    ready = ready.copy()
    ready["일자"] = pd.to_datetime(ready["일자"], errors="coerce")
    ready["수수료"] = pd.to_numeric(ready["수수료"], errors="coerce").fillna(0.0)
    result = portfolio.realized_pnl(ready)
    if result.empty:
        st.info("아직 매도 내역이 없습니다. 팔기 전까지는 전부 평가손익(미실현)입니다.")
        return

    gain = result["실현손익"].sum()
    won = result[result["실현손익"] > 0]
    m1, m2, m3 = st.columns(3)
    m1.metric("실현손익 합계", f"{gain:+,.0f}원")
    m2.metric("매도 건수", f"{len(result)}건")
    m3.metric("이익 낸 비율", f"{len(won) / len(result) * 100:.0f}%",
              help=f"{len(won)}건 이익 / {len(result) - len(won)}건 손실")

    st.dataframe(
        result, width="stretch", hide_index=True,
        column_config={
            "일자": st.column_config.DateColumn(width="small"),
            "계좌": st.column_config.TextColumn(width="small"),
            "종목명": st.column_config.TextColumn(width="medium", pinned=True),
            "수량": st.column_config.NumberColumn(width="small", format="%d"),
            "매도단가": st.column_config.NumberColumn(width="small", format="%.0f"),
            "평균매수단가": st.column_config.NumberColumn(
                width="small", format="%.0f", help="이동평균법 — 매도 시점까지의 평균 매입가"),
            "실현손익": st.column_config.NumberColumn(width="small", format="localized"),
            "수익률": st.column_config.NumberColumn(width="small", format="%+.2f%%"),
        },
    )
    st.caption("**이동평균법**으로 계산했습니다(국내 증권사 대부분이 쓰는 방식). "
               "선입선출법과는 결과가 다를 수 있습니다. 국내 상장 ETF의 매매차익은 "
               "일반 계좌에서 **비과세**(국내주식형)이거나 배당소득세 대상(기타 ETF)으로 갈립니다.")


# ── 수급 (설정·환매) ──────────────────────────────────────
def render_flow(df: pd.DataFrame) -> None:
    """관심종목의 상장주식수(설정·환매) 흐름 — PRD v1.1 (docs/수급_PRD.md)."""
    from src.flow import fetch, metrics, signals
    from src.flow import long_shares as flow_long

    st.subheader("수급 — 설정·환매(상장주식수) 흐름")
    st.caption("ETF 는 자금이 들어오면 **설정**으로 상장주식수가 늘고, 빠지면 **환매**로 "
               "줄어듭니다. 가격과 달리 자금 흐름을 직접 보여줍니다. "
               "판단 재료일 뿐 매수·매도 지시가 아닙니다.")

    # ── 관심종목 관리 ────────────────────────────────
    watch = fetch.load_watchlist()
    before = set(watch["종목코드"])
    columns = st.columns([2, 1])
    with columns[0]:
        new_code = st.text_input("종목코드 추가 (국내 6자리)", key="flow_add",
                                 placeholder="예: 069500").strip()
    with columns[1]:
        selected = set(st.multiselect("관심종목 (빼면 삭제)", list(before),
                                      default=list(before)))
    if new_code:
        if new_code.isdigit() and len(new_code) == 6:
            if new_code not in before:
                watch = pd.concat(
                    [watch, pd.DataFrame([{"종목코드": new_code, "종목명": ""}])],
                    ignore_index=True)
            selected.add(new_code)
        else:
            st.warning("종목코드는 숫자 6자리입니다. (미국 티커는 수급 대상이 아닙니다)")
    watch = watch[watch["종목코드"].isin(selected)].reset_index(drop=True)
    if set(watch["종목코드"]) != before:
        fetch.save_watchlist(watch)

    if watch.empty:
        st.info("관심종목이 없습니다. 위에 종목코드를 추가하세요.")
        return

    # ── 기간·갱신 ────────────────────────────────────
    columns = st.columns([1, 1, 2])
    with columns[0]:
        period = st.selectbox("기간(거래일)", [20, 60, 120, 200, 490], index=1)
    with columns[1]:
        refresh = st.button("데이터 받기/갱신", key="flow_refresh")
    have = fetch.cached_days()
    if refresh:
        with st.spinner("KRX Open API 에서 받는 중…"):
            got = fetch.ensure_days(period)
        st.success(f"새로 {got}일 받음 (캐시 {len(fetch.cached_days())}일)")
        have = fetch.cached_days()
    if not have:
        st.info("캐시가 비어 있습니다. [데이터 받기/갱신] 또는 "
                "`python src/flow/fetch.py 60` 을 실행하세요.")
        return
    if len(have) < period:
        st.caption(f"⚠ 캐시가 {len(have)}일치라 요청 기간({period}일)보다 짧습니다 — "
                   "있는 만큼만 계산합니다. [데이터 받기/갱신]으로 채울 수 있습니다.")

    # ── 종목명·분배율 붙이기 (분배ETF목록에서) ────────
    catalog = df.set_index(df["종목코드"].astype(str).str.strip()) \
        if "종목코드" in df.columns else pd.DataFrame()

    # ── 장기 상장주식수 (분배 이력에서 되짚은 2년치) ──
    # 날 단위 캐시는 쌓기 시작한 날부터라 짧다. 그 앞 구간을 분배 이력으로 메운다.
    long_shares = load_long_shares()

    # ── 요약 표 + 플래그 ─────────────────────────────
    rows, histories = [], {}
    for code in watch["종목코드"]:
        history = fetch.shares_history(code, period)
        histories[code] = history
        long_growth = flow_long.growth(long_shares, code, months=24)
        if history.empty:
            # 일별 캐시에 없어도 분배 이력으로 되짚은 값은 넣는다 (있으면)
            rows.append({
                "코드": code, "종목명": "(일별 캐시에 없음)", "데이터일수": 0,
                "2년 증감(%)": (round(long_growth["증감률"], 1)
                                if long_growth["증감률"] is not None else None),
                "2년 점": long_growth["점개수"] or None,
            })
            continue
        summary = metrics.summarize(pd.DataFrame(), history, period)
        name = str(history["종목명"].iloc[-1]) if "종목명" in history.columns else code
        rows.append({
            "코드": code, "종목명": name,
            "20일 증감(%)": round(summary["상장주식수_증감률_20일"], 2),
            "60일 증감(%)": round(summary["상장주식수_증감률_60일"], 2),
            # 2년 증감은 분배가 있던 날에만 점이 찍힌다 → 점 개수를 같이 보여준다
            "2년 증감(%)": (round(long_growth["증감률"], 1)
                            if long_growth["증감률"] is not None else None),
            "2년 점": long_growth["점개수"] or None,
            "연속증가일": summary["연속증가일"],
            "일평균거래대금(억)": round(summary["일평균거래대금"] / 1e8, 1),
            "데이터일수": summary["데이터일수"],
            "_이탈": signals.outflow(summary),
            "_유입": signals.inflow(summary),
        })
    table = pd.DataFrame(rows)

    # ⚠ 분배율 함정 — 전체 목록의 연간분배율과 교차 (연결 지점은 함수 하나)
    if not catalog.empty and "연간분배율" in catalog.columns:
        traps = signals.flag_distribution_trap(pd.DataFrame({
            "종목코드": catalog.index,
            "연환산 분배율": pd.to_numeric(catalog["연간분배율"], errors="coerce"),
            "이탈": [bool(table.set_index("코드")["_이탈"].get(c, False))
                     for c in catalog.index],
        }))
        trap_codes = set(traps[traps["함정"]]["종목코드"])
        table["플래그"] = table.apply(
            lambda r: " ".join(filter(None, [
                signals.OUTFLOW if r.get("_이탈") else "",
                signals.INFLOW if r.get("_유입") else "",
                signals.TRAP if r["코드"] in trap_codes else ""])), axis=1)
    else:
        table["플래그"] = table.apply(
            lambda r: " ".join(filter(None, [
                signals.OUTFLOW if r.get("_이탈") else "",
                signals.INFLOW if r.get("_유입") else "",
                signals.NO_YIELD])), axis=1)

    show = table.drop(columns=[c for c in table.columns if c.startswith("_")])
    st.dataframe(show, width="stretch", hide_index=True, column_config={
        "20일 증감(%)": st.column_config.NumberColumn(format="%+.2f%%"),
        "60일 증감(%)": st.column_config.NumberColumn(format="%+.2f%%"),
        "2년 증감(%)": st.column_config.NumberColumn(
            format="%+.1f%%",
            help="분배 이력에서 되짚은 장기 상장주식수 증감 "
                 "(총분배금 ÷ 주당분배금 = 그 분배를 받을 권리가 있던 주식 수). "
                 "KRX 조회가 아니라 **분배가 있던 날에만 점이 찍힌다** — "
                 "옆의 '2년 점' 이 그 점 개수다. 점이 2개 미만이면 비어 있다."),
        "2년 점": st.column_config.NumberColumn(
            format="%d",
            help="2년 증감을 낸 근거 점 개수. 연 1회 분배 종목은 2~3개뿐이라 "
                 "추세가 아니라 두 시점의 비교로만 읽어야 한다."),
        "플래그": st.column_config.TextColumn(
            help="🔴 이탈 = 20일·60일 모두 상장주식수 감소(환매 우위 지속) / "
                 "🟢 유입 = 5일 연속 + 20일 증가 / "
                 "⚠ 함정 = 분배율 상위 30%인데 🔴 — 규칙: docs/수급_PRD.md"),
    })

    # ── 상세 차트 ────────────────────────────────────
    pick = st.selectbox("상세 볼 종목", list(watch["종목코드"]),
                        format_func=lambda c: f"{c} " + str(
                            table.set_index("코드")["종목명"].get(c, "")))
    history = histories.get(pick, pd.DataFrame())
    if history.empty:
        # 일별 캐시에 없어도 **분배 이력으로 되짚은 장기 자료는 있을 수 있다.**
        # 여기서 return 하면 그걸 못 보고 지나친다.
        st.info("이 종목은 일별 캐시에 없습니다. 상장 폐지됐거나 코드가 틀렸을 수 "
                "있습니다. 분배 이력이 있으면 아래 장기 흐름만 보여 줍니다.")
    else:
        # ── HTS 스타일 — 주가 + 투자자별 매도(◀)/매수(▶) 가로 막대 ──
        # (전에는 상장주식수·거래대금 차트였는데, 사용자가 HTS 처럼 투자자별
        #  수급을 보고 싶다고 해서 바꿨다. 41단계)
        c1, c2 = st.columns(2)
        inv_start = c1.date_input(
            "시작일", value=(pd.Timestamp.today() - pd.DateOffset(months=1)).date(),
            key="inv_start")
        inv_end = c2.date_input("종료일", value=date.today(), key="inv_end")

        price = history[(history.index >= pd.Timestamp(inv_start))
                        & (history.index <= pd.Timestamp(inv_end))]
        # KIS 는 KRX 단축코드(숫자·영숫자 6자리)로 조회한다. 미국 티커는 대상 아님
        code = str(pick).strip()
        is_domestic = len(code) == 6 and not code.isalpha()

        col_price, col_inv = st.columns([1.35, 1])
        with col_price:
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=price.index, y=price["종가"],
                                     name="종가", line=dict(width=2)))
            fig.update_layout(height=420, margin=dict(l=10, r=10, t=30, b=10),
                              yaxis_title="종가(원)",
                              legend=dict(orientation="h", y=1.08))
            st.plotly_chart(fig, width="stretch")
        with col_inv:
            # 화면이 다시 그려질 때마다 API 를 부르지 않게 **버튼으로만** 조회
            state_key = (f"inv_{pick}_{pd.Timestamp(inv_start):%Y%m%d}"
                         f"_{pd.Timestamp(inv_end):%Y%m%d}")
            if is_domestic and st.button("투자자별 거래 조회 (한투 API)",
                                         key="inv_fetch"):
                try:
                    with st.spinner("한투 API 투자자별 거래 조회 중..."):
                        st.session_state[state_key] = investor_flow.fetch(
                            code, pd.Timestamp(inv_start).strftime("%Y%m%d"),
                            pd.Timestamp(inv_end).strftime("%Y%m%d"))
                except Exception as exc:
                    st.error("투자자별 거래를 못 받았습니다 — .env 의 "
                             "KIS_APP_KEY/KIS_APP_SECRET 과 네트워크를 확인하세요. "
                             f"(원인: {type(exc).__name__})")
            investors = st.session_state.get(state_key, pd.DataFrame())
            if not investors.empty:
                # HTS 순매매 화면처럼 왼쪽이 매도, 오른쪽이 매수다
                order = investors["투자자"].tolist()[::-1]   # 금융투자가 맨 위
                inv = investors.set_index("투자자").reindex(order).reset_index()
                fig_inv = go.Figure()
                fig_inv.add_trace(go.Bar(
                    y=inv["투자자"], x=-inv["매도"], orientation="h",
                    name="매도", marker_color="#a14b3a"))
                fig_inv.add_trace(go.Bar(
                    y=inv["투자자"], x=inv["매수"], orientation="h",
                    name="매수", marker_color="#2c4a73"))
                fig_inv.update_layout(
                    height=420, margin=dict(l=10, r=10, t=30, b=10),
                    barmode="overlay", xaxis_title="거래량(주) — ◀ 매도 · 매수 ▶",
                    legend=dict(orientation="h", y=1.08))
                fig_inv.update_xaxes(tickformat=",.0f",
                                     tickvals=None, zeroline=True, zerolinewidth=1)
                st.plotly_chart(fig_inv, width="stretch")
            elif is_domestic and state_key in st.session_state:
                st.info("이 기간의 투자자별 거래 자료가 없습니다.")
            elif is_domestic:
                st.info("위 버튼을 누르면 이 기간의 투자자별 매도·매수를 "
                        "한투 API 에서 받아 옵니다.")
            else:
                st.info("미국 상장 종목은 국내 투자자별 거래 자료가 없습니다.")
        st.caption(f"주가: KRX Open API 일별 캐시({len(price)}일치, 캐시에 있는 "
                   "날짜만). 투자자별 거래량: 한투 API [0416] 종목별 투자자매매동향 — "
                   "기간 **합계**(주 수량)이고, 기관은 금융투자·보험·투신·사모·은행·"
                   "종금·기금으로 분해됩니다. 순매수 = 매수 − 매도.")

    # ── 장기 흐름 (분배 이력에서 되짚은 2년치) ────────
    long_series = flow_long.series(long_shares, pick)
    if long_series.empty:
        st.caption("장기 상장주식수: 이 종목은 분배 이력이 없어 되짚을 수 없습니다.")
        return
    long_fig = go.Figure()
    long_fig.add_trace(go.Scatter(
        x=long_series.index, y=long_series["상장주식수"],
        name="상장주식수(분배일)", mode="lines+markers",
        line=dict(width=2), marker=dict(size=7)))
    long_fig.update_layout(
        height=320, margin=dict(l=10, r=10, t=30, b=10),
        yaxis_title="상장주식수(주)",
        legend=dict(orientation="h", y=1.12))
    st.plotly_chart(long_fig, width="stretch")

    span = flow_long.growth(long_shares, pick, months=24)
    if span["증감률"] is not None:
        st.caption(
            f"되짚은 점 {span['점개수']}개 "
            f"({span['시작일']:%y-%m-%d} → {span['끝일']:%y-%m-%d}) — "
            f"{span['시작']:,}주 → {span['끝']:,}주 "
            f"(**{span['증감률']:+.1f}%**). "
            "**KRX 를 새로 조회한 값이 아니다.** 세이브로 분배 이력의 "
            "`총분배금 ÷ 주당분배금` 으로 되짚은 것이라 **분배가 있던 날에만 점이 있다**. "
            "위 일별 차트와 겹쳐 봐도 어긋나지 않게 지급기준일 다음 개장일에 찍었다 "
            "(캐시와 겹치는 826건 전부 일치 확인).")
    else:
        st.caption(f"되짚은 점이 {span['점개수']}개뿐이라 증감을 내지 않습니다 "
                   "(연 1회 분배 종목은 점이 적습니다).")


# ── 시장국면 ──────────────────────────────────────────────
def render_market_regime() -> None:
    """반도체 현물가·수출·금리를 겹쳐 보는 거시 탭. 종목 필터와 무관하다."""
    from plotly.subplots import make_subplots
    from src import market_regime as mr

    st.subheader("시장국면 — 반도체 현물가 · 수출 · 국고채 10년")
    st.caption("⚠ **상관은 인과가 아닙니다.** 함께 움직였다는 사실만 보여줍니다 — "
               "둘 다 같은 것(AI 투자 사이클 → 수출 호조 → 성장·물가 기대)에 반응한 "
               "결과일 수 있습니다. 매수·매도 신호가 아니라 국면을 읽는 참고 자료입니다.")

    # ── ① DRAM 현물가 ↔ 국고채 10년 (일별) ──────────────
    daily = mr.dram_vs_rate()
    if daily.empty:
        st.info("반도체 현물가 자료가 아직 없습니다. "
                "`data/reference/블룸버그_시계열.csv` 또는 `python src/ddr5.py` 를 확인하세요.")
    else:
        # 그래프는 2025년 이후만 — 이전 이력은 지우지 않고 상관계수 계산에만 쓴다.
        # DDR5 는 수집은 계속하지만 그래프에서 뺐다(DDR4 와 다른 물건이라 겹치면
        # 축만 어지럽다는 사용자 판단, 41단계)
        shown_daily = daily[daily["날짜"] >= pd.Timestamp("2025-01-01")]
        if shown_daily.empty:
            shown_daily = daily
        fig = make_subplots(specs=[[{"secondary_y": True}]])
        if shown_daily["DRAM_DDR4"].notna().any():
            fig.add_trace(go.Scatter(
                x=shown_daily["날짜"], y=shown_daily["DRAM_DDR4"],
                name="DDR4 8Gb 현물($)",
                line=dict(width=1.6)), secondary_y=False)
        fig.add_trace(go.Scatter(
            x=shown_daily["날짜"], y=shown_daily["금리"], name="국고채 10년(%)",
            line=dict(width=1.6, dash="dot")), secondary_y=True)
        fig.update_yaxes(title_text="DRAM 현물가 ($)", secondary_y=False)
        fig.update_yaxes(title_text="국고채 10년 (%)", secondary_y=True)
        fig.update_layout(height=430, margin=dict(l=10, r=10, t=30, b=10),
                          legend=dict(orientation="h", y=1.08))
        st.plotly_chart(fig, width="stretch")

        # 상관계수 — 전체 구간과 최근 1년을 나란히 (구간을 좁히면 부풀 수 있다)
        recent = daily[daily["날짜"] >= daily["날짜"].max() - pd.DateOffset(years=1)]
        full_r = mr.correlation(daily, "DRAM_DDR4", "금리")
        year_r = mr.correlation(recent, "DRAM_DDR4", "금리")
        if full_r is not None:
            st.caption(f"상관계수(DDR4↔금리): 전체 {full_r:+.3f} / "
                       f"최근 1년 {year_r:+.3f}" if year_r is not None else
                       f"상관계수(DDR4↔금리): 전체 {full_r:+.3f}")
        st.caption("자료: 블룸버그 수기 추출(2023-10~, DDR4 8Gb) + 한국은행 ECOS — "
                   "**그래프는 2025년 이후만** 보여줍니다(이력은 보존, 상관계수는 "
                   "전체 구간으로 계산). DDR5 16Gb 는 수집은 계속하지만(평일 18시 "
                   "자동) DDR4 와 다른 물건이라 그래프에서는 뺐습니다.")

    st.divider()

    # ── ② 한국 수출 ↔ 국고채 10년 (월별) ────────────────
    monthly = mr.exports_vs_rate()
    if monthly.empty:
        st.info("수출 자료가 아직 없습니다. `python src/ecos.py` 를 먼저 실행하세요.")
        return
    # 그래프는 2025년 이후만 — 이전 이력은 지우지 않고 상관계수 등 계산에만 쓴다
    shown = monthly[monthly["월"] >= pd.Timestamp("2025-01-01")]
    if shown.empty:
        shown = monthly     # 2025년 이후 자료가 아예 없으면 전체를 보여준다
    fig2 = make_subplots(specs=[[{"secondary_y": True}]])
    fig2.add_trace(go.Bar(
        x=shown["월"], y=shown["수출금액"] / 1e6,      # 천달러 → 십억달러
        name="수출금액(십억$)", opacity=0.55), secondary_y=False)
    fig2.add_trace(go.Scatter(
        x=shown["월"], y=shown["금리"], name="국고채 10년(%, 월평균)",
        line=dict(width=2)), secondary_y=True)
    fig2.update_yaxes(title_text="수출금액 (십억$)", secondary_y=False)
    fig2.update_yaxes(title_text="국고채 10년 (%)", secondary_y=True)
    fig2.update_layout(height=400, margin=dict(l=10, r=10, t=30, b=10),
                       legend=dict(orientation="h", y=1.08))
    st.plotly_chart(fig2, width="stretch")

    months = len(monthly)
    st.caption(f"자료: 한국은행 ECOS 통관 수출금액(월별) — 현재 {months}개월치 중 "
               f"**그래프는 2025년 이후 {len(shown)}개월만** 보여줍니다(이력은 보존). "
               "`python src/ecos.py 5y` 처럼 기간을 늘려 돌리면 이력이 길어집니다. "
               "금리는 일별을 월평균으로 눌러 맞췄습니다.")

    st.divider()

    # ── ③ 투자자예탁금 (월별) — 역사적 평균 대비 지금 어디쯤인가 ──
    dep = mr.deposits()
    if dep.empty:
        st.info("예탁금 자료가 아직 없습니다. `python src/ecos.py` 를 먼저 실행하세요.")
        return
    # 기간 선택 — 두 예탁금 차트에 같이 걸린다. 종료 기본값은 오늘,
    # 이동평균은 **전체 이력**으로 계산한 뒤 표시만 자른다(구간을 좁혀도 안 흔들리게)
    c1, c2 = st.columns(2)
    dep_start = c1.date_input("시작", value=dep["월"].min().date(),
                              min_value=dep["월"].min().date(), key="dep_start")
    dep_end = c2.date_input("종료", value=date.today(), key="dep_end")
    dep_shown = dep[(dep["월"] >= pd.Timestamp(dep_start))
                    & (dep["월"] <= pd.Timestamp(dep_end))]
    if dep_shown.empty:
        dep_shown = dep

    MA_DASH = {3: "dot", 6: "dash", 12: "longdash"}
    fig3 = go.Figure()
    fig3.add_trace(go.Scatter(
        x=dep_shown["월"], y=dep_shown["예탁금_조원"], name="투자자예탁금(조원)",
        line=dict(width=2)))
    for window, dash in MA_DASH.items():
        column = f"이동평균{window}_조원"
        if column in dep_shown.columns and dep_shown[column].notna().any():
            fig3.add_trace(go.Scatter(
                x=dep_shown["월"], y=dep_shown[column], name=f"{window}개월 이동평균",
                line=dict(width=1.4, dash=dash)))
    average = float(dep["평균_조원"].iloc[0])
    fig3.add_hline(y=average, line_dash="dash", line_width=1,
                   annotation_text=f"기간 평균 {average:,.0f}조원",
                   annotation_position="bottom right")
    fig3.update_yaxes(title_text="투자자예탁금 (조원)")
    fig3.update_xaxes(rangeslider_visible=True)   # 아래 미니 차트를 끌어서 확대
    fig3.update_layout(height=430, margin=dict(l=10, r=10, t=30, b=10),
                       legend=dict(orientation="h", y=1.08))
    st.plotly_chart(fig3, width="stretch")

    latest = dep.iloc[-1]
    gap = (latest["예탁금_조원"] / average - 1) * 100
    st.caption(f"자료: 한국은행 ECOS 증시주변자금동향(월별) — 현재 {len(dep)}개월치. "
               f"최신 {latest['월']:%Y-%m} = **{latest['예탁금_조원']:,.0f}조원**, "
               f"기간 평균 대비 **{gap:+.0f}%**.")

    # ── ③-2 예탁금/시총 비중 — 돈이 많아진 건지 시장이 커진 건지 가른다 ──
    ratio = mr.deposit_ratio()
    if not ratio.empty:
        denominator = str(ratio["분모시장"].iloc[0])
        ratio_shown = ratio[(ratio["월"] >= pd.Timestamp(dep_start))
                            & (ratio["월"] <= pd.Timestamp(dep_end))]
        if ratio_shown.empty:
            ratio_shown = ratio
        fig4 = go.Figure()
        fig4.add_trace(go.Scatter(
            x=ratio_shown["월"], y=ratio_shown["비중"],
            name=f"예탁금/{denominator} 시총(%)",
            line=dict(width=2)))
        for window, dash in MA_DASH.items():
            column = f"이동평균{window}"
            if column in ratio_shown.columns and ratio_shown[column].notna().any():
                fig4.add_trace(go.Scatter(
                    x=ratio_shown["월"], y=ratio_shown[column],
                    name=f"{window}개월 이동평균",
                    line=dict(width=1.4, dash=dash)))
        ratio_average = float(ratio["평균"].iloc[0])
        fig4.add_hline(y=ratio_average, line_dash="dash", line_width=1,
                       annotation_text=f"기간 평균 {ratio_average:.2f}%",
                       annotation_position="bottom right")
        fig4.update_yaxes(title_text=f"예탁금 ÷ {denominator} 시총 (%)")
        fig4.update_xaxes(rangeslider_visible=True)
        fig4.update_layout(height=430, margin=dict(l=10, r=10, t=30, b=10),
                           legend=dict(orientation="h", y=1.08))
        st.plotly_chart(fig4, width="stretch")
        last_ratio = ratio.iloc[-1]
        note = ("" if denominator == "KOSPI+KOSDAQ" else
                " ⚠ 코스닥 시총 이력이 없어 분모가 KOSPI 뿐입니다 — "
                "`python src/krx_api.py` 로 채워지면 합산으로 바뀝니다.")
        st.caption(f"최신 {last_ratio['월']:%Y-%m} = **{last_ratio['비중']:.2f}%** "
                   f"(평균 {ratio_average:.2f}%, 분모 {denominator}). "
                   "예탁금이 늘어도 시장이 더 커지면 이 비중은 내려갑니다. "
                   f"갱신: `python src/krx_api.py`{note}")

    st.caption("예탁금 절대액의 평균선은 쌓인 구간의 평균입니다 — 이력이 길수록"
               "(현재 1998년~) '역사적 평균'에 가깝습니다.")


def apply_fx_scenario(planned: pd.DataFrame) -> tuple[pd.DataFrame, float]:
    """환율 시나리오 — 미국 종목 원화 금액을 다른 환율로 바꿔 본다.

    돌려주는 값: (다시 계산한 표, 적용한 환율). 미국 종목이 없으면 그대로 둔다.
    """
    base = watch.base_rate()
    has_us = (planned["시장"].astype(str).str.strip() == "미국").any() \
        if "시장" in planned.columns else False
    if not base or not has_us:
        return planned, base

    with st.expander(f"💱 환율 시나리오 (지금 적용 {base:,.0f}원)", expanded=False):
        st.caption("미국 종목의 원화 금액은 **수집 당시 환율**로 환산해 둔 값입니다. "
                   "환율이 오르면 원화로 받는 분배금도 그만큼 늘지만, **살 때 원금도 같이 "
                   "늘어서 분배율(%)은 그대로**입니다. 여기서 바꾸는 건 원화 금액뿐입니다.")
        c1, c2 = st.columns([1, 1.6])
        if c1.button("지금 환율 받기", key="watch_fx_fetch"):
            try:
                from src import us_etf
                live = us_etf.fetch_usdkrw()
                if live:
                    st.session_state["watch_fx"] = float(live)
                    c2.success(f"현재 {live:,.2f}원 (수집 시점 대비 "
                               f"{(live / base - 1) * 100:+.2f}%)")
                else:
                    c2.error("환율을 받지 못했습니다.")
            except Exception:
                c2.error("환율을 받지 못했습니다. 잠시 뒤 다시 눌러 주세요.")

        current = float(st.session_state.get("watch_fx", base))
        low, high = int(base * 0.75), int(base * 1.25)
        rate = st.slider("원/달러 환율", low, high, int(round(current)), step=5,
                         key="watch_fx_slider",
                         help="가정해 볼 환율. 수집 시점 환율에서 ±25% 범위입니다")
        gap = (rate / base - 1) * 100
        st.caption(f"수집 시점 {base:,.0f}원 → 가정 **{rate:,}원** ({gap:+.1f}%). "
                   "국내 상장 종목은 영향받지 않습니다.")

        # 29단계가 남긴 것 — 위 표는 "가정 환율로 산다"라 분배율이 그대로지만,
        # **사고 난 뒤** 환율이 움직이면 원금에 환차손익이 생긴다. 그 총효과.
        effect = watch.fx_total_return(planned, rate, base)
        if effect:
            st.markdown("**산 뒤에 환율이 움직이면** — 원금은 오늘 환율로 이미 "
                        "나갔으므로 환차손익이 생깁니다. 미국 종목만, 가격 변동은 "
                        "없다고 가정, 분배금은 세후입니다.")
            m1, m2, m3 = st.columns(3)
            m1.metric("원금 환차손익", f"{effect['환차손익']:+,.0f}원",
                      help=f"미국 종목 투자금 {effect['투자금액']:,.0f}원 × "
                           f"환율 변화 {gap:+.1f}%")
            m2.metric("연 분배금(세후)", f"{effect['연분배금_후']:,.0f}원",
                      delta=f"{effect['연분배금_후'] - effect['연분배금_전']:+,.0f}원",
                      help="새 환율로 받는 1년치 분배금과 그 변화")
            m3.metric("1년 총효과", f"{effect['총효과']:+,.0f}원",
                      help=f"환차손익 + 연 분배금. 환율이 그대로면 "
                           f"{effect['총효과_환율_그대로']:+,.0f}원(분배금뿐)입니다. "
                           "환율을 빼고도 남는 게 있는지 이 둘을 견주세요.")
    return watch.apply_fx(planned, rate, base), float(rate)


def render_watch_plan(df: pd.DataFrame) -> None:
    """관심종목을 '사면 얼마가 들어오나' — 배당 캘린더·현금흐름 시뮬레이션.

    사이드바 필터와 무관하게 전체 목록(df)에서 찾는다.
    """
    st.subheader("⭐ 관심종목 — 사면 얼마가 들어오나")
    st.caption("아직 안 산 ETF를 넣고 수량이나 투자금액을 적으면, **직전 12개월 분배가 "
               "그대로 반복된다고 가정**하고 1년 현금흐름을 계산합니다. "
               "종목 목록은 🌊 수급 탭과 같은 관심종목 파일을 씁니다.")

    stored = st.session_state.get("watch_edit")
    if stored is None:
        stored = watch.load()
        st.session_state["watch_edit"] = stored

    edited = st.data_editor(
        stored, num_rows="dynamic", width="stretch", hide_index=True,
        key="watch_editor",
        column_config={
            "종목코드": st.column_config.TextColumn(
                width=90, help="국내 6자리 또는 미국 티커(SCHD). 비우면 종목명으로 찾습니다"),
            "종목명": st.column_config.TextColumn(width=250),
            "수량": st.column_config.NumberColumn(width=70, format="%d",
                                                 help="살 주수. 투자금액만 넣어도 됩니다"),
            "투자금액": st.column_config.NumberColumn(
                width=110, format="localized",
                help="원화. 수량이 비면 현재가로 나눠 정수 주수로 바꿉니다"),
            "메모": st.column_config.TextColumn(width=150),
        },
    )
    c1, c2, c3 = st.columns([1, 2, 2.2])
    if c1.button("저장", type="primary", key="save_watch"):
        path = watch.save(edited)
        st.session_state["watch_edit"] = watch.load()
        c2.success(f"저장했습니다 → {path.relative_to(BASE)}")
    account = c3.selectbox("어느 계좌로 살 계획인가요?", list(portfolio.ACCOUNT_TAX),
                           key="watch_account",
                           help="세후 금액이 달라집니다. 미국 상장분은 계좌와 무관하게 "
                                "현지 15%를 뗍니다")

    usable = edited[(edited["종목코드"].astype(str).str.strip() != "")
                    | (edited["종목명"].astype(str).str.strip() != "")]
    if usable.empty:
        st.info("관심종목을 넣으면 예상 분배 일정과 현금흐름이 여기에 나옵니다.")
        return

    planned = watch.plan(usable, df, account)
    if planned.empty:
        st.info("계산할 수 있는 종목이 없습니다.")
        return

    planned, fx_rate = apply_fx_scenario(planned)

    # ✅ 매수 전 5분 루틴 — 관심종목 목록 자체를 거른다 (사이드바 필터와 별개).
    # 값이 없는 종목(NA)도 떨어진다 — 모르는 것을 통과로 치지 않는다 (31단계 규칙)
    with st.expander("✅ 매수 전 5분 루틴으로 거르기", expanded=False):
        st.caption("체크한 항목을 **모두 통과한 종목만** 아래 표·합계에 남습니다. "
                   "자료가 없는 종목도 떨어집니다 — 모르는 것을 통과로 치지 않습니다.")
        chosen = [label for label, _, help_text in routine.FILTERS
                  if st.checkbox(label, key=f"watch_rt_{label}", help=help_text)]
        drop_cc = st.checkbox("커버드콜 제외", key="watch_rt_cc",
                              help="⑤ 커버드콜 천장은 데이터로 판정할 수 없어 "
                                   "제외 여부만 고릅니다")
    total_before = len(planned)
    planned = routine.apply_filters(planned, chosen, drop_cc)
    if len(planned) < total_before:
        st.caption(f"루틴 필터: {total_before}종목 → **{len(planned)}종목**")
    if planned.empty:
        st.info("루틴 필터를 모두 통과한 관심종목이 없습니다. 체크를 줄여 보세요.")
        return

    blocked = planned[planned["비고"].astype(str).str.contains("매수 불가", na=False)]
    if not blocked.empty:
        st.error(f"**{account} 계좌로는 못 사는 종목이 있습니다** (미국 직접 상장): "
                 + ", ".join(blocked["종목명"].head(5)))

    ok = planned[planned["예상연분배금"].notna() & planned["투자금액"].notna()]
    invested = float(ok["투자금액"].sum())
    yearly = float(ok["예상연분배금"].sum())
    yearly_after = float(ok["예상연분배금_세후"].sum())
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("투자금액 합계", f"{invested:,.0f}원",
              help="수량 × 현재가. 투자금액만 넣은 종목은 정수 주수로 바꾼 뒤의 값")
    k2.metric("예상 연 분배금(세전)", f"{yearly:,.0f}원")
    k3.metric("예상 연 분배금(세후)", f"{yearly_after:,.0f}원",
              help=f"{account} 기준 원천징수 반영. 미국 상장분은 15%")
    k4.metric("예상 연 분배율", f"{yearly / invested * 100:.2f}%" if invested else "—",
              help="예상 연 분배금(세전) ÷ 투자금액. 가격 변동은 반영하지 않습니다")

    show = pd.DataFrame({
        "종목명": planned["종목명"],
        "코드": planned["코드"],
        "시장": planned["시장"] if "시장" in planned.columns else "",
        "주기": planned["주기"] if "주기" in planned.columns else "",
        "현재가": pd.to_numeric(planned.get("현재가"), errors="coerce"),
        "수량": pd.to_numeric(planned.get("수량"), errors="coerce"),
        "투자금액": pd.to_numeric(planned.get("투자금액"), errors="coerce"),
        "연분배금(세전)": pd.to_numeric(planned.get("예상연분배금"), errors="coerce"),
        "연분배금(세후)": pd.to_numeric(planned.get("예상연분배금_세후"), errors="coerce"),
        "연분배율": pd.to_numeric(planned.get("예상연분배율"), errors="coerce"),
        "1년수익률": pd.to_numeric(planned.get("1년수익률"), errors="coerce"),
        "분배성장": pd.to_numeric(planned.get("분배금성장률"), errors="coerce"),
        "분배변동": pd.to_numeric(planned.get("분배변동계수"), errors="coerce"),
        "루틴": pd.to_numeric(planned.get("루틴점수"), errors="coerce"),
        "다음매수마감": planned.get("다음매수마감"),
        "비고": planned.get("비고"),
    })
    st.dataframe(
        show, width="stretch", hide_index=True,
        column_config={
            "종목명": st.column_config.TextColumn(width=250, pinned=True),
            "코드": st.column_config.TextColumn(width=75),
            "시장": st.column_config.TextColumn(width=55),
            "주기": st.column_config.TextColumn(width=55),
            "현재가": st.column_config.NumberColumn(width=85, format="localized"),
            "수량": st.column_config.NumberColumn(width=60, format="%d"),
            "투자금액": st.column_config.NumberColumn(width=100, format="localized"),
            "연분배금(세전)": st.column_config.NumberColumn(width=110, format="localized",
                                                       help="연간주당분배금 × 수량"),
            "연분배금(세후)": st.column_config.NumberColumn(width=110, format="localized"),
            "연분배율": st.column_config.NumberColumn(
                width=80, format="%.2f%%",
                help="연분배금(세전) ÷ 투자금액 — 최근 12개월 실적 기준"),
            "1년수익률": st.column_config.NumberColumn(
                width=90, format="%+.2f%%",
                help="**과거 참고값**입니다(가격만, 분배금 제외). 미래 예측이 아닙니다"),
            "분배성장": st.column_config.NumberColumn(
                width=85, format="%+.1f%%",
                help="직전 12개월 분배금이 그 앞 12개월보다 얼마나 늘었나"),
            "분배변동": st.column_config.NumberColumn(
                width=85, format="%.0f%%",
                help="회차별 분배금이 얼마나 들쭉날쭉한가(표준편차 ÷ 평균). "
                     "**이 값이 크면 위 '연분배금'을 그대로 믿기 어렵습니다** — "
                     "회차마다 금액이 크게 달라집니다. 주기별 중앙값은 "
                     "월 8% · 분기 31% · 위클리 71%"),
            "루틴": st.column_config.NumberColumn(
                width=60, format="%+d",
                help="매수 전 5분 루틴 총점(-4~+4). 항목별 근거는 ✅ 5분 루틴 탭에서 "
                     "종목을 골라 보세요"),
            "다음매수마감": st.column_config.TextColumn(
                width=100, help="다음 회차를 받으려면 이날까지 사야 합니다(예상)"),
            "비고": st.column_config.TextColumn(width=140),
        },
    )
    high = show[(show["연분배율"] > 20) & (show["1년수익률"] < 0)]
    if not high.empty:
        st.warning("⚠ 연분배율 20%↑ 인데 1년 수익률이 마이너스인 종목: "
                   + ", ".join(high["종목명"].head(3))
                   + " — 원금을 헐어 나눠주는 구조일 수 있습니다. 📋 종목 목록의 "
                     "**총수익(1년)** 칸을 확인하세요.")

    upcoming = watch.schedule(planned, df, months=12, account_type=account)
    upcoming = watch.apply_fx(upcoming, fx_rate)   # 일정 금액도 같은 환율로
    if upcoming.empty:
        return
    st.divider()
    st.write("**월별 예상 현금흐름 (12개월, 세후)**")
    monthly = upcoming.copy()
    monthly["월"] = [f"{d.year % 100:02d}-{d.month:02d}" for d in monthly["예상기준일"]]
    agg = monthly.groupby(["월", "종목명"], as_index=False)["세후"].sum()
    fig = px.bar(agg, x="월", y="세후", color="종목명", barmode="stack")
    fig.update_layout(xaxis_title="", yaxis_title="원", hovermode="x unified",
                      legend_title_text="", bargap=0.35)
    st.plotly_chart(fig, width="stretch")
    per_month = upcoming["세후"].sum() / 12
    st.caption(f"12개월 합계 세전 {upcoming['세전'].sum():,.0f}원 / "
               f"세후 {upcoming['세후'].sum():,.0f}원 · **월 평균 {per_month:,.0f}원** · "
               "1회분 = 직전 12개월 분배금 ÷ 지급 횟수(평균). 공시 전 추정입니다.")

    st.write("**다가오는 지급 일정 (가까운 15건)**")
    near = upcoming.head(15).copy()
    near["예상기준일"] = near["예상기준일"].map(ymd)
    near["예상매수마감"] = near["예상매수마감"].map(ymd)
    st.dataframe(
        near, width="stretch", hide_index=True,
        column_config={
            "예상기준일": st.column_config.TextColumn(width=85, help="예상 지급기준일(배당락 기준)"),
            "예상매수마감": st.column_config.TextColumn(width=95,
                                                   help="이날까지 사야 이 회차를 받습니다"),
            "종목명": st.column_config.TextColumn(width=250, pinned=True),
            "시장": st.column_config.TextColumn(width=55,
                                               help="미국 종목 금액은 환율 시나리오를 따릅니다"),
            "주기": st.column_config.TextColumn(width=55),
            "세전": st.column_config.NumberColumn(width=90, format="localized"),
            "세후": st.column_config.NumberColumn(width=90, format="localized"),
        },
    )
    st.caption("사이드바에서 **관심종목만**을 고르면 📅 다가오는 일정 탭이 "
               "관심종목 배당 캘린더(목록·달력·월별 건수)가 됩니다.")


# ── 메인 ─────────────────────────────────────────────────
# ── 종이 톤 스타일 (참고: 개인연금ETF포커스_BM점검_FY26.html) ──
# 본문 고운바탕(세리프), 제목·큰 숫자 Fraunces, 표 숫자 IBM Plex Mono(tabular).
# 색은 .streamlit/config.toml 의 테마와 같은 값 — 두 곳이 어긋나면 안 된다.
PAPER_CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Gowun+Batang:wght@400;700&family=Fraunces:opsz,wght@9..144,400;9..144,600&family=IBM+Plex+Mono:wght@400;500&display=swap');
:root{--paper:#f4efe6;--ink:#23201c;--muted:#8a8175;--gold:#b8893a;--line:#ddd5c7;--card:#fffdf8;}
html, body, [data-testid="stAppViewContainer"] *{
  font-family:'Gowun Batang','Noto Serif KR',serif;
}
/* ⚠ Streamlit 아이콘은 'arrow_drop_down' 같은 글자를 **아이콘 폰트**로 그린다.
   위에서 폰트를 통째로 덮으면 그 글자가 그대로 노출돼 라벨과 겹친다 → 되돌린다 */
[data-testid="stIconMaterial"],
span[class*="material-symbols"], i[class*="material-symbols"]{
  font-family:'Material Symbols Rounded' !important;
}
code, pre, [data-testid="stMetricValue"]{
  font-variant-numeric:tabular-nums;
}
h1, h2, h3, [data-testid="stMetricValue"]{
  font-family:'Fraunces','Gowun Batang',serif !important;
}
h1{letter-spacing:-.5px;}
h2, h3{border-bottom:2px solid var(--line);padding-bottom:5px;}
/* KPI 타일 — 카드 + 골드 상단 보더 (참고 리포트의 .kpi) */
[data-testid="stMetric"]{
  background:var(--card);border:1px solid var(--line);border-top:3px solid var(--gold);
  border-radius:5px;padding:12px 14px 10px;
}
[data-testid="stMetricLabel"] p{
  font-size:12px;letter-spacing:.4px;color:var(--muted);
}
/* 탭 — 고른 것만 골드로 (일관성: 강조는 골드 하나) */
[data-testid="stTabs"] button[aria-selected="true"]{
  color:var(--gold);font-weight:700;
}
[data-testid="stTabs"] [data-baseweb="tab-highlight"]{background-color:var(--gold);}
/* 사이드바 — 본문보다 살짝 어두운 종이 */
[data-testid="stSidebar"]{background:#efe8db;border-right:1px solid var(--line);}
/* 표 숫자 — 자릿수가 흔들리면 비교가 안 된다 */
[data-testid="stDataFrame"] *{font-variant-numeric:tabular-nums;}
hr{border-color:var(--line);}
</style>
"""


def main() -> None:
    st.set_page_config(page_title="ETF 분배·포트폴리오 대시보드", page_icon="💰",
                       layout="wide")
    st.markdown(PAPER_CSS, unsafe_allow_html=True)
    st.title("💰 ETF 분배·포트폴리오 대시보드")

    if not XLSX.exists():
        st.error("output/분배ETF목록.xlsx 가 없습니다. 먼저 1단계(collect.py→classify.py)를 실행하세요.")
        return

    try:
        df = load_data()
        events = load_calendar()
    except Exception:
        st.error("대시보드 데이터를 읽지 못했습니다. 입력 파일 형식과 권한을 확인해 주세요.")
        return

    try:
        filtered = sidebar_filters(df)
        # 차트에서 고른 기준은 **탭 전체**에 걸린다 (차트는 늘 사이드바 결과로 그린다)
        cycles, domestics = render_kpis(filtered, total=len(df))
        filtered = apply_chart_picks(filtered, cycles, domestics)
        st.divider()

        tab1, tab9, tab10, tab2, tab3, tab4, tab5, tab6, tab7, tab8 = st.tabs(
            ["📋 종목 목록", "✅ 5분 루틴", "⚖ 대안 비교", "📅 다가오는 일정",
             "📉 NAV 침식", "📊 NAV 비교표", "💼 내 포트폴리오", "📈 시장국면",
             "🌊 수급", "⭐ 관심종목"])
        with tab1:
            render_table(filtered)
        with tab9:
            render_routine(filtered)
        with tab10:
            render_compare(filtered, df)
        with tab2:
            render_calendar(events, filtered)
        with tab3:
            render_nav_erosion(filtered)
        with tab4:
            render_nav_compare(filtered)
        with tab5:
            render_portfolio(df, events)      # 사이드바 필터와 무관 (내 보유는 항상 전부)
        with tab6:
            render_market_regime()            # 종목 필터와 무관한 거시 지표
        with tab7:
            render_flow(df)                   # 관심종목 수급 (사이드바 필터와 무관)
        with tab8:
            render_watch_plan(df)             # 관심종목 현금흐름 시뮬레이션 (필터 무관)
    except Exception:
        st.error("화면을 구성하지 못했습니다. 입력 데이터 형식을 확인해 주세요.")
        return

    st.caption("데이터: 세이브로 분배금지급현황 · 영업일: 한국거래소(XKRX)")


if __name__ == "__main__":
    main()
