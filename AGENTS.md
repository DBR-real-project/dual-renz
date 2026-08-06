# DualGuard - 미디어 분석 파트

보이스피싱/딥페이크 탐지 해커톤 프로젝트. 이 저장소는 **이상원(BE/미디어 분석)** 담당 파트.

## 아키텍처

두 개의 위험도 신호를 하나의 Fraud Risk Score(0~100)로 합친다.

- **content_risk** — 강동연 담당. STT → LLM으로 8대 사회공학 기법 분류. 아직 없어서
  `src/content_analysis/content_risk_stub.py`의 해시 기반 더미로 대역.
- **media_risk** — 이상원 담당. `src/media_detection/media_risk.py`가 정식 진입점.
  영상 딥페이크는 실제 모델 추론, 음성 스푸핑(AASIST)은 아직 더미.
- **통합** — `src/scoring/fraud_risk_score.py`. 이 부분은 완성 상태.

### src/media_detection 구성

| 파일 | 역할 |
|---|---|
| `media_risk.py` | **정식 진입점.** 영상=실제 모델, 오디오=더미. 추론 실패 시 더미 폴백 |
| `media_risk_dummy.py` | 전체 더미. 오프라인 데모 안전판이므로 지우지 말 것 |
| `deepfake_detector.py` | HuggingFace ViT 백엔드 (현재 기본값) |
| `faceforensics_detector.py` | FF++ Xception 백엔드 (가중치 필요) |
| `face_utils.py` | Haar cascade 얼굴 검출/크롭. dlib 대체 |

**⚠ 현재 ViT 모델은 실제 사진을 위조로 판정한다** (실측: 진짜 사진이 Deepfake 70%).
점수를 탐지 근거로 쓰면 안 된다. 자세한 내용과 수치는 `docs/model_research.md`의 "실측 결과" 참고.

## 중요한 미결 사항 (임의로 결정하지 말 것)

1. **통합 공식이 기획서 두 버전에서 다름.** 둘 다 구현돼 있고 `ScoringStrategy` enum으로 선택.
   기본값은 `MULTIPLICATIVE_BONUS`(DOCX 버전). 팀 회의 전까지 기본값 바꾸지 말 것.
2. **audio/video 점수 결합 방식** — 현재 `max()`가 기본(보수적). `weighted_average`도 구현돼 있음.
   팀 논의 필요.

## 인터페이스 계약

더미 모듈을 실제 모델로 교체할 때 **함수 시그니처는 유지**할 것.
`get_audio_spoof_score(path) -> float`, `get_deepfake_score(path) -> float` 둘 다 0~100 반환.
scoring 모듈은 이 계약만 알고 있으면 되도록 설계돼 있음.

## 환경 (중요)

- **`python`은 Windows 스토어 스텁이라 동작하지 않음.** 항상 `.venv\Scripts\python.exe`를 쓸 것.
  (venv 밖에서 돌려야 하면 `py -3.9`)
- venv는 Python 3.9.7 기반. 패키지는 `requirements.txt` 참고.
- **opencv는 4.x로 고정.** 5.0에는 `cv2.CascadeClassifier`가 없어서 얼굴 크롭이 죽는다.
- **dlib은 설치하지 않았고 필요 없다.** 얼굴 검출은 `face_utils.py`의 Haar cascade로 대체했다.
  dlib을 요구하는 코드를 만나면 설치하지 말고 `face_utils`로 갈아끼울 것.
- torch는 CPU 전용 휠. `--extra-index-url https://download.pytorch.org/whl/cpu` 없이 설치하면
  CUDA 휠 2GB를 받으려 하니 주의.

## 실행

```
.venv\Scripts\python.exe scripts\demo_full_pipeline.py          # 영상 -> 추론 -> 통합 점수 전체
.venv\Scripts\python.exe scripts\demo_fraud_risk_score.py       # 스코어링 로직만 (모델 불필요)
.venv\Scripts\python.exe scripts\detect_deepfake.py --input <영상>
.venv\Scripts\python.exe scripts\detect_deepfake.py --image <이미지>   # 모델 판별력 확인용
.venv\Scripts\python.exe scripts\extract_frames.py --input <영상> --fps 1 --size 256
.venv\Scripts\python.exe scripts\download_ff_weights.py    # FF++ 가중치 (444MB, 이어받기 지원)
```

## 컨벤션

- 주석·docstring은 한국어. 담당자 이름과 TODO를 명시해 두는 스타일을 유지할 것.
- 더미/스텁 코드는 랜덤 대신 **해시 기반 결정적 값**을 쓴다 (디버깅 중 점수가 흔들리지 않게).
