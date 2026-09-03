# -*- coding: utf-8 -*-
"""KIS API 모듈 — 키·네트워크 없이 파싱과 규칙을 검증한다.

여기서 지키려는 것:
1) **오류를 조용히 삼키지 않는다.** rt_cd 가 0 이 아니면 빈 표가 아니라 예외다 —
   krx_pdf 때 예외를 삼켜 User-Agent 사고를 늦게 찾았던 그 교훈이다.
2) 토큰은 캐시를 먼저 쓴다 — 발급마다 알림톡이 오는 공식 동작이라 아껴야 한다.
3) 응답의 콤마 섞인 숫자·YYYYMMDD 날짜가 숫자·datetime 으로 돌아온다.
"""
from datetime import datetime, timedelta
from pathlib import Path
import json
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import kis_api


class _Response:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self):
        return self._payload


class _Session:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        return _Response(self._payload)

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        return _Response(self._payload)


@pytest.fixture
def keys_and_token(monkeypatch, tmp_path):
    """앱키·토큰 캐시를 흉내 내고, **공유 지점을 전부 임시 폴더로** 돌린다.

    안 돌리면 테스트가 진짜 shared-data 의 토큰을 읽거나 잠금 파일을 남긴다.
    """
    monkeypatch.setenv("KIS_APP_KEY", "test-key")
    monkeypatch.setenv("KIS_APP_SECRET", "test-secret")
    cache = tmp_path / "kis_token.json"
    # shared-data 규약의 스키마 (access_token / expires_at)
    cache.write_text(json.dumps({
        "access_token": "cached-token",
        "expires_at": (datetime.now() + timedelta(hours=12))
        .isoformat(timespec="seconds"),
    }), encoding="utf-8")
    monkeypatch.setattr(kis_api, "TOKEN_CACHE", cache)
    monkeypatch.setattr(kis_api, "TOKEN_LOCK", tmp_path / "kis_token.lock")
    monkeypatch.setattr(kis_api, "SHARED_CACHE", tmp_path / "shared")
    return cache


# ── 키·토큰 ─────────────────────────────────────────────
def test_키가_없으면_어디가_비었는지_말한다(monkeypatch):
    monkeypatch.delenv("KIS_APP_KEY", raising=False)
    monkeypatch.delenv("KIS_APP_SECRET", raising=False)
    monkeypatch.setattr(kis_api.env_keys, "missing",
                        lambda *names: list(names))
    with pytest.raises(kis_api.KisKeyMissing) as err:
        kis_api._keys()
    assert "KIS_APP_KEY" in str(err.value)


def test_살아있는_토큰_캐시는_재발급하지_않는다(keys_and_token):
    """발급마다 알림톡이 온다 — 캐시가 있으면 POST 자체가 없어야 한다."""
    session = _Session({})
    assert kis_api.access_token(session) == "cached-token"
    assert session.calls == []


def test_만료_임박_토큰은_새로_받는다(monkeypatch, tmp_path):
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    cache = tmp_path / "kis_token.json"
    cache.write_text(json.dumps({
        "access_token": "stale",
        "expires_at": (datetime.now() + timedelta(minutes=10)).isoformat(),
    }), encoding="utf-8")
    monkeypatch.setattr(kis_api, "TOKEN_CACHE", cache)
    monkeypatch.setattr(kis_api, "TOKEN_LOCK", tmp_path / "kis_token.lock")
    session = _Session({"access_token": "fresh", "expires_in": 86400})
    assert kis_api.access_token(session) == "fresh"
    assert session.calls[0][0] == "POST"
    # 새 토큰이 공유 스키마로 저장됐고, 잠금은 풀렸다
    saved = json.loads(cache.read_text(encoding="utf-8"))
    assert saved["access_token"] == "fresh"
    assert "expires_at" in saved
    assert not (tmp_path / "kis_token.lock").exists()


def test_잠금이_잡혀_있으면_기다린다_죽은_잠금은_회수한다(monkeypatch, tmp_path):
    """다른 세션이 발급 중이면 대기하고, 90초 넘은 잠금은 죽은 것으로 회수한다."""
    import os as _os

    lock = tmp_path / "kis_token.lock"
    monkeypatch.setattr(kis_api, "TOKEN_LOCK", lock)
    # 살아 있는 잠금 → 짧은 timeout 안에서는 못 잡는다
    lock.write_text("123")
    assert kis_api._acquire_lock(timeout=0.1) is False
    # 죽은(오래된) 잠금 → 회수하고 잡는다
    old = datetime.now().timestamp() - 120
    _os.utime(lock, (old, old))
    assert kis_api._acquire_lock(timeout=5.0) is True
    lock.unlink(missing_ok=True)


def test_잠금을_기다린_뒤_남이_발급해_뒀으면_재발급하지_않는다(monkeypatch, tmp_path):
    """규약의 재확인(double-check) — 발급은 분당 1회라 겹치면 안 된다."""
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    cache = tmp_path / "kis_token.json"
    cache.write_text(json.dumps({"access_token": "old",
                                 "expires_at": "2000-01-01T00:00:00"}),
                     encoding="utf-8")
    monkeypatch.setattr(kis_api, "TOKEN_CACHE", cache)
    monkeypatch.setattr(kis_api, "TOKEN_LOCK", tmp_path / "kis_token.lock")

    # 잠금을 잡는 순간 '다른 세션'이 새 토큰을 써 뒀다고 흉내 낸다
    real_acquire = kis_api._acquire_lock

    def acquire_and_plant(timeout=70.0):
        got = real_acquire(timeout)
        cache.write_text(json.dumps({
            "access_token": "someone-elses",
            "expires_at": (datetime.now() + timedelta(hours=12))
            .isoformat(timespec="seconds"),
        }), encoding="utf-8")
        return got

    monkeypatch.setattr(kis_api, "_acquire_lock", acquire_and_plant)
    session = _Session({"access_token": "should-not-issue"})
    assert kis_api.access_token(session) == "someone-elses"
    assert session.calls == []                   # POST(발급) 없음


# ── 오류 (핵심) ─────────────────────────────────────────
def test_rt_cd_가_0이_아니면_예외다(keys_and_token):
    """빈 표로 돌려주면 '구성종목이 없다'와 '조회가 실패했다'가 안 갈린다."""
    session = _Session({"rt_cd": "1", "msg_cd": "EGW00123",
                        "msg1": "기간이 만료된 token 입니다."})
    with pytest.raises(RuntimeError) as err:
        kis_api.component_stocks("069500", session)
    assert "EGW00123" in str(err.value)


# ── 구성종목 ────────────────────────────────────────────
def _comp_payload():
    return {"rt_cd": "0", "output1": {"stck_prpr": "33,000"}, "output2": [
        {"stck_shrn_iscd": "005930", "hts_kor_isnm": "삼성전자",
         "stck_prpr": "71,900", "etf_cnfg_issu_avls": "1,234,567",
         "etf_vltn_amt": "999", "etf_cnfg_issu_rlim": "25.31"},
        {"stck_shrn_iscd": "000660", "hts_kor_isnm": "SK하이닉스",
         "stck_prpr": "195,000", "etf_cnfg_issu_avls": "890,123",
         "etf_vltn_amt": "888", "etf_cnfg_issu_rlim": "12.50"},
    ]}


def test_구성종목_콤마_숫자가_숫자로_돌아온다(keys_and_token):
    got = kis_api.component_stocks("069500", _Session(_comp_payload()))
    assert len(got) == 2
    assert got["현재가"].iloc[0] == 71900.0
    assert got["비중"].iloc[0] == 25.31
    assert got["종목코드"].iloc[0] == "069500"       # 모(母) ETF 코드가 붙는다


def test_구성종목이_없으면_빈_표다(keys_and_token):
    got = kis_api.component_stocks(
        "069500", _Session({"rt_cd": "0", "output1": {}, "output2": []}))
    assert got.empty
    assert "구성종목코드" in got.columns


# ── NAV ────────────────────────────────────────────────
def test_NAV_날짜가_datetime_으로_정렬돼_온다(keys_and_token):
    payload = {"rt_cd": "0", "output": [
        {"stck_bsop_date": "20260812", "stck_clpr": "33,500",
         "nav": "33,512.34", "dprt": "-0.04"},
        {"stck_bsop_date": "20260811", "stck_clpr": "33,000",
         "nav": "33,010.00", "dprt": "0.03"},
    ]}
    got = kis_api.nav_daily("069500", "20260801", "20260813",
                            _Session(payload))
    assert list(got["날짜"]) == [pd.Timestamp("2026-08-11"),
                                 pd.Timestamp("2026-08-12")]
    assert got["NAV"].iloc[1] == 33512.34


def test_긴_구간은_나눠_받아_중복_없이_합친다(keys_and_token, monkeypatch):
    calls = []

    def fake_nav_daily(code, start, end, session=None):
        calls.append((start, end))
        return pd.DataFrame({"날짜": [pd.Timestamp(start[:4] + "-06-01")],
                             "종가": [1.0], "NAV": [1.0], "괴리율": [0.0]})

    monkeypatch.setattr(kis_api, "nav_daily", fake_nav_daily)
    monkeypatch.setattr(kis_api.time, "sleep", lambda s: None)
    got = kis_api.nav_history("069500", "20250101", "20260101")
    assert len(calls) >= 3                    # 1년이면 최소 3구간
    # 각 구간이 140일을 넘지 않는다 (100거래일 제한 안쪽)
    for start, end in calls:
        assert (pd.Timestamp(end) - pd.Timestamp(start)).days <= 140
    assert got["날짜"].is_unique


# ── 투자자별 ────────────────────────────────────────────
def test_투자자별_순매수가_숫자로_돌아온다(keys_and_token):
    payload = {"rt_cd": "0", "output": [
        {"stck_bsop_date": "20260812", "stck_clpr": "33,500",
         "prsn_ntby_qty": "-1,234", "frgn_ntby_qty": "2,000",
         "orgn_ntby_qty": "-766", "prsn_ntby_tr_pbmn": "-41,339",
         "frgn_ntby_tr_pbmn": "67,000", "orgn_ntby_tr_pbmn": "-25,661"},
    ]}
    got = kis_api.investor_daily("069500", _Session(payload))
    assert got["개인_순매수량"].iloc[0] == -1234.0
    assert got["외국인_순매수대금"].iloc[0] == 67000.0


def test_세부_투자자_그룹이_한글_칸으로_돌아온다(keys_and_token):
    payload = {"rt_cd": "0", "output1": {}, "output2": [
        {"stck_bsop_date": "20260814", "stck_clpr": "110,060",
         "scrt_seln_tr_pbmn": "1,000", "scrt_shnu_tr_pbmn": "3,000",
         "scrt_ntby_tr_pbmn": "2,000",
         "ivtr_ntby_tr_pbmn": "-500", "prsn_ntby_tr_pbmn": "-54,869"},
    ]}
    got = kis_api.investor_detail("069500", session=_Session(payload))
    assert got["금융투자_순매수대금"].iloc[0] == 2000.0
    assert got["금융투자_매수대금"].iloc[0] == 3000.0
    assert got["투신_순매수대금"].iloc[0] == -500.0


# ── 겹침 사다리의 KIS30 층 ──────────────────────────────
def test_겹침_사다리는_PDF_다음에_KIS30을_본다():
    import compare

    pdf = {"A": {"X": 50.0, "Y": 50.0}, "B": {"X": 30.0, "Z": 70.0}}
    kis30 = {"A": {"X": 99.0}, "B": {"X": 99.0}, "C": {"X": 80.0},
             "D": {"X": 70.0}}
    sectors = {}
    # 둘 다 PDF 에 있으면 PDF 가 이긴다 (KIS30 의 99 가 아니라 30)
    assert compare.real_overlap("A", "B", sectors, pdf, kis30) == 30.0
    # PDF 에 없는 쌍은 KIS30 으로 내려간다
    assert compare.real_overlap("C", "D", sectors, pdf, kis30) == 70.0
    # 어느 층에도 같이 없으면 None (분류 폴백은 호출부 몫)
    assert compare.real_overlap("A", "C", sectors, {}, {"A": {"X": 1.0}}) is None


def test_KIS30_로더는_비중합_50_미만을_버린다(tmp_path):
    """합성형처럼 담보 몇 줄만 오는 종목을 '실측'이라 부르면 안 된다."""
    import compare

    pd.DataFrame({
        "ISIN": ["KR1"] * 2 + ["KR2"] * 2,
        "구성종목코드": ["005930", "000660", "005930", "000660"],
        "비중": [60.0, 25.0, 10.0, 5.0],       # KR1=85 (통과) / KR2=15 (탈락)
    }).to_csv(tmp_path / "kis_TOP30_20260815.csv", index=False,
              encoding="utf-8-sig")
    got = compare.load_kis30(tmp_path)
    assert "KR1" in got and "KR2" not in got


def test_KIS30_비중은_100으로_정규화된다(tmp_path):
    """TOP30 은 비중의 84% 정도만 덮는다. 그대로 쓰면 같은 지수를 따르는
    두 ETF 도 84% 로 나와 PDF 로 잰 값(99.3)과 한 표에서 비교가 안 된다."""
    import compare

    pd.DataFrame({
        "ISIN": ["KR1"] * 2 + ["KR2"] * 2,
        "구성종목코드": ["005930", "000660"] * 2,
        "비중": [60.0, 25.0, 60.0, 25.0],       # 둘 다 합 85 (같은 구성)
    }).to_csv(tmp_path / "kis_TOP30_20260815.csv", index=False,
              encoding="utf-8-sig")
    got = compare.load_kis30(tmp_path)
    assert round(sum(got["KR1"].values())) == 100
    assert round(got["KR1"]["005930"], 1) == 70.6      # 60/85
    # 같은 구성이면 겹침이 100 이어야 한다 (정규화 전이면 85)
    assert compare.real_overlap("KR1", "KR2", {}, None, got) == 100.0


def test_KIS30_파일이_없으면_빈_dict다(tmp_path):
    import compare

    assert compare.load_kis30(tmp_path) == {}


# ── 공유 캐시 (여러 프로젝트가 같은 앱키를 쓸 때) ────────
def test_같은_날_두번째_호출은_API를_안_부른다(keys_and_token):
    """다른 VS Code 창(프로젝트)이 이미 받아 둔 것을 다시 안 받는다."""
    session1 = _Session(_comp_payload())
    first = kis_api.component_stocks("069500", session1)
    assert len(session1.calls) == 1

    session2 = _Session({"rt_cd": "1", "msg1": "부르면 안 되는 호출"})
    second = kis_api.component_stocks("069500", session2)
    assert session2.calls == []                  # API 호출 없음
    assert second["구성종목명"].tolist() == first["구성종목명"].tolist()


def test_캐시를_끄면_다시_부른다(keys_and_token):
    kis_api.component_stocks("069500", _Session(_comp_payload()))
    session = _Session(_comp_payload())
    kis_api.component_stocks("069500", session, use_cache=False)
    assert len(session.calls) == 1


def test_투자자별_캐시는_날짜가_datetime_으로_돌아온다(keys_and_token):
    payload = {"rt_cd": "0", "output": [
        {"stck_bsop_date": "20260812", "stck_clpr": "33,500",
         "prsn_ntby_qty": "-1", "frgn_ntby_qty": "2", "orgn_ntby_qty": "-1",
         "prsn_ntby_tr_pbmn": "-1", "frgn_ntby_tr_pbmn": "2",
         "orgn_ntby_tr_pbmn": "-1"},
    ]}
    kis_api.investor_daily("069500", _Session(payload))
    cached = kis_api.investor_daily("069500", _Session({}))
    assert str(cached["날짜"].dtype).startswith("datetime64")
