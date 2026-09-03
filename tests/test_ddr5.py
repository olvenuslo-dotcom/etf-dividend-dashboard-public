# -*- coding: utf-8 -*-
"""DDR5 현물가 파싱·누적. 네트워크를 타지 않는다(HTML 조각만 쓴다)."""
from datetime import date
from pathlib import Path
import sys

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

import ddr5


HTML = """
<table>
  <tr><th>Item</th><th>Daily High</th><th>Daily Low</th>
      <th>Session High</th><th>Session Low</th><th>Session Average</th></tr>
  <tr><td>DDR5 16Gb (2Gx8) 4800/5600</td><td>68.00</td><td>33.00</td>
      <td>68.00</td><td>33.00</td><td>51.50</td></tr>
  <tr><td>DDR4 16Gb</td><td>10.00</td><td>9.00</td>
      <td>10.00</td><td>9.00</td><td>9.50</td></tr>
</table>
<table>
  <tr><th>Item</th><th>Session High</th><th>Session Low</th><th>Session Average</th></tr>
  <tr><td>DDR5 RDIMM 32GB 4800/5600</td><td>1,750.00</td><td>1,450.00</td><td>1,545.00</td></tr>
</table>
<table>
  <tr><td>LPDDR5X 16GB</td><td>LPDDR5X 12GB</td></tr>
</table>
"""


def test_DDR5만_골라내고_DDR4는_버린다():
    out = ddr5.parse(HTML, date(2026, 8, 7))

    assert set(out["품목"]) == {"DDR5 16Gb (2Gx8) 4800/5600",
                                "DDR5 RDIMM 32GB 4800/5600"}


def test_쉼표가_든_가격을_숫자로_읽는다():
    out = ddr5.parse(HTML, date(2026, 8, 7))
    rdimm = out[out["품목"] == "DDR5 RDIMM 32GB 4800/5600"].iloc[0]

    assert rdimm["평균가"] == 1545.0
    assert rdimm["고가"] == 1750.0


def test_값이_없는_줄은_버린다():
    """머리글 없는 표에서 품목 이름만 딸려 들어오는 것을 막는다."""
    out = ddr5.parse(HTML, date(2026, 8, 7))

    assert "LPDDR5X 16GB" not in set(out["품목"])
    assert out["평균가"].notna().all()


def test_표마다_칸_위치가_달라도_평균가를_찾는다():
    # 첫 표는 Session Average 가 6번째, 둘째 표는 4번째다
    out = ddr5.parse(HTML, date(2026, 8, 7))
    chip = out[out["품목"] == "DDR5 16Gb (2Gx8) 4800/5600"].iloc[0]

    assert chip["평균가"] == 51.50


def test_같은_날_다시_받으면_덮어쓴다(tmp_path, monkeypatch):
    monkeypatch.setattr(ddr5, "HISTORY_CSV", tmp_path / "ddr5.csv")

    첫번째 = pd.DataFrame([{"날짜": "2026-08-07", "품목": "A",
                            "평균가": 50.0, "고가": 60.0, "저가": 40.0}])
    두번째 = pd.DataFrame([{"날짜": "2026-08-07", "품목": "A",
                            "평균가": 55.0, "고가": 60.0, "저가": 40.0}])
    ddr5.append(첫번째)
    ddr5.append(두번째)

    history = ddr5.load_history()

    assert len(history) == 1                     # 중복이 쌓이지 않는다
    assert history["평균가"].iloc[0] == 55.0     # 새 값으로 바뀐다


def test_다른_날짜는_쌓인다(tmp_path, monkeypatch):
    monkeypatch.setattr(ddr5, "HISTORY_CSV", tmp_path / "ddr5.csv")

    for day, price in [("2026-08-06", 50.0), ("2026-08-07", 51.5)]:
        ddr5.append(pd.DataFrame([{"날짜": day, "품목": ddr5.MAIN_ITEM,
                                   "평균가": price, "고가": None, "저가": None}]))

    out = ddr5.series(ddr5.MAIN_ITEM)

    assert len(out) == 2
    assert list(out["평균가"]) == [50.0, 51.5]    # 날짜순으로 정렬된다


def test_이력이_없으면_빈_표를_준다(tmp_path, monkeypatch):
    monkeypatch.setattr(ddr5, "HISTORY_CSV", tmp_path / "없는파일.csv")

    assert ddr5.load_history().empty
    assert ddr5.series().empty
