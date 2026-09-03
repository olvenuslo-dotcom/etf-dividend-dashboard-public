# -*- coding: utf-8 -*-
"""`streamlit run src\\dashboard.py` 로 실제 실행될 때의 임포트 환경을 재현한다.

pytest 는 CWD(프로젝트 루트)를 sys.path 에 넣어 주지만 `streamlit run` 은 그러지 않고
**스크립트가 있는 src 폴더**를 넣는다. 그래서 pytest 에서는 통과하는데 실행하면
`ModuleNotFoundError: No module named 'src'` 로 죽는 일이 실제로 있었다.
"""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "src" / "dashboard.py"


def test_dashboard_imports_with_only_its_own_folder_on_path(tmp_path):
    code = (
        "import runpy, sys; "
        f"sys.path.insert(0, r'{ROOT / 'src'}'); "
        # run_name 을 '__main__' 이 아닌 값으로 줘서 main() 은 실행하지 않는다
        f"runpy.run_path(r'{DASHBOARD}', run_name='dashboard')"
    )

    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(tmp_path),          # 프로젝트 루트가 CWD 가 아닌 상황을 만든다
        capture_output=True, text=True, timeout=300,
    )

    assert proc.returncode == 0, f"대시보드 임포트 실패:\n{proc.stderr[-2000:]}"
