# 사전학습 딥페이크 탐지 모델 후보 리서치
담당: 이상원 | 작성: 2026-08-06 (자동 조사 결과, 팀 검토 필요)

> 하단 표/설명은 general-purpose 에이전트가 웹 리서치로 조사한 내용입니다.
> 링크·라이선스·정확도 수치는 클론/다운로드 전에 재확인 권장.

## 최종 추천 (2일 해커톤 MVP 기준)

**1순위: `ondyari/FaceForensics` (공식 FF++ Xception 베이스라인)**
- 별도 신청서 없이 바로 받아지는 가중치: `http://kaldir.vc.in.tum.de:/FaceForensics/models/faceforensics++_models.zip`
- `python detect_from_video.py -i <video/folder> -m <model.p> -o <out_dir>` — 바로 "clone & run" 가능
- 얼굴 크롭 + 분류까지 한 번에 처리, GPU 없이도(CPU) 동작 가능한 단일 Xception 모델
- 단점: Python 3.6 시절 코드라 dlib/torchvision 버전 핀이 필요할 수 있음

**데모용 백업/보완: HuggingFace `prithivMLmods/Deep-Fake-Detector-v2-Model`**
- `pip install transformers torch pillow` 후 3줄이면 끝 (ViT-Base, Apache-2.0)
- FF++ 학습 여부 불명확 (자체 큐레이션 데이터셋, 92.12% acc) → "심사위원 데모용 즉석 결과" 용도로만, 정확도 수치는 신뢰하지 말 것
- `not-lain/deepfake` 도 후보지만 `trust_remote_code=True` 필요(작성자 코드 실행) — 해커톤엔 괜찮지만 인지하고 사용

**Xception 설치가 막힐 경우 대안: `selimsef/dfdc_deepfake_challenge` (DFDC Kaggle 1위)**
- `download_weights.sh` + `predict_submission.sh <video_dir> <out.csv>` 로 바로 추론
- Docker/다중 GPU 가정 코드라 세팅이 더 무거움, 하지만 이슈트래커에 트러블슈팅 사례 많음

## 전체 후보 목록

| 순위 | 레포 | 백본 | 학습 데이터 | 가중치 다운로드 | 라이선스 | 비고 |
|---|---|---|---|---|---|---|
| 1 | ondyari/FaceForensics | XceptionNet | FF++ (c23/c40) | 직접 링크 (신청 불필요) | MIT | 최추천, clone&run 가장 쉬움 |
| 2 | HF prithivMLmods/Deep-Fake-Detector-v2-Model | ViT-Base | 자체 큐레이션 | HF Hub 자동 | Apache-2.0 | 데모/UI 백업용, 3줄 세팅 |
| 3 | HF not-lain/deepfake | ResNet-Inception | DFDC 계열(불명확) | HF Hub 자동 | Apache-2.0 | trust_remote_code 필요 |
| 4 | selimsef/dfdc_deepfake_challenge | EfficientNet-B7 앙상블 | DFDC | download_weights.sh | MIT | DFDC 1위, Docker/다중GPU 가정 |
| 5 | mapooon/SelfBlendedImages (SBI) | EfficientNet-B4 | FF++ real + 자체 블렌딩 | Google Drive | 연구용 무료(상업 이용 별도 문의) | 크로스도메인 일반화 최고 |
| 6 | NTech-Lab/deepfake-detection-challenge | EfficientNet-B7 x3 | DFDC | Google Drive | Apache-2.0 | DFDC 3위, DSFD 얼굴탐지 별도 필요 |
| 7 | cuihaoleo/kaggle-dfdc | WS-DAN (Xception/EffNet-B3) | DFDC | Google Drive | MIT | DFDC 2위, 2022.12 아카이브(업데이트 중단) |
| 8 | megvii-research/CADDM | ResNet-34/EfficientNet-B3·B4 | FF++ | Google Drive | Apache-2.0 | FF++ AUC 99.79%로 최고치, 전처리 단계 많음 |
| 9 | SCLBD/DeepfakeBench | 36개 탐지기 통합 벤치마크 | FF++/Celeb-DF/DFDC | GitHub Releases | CC BY-NC 4.0 | 여러 모델 한번에 비교하고 싶을 때 |

## 다음 액션
- [x] `ondyari/FaceForensics` clone 후 CPU에서 세팅 난이도 실측 → 아래 "실측 결과" 참고
- [x] HuggingFace ViT 파이프라인 연동 (해커톤 데모 안전판)
- [x] get_deepfake_score()를 실제 모델 추론으로 교체 → `src/media_detection/media_risk.py`
- [ ] FF++ 가중치 다운로드 완료 후 `--backend ff` 실측 (진행 중, 서버가 느림)
- [ ] AASIST(clovaai/aasist) 음성 스푸핑 연동
- [ ] **실제 딥페이크 샘플 확보** — 아래 실측 결과의 가장 큰 공백

---

# 실측 결과 (2026-08-06, 이상원)

리서치 문서의 표는 웹 조사 결과였고, 아래는 **실제로 돌려본 결과**다. 결론이 표와 다르다.

## 환경 실측

| 항목 | 결과 |
|---|---|
| Python | 시스템 PATH의 `python`은 **Windows 스토어 스텁이라 먹통**. 실제 인터프리터는 3.9.7 |
| 해결 | `.venv` 생성 (`py -3.9 -m venv .venv`), 이후 `.venv\Scripts\python.exe` 사용 |
| torch | 2.8.0+cpu 설치 성공 (CPU 휠 인덱스 사용, CUDA 휠 2GB 회피) |
| opencv | **5.0에는 `cv2.CascadeClassifier`가 없다.** 4.11.0.86으로 고정함 |
| dlib | **설치하지 않았고, 필요도 없어졌다** (아래 참고) |
| ffmpeg | 설치돼 있음 |

## dlib 문제는 우회했다

리서치 당시 FF++ 경로의 최대 리스크로 꼽았던 dlib은 결국 **필요 없었다.**

원본 `detect_from_video.py`에서 dlib이 쓰이는 곳은 `get_frontal_face_detector()` 하나뿐이고,
FF++ README에 따르면 모델은 *"slightly enlarged face crops with a scale factor of 1.3"* 로
학습됐다. 즉 **같은 규칙으로 정사각형 크롭만 만들어주면 검출기는 무엇이든 상관없다.**

→ `src/media_detection/face_utils.py`의 `crop_face_square(scale=1.3)`가
   원본 `get_boundingbox()`와 동일한 규칙(긴 변 × 1.3, 얼굴 중심, 경계 클리핑)을 구현한다.
   얼굴 검출은 OpenCV 내장 Haar cascade로 대체 → 추가 의존성 0, 빌드툴 불필요.

원본 스크립트를 그대로 안 쓰고 래퍼(`faceforensics_detector.py`)를 만든 이유 3가지:
1. dlib 제거
2. `cv2.imshow()` GUI / 결과 영상 파일 출력 제거 (서버는 점수만 필요)
3. `torch.load` 호환 — 가중치가 torch 1.0 시절 "모델 객체 통째 pickle"이라
   torch 2.6+ 기본값(`weights_only=True`)으로는 로드 실패. `weights_only=False` 명시 필요.

## ⚠ ViT 모델(2순위)은 판별을 못 하고 있다

`prithivMLmods/Deep-Fake-Detector-v2-Model` 연동은 성공했고 파이프라인도 끝까지 돈다.
그런데 **점수가 쓸 수 없는 상태**다.

| 입력 | 성격 | Deepfake 확률 |
|---|---|---|
| lena.jpg (실제 인물 사진, 얼굴 크롭) | **진짜** | **70.43** |
| lena.jpg (전체 이미지) | **진짜** | **76.66** |
| messi5.jpg (실제 인물 사진) | **진짜** | **55.84** |
| synthetic_test_clip.mp4 (움직이는 도형, 얼굴 없음) | 해당없음 | 67.6 ~ 69.8 |

**진짜 사진을 위조로 판정하고 있고, 입력 성격과 무관하게 55~77의 좁은 밴드에 몰린다.**
사실상 판별 신호가 없다는 뜻이다. 리서치 표에 적힌 92.12% acc는 우리 입력에서 재현되지 않는다.

가능한 원인 (아직 확정 못 함):
- 라벨 순서가 뒤집혔을 가능성 — `id2label`은 `{0: Realism, 1: Deepfake}`인데 학습과 다를 수 있다
- 학습 분포와 우리 입력이 너무 다름 (lena는 1972년 스캔본)
- 모델 자체의 성능 한계

**확인하려면 확실한 딥페이크 샘플이 필요한데 아직 없다.** 진짜/가짜 각 몇 장만 있어도
라벨 뒤집힘 여부는 바로 판별된다.

### 팀에 대한 결론
> **ViT 점수를 심사 자료의 탐지 근거로 쓰지 말 것.** 현재 이 경로의 역할은
> "미디어 분석 파이프라인이 실제 추론으로 끝까지 돈다"를 보여주는 것까지다.
> 정확도 근거가 필요하면 FF++ 경로를 써야 한다.

## FF++ 경로 현황

- 코드: **준비 완료** (`src/media_detection/faceforensics_detector.py`, `--backend ff`)
- 의존성: `pretrainedmodels` 설치 완료, dlib 불필요
- 가중치: `faceforensics++_models.zip`, **444.7 MB**. 미러 없음.
  (리서치 표에 적힌 `tum.de:/FaceForensics` 는 콜론 오타. 콜론 빼야 받아진다)
- 가중치만 도착하면 바로 돌려볼 수 있는 상태

### TUM 서버가 느린 문제 → 분할 병렬 다운로드로 해결

단일 연결 처리량이 **44 KB/s**밖에 안 나온다. 444 MB면 4시간이 넘는다.
서버가 Range 요청을 지원하는 걸 확인해서 연결을 늘려봤더니 거의 선형으로 빨라졌다:

| 동시 연결 | 처리량 |
|---|---|
| 1 | 44.2 KB/s |
| 4 | 157.0 KB/s |

→ `scripts/download_ff_weights.py` 작성. 12개 구간으로 나눠 동시에 받고,
   끊기면 구간별 `.part` 파일에서 **이어받는다**. 완료 후 zip 무결성까지 검사한다.

```
.venv\Scripts\python.exe scripts\download_ff_weights.py --connections 12
```

발표장 네트워크에서 다시 받아야 할 상황이면 이 스크립트를 쓸 것. 중간에 끊겨도
다시 실행하면 받던 지점부터 이어간다.

## 재현 방법

```
py -3.9 -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 전체 파이프라인
.venv\Scripts\python.exe scripts\demo_full_pipeline.py

# 단일 이미지로 모델이 실제로 판별하는지 확인 (위 표 재현)
# 샘플 이미지는 저작권 때문에 저장소에 없다. OpenCV 샘플에서 받아온다:
#   mkdir data\sanity
#   curl -o data\sanity\real_face_lena.jpg  https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/lena.jpg
#   curl -o data\sanity\real_face_messi.jpg https://raw.githubusercontent.com/opencv/opencv/4.x/samples/data/messi5.jpg
.venv\Scripts\python.exe scripts\detect_deepfake.py --image data\sanity\real_face_lena.jpg

# FF++ 경로 (가중치 준비 후)
.venv\Scripts\python.exe scripts\detect_deepfake.py --input <영상> --backend ff
```
