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
- [ ] `ondyari/FaceForensics` clone 후 CPU에서 `detect_from_video.py` 로 synthetic_test_clip.mp4 돌려서 세팅 난이도 실측
- [ ] 안 되면 HuggingFace ViT 파이프라인으로 즉시 대체 (해커톤 데모 안전판)
- [ ] media_risk_dummy.py의 get_deepfake_score()를 실제 모델 추론으로 교체
