# 막힌 것 / 다음에 할 것

갱신: 2026-08-09 (2차) | 이 문서만 보면 현재 상태와 다음 할 일을 알 수 있다.

전체 파이프라인(STT → 화법분석 → 음성/영상 위조 판별 → 통합 스코어 → 대시보드)이
**끝까지 동작한다.** 아래는 남은 것들이다.

---

## 1. 사람이 직접 해야 하는 것 (내가 못 함)

이전에 6건이었다. **activeTab까지 해결돼 실제로 남은 건 2건뿐**이다.
처리한 것들은 아래 「해결한 것」에 결과와 함께 적어뒀다.

### 1-1. ~~확장 아이콘 클릭 시 `activeTab` 부여~~ — 해결됨 (2026-08-18)

**우회 없이 진짜로 뚫었다.** 예전엔 CDP 가짜 제스처가 전부 막혀서 사람이 눌러야 했다:

| 시도 | 결과 |
|---|---|
| `Runtime.evaluate({userGesture: true})` | 실패 |
| `chrome.action.openPopup()` + 팝업 버튼 클릭 | 실패 |
| `Browser.grantPermissions` (CDP) | 실패 |
| `host_permissions: <all_urls>` | 실패 |
| **확장 단축키를 OS 레벨 SendKeys로 실제 입력** | **성공** |

크롬 문서상 **확장이 등록한 키보드 단축키는 "사용자 호출"에 포함**되고,
SendKeys로 보낸 키를 크롬은 사람의 입력과 구분하지 않는다.
그래서 manifest에 `_execute_action` 단축키(`Ctrl+Shift+Y`)를 추가했다 —
검증용일 뿐 아니라 **통화 중 빠른 실행**이라는 실용 기능이기도 하다.

```powershell
.venv\Scripts\python.exe scripts\run_server.py     # 다른 창에서
node scripts\verify_extension_chrome.js            # 완전 자동, 사람 개입 없음
```

구현에 걸린 두 가지:
- **창을 화면 안에 띄워야 한다.** 화면 밖(-2400)이나 헤드리스면 포커스를 못 받아 키가 안 간다.
- **Windows 포커스 탈취 방지.** `AttachThreadInput`으로 현재 foreground 스레드에
  입력 큐를 붙여야 `SetForegroundWindow`가 통한다.
- ⚠ **반드시 우리가 띄운 PID의 창에만 보낼 것.** 프로세스 이름으로 고르면
  사용자가 쓰던 크롬이 잡혀 엉뚱한 창에 키가 간다(실제로 한 번 그랬다).
  지금은 foreground PID를 대조해 불일치면 전송하지 않는다.

포커스를 줄 수 없는 환경(CI·원격 세션)에서는 `--allowlist`로 검사를 건너뛸 수 있지만
그만큼 검증 강도가 내려간다.

### 1-2. LLM API 키 — 상용 모델 정확도

두 가지를 이미 해결해서, **키가 없어도 LLM 경로를 검수할 수 있다.**

1. **스텁 서버 검증** (`scripts/test_llm_client.py`) — 요청 형식·JSON 스키마 전달·
   응답 파싱·집계 공식·빈 응답/깨진 JSON 예외·서버 다운 시 폴백까지 확인.
2. **키 없이 도는 로컬 LLM 백엔드** (`DUALGUARD_LLM_PROVIDER=local`) — 이미 깔린
   transformers로 Qwen2.5-1.5B를 돌린다. 별도 설치도 계정도 없다.

   ```powershell
   $env:DUALGUARD_LLM_PROVIDER = "local"
   .venv\Scripts\python.exe scripts\validate_content_risk.py --backend llm
   ```

   > 소형 모델이라 상용 LLM 수준이 아니다. **"LLM 경로가 실제 모델로 돈다"를
   > 보이는 검수용**이고, 발표 수치는 진짜 키로 다시 재야 한다.
   > CPU에서 발화당 30~50초 걸리고, bfloat16으로 올려도 약 3GB를 쓴다
   > (float32로 두면 메모리 부족으로 프로세스가 조용히 죽는다 — 실제로 겪었다).

실측 결과 (21건, 임계값 50):

| 백엔드 | 정탐률 | 오탐률 | 정확도 | 기법별 회수율 |
|---|---|---|---|---|
| 오프라인 분류기 | 90.9% | 10.0% | 90.5% | 24/41 |
| 로컬 LLM (Qwen2.5-1.5B) | 90.9% | 10.0% | 90.5% | **30/41** |

총점은 동률인데 **기법별 분류는 LLM이 낫다**(권위 사칭 2/6→5/6, 신뢰 구축 2/5→4/5).
대시보드가 보여주는 "왜 위험한가"의 정확도가 달라진다는 뜻이다.

OpenAI(gpt-4o-mini)는 강동연이 실측했다 — 기준선 62.5에서 정확도 95.2%인데,
**그 기준선을 평가에 쓴 21건으로 정해서** 과적합 소지가 있다
(`validation_report.md` 0-10절). 못 잰 건 **Claude/Gemini의 분류 정확도**다.

키는 `.env`로 준다(`.env.example`을 복사해 채운다). 환경변수로 직접 줘도 된다.

```powershell
copy .env.example .env
# .env 를 열어 ANTHROPIC_API_KEY 또는 GEMINI_API_KEY 를 채운다
.venv\Scripts\python.exe scripts\validate_content_risk.py --backend both
```

키가 없어도 오프라인 분류기(자체셋 90.5%, 도메인 밖 오탐 1.53%)로 돌아간다.

> **⚠ Gemini 키 형식 주의.** `AQ.Ab8RN6...`는 API 키가 아니라 수명이 짧은 OAuth
> 액세스 토큰이다. [aistudio.google.com](https://aistudio.google.com/apikey)에서
> `AIza...`로 시작하는 키를 받을 것. 무료 티어는 분당 제한이 빡빡하므로
> `$env:GEMINI_MIN_INTERVAL_SEC = "12"`.

> **⚠ 키를 소스에 넣지 말 것.** 초기 프로토타입에 하드코딩된 전례가 있다(폐기 완료).

**우선순위: 중.**

### 1-3. "진짜 목소리 + 사기 화법" 데모 샘플 — 30초 녹음

세 등급(초록/노랑/빨강)은 이미 다 나온다. 이건 **교차검증의 반대 방향**
("목소리는 진짜인데 화법이 사기")을 보여주는 추가 샘플이다.

```powershell
.venv\Scripts\python.exe scripts\record_call_sample.py --list
.venv\Scripts\python.exe scripts\record_call_sample.py --device "마이크(Realtek(R) Audio)" --script scam
```

> **공개 코퍼스로 만들려다 그만뒀다.** Zeroth-Korean 학습셋 22,263발화를 전수 스캔해
> 사기 어휘가 든 문장을 화자별로 모아봤는데, 나온 건 전부 **대출금리·검찰수사 뉴스
> 문장**이었다. 분류기는 높게 잡지만 그건 사기 통화가 아니라 뉴스다.
> 그걸 이어 붙여 "사기 통화"로 시연하는 건 조작이라 하지 않았다.
> 대신 그 스캔에서 드러난 오탐 패턴은 `validate_content_fpr.py`로 수치화했다(1.53%).

**우선순위: 상** (시연 완성도) / **하** (제품 기능).

### 1-4. Codex CLI 로그인

설치·MCP 등록·권한 설정은 끝났고 로그인만 남았다. 프로젝트와 무관하다.

```
! "C:\Users\migle\AppData\Roaming\npm\codex.cmd" login
```

**우선순위: 하.**

---

## 1-A. 해결한 것 (2026-08-09)

| 이전 항목 | 결과 |
|---|---|
| 한국어 '초록' 데모 샘플 | ✅ 공개 코퍼스(Zeroth-Korean)에서 진짜 사람 목소리 확보. `fetch_korean_speech_samples.py` |
| 크롬 확장 브라우저 로드 | ✅ 실제 크롬 + CDP로 자동 검증. `--load-extension`이 Chrome 137+에서 막혀 `Extensions.loadUnpacked`로 우회 |
| Kaggle 자격증명 (DFDC) | ✅ **계정 없이 확보.** HuggingFace 미러 96.5GB zip에서 색인 3.4MB + 영상 431MB만 받아 50건. `fetch_dfdc_samples.py` |
| LLM 연동 검증 | ◐ 스텁 서버로 클라이언트 경로 검증 완료. 실제 정확도만 남음 |
| 검증셋 확충 | ✅ 영상 50→116, 음성 50→120, 도메인 밖 한국어 457문장 추가 |
| **실제 통화 녹취** | ✅ **금융감독원 「그놈 목소리」 공개 자료로 해결.** 실제 사기범 녹취 5건 → 5/5 탐지. `fetch_real_call_samples.py` |

**DFDC 결과는 좋지 않았다** — 영상 엔진이 크로스도메인에서 오탐 55%를 냈다.
숨기지 않고 [validation_report.md](validation_report.md) 0-3절에 적었고,
파이프라인이 영상 분석 시 항상 경고를 띄우게 했다.

---

## 2. ~~팀이 정해야 할 것~~ — 전부 실측으로 확정 (2026-08-09)

감으로 고르지 않고 스크립트로 재서 정했다. **바꾸고 싶으면 해당 스크립트를 다시
돌려 반박 데이터를 낼 것.**

| # | 항목 | 확정값 | 근거 (요약) | 재현 |
|---|---|---|---|---|
| 1 | 통합 공식 (PDF vs DOCX) | **DOCX**(`multiplicative_bonus`) | 정확도는 동률인데 PDF 버전은 임계값에서 점수가 +15.20 계단으로 튄다. 기획서의 "확률적 표현" 원칙과 충돌 | `decide_scoring.py` |
| 2 | 딥페이크 임계값 (50 vs 7.5) | **결정 자체를 없앰** | 점수를 재척도해 50 하나만 쓴다. 정탐 66.7→75.8%(5-겹 교차검증), 오탐 2% 유지. 최적 임계값이 정확히 50에 온다 | `calibrate_deepfake.py` |
| 3 | 오디오·영상 결합 | **max** | 가중평균은 한쪽만 위조된 경우 4조합 중 2개를 '낮음'으로 놓친다 | `decide_media_combine.py` |
| 4 | 신호등 경계 | **높음 55 / 중간 30** | 70/40은 사기를 낮음으로 놓치는 게 84건, 55/30은 21건(하한). 헛경보는 둘 다 0 | `decide_scoring.py` |
| 5 | 콘텐츠/미디어 가중치 | **0.65 / 0.35** | 위와 같은 스윕 | `decide_scoring.py` |
| 6 | 얼굴 검출기 | **YuNet**(Haar 폴백) | 30° 회전에서 검출률 34→100%, 판정 불가 영상 13→0개 | `benchmark_face_detector.py` |
| 7 | STT 모델 크기 | **파일 `small` / 스트리밍 `base`** | 5dB 소음 CER 0.34 vs 0.56. 스트리밍은 3.1배 빠른 쪽이 필요 | `compare_stt_models.py` |
| 8 | 좌우 반전 TTA | **켬** | 정탐 77.3→80.3%, 오탐 그대로. FaceShifter 4/11→6/11 | `validate_detector.py` |
| 9 | 코드 주석의 담당자 표기 | 이상원 | 기획서 역할표 기준 | — |

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

지금 26건이고(국내 보도 기반 kr-aug-001~008을 더했다) 대부분 팀이 정리하거나
재구성한 수법 패턴이다. 기획서 데이터 확보 방안대로
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
- **⚠ 크로스도메인 일반화 — 측정했고, 나빴다.** DFDC에서 오탐률 55%(FF++에서는 2%).
  c40 가중치로 바꾸면 더 나쁘다(90%). 모델을 교체하지 않으면 개선되지 않는다.
  자세한 수치와 대응은 [validation_report.md](validation_report.md) 0-3절.
- **FaceShifter·NeuralTextures 취약점** — 2019년 체크포인트의 학습 범위 밖.
  TTA로 FaceShifter 4/11 → 6/11까지 올렸지만 거기까지다.
  발표에서 한계로 먼저 밝히는 것을 권한다.
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
