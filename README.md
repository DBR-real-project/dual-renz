# DualGuard — 통화·화상통화 사기 교차분석

듀얼렌즈(DualLens) 팀 / 듀얼가드(DualGuard).
통화 파일을 넣으면 **대화 화법**과 **음성·영상의 진위**를 각각 분석하고,
교차 검증해 하나의 **Fraud Risk Score(0~100)** 로 통합한다.

```
통화 파일 ──┬─ STT ──→ 8대 사회공학 기법 분류 ─┬→ 콘텐츠 위험도 ─┐
            │                (+RAG 사례 대조)   │                 ├→ Fraud Risk Score
            ├─ 음성 ──→ AASIST 합성 판별 ───────┤                 │   (교차 가산)
            └─ 영상 ──→ FF++ 얼굴 위조 판별 ────┴→ 미디어 위험도 ─┘
                                                                     ↓
                                          신호등 + 구간별 타임라인 + 3단계 액션 플랜
```

---

## 지금 되는 것

| 기능 | 상태 | 비고 |
|---|---|---|
| **웹 대시보드** (업로드 → 진행률 → 결과) | ✅ | `scripts/run_server.py` |
| **CLI 분석** | ✅ | `scripts/analyze_call.py` |
| STT (한국어) | ✅ | faster-whisper. 8문장 통화를 정확히 8구간으로 분리 |
| 8대 사회공학 기법 분류 | ✅ | LLM(**Claude / Gemini / OpenAI / Ollama / 로컬**) + **오프라인 분류기** 폴백 |
| RAG 사기사례 대조 | ✅ | 사기/정상 13문장 판별 **13/13** |
| **콘텐츠 도메인 밖 오탐 검증** | ✅ | 우리가 만들지 않은 한국어 457문장에서 **오탐률 1.53%** (Zeroth-Korean) |
| **크로스도메인 검증 (영상)** | ✅ | DFDC 50건 실측 — Kaggle 계정 없이 96GB zip에서 431MB만 받아 확보 |
| **실제 보이스피싱 통화 검증** | ✅ | 금융감독원 공개 「그놈 목소리」 실제 녹취 5건 → **5/5 '높음' 탐지** |
| **정상 대화 헛경보 검증** | ✅ | 한국어 자유대화 30건 → 헛경보 23.3%, 그중 **'높음'은 0%**. 주범은 음성 엔진(20.0%), 콘텐츠는 3.3%로 안정 |
| 음성 스푸핑 (AASIST) | ⚠️ | ASVspoof 120건에서 정탐 **97.1%** / 오탐 **0%** / 정확도 **98.3%**. **그러나 학습 도메인 밖에서는 진짜 목소리도 합성으로 판정한다** (원인 규명 완료) — 아래 「알려진 한계」 |
| 딥페이크 (FF++ Xception) | ⚠️ | FF++ 안에서는 정탐 **77.3%** / 오탐 **2.0%** / 정확도 **86.2%** (116건). **그러나 DFDC(다른 데이터셋)에서는 오탐률 55%로 무너진다** — 아래 「알려진 한계」 필독 |
| **실시간 스트리밍** | ✅ | 세션 API. 모델 상주 + 청크 누적 분석. **오디오 78초를 26초에 처리(3배속)** |
| STT 모델 크기 비교 | ✅ | tiny/base/small CER·처리시간 실측, 화이트노이즈 강건성 포함 (`scripts/compare_stt_models.py`) |
| **콘텐츠 분류 성능 실측** | ✅ | 시나리오 21건 기준 **정탐 90.9% / 오탐 10.0% / 정확도 90.5%** (키 없이). 키워드만 쓰던 기존 66.7%에서 개선 |
| **콘텐츠 카테고리별 정밀 채점** | ✅ | 기대 점수대(밴드) vs 실제 비교, 168개 항목 밴드 일치율 62.5% (`scripts/grade_content_rubric.py`) |
| 통합 스코어링 | ✅ | 기획서 두 버전 공식 모두 구현 |
| **분석 히스토리** | ✅ | 결과를 디스크에 저장, 서버 재시작 후에도 열람·삭제 가능 + 위험도 추이 그래프 |
| 크롬 확장 (MV3) | ✅ | **실제 크롬에서 전 과정 자동 검증 통과** (`node scripts/verify_extension_chrome.js`). 확장 단축키를 OS 레벨로 눌러 **`activeTab`을 정식으로 부여받고** 탭 오디오 캡처 → 백엔드 → 오버레이까지 확인. 우회 없음 |
| LLM 연동 경로 | ✅ | 스텁 서버로 클라이언트 코드 실행 검증 (`scripts/test_llm_client.py`) |
| **키 없이 도는 로컬 LLM** | ✅ | `DUALGUARD_LLM_PROVIDER=local` — transformers로 Qwen2.5-1.5B. **키도 설치도 불필요.** 실측 정탐 90.9% / 오탐 10.0%로 오프라인 분류기와 동률인데 **기법별 회수율은 24/41 → 30/41로 더 낫다** |
| 상용 LLM 실제 수치 | ⚠️ | Claude/Gemini는 키가 없어 미측정. **OpenAI(gpt-4o-mini)만 실측** — 기준선 62.5에서 정확도 95.2%(21건). 단 그 기준선을 같은 21건으로 정해 과적합 소지가 있다 ([0-10절](docs/validation_report.md)). 키가 없어도 오프라인 분류기(90.5%)로 정상 동작 |

성능 수치의 측정 조건은 **[docs/validation_report.md](docs/validation_report.md)** 에 있다.
**특히 영상 엔진은 학습 도메인 밖에서 무너진다** — 발표에 인용하기 전에 반드시 읽을 것.

---

## 이 프로젝트의 MVP 범위

**"완전한 고도화보다, 특정 고객에게 지금 중요한 것"** — 멘토링 피드백을 반영해
범위를 명확히 적는다.

> **한 문장 정의** — 온라인에서 처음 만난 상대와의 **브라우저 화상통화**에서,
> 대화 화법과 음성·영상 위조를 **동시에** 보고 **통화 중에** 경고한다.

| 구분 | 내용 |
|---|---|
| **누구를 위한 것인가** | 온라인에서 처음 만난 상대와 화상통화하는 사람. 중고거래·소개팅앱·투자 권유·원격 면접처럼 **상대를 아직 믿을 수 없는 통화** |
| **언제 쓰는가** | **통화 중.** 크롬 확장이 Meet/Zoom/Teams 페이지 **위에 직접** 경고를 띄운다. 우리 사이트에 올 필요가 없다 |
| **MVP의 핵심 축** | **대화 화법 분석**(가중치 0.65). 실제 사기 녹취 5/5를 잡은 것도, 정상 대화 헛경보 3.3%인 것도 이쪽이다 |
| **보조 축** | 음성·영상 위조 판별(0.35). 벤치마크 안에서는 잘 되지만 **도메인 밖 한계가 명확**해 경고와 함께 보조로만 쓴다 |
| **범위 밖 (의도적으로 안 함)** | **폰 전화 통화의 실시간 분석**(아래 참고), 다국어, 영상 딥페이크 단독 판정 |

> **왜 화법을 축으로 삼았나** — 실제 사기범은 **사람이 직접 말한다.** 금감원 공개
> 녹취 5건이 전부 그랬다. 딥페이크 탐지만 하는 방식으로는 이 5건이 **하나도**
> 안 잡힌다. 지금 시장에 실제로 필요한 건 위조 판별이 아니라 **화법 판별**이라고 봤다.

### 왜 '화상통화'로 좁혔나 — 엔진이 아니라 입구의 문제다

분석 엔진은 **오디오만 있으면 통화 종류를 가리지 않는다.** 검증에 쓴 금감원 녹취
5건은 전부 **일반 전화 통화**였고 5건 다 잡았다.

좁힌 이유는 **입구**다. 폰 통화 오디오는 iOS가 앱 접근을 막고 안드로이드도 Android 10
이후 서드파티 통화 녹음을 제한한다. **앱으로 만들어도 마찬가지다** — 통화 중 실시간
분석은 제조사(단말)나 통신사(망)만 할 수 있는 영역이다.

그래서 **지금 접근 가능한 입구부터** 만들었다.

| 통화 유형 | 통화 중 실시간 | 통화 후 분석 |
|---|---|---|
| 브라우저 화상통화 (Meet/Zoom/Teams/Whereby) | ✅ 크롬 확장 | ✅ 웹 대시보드 |
| 일반 전화 (전화망) | ❌ 플랫폼 제약 | ✅ 녹음 파일 업로드 |

**엔진과 입구를 분리해 설계했으므로**, 통신사·제조사와 연계되면 입구만 바꿔 끼우면
된다. B2B·공공 협업을 다음 단계로 잡은 것이 그 이유다
([data_roadmap.md](docs/data_roadmap.md)).

> **발표·문서에서 범위를 말할 때** — 간판은 **화상통화**다.
> 보이스피싱(전화) 데이터는 *"엔진이 실제 사기 통화에서 검증됐다"* 는 **근거**로 쓰고,
> 로맨스 스캠은 *"온라인에서 처음 만난 상대와의 화상통화"* 에 이미 포함되는
> **확장 사례**로 둔다. 셋을 나란히 간판에 걸면 시연 범위와 어긋난다.

---

## 기획서 대비 진척도

기획서(원페이지 PDF + 상세 DOCX)에 적은 항목을 하나씩 대조한 것이다.
**본선 범위(PDF 2장: "업로드한 파일을 분석해 구간별 위험도 타임라인을 제공하는 MVP")는
전부 구현됐다.** 미완은 Phase 2 이후 항목과 외부 계정이 필요한 검증이다.

### Phase 1 — 듀얼가드 코어 엔진 (본선 범위)

| 기획서 항목 | 상태 | 실제 구현 |
|---|---|---|
| STT(Whisper)로 대화 텍스트화 | ✅ | faster-whisper. 구간별 타임스탬프까지 |
| LLM이 8대 사회공학 기법 분류 | ✅ | Claude/Gemini/OpenAI/Ollama/로컬 5백엔드 + 오프라인 폴백 |
| `콘텐츠위험도 = 0.5×최고 + 0.5×상위3평균` | ✅ | 공식 그대로 |
| AASIST 음성 스푸핑 판별 | ⚠️ | ASVspoof 안에서는 98.3%. 도메인 밖에서는 신뢰 불가 |
| 딥페이크 탐지 모델로 얼굴 위조 판별 | ⚠️ | FF++ 안에서는 동작(오탐 2%). 다른 데이터셋에서는 신뢰 불가 |
| 통합 Fraud Risk Score (교차 가산) | ✅ | 기획서 두 버전 공식 모두 |
| RAG 실제 사기사례 참조 (ChromaDB) | ✅ | 한국어 임베딩, 사례 26건 (18건 + 국내 보도 기반 8건) |
| 프레임 추출 (초당 1프레임, 224~256px) | ✅ | 명세 그대로 |

### Phase 1 — 화면 (PDF 2장 [화면 설계])

| 기획서 항목 | 상태 | 실제 구현 |
|---|---|---|
| ① 업로드 화면 (드래그앤드롭 + 분석 동의) | ✅ | |
| ② 분석 진행 화면 (병렬 처리 진행률) | ✅ | WebSocket + 폴링 폴백 |
| ③ 결과 대시보드 (이중 라인 그래프) | ✅ | 콘텐츠/미디어 시간축 겹침 |
| 위험 구간 클릭 → 근거 표시 | ✅ | 걸린 표현 + 유사 사례 + 프레임 수 |
| 품질 검증 (정탐률·오탐률 실측) | ✅ | 세 엔진 각각 측정 + **크로스도메인 2종**(DFDC 영상, 도메인 밖 한국어 문장) |
| 개인정보 보호 (분석 후 즉시 삭제) | ✅ | 업로드 원본 삭제, 결과만 보관 |
| 확률적 표현으로 과신 방지 | ✅ | "위험 가능성 약 OO%" + 확정 판정 아님 명시 |

### Phase 2 — 실시간 경고 및 UX

| 기획서 항목 | 상태 | 비고 |
|---|---|---|
| 신호등 경고 UI (초록/노랑/빨강) | ✅ | 대시보드 + 확장 오버레이 |
| 3단계 액션 플랜 (주의/재확인/즉시종료+신고) | ✅ | 112·1332·사이버수사대 링크 포함 |
| 분석 히스토리 대시보드 + 위험도 추이 | ✅ | |
| **실시간 스트리밍 백엔드** | ✅ | 세션 API. 3배속으로 실시간을 따라간다 (음성·화법만) |
| 크롬 확장 실시간 캡처 (MV3 3계층) | ✅ | 실제 크롬에서 캡처→분석→오버레이까지 자동 검증 통과 (activeTab 정식 부여) |

> 기획서 PDF 2장이 본선 범위를 *"실시간 가로채기 대신 업로드 기반 MVP"* 로
> 명시했으므로 Phase 2는 원래 본선 범위 밖인데, 스트리밍까지 구현했다.

### Phase 3 — B2B·공공 확장

기획서상 사업화 단계 계획이라 구현 대상이 아니다 (API 라이선스, 데이팅앱 제휴, 공공 협업).

### 남은 것

| 항목 | 왜 안 됐나 |
|---|---|
| LLM 실제 모델 정확도 | API 키 필요. 연동 경로는 스텁 서버로 검증했고, 오프라인 분류기로 대체 동작 중 |
| 영상 엔진의 크로스도메인 성능 | 측정은 끝났다. **결과가 나빴다**(DFDC 오탐 55%). 모델을 바꾸지 않으면 개선 안 됨 |
| "진짜 목소리 + 사기 화법" 샘플 | 사람이 사기 대본을 읽어야 한다. 공개 낭독 코퍼스에는 사기 대본이 없고, **뉴스 문장을 이어 붙여 사기 통화로 포장하는 건 시연 조작이라 하지 않았다** |

각각의 해결 방법은 **[docs/blocked_and_next.md](docs/blocked_and_next.md)** 에 있다.

> **시연 담당자는 [docs/demo_guide.md](docs/demo_guide.md) 부터.**
> 심사장 시연 순서, 예상 질문 답변, 하지 말아야 할 것이 정리돼 있다.
>
> **개발자는 [docs/blocked_and_next.md](docs/blocked_and_next.md) 부터.**
> 막힌 것, 팀이 정해야 할 것, 다음 작업이 한 문서에 정리돼 있다.

---

## 빠른 시작

```powershell
# 1. 환경 (Python 3.9)
py -3.9 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 2. 외부 모델 레포 (가중치 포함)
git clone --depth 1 https://github.com/clovaai/aasist.git external/aasist
git clone --depth 1 https://github.com/ondyari/FaceForensics.git external/FaceForensics

# 3. 딥페이크 가중치 (444MB, 분할 병렬 다운로드로 약 15분)
.venv\Scripts\python.exe scripts\download_ff_weights.py
.venv\Scripts\python.exe -c "import zipfile; zipfile.ZipFile(r'models\faceforensics++_models.zip').extractall(r'models')"

# 3-1. 얼굴 검출기 YuNet (232KB, 몇 초). 없으면 Haar로 폴백하지만 검출률이 떨어진다
.venv\Scripts\python.exe scripts\download_face_detector.py

# 4. 웹 대시보드 실행
.venv\Scripts\python.exe scripts\run_server.py
#   → http://127.0.0.1:8000
```

> **⚠ `python` 명령을 쓰지 말 것.** 시스템 PATH의 `python`은 Windows 스토어 스텁이라
> 아무것도 실행되지 않는다. 항상 `.venv\Scripts\python.exe` 를 쓴다.

> **⚠ 메모리.** 전체 파이프라인은 Whisper + AASIST + Xception + 임베딩 모델을
> 차례로 쓴다. **여유 메모리 6GB 아래**에서는 `mkl_malloc` 실패나 프로세스 강제 종료가
> 발생한다. 서버와 CLI를 동시에 돌리지 말 것.

시연용 한국어 통화 샘플은 명령 두 개로 세 등급이 다 갖춰진다:

```powershell
# 빨강(사기) + 노랑(내용 정상인데 합성 음성) — Windows TTS로 생성
.venv\Scripts\python.exe scripts\make_korean_call_samples.py
# 초록(정상) — 공개 코퍼스의 진짜 사람 목소리. 사람이 녹음할 필요 없다
.venv\Scripts\python.exe scripts\fetch_korean_speech_samples.py

.venv\Scripts\python.exe scripts\analyze_call.py --input data\korean_calls\scam_call.wav
```

| 파일 | 콘텐츠 | 미디어 | Fraud Risk Score |
|---|---|---|---|
| `real_normal_call.wav` (진짜 목소리 + 정상 대화) | 0.0 | 26.7 | **9.3** 낮음 🟢 |
| `normal_call.wav` (합성 음성 + 정상 대화) | 0.0 | 100.0 | **35.0** 중간 🟡 |
| `scam_call.wav` (합성 음성 + 사기 화법) | 100.0 | 100.0 | **100.0** 높음 🔴 |

가운데 줄이 교차검증의 핵심이다 — **대화 내용은 정상인데 목소리가 합성이라 경고가 뜬다.**

---

## 구조

```
src/
  api/main.py                  FastAPI. 업로드/진행률(WebSocket)/히스토리/실시간 세션
  orchestration/
    pipeline.py                ★ 전체 분석 파이프라인. 시간축 정렬이 핵심
    streaming.py               ★ 실시간 세션. 모델을 상주시키고 청크를 누적 분석
  content_analysis/
    stt.py                     faster-whisper STT (구간별 타임스탬프)
    content_risk.py            8대 카테고리 + 집계 공식
    semantic_classifier.py     ★ 오프라인 의미 분류기 — 키 없이 90.5% 내는 부분
    llm_classifier.py          Claude/Gemini/Ollama 구조화 출력 분류 (키 없으면 폴백)
    rag.py                     ChromaDB 사례 검색 (한국어 임베딩)
  media_detection/
    media_risk.py              영상+음성을 media_risk 하나로
    faceforensics_detector.py  FF++ Xception (영상, 정식 경로)
    audio_spoof_detector.py    AASIST (음성)
    calibration.py             딥페이크 점수 재척도 — 임계값 결정 문제를 없앤 부분
    deepfake_detector.py       HuggingFace ViT (폴백 전용 — 점수는 쓰지 않는다)
    face_utils.py              YuNet + Haar 얼굴 검출. dlib 대체
  scoring/fraud_risk_score.py  통합 Fraud Risk Score (확정된 기본값이 여기 한 곳)

web/                           대시보드 (외부 라이브러리 0 — CDN 의존 없음)
extension/                     크롬 확장 (Manifest V3)
data_seed/
  scam_cases.json              RAG 사례 데이터 (출처 표기 필수)
  category_prototypes.json     의미 분류기 기준 문장 — 8기법 + 정상 + 부정문 패턴
                                (⚠ 검증셋 문장을 여기 베껴 넣으면 성능 수치가 무의미해진다)
  content_test_scenarios.json  콘텐츠 분류 채점용 라벨링 대화 21건 (사기 11 / 정상 10,
                                경계 케이스 7건 포함) + 카테고리별 기대 점수대(밴드)

data/                          (전부 .gitignore) 검증 샘플 + reports/ 분석 히스토리

scripts/
  run_server.py                서버 + 대시보드
  analyze_call.py              ★ CLI 전체 분석
  demo_cross_validation.py     교차검증 4조합 시연 (발표용)
  validate_detector.py         영상 성능 실측
  validate_audio_spoof.py      음성 성능 실측
  validate_content_risk.py     콘텐츠(8대 기법) 성능 실측 — 키워드 / 오프라인 / LLM 비교
  build_rag_index.py           RAG 색인 재생성 (기존 사례를 수정·삭제했을 때만 필요)
  test_streaming.py            ★ 실시간 세션 E2E (서버 필요)
  test_extension.js            ★ 크롬 확장 자동 검증 — 브라우저 없이 확장 코드 실행
  audit_ui.js                  ★ 웹 화면 UI 점검 — 헤드리스 크롬 스크린샷 + 글자 잘림 검출
  decide_scoring.py            통합 공식·가중치·신호등 경계를 실측으로 결정
  decide_media_combine.py      오디오·영상 결합 방식(max vs 가중평균) 결정
  calibrate_deepfake.py        딥페이크 점수 재척도 파라미터 적합 (5-겹 교차검증)
  benchmark_face_detector.py   Haar vs YuNet 검출률 비교 (회전 스트레스 포함)
  download_face_detector.py    YuNet 가중치 232KB 받기
  verify_extension_chrome.js   ★ 실제 크롬을 띄워 확장을 CDP로 자동 검증
  test_llm_client.py           LLM 클라이언트 경로 검증 (스텁 서버, 키 불필요)
  fetch_dfdc_samples.py        ★ DFDC 크로스도메인 샘플 (Kaggle 없이 96GB zip 부분 추출)
  validate_content_fpr.py      콘텐츠 분류기 도메인 밖 오탐률 (한국어 457문장)
  diagnose_audio_fp.py         ★ 음성 오탐 원인 추적 (코덱·편집·레벨 통제 실험)
  compare_deepfake_models.py   공개 딥페이크 모델 후보 비교 (교체 가치 판단)
  fetch_korean_conversation.py ★ 한국어 자연 대화체 음성 (검증용, 재배포 금지)
  validate_normal_calls.py     ★ 정상 대화 헛경보율 실측
  fetch_real_call_samples.py   ★ 실제 보이스피싱 녹취 (금융감독원 공개 자료)
  validate_real_calls.py       ★ 실제 통화에서의 탐지 성능
  calibrate_semantic.py        의미 분류기 유사도 분포 진단 (임계값 근거)
  record_call_sample.py        마이크로 통화 샘플 녹음 — 한국어 '초록(안전)' 데모용
  grade_content_rubric.py      콘텐츠 카테고리별 정밀 채점 — 기대 점수대(밴드) vs 실제
  compare_stt_models.py        faster-whisper tiny/base/small 비교 (CER·처리시간·노이즈 강건성)
  _metrics.py                  세 validate_* 스크립트 공용 지표 (정탐/오탐/분리도)
  _console.py                  콘솔 UTF-8 고정 (cp949에서 '—' 출력 시 죽던 문제)
  fetch_ff_samples.py          FF++ 검증 샘플 (16GB zip에서 필요분만)
  fetch_asvspoof_samples.py    ASVspoof 검증 샘플 (parquet 부분 읽기)
  fetch_korean_speech_samples.py  ★ 초록 시연용 '진짜 사람 목소리' 정상 통화
                                  (Zeroth-Korean, CC BY 4.0 — 사람 녹음 불필요)
  make_korean_call_samples.py  한국어 통화 샘플 (Windows TTS)
  make_demo_clips.py           교차검증 데모 클립
  download_ff_weights.py       FF++ 가중치 분할 병렬 다운로드
  download_kaggle_data.py      DFDC 등 (Kaggle 계정 필요)

docs/
  demo_guide.md                ★ 심사장 시연 대본 — 순서 / 예상 질문 / 하지 말 것
  mentoring_log.md             멘토링 일지 + 반영 내역
  data_roadmap.md              ★ 데이터 확보 로드맵 (공공데이터·AI Hub·가명화)
  blocked_and_next.md          ★ 막힌 것 / 팀 결정 사항 / 다음 작업
  validation_report.md         성능 실측 보고 ★ 발표 자료의 근거 (영상·음성·콘텐츠)
  stt_benchmark.md             STT 모델 크기(tiny/base/small) 실측 — CER·처리시간·노이즈 강건성
  spec_reconciliation.md       기획서 2종 대조
  model_research.md            모델 후보 리서치 + 세팅 실측
  overnight_report_2026-09-02.md  강동연 야간 작업 로그 (스트리밍 webm 버그 수정 · RAG +8건)
```

---

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| `POST` | `/api/analyze` | 파일 업로드 → `job_id` (분석은 백그라운드) |
| `GET` | `/api/jobs/{id}` | 진행률/상태 (웹소켓 폴백용) |
| `GET` | `/api/results/{id}` | 분석 리포트 |
| `WS` | `/ws/jobs/{id}` | 진행률 실시간 스트림 |
| `GET` | `/api/health` | 엔진별 준비 상태 |
| `GET` | `/api/history` | 지난 분석 목록 (`limit`, 기본 30건). 디스크 저장이라 서버 재시작 후에도 남는다 |
| `GET` | `/api/history/{id}` | 저장된 리포트 전문 재조회 |
| `DELETE` | `/api/history/{id}` | 기록 삭제 |
| `POST` | `/api/sessions` | **실시간 세션 시작.** 모델을 미리 올린다(안 하면 첫 청크가 26초) |
| `POST` | `/api/sessions/{id}/chunk` | 오디오 청크 투입 → 갱신된 위험도를 즉시 응답 |
| `GET` | `/api/sessions/{id}` | 현재까지의 구간별 결과 |
| `DELETE` | `/api/sessions/{id}` | 세션 종료 + 모델 해제 |

```python
from orchestration.pipeline import analyze
report = analyze("call.mp4", progress=lambda stage, r, msg: print(msg))
report.as_dict()["overall_score"]
```

업로드 파일은 **분석 완료 즉시 서버에서 삭제**한다 (기획서 개인정보 보호 설계).
히스토리는 원본 미디어가 아니라 **분석 결과 JSON만** `data/reports/` 에 남긴다.
전사 텍스트가 들어 있으므로 이 디렉터리는 `.gitignore` 대상이며 절대 커밋하지 않는다.

---

## 확정된 설정 (전부 실측으로 정했다)

기획서 두 벌이 서로 다르거나 근거 없이 잡아뒀던 값들을, 감이 아니라 데이터로 확정했다.
재현 스크립트와 근거 파일을 함께 적었으니 이견이 있으면 스크립트를 다시 돌려서 반박하면 된다.

| 항목 | 확정값 | 근거 | 재현 |
|---|---|---|---|
| **통합 공식** | 버전 B (DOCX, `multiplicative_bonus`) | 정확도는 두 공식이 사실상 동률인데, 버전 A는 임계값에서 점수가 **+15.20 계단**으로 튄다(입력 0.2 변화). "확률적 표현으로 과신 방지"라는 기획 원칙과 충돌 | `decide_scoring.py` |
| **콘텐츠·미디어 가중치** | 0.65 / 0.35 | 0.5/0.5 대비 사기를 '낮음'으로 놓치는 건수 **84 → 21**, 헛경보는 0 유지 | `decide_scoring.py` |
| **신호등 경계** | 높음 **55** / 중간 **30** (70/40에서 내림) | 위와 같은 스윕. 756개 조합 중 최적 | `decide_scoring.py` |
| **오디오·영상 결합** | `max` 유지 | 가중평균은 한쪽 채널만 위조된 경우 점수를 절반으로 깎아 **4조합 중 2개를 놓친다** | `decide_media_combine.py` |
| **딥페이크 임계값** | **결정 사항 자체를 없앰** — 점수를 재척도해 50 하나만 쓴다 | 원점수가 0/100에 몰려 임계값이 불안정했다. 재척도 후 정탐 66.7 → **75.8%**(5-겹 교차검증), 오탐 2.0% 유지. 최적 임계값이 정확히 50에 온다 | `calibrate_deepfake.py` |
| **얼굴 검출기** | YuNet (Haar는 폴백) | 30° 회전(고개 돌림)에서 검출률 **34% → 100%**, 판정 불가 영상 13개 → 0개 | `benchmark_face_detector.py` |
| **좌우 반전 TTA** | 켬 (`DUALGUARD_FF_TTA=0`으로 끔) | 정탐 77.3 → **80.3%**, 오탐 그대로 2.0%. FaceShifter 4/11 → 6/11 | `validate_detector.py` |
| **STT 모델 크기** | 파일 분석 `small`, 스트리밍 `base` | 5dB 소음에서 small CER 0.34 vs base 0.56. 스트리밍은 실시간을 따라가야 해서 3.1배 빠른 base | `compare_stt_models.py` |
| **리샘플링** | 폴리페이즈 (선형 보간에서 교체) | 8kHz 전화망 조건에서 분포 겹침 18.3 → **14.7**로 감소 | `validate_audio_spoof.py --simulate-telephone` |

> 결정 기록 원본: [docs/scoring_decision.json](docs/scoring_decision.json),
> [docs/media_combine_decision.json](docs/media_combine_decision.json),
> [data_seed/deepfake_calibration.json](data_seed/deepfake_calibration.json),
> [docs/face_detector_benchmark.json](docs/face_detector_benchmark.json)

**주의 — 스코어링 결정에 쓴 격자는 실제 통화 1,050건이 아니다.** 콘텐츠 21건과
음성 50건의 실측 점수를 교차한 합성 격자이고, 두 축이 독립이라고 가정했다.
**설정 A와 B 중 무엇이 나은지 고르는 용도**로만 유효하며, 격자에서 나온 절대
정확도(75.3%)를 제품 성능으로 인용하면 안 된다.

---

## 해야 할 것

이전 목록 6건 중 **5건을 처리했다.** 무엇을 어떻게 했는지, 남은 게 왜 남았는지 적는다.

| # | 항목 | 결과 |
|---|---|---|
| 1 | 크롬 확장 브라우저 실제 로드 | ✅ **완료.** `--load-extension`이 Chrome 137+에서 막혀 CDP `Extensions.loadUnpacked`로 우회했다. 실제 탭 오디오 캡처 → 백엔드 세션 → 오버레이까지 확인 (`verify_extension_chrome.js`). 남은 건 `activeTab` 한 조각뿐 (아래 참고) |
| 2 | LLM 실제 호출 | ◐ **절반.** 키가 없어 정확도는 못 쟀지만, Ollama API를 흉내 낸 스텁 서버로 **클라이언트 코드를 실제로 실행**해 요청 형식·스키마·파싱·비정상 응답·폴백을 검증했다 (`test_llm_client.py`) |
| 3 | DFDC 크로스도메인 검증 | ✅ **완료.** Kaggle 없이 HuggingFace 미러의 96.5GB zip에서 **431MB만 받아** 50건 확보 (`fetch_dfdc_samples.py`). **결과는 나빴다** — 아래 「알려진 한계」 |
| 4 | "진짜 목소리 + 사기 화법" 샘플 | ❌ **하지 않기로 했다.** 공개 낭독 코퍼스 22,263발화를 뒤졌지만 사기 대본은 없다. 금융·수사 뉴스 문장을 이어 붙이면 분류기는 높게 잡지만 그건 **사기 통화가 아니라 뉴스**다. 시연에 그렇게 쓰는 건 조작이다 |
| 5 | 검증셋 확충 | ✅ **완료.** 영상 50→116건, 음성 50→120건. 여기에 **도메인 밖 한국어 457문장** 오탐 측정을 새로 추가 |
| 6 | FaceShifter·NeuralTextures | ◐ **부분.** 좌우 반전 TTA로 FaceShifter 4/11→5~6/11. 정탐 전체는 77.3%→80.3%(오탐 동일). 모델을 바꾸지 않으면 여기까지 |

### 정말로 남은 것

| 항목 | 왜 |
|---|---|
| LLM 실제 정확도 | API 키 |
| 영상 엔진 일반화 | **공개 모델 4종을 재봤고 전부 더 나빴다.** 통화 도메인 데이터로 미세조정하거나 비공개 최신 모델이 필요하다 |
| **전화망을 통과한** 정상 통화 | 자유대화(KsponSpeech)로 헛경보 23.3%를 쟀지만, 그건 대면 대화라 전화망 대역·코덱이 없다. 진짜 전화 음성은 **AI Hub 콜센터 데이터 정식 신청**이 필요하다 ([data_roadmap.md](docs/data_roadmap.md)) |

---

## 알려진 한계

### ⚠ 영상 딥페이크 엔진은 학습 도메인 밖에서 무너진다 (가장 중요)

크로스도메인 검증을 실제로 해봤고, 결과가 나빴다.

| 데이터셋 | 정탐률 | 오탐률 | 정확도 |
|---|---|---|---|
| FaceForensics++ (모델 학습 도메인, 116건) | 77.3% | **2.0%** | 86.2% |
| **DFDC (다른 데이터셋, 50건)** | 70.0% | **55.0%** | **60.0%** |

DFDC에서는 진짜 영상 평균 48.5 / 가짜 평균 58.0으로 **분포가 거의 겹친다.**
c40(고압축) 가중치로 바꿔도 더 나빴다(오탐 90%). 이건 우리 구현 문제가 아니라
2019년 FF++ Xception 체크포인트의 일반화 한계이고, 딥페이크 탐지 분야의 알려진 문제다.

**모델을 바꾸면 되는지도 재봤다.** 공개 딥페이크 탐지 모델 4종을 같은 두 데이터셋에
넣어 비교했는데(`scripts/compare_deepfake_models.py`), **전부 우리보다 나빴다** —
DFDC 분리도가 +7.3 / +0.5 / −2.1 / −17.6이고 오탐은 50~100%다.
(음수는 진짜를 가짜보다 높게 준다는 뜻) 우리 FF++는 재척도 후 +9.4다.
**크로스도메인 문제는 모델 교체로 풀리지 않는다** — 공개 모델 전반의 한계다.

**그래서 이렇게 대응했다:**
- 영상 분석이 돌아가면 리포트에 **항상 이 경고를 함께 띄운다** (숨기지 않는다)
- 발표에서 영상 수치를 인용할 때는 반드시 "FF++ 기준"을 붙일 것
- 통합 점수는 **콘텐츠 가중치(0.65)가 주도**하도록 설계돼 있다

### ⚠ 음성 엔진도 학습 도메인 밖에서 무너진다 (원인 규명 완료)

금융감독원 공개 **실제 사기범 녹취 5건**은 5/5 전부 '높음'으로 잡았다(콘텐츠 엔진이
끌어올림). 그런데 **미디어 위험도가 5건 모두 100**이었다 — 사람이 직접 말한 통화인데.

끝까지 추적해서 **원인을 규명했다.** 코덱·전화망·편집은 전부 기각됐고
(오히려 점수가 내려간다), 음량을 통일해 비교하니 답이 나왔다:

| 그룹 | -39dB | -30dB | -25dB | -20dB |
|---|---|---|---|---|
| **ASVspoof 진짜 (학습 도메인)** | 0.8 | 0.4 | 0.2 | **0.3** |
| Zeroth 낭독 (도메인 밖) | 16.9 | 81.1 | 98.9 | **100.0** |
| 실제 통화 녹취 (도메인 밖) | 45.4 | 98.1 | 100.0 | **100.0** |

**학습 도메인 안에서는 레벨과 무관하게 안정적인데, 도메인 밖 진짜 음성은 음량이
올라가면 합성으로 판정된다.** 영상 엔진(DFDC 오탐 55%)과 같은 종류의 문제다.

> **이 발견이 우리 수치에 미치는 영향 두 가지**
> - 초록 시연 샘플이 낮은 점수인 건 **"진짜 목소리라서"가 아니라 "조용해서"**(-39dB)다.
>   같은 파일을 -20dB로 올리면 100이 된다.
> - **ASVspoof 98.3%도 "그 벤치마크 안에서"라는 단서 없이 인용하면 안 된다.**

레벨 정규화로 "고치면" 안 된다 — 조용한 입력을 100으로 밀어 올릴 뿐이다.
파이프라인이 음성 결과에 항상 경고를 붙이도록 했다.
재현·상세: `scripts/diagnose_audio_fp.py`, [validation_report.md](docs/validation_report.md) 0-7절.

### 그 밖의 한계

- **FaceShifter·NeuralTextures는 절반도 못 잡는다.** 2019년 체크포인트의 학습 범위 밖.
  좌우 반전 TTA로 FaceShifter를 4/11 → 5/11까지 올렸지만 거기까지다.
  학습한 3개 기법(Deepfakes/Face2Face/FaceSwap)은 11/11 전부 탐지.
- **전화망 시뮬레이션 수치를 실제 통화의 근거로 쓰지 말 것.** 스튜디오 음성에
  필터를 건 실험이었고, 실제 녹취에서는 결과가 전혀 달랐다(위 참고).
- **미디어 두 엔진이 같은 한계를 공유한다.** 그래서 통합 점수는 콘텐츠 쪽
  가중치(0.65)가 주도하도록 설계돼 있고, 실제 녹취 5/5도 콘텐츠가 잡은 것이다.
- **TTS 안내방송도 합성 음성으로 잡힌다.** 대화 내용이 정상이면 그 사실을 경고에
  같이 표시하지만, 근본적으로 "합성 음성 = 사기"가 아니라는 한계가 있다.
- **확장은 실제 크롬에서 전 과정 자동 검증된다.** 설치·권한·`activeTab` 부여·탭 오디오
  캡처·백엔드 세션·오버레이·정리까지. 확장 단축키(`Ctrl+Shift+Y`)를 OS 레벨로 눌러
  activeTab을 정식으로 받으므로 **검사를 건너뛰지 않는다.**
- **실시간 스트리밍은 음성·화법만 본다.** 영상은 실시간 예산에 안 맞아 통화 종료 후
  파일 분석에서 처리한다. 세션은 동시에 하나만 열린다(모델 상주 때문).
- **ViT 백엔드는 판별력이 없다.** 진짜를 가짜로 판정한다. 점수를 미디어 위험도에
  반영하지 않고 "영상 판정 제외" 경고를 띄운다.
- **콘텐츠 90.5%는 자체 시나리오 21건 기준이다.** 다만 이번에 **우리가 만들지 않은
  한국어 457문장**(Zeroth-Korean)으로 오탐을 따로 쟀다 — 임계값 50에서 **1.53%**,
  30에서 18.4%다. 뉴스 낭독이라 금융·수사 어휘가 많은, 분류기에 불리한 조건이었다.
  걸린 것들은 전부 대출금리·수사 관련 뉴스 문장이다.
