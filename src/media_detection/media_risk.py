"""
미디어 위험도(media_risk) - 실제 모델 연동 버전
담당: 이상원 (BE/미디어 분석)

media_risk_dummy.py와 **동일한 인터페이스**를 제공하되, 영상 딥페이크 점수만
실제 모델 추론으로 교체한 모듈. 음성 스푸핑(AASIST)은 아직 미연동이라
더미 모듈에 그대로 위임한다.

  media_risk_dummy.py는 지우지 않았다. 모델 다운로드가 안 되는 환경(오프라인,
  발표장 네트워크 등)에서 데모가 통째로 죽지 않도록, 추론 실패 시 더미로
  폴백하고 그 사실을 결과에 fallback_reason으로 남긴다.

사용:
    from media_detection.media_risk import get_media_risk
    get_media_risk(video_path="data/test_clips/xxx.mp4")

TODO(이상원): get_audio_spoof_score를 clovaai/aasist 추론으로 교체
TODO(팀 논의): audio/video 결합 방식(max vs weighted_average) - 더미 모듈과 동일하게
               일단 max가 기본값. 결정되면 두 모듈 모두 반영할 것.
"""

from typing import Optional

from . import faceforensics_detector as ff_backend
from .deepfake_detector import (
    DEFAULT_MODEL_ID,
    FrameAggregation,
    get_shared_detector,
)
from .media_risk_dummy import (
    MediaCombineMode,
    get_audio_spoof_score,  # AASIST 연동 전까지 더미 그대로 사용
)
from .media_risk_dummy import get_deepfake_score as get_deepfake_score_dummy

# 백엔드 선택
#   "auto" : FF++ 가중치가 준비돼 있으면 FF++, 아니면 ViT (기본값)
#   "ff"   : FF++ Xception 강제 (가중치 없으면 예외)
#   "vit"  : HuggingFace ViT 강제
DEFAULT_BACKEND = "auto"


def resolve_backend(backend: str = DEFAULT_BACKEND) -> str:
    """'auto'를 실제 백엔드 이름으로 확정한다."""
    if backend != "auto":
        return backend
    return "ff" if ff_backend.is_available() else "vit"


def analyze_video(
    video_path: str,
    target_fps: float = 1.0,
    max_frames: int = 32,
    aggregation: FrameAggregation = FrameAggregation.TOPK_MEAN,
    use_face_crop: bool = True,
    model_id: str = DEFAULT_MODEL_ID,
    backend: str = DEFAULT_BACKEND,
):
    """
    영상 딥페이크 분석 전체 결과(프레임별 점수 포함)를 반환. 대시보드 근거용.
    반환 타입은 백엔드에 따라 VideoDeepfakeResult 또는 FFVideoResult지만
    둘 다 .deepfake_score / .as_dict() 를 갖는다.
    """
    resolved = resolve_backend(backend)

    if resolved == "ff":
        return ff_backend.get_shared_detector().score_video(
            video_path,
            target_fps=target_fps,
            max_frames=max_frames,
            aggregation=aggregation,
        )

    if resolved == "vit":
        return get_shared_detector(model_id).score_video(
            video_path,
            target_fps=target_fps,
            max_frames=max_frames,
            aggregation=aggregation,
            use_face_crop=use_face_crop,
        )

    raise ValueError(f"알 수 없는 backend: {backend}")


def get_deepfake_score(video_path: str, **kwargs) -> float:
    """
    영상 딥페이크 위험도를 0~100으로 반환. media_risk_dummy와 시그니처 호환.
    추론이 실패하면 예외를 올리지 않고 더미값으로 폴백한다(데모 안전판).
    """
    try:
        return round(analyze_video(video_path, **kwargs).deepfake_score, 2)
    except Exception:
        return get_deepfake_score_dummy(video_path)


def get_media_risk(
    video_path: Optional[str] = None,
    audio_path: Optional[str] = None,
    mode: MediaCombineMode = MediaCombineMode.MAX,
    audio_weight: float = 0.5,
    video_weight: float = 0.5,
    **video_kwargs,
) -> dict:
    """
    오디오 스푸핑 점수 + 영상 딥페이크 점수를 하나의 media_risk로 합쳐 반환.
    media_risk_dummy.get_media_risk()와 반환 키가 호환되며, 아래가 추가된다:
      - deepfake_is_real_model: 영상 점수가 실제 모델 추론인지 여부
      - deepfake_backend: 실제로 쓰인 백엔드 ("ff" | "vit" | "dummy")
      - fallback_reason: 폴백했다면 그 이유
      - video_detail: 프레임별 점수 등 근거 (성공했을 때만)

    video_kwargs로 backend="ff"|"vit"|"auto" 를 넘겨 백엔드를 고를 수 있다.
    """
    if video_path is None and audio_path is None:
        return {
            "media_risk": 50.0,
            "audio_spoof_score": None,
            "deepfake_score": None,
            "mode": mode.value,
            "deepfake_is_real_model": False,
            "fallback_reason": "video/audio 경로 없음 - 완전 고정 더미값 사용",
        }

    audio_score = get_audio_spoof_score(audio_path) if audio_path else 0.0

    video_score = 0.0
    video_detail = None
    is_real_model = False
    fallback_reason = None

    backend_used = None
    if video_path:
        backend_used = resolve_backend(video_kwargs.get("backend", DEFAULT_BACKEND))
        try:
            result = analyze_video(video_path, **video_kwargs)
            video_score = round(result.deepfake_score, 2)
            video_detail = result.as_dict()
            is_real_model = True
        except Exception as exc:
            video_score = get_deepfake_score_dummy(video_path)
            fallback_reason = f"{type(exc).__name__}: {exc}"
            backend_used = "dummy"

    if mode == MediaCombineMode.MAX:
        media_risk = max(audio_score, video_score)
    elif mode == MediaCombineMode.WEIGHTED_AVERAGE:
        media_risk = audio_weight * audio_score + video_weight * video_score
    else:
        raise ValueError(f"알 수 없는 mode: {mode}")

    out = {
        "media_risk": round(media_risk, 2),
        "audio_spoof_score": audio_score,
        "deepfake_score": video_score,
        "mode": mode.value,
        "deepfake_is_real_model": is_real_model,
        "deepfake_backend": backend_used,   # "ff" | "vit" | "dummy" | None
        "audio_spoof_is_real_model": False,  # AASIST 미연동
    }
    if fallback_reason:
        out["fallback_reason"] = fallback_reason
    if video_detail:
        out["video_detail"] = video_detail
    return out
