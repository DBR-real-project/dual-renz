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
| 8대 사회공학 기법 분류 | ✅ | LLM(**Claude / Gemini / Ollama**) + **오프라인 분류기** 폴백 |
| RAG 사기사례 대조 | ✅ | 사기/정상 13문장 판별 **13/13** |
| 음성 스푸핑 (AASIST) | ✅ | 정탐률 96.7%, 오탐률 0%, 정확도 98% |
| 딥페이크 (FF++ Xception) | ✅ | 정탐률 66.7%, 오탐률 0%, 정확도 80% |
| STT 모델 크기 비교 | ✅ | tiny/base/small CER·처리시간 실측, 화이트노이즈 강건성 포함 (`scripts/compare_stt_models.py`) |
| **콘텐츠 분류 성능 실측** | ✅ | 시나리오 21건 기준 **정탐 90.9% / 오탐 10.0% / 정확도 90.5%** (키 없이). 키워드만 쓰던 기존 66.7%에서 개선 |
| **콘텐츠 카테고리별 정밀 채점** | ✅ | 기대 점수대(밴드) vs 실제 비교, 168개 항목 밴드 일치율 62.5% (`scripts/grade_content_rubric.py`) |
| 통합 스코어링 | ✅ | 기획서 두 버전 공식 모두 구현 |
| **분석 히스토리** | ✅ | 결과를 디스크에 저장, 서버 재시작 후에도 열람·삭제 가능 + 위험도 추이 그래프 |
| 크롬 확장 (MV3) | ⚠️ | 코드 작성 완료, **브라우저 실제 로드 테스트 미실시** |
| LLM 실제 호출 | ⚠️ | `ANTHROPIC_API_KEY` 또는 `GEMINI_API_KEY` 필요. **없어도 오프라인 분류기(정확도 90.5%)로 정상 동작한다.** `DUALGUARD_LLM_PROVIDER=ollama`로 로컬 LLM도 가능(**이 환경엔 Ollama가 없어 연동 코드만 있고 실제 호출은 미검증**) |

성능 수치의 측정 조건은 **[docs/validation_report.md](docs/validation_report.md)** 에 있다.
발표에 인용하기 전에 그 문서의 전제(학습 도메인 내 측정)를 반드시 확인할 것.

---

## 기획서 대비 진척도

기획서(원페이지 PDF + 상세 DOCX)에 적은 항목을 하나씩 대조한 것이다.
**본선 범위(PDF 2장: "업로드한 파일을 분석해 구간별 위험도 타임라인을 제공하는 MVP")는
전부 구현됐다.** 미완은 Phase 2 이후 항목과 외부 계정이 필요한 검증이다.

### Phase 1 — 듀얼가드 코어 엔진 (본선 범위)

| 기획서 항목 | 상태 | 실제 구현 |
|---|---|---|
| STT(Whisper)로 대화 텍스트화 | ✅ | faster-whisper. 구간별 타임스탬프까지 |
| LLM이 8대 사회공학 기법 분류 | ✅ | Claude/Gemini/Ollama 3백엔드 + 오프라인 폴백 |
| `콘텐츠위험도 = 0.5×최고 + 0.5×상위3평균` | ✅ | 공식 그대로 |
| AASIST 음성 스푸핑 판별 | ✅ | 정확도 98% 실측 |
| 딥페이크 탐지 모델로 얼굴 위조 판별 | ✅ | FF++ Xception, 오탐률 0% |
| 통합 Fraud Risk Score (교차 가산) | ✅ | 기획서 두 버전 공식 모두 |
| RAG 실제 사기사례 참조 (ChromaDB) | ✅ | 한국어 임베딩, 사례 18건 |
| 프레임 추출 (초당 1프레임, 224~256px) | ✅ | 명세 그대로 |

### Phase 1 — 화면 (PDF 2장 [화면 설계])

| 기획서 항목 | 상태 | 실제 구현 |
|---|---|---|
| ① 업로드 화면 (드래그앤드롭 + 분석 동의) | ✅ | |
| ② 분석 진행 화면 (병렬 처리 진행률) | ✅ | WebSocket + 폴링 폴백 |
| ③ 결과 대시보드 (이중 라인 그래프) | ✅ | 콘텐츠/미디어 시간축 겹침 |
| 위험 구간 클릭 → 근거 표시 | ✅ | 걸린 표현 + 유사 사례 + 프레임 수 |
| 품질 검증 (정탐률·오탐률 실측) | ✅ | 세 엔진 각각 측정 |
| 개인정보 보호 (분석 후 즉시 삭제) | ✅ | 업로드 원본 삭제, 결과만 보관 |
| 확률적 표현으로 과신 방지 | ✅ | "위험 가능성 약 OO%" + 확정 판정 아님 명시 |

### Phase 2 — 실시간 경고 및 UX

| 기획서 항목 | 상태 | 비고 |
|---|---|---|
| 신호등 경고 UI (초록/노랑/빨강) | ✅ | 대시보드 + 확장 오버레이 |
| 3단계 액션 플랜 (주의/재확인/즉시종료+신고) | ✅ | 112·1332·사이버수사대 링크 포함 |
| 분석 히스토리 대시보드 + 위험도 추이 | ✅ | 이번에 추가 |
| 크롬 확장 실시간 캡처 (MV3 3계층) | ⚠️ | 코드 완료, 브라우저 로드 미검증 |
| 실시간 스트리밍 백엔드 | ❌ | 현재 파일 단건 분석. 세션 API 필요 |

> 기획서 PDF 2장이 본선 범위를 *"실시간 가로채기 대신 업로드 기반 MVP"* 로
> 명시했으므로, 위 표의 ⚠️·❌ 두 항목은 **본선 범위 밖**이다.

### Phase 3 — B2B·공공 확장

기획서상 사업화 단계 계획이라 구현 대상이 아니다 (API 라이선스, 데이팅앱 제휴, 공공 협업).

### 남은 것

| 항목 | 왜 안 됐나 |
|---|---|
| LLM 실제 호출 수치 | API 키 필요. 오프라인 분류기(90.5%)로 대체 동작 중 |
| 크롬 확장 실제 동작 | 브라우저에 로드해봐야 확인 가능 |
| DFDC 크로스도메인 검증 | Kaggle 계정 + 대회 규칙 동의 필요 |
| "진짜 목소리 + 사기 화법" 샘플 | 사람이 사기 대본을 읽어야 한다 (`scripts/record_call_sample.py --script scam`). 공개 낭독 코퍼스에는 사기 대본이 없다 |

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
| `real_normal_call.wav` (진짜 목소리 + 정상 대화) | 0.0 | 26.7 | **13.3** 낮음 🟢 |
| `normal_call.wav` (합성 음성 + 정상 대화) | 0.0 | 100.0 | **50.0** 중간 🟡 |
| `scam_call.wav` (합성 음성 + 사기 화법) | 100.0 | 100.0 | **100.0** 높음 🔴 |

가운데 줄이 교차검증의 핵심이다 — **대화 내용은 정상인데 목소리가 합성이라 경고가 뜬다.**

---

## 구조

```
src/
  api/main.py                  FastAPI. 업로드/진행률(WebSocket)/결과 API
  orchestration/pipeline.py    ★ 전체 분석 파이프라인. 시간축 정렬이 핵심
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
    deepfake_detector.py       HuggingFace ViT (폴백 전용 — 판별력 없음)
    face_utils.py              Haar cascade 얼굴 검출. dlib 대체
  scoring/fraud_risk_score.py  통합 Fraud Risk Score

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
  blocked_and_next.md          ★ 막힌 것 / 팀 결정 사항 / 다음 작업
  validation_report.md         성능 실측 보고 ★ 발표 자료의 근거 (영상·음성·콘텐츠)
  stt_benchmark.md             STT 모델 크기(tiny/base/small) 실측 — CER·처리시간·노이즈 강건성
  spec_reconciliation.md       기획서 2종 대조
  model_research.md            모델 후보 리서치 + 세팅 실측
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

```python
from orchestration.pipeline import analyze
report = analyze("call.mp4", progress=lambda stage, r, msg: print(msg))
report.as_dict()["overall_score"]
```

업로드 파일은 **분석 완료 즉시 서버에서 삭제**한다 (기획서 개인정보 보호 설계).
히스토리는 원본 미디어가 아니라 **분석 결과 JSON만** `data/reports/` 에 남긴다.
전사 텍스트가 들어 있으므로 이 디렉터리는 `.gitignore` 대상이며 절대 커밋하지 않는다.

---

## 팀이 정해야 할 것

코드에는 선택지가 모두 구현돼 있고 기본값만 잡아둔 상태다.
근거는 **[docs/spec_reconciliation.md](docs/spec_reconciliation.md)**.

1. **통합 공식** — 기획서 PDF 버전 vs DOCX 버전 (내용이 다르다)
2. **오디오·영상 결합** — `max` vs `weighted_average`
3. **딥페이크 임계값** — 50(오탐 0%) vs 7.5(정탐 80%)
4. **신호등 경계** — 현재 높음 70 / 중간 40
5. **STT 모델 크기** — 현재 `small`(강건성 우선) vs `base`(3배 빠름, clean 정확도
   거의 동일) — 근거: **[docs/stt_benchmark.md](docs/stt_benchmark.md)**

---

## 알려진 한계

- **FaceShifter·NeuralTextures 조작은 거의 못 잡는다.** 2019년 체크포인트가 학습하지
  않은 기법이다. 학습한 3개 기법은 100% 탐지.
- **실측은 모두 학습 도메인 내에서 이뤄졌다.** 한국어 음성, 실제 통화 화질,
  전화망 8kHz 압축 환경에서는 성능이 달라질 수 있다.
- **TTS 안내방송도 합성 음성으로 잡힌다.** 대화 내용이 정상이면 그 사실을 경고에
  같이 표시하지만, 근본적으로는 "합성 음성 = 사기"가 아니라는 한계가 있다.
- **크롬 확장은 브라우저에서 실제로 로드해 보지 않았다.** 문법 검사만 통과한 상태.
- **실시간 스트리밍은 미구현.** 백엔드가 파일 단건 분석만 지원한다. 확장의 청크
  전송은 그 엔드포인트를 반복 호출하는 임시 형태다.
- **ViT 백엔드는 판별력이 없다.** 진짜를 가짜로 판정한다. 오프라인 폴백 용도.
- **오프라인 분류기 90.5%는 21건짜리 자체 시나리오 기준이다.** 실제 통화 녹취로
  측정한 값이 아니고, 기준 문장(`category_prototypes.json`)을 팀이 직접 쓴 것이라
  같은 팀이 만든 검증셋과 표현이 겹칠 여지가 있다. 남은 오답 2건은
  **[docs/validation_report.md](docs/validation_report.md) 5-0절**에 원인까지 적어놨다.
