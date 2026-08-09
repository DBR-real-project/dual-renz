"""
FaceForensics++ 사전학습 가중치 다운로더 (분할 병렬 + 이어받기)
담당: 이상원

배포처인 TUM 서버(kaldir.vc.in.tum.de)는 연결 하나당 40~50 KB/s로 매우 느리다.
444 MB를 단일 연결로 받으면 4시간이 넘는다. 다행히 Range 요청을 지원해서
연결을 여러 개 열면 거의 선형으로 빨라진다 (실측: 1연결 44 KB/s -> 4연결 157 KB/s).

이 스크립트는 파일을 N개 구간으로 나눠 동시에 받고, 중간에 끊겨도
구간별 .part 파일에서 이어받는다. 해커톤 중 네트워크가 끊겨도 처음부터 다시
받지 않아도 된다.

실행:
    .venv\\Scripts\\python.exe scripts/download_ff_weights.py
    .venv\\Scripts\\python.exe scripts/download_ff_weights.py --connections 12
"""

import argparse
import sys
import threading
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

URL = "http://kaldir.vc.in.tum.de/FaceForensics/models/faceforensics++_models.zip"
PROJECT_ROOT = Path(__file__).resolve().parent.parent
MODELS_DIR = PROJECT_ROOT / "models"
PARTS_DIR = MODELS_DIR / ".download"
OUT_PATH = MODELS_DIR / "faceforensics++_models.zip"

_lock = threading.Lock()
_downloaded = 0


def get_total_size(url: str) -> int:
    """Range 요청 1바이트로 Content-Range 헤더에서 전체 크기를 얻는다."""
    req = urllib.request.Request(url, headers={"Range": "bytes=0-0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        content_range = r.headers.get("Content-Range")
    if not content_range or "/" not in content_range:
        raise RuntimeError(
            f"서버가 Range 요청을 지원하지 않습니다. 분할 다운로드 불가: {content_range}"
        )
    return int(content_range.rsplit("/", 1)[1])


def download_segment(index: int, start: int, end: int, retries: int = 5) -> Path:
    """[start, end] 구간을 받아 part 파일로 저장. 이미 받은 만큼은 건너뛴다."""
    global _downloaded
    part_path = PARTS_DIR / f"seg_{index:02d}.part"
    expected = end - start + 1

    for attempt in range(retries):
        have = part_path.stat().st_size if part_path.exists() else 0
        if have >= expected:
            with _lock:
                _downloaded += 0  # 이미 카운트됨
            return part_path

        req = urllib.request.Request(
            URL, headers={"Range": f"bytes={start + have}-{end}"}
        )
        try:
            with urllib.request.urlopen(req, timeout=120) as r, open(part_path, "ab") as f:
                while True:
                    chunk = r.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    with _lock:
                        _downloaded += len(chunk)
        except Exception as exc:
            if attempt == retries - 1:
                raise
            print(f"  [구간 {index}] 재시도 {attempt + 1}/{retries - 1}: {exc}", flush=True)
            time.sleep(2 * (attempt + 1))

    have = part_path.stat().st_size if part_path.exists() else 0
    if have != expected:
        raise RuntimeError(f"구간 {index} 크기 불일치: {have} != {expected}")
    return part_path


def merge_parts(part_paths, out_path: Path) -> None:
    with open(out_path, "wb") as out:
        for p in part_paths:
            with open(p, "rb") as f:
                while True:
                    chunk = f.read(1024 * 1024)
                    if not chunk:
                        break
                    out.write(chunk)


def report_progress(total: int, already: int, stop_event: threading.Event) -> None:
    start_time = time.time()
    while not stop_event.wait(10):
        with _lock:
            done = already + _downloaded
        elapsed = time.time() - start_time
        rate = _downloaded / elapsed / 1024 if elapsed > 0 else 0
        remaining = (total - done) / (rate * 1024) if rate > 0 else float("inf")
        print(
            f"  {done / 1024 / 1024:7.1f} / {total / 1024 / 1024:.1f} MB "
            f"({done / total:5.1%})  {rate:6.1f} KB/s  남은시간 ~{remaining / 60:.0f}분",
            flush=True,
        )


def main():
    parser = argparse.ArgumentParser(description="FF++ 가중치 분할 병렬 다운로드")
    parser.add_argument("--connections", type=int, default=8, help="동시 연결 수")
    parser.add_argument("--force", action="store_true", help="기존 파일이 있어도 다시 받기")
    args = parser.parse_args()

    if OUT_PATH.exists() and not args.force:
        print(f"이미 존재합니다: {OUT_PATH} ({OUT_PATH.stat().st_size / 1024 / 1024:.1f} MB)")
        print("다시 받으려면 --force 를 쓰세요.")
        return

    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    PARTS_DIR.mkdir(parents=True, exist_ok=True)

    print(f"대상: {URL}")
    total = get_total_size(URL)
    print(f"전체 크기: {total / 1024 / 1024:.1f} MB, 연결 {args.connections}개로 분할")

    n = args.connections
    seg_size = total // n
    ranges = []
    for i in range(n):
        start = i * seg_size
        end = total - 1 if i == n - 1 else (start + seg_size - 1)
        ranges.append((i, start, end))

    already = sum(
        (PARTS_DIR / f"seg_{i:02d}.part").stat().st_size
        for i, _, _ in ranges
        if (PARTS_DIR / f"seg_{i:02d}.part").exists()
    )
    if already:
        print(f"이어받기: 이미 {already / 1024 / 1024:.1f} MB 받아둔 상태")

    stop_event = threading.Event()
    reporter = threading.Thread(
        target=report_progress, args=(total, already, stop_event), daemon=True
    )
    reporter.start()

    t0 = time.time()
    try:
        with ThreadPoolExecutor(max_workers=n) as ex:
            futures = [ex.submit(download_segment, i, s, e) for i, s, e in ranges]
            part_paths = [f.result() for f in futures]
    finally:
        stop_event.set()

    print(f"\n다운로드 완료: {time.time() - t0:.0f}초. 구간 병합 중...")
    merge_parts(part_paths, OUT_PATH)

    size = OUT_PATH.stat().st_size
    if size != total:
        print(f"[에러] 크기 불일치: {size} != {total}", file=sys.stderr)
        sys.exit(1)

    if not zipfile.is_zipfile(OUT_PATH):
        print("[에러] 받은 파일이 올바른 zip이 아닙니다.", file=sys.stderr)
        sys.exit(1)

    for p in part_paths:
        p.unlink()
    try:
        PARTS_DIR.rmdir()
    except OSError:
        pass

    print(f"완료: {OUT_PATH} ({size / 1024 / 1024:.1f} MB), zip 무결성 확인됨")
    print("압축 해제:")
    print(f"    .venv\\Scripts\\python.exe -c \"import zipfile;"
          f" zipfile.ZipFile(r'{OUT_PATH}').extractall(r'{MODELS_DIR}')\"")


if __name__ == "__main__":
    main()
