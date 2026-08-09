"""
실시간 스트리밍 세션 E2E 점검
담당: 이상원

통화 파일을 5초 청크로 잘라 실제 브라우저처럼 순서대로 밀어 넣고,
구간 결과가 실시간으로 갱신되는지 확인한다. 크롬 확장을 브라우저에 올리지 않고도
**백엔드 쪽 실시간 경로가 동작하는지**를 검증하기 위한 스크립트다.

확인하는 것:
  1. 세션 생성 -> 청크 투입 -> 위험도 갱신 -> 종료
  2. 청크 처리 시간이 청크 길이보다 짧은가 (실시간을 따라갈 수 있는가)
  3. 세션이 열려 있는 동안 파일 분석이 409로 막히는가 (메모리 충돌 방지)
  4. 종료 후 모델이 해제돼 다음 분석이 가능한가

서버를 먼저 띄워야 한다:
    .venv\\Scripts\\python.exe scripts\\run_server.py

실행:
    .venv\\Scripts\\python.exe scripts/test_streaming.py
    .venv\\Scripts\\python.exe scripts/test_streaming.py --input data/korean_calls/scam_call.wav
"""

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT = PROJECT_ROOT / "data" / "korean_calls" / "scam_call.wav"
BASE = "http://127.0.0.1:8000"
CHUNK_SEC = 5.0


def post_file(url: str, name: str, blob: bytes) -> dict:
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\n".encode()
            + f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'.encode()
            + b"Content-Type: application/octet-stream\r\n\r\n"
            + blob + f"\r\n--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        url, data=body,
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        method="POST")
    with urllib.request.urlopen(req, timeout=300) as r:
        return json.load(r)


def request(url: str, method: str = "GET") -> dict:
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def make_chunks(path: Path, chunk_sec: float):
    """wav를 chunk_sec 단위로 잘라 완결된 wav 바이트로 만든다."""
    import io

    import soundfile as sf

    data, sr = sf.read(str(path), dtype="float32")
    if data.ndim > 1:
        data = data.mean(axis=1)
    step = int(chunk_sec * sr)
    for i in range(0, len(data), step):
        piece = data[i:i + step]
        if piece.size == 0:
            continue
        buf = io.BytesIO()
        sf.write(buf, piece, sr, format="WAV", subtype="PCM_16")
        yield i / sr, piece.size / sr, buf.getvalue()


def main():
    parser = argparse.ArgumentParser(description="실시간 스트리밍 세션 E2E 점검")
    parser.add_argument("--input", default=str(DEFAULT_INPUT))
    parser.add_argument("--base", default=BASE)
    parser.add_argument("--chunk-sec", type=float, default=CHUNK_SEC)
    args = parser.parse_args()

    src = Path(args.input)
    if not src.exists():
        print(f"입력 파일이 없습니다: {src}")
        return 1

    try:
        request(f"{args.base}/api/health")
    except Exception as exc:
        print(f"서버에 연결할 수 없습니다 ({exc}).")
        print("  .venv\\Scripts\\python.exe scripts\\run_server.py")
        return 1

    print(f"입력: {src.name}  청크 {args.chunk_sec:.0f}초\n")

    print("1) 세션 생성")
    try:
        sess = request(f"{args.base}/api/sessions", method="POST")
    except urllib.error.HTTPError as e:
        print(f"   실패 HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
        return 1
    sid = sess["session_id"]
    print(f"   session_id={sid}  엔진={sess['engines']['stt']}")

    print("\n2) 세션 중에는 파일 분석이 막히는지")
    try:
        post_file(f"{args.base}/api/analyze", src.name, src.read_bytes())
        print("   [문제] 막히지 않았습니다. 동시에 돌면 프로세스가 죽을 수 있습니다.")
    except urllib.error.HTTPError as e:
        print(f"   HTTP {e.code} — 의도대로 차단됨" if e.code == 409
              else f"   [문제] 예상과 다른 코드 {e.code}")

    print("\n3) 청크 투입")
    print(f"   {'구간':>12}  {'처리(초)':>8}  {'배속':>6}  {'누적점수':>8} {'등급':>4}  새 구간")
    total_audio = 0.0
    total_proc = 0.0
    slow = 0
    for offset, dur, blob in make_chunks(src, args.chunk_sec):
        t0 = time.time()
        try:
            res = post_file(f"{args.base}/api/sessions/{sid}/chunk", "chunk.wav", blob)
        except urllib.error.HTTPError as e:
            print(f"   실패 HTTP {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")
            request(f"{args.base}/api/sessions/{sid}", method="DELETE")
            return 1
        took = time.time() - t0
        total_audio += dur
        total_proc += took
        ratio = dur / took if took else 0.0
        if ratio < 1.0:
            slow += 1
        new = res["new_segments"]
        head = new[0]["transcript"][:26] if new else ""
        print(f"   {offset:5.0f}-{offset + dur:5.0f}s  {took:8.2f}  {ratio:5.1f}x  "
              f"{res['overall_score']:8.1f} {res['overall_level']:>4}  "
              f"{len(new)}개 {head}")

    print(f"\n   오디오 {total_audio:.0f}초를 {total_proc:.0f}초에 처리 "
          f"(평균 {total_audio / total_proc:.1f}배속)")
    if slow:
        print(f"   [주의] {slow}개 청크가 실시간보다 느렸습니다. "
              f"청크를 길게 잡거나 STT를 tiny로 낮추세요.")
    else:
        print("   모든 청크가 실시간보다 빠릅니다.")

    print("\n4) 최종 결과")
    final = request(f"{args.base}/api/sessions/{sid}")
    print(f"   구간 {final['n_segments']}개  콘텐츠 {final['content_risk']:.1f} / "
          f"미디어 {final['media_risk']:.1f} -> {final['overall_score']:.1f} "
          f"({final['overall_level']})")
    for s in final["segments"][:4]:
        cat = f"  [{s['top_category']}]" if s["top_category"] else ""
        print(f"     {s['start']:5.1f}-{s['end']:5.1f}s  {s['fraud_risk_score']:5.1f} "
              f"{s['level']}{cat}  {s['transcript'][:34]}")

    print("\n5) 세션 종료")
    closed = request(f"{args.base}/api/sessions/{sid}", method="DELETE")
    print(f"   closed={closed['closed']}  최종 {closed['overall_score']:.1f} "
          f"({closed['overall_level']})")

    print("\n6) 종료 후 파일 분석이 다시 되는지")
    try:
        job = post_file(f"{args.base}/api/analyze", src.name, src.read_bytes())
        print(f"   OK — job_id={job['job_id']} (분석은 백그라운드에서 진행)")
    except urllib.error.HTTPError as e:
        print(f"   [문제] HTTP {e.code} — 세션 종료 후에도 막혀 있습니다")
        return 1

    print("\n스트리밍 E2E OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
