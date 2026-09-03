# -*- coding: utf-8 -*-
"""19a 공시 파서 — 발행사 넷은 공시 자리도, 내놓는 값도 다르다.

여기서 지키려는 것은 셋이다.
1) **보일러플레이트를 공시값으로 읽지 않는다.** 모든 종목 페이지에 "분배금이
   원금 반환으로 처리될 수 있다"는 위험고지가 따로 있다. 그건 공시가 아니다.
2) **남의 펀드 값을 가져오지 않는다.** GraniteShares 페이지에는 숫자 id 가 둘이라
   잘못 집으면 다른 펀드의 ROC 가 조용히 들어온다(실제로 그랬다).
3) **없는 누적을 이번 회차로 채우지 않는다.** 채우면 '연간 이만큼'이라는 뜻이 돼서
   판정이 실제보다 단단해 보인다.
"""
from pathlib import Path
import json
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

import roc


class _Response:
    def __init__(self, text="", status=200, url="", payload=None):
        self.text = text
        self.status_code = status
        self.url = url
        self._payload = payload

    def json(self):
        return self._payload


class _Session:
    """요청 URL 순서대로 정해 둔 응답을 돌려주는 가짜 세션."""

    def __init__(self, *responses):
        self._responses = list(responses)
        self.urls = []

    def get(self, url, **_):
        self.urls.append(url)
        return self._responses.pop(0)


# 실제 페이지에 있는 위험고지 문구 (모든 종목에 붙어 있다)
BOILERPLATE = (
    "<p>These distributions may exceed the Fund's income and gains. "
    "Distributions in excess of the Fund's current and accumulated earnings "
    "and profits will be treated as a return of capital.</p>")

# 실제 공시 문구
ROUNDHILL_REAL = (
    "<em>Per the Fund's most recent 19a-1 notice, the estimated per share "
    "composition of the distribution includes return of capital (ROC) of "
    "97.5%.</em>")
REX_REAL = ("<strong>Current distributions consist of 84.25% estimated "
            "return of capital (ROC).</strong>")


# ── ① 보일러플레이트를 안 문다 ──────────────────────────
def test_Roundhill_공시_문구에서_비율을_읽는다():
    got = roc.fetch_roundhill("QDTE", _Session(
        _Response(BOILERPLATE + ROUNDHILL_REAL, url="https://x/etf/qdte/")))
    assert got["원금반환비율"] == 97.5
    assert got["티커"] == "QDTE"


def test_Roundhill_위험고지만_있으면_값이_없다():
    """'원금 반환으로 처리될 수 있다'는 설명은 공시값이 아니다."""
    assert roc.fetch_roundhill("WEEK", _Session(_Response(BOILERPLATE))) is None


def test_Rex_공시_문구에서_비율을_읽는다():
    got = roc.fetch_rex("FEPI", _Session(
        _Response(BOILERPLATE + REX_REAL, url="https://x/fepi/")))
    assert got["원금반환비율"] == 84.25
    assert got["발행사"] == "Rex"


def test_Rex_위험고지만_있으면_값이_없다():
    assert roc.fetch_rex("BTCL", _Session(_Response(BOILERPLATE))) is None


def test_페이지가_안_열리면_값이_없다():
    assert roc.fetch_roundhill("XXX", _Session(_Response("", status=404))) is None


# ── ② 누적을 지어내지 않는다 ────────────────────────────
def test_누적은_비운다_이번_회차로_채우지_않는다():
    """이 발행사들은 회계연도 누적을 공시하지 않는다. 빈칸이 정직한 표시다."""
    got = roc.fetch_roundhill("QDTE", _Session(_Response(ROUNDHILL_REAL)))
    assert got["원금반환비율"] == 97.5
    assert got["누적_원금반환비율"] is None
    assert got["누적_순투자소득비율"] is None


# ── ③ GraniteShares — 남의 펀드 값을 막는다 (핵심) ──────
def _granite_page(product_id: str, node_id: str = "9999") -> str:
    """실제 페이지처럼 숫자 id 를 **둘** 담는다."""
    return (f"<script>function getProductId() {{ return '{product_id}'; }} "
            f"function getId() {{ return '{node_id}'; }}</script>")


def _fund_yield(roc_value):
    return json.dumps([{"ProductId": 1, "DistributionRate": 35.9,
                        "DistributionDate": "2026-08-07", "ROC": roc_value}])


def test_GraniteShares_는_getProductId_를_쓴다():
    """getId() 는 CMS 노드 id 다. 그걸 쓰면 다른 펀드를 부른다."""
    session = _Session(
        _Response(_granite_page("1119", node_id="1164")),
        _Response(url="https://x/product/1119/en-us/",
                  payload={"Ticker": "TSYY", "fundYield": _fund_yield(95.96)}))
    got = roc.fetch_graniteshares("TSYY", session)
    assert "1119" in session.urls[1] and "1164" not in session.urls[1]
    assert got["원금반환비율"] == 95.96
    assert got["공시일"] == "2026-08-07"


def test_GraniteShares_는_티커가_다르면_버린다():
    """id 를 잘못 집었을 때 **남의 값을 조용히 받아들이지 않는다.**

    실제로 이 일이 있었다 — TSYY 자리에 ANV 의 ROC 0.00 이 들어왔다.
    숫자가 그럴듯해서 표만 봐서는 안 보인다.
    """
    session = _Session(
        _Response(_granite_page("1164")),
        _Response(payload={"Ticker": "ANV", "fundYield": _fund_yield(0.0)}))
    assert roc.fetch_graniteshares("TSYY", session) is None


def test_GraniteShares_분배자료가_없으면_값이_없다():
    session = _Session(
        _Response(_granite_page("1134")),
        _Response(payload={"Ticker": "MSTP", "fundYield": ""}))
    assert roc.fetch_graniteshares("MSTP", session) is None


def test_GraniteShares_ROC_칸이_비면_값이_없다():
    session = _Session(
        _Response(_granite_page("58")),
        _Response(payload={"Ticker": "COMB", "fundYield": _fund_yield(None)}))
    assert roc.fetch_graniteshares("COMB", session) is None


def test_GraniteShares_id를_못_찾으면_API를_안_부른다():
    session = _Session(_Response("<html>id 없음</html>"))
    assert roc.fetch_graniteshares("TSYY", session) is None
    assert len(session.urls) == 1


# ── 발행사 표 ───────────────────────────────────────────
def test_한_발행사가_두_이름으로_들어와_있어도_잡는다():
    """목록에 Roundhill Financial / Roundhill Investments 두 이름이 있다."""
    names = roc.ISSUERS["Roundhill"]["운용사"]
    assert "Roundhill Financial" in names and "Roundhill Investments" in names


def test_Rex_는_robots의_Crawl_delay를_지킨다():
    assert roc.ISSUERS["Rex"]["쉼"] >= 10.0
