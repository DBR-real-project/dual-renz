"""
LLM 분류 클라이언트 경로 검증 (실제 모델 없이)
담당: 이상원

## 왜 필요한가

`llm_classifier.py`는 세 백엔드(Claude/Gemini/Ollama)를 지원하는데 **한 번도 실행된 적이
없다.** API 키가 없고 Ollama도 안 깔려 있어서다. 그런데 심사 당일 누가 키를 넣는 순간
이 코드가 처음 돌아간다. 프롬프트 조립이나 JSON 스키마 파싱에 오타 하나만 있어도
그 자리에서 무너진다.

이 스크립트는 **Ollama API 모양을 흉내 내는 스텁 서버**를 띄워 클라이언트 코드를
실제로 실행시킨다. 모델 성능을 재는 게 아니라 **배관이 뚫려 있는지**를 본다:

  ✔ 요청 본문이 Ollama가 받는 모양인가 (model/messages/format/stream/options)
  ✔ 시스템 프롬프트와 사용자 프롬프트가 실제로 실려 나가는가
  ✔ JSON 스키마가 format으로 전달되는가
  ✔ 정상 응답을 ContentRiskBreakdown으로 파싱하는가 (8개 카테고리, 집계 공식)
  ✔ 빈 응답 / 깨진 JSON / 서버 다운에서 예외가 나고 파이프라인이 폴백하는가
  ✔ available 판정이 /api/tags를 제대로 읽는가

**검증되지 않는 것**: 실제 모델의 분류 정확도. 그건 진짜 키나 Ollama가 있어야 한다.

실행:
    .venv\\Scripts\\python.exe scripts/test_llm_client.py
"""

import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "src"))

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

PORT = 11499  # 실제 Ollama(11434)와 겹치지 않게
HOST = f"http://127.0.0.1:{PORT}"
MODEL = "stub-model"

CATEGORIES = ["urgency", "authority", "money_transfer", "credentials",
              "secrecy", "emotional", "trust_building", "isolation"]

failures = []


def check(ok: bool, label: str, extra: str = "") -> bool:
    print(f"   {'OK  ' if ok else 'FAIL'} {label}{('  ' + extra) if extra else ''}")
    if not ok:
        failures.append(label)
    return ok


class StubHandler(BaseHTTPRequestHandler):
    """Ollama /api/tags 와 /api/chat 을 흉내 낸다."""

    mode = "ok"          # ok | empty | broken
    last_request = None

    def log_message(self, *args):
        pass             # 요청 로그로 출력을 어지럽히지 않는다

    def _json(self, code, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/tags":
            self._json(200, {"models": [{"name": f"{MODEL}:latest"}]})
        else:
            self._json(404, {"error": "not found"})

    def do_POST(self):
        if self.path != "/api/chat":
            self._json(404, {"error": "not found"})
            return
        n = int(self.headers.get("Content-Length", 0))
        StubHandler.last_request = json.loads(self.rfile.read(n).decode("utf-8"))

        if StubHandler.mode == "empty":
            self._json(200, {"message": {"content": ""}})
            return
        if StubHandler.mode == "broken":
            self._json(200, {"message": {"content": "{이건 JSON이 아님"}})
            return

        # 스키마를 지키는 정상 응답. 값은 고정이라 파싱 결과를 정확히 대조할 수 있다.
        #
        # 모양은 요청에 실려 온 format(JSON 스키마)을 그대로 따른다. 처음엔
        # {"categories":[...]} 배열로 만들었다가 전부 0점이 나왔는데, 실제 스키마는
        # 카테고리를 **최상위 키로 나열하는 평면 객체**였다. 스키마에서 모양을
        # 읽어 만들면 이런 어긋남이 다시 생기지 않는다.
        scores = {c: 0 for c in CATEGORIES}
        scores["urgency"] = 80
        scores["money_transfer"] = 60
        scores["authority"] = 40
        payload = dict(scores)
        payload["evidence"] = ["지금 즉시 안전계좌로 이체"]
        payload["summary"] = "긴급성과 금전 요구가 함께 나타난다"
        self._json(200, {"message": {"content": json.dumps(payload, ensure_ascii=False)}})


def start_server():
    srv = HTTPServer(("127.0.0.1", PORT), StubHandler)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    return srv


def main():
    print("LLM 분류 클라이언트 경로 검증 (스텁 서버로 실제 코드 실행)\n")

    os.environ["DUALGUARD_LLM_PROVIDER"] = "ollama"
    os.environ["OLLAMA_HOST"] = HOST
    os.environ["OLLAMA_MODEL"] = MODEL

    srv = start_server()
    try:
        from content_analysis import llm_classifier as L

        print("1) 백엔드 선택")
        clf = L.OllamaClassifier(model=MODEL, host=HOST)
        check(clf.available, "available: /api/tags를 읽고 모델을 찾았다", clf.label)

        # get_shared_classifier가 환경변수를 보고 ollama를 고르는지
        L._shared = None
        shared = L.get_shared_classifier()
        check(getattr(shared, "provider", None) == "ollama",
              "DUALGUARD_LLM_PROVIDER=ollama 를 따른다",
              getattr(shared, "provider", "?"))

        print("\n2) 정상 응답 파싱")
        StubHandler.mode = "ok"
        br = clf.classify("지금 즉시 안전계좌로 이체하지 않으면 계좌가 동결됩니다",
                          context_before="앞 문장", context_after="뒤 문장")
        req = StubHandler.last_request
        check(req is not None, "요청이 서버에 도달")
        check(req.get("model") == MODEL, "요청 본문에 model")
        check(req.get("stream") is False, "stream=False (한 번에 받기)")
        fmt = req.get("format")
        check(isinstance(fmt, dict) and "properties" in fmt,
              "JSON 스키마가 format으로 전달됨")
        # 스텁 응답이 스키마와 어긋나면 테스트가 조용히 무의미해진다. 여기서 못 박는다.
        props = set((fmt or {}).get("properties", {}))
        check(set(CATEGORIES).issubset(props),
              "스키마가 8개 카테고리를 최상위 키로 요구한다 (스텁 응답 모양과 일치)",
              f"{len(props)}개 키")
        check(req.get("options", {}).get("temperature") == 0,
              "temperature=0 (분류는 결정적으로)")
        msgs = req.get("messages", [])
        check(len(msgs) == 2 and msgs[0]["role"] == "system",
              "시스템/사용자 메시지 2개")
        check("사회공학" in msgs[0]["content"] or "카테고리" in msgs[0]["content"]
              or "urgency" in msgs[0]["content"],
              "시스템 프롬프트에 분류 지침이 실렸다",
              f"{len(msgs[0]['content'])}자")
        check("앞 문장" in msgs[1]["content"] and "뒤 문장" in msgs[1]["content"],
              "앞뒤 문맥이 사용자 프롬프트에 실렸다")

        check(br.is_llm is True, "결과가 LLM 산출로 표시됨")
        check(br.top_category == "urgency" and abs(br.top_score - 80) < 1e-6,
              "최고 카테고리 파싱", f"{br.top_category} {br.top_score}")
        # 집계 공식: 0.5*최고 + 0.5*상위3평균 = 0.5*80 + 0.5*(80+60+40)/3 = 70.0
        check(abs(br.content_risk - 70.0) < 0.01,
              "집계 공식 그대로 적용 (0.5*최고 + 0.5*상위3평균)",
              f"{br.content_risk:.2f} (기대 70.00)")
        check(len(br.category_scores) == 8, "8개 카테고리 모두 채워짐",
              str(len(br.category_scores)))

        print("\n3) 비정상 응답 처리")
        StubHandler.mode = "empty"
        try:
            clf.classify("아무 말")
            check(False, "빈 응답에서 예외 발생")
        except Exception as exc:
            check(True, "빈 응답에서 예외 발생", type(exc).__name__)

        StubHandler.mode = "broken"
        try:
            clf.classify("아무 말")
            check(False, "깨진 JSON에서 예외 발생")
        except Exception as exc:
            check(True, "깨진 JSON에서 예외 발생", type(exc).__name__)

        print("\n4) 서버가 없을 때")
        dead = L.OllamaClassifier(model=MODEL, host="http://127.0.0.1:1")
        check(dead.available is False, "available이 False로 떨어진다 (폴백 조건)")

        print("\n5) 파이프라인 폴백 (LLM이 죽어도 분석은 계속된다)")
        StubHandler.mode = "broken"
        from content_analysis.content_risk import classify_offline
        off = classify_offline("지금 즉시 안전계좌로 이체하지 않으면 계좌가 동결됩니다")
        check(off.content_risk > 50,
              "오프라인 분류기가 같은 문장을 잡는다 (폴백 경로)",
              f"{off.content_risk:.1f}")

    finally:
        srv.shutdown()
        for k in ("DUALGUARD_LLM_PROVIDER", "OLLAMA_HOST", "OLLAMA_MODEL"):
            os.environ.pop(k, None)

    print()
    if failures:
        print(f"실패 {len(failures)}건: {', '.join(failures[:5])}")
        return 1
    print("LLM 클라이언트 경로 검증 통과")
    print("※ 검증한 것은 '배관'이다. 실제 모델의 분류 정확도는 진짜 키나 Ollama가 있어야 잰다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
