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

audio/video 결합 방식은 **max로 확정**했다(2026-08-09). `scripts/decide_media_combine.py`가
데모 4조합으로 재본 결과, 가중평균은 한쪽 채널만 위조된 경우 점수를 절반으로 깎아
4개 중 2개("영상만 위조", "음성만 위조")를 '낮음'으로 놓쳤다. max는 0건이다.
"영상만 위조와 둘 다 위조가 똑같이 100"이라는 지적은 맞지만, 사용자가 할 행동
(통화를 끊을지)은 어차피 같아서 구분 실익이 없다. weighted_average도 그대로 두었으니
필요하면 mode 인자로 쓸 수 있다. 근거: docs/media_combine_decision.json
"""

from typing import Optional

from . import audio_spoof_detector as aasist_backend
from . import faceforensics_detector as ff_backend
from .deepfake_detector import (
    DEFAULT_MODEL_ID,
    FrameAggregation,
    get_shared_detector,
)
from .media_risk_dummy import MediaCombineMode
from .media_risk_dummy import get_audio_spoof_score as get_audio_spoof_score_dummy
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


def analyze_audio(
    audio_path: str,
    aggregation: FrameAggregation = FrameAggregation.TOPK_MEAN,
    max_windows: int = 8,
):
    """
    음성 스푸핑 분석 전체 결과(4초 창별 점수 포함)를 반환. 대시보드 근거용.
    영상 경로를 넣어도 된다 (ffmpeg으로 오디오 트랙을 뽑아 쓴다).
    """
    return aasist_backend.get_shared_detector().score_audio(
        audio_path, aggregation=aggregation, max_windows=max_windows
    )


def get_audio_spoof_score(audio_path: str, **kwargs) -> float:
    """
    음성 스푸핑 위험도를 0~100으로 반환. media_risk_dummy와 시그니처 호환.
    AASIST를 쓸 수 없으면 예외를 올리지 않고 더미로 폴백한다.
    """
    try:
        return round(analyze_audio(audio_path, **kwargs).spoof_score, 2)
    except Exception:
        return get_audio_spoof_score_dummy(audio_path)


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
    audio_from_video: bool = True,
    **video_kwargs,
) -> dict:
    """
    오디오 스푸핑 점수 + 영상 딥페이크 점수를 하나의 media_risk로 합쳐 반환.
    media_risk_dummy.get_media_risk()와 반환 키가 호환되며, 아래가 추가된다:
      - deepfake_is_real_model / audio_spoof_is_real_model: 실제 모델 추론 여부
      - deepfake_backend: 실제로 쓰인 영상 백엔드 ("ff" | "vit" | "dummy")
      - fallback_reason / audio_note: 폴백하거나 건너뛰었다면 그 이유
      - video_detail / audio_detail: 프레임별·구간별 점수 등 근거

    audio_path를 따로 주지 않아도 audio_from_video=True(기본)면 영상에서 오디오 트랙을
    뽑아 분석한다. 기획서 흐름("업로드된 파일에서 오디오·비디오 트랙이 분리되고
    두 엔진이 병렬로 작동")에 맞추기 위한 것으로, 화상통화 파일 하나로 두 엔진을
    모두 돌릴 수 있다. 오디오 트랙이 없는 영상이면 조용히 건너뛴다.

    video_kwargs로 backend="ff"|"vit"|"auto" 를 넘겨 영상 백엔드를 고를 수 있다.
    """
    if video_path is None and audio_path is None:
        return {
            "media_risk": 50.0,
            "audio_spoof_score": None,
            "deepfake_score": None,
            "mode": mode.value,
            "deepfake_is_real_model": False,
            "audio_spoof_is_real_model": False,
            "fallback_reason": "video/audio 경로 없음 - 완전 고정 더미값 사용",
        }

    # --- 오디오 (AASIST) ---
    audio_source = audio_path or (video_path if audio_from_video else None)
    audio_score = 0.0
    audio_detail = None
    audio_is_real_model = False
    audio_note = None

    if audio_source:
        try:
            ar = analyze_audio(audio_source)
            audio_score = round(ar.spoof_score, 2)
            audio_detail = ar.as_dict()
            audio_is_real_model = True
        except Exception as exc:
            if audio_path:
                # 오디오를 명시적으로 준 경우엔 실패를 더미로 덮되 이유를 남긴다
                audio_score = get_audio_spoof_score_dummy(audio_path)
                audio_note = f"AASIST 실패, 더미 폴백: {type(exc).__name__}: {exc}"
            else:
                # 영상에서 뽑으려다 실패 - 오디오 트랙이 없는 영상일 수 있다.
                # 이때 더미값을 넣으면 media_risk가 근거 없이 부풀어 오르므로 0으로 둔다.
                audio_score = 0.0
                audio_note = (f"영상에서 오디오를 분석하지 못해 제외했습니다 "
                              f"({type(exc).__name__})")
    else:
        audio_note = "오디오 분석 안 함 (audio_from_video=False)"

    video_score = 0.0
    video_detail = None
    is_real_model = False
    fallback_reason = None

    backend_used = None
    video_note = None
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

        # ViT 백엔드는 실측에서 판별력이 없었다. 진짜 얼굴을 Deepfake 70.43으로,
        # 같은 입력을 FF++는 0.00으로 판정했다(docs/model_research.md 실측 결과).
        # 이런 점수를 media_risk에 섞으면 **근거 없이 위험도가 부풀어 오른다.**
        # 그래서 점수는 근거용으로 남기되 결합에서는 빼고, 이유를 사용자에게 알린다.
        # 지우지 않는 이유는 FF++ 가중치가 없는 환경에서 파이프라인이 통째로
        # 죽지 않게 하기 위해서다.
        if backend_used == "vit":
            video_note = (
                "영상 판정 제외 — ViT 폴백 백엔드는 실측에서 진짜와 가짜를 구분하지 "
                "못했습니다(진짜 얼굴을 70점으로 판정). 영상 딥페이크 판정을 켜려면 "
                "FF++ 가중치를 설치하세요: scripts/download_ff_weights.py"
            )
            video_score = 0.0
            is_real_model = False

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
        "audio_spoof_is_real_model": audio_is_real_model,
    }
    if fallback_reason:
        out["fallback_reason"] = fallback_reason
    if audio_note:
        out["audio_note"] = audio_note
    if video_note:
        out["video_note"] = video_note
    if video_detail:
        out["video_detail"] = video_detail
    if audio_detail:
        out["audio_detail"] = audio_detail
    return out
