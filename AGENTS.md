# DualGuard

보이스피싱·화상통화 딥페이크 탐지 해커톤 프로젝트.
처음에는 미디어 분석 파트만이었으나 **전체 파이프라인(STT · 화법분석 · RAG ·
음성/영상 위조 판별 · 통합 스코어링 · 웹 대시보드 · 크롬 확장)** 이 이 저장소에 있다.

## 작업 전 반드시 알아야 할 것

1. **`python`이 아니라 `.venv\Scripts\python.exe`** — 시스템 PATH의 python은 스토어 스텁이라 먹통이다.
2. **메모리가 빠듯하다.** Whisper + AASIST + Xception + 임베딩 모델을 순차로 쓴다.
   여유 6GB 아래면 `mkl_malloc` 실패나 **네이티브 크래시(0xC0000005)** 가 난다.
   후자는 try/except로 못 잡으므로 애초에 피해야 한다:
   - 파이프라인은 단계마다 모델을 해제한다 (`analyze(free_models=True)`)
   - AASIST 배치는 4를 넘기지 말 것
   - 서버와 CLI를 동시에 돌리지 말 것
3. **모델을 바꿀 때는 실측하고 바꿔라.** RAG 임베딩 모델을 다국어 모델로 두면
   무관한 한국어 문장을 동일 문장보다 가깝게 판정한다 (`rag.py` 상단 실측표 참고).

## 아키텍처

두 개의 위험도 신호를 하나의 Fraud Risk Score(0~100)로 합친다.

- **content_risk** — `src/content_analysis/`. STT(faster-whisper) → 8대 사회공학 기법 분류
  (LLM, 키 없으면 키워드 규칙) + RAG 사례 대조. `content_risk_stub.py`는 구버전이니 쓰지 말 것.
- **media_risk** — 이상원 담당. `src/media_detection/media_risk.py`가 정식 진입점.
  영상·음성 **둘 다 실제 모델 추론**이다. 더미는 오프라인 폴백용으로만 남아 있다.
- **통합** — `src/scoring/fraud_risk_score.py`. 이 부분은 완성 상태.

### src/media_detection 구성

| 파일 | 역할 |
|---|---|
| `media_risk.py` | **정식 진입점.** 영상+음성 둘 다 실제 모델. 실패 시 더미 폴백 |
| `media_risk_dummy.py` | 전체 더미. 오프라인 데모 안전판이므로 지우지 말 것 |
| `faceforensics_detector.py` | **FF++ Xception (영상) — 정식 경로** |
| `audio_spoof_detector.py` | **AASIST (음성) — 정확도 98.3%. 도메인이 바뀌어도 유지되는 유일한 엔진** |
| `calibration.py` | 딥페이크 점수 재척도 (임계값 50 하나만 쓰게 만드는 부분) |
| `deepfake_detector.py` | HuggingFace ViT (영상) — 폴백 전용 |
| `face_utils.py` | YuNet(1순위) + Haar(폴백) 얼굴 검출/크롭. dlib 대체 |

`get_media_risk(video_path=...)` 에 영상만 줘도 ffmpeg으로 오디오 트랙을 뽑아
두 엔진을 모두 돌린다. 오디오 트랙이 없으면 조용히 건너뛰고 `audio_note`에 이유를 남긴다.

백엔드는 `backend="auto"`(기본값)가 고른다. `models/`에 FF++ 가중치가 있으면 FF++,
없으면 ViT. 결과 dict의 `deepfake_backend`로 실제 뭐가 쓰였는지 확인할 수 있다.

**⚠ ViT는 판별을 못 한다.** 실측에서 진짜 얼굴을 Deepfake 69로 판정했다
(같은 입력에 FF++는 0.64). 그래서 지금은 **ViT가 쓰이면 영상 점수를 media_risk에서
아예 빼고** 사용자에게 "영상 판정 제외" 경고를 띄운다. 지우지는 않았다 —
FF++ 가중치가 없는 환경에서 파이프라인이 통째로 죽는 걸 막는 안전판이다.

**⚠ FF++는 얼굴 크롭 전용이다.** 얼굴이 검출 안 되면 점수를 내지 않고 예외를 던진다.
이건 버그가 아니라 의도된 동작 — 전체 프레임을 넣으면 아무 값이나 나온다.

**⚠⚠ 영상 엔진은 학습 도메인 밖에서 못 쓴다.** FF++ 안에서는 오탐 2%인데
DFDC에서는 **55%**다(실측, `docs/validation_report.md` 0-3). 파이프라인이 영상 분석
결과에 항상 경고를 붙이도록 해놨으니 **그 경고를 지우지 말 것.** 발표·문서에서
영상 수치를 인용할 때는 반드시 "FF++ 기준"을 함께 적을 것.

**⚠ 딥페이크 점수 재척도는 '집계 뒤 한 번만'이다.** `score_frames()`는 **원점수**를
돌려주고, 프레임 점수를 집계한 뒤 `calibration.calibrate()`를 한 번 적용한다.
프레임마다 변환한 뒤 집계하면 적합 방식(영상 단위)과 어긋나 **판정 경계가 50에서
벗어난다**(실제로 32에 생겼다). 순서를 바꾸지 말 것.
검증 리포트로 파라미터를 다시 적합할 때는 반드시 `score_raw` 필드를 쓸 것.

## 실시간 스트리밍 (Phase 2)

`src/orchestration/streaming.py` + `POST /api/sessions`. 파일 분석 경로와 반대로
**모델을 세션 동안 상주**시킨다. 그래서 제약이 있다:

- **동시에 세션 하나만.** 세션 중에는 파일 분석이 409로 막힌다(동시에 돌리면 죽는다).
- 청크가 90초 끊기면 죽은 세션으로 보고 자동 정리한다(`IDLE_TIMEOUT_SEC`).
  확장이 DELETE를 못 보내고 죽는 경우가 실제로 있었다.
- 영상은 안 본다. 프레임마다 얼굴 검출 + Xception은 실시간 예산에 안 맞는다.
- 실측: 오디오 78초를 26초에 처리(약 3배속).

크롬 확장 검증은 두 단계다 (둘 다 서버를 먼저 띄울 것):

- `node scripts/test_extension.js` — 브라우저 없이 확장 코드를 그대로 실행. 빠르다.
- `node scripts/verify_extension_chrome.js` — **진짜 크롬을 띄워** CDP로 조작.
  `--load-extension`은 Chrome 137+에서 막혔으므로 `Extensions.loadUnpacked`를 쓴다.
  `tabCapture`의 activeTab 검사는 사람의 실제 클릭에만 통과하므로, 자동 실행은
  `--allowlisted-extension-id`로 그 검사만 건너뛴다. 사람이 눌러 확인하려면 `--manual`.

## 확정된 설정 (2026-08-09, 전부 실측 근거 있음)

바꾸려면 **먼저 해당 스크립트를 다시 돌려 반박 데이터를 낼 것.** 감으로 바꾸지 말 것.

| 항목 | 값 | 재현 스크립트 |
|---|---|---|
| 통합 공식 | `MULTIPLICATIVE_BONUS` (DOCX). 버전A는 임계값에서 +15.2 계단이 생긴다 | `decide_scoring.py` |
| 콘텐츠/미디어 가중치 | 0.65 / 0.35 (놓침 84→21) | `decide_scoring.py` |
| 신호등 경계 | 높음 55 / 중간 30 | `decide_scoring.py` |
| audio/video 결합 | `max` (가중평균은 4조합 중 2개를 놓침) | `decide_media_combine.py` |
| 딥페이크 점수 | 재척도 후 임계값 50 하나만 사용 | `calibrate_deepfake.py` |
| 얼굴 검출기 | YuNet 1순위, Haar 폴백 | `benchmark_face_detector.py` |
| STT | 파일 `small`, 스트리밍 `base` | `compare_stt_models.py` |
| 좌우 반전 TTA | 켬 (`DUALGUARD_FF_TTA=0`으로 끔). 정탐 +3%p, 오탐 동일 | `validate_detector.py` |

기본값은 `src/scoring/fraud_risk_score.py`의 `DEFAULT_STRATEGY` / `DEFAULT_CONTENT_WEIGHT`와
`src/orchestration/pipeline.py`의 `LEVEL_THRESHOLDS` **한 곳씩**에만 있다. 하드코딩하지 말 것.

## 인터페이스 계약

더미 모듈을 실제 모델로 교체할 때 **함수 시그니처는 유지**할 것.
`get_audio_spoof_score(path) -> float`, `get_deepfake_score(path) -> float` 둘 다 0~100 반환.
scoring 모듈은 이 계약만 알고 있으면 되도록 설계돼 있음.

## 환경 (중요)

- **`python`은 Windows 스토어 스텁이라 동작하지 않음.** 항상 `.venv\Scripts\python.exe`를 쓸 것.
  (venv 밖에서 돌려야 하면 `py -3.9`)
- venv는 Python 3.9.7 기반. 패키지는 `requirements.txt` 참고.
- **opencv는 4.x로 고정.** 5.0에는 `cv2.CascadeClassifier`가 없어서 얼굴 크롭이 죽는다.
- **dlib은 설치하지 않았고 필요 없다.** 얼굴 검출은 `face_utils.py`가 한다
  (YuNet 1순위, Haar 폴백). YuNet 가중치는 `scripts/download_face_detector.py`로
  받는다(232KB, 없으면 Haar로 자동 폴백).
- torch는 CPU 전용 휠. `--extra-index-url https://download.pytorch.org/whl/cpu` 없이 설치하면
  CUDA 휠 2GB를 받으려 하니 주의.
- **콘솔이 cp949라 한글 출력 중에 죽을 수 있다.** `—`(em dash), `█`, `⚠` 같은 글자가
  cp949에 없어서 `UnicodeEncodeError`가 난다. 분석은 다 끝나고 결과를 찍다가 죽어서
  원인을 찾기 어렵다. **새 CLI 스크립트를 만들면 `scripts/_console.py`의
  `setup_console()`을 맨 위에서 호출할 것.** 기존 스크립트에는 전부 넣어놨다.

## 실행

```
.venv\Scripts\python.exe scripts\run_server.py                  # 웹 대시보드 (권장)
.venv\Scripts\python.exe scripts\analyze_call.py --input <파일>  # CLI 전체 분석
.venv\Scripts\python.exe scripts\demo_cross_validation.py       # 교차검증 4조합 (발표용)
.venv\Scripts\python.exe scripts\demo_fraud_risk_score.py       # 스코어링만 (모델 불필요)
.venv\Scripts\python.exe scripts\validate_detector.py           # 영상 성능 실측
.venv\Scripts\python.exe scripts\validate_audio_spoof.py        # 음성 성능 실측
.venv\Scripts\python.exe scripts\make_korean_call_samples.py    # 한국어 통화 샘플 (TTS)
.venv\Scripts\python.exe scripts\fetch_korean_speech_samples.py # 초록 시연용 진짜 사람 목소리
.venv\Scripts\python.exe scripts\test_streaming.py              # 실시간 세션 E2E (서버 필요)
node scripts\test_extension.js                                  # 크롬 확장 검증 (서버 필요)
node scripts\verify_extension_chrome.js                         # 실제 크롬으로 확장 검증
.venv\Scripts\python.exe scripts\test_llm_client.py              # LLM 클라이언트 경로 (키 불필요)
.venv\Scripts\python.exe scripts\fetch_dfdc_samples.py           # DFDC 크로스도메인 샘플
.venv\Scripts\python.exe scripts\validate_content_fpr.py         # 콘텐츠 도메인 밖 오탐률
.venv\Scripts\python.exe scripts\decide_scoring.py              # 스코어링 설정 재결정
.venv\Scripts\python.exe scripts\benchmark_face_detector.py     # 얼굴 검출기 비교
```

전체 목록과 각 스크립트 설명은 `README.md` 참고.

## 코드 컨벤션

- 주석·docstring은 한국어. **왜 그렇게 했는지**를 적는다. 특히 실측으로 정한 값
  (배치 크기, 임계값, 모델 선택)은 근거 수치를 함께 남긴다.
- 외부 의존성을 늘리기 전에 대안을 먼저 본다. dlib 대신 OpenCV Haar cascade,
  차트 라이브러리 대신 SVG 직접 생성 — 둘 다 시연 환경의 리스크를 줄이려는 선택이다.
- 실패는 조용히 넘기지 말고 `warnings`에 남겨 사용자에게 보여준다.
  (어떤 엔진이 실제로 쓰였는지 리포트에 항상 표시된다)

## 데이터셋을 부분만 받는 패턴

공개 데이터셋이 통짜 대용량 파일로 배포되는 경우가 많다(FF++ 16.66GB zip,
ASVspoof 464MB parquet). `scripts/_http_range.py`의 `HttpRangeFile`이
HTTP Range로 원격 파일을 로컬 파일처럼 읽게 해줘서, zipfile과 pyarrow 둘 다
그대로 쓸 수 있다. **전체를 받지 말고 이 패턴을 먼저 검토할 것.**
실적:
  - FF++ 영상 116개 ← 16.66GB zip
  - ASVspoof 음성 120개 ← 464MB parquet
  - **DFDC 영상 50개 ← 96.5GB zip에서 431MB만** (Kaggle 계정 없이 크로스도메인 검증 확보)
  - Zeroth-Korean 문장 457개 ← 오디오 컬럼을 건너뛰고 텍스트 컬럼만

## 컨벤션

- 주석·docstring은 한국어. 담당자 이름과 TODO를 명시해 두는 스타일을 유지할 것.
- 더미/스텁 코드는 랜덤 대신 **해시 기반 결정적 값**을 쓴다 (디버깅 중 점수가 흔들리지 않게).
