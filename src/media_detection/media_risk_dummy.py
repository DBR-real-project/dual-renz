"""
미디어 위험도(media_risk) 더미 모듈
담당: 이상원 (BE/미디어 분석)

실제 AASIST(음성 안티스푸핑) + 딥페이크 탐지 모델이 아직 연동되기 전까지,
Fraud Risk Score 통합 로직(src/scoring/fraud_risk_score.py)을 먼저 완성하고
테스트할 수 있도록 media_risk(0~100) 자리를 채워주는 더미 구현.

실제 모델이 준비되면 아래 두 함수(get_audio_spoof_score, get_deepfake_score)의
내부만 실제 추론 코드로 교체하면 되고, 외부(scoring 모듈 등)에서 보는 인터페이스
(입력: 파일 경로, 출력: 0~100 float)는 그대로 유지되도록 설계함.

TODO(이상원): AASIST(clovaai/aasist) 연동 후 get_audio_spoof_score 구현
TODO(이상원): FF++ 기반 사전학습 딥페이크 탐지 모델 연동 후 get_deepfake_score 구현
TODO(팀 논의): audio_spoof_score와 deepfake_score를 media_risk 하나로 합칠 때
               max(둘 중 더 의심스러운 쪽 우선, 보수적) vs
               weighted_average(부드러운 통합) 중 어떤 걸 기본값으로 할지 결정 필요.
               일단 기본값은 max()로 설정 — 둘 중 하나만 위조돼도 놓치지 않기 위함.
"""

import hashlib
from enum import Enum
from typing import Optional


class MediaCombineMode(str, Enum):
    MAX = "max"
    WEIGHTED_AVERAGE = "weighted_average"


def _deterministic_dummy_score(seed_text: str, low: float = 10.0, high: float = 90.0) -> float:
    """
    완전 랜덤 대신, 같은 입력(파일 경로/이름)에 대해 항상 같은 더미값을 반환하도록
    해시 기반 의사난수를 사용. 데모/테스트 중 매번 점수가 바뀌면 디버깅이 어려워지는 것을 방지.
    """
    digest = hashlib.md5(seed_text.encode("utf-8")).hexdigest()
    ratio = int(digest[:8], 16) / 0xFFFFFFFF
    return low + ratio * (high - low)


def get_audio_spoof_score(audio_path: str) -> float:
    """
    [더미] AASIST 기반 음성 스푸핑(합성·변조) 위험도를 0~100으로 반환.
    실제 구현 예정: clovaai/aasist 체크포인트로 추론 -> spoof 확률을 0~100으로 스케일링.
    """
    return round(_deterministic_dummy_score(f"audio::{audio_path}"), 2)


def get_deepfake_score(video_path: str) -> float:
    """
    [더미] 딥페이크 탐지 모델 기반 영상 프레임 얼굴 위조 위험도를 0~100으로 반환.
    실제 구현 예정: FF++ 기반 사전학습 모델로 프레임별 추론 -> 구간 평균/최댓값 등으로 집계.
    """
    return round(_deterministic_dummy_score(f"video::{video_path}"), 2)


def get_media_risk(
    video_path: Optional[str] = None,
    audio_path: Optional[str] = None,
    mode: MediaCombineMode = MediaCombineMode.MAX,
    audio_weight: float = 0.5,
    video_weight: float = 0.5,
) -> dict:
    """
    오디오 스푸핑 점수 + 영상 딥페이크 점수를 하나의 media_risk로 합쳐 반환.
    video_path/audio_path 둘 다 없으면 완전 고정 더미값(50.0)을 반환 (프레임 추출 전 스모크 테스트용).

    반환값: {"media_risk": float, "audio_spoof_score": float, "deepfake_score": float, "mode": str}
    """
    if video_path is None and audio_path is None:
        return {
            "media_risk": 50.0,
            "audio_spoof_score": None,
            "deepfake_score": None,
            "mode": mode.value,
            "note": "video/audio 경로 없음 - 완전 고정 더미값 사용",
        }

    audio_score = get_audio_spoof_score(audio_path) if audio_path else 0.0
    video_score = get_deepfake_score(video_path) if video_path else 0.0

    if mode == MediaCombineMode.MAX:
        media_risk = max(audio_score, video_score)
    elif mode == MediaCombineMode.WEIGHTED_AVERAGE:
        media_risk = audio_weight * audio_score + video_weight * video_score
    else:
        raise ValueError(f"알 수 없는 mode: {mode}")

    return {
        "media_risk": round(media_risk, 2),
        "audio_spoof_score": audio_score,
        "deepfake_score": video_score,
        "mode": mode.value,
    }


if __name__ == "__main__":
    print(get_media_risk("data/test_clips/sample.mp4", "data/test_clips/sample.wav"))
    print(get_media_risk())
