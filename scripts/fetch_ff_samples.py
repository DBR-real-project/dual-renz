"""
FaceForensics++ C23 검증용 샘플 확보 (zip 전체를 받지 않고 필요한 것만)
담당: 이상원

기획서 "데이터 확보 방안"의 [영상] 항목 대응. 공식 FF++는 ToS 승인 대기가 걸리고,
Kaggle 미러는 계정이 필요하다. HuggingFace의 `bitmind/FaceForensicsC23`은
게이트가 없어 인증 없이 받을 수 있는데, **17.9GB 통짜 zip 하나**로 올라와 있다.

해커톤 일정에 17.9GB는 무리다. 그런데 HuggingFace CDN이 HTTP Range를 지원하고
(206 응답 확인), zip은 파일 끝에 중앙 디렉터리(목차)가 있는 구조다. 그래서:

    1. 파일 끝 일부만 받아 목차를 읽고
    2. 원하는 영상 몇 개의 위치를 알아낸 뒤
    3. 그 구간만 Range로 받는다

이렇게 하면 17.9GB 중 실제로는 수십 MB만 받고도 real/fake 샘플을 확보할 수 있다.

왜 이 데이터셋인가:
  우리 탐지 모델(FF++ Xception c23)이 **바로 이 데이터로 학습됐다.** 다른 데이터셋으로
  검증하면 크로스도메인 성능 저하가 섞여 들어와 "모델이 나쁜 건지 도메인이 다른 건지"
  구분이 안 된다. 같은 분포에서 먼저 검증해야 기준선이 생긴다.

실행:
    .venv\\Scripts\\python.exe scripts/fetch_ff_samples.py --list
    .venv\\Scripts\\python.exe scripts/fetch_ff_samples.py --real 4 --fake 4
"""

import argparse
import io
import json
import sys
import urllib.request
import zipfile
from pathlib import Path

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

DATASET_URL = (
    "https://huggingface.co/datasets/bitmind/FaceForensicsC23/"
    "resolve/main/FaceForensics%2B%2B_C23.zip"
)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "data" / "ff_samples"
INDEX_CACHE = OUT_DIR / "_zip_index.json"


class HttpRangeFile(io.RawIOBase):
    """
    HTTP Range 요청으로 원격 파일을 로컬 파일처럼 읽게 해주는 어댑터.
    zipfile이 seek/read만 요구하므로 그 둘만 구현하면 된다.

    zipfile은 목차를 읽을 때 작은 read를 여러 번 날린다. 매번 HTTP 요청을 보내면
    느려지므로 chunk_size 단위로 미리 받아 버퍼에 들고 있는다.
    """

    def __init__(self, url: str, chunk_size: int = 1 << 20):
        self.url = url
        self.chunk_size = chunk_size
        self._pos = 0
        self._buf = b""
        self._buf_start = -1
        self.requests = 0
        self.bytes_fetched = 0
        self.size = self._probe_size()

    def _probe_size(self) -> int:
        req = urllib.request.Request(self.url, headers={"Range": "bytes=0-0"})
        with urllib.request.urlopen(req, timeout=60) as r:
            cr = r.headers.get("Content-Range")
        if not cr or "/" not in cr:
            raise RuntimeError(f"서버가 Range를 지원하지 않습니다: {cr}")
        return int(cr.rsplit("/", 1)[1])

    def _fetch(self, start: int, end: int) -> bytes:
        end = min(end, self.size - 1)
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={start}-{end}"})
        with urllib.request.urlopen(req, timeout=180) as r:
            data = r.read()
        self.requests += 1
        self.bytes_fetched += len(data)
        return data

    # --- file-like 인터페이스 ---
    def seekable(self) -> bool:
        return True

    def readable(self) -> bool:
        return True

    def tell(self) -> int:
        return self._pos

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        if whence == io.SEEK_SET:
            self._pos = offset
        elif whence == io.SEEK_CUR:
            self._pos += offset
        elif whence == io.SEEK_END:
            self._pos = self.size + offset
        else:
            raise ValueError(f"잘못된 whence: {whence}")
        return self._pos

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = self.size - self._pos
        n = min(n, self.size - self._pos)
        if n <= 0:
            return b""

        # 큰 요청은 버퍼를 거치지 않고 바로 받는다 (영상 파일 추출 시)
        if n > self.chunk_size:
            data = self._fetch(self._pos, self._pos + n - 1)
            self._pos += len(data)
            return data

        if not (self._buf_start >= 0
                and self._buf_start <= self._pos
                and self._pos + n <= self._buf_start + len(self._buf)):
            start = self._pos
            self._buf = self._fetch(start, start + self.chunk_size - 1)
            self._buf_start = start

        off = self._pos - self._buf_start
        data = self._buf[off:off + n]
        self._pos += len(data)
        return data


def load_index(force: bool = False) -> list:
    """zip 목차(파일 경로 목록)를 읽어 캐시한다. 매번 읽으면 느리다."""
    if INDEX_CACHE.exists() and not force:
        return json.loads(INDEX_CACHE.read_text(encoding="utf-8"))

    print("원격 zip 목차 읽는 중... (파일 끝부분만 받습니다)")
    remote = HttpRangeFile(DATASET_URL)
    print(f"  전체 크기: {remote.size / 1024**3:.2f} GB")
    with zipfile.ZipFile(remote) as zf:
        names = zf.namelist()
    print(f"  목차 완료: 항목 {len(names)}개, "
          f"실제 받은 양 {remote.bytes_fetched / 1024**2:.1f} MB "
          f"(요청 {remote.requests}회)")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    INDEX_CACHE.write_text(json.dumps(names, ensure_ascii=False), encoding="utf-8")
    return names


def classify(names: list) -> dict:
    """
    이 미러의 구조를 real / 조작기법별로 나눈다.

        FaceForensics++_C23/real/<id>.mp4                 (1000개)
        FaceForensics++_C23/fake/<기법>/<id>.mp4           (6기법 x 1000개)

    공식 FF++의 original_sequences/manipulated_sequences 구조와 다르므로,
    공식 배포본을 쓰게 되면 이 함수를 고쳐야 한다.
    """
    buckets = {"real": []}
    for n in names:
        if n.endswith("/") or not n.lower().endswith(".mp4"):
            continue
        parts = n.replace("\\", "/").split("/")
        if "real" in parts:
            buckets["real"].append(n)
        elif "fake" in parts:
            i = parts.index("fake")
            method = parts[i + 1] if i + 1 < len(parts) - 1 else "unknown"
            buckets.setdefault(method, []).append(n)
    return buckets


def is_real(name: str) -> bool:
    return "real" in name.replace("\\", "/").split("/")


def extract(names_to_get: list, out_dir: Path) -> list:
    out_dir.mkdir(parents=True, exist_ok=True)
    remote = HttpRangeFile(DATASET_URL)
    saved = []
    with zipfile.ZipFile(remote) as zf:
        for name in names_to_get:
            info = zf.getinfo(name)
            if is_real(name):
                dst = out_dir / f"real__{Path(name).name}"
            else:
                # 어떤 조작 기법인지 파일명에 남긴다 (기법별 성능 차이를 보기 위해)
                parts = name.replace("\\", "/").split("/")
                method = parts[parts.index("fake") + 1] if "fake" in parts else "unknown"
                dst = out_dir / f"fake_{method}__{Path(name).name}"
            if dst.exists():
                print(f"  건너뜀(이미 있음): {dst.name}")
                saved.append(dst)
                continue
            print(f"  받는 중: {dst.name}  ({info.file_size / 1024**2:.1f} MB)")
            with zf.open(name) as src, open(dst, "wb") as f:
                while True:
                    chunk = src.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
            saved.append(dst)
    print(f"\n실제 다운로드량: {remote.bytes_fetched / 1024**2:.1f} MB "
          f"(전체 zip은 {remote.size / 1024**3:.2f} GB)")
    return saved


def main():
    parser = argparse.ArgumentParser(description="FF++ C23 검증 샘플 부분 추출")
    parser.add_argument("--list", action="store_true", help="목차만 보고 종료")
    parser.add_argument("--real", type=int, default=4, help="받을 진짜 영상 수")
    parser.add_argument("--fake", type=int, default=4, help="받을 가짜 영상 수 (기법별 분산)")
    parser.add_argument("--refresh-index", action="store_true", help="목차 캐시 무시하고 다시 읽기")
    args = parser.parse_args()

    names = load_index(force=args.refresh_index)
    buckets = classify(names)

    print("\n=== 데이터셋 구성 ===")
    for k, v in sorted(buckets.items(), key=lambda kv: -len(kv[1])):
        print(f"  {k:24} {len(v):>6}개")
        for sample in v[:2]:
            print(f"      예: {sample}")

    if args.list:
        return

    if not buckets.get("real"):
        print("\n[에러] real 샘플을 못 찾았습니다. 디렉터리 구조가 예상과 다릅니다.",
              file=sys.stderr)
        print("      --list 로 구조를 확인하고 classify()를 고치세요.", file=sys.stderr)
        sys.exit(1)

    targets = list(buckets["real"][:args.real])

    # 가짜는 한 기법에 몰리지 않게 기법별로 골고루 섞는다.
    methods = [k for k in buckets if k != "real"]
    if methods:
        per = max(1, args.fake // len(methods))
        for m in methods:
            targets.extend(buckets[m][:per])
        targets = targets[:args.real + args.fake]

    print(f"\n=== 추출 대상 {len(targets)}개 ===")
    for t in targets:
        print(f"  {t}")

    print()
    saved = extract(targets, OUT_DIR)
    print(f"\n저장 위치: {OUT_DIR}")
    print(f"파일 {len(saved)}개")
    print("\n다음 단계:")
    print("  .venv\\Scripts\\python.exe scripts\\validate_detector.py")


if __name__ == "__main__":
    main()
