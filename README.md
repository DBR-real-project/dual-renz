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
| 8대 사회공학 기법 분류 | ✅ | LLM(**Claude / Gemini 선택**) + 키워드 규칙 폴백 |
| RAG 사기사례 대조 | ✅ | 사기/정상 13문장 판별 **13/13** |
| 음성 스푸핑 (AASIST) | ✅ | 정탐률 96.7%, 오탐률 0%, 정확도 98% |
| 딥페이크 (FF++ Xception) | ✅ | 정탐률 66.7%, 오탐률 0%, 정확도 80% |
| **콘텐츠 분류 성능 실측** | ⚠️ | 키워드 기준선만 측정 (정탐 55.6% / 오탐 20%). **LLM 수치는 키가 있어야 나옴** |
| 통합 스코어링 | ✅ | 기획서 두 버전 공식 모두 구현 |
| 크롬 확장 (MV3) | ⚠️ | 코드 작성 완료, **브라우저 실제 로드 테스트 미실시** |
| LLM 실제 호출 | ⚠️ | `ANTHROPIC_API_KEY` 또는 `GEMINI_API_KEY` 필요. 없으면 키워드 규칙으로 동작 |

성능 수치의 측정 조건은 **[docs/validation_report.md](docs/validation_report.md)** 에 있다.
발표에 인용하기 전에 그 문서의 전제(학습 도메인 내 측정)를 반드시 확인할 것.

> **처음 보는 사람은 [docs/blocked_and_next.md](docs/blocked_and_next.md) 부터.**
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

한국어 테스트 통화가 없다면 Windows TTS로 만들 수 있다:

```powershell
.venv\Scripts\python.exe scripts\make_korean_call_samples.py
.venv\Scripts\python.exe scripts\analyze_call.py --input data\korean_calls\scam_call.wav
```

---

## 구조

```
src/
  api/main.py                  FastAPI. 업로드/진행률(WebSocket)/결과 API
  orchestration/pipeline.py    ★ 전체 분석 파이프라인. 시간축 정렬이 핵심
  content_analysis/
    stt.py                     faster-whisper STT (구간별 타임스탬프)
    content_risk.py            8대 카테고리 + 집계 공식
    llm_classifier.py          Claude/Gemini 구조화 출력 분류 (키 없으면 폴백)
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
  content_test_scenarios.json  콘텐츠 분류 채점용 라벨링 대화 14건 (사기 9 / 정상 5)

scripts/
  run_server.py                서버 + 대시보드
  analyze_call.py              ★ CLI 전체 분석
  demo_cross_validation.py     교차검증 4조합 시연 (발표용)
  validate_detector.py         영상 성능 실측
  validate_audio_spoof.py      음성 성능 실측
  validate_content_risk.py     콘텐츠(8대 기법) 성능 실측 — 키워드 vs LLM 비교
  grade_content_rubric.py      콘텐츠 카테고리별 정밀 채점 — 기대 점수대(밴드) vs 실제
  _metrics.py                  세 validate_* 스크립트 공용 지표 (정탐/오탐/분리도)
  fetch_ff_samples.py          FF++ 검증 샘플 (16GB zip에서 필요분만)
  fetch_asvspoof_samples.py    ASVspoof 검증 샘플 (parquet 부분 읽기)
  make_korean_call_samples.py  한국어 통화 샘플 (Windows TTS)
  make_demo_clips.py           교차검증 데모 클립
  download_ff_weights.py       FF++ 가중치 분할 병렬 다운로드
  download_kaggle_data.py      DFDC 등 (Kaggle 계정 필요)

docs/
  blocked_and_next.md          ★ 막힌 것 / 팀 결정 사항 / 다음 작업
  validation_report.md         성능 실측 보고 ★ 발표 자료의 근거
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

```python
from orchestration.pipeline import analyze
report = analyze("call.mp4", progress=lambda stage, r, msg: print(msg))
report.as_dict()["overall_score"]
```

업로드 파일은 **분석 완료 즉시 서버에서 삭제**한다 (기획서 개인정보 보호 설계).

---

## 팀이 정해야 할 것

코드에는 선택지가 모두 구현돼 있고 기본값만 잡아둔 상태다.
근거는 **[docs/spec_reconciliation.md](docs/spec_reconciliation.md)**.

1. **통합 공식** — 기획서 PDF 버전 vs DOCX 버전 (내용이 다르다)
2. **오디오·영상 결합** — `max` vs `weighted_average`
3. **딥페이크 임계값** — 50(오탐 0%) vs 7.5(정탐 80%)
4. **신호등 경계** — 현재 높음 70 / 중간 40

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
