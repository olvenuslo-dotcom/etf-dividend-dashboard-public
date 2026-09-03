# -*- coding: utf-8 -*-
"""KRX 로그인 1회 점검 — 비밀번호를 직접 타이핑해서 서버 응답 코드만 본다.

왜 있나(42단계): 웹 브라우저는 저장된 비밀번호로 자동 로그인되기 때문에
'웹은 되는데 스크립트는 안 된다'가 비밀번호 문제인지 흐름 문제인지 안 갈린다.
이 스크립트는 **타이핑한 값 그대로 1회만** 보내고 코드(CD001=성공, CD006=불일치,
CD007=잠금)를 보여준다. 비밀번호는 화면에 표시하지도 저장하지도 않는다.

실행: venv\\Scripts\\python tools\\krx_login_check.py   (본인 터미널에서 직접)
"""
from pathlib import Path
import getpass
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import truststore
truststore.inject_into_ssl()
import requests

from pykrx.website.comm import auth   # noqa: E402


def main():
    login_id = input("KRX 아이디: ").strip()
    login_pw = getpass.getpass("KRX 비밀번호 (화면에 안 보임): ")
    print(f"(참고) 입력한 비밀번호 길이: {len(login_pw)}자")

    sess = requests.Session()
    auth.warmup_krx_session(sess)
    payload = {"mbrNm": "", "telNo": "", "di": "", "certType": "",
               "mbrId": login_id, "pw": login_pw}
    resp = sess.post(auth.LOGIN_URL, data=payload, timeout=15,
                     headers={"User-Agent": auth.USER_AGENT,
                              "Referer": auth.LOGIN_PAGE})
    data = resp.json()
    code = data.get("_error_code", "")
    print(f"응답 코드: {code} / 메시지: {data.get('_error_message', '')}")
    if code == "CD001":
        print("→ 성공. 이 비밀번호를 .env 의 KRX_PW 에 그대로 넣으면 됩니다.")
    elif code == "CD011":
        print("→ 중복 로그인(다른 세션이 살아 있음). 비밀번호 자체는 맞습니다.")
    elif code == "CD006":
        print("→ 불일치. 방금 타이핑한 값이 실제 비밀번호와 다릅니다.")
    elif code == "CD007":
        print("→ 잠금. data.krx.co.kr 에서 잠금을 풀어야 합니다.")


if __name__ == "__main__":
    main()
