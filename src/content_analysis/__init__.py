"""콘텐츠 분석 패키지 (STT · 8대 사회공학 기법 분류 · RAG 사례 검색)."""

import warnings

# google-auth / google-genai가 import 시점에 Python 3.9 EOL 경고를 찍는다.
# 기능과 무관한 안내인데 데모 화면에 노란 경고가 뜨면 "뭔가 잘못됐나" 싶어 보인다.
# 이 패키지를 쓰는 모든 진입점에 동일하게 적용되도록 여기서 한 번만 끈다.
warnings.filterwarnings("ignore", category=FutureWarning, module=r"google\..*")
