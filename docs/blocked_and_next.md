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

### 1-1-b. ~~한국어 '초록(안전)' 데모 샘플~~ — 해결됨 (2026-08-09)

**해결됐다.** 사람이 녹음하지 않아도 초록을 보여줄 수 있다.

문제는 저장소의 `normal_call.wav`가 Windows TTS라 음성이 실제로 합성이라는 것이었다.
AASIST가 정확히 잡아 미디어 위험도 100 → 결과가 '중간'이 됐다. 판정은 맞지만
초록 등급을 보여줄 수가 없었다.

공개 한국어 음성 코퍼스에서 **실제 사람 발화**를 가져오는 것으로 해결했다:

```powershell
.venv\Scripts\python.exe scripts\fetch_korean_speech_samples.py --verify
# → data\korean_calls\real_normal_call.wav (40초)
```

Zeroth-Korean(openslr.org/40, CC BY 4.0) test 스플릿에서 화자 한 명의 발화 5개를
이어 붙인다. 발화는 **오프라인 분류기로 90개를 전수 채점해 전 카테고리 0점인
것만** 골랐고, STT가 잘 받아쓰는 일상 어휘인지도 확인했다(자세한 기준은 스크립트
docstring). 실측:

| 파일 | 콘텐츠 | 미디어 | FRS | 등급 |
|---|---|---|---|---|
| `real_normal_call.wav` (진짜 목소리) | 0.0 | 26.7 | **13.3** | 낮음 🟢 |
| `normal_call.wav` (TTS, 내용 정상) | 0.0 | 100.0 | **50.0** | 중간 🟡 |
| `scam_call.wav` | 100.0 | 100.0 | **100.0** | 높음 🔴 |

세 등급이 전부 나오므로 시연 준비물은 더 없다.

**남은 것 하나** — "목소리는 진짜인데 화법이 사기"인 반대 방향 노랑은 여전히
사람이 읽어야 한다(`record_call_sample.py --script scam`). 공개 낭독 코퍼스에는
사기 대본이 없다. 없어도 위 표의 노랑으로 교차검증 설명은 된다.

**우선순위: 하.**

### 1-2. 크롬 확장 실제 로드 테스트

**자동 검증은 통과했다.** `node scripts/test_extension.js`가 확장 코드를 그대로
실행해(chrome.* API와 MediaRecorder를 가짜로 끼워 넣고) 실제 백엔드와 통신시킨다.
검증되는 것: 3계층 메시지 흐름, 세션 생성→청크→결과→종료, 밀림 방지,
백엔드 다운 시 오류 노출, 오버레이 렌더링 분기.

> 이 하네스가 실제로 버그를 두 개 잡았다.
> ① `{type:'risk', ...msg}`에서 스프레드가 `type`을 `'result'`로 덮어써서
>    **경고 오버레이가 영영 안 뜨는** 버그. 브라우저에서 눌러 봤어도 "왜 안 뜨지"만
>    반복했을 것이다.
> ② 청크가 끊긴 세션이 서버 락을 영원히 쥐는 문제(→ 유휴 90초 자동 정리 추가).

**그래도 브라우저에서 확인해야 하는 것**(하네스로는 불가능):
manifest 권한 승인, `tabCapture` 실제 권한 팝업, 탭 오디오 캡처와 소리 되돌리기,
실제 화상통화 페이지 위에서의 CSS 표시.

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

## 2. ~~팀이 정해야 할 것~~ — 전부 실측으로 확정 (2026-08-09)

감으로 고르지 않고 스크립트로 재서 정했다. **바꾸고 싶으면 해당 스크립트를 다시
돌려 반박 데이터를 낼 것.**

| # | 항목 | 확정값 | 근거 (요약) | 재현 |
|---|---|---|---|---|
| 1 | 통합 공식 (PDF vs DOCX) | **DOCX**(`multiplicative_bonus`) | 정확도는 동률인데 PDF 버전은 임계값에서 점수가 +15.20 계단으로 튄다. 기획서의 "확률적 표현" 원칙과 충돌 | `decide_scoring.py` |
| 2 | 딥페이크 임계값 (50 vs 7.5) | **결정 자체를 없앰** | 점수를 재척도해 50 하나만 쓴다. 정탐 63.3→73.3%(교차검증), 오탐 0% 유지 | `calibrate_deepfake.py` |
| 3 | 오디오·영상 결합 | **max** | 가중평균은 한쪽만 위조된 경우 4조합 중 2개를 '낮음'으로 놓친다 | `decide_media_combine.py` |
| 4 | 신호등 경계 | **높음 55 / 중간 30** | 70/40은 사기를 낮음으로 놓치는 게 84건, 55/30은 21건(하한). 헛경보는 둘 다 0 | `decide_scoring.py` |
| 5 | 콘텐츠/미디어 가중치 | **0.65 / 0.35** | 위와 같은 스윕 | `decide_scoring.py` |
| 6 | 얼굴 검출기 | **YuNet**(Haar 폴백) | 30° 회전에서 검출률 34→100%, 판정 불가 영상 13→0개 | `benchmark_face_detector.py` |
| 7 | STT 모델 크기 | **파일 `small` / 스트리밍 `base`** | 5dB 소음 CER 0.34 vs 0.56. 스트리밍은 3.1배 빠른 쪽이 필요 | `compare_stt_models.py` |
| 8 | 코드 주석의 담당자 표기 | 이상원 | 기획서 역할표 기준 | — |

> **4·5번 수치의 한계**: 콘텐츠 21건 × 음성 50건을 교차한 합성 격자(1,050쌍)로 골랐다.
> 실제 통화가 아니고 두 축이 독립이라고 가정했다. **설정 비교용으로만 유효**하며
> 격자 정확도(75.3%)를 제품 성능으로 인용하면 안 된다.
> 정답 라벨을 "콘텐츠가 사기면 높음"으로 정의했으므로 콘텐츠 가중이 높게 나온 건
> 부분적으로 설계에 내재한다 — `scripts/decide_scoring.py` docstring에 명시해뒀다.

---

## 3. 다음 개발 작업

### 3-1. ~~실시간 스트리밍~~ — 구현 완료 (2026-08-09)

세션 API를 붙였다. `src/orchestration/streaming.py` + `POST /api/sessions`.
예전에는 청크마다 `POST /api/analyze`를 불러 STT 모델을 매번 다시 로드했다
(청크 5초당 30초 넘게 걸림). 지금은 모델을 세션 동안 상주시킨다.

실측: **오디오 78초를 26초에 처리(약 3배속).** 청크당 1.1~1.6초.
재현: `.venv\Scripts\python.exe scripts\test_streaming.py` (서버 필요)

설계상 제약 (모두 의도된 것):
- **동시에 세션 하나만.** 모델 상주 때문. 세션 중 파일 분석은 409로 막는다.
- 청크 90초 끊기면 자동 정리(`IDLE_TIMEOUT_SEC`). 확장이 죽어도 락이 안 남는다.
- **영상은 안 본다.** 프레임마다 얼굴 검출 + Xception은 실시간 예산에 안 맞는다.
  영상 딥페이크는 통화 종료 후 파일 분석 경로에서 본다.
- STT는 `base`(파일 분석은 `small`). 실시간을 따라가려면 속도가 필요하다.

남은 것: 웹소켓 양방향(지금은 청크 POST의 응답으로 결과를 받는다 — 왕복 한 번이라
실용상 문제는 없다), 그리고 여러 세션 동시 처리(서버 메모리가 넉넉해지면).

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

- ~~**얼굴 미검출 대응**~~ — **해결.** YuNet으로 교체했다(Haar는 폴백으로 남김).
  30° 회전 스트레스에서 검출률 34% → 100%, 판정 불가 영상 13개 → 0개.
  재현: `scripts/benchmark_face_detector.py`
- **FaceShifter·NeuralTextures 취약점** — 2019년 체크포인트의 학습 범위 밖.
  재척도로 각 1/5 → 2/5까지는 올라갔지만 모델을 바꾸지 않으면 여기까지다.
  발표에서 한계로 먼저 밝히는 것을 권한다 ([validation_report.md](validation_report.md) 4-3).
- ~~**전화망 음성**~~ — **측정 완료.** 리샘플링을 폴리페이즈로 바꿨고(선형 보간은
  앤티앨리어싱이 없어 에일리어싱이 생긴다), 8kHz 전화망 조건을 실제로 재봤다.
  **정확도는 98%로 그대로지만 진짜 음성 점수가 2.2 → 30.9로 올라 오탐 여유가
  크게 준다.** 재현: `validate_audio_spoof.py --simulate-telephone`
- ~~**점수 보정**~~ — **해결.** 로짓 공간 1차 변환으로 재척도했다. 판정 경계가 50에
  오도록 맞춰 임계값 결정 문제 자체를 없앴다. 다만 이건 **확률이 아니다** —
  50건으로 진짜 확률 보정은 못 한다. `src/media_detection/calibration.py` 참고.
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
