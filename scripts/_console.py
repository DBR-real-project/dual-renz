"""
콘솔 출력 인코딩 고정
담당: 이상원

Windows에서 파이썬 표준 출력은 콘솔 코드페이지(한국어 환경이면 cp949)를 따라간다.
그런데 이 프로젝트의 출력에는 cp949에 없는 글자가 섞여 있다:

  —  (em dash, U+2014)   파이프라인 경고 문구
  █ ·                     analyze_call.py 의 막대 그래프
  ⚠ ★ →                   각종 안내 문구

cp949로 인코딩할 수 없는 글자를 만나면 파이썬은 **UnicodeEncodeError로 죽는다.**
분석은 다 끝났는데 결과를 찍다가 죽는 것이라 원인을 찾기도 어렵다.
실제로 `analyze_call.py`가 오디오 전용 파일을 분석한 뒤
"오디오 전용 파일 — 영상 분석 없음"을 출력하다가 이 이유로 죽었다.

시연 중에 터지면 치명적이므로, CLI 진입점에서 이 함수를 먼저 호출해
표준 출력을 UTF-8로 고정한다. 그래도 안 되는 환경(파이프로 넘길 때 등)을 위해
errors="replace"를 함께 준다 — 글자 하나가 '?'로 바뀔지언정 죽지는 않게.

사용:
    from _console import setup_console
    setup_console()
"""

import sys


def setup_console() -> None:
    """표준 출력/에러를 UTF-8로 재설정한다. 실패해도 조용히 넘어간다."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue  # 파이프로 감싸인 객체 등. 이 경우는 원래 UTF-8이라 문제 없다
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            # 이미 닫혔거나 재설정을 지원하지 않는 스트림. 출력 인코딩을 못 바꿔도
            # 프로그램을 멈출 이유는 없다.
            pass
