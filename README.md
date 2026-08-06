# DualGuard — 미디어 분석 파트

듀얼렌즈(DualLens) 팀 / 듀얼가드(DualGuard) 프로젝트의 **미디어 분석 엔진**과
**통합 스코어링 로직** 저장소. 담당: 이상원 (BE/미디어 분석)

통화·화상통화 파일을 받아 **영상의 얼굴 위조**와 **음성의 합성 여부**를 판별하고,
콘텐츠 분석 결과와 교차검증해 하나의 **Fraud Risk Score(0~100)** 로 통합한다.

---

## 현재 상태

| 엔진 | 상태 | 실측 성능 |
|---|---|---|
| **음성 스푸핑** (AASIST) | ✅ 동작 | 정탐률 96.7%, 오탐률 0%, 정확도 98% |
| **영상 딥페이크** (FF++ Xception) | ✅ 동작 | 정탐률 66.7%, 오탐률 0%, 정확도 80% |
| **통합 스코어링** | ✅ 완료 | 기획서 2개 버전 공식 모두 구현 |
| **콘텐츠 위험도** | ⚠ 구조만 | 8대 카테고리·집계공식 완료, LLM 분류는 강동연 파트 |

측정 근거와 조건은 **[docs/validation_report.md](docs/validation_report.md)** 참고.
숫자를 발표에 쓰기 전에 그 문서의 전제(학습 도메인 내 측정)를 반드시 확인할 것.

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

# 3. 딥페이크 탐지 가중치 (444MB, 분할 병렬 다운로드로 약 15분)
.venv\Scripts\python.exe scripts\download_ff_weights.py
.venv\Scripts\python.exe -c "import zipfile; zipfile.ZipFile(r'models\faceforensics++_models.zip').extractall(r'models')"

# 4. 동작 확인
.venv\Scripts\python.exe scripts\demo_fraud_risk_score.py
```

> **⚠ `python` 명령을 쓰지 말 것.** 시스템 PATH의 `python`은 Windows 스토어 스텁이라
> 아무것도 실행되지 않는다. 항상 `.venv\Scripts\python.exe` 를 쓴다.

---

## 구조

```
src/
  media_detection/
    media_risk.py              정식 진입점. 영상+음성을 media_risk 하나로
    faceforensics_detector.py  FF++ Xception (영상, 정식 경로)
    deepfake_detector.py       HuggingFace ViT (영상, 폴백 전용 - 판별력 없음)
    audio_spoof_detector.py    AASIST (음성)
    face_utils.py              Haar cascade 얼굴 검출. dlib 대체
    media_risk_dummy.py        전체 더미 (오프라인 데모 안전판)
  content_analysis/
    content_risk.py            8대 사회공학 기법 구조 + 집계 공식
  scoring/
    fraud_risk_score.py        통합 Fraud Risk Score (기획서 2개 버전)

scripts/
  demo_full_pipeline.py        영상 하나 -> 두 엔진 -> 통합 점수 (전체 흐름)
  demo_cross_validation.py     교차검증 4조합 비교 (발표용 핵심 자료)
  demo_fraud_risk_score.py     스코어링 로직만 (모델 없이 실행 가능)
  detect_deepfake.py           영상/이미지 딥페이크 추론 CLI
  validate_detector.py         영상 성능 실측 (정탐률/오탐률)
  validate_audio_spoof.py      음성 성능 실측
  fetch_ff_samples.py          FF++ 검증 샘플 (16GB zip에서 필요분만)
  fetch_asvspoof_samples.py    ASVspoof 검증 샘플 (parquet에서 필요분만)
  make_demo_clips.py           교차검증 데모 클립 생성
  make_synthetic_test_clip.py  테스트 영상 생성
  extract_frames.py            프레임 추출 (모델 인풋 포맷 확인용)
  download_ff_weights.py       FF++ 가중치 분할 병렬 다운로드
  download_kaggle_data.py      DFDC 등 Kaggle 데이터 (계정 필요)

docs/
  blocked_and_next.md          ★ 여기부터. 막힌 것 / 팀 결정 사항 / 다음 작업
  validation_report.md         성능 실측 보고 ★ 발표 자료의 근거
  spec_reconciliation.md       기획서 2종 대조 + 팀 결정 필요 사항
  model_research.md            모델 후보 리서치 + 세팅 실측
```

---

## 다른 파트와 붙이는 지점

### 콘텐츠 분석 (강동연)

`src/content_analysis/content_risk.py` 의 `classify_with_llm()` 하나만 채우면 된다.
8대 카테고리별 0~100 점수를 `compute_content_risk()` 에 넘기면 집계·통합은 그대로 동작한다.

```python
from content_analysis.content_risk import compute_content_risk

breakdown = compute_content_risk({
    "urgency": 90, "authority": 80, "money_transfer": 70, ...
})
breakdown.content_risk   # 0.5*최고 + 0.5*상위3평균
```

LLM 연동 전까지는 `classify_by_keywords()` 가 규칙 기반으로 대역한다.

### 오케스트레이션 (김재한)

```python
from media_detection.media_risk import get_media_risk

result = get_media_risk(video_path="uploaded.mp4")
# {"media_risk": 100.0, "deepfake_score": 100.0, "audio_spoof_score": 99.95,
#  "deepfake_backend": "ff", "video_detail": {...}, "audio_detail": {...}}
```

영상 파일 하나만 주면 오디오 트랙을 자동으로 뽑아 두 엔진을 모두 돌린다.
`video_detail` / `audio_detail` 에 프레임별·구간별 점수가 들어 있어 대시보드 근거로 쓸 수 있다.

### 대시보드 (홍수지)

`scoring.fraud_risk_score.compute_timeline()` 이 구간별 점수 리스트를 만든다.
이중 라인 그래프(콘텐츠/미디어)와 구간 클릭 시 근거 표시에 필요한 데이터가 모두 들어 있다.

---

## 팀이 정해야 할 것

코드에는 선택지가 모두 구현돼 있고 기본값만 잡아둔 상태다.
근거와 함께 **[docs/spec_reconciliation.md](docs/spec_reconciliation.md)** 에 정리했다.

1. **통합 공식** — 기획서 PDF 버전 vs DOCX 버전 (내용이 다르다)
2. **오디오·영상 결합** — `max` vs `weighted_average`
3. **프레임 점수 집계** — `mean` / `max` / `topk_mean`
4. **딥페이크 임계값** — 50(오탐 0%) vs 7.5(정탐 80%)

---

## 알려진 한계

- **FaceShifter·NeuralTextures 조작은 거의 못 잡는다.** 2019년 체크포인트가 학습하지
  않은 기법이다. 학습한 3개 기법(Deepfakes/Face2Face/FaceSwap)은 100% 탐지.
- **실측은 모두 학습 도메인 내에서 이뤄졌다.** 한국어 음성, 실제 통화 화질,
  전화망 8kHz 압축 환경에서는 성능이 달라질 수 있다.
- **얼굴이 검출되지 않으면 영상 판정을 하지 않는다.** FF++ Xception은 얼굴 크롭
  전용이라 전체 프레임을 넣으면 값이 튄다. 의도된 동작이다.
- **ViT 백엔드는 판별력이 없다.** 진짜를 가짜로 판정한다. 오프라인 폴백 용도로만 남겨뒀다.
