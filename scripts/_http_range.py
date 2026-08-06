"""
HTTP Range 요청으로 원격 파일을 로컬 파일처럼 읽는 어댑터
담당: 이상원

용도: 공개 데이터셋이 수십 GB짜리 통짜 파일(zip, parquet)로 올라와 있을 때,
전체를 받지 않고 필요한 부분만 뽑아내기 위한 것.

zipfile과 pyarrow.parquet 둘 다 "seek + read가 되는 파일 객체"만 요구하기 때문에,
이 어댑터 하나로 두 포맷 모두 부분 추출이 가능하다. 실제로:
  - FF++ C23 미러: 16.66GB zip 중 91.9MB만 받아 영상 50개 확보
  - ASVspoof2019 LA: 463MB parquet 중 필요한 row group만

사용:
    from _http_range import HttpRangeFile
    remote = HttpRangeFile(url)
    with zipfile.ZipFile(remote) as zf: ...
"""

import io
import urllib.request


class HttpRangeFile(io.RawIOBase):
    """
    zipfile/pyarrow는 목차를 읽을 때 작은 read를 여러 번 날린다. 매번 HTTP 요청을
    보내면 느리므로 chunk_size 단위로 미리 받아 버퍼에 들고 있는다.
    """

    def __init__(self, url: str, chunk_size: int = 1 << 20, timeout: int = 180):
        self.url = url
        self.chunk_size = chunk_size
        self.timeout = timeout
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
            raise RuntimeError(
                f"서버가 Range를 지원하지 않아 부분 추출이 불가능합니다: {cr}"
            )
        return int(cr.rsplit("/", 1)[1])

    def _fetch(self, start: int, end: int) -> bytes:
        end = min(end, self.size - 1)
        req = urllib.request.Request(self.url, headers={"Range": f"bytes={start}-{end}"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            data = r.read()
        self.requests += 1
        self.bytes_fetched += len(data)
        return data

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

        # 큰 요청은 버퍼를 거치지 않고 바로 받는다
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

    def readinto(self, b) -> int:
        data = self.read(len(b))
        b[:len(data)] = data
        return len(data)

    def summary(self) -> str:
        return (f"받은 양 {self.bytes_fetched / 1024**2:.1f} MB "
                f"(전체 {self.size / 1024**3:.2f} GB, 요청 {self.requests}회)")
