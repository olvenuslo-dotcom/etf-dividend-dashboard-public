# -*- coding: utf-8 -*-
"""콘솔 출력 인코딩 보정.

윈도우 기본 콘솔은 cp949라, 진행 메시지에 섞인 '—' 같은 기호에서
UnicodeEncodeError 로 스크립트가 통째로 죽는다(엑셀은 이미 저장된 뒤라
결과는 멀쩡한데 명령만 실패한 것처럼 보인다).
각 스크립트의 __main__ 에서 한 번 불러 stdout/stderr 을 utf-8 로 바꾼다.
"""
import sys


def use_utf8_stdout() -> None:
    """stdout/stderr 을 utf-8 로 다시 연다. 안 되는 환경이면 조용히 넘어간다."""
    for stream in (sys.stdout, sys.stderr):
        # 파이프로 넘길 때처럼 reconfigure 가 없는 객체면 건드리지 않는다
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            # errors="replace": 그래도 못 쓰는 글자가 있으면 죽지 말고 '?' 로
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
