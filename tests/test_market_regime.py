# -*- coding: utf-8 -*-
"""시장국면 데이터 준비 — 가짜 CSV 만 쓰고 네트워크를 타지 않는다."""
from pathlib import Path
import sys

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import ddr5
import ecos
import market_regime as mr


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """세 출처(블룸버그·TrendForce·ECOS)를 전부 임시 폴더로 돌린다."""
    bloomberg = tmp_path / "블룸버그.csv"
    monkeypatch.setattr(mr, "BLOOMBERG_CSV", bloomberg)
    monkeypatch.setattr(ddr5, "HISTORY_CSV", tmp_path / "ddr5.csv")
    monkeypatch.setattr(ecos, "DATA_DIR", tmp_path / "ecos")
    return tmp_path


def write_bloomberg(path: Path, rows: list[tuple[str, float, float]]) -> None:
    frame = pd.DataFrame(rows, columns=["날짜", "DRAM_DDR4_8Gb", "국고채10년"])
    frame["NAND_512Gb"] = 1.0
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def write_ecos(name: str, rows: list[tuple[str, float]]) -> None:
    ecos.DATA_DIR.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=["날짜", "값"]).to_csv(
        ecos.csv_path(name), index=False, encoding="utf-8-sig")


def test_금리는_블룸버그_구간_이후만_ECOS로_잇는다(sandbox):
    write_bloomberg(mr.BLOOMBERG_CSV, [("2026-08-05", 49.66, 4.146),
                                       ("2026-08-06", 49.66, 4.191)])
    # ECOS 에 겹치는 날(8/6, 값이 미세하게 다름)과 새 날(8/7)이 있다
    write_ecos("국고채10년", [("20260806", 4.20), ("20260807", 4.191)])

    out = mr.rate_series()

    assert len(out) == 3
    # 겹친 8/6 은 블룸버그 값이 남는다 (한 출처 안에서 섞지 않는다)
    assert out[out["날짜"] == "2026-08-06"]["금리"].iloc[0] == 4.191
    assert out[out["날짜"] == "2026-08-07"]["금리"].iloc[0] == 4.191


def test_DDR4와_DDR5는_다른_열로_나란히_둔다(sandbox):
    write_bloomberg(mr.BLOOMBERG_CSV, [("2026-08-06", 49.66, 4.191)])
    ddr5.append(pd.DataFrame([{"날짜": "2026-08-07", "품목": ddr5.MAIN_ITEM,
                               "평균가": 51.6, "고가": None, "저가": None}]))

    out = mr.dram_vs_rate()

    assert set(out.columns) >= {"날짜", "DRAM_DDR4", "DDR5", "금리"}
    assert len(out) == 2                                  # 두 날이 합쳐진다
    assert out["DRAM_DDR4"].notna().sum() == 1            # 서로 다른 날 하나씩
    assert out["DDR5"].notna().sum() == 1


def test_수출은_월별이라_금리를_월평균으로_맞춘다(sandbox):
    write_bloomberg(mr.BLOOMBERG_CSV, [("2026-06-01", 40.0, 4.0),
                                       ("2026-06-02", 40.5, 5.0)])
    write_ecos("수출금액", [("202606", 60000000.0)])

    out = mr.exports_vs_rate()

    assert len(out) == 1
    assert out["금리"].iloc[0] == pytest.approx(4.5)      # (4.0+5.0)/2


def test_표본이_10개_미만이면_상관을_내지_않는다():
    frame = pd.DataFrame({"a": range(5), "b": range(5)})

    assert mr.correlation(frame, "a", "b") is None


def test_자료가_하나도_없어도_죽지_않는다(sandbox):
    assert mr.load_bloomberg().empty
    assert mr.rate_series().empty
    assert mr.dram_vs_rate().empty
    assert mr.exports_vs_rate().empty


def test_예탁금은_원을_조원으로_바꾸고_평균을_붙인다(sandbox):
    # ECOS 원 단위 그대로: 100조·120조·110조
    write_ecos("투자자예탁금", [("202505", 100e12), ("202506", 120e12),
                               ("202507", 110e12)])

    out = mr.deposits()

    assert len(out) == 3
    assert list(out["예탁금_조원"]) == [100.0, 120.0, 110.0]
    assert out["평균_조원"].iloc[0] == pytest.approx(110.0)


def test_예탁금_자료가_없으면_빈_표다(sandbox):
    assert mr.deposits().empty


def test_예탁금_이동평균은_창이_쌓여야_나온다(sandbox):
    """수평선(전체 평균)과 별개로 최근 추세를 보는 3·6·12개월 이동평균 (41단계)."""
    months = [f"2024{m:02d}" for m in range(8, 13)] + \
             [f"2025{m:02d}" for m in range(1, 8)]          # 12개월 연속
    write_ecos("투자자예탁금", [(m, 100e12) for m in months])

    out = mr.deposits()

    assert len(out) == 12
    for window in mr.MA_WINDOWS:                            # 3, 6, 12
        column = f"이동평균{window}_조원"
        # 창이 안 쌓인 앞부분은 **비운다** — 짧은 창으로 그럴듯한 값을 안 만든다
        assert out[column].iloc[:window - 1].isna().all()
        assert out[column].iloc[-1] == pytest.approx(100.0)


def caps_sandbox(sandbox, monkeypatch):
    """krx_api 의 두 시총 CSV 를 임시 폴더로 돌린다 (실제 데이터가 새면 안 된다)."""
    import krx_api
    monkeypatch.setattr(krx_api, "CAPS_CSV", sandbox / "코스피.csv")
    monkeypatch.setattr(krx_api, "KOSDAQ_CAPS_CSV", sandbox / "코스닥.csv")
    return krx_api


def write_caps(path, rows):
    pd.DataFrame([{"월": m, "기준일": m + "30", "시총_조원": v} for m, v in rows]) \
        .to_csv(path, index=False, encoding="utf-8-sig")


def test_예탁금_시총_비중은_같은_달끼리_나눈다(sandbox, monkeypatch):
    krx_api = caps_sandbox(sandbox, monkeypatch)
    write_ecos("투자자예탁금", [("202506", 120e12), ("202507", 100e12)])
    write_caps(krx_api.CAPS_CSV, [("202506", 6000.0), ("202507", 5000.0)])

    out = mr.deposit_ratio()

    assert len(out) == 2
    assert list(out["비중"]) == [2.0, 2.0]        # 120/6000, 100/5000
    assert out["평균"].iloc[0] == pytest.approx(2.0)
    assert out["분모시장"].iloc[0] == "KOSPI"     # 코스닥 이력이 없을 때


def test_코스닥이_있으면_분모를_합산한다(sandbox, monkeypatch):
    krx_api = caps_sandbox(sandbox, monkeypatch)
    write_ecos("투자자예탁금", [("202506", 120e12), ("202507", 100e12)])
    write_caps(krx_api.CAPS_CSV, [("202506", 5000.0), ("202507", 4500.0)])
    # 코스닥은 202507 만 있다 → 분모가 달라지지 않게 202506 은 버린다
    write_caps(krx_api.KOSDAQ_CAPS_CSV, [("202507", 500.0)])

    out = mr.deposit_ratio()

    assert len(out) == 1
    assert out["비중"].iloc[0] == 2.0             # 100 / (4500+500)
    assert out["분모시장"].iloc[0] == "KOSPI+KOSDAQ"


def test_시총이_없는_달은_비중에서_빠진다(sandbox, monkeypatch):
    krx_api = caps_sandbox(sandbox, monkeypatch)
    write_ecos("투자자예탁금", [("202506", 120e12), ("202507", 100e12)])
    write_caps(krx_api.CAPS_CSV, [("202507", 5000.0)])

    out = mr.deposit_ratio()

    assert len(out) == 1                          # 겹치는 달만


def test_시총_수집은_받은_달을_다시_부르지_않는다(sandbox, monkeypatch):
    import krx_api
    monkeypatch.setattr(krx_api, "CAPS_CSV", sandbox / "시총.csv")
    monkeypatch.setattr(krx_api, "month_end_sessions",
                        lambda months: ["20260630", "20260731"])
    calls = []

    def fake_fetch(bas_dd):
        calls.append(bas_dd)
        return 5000.0

    krx_api.collect(2, fetcher=fake_fetch)
    krx_api.collect(2, fetcher=fake_fetch)        # 두 번째는 다 캐시

    # 이번 달(202608)이 아닌 지나간 달들은 한 번씩만 불린다
    assert calls == ["20260630", "20260731"]
    assert len(krx_api.load()) == 2
