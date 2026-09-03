# -*- coding: utf-8 -*-
"""
1단계-보조: KRX에서 ETF 기본정보(총보수)와 시세(시가총액)를 수집
- 데이터 소스: KRX 정보데이터시스템
  * [13104] 전종목 기본정보 → 총보수(ETF_TOT_FEE), 상장일
  * [13101] 전종목 시세     → 시가총액(MKTCAP)
- 둘 다 '전 종목 한 번에' 오는 요청이라 종목마다 조회할 필요가 없다
- 결과: data/raw/krx_etf정보_YYYYMMDD.csv  (classify.py 가 ISIN으로 붙여 씀)
"""
from contextlib import redirect_stdout, redirect_stderr
from datetime import date
from io import StringIO
from pathlib import Path
import os
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import etf_taxonomy                       # noqa: E402

BASE = Path(__file__).resolve().parent.parent
RAW_DIR = BASE / "data" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

OUTPUT_COLUMNS = ["ISIN", "KRX코드", "종목명", "운용사", "총보수", "시가총액",
                  "종가", "NAV", "상장일",
                  "자산분류", "시장분류", "운용방식", "과세유형", "기초지수명",
                  "투자지역", "테마섹터", "전략", "기준일자"]


def _to_number(series: pd.Series) -> pd.Series:
    """'420,312,000,000' 같은 문자열을 숫자로."""
    return pd.to_numeric(
        series.astype(str).str.replace(",", "", regex=False), errors="coerce")


# ⚠ 아래 fetch_* 는 pykrx 로 **data.krx.co.kr(KDM)** 을 친다. KDM 은 자동화 조회를
# 약관으로 금지하고 IP 를 차단한다(44단계-취소, 2026-08-15 실제 차단됨).
# **일상 갱신은 이 파일이 아니라 `etf_refresh.py`(KRX Open API, 공식 승인 경로)** 다.
# 여기 함수들은 정적 칸(총보수·분류·상장일)을 처음 만들 때만 쓰는 것이라
# 실수로 도는 것을 막는다. 정말 필요하면 ETF_ALLOW_KDM=1 을 주고 **1회만**.
KDM_GUARD_MESSAGE = (
    "data.krx.co.kr(KDM) 자동 조회는 약관 위반이라 막아 뒀습니다. "
    "일상 갱신은 `python src/etf_refresh.py`(KRX Open API)를 쓰세요. "
    "정적 칸을 새로 만들어야 한다면 ETF_ALLOW_KDM=1 을 주고 1회만 실행하세요."
)


def _guard_kdm() -> None:
    if os.environ.get("ETF_ALLOW_KDM") != "1":
        raise RuntimeError(KDM_GUARD_MESSAGE)


def fetch_basic() -> pd.DataFrame:
    """[13104] 전종목 기본정보 — 총보수·상장일·분류 축들.

    분류는 전부 KRX가 주는 값 그대로다:
      IDX_ASST_CLSS_NM 자산분류(주식·채권·원자재…) / IDX_MKT_CLSS_NM 시장분류(국내·해외)
      ETF_REPLICA_METHD_TP_CD 운용방식(실물·합성 × 패시브·액티브) / TAX_TP_CD 과세유형
    세부 지역·섹터만 KRX에 없어 기초지수명으로 추정한다(etf_taxonomy).
    """
    _guard_kdm()
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        from pykrx.website.krx.etx.core import ETF_전종목기본종목
        raw = ETF_전종목기본종목().fetch()
    required = {"ISU_CD", "ISU_SRT_CD", "ETF_TOT_FEE", "LIST_DD"}
    missing = required.difference(raw.columns)
    if missing:
        raise ValueError(f"KRX 기본정보 컬럼이 부족합니다: {', '.join(sorted(missing))}")

    def text(column: str) -> pd.Series:
        """없을 수도 있는 분류 컬럼은 빈 문자열로 (KRX 응답이 바뀌어도 안 죽게)."""
        if column not in raw.columns:
            return pd.Series("", index=raw.index)
        return raw[column].astype(str).str.strip().replace({"nan": "", "None": ""})

    basic = pd.DataFrame({
        "ISIN": raw["ISU_CD"].astype(str).str.strip(),
        "KRX코드": raw["ISU_SRT_CD"].astype(str).str.strip(),
        # 종목명은 약칭(ISU_ABBRV) — 세이브로 종목명과 같은 형태다.
        # 운용사(COM_ABBRV)도 KRX 값 그대로 받는다(이름에서 추정하지 않는다).
        "종목명": text("ISU_ABBRV"),
        "운용사": text("COM_ABBRV"),
        "총보수": _to_number(raw["ETF_TOT_FEE"]),
        "상장일": pd.to_datetime(raw["LIST_DD"], format="%Y/%m/%d", errors="coerce").dt.date,
        "자산분류": text("IDX_ASST_CLSS_NM"),
        "시장분류": text("IDX_MKT_CLSS_NM"),
        "운용방식": text("ETF_REPLICA_METHD_TP_CD"),
        "과세유형": text("TAX_TP_CD"),
        "기초지수명": text("ETF_OBJ_IDX_NM"),
    })
    return etf_taxonomy.attach(basic)      # 투자지역·테마섹터·전략(추정) 추가


def fetch_marketcap(trade_date: str) -> pd.DataFrame:
    """[13101] 전종목 시세 — 시가총액·종가·NAV (포트폴리오 평가에 종가를 쓴다).
    휴장일에도 종목 목록은 오지만 값이 전부 비어 있어서, 값 없는 행은 버린다."""
    _guard_kdm()
    with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
        from pykrx.website.krx.etx.core import 전종목시세_ETF
        raw = 전종목시세_ETF().fetch(trade_date)
    if raw is None or raw.empty:
        return pd.DataFrame(columns=["ISIN", "시가총액", "종가", "NAV"])
    if "ISU_CD" not in raw.columns or "MKTCAP" not in raw.columns:
        raise ValueError("KRX 시세 응답에 ISU_CD/MKTCAP 컬럼이 없습니다.")
    frame = pd.DataFrame({
        "ISIN": raw["ISU_CD"].astype(str).str.strip(),
        "시가총액": _to_number(raw["MKTCAP"]),
        "종가": _to_number(raw["TDD_CLSPRC"]) if "TDD_CLSPRC" in raw.columns else pd.NA,
        "NAV": _to_number(raw["NAV"]) if "NAV" in raw.columns else pd.NA,
    })
    return frame.dropna(subset=["시가총액"])


def latest_marketcap(today: date, lookback_days: int = 10) -> tuple[pd.DataFrame, str]:
    """가장 최근 개장일의 시가총액을 찾는다. (휴장일이면 하루씩 앞으로)"""
    for back in range(lookback_days):
        day = (pd.Timestamp(today) - pd.Timedelta(days=back)).strftime("%Y%m%d")
        frame = fetch_marketcap(day)
        if not frame.empty:
            return frame, day
    raise RuntimeError(f"최근 {lookback_days}일 안에 KRX 개장일 시세를 찾지 못했습니다.")


def collect(today: date | None = None) -> pd.DataFrame:
    """총보수 + 시가총액을 ISIN 기준으로 합친다."""
    today = today or date.today()
    basic = fetch_basic()
    marketcap, trade_date = latest_marketcap(today)
    merged = basic.merge(marketcap, on="ISIN", how="left")
    merged["기준일자"] = trade_date
    return merged[OUTPUT_COLUMNS]


def latest_info_csv() -> Path | None:
    """data/raw 에서 가장 최근 KRX 정보 CSV. 없으면 None."""
    files = sorted(RAW_DIR.glob("krx_etf정보_*.csv"))
    return files[-1] if files else None


def load_latest() -> pd.DataFrame:
    """저장된 KRX 정보를 읽는다. 파일이 없으면 빈 DataFrame."""
    path = latest_info_csv()
    if path is None:
        return pd.DataFrame(columns=OUTPUT_COLUMNS)
    frame = pd.read_csv(path, dtype={"ISIN": str, "KRX코드": str})
    for column in ("총보수", "시가총액", "종가", "NAV"):
        if column in frame.columns:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
    # 테마섹터 '광범위지수'는 '혼합'으로 이름을 바꿨다(41단계). 분류 규칙만 고치면
    # **CSV 에 저장된 옛 값이 그대로 남으므로** 읽는 자리에서 치환한다 —
    # 과거 CSV 전부에 통하고, 갱신 스크립트가 이 값을 이어받아 저장하므로
    # 다음 수집분부터는 파일에도 새 이름으로 남는다.
    if "테마섹터" in frame.columns:
        frame["테마섹터"] = frame["테마섹터"].replace("광범위지수", "혼합")
    return frame


def main():
    today = date.today()
    df = collect(today)

    out = RAW_DIR / f"krx_etf정보_{today.strftime('%Y%m%d')}.csv"
    df.to_csv(out, index=False, encoding="utf-8-sig")
    print(f"저장: {out}")
    print(f"종목 수: {len(df):,}개 / 시가총액 기준일: {df['기준일자'].iloc[0]}")
    print(f"총보수 있음: {df['총보수'].notna().sum():,}개 / "
          f"시가총액 있음: {df['시가총액'].notna().sum():,}개")
    print("\n[분류 분포]")
    for column in ("자산분류", "시장분류", "투자지역", "테마섹터", "전략"):
        top = df[column].value_counts().head(8).to_dict()
        print(f"  {column}: {top}")
    print("\n[확인용 상위 5행]")
    print(df[["KRX코드", "자산분류", "시장분류", "투자지역", "테마섹터", "전략"]]
          .head().to_string())


if __name__ == "__main__":
    from console import use_utf8_stdout   # 콘솔이 cp949여도 안 죽게
    use_utf8_stdout()
    from corp_ca import apply as apply_corp_ca   # 회사 HTTPS 가로채기 뒤에서도 받게
    apply_corp_ca()
    sys.exit(main())
