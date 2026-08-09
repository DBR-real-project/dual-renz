# 막힌 것 / 다음에 할 것

갱신: 2026-08-09 | 이 문서만 보면 현재 상태와 다음 할 일을 알 수 있다.

전체 파이프라인(STT → 화법분석 → 음성/영상 위조 판별 → 통합 스코어 → 대시보드)이
**끝까지 동작한다.** 아래는 남은 것들이다.

---

## 1. 사람이 직접 해야 하는 것 (내가 못 함)

### 1-1. ⭐ LLM API 키 — 화법 분석 (Claude 또는 Gemini)

**지금 가장 임팩트가 큰 항목.** 코드는 완성돼 있고 키만 넣으면 즉시 켜진다.
**둘 중 아무 키나 하나 있으면 된다** — 기획서가 "Claude API 또는 Gemini API"를
허용하고, 두 백엔드가 모두 구현돼 있다.

**우선순위가 '상'에서 '중'으로 내려갔다.** 키워드의 한계(부정문·어휘 변이)를
오프라인 분류기로 해결해서, **키가 없어도 정확도 90.5%가 나온다**
(`docs/validation_report.md` 5-0). 키가 있으면 더 나아질 여지가 있지만
**더 이상 제품 성립의 전제 조건이 아니다.**

| 백엔드 | 정탐률 | 오탐률 | 정확도 |
|---|---|---|---|
| 키워드 규칙 (개선 전) | 63.6% (7/11) | 30.0% (3/10) | 66.7% |
| **오프라인 (현재 기본값)** | **90.9%** (10/11) | **10.0%** (1/10) | **90.5%** |
| LLM | 미측정 — 키 필요 | | |

LLM을 붙이면 좋은 지점은 남아 있다. 남은 오차 2건이 전부 **맥락 판단**이 필요한
경우다 — 데이트 초반 안부 대화(검증셋도 "구분 불가"로 표기), 연인 사이의 부드러운
어투로 통관비를 요구하는 로맨스 스캠. 이런 건 의미 유사도로는 한계가 있다.

**할 일 (둘 중 하나):**
```powershell
# A. Claude
$env:ANTHROPIC_API_KEY = "sk-ant-..."
# B. Gemini
$env:GEMINI_API_KEY = "AIza..."
# 둘 다 있는데 특정 백엔드로 고정하고 싶으면:
$env:DUALGUARD_LLM_PROVIDER = "gemini"   # auto(기본) | anthropic | gemini

# 성능 실측 (LLM이 규칙보다 얼마나 나은지가 발표 자료의 핵심)
.venv\Scripts\python.exe scripts\validate_content_risk.py --backend both
.venv\Scripts\python.exe scripts\grade_content_rubric.py --backend both   # 카테고리별 정밀 채점

# 파이프라인 확인
.venv\Scripts\python.exe scripts\analyze_call.py --input data\korean_calls\scam_call.wav
# → "engines.content"가 "Claude (claude-opus-5)" 또는 "Gemini (gemini-flash-latest)"로 바뀌면 성공
```

키가 없어도 파이프라인은 죽지 않고 키워드로 폴백한다. 리포트의 `warnings`와
대시보드 상단 칩에 어느 쪽이 쓰였는지 항상 표시된다.

> **⚠ Gemini 키 형식 주의.** `AQ.Ab8RN6...`로 시작하는 문자열은 API 키가 아니라
> 수명이 짧은 OAuth 액세스 토큰이다. 넣으면 `401 ACCESS_TOKEN_TYPE_UNSUPPORTED`가 난다.
> [aistudio.google.com](https://aistudio.google.com/apikey)에서 `AIza...`로 시작하는
> API 키를 발급받을 것. 무료 티어는 분당 요청 제한이 빡빡해서 통화 하나에
> 세그먼트가 많으면 429가 난다 — `$env:GEMINI_MIN_INTERVAL_SEC = "12"` 로 간격을 강제할 수 있다.

> **⚠ 키를 소스에 넣지 말 것.** 초기 프로토타입(`dual-lens/batch_test.py`,
> `stt_test.py`)에 키가 하드코딩돼 있었다. 그 키들은 폐기하고 환경변수로만 쓴다.

**우선순위: 중.** (오프라인 분류기로 대체 가능해져 '상'에서 내려왔다)

### 1-1-b. ⭐ 한국어 '초록(안전)' 데모 샘플 녹음 — 30초면 된다

**시연에서 가장 아쉬운 지점.** 저장소의 `normal_call.wav`는 Windows TTS라 음성이
실제로 합성이다. AASIST가 정확히 잡아 미디어 위험도 100 → 결과가 '중간'으로 나온다.
**판정은 맞지만 초록 등급을 보여줄 수가 없다.**

사람이 30초만 녹음하면 해결된다:

```powershell
.venv\Scripts\python.exe scripts\record_call_sample.py --list      # 마이크 이름 확인
.venv\Scripts\python.exe scripts\record_call_sample.py --device "마이크(Realtek(R) Audio)"
```

대본이 화면에 뜨니 그대로 읽으면 된다. `--script scam` 으로 하면 "목소리는 진짜인데
화법이 사기"인 교차검증 시연용 샘플도 만들 수 있다.

실측 근거: 진짜 사람 목소리 미디어 위험도 0.0~2.2 → 낮음(초록), 합성 음성 100.

**우선순위: 상.** 시연 품질에 직결된다.

### 1-2. 크롬 확장 실제 로드 테스트

코드는 다 썼고 문법 검사(`node --check`)와 manifest 유효성은 통과했지만,
**브라우저에 올려서 실제로 캡처가 되는지는 확인하지 못했다.** 나는 브라우저를
조작할 수 없다.

**할 일:**
1. 백엔드 실행: `.venv\Scripts\python.exe scripts\run_server.py`
2. 크롬 → `chrome://extensions` → 개발자 모드 켜기 → **압축해제된 확장 프로그램 로드**
   → `extension/` 폴더 선택
3. 구글 미트 등 화상통화 페이지를 열고 확장 아이콘 → **분석 시작**
4. 확인할 것:
   - 탭 캡처 권한 팝업이 뜨는가
   - 캡처 시작 후에도 **통화 소리가 계속 들리는가** (오디오 되돌리기 처리를 넣어뒀다)
   - 우상단에 오버레이가 뜨는가
   - 서비스 워커 콘솔(`chrome://extensions` → 서비스 워커)에 에러가 없는가

**우선순위: 중.** 본선 범위는 업로드 기반 MVP이고, 확장은 Phase 2다.

### 1-3. Kaggle 자격증명 — DFDC 크로스도메인 검증

성능 실측이 전부 **모델이 학습한 도메인 안에서** 이뤄졌다. 실제 통화 영상에서도
유지되는지는 아직 모른다.

```
1. https://www.kaggle.com/settings/account → API → Create New Token
2. kaggle.json 을 C:\Users\migle\.kaggle\kaggle.json 에 복사
3. https://www.kaggle.com/c/deepfake-detection-challenge/rules 에서 규칙 동의
4. .venv\Scripts\python.exe scripts\download_kaggle_data.py --dataset dfdc
```

**우선순위: 중.** 없어도 발표는 가능하지만 "실제 통화에서도 되냐"는 질문에
답할 근거가 없다.

### 1-4. Codex CLI 로그인

설치·MCP 등록·권한 설정은 끝났고 로그인만 남았다. 프로젝트와는 무관하다.

```
! "C:\Users\migle\AppData\Roaming\npm\codex.cmd" login
```

**우선순위: 하.**

---

## 2. 팀이 정해야 할 것

상세 근거는 [spec_reconciliation.md](spec_reconciliation.md).

| # | 항목 | 현재값 | 왜 중요한가 |
|---|---|---|---|
| 1 | **통합 공식** (PDF vs DOCX) | DOCX 버전 | **기획서 두 벌에 다른 공식이 적혀 있다.** 심사 자료와 코드가 어긋나면 안 된다 |
| 2 | 딥페이크 임계값 (50 vs 7.5) | 50 | 50은 오탐 0%/정탐 66.7%, 7.5는 오탐 10%/정탐 80% |
| 3 | 오디오·영상 결합 (max vs 가중평균) | max | 현재 "영상만 위조"와 "둘 다 위조"가 똑같이 100점 |
| 4 | 신호등 경계 | 높음 70 / 중간 40 | `pipeline.LEVEL_THRESHOLDS`. 사용자가 보는 등급을 좌우한다 |
| 5 | 코드 주석의 담당자 표기 | 이상원 | 기획서 역할표 기준 |

**1번이 가장 급하다.** 발표 자료 만들기 전에 확정해야 한다.

---

## 3. 다음 개발 작업

### 3-1. 실시간 스트리밍 (Phase 2의 핵심)

현재 백엔드는 **파일 단건 분석**만 한다. 크롬 확장은 5초 청크를 만들어
`POST /api/analyze`를 반복 호출하는 임시 형태다. 청크마다 STT 모델을 다시
로드하므로 실시간이라 부르기 어렵다.

필요한 것:
- 세션 API (`POST /api/sessions` → 청크 누적 → 부분 결과 push)
- 모델을 상주시키되 메모리 한도 안에서 (3-3 참고)
- 웹소켓 양방향 (청크 업로드 + 결과 수신)

### 3-2. RAG 사례 데이터 확충

지금 18건뿐이고 대부분 팀이 정리한 수법 패턴이다. 기획서 데이터 확보 방안대로
언론 보도·판례·국정원 111센터 통계를 크롤링해 늘려야 한다.
**출처 표기는 필수** — 근거 없는 사례가 대시보드에 "실제 사례"로 뜨면 신뢰를 해친다.

추가 후 재색인:
```powershell
.venv\Scripts\python.exe -c "import sys; sys.path.insert(0,'src'); from content_analysis.rag import get_shared_retriever; print(get_shared_retriever().rebuild())"
```

### 3-2-1. 콘텐츠 채점 검증셋 확충 — 경계 케이스

`data_seed/content_test_scenarios.json`은 현재 21건(사기 11 / 정상 10)이고,
그중 7건이 경계 케이스다 — 정상 은행원이 급하게 말하는 경우, 가족 사이의 정당한
비밀 이야기, 실제 이체를 요청하는 정상 업무 통화 등. 다만 **21건은 통계적으로
작다.** 오탐 1건이 오탐률 10%p를 움직이는 규모라, 수치를 발표에 쓸 때는
표본 크기를 같이 밝히는 편이 안전하다.

> **⚠ 검증셋을 늘릴 때 `data_seed/category_prototypes.json`(의미 분류기 기준 문장)에
> 같은 문장을 넣지 말 것.** 넣는 순간 그 항목은 반드시 맞히고, 정확도 수치가
> 실력이 아니라 정답 유출이 된다.

시나리오를 추가할 때 스키마:
```json
{
  "id": "boundary_01",
  "label": "fraud | normal",
  "title": "한 줄 설명",
  "channel": "call | video_call",
  "techniques": ["urgency", "authority", ...],   // 기존 이진 참고 라벨 (선택)
  "expected_scores": {
    "urgency": {"band": "none|weak|clear|definitive", "evidence": "인용문 (band가 none이 아니면 필수 권장)"},
    "authority": {"band": "..."},
    "money_transfer": {"band": "..."},
    "credentials": {"band": "..."},
    "secrecy": {"band": "..."},
    "emotional": {"band": "..."},
    "trust_building": {"band": "..."},
    "isolation": {"band": "..."}
  },
  "turns": [{"speaker": "...", "text": "..."}]
}
```
8개 카테고리 전부를 채워야 한다(없으면 `"band": "none"`). 밴드 기준은
`llm_classifier.SYSTEM_PROMPT`와 동일: none(0) / weak(1~30) / clear(31~70) /
definitive(71~100). 추가 후:
```powershell
.venv\Scripts\python.exe scripts\validate_content_risk.py --backend both
.venv\Scripts\python.exe scripts\grade_content_rubric.py --backend both
```

### 3-3. 메모리 문제 (구조적 제약)

전체 파이프라인이 Whisper + AASIST + Xception + 임베딩 모델을 순차로 쓴다.
실측 환경(RAM 16GB, 브라우저 상시 실행 → 여유 5.8GB)에서:

| 증상 | 원인 |
|---|---|
| `mkl_malloc: failed to allocate memory` | 메모리 부족 (잡히는 예외) |
| 프로세스 즉시 종료 (0xC0000005) | 같은 원인인데 네이티브 크래시로 나타남. **try/except로 못 잡는다** |

현재 대응:
- 단계마다 모델 해제 (`pipeline.analyze(free_models=True)`, 기본값)
- AASIST 배치 4로 제한 (6 이상이면 크래시)

한계: 매 요청마다 모델을 다시 로드하므로 느리다. 동시 요청도 못 받는다
(`api/main.py`의 워커 1개). 서버 메모리가 넉넉해지면 `free_models=False`로
되돌리고 워커를 늘릴 수 있다.

### 3-4. 로컬 LLM(Ollama)으로 프롬프트 사전 검증

`llm_classifier.py`에 `OllamaClassifier`를 세 번째 백엔드로 추가했다. 실제
서비스용이 아니라 **클라우드 API를 부르기 전에 프롬프트 구조(시스템 프롬프트·
JSON 스키마)가 말이 되는지 로컬에서 공짜로 확인**하는 용도다. Gemini 무료
티어 쿼터가 막혔을 때 특히 쓸모 있다. 그래서 `auto` 폴백 체인에는 안 들어가고
`DUALGUARD_LLM_PROVIDER=ollama`로 명시했을 때만 쓰인다.

```powershell
# 1. Ollama 설치 후 모델 하나 받기 (한 번만)
ollama pull llama3.1

# 2. 이 프로젝트 프롬프트로 채점 (validate_content_risk.py가 그대로 씀)
$env:DUALGUARD_LLM_PROVIDER = "ollama"
.venv\Scripts\python.exe scripts\validate_content_risk.py --backend llm
.venv\Scripts\python.exe scripts\grade_content_rubric.py --backend llm
```

다른 모델을 쓰려면 `$env:OLLAMA_MODEL = "qwen2.5:7b"` 식으로. Ollama 0.5 미만
버전은 `format`에 JSON 스키마를 못 받아 파싱이 실패할 수 있다 — `ollama --version`
확인할 것.

### 3-5. 미해결 기술 과제

- **얼굴 미검출 대응** — FF++는 얼굴 크롭 전용이라 얼굴이 안 잡히면 판정 불가.
  실제 통화는 각도가 틀어지는 구간이 많다. Haar cascade보다 나은 검출기(YuNet 등)
  교체를 검토할 것.
- **FaceShifter·NeuralTextures 취약점** — 2019년 체크포인트의 학습 범위 밖.
  발표에서 한계로 먼저 밝히는 것을 권한다 ([validation_report.md](validation_report.md) 4-3).
- **전화망 음성** — AASIST는 16kHz 학습인데 실제 통화는 8kHz다. 현재 리샘플링이
  선형 보간이라 품질이 좋지 않다 (`audio_spoof_detector._resample_linear`).
- **점수 보정** — FF++ 점수가 0 아니면 100으로 극단적이라 "위험도 66%" 같은
  확률 표현에 그대로 쓰기 부적절하다.
- **TTS ≠ 사기** — AASIST는 안내방송·오디오북도 합성으로 잡는다. 지금은 대화 내용이
  정상이면 경고에 그 사실을 덧붙이는 정도로 대응하고 있다.

---

## 4. 지금 바로 돌려볼 수 있는 것

```powershell
cd C:\Users\migle\DualGuard-MediaAnalysis

# 웹 대시보드 (가장 보기 좋음)
.venv\Scripts\python.exe scripts\run_server.py
#   → http://127.0.0.1:8000 에서 data\korean_calls\scam_call.wav 업로드

# CLI 전체 분석
.venv\Scripts\python.exe scripts\analyze_call.py --input data\korean_calls\scam_call.wav

# 교차검증 4조합 (발표용 핵심 자료)
.venv\Scripts\python.exe scripts\demo_cross_validation.py

# 성능 수치 재현
.venv\Scripts\python.exe scripts\validate_detector.py --backend ff
.venv\Scripts\python.exe scripts\validate_audio_spoof.py
.venv\Scripts\python.exe scripts\validate_content_risk.py   # 데이터 다운로드 불필요
.venv\Scripts\python.exe scripts\grade_content_rubric.py    # 카테고리별 정밀 채점 (기대 점수대 vs 실제)
```

데이터가 없다면 (git에 포함 안 됨):

```powershell
.venv\Scripts\python.exe scripts\make_korean_call_samples.py
.venv\Scripts\python.exe scripts\fetch_ff_samples.py --real 20 --fake 30
.venv\Scripts\python.exe scripts\fetch_asvspoof_samples.py --bonafide 20 --spoof 30
.venv\Scripts\python.exe scripts\make_demo_clips.py
```
