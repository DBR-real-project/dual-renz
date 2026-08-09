"""
실제 보이스피싱 통화 녹취 확보 (금융감독원 「그놈 목소리」)
담당: 이상원

## 왜 필요한가

지금까지의 검증은 전부 **우리가 만든 TTS 통화**나 **공개 코퍼스 낭독 음성**이었다.
"진짜 보이스피싱 통화에서도 되냐"는 질문에 답할 자료가 없었다.
개인정보 때문에 실제 피해 녹취를 구할 수 없다고 미뤄뒀던 항목이다.

**금융감독원이 이미 공익 목적으로 공개해 둔 자료가 있다.**
보이스피싱지킴이 「그놈 목소리」는 실제 사기범의 통화 녹취를 국민이 직접 들어보고
수법을 익히도록 공개한 것이다. 즉:

  - 실제 통화 녹취가 맞다 (우리가 만든 게 아니다)
  - 개인정보 문제는 **배포처가 이미 처리했다** (사기범 음성, 피해자 정보는 제거됨)
  - 공개 목적 자체가 "수법을 알려 피해를 막는 것" — 탐지 성능 검증은 그 목적에 부합한다

## 예의

공공 서버다. 기본값은 5건이고 요청 사이에 지연을 둔다. 대량 크롤링을 하지 말 것.
받은 파일은 `data/`(gitignore 대상)에만 두고 **저장소에 커밋하지 않는다.**
출처와 이용 조건은 받은 파일 옆 `_출처.txt`에 남긴다.

## 주의: 이 파일들은 합본이다

한 파일에 여러 통화가 이어 붙어 있는 경우가 많다(예: NR1717+1751+1761+1920).
길이도 5분을 넘는다. 파이프라인에 그대로 넣으면 오래 걸리므로
`--max-sec`로 앞부분만 잘라 쓸 수 있게 했다.

실행:
    .venv\\Scripts\\python.exe scripts/fetch_real_call_samples.py
    .venv\\Scripts\\python.exe scripts/fetch_real_call_samples.py --count 8 --max-sec 90
"""

import argparse
import re
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "data" / "real_calls"

BASE = "https://www.fss.or.kr"
# 「그놈 목소리」 게시판. 유형별로 게시판이 나뉘어 있다.
BOARDS = [
    ("B0000207", "200691", "수사기관 사칭형"),
]

UA = "Mozilla/5.0 (compatible; DualGuard-research/1.0)"
DELAY_SEC = 1.5          # 공공 서버에 부담 주지 않기 위한 요청 간격

SOURCE_NOTE = """실제 보이스피싱 통화 녹취

출처: 금융감독원 보이스피싱지킴이 「그놈 목소리」
      https://www.fss.or.kr/fss/bbs/B0000207/list.do?menuNo=200691

금융감독원이 국민이 사기 수법을 직접 듣고 익히도록 공개한 실제 통화 녹취입니다.
사기범의 음성이며 피해자 개인정보는 배포처에서 이미 제거한 자료입니다.

DualGuard에서는 탐지 성능 검증 용도로만 사용합니다.
- 저장소에 커밋하지 않습니다 (data/ 는 .gitignore 대상)
- 재배포하지 않습니다
- 발표에 인용할 때는 반드시 출처를 함께 밝힙니다

받은 파일:
"""


def get(url: str, binary: bool = False):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        data = r.read()
        return (data, dict(r.headers)) if binary else data.decode("utf-8", "replace")


def list_posts(bbs_id: str, menu_no: str) -> list:
    """게시판 목록에서 글 번호(nttId)를 뽑는다."""
    html = get(f"{BASE}/fss/bbs/{bbs_id}/list.do?menuNo={menu_no}")
    return sorted(set(re.findall(r"nttId=?[\"'(]?(\d{3,})", html)), reverse=True)


def find_attachment(bbs_id: str, menu_no: str, ntt_id: str):
    """상세 페이지에서 첨부 파일 링크와 제목을 뽑는다."""
    html = get(f"{BASE}/fss/bbs/{bbs_id}/view.do?nttId={ntt_id}&menuNo={menu_no}")
    m = re.search(r"[\"']([^\"']*fileDown\.do[^\"']*)[\"']", html)
    if not m:
        return None, None
    link = m.group(1).replace("&amp;", "&")
    if link.startswith("/"):
        link = BASE + link
    title = None
    tm = re.search(r'<h4[^>]*>(.*?)</h4>', html, re.S)
    if tm:
        title = re.sub(r"<[^>]+>", "", tm.group(1)).strip()[:40]
    return link, title


def filename_from_headers(headers: dict, fallback: str) -> str:
    cd = headers.get("Content-Disposition", "")
    m = re.search(r'filename="([^"]+)"', cd)
    if not m:
        return fallback
    name = urllib.parse.unquote(m.group(1).replace("+", " "))
    return re.sub(r'[\\/:*?"<>|]', "_", name)


def trim(src: Path, max_sec: float) -> bool:
    """앞부분만 잘라 wav로 변환한다. 합본이 길어 파이프라인이 느려지는 걸 막는다."""
    dst = src.with_suffix(".wav")
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", "-i", str(src),
           "-t", str(max_sec), "-ac", "1", "-ar", "16000", str(dst)]
    ok = subprocess.run(cmd, capture_output=True).returncode == 0
    if ok:
        src.unlink(missing_ok=True)
    return ok


def main():
    parser = argparse.ArgumentParser(description="실제 보이스피싱 녹취 확보 (금감원 공개 자료)")
    parser.add_argument("--count", type=int, default=5, help="받을 개수 (공공 서버 배려)")
    parser.add_argument("--max-sec", type=float, default=120.0,
                        help="앞에서 이만큼만 잘라 쓴다 (0이면 원본 유지)")
    parser.add_argument("--out", default=str(OUT_DIR))
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    print("금융감독원 「그놈 목소리」 — 실제 보이스피싱 통화 녹취")
    print("  공익 목적으로 공개된 자료입니다. 대량 수집하지 마세요.\n")

    saved = []
    for bbs_id, menu_no, label in BOARDS:
        try:
            posts = list_posts(bbs_id, menu_no)
        except Exception as exc:
            print(f"[실패] 목록 조회 ({label}): {type(exc).__name__}: {exc}")
            continue
        print(f"{label}: 글 {len(posts)}건 확인")

        for ntt_id in posts:
            if len(saved) >= args.count:
                break
            time.sleep(DELAY_SEC)
            try:
                link, title = find_attachment(bbs_id, menu_no, ntt_id)
            except Exception as exc:
                print(f"  [건너뜀] {ntt_id}: {type(exc).__name__}")
                continue
            if not link:
                continue

            time.sleep(DELAY_SEC)
            try:
                data, headers = get(link, binary=True)
            except Exception as exc:
                print(f"  [실패] {ntt_id} 내려받기: {type(exc).__name__}")
                continue

            name = filename_from_headers(headers, f"gnom_{ntt_id}.mp3")
            if not name.lower().endswith((".mp3", ".wav", ".m4a")):
                print(f"  [건너뜀] {ntt_id}: 음성 파일이 아님 ({name[:40]})")
                continue

            dst = out_dir / f"realscam__{name}"
            dst.write_bytes(data)
            note = f"{dst.name}  ({len(data) / 1024**2:.1f} MB)"
            if args.max_sec > 0 and trim(dst, args.max_sec):
                dst = dst.with_suffix(".wav")
                note += f" -> 앞 {args.max_sec:.0f}초만 wav로"
            saved.append((dst, title or label))
            print(f"  받음: {note}")

    if not saved:
        print("\n[실패] 아무것도 받지 못했습니다. 사이트 구조가 바뀌었을 수 있습니다.")
        print("       https://www.fss.or.kr/fss/bbs/B0000207/list.do?menuNo=200691 확인")
        return 1

    (out_dir / "_출처.txt").write_text(
        SOURCE_NOTE + "".join(f"  {p.name}   {t}\n" for p, t in saved),
        encoding="utf-8")

    print(f"\n저장: {out_dir}  ({len(saved)}건)")
    print("\n다음 단계 (실제 통화에서의 성능 확인):")
    print("  .venv\\Scripts\\python.exe scripts\\validate_real_calls.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
