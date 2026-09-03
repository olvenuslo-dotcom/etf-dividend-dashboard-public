# -*- coding: utf-8 -*-
"""ETF 배수(레버리지·인버스) — 이름이 아니라 데이터 값으로.

6단계에서 확인했듯 KRX 가 주는 자산분류·운용방식·기초지수명으로는 레버리지를
구분할 수 없다(`KODEX 레버리지` 와 `KODEX 200` 이 전 칸 동일, 기초지수도 똑같이
'코스피 200'). 한투의 `etf_trc_ert_mltp` 가 유일한 근거다.

여기서 지키려는 것:
1) **배수 0 은 '0배'가 아니라 미제공.** 실측 31종목이 0 으로 왔는데 그 안에
   `SOL 미국테크TOP10인버스(합성)` 가 있었다 — 0 을 값으로 쓰면 인버스가 일반이 된다.
2) 목록의 `종목코드` 가 빈 234종목도 **ISIN 으로** 붙어야 한다.
3) 1배는 화면에서 비운다 — 거의 다 1배라 전부 적으면 레버리지가 안 보인다.
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

import classify
import kis_api


class _Response:
    def __init__(self, payload):
        self._payload = payload
        self.status_code = 200

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _Session:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(url)
        return _Response(self._payload)


@pytest.fixture
def keyed(monkeypatch, tmp_path):
    monkeypatch.setenv("KIS_APP_KEY", "k")
    monkeypatch.setenv("KIS_APP_SECRET", "s")
    token = tmp_path / "token.json"
    token.write_text(json.dumps({
        "access_token": "t",
        "expires_at": (datetime.now() + timedelta(hours=9)).isoformat(),
    }), encoding="utf-8")
    monkeypatch.setattr(kis_api, "TOKEN_CACHE", token)
    monkeypatch.setattr(kis_api, "SHARED_CACHE", tmp_path / "cache")


def _payload(mltp, index_name="KOSPI200"):
    return {"rt_cd": "0", "output": {
        "etf_trc_ert_mltp": mltp, "etf_rprs_bstp_kor_isnm": index_name,
        "etf_cnfg_issu_cnt": "209", "etf_ntas_ttam": "62872"}}


# ── 수집 ────────────────────────────────────────────────
def test_레버리지와_인버스_배수를_읽는다(keyed):
    lev = kis_api.etf_price_info("122630", _Session(_payload("2.00")))
    inv = kis_api.etf_price_info("114800", _Session(_payload("-1.00")),
                                 use_cache=False)
    assert lev["배수"] == 2.0
    assert inv["배수"] == -1.0


def test_배수_0은_값이_아니라_미제공이다(keyed):
    """실측: 0 으로 온 31종목에 `SOL 미국테크TOP10인버스(합성)` 가 있었다.
    0 을 그대로 쓰면 인버스가 '0배', 1 로 메우면 '일반'이 된다 — 둘 다 틀리다."""
    got = kis_api.etf_price_info("481200", _Session(_payload("0.00")))
    assert "배수" not in got
    # 다른 칸은 그대로 온다 (응답 자체가 실패한 것이 아니다)
    assert got["구성종목수"] == 209


def test_대표지수도_같이_받는다(keyed):
    got = kis_api.etf_price_info("069500", _Session(_payload("1.00")))
    assert got["대표지수"] == "KOSPI200"


# ── 목록에 붙이기 ────────────────────────────────────────
def _catalog():
    return pd.DataFrame({
        "종목명": ["KODEX 레버리지", "만기종목", "미국ETF"],
        "종목코드": ["122630", None, "SCHD"],      # 가운데는 종목코드가 없다
        "ISIN": ["KR7122630046", "KR7999999999", ""],
    })


def _collected():
    return pd.DataFrame({
        "종목코드": ["122630", "999999"],
        "ISIN": ["KR7122630046", "KR7999999999"],
        "배수": [2.0, -2.0],
    })


def test_종목코드가_없어도_ISIN으로_붙는다(monkeypatch):
    """목록의 종목코드는 만기·상폐 234종목이 비어 있다 — ISIN 이 있어야 붙는다."""
    import kis_multiple

    monkeypatch.setattr(kis_multiple, "load_latest", _collected)
    got = classify.attach_multiple(_catalog())
    assert got.loc[0, "배수"] == 2.0
    assert got.loc[1, "배수"] == -2.0          # 종목코드 없이 ISIN 으로 붙었다


def test_수집분에_없으면_빈칸이다(monkeypatch):
    """미국 상장분은 이 API 대상이 아니다 — 1 로 메우지 않는다."""
    import kis_multiple

    monkeypatch.setattr(kis_multiple, "load_latest", _collected)
    got = classify.attach_multiple(_catalog())
    assert pd.isna(got.loc[2, "배수"])


def test_수집분이_없어도_안_죽는다(monkeypatch):
    import kis_multiple

    monkeypatch.setattr(kis_multiple, "load_latest", pd.DataFrame)
    got = classify.attach_multiple(_catalog())
    assert "배수" in got.columns
    assert got["배수"].isna().all()


def test_저장된_CSV의_0도_지운다(tmp_path, monkeypatch):
    """수집 시점 가드가 들어가기 전에 저장된 CSV 에는 0 이 남아 있다.
    읽는 쪽에서 안 막으면 인버스가 '0배'로 화면에 뜬다."""
    import kis_multiple

    pd.DataFrame({
        "종목코드": ["122630", "481200", "069500"],
        "ISIN": ["KR7122630046", "KR7481200000", "KR7069500007"],
        "배수": [2.0, 0.0, 1.0],          # 가운데가 인버스인데 0 으로 저장됐다
    }).to_csv(tmp_path / "kis_배수_20260815.csv", index=False,
              encoding="utf-8-sig")
    monkeypatch.setattr(kis_multiple, "RAW_DIR", tmp_path)
    got = kis_multiple.load_latest().set_index("종목코드")["배수"]
    assert got["122630"] == 2.0
    assert pd.isna(got["481200"])          # 0 → 빈칸
    assert got["069500"] == 1.0


# ── 화면 글자 ───────────────────────────────────────────
def test_1배도_적고_빈칸은_자료없음만_뜻한다():
    """1배를 비우면 '평범한 1배'와 '모름'이 같은 빈칸이 돼 구분이 안 된다."""
    from src.dashboard import multiple_label

    got = multiple_label(pd.Series([1.0, 2.0, -1.0, -2.0, None, 1.5]))
    assert list(got) == ["1배", "2배", "-1배", "-2배", "", "1.5배"]


# ── 대안 비교의 '무엇부터 올릴까' 기준 ───────────────────
# 전에는 정렬이 없어 앞 300개가 '주기→가나다 앞부분'이었다 (5단계에서 NAV
# 비교표에 있던 것과 같은 함정). 기준을 골라 자르도록 바꿨다.
def test_비교_기준에_겹침과_루틴이_있다():
    from src.dashboard import COMPARE_PICK_ORDER

    assert "겹침 낮은 순" in COMPARE_PICK_ORDER
    assert "루틴 점수 높은 순" in COMPARE_PICK_ORDER
    column, ascending, _ = COMPARE_PICK_ORDER["겹침 낮은 순"]
    assert (column, ascending) == ("_겹침", True)     # 낮은 것이 위로
    column, ascending, _ = COMPARE_PICK_ORDER["루틴 점수 높은 순"]
    assert (column, ascending) == ("루틴점수", False)  # 높은 것이 위로


def test_모든_기준에_설명이_붙어_있다():
    """설명이 곧 화면 문구다 — 비면 사용자가 기준을 못 고른다."""
    from src.dashboard import COMPARE_PICK_ORDER

    for label, (column, _, desc) in COMPARE_PICK_ORDER.items():
        assert column, f"{label}: 정렬 컬럼이 비었다"
        assert len(desc) > 15, f"{label}: 설명이 너무 짧다"


def test_값이_없는_종목은_항상_뒤로_간다():
    """오름차순에서 결측이 1등이 되면 '총보수 제일 싼 것'에 자료 없는 종목이 온다."""
    from src.dashboard import sort_by_column

    frame = pd.DataFrame({"종목명": ["A", "B", "C"], "총보수": [0.5, None, 0.1]})
    got = sort_by_column(frame, "총보수", ascending=True)
    assert list(got["종목명"]) == ["C", "A", "B"]
    got_desc = sort_by_column(frame, "총보수", ascending=False)
    assert list(got_desc["종목명"]) == ["A", "C", "B"]


def test_없는_칸으로_정렬하면_원래_순서다():
    from src.dashboard import sort_by_column

    frame = pd.DataFrame({"종목명": ["A", "B"]})
    assert list(sort_by_column(frame, "없는칸", True)["종목명"]) == ["A", "B"]
