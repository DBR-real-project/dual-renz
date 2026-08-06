# DualGuard - 미디어 분석 파트

보이스피싱/딥페이크 탐지 해커톤 프로젝트. 이 저장소는 **이상원(BE/미디어 분석)** 담당 파트.

## 아키텍처

두 개의 위험도 신호를 하나의 Fraud Risk Score(0~100)로 합친다.

- **content_risk** — 강동연 담당. STT → LLM으로 8대 사회공학 기법 분류. 아직 없어서
  `src/content_analysis/content_risk_stub.py`의 해시 기반 더미로 대역.
- **media_risk** — 이상원 담당. AASIST(음성 스푸핑) + 딥페이크 탐지(영상). 아직 모델 미연동이라
  `src/media_detection/media_risk_dummy.py`의 더미값 사용.
- **통합** — `src/scoring/fraud_risk_score.py`. 이 부분은 완성 상태.

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

- **`python`은 Windows 스토어 스텁이라 동작하지 않음.** 반드시 아래 인터프리터를 쓸 것:
  `C:\Users\migle\AppData\Local\Programs\Python\Python39\python.exe` (또는 `py -3.9`)
- 설치돼 있음: `cv2`, `numpy`, `requests`, ffmpeg CLI
- 없음: `torch`, `torchvision`, `transformers`, `PIL`, `dlib` — 딥페이크 모델 붙이려면 설치 필요
- Python 3.9라서 최신 패키지 버전 핀에 주의

## 실행

```
py -3.9 scripts/demo_fraud_risk_score.py
py -3.9 scripts/extract_frames.py --input data/test_clips/synthetic_test_clip.mp4 --fps 1 --size 256
```

## 컨벤션

- 주석·docstring은 한국어. 담당자 이름과 TODO를 명시해 두는 스타일을 유지할 것.
- 더미/스텁 코드는 랜덤 대신 **해시 기반 결정적 값**을 쓴다 (디버깅 중 점수가 흔들리지 않게).
