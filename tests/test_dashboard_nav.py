# -*- coding: utf-8 -*-
from pathlib import Path
import tomllib

from streamlit.testing.v1 import AppTest


ROOT = Path(__file__).resolve().parents[1]


def test_streamlit_hides_internal_error_details():
    config = tomllib.loads((ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8"))

    assert config["client"]["showErrorDetails"] == "none"


def test_dashboard_exposes_working_nav_analysis_controls():
    app = AppTest.from_file(str(ROOT / "src" / "dashboard.py"))
    # 첫 렌더는 xlsx·ICS·시세 CSV 를 모두 읽어서 느리다(테스트 환경에서 40초 안팎).
    app.run(timeout=120)

    assert not app.exception
    button_labels = [button.label for button in app.button]
    assert "KRX NAV 조회·분석" in button_labels
    info_messages = [item.value for item in app.info]
    assert not any("3단계-A 예정" in message for message in info_messages)

    app.text_input[0].set_value("[")
    # 첫 렌더는 xlsx·ICS·시세 CSV 를 모두 읽어서 느리다(테스트 환경에서 40초 안팎).
    app.run(timeout=120)
    assert not app.exception
