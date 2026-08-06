# 막힌 것 / 다음에 할 것

작성: 2026-08-06 | 이 문서만 보면 현재 상태와 다음 할 일을 알 수 있다.

---

## 1. 내가 못 한 것 — 사람이 직접 해야 함

브라우저 로그인이나 계정 인증이 필요한 절차라 자동화할 수 없었다.

### 1-1. Kaggle 자격증명 (DFDC 크로스도메인 검증용)

**왜 필요한가:** 현재 성능 실측은 전부 **모델이 학습한 도메인 안에서** 이뤄졌다.
FF++ 모델을 FF++로, AASIST를 ASVspoof로 검증한 것이라 실제 통화 영상에서
얼마나 유지되는지는 아직 모른다. DFDC는 다른 분포라 이 공백을 메운다.

**할 일:**
1. https://www.kaggle.com/settings/account → API → `Create New Token`
2. 받은 `kaggle.json` 을 `C:\Users\migle\.kaggle\kaggle.json` 에 복사
3. https://www.kaggle.com/c/deepfake-detection-challenge/rules 에서 규칙 동의
4. `.venv\Scripts\python.exe scripts\download_kaggle_data.py --dataset dfdc`

스크립트는 이미 준비돼 있고 자격증명만 생기면 바로 돈다.

**우선순위: 중.** 없어도 발표는 가능하다. 다만 "실제 통화에서도 되냐"는 질문이
나오면 답할 근거가 없다.

### 1-2. Codex CLI 로그인

설치·MCP 등록·권한 설정은 다 끝났고 로그인만 남았다.

```
! "C:\Users\migle\AppData\Roaming\npm\codex.cmd" login
```

**우선순위: 낮음.** 프로젝트 진행과 무관하다.

### 1-3. FaceForensics++ 공식 ToS 신청

기획서에 "공식 신청도 병행"이라고 적혀 있었다. 구글 폼 제출이라 자동화 불가.
https://github.com/ondyari/FaceForensics 의 신청 폼.

**우선순위: 낮음.** HuggingFace 미러(`bitmind/FaceForensicsC23`)로 이미 검증
샘플을 확보했고 가중치도 받았다. 승인이 며칠 걸리므로 지금 넣어두면 나중에 쓸 수 있는 정도.

---

## 2. 팀이 정해야 할 것 — 코드에는 선택지가 다 있음

상세 근거는 [spec_reconciliation.md](spec_reconciliation.md).

| # | 항목 | 현재값 | 왜 중요한가 |
|---|---|---|---|
| 1 | **통합 공식** (PDF vs DOCX) | DOCX 버전 | **기획서 두 벌에 다른 공식이 적혀 있다.** 심사 자료와 코드가 어긋나면 안 된다 |
| 2 | 딥페이크 임계값 (50 vs 7.5) | 50 | 50은 오탐 0%/정탐 66.7%, 7.5는 오탐 10%/정탐 80% |
| 3 | 오디오·영상 결합 (max vs 가중평균) | max | 현재 "영상만 위조"와 "둘 다 위조"가 똑같이 100점 |
| 4 | 프레임 집계 (mean/max/topk_mean) | topk_mean | |
| 5 | 코드 주석의 담당자 표기 | 이상원 | 역할표 기준. 실제 작업자와 다르면 정리 필요 |

**1번이 가장 급하다.** 발표 자료 만들기 전에 확정해야 한다.

---

## 3. 다음 개발 작업 (우선순위 순)

### 3-1. 강동연 파트 연동 — 콘텐츠 분석 LLM

**가장 중요하다.** 미디어 쪽은 두 엔진 다 동작하는데 콘텐츠 쪽이 비어 있어서
아직 "교차검증"이 반쪽이다.

붙일 지점은 하나뿐이다:
`src/content_analysis/content_risk.py` 의 `classify_with_llm()` 함수.
8대 카테고리별 0~100 점수를 반환하도록 채우면 집계·통합은 그대로 동작한다.

지금은 `classify_by_keywords()` 가 규칙 기반으로 대역하고 있다.

### 3-2. 김재한 파트 — API 오케스트레이션

```python
from media_detection.media_risk import get_media_risk
result = get_media_risk(video_path="uploaded.mp4")
```

영상 하나만 주면 오디오까지 알아서 처리한다. `video_detail` / `audio_detail` 에
대시보드 근거용 세부 점수가 들어 있다.

**성능 참고:** 영상 2.8초 + 음성 0.29초 (CPU, 영상 12프레임 기준).
FastAPI에서 동기로 돌리면 요청당 3초 정도 걸린다. 백그라운드 작업으로 빼는 것을 권한다.

### 3-3. 미해결 기술 과제

- **얼굴 미검출 시 대응** — FF++는 얼굴 크롭 전용이라 얼굴이 안 잡히면 판정을 못 한다.
  실제 통화는 각도가 틀어지는 구간이 있어 이 케이스가 자주 날 수 있다.
  Haar cascade보다 나은 검출기(YuNet 등)로 교체하거나, 미검출 구간을 UI에서
  "분석 불가"로 표시하는 처리가 필요하다.
- **FaceShifter·NeuralTextures 취약점** — 2019년 체크포인트의 학습 범위 밖.
  발표에서 한계로 먼저 밝히는 것을 권한다 ([validation_report.md](validation_report.md) 4-3).
- **점수 보정(calibration)** — FF++ 점수가 0 아니면 100으로 극단적이라
  "위험도 66%" 같은 확률 표현에 그대로 쓰기 부적절하다.
- **전화망 음성** — AASIST는 16kHz 학습인데 실제 통화는 8kHz다.
  현재 리샘플링이 선형 보간이라 품질이 좋지 않다 (`face_utils` 아님,
  `audio_spoof_detector._resample_linear`). 실제 통화 데이터를 다루게 되면 교체 필요.

---

## 4. 지금 바로 돌려볼 수 있는 것

```powershell
cd C:\Users\migle\DualGuard-MediaAnalysis

# 교차검증이 동작하는 걸 한 화면에서 (발표용 핵심 자료)
.venv\Scripts\python.exe scripts\demo_cross_validation.py

# 성능 수치 재현
.venv\Scripts\python.exe scripts\validate_detector.py --backend ff
.venv\Scripts\python.exe scripts\validate_audio_spoof.py

# 영상 하나 전체 흐름
.venv\Scripts\python.exe scripts\demo_full_pipeline.py --input data\demo_clips\both_fake.mp4
```

데이터가 지워졌다면 (git에 포함 안 됨):

```powershell
.venv\Scripts\python.exe scripts\fetch_ff_samples.py --real 20 --fake 30
.venv\Scripts\python.exe scripts\fetch_asvspoof_samples.py --bonafide 20 --spoof 30
.venv\Scripts\python.exe scripts\make_demo_clips.py
```
