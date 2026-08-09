"""
DualGuard 백엔드 + 대시보드 실행
담당: 이상원

실행:
    .venv\\Scripts\\python.exe scripts/run_server.py
    .venv\\Scripts\\python.exe scripts/run_server.py --port 9000 --reload

띄우면 http://127.0.0.1:8000 에서 업로드 화면이 열린다.
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))


from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

def main():
    parser = argparse.ArgumentParser(description="DualGuard 서버 실행")
    parser.add_argument("--host", default="127.0.0.1",
                        help="기본값은 로컬 전용. 외부 공개 시 0.0.0.0")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true", help="개발용 자동 재시작")
    args = parser.parse_args()

    import uvicorn

    print(f"  대시보드: http://{args.host}:{args.port}")
    print(f"  API 문서: http://{args.host}:{args.port}/docs")
    print(f"  엔진 상태: http://{args.host}:{args.port}/api/health\n")

    uvicorn.run(
        "api.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        app_dir=str(PROJECT_ROOT / "src"),
    )


if __name__ == "__main__":
    main()
