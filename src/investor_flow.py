# -*- coding: utf-8 -*-
"""투자자별 거래 — 수급 탭 HTS 스타일 화면의 재료. **KIS API 로 받는다.**

전에는 KRX 정보데이터시스템 [13207]을 pykrx 로그인으로 긁었는데, KDM 이
자동화 조회를 약관으로 금지하면서(44단계-취소) 못 쓰게 됐다. 한투 [0416]
API(`kis_api.investor_detail`)가 같은 값을 **더 세부 분류로** 준다 —
증권·투신·기금·은행·보험·종금·사모·기타법인까지, KRX 로그인 없이.

한 번에 최근 30거래일이 오고, base_date 를 뒤로 밀며 과거를 백필한다.
기간 합계 [투자자, 매도, 매수, 순매수] (주 수량) — 화면 쪽 모양은 그대로다.
"""
from pathlib import Path
import sys
import time

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import kis_api                   # noqa: E402

BASE = Path(__file__).resolve().parent.parent
CACHE_DIR = BASE / "data" / "investor_cache"

# 화면에 보여줄 세부 투자자 (kis_api.DETAIL_GROUPS 의 한글 이름).
# '기관합계'는 세부와 겹쳐 그리면 두 번 세는 그림이 돼서 뺀다.
DETAIL_INVESTORS = ["금융투자", "보험", "투신", "사모", "은행", "종금",
                    "기금", "기타법인", "개인", "외국인"]

MAX_PAGES = 14                   # 30거래일 × 14 ≈ 1년 반 — 백필 상한


def _page_span(code: str, start_ts: pd.Timestamp, end_ts: pd.Timestamp,
               force: bool) -> pd.DataFrame:
    """[start, end] 구간을 덮을 때까지 30거래일짜리 페이지를 뒤로 밀며 받는다."""
    frames = []
    base = end_ts
    for _ in range(MAX_PAGES):
        got = kis_api.investor_detail(code, base.strftime("%Y%m%d"),
                                      use_cache=not force)
        if got.empty:
            break
        frames.append(got)
        oldest = got["날짜"].min()
        if oldest <= start_ts or len(got) < 25:   # 상장 초기까지 다 받았다
            break
        base = oldest - pd.Timedelta(days=1)
        time.sleep(kis_api.PAUSE)
    if not frames:
        return pd.DataFrame()
    merged = (pd.concat(frames, ignore_index=True)
              .drop_duplicates("날짜").sort_values("날짜"))
    return merged[(merged["날짜"] >= start_ts) & (merged["날짜"] <= end_ts)]


def fetch(code: str, start: str, end: str, force: bool = False) -> pd.DataFrame:
    """기간 합계 [투자자, 매도, 매수, 순매수] (주 수량). 기간별 CSV 캐시.

    실패하면 예외를 그대로 올린다 — 화면에서 원인(키·네트워크)을 보여줘야
    한다. 빈 표로 눙치면 '거래가 없었다'와 구분이 안 된다.
    """
    code = str(code).strip()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache = CACHE_DIR / f"{code}_{start}_{end}.csv"
    if cache.exists() and not force:
        return pd.read_csv(cache)

    daily = _page_span(code, pd.Timestamp(start), pd.Timestamp(end), force)

    rows = []
    for name in DETAIL_INVESTORS:
        sell, buy, net = (f"{name}_매도량", f"{name}_매수량", f"{name}_순매수량")
        if daily.empty or sell not in daily.columns:
            continue
        rows.append({
            "투자자": name,
            "매도": float(daily[sell].fillna(0).sum()),
            "매수": float(daily[buy].fillna(0).sum()) if buy in daily.columns else None,
            "순매수": float(daily[net].fillna(0).sum()) if net in daily.columns else None,
        })
    out = pd.DataFrame(rows, columns=["투자자", "매도", "매수", "순매수"])
    if not out.empty and out["매도"].fillna(0).sum() + out["매수"].fillna(0).sum() > 0:
        out.to_csv(cache, index=False, encoding="utf-8-sig")   # 0뿐이면 캐시 안 함
    return out
