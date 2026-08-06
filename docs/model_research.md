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

> **⚠ 이 문서 상단의 표는 웹 조사 결과이고, 실제로 돌려본 결과는 아래 "실측 결과" 절에 있다.**
> **결론이 표와 다르다.** 2순위로 적어둔 ViT는 판별을 못 하고, 1순위 FF++는 dlib 없이도 잘 된다.

## 다음 액션
- [x] `ondyari/FaceForensics` clone 후 CPU에서 세팅 난이도 실측
- [x] HuggingFace ViT 파이프라인 연동
- [x] get_deepfake_score()를 실제 모델 추론으로 교체 → `src/media_detection/media_risk.py`
- [x] FF++ 가중치 다운로드 + `--backend ff` 실측 → **성공, 정식 경로로 확정**
- [ ] AASIST(clovaai/aasist) 음성 스푸핑 연동 ← **다음 작업**
- [ ] 실제 딥페이크 샘플 확보 — 지금은 "진짜를 진짜로 판정"만 확인했고
      "가짜를 가짜로 판정"은 아직 확인 못 했다. 가장 큰 남은 공백.
- [ ] 통화 영상은 정면 얼굴이 아닐 때가 있음 — Haar cascade 검출률이 실영상에서
      얼마나 나오는지 확인 필요 (검출 실패 시 FF++는 점수를 못 낸다)

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

**라벨 뒤집힘 가능성은 FF++ 결과로 배제됐다.** 같은 입력에 FF++는 0.0~0.64를 주는데
ViT는 69를 준다. 라벨만 뒤집힌 거라면 일관되게 반대로 나와야 하는데, ViT는 진짜/가짜
구분 없이 중간값에 몰린다. 즉 **신호 자체가 없다.**

### 팀에 대한 결론
> **ViT 점수를 심사 자료의 탐지 근거로 쓰지 말 것.** 정식 경로는 FF++ (`backend="ff"`)다.
> ViT는 FF++ 가중치를 못 쓰는 환경(가중치 없음/오프라인)에서 파이프라인이
> 죽지 않게 하는 폴백으로만 남겨둔다.

## ✅ FF++ 경로 성공 — 이쪽을 정식 경로로 쓴다

**dlib 없이 FF++ Xception이 CPU에서 돌아간다. 판별도 제대로 한다.**

같은 입력에 대한 두 백엔드 비교 (실제 인물 사진 / 그 사진으로 만든 6초 영상):

| 입력 | FF++ Xception | ViT |
|---|---|---|
| lena 얼굴 크롭 (진짜) | **0.00** | 70.43 |
| lena 얼굴 영상 6초, 6프레임 (진짜) | **0.64** | 68.89 |
| 프레임별 점수 (FF++) | 0.94 / 0.02 / 0.0 / 0.0 / 0.04 / 0.33 | 54~73 |

FF++는 진짜 얼굴을 **0에 가깝게** 판정한다. ViT는 같은 입력을 69로 판정한다.
→ **ViT의 라벨이 뒤집힌 게 아니라 그냥 판별을 못 하는 것**으로 보인다.
   (뒤집혔다면 FF++와 반대로 일관되게 나와야 하는데, ViT는 진짜/가짜 구분 없이 중간값에 몰린다)

### 가중치 선택 주의 — 섞으면 결과가 망가진다

배포본에 두 계열이 들어있고 **입력 형태가 다르다**:

| 경로 | 학습 입력 | 우리 용도 |
|---|---|---|
| `face_detection/xception/all_c23.p` | 얼굴 크롭 | **← 이걸 쓴다** |
| `full/xception/full_c23.p` | 전체 프레임 | 안 씀 |

`default_weights()`가 `face_detection` 계열 + `c23`을 자동으로 고른다.
(c23은 FF++ 논문 기준 실제 통화/유튜브 영상의 압축률에 가장 가깝다)

얼굴 크롭 모델에 전체 프레임을 넣으면 분포 밖 입력이라 값이 튄다
(실측: 축구 사진 전체를 넣으니 98.07). 그래서 얼굴 검출 실패 시 FF++ 경로는
점수를 내지 않고 **명시적으로 실패**한다.

### 세팅 중 걸린 것들 (다음에 다시 할 때 참고)

| 문제 | 해결 |
|---|---|
| `six` 없음 → 가중치 로드가 `ModuleNotFoundError`로 죽음 | `pretrainedmodels`가 의존성 선언을 안 함. `pip install six` |
| torch 2.6+ 기본값 `weights_only=True`로 로드 실패 | `weights_only=False` 명시 (TUM 공식 배포본에 한함) |
| `SourceChangeWarning` 폭탄 | 2019년 pickle이라 클래스마다 경고. 무해하므로 억제 |
| 가중치 URL 404 | 리서치 표의 `tum.de:/FaceForensics` 는 콜론 오타 |

### TUM 서버가 느린 문제 → 분할 병렬 다운로드로 해결

단일 연결 처리량이 **44 KB/s**밖에 안 나온다. 444.7 MB면 4시간이 넘는다.
서버가 Range 요청을 지원하는 걸 확인해서 연결을 늘려봤더니 거의 선형으로 빨라졌다:

| 동시 연결 | 처리량 | 444.7MB 예상 시간 |
|---|---|---|
| 1 | 44.2 KB/s | 4시간+ |
| 4 | 157.0 KB/s | 48분 |
| 12 | **~570 KB/s** | **15분 (실측 878초)** |

→ `scripts/download_ff_weights.py` 작성. 12개 구간으로 나눠 동시에 받고,
   끊기면 구간별 `.part` 파일에서 **이어받는다**. 완료 후 zip 무결성까지 검사한다.

```
.venv\Scripts\python.exe scripts\download_ff_weights.py --connections 12
.venv\Scripts\python.exe -c "import zipfile; zipfile.ZipFile(r'models\faceforensics++_models.zip').extractall(r'models')"
```

발표장 네트워크에서 다시 받아야 할 상황이면 이 스크립트를 쓸 것. 중간에 끊겨도
다시 실행하면 받던 지점부터 이어간다.

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
