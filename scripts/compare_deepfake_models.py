"""
딥페이크 탐지 모델 후보 비교 — 크로스도메인에서 쓸 만한 게 있는가
담당: 이상원

## 왜 필요한가

지금 쓰는 FF++ Xception(2019 체크포인트)은 **학습 도메인 밖에서 무너진다**:
FF++ 안에서 오탐 2%인데 DFDC에서 55%다(`docs/validation_report.md` 0-3).
c40 가중치로 바꿔도 더 나빴다. 모델을 교체하지 않으면 개선되지 않는다.

이 스크립트는 HuggingFace의 공개 딥페이크 탐지 모델들을 **같은 두 데이터셋**에
넣어 비교한다. 교체할 가치가 있는지 판단하는 게 목적이다.

## 평가 기준 (정확도만 보면 안 된다)

세 가지를 함께 본다:

1. **FF++ 성능** — 우리가 이미 잘하는 도메인. 여기서 크게 떨어지면 교체 손해다.
2. **DFDC 성능** — 문제의 도메인. 여기가 개선돼야 교체 의미가 있다.
3. **진짜/가짜 평균 점수 차이(분리도)** — 정확도는 임계값에 좌우되지만
   분리도는 모델이 실제로 두 부류를 구분하는지 보여준다. **이게 핵심 지표다.**
   분리도가 0 근처면 임계값을 어떻게 잡아도 소용없다(현재 ViT 폴백이 그렇다).

## 주의

- 모델마다 라벨 순서가 다르다. `id2label`에서 fake/real을 찾아 쓰고,
  못 찾으면 건너뛴다(추측해서 넣으면 결과가 뒤집힌다).
- 각 모델은 수백 MB~1GB를 내려받는다. 메모리 때문에 **한 번에 하나만** 올린다.

실행:
    .venv\\Scripts\\python.exe scripts/compare_deepfake_models.py
    .venv\\Scripts\\python.exe scripts/compare_deepfake_models.py --videos 20 --frames 8
"""

import argparse
import gc
import json
import sys
import time
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "src"))

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

import cv2  # noqa: E402

PROJECT_ROOT = SCRIPT_DIR.parent
REPORT = PROJECT_ROOT / "docs" / "deepfake_model_comparison.json"

DATASETS = [
    ("FF++", PROJECT_ROOT / "data" / "ff_samples"),
    ("DFDC", PROJECT_ROOT / "data" / "dfdc_samples"),
]

# 후보. 다운로드 수와 최신성을 보고 골랐다.
CANDIDATES = [
    "dima806/deepfake_vs_real_image_detection",
    "prithivMLmods/open-deepfake-detection",
    "Wvolf/ViT_Deepfake_Detection",
    "prithivMLmods/Deep-Fake-Detector-v2-Model",   # 현재 폴백 (기준선)
]

FAKE_WORDS = ("fake", "deepfake", "spoof", "manipulated", "ai", "synthetic")
REAL_WORDS = ("real", "realism", "authentic", "genuine", "human", "original")


def label_of(name: str) -> str:
    """파일명 규칙에서 라벨을 읽는다 (validate_detector.py와 동일)."""
    if name.startswith("real__"):
        return "real"
    if name.startswith("fake_"):
        return "fake"
    return ""


def load_faces(video: Path, max_frames: int):
    """1fps로 얼굴 크롭을 모은다. 모든 모델에 같은 입력을 준다."""
    from media_detection.face_utils import crop_face_square

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        return []
    fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
    interval = max(1, round(fps))
    faces, idx = [], 0
    while len(faces) < max_frames:
        ok, frame = cap.read()
        if not ok:
            break
        if idx % interval == 0:
            f = crop_face_square(frame)
            if f is not None:
                faces.append(f)
        idx += 1
    cap.release()
    return faces


def fake_index(id2label: dict):
    """모델의 라벨 순서에서 fake 쪽 인덱스를 찾는다. 못 찾으면 None."""
    lowered = {int(k): str(v).lower() for k, v in id2label.items()}
    for i, name in lowered.items():
        if any(w in name for w in FAKE_WORDS) and not any(w in name for w in REAL_WORDS):
            return i
    return None


def eval_model(model_id: str, samples: dict, frames: int):
    """모델 하나를 모든 데이터셋에서 평가한다."""
    import numpy as np
    import torch
    from PIL import Image
    from transformers import AutoImageProcessor, AutoModelForImageClassification

    proc = AutoImageProcessor.from_pretrained(model_id)
    model = AutoModelForImageClassification.from_pretrained(model_id)
    model.eval()

    fi = fake_index(model.config.id2label)
    if fi is None:
        raise RuntimeError(f"fake 라벨을 찾지 못했습니다: {model.config.id2label}")

    out = {"model": model_id, "id2label": {str(k): v for k, v in model.config.id2label.items()},
           "fake_index": fi, "datasets": {}}

    for ds_name, items in samples.items():
        reals, fakes = [], []
        for path, label, faces in items:
            if not faces:
                continue
            with torch.no_grad():
                inputs = proc(images=[Image.fromarray(cv2.cvtColor(f, cv2.COLOR_BGR2RGB))
                                      for f in faces], return_tensors="pt")
                probs = torch.softmax(model(**inputs).logits, dim=1)[:, fi].numpy()
            # 영상 점수 = 상위 k개 평균 (파이프라인과 같은 집계)
            k = max(1, len(probs) // 3)
            score = float(np.sort(probs)[-k:].mean()) * 100.0
            (fakes if label == "fake" else reals).append(score)

        if not reals or not fakes:
            continue
        r, f = np.array(reals), np.array(fakes)
        tp = int((f >= 50).sum()); fn = len(f) - tp
        fp = int((r >= 50).sum()); tn = len(r) - fp
        out["datasets"][ds_name] = {
            "n_real": len(r), "n_fake": len(f),
            "real_mean": round(float(r.mean()), 1),
            "fake_mean": round(float(f.mean()), 1),
            "separation": round(float(f.mean() - r.mean()), 1),
            "recall": round(100.0 * tp / max(1, tp + fn), 1),
            "fpr": round(100.0 * fp / max(1, fp + tn), 1),
            "accuracy": round(100.0 * (tp + tn) / (tp + tn + fp + fn), 1),
        }

    del model, proc
    gc.collect()
    return out


def main():
    parser = argparse.ArgumentParser(description="딥페이크 모델 후보 비교")
    parser.add_argument("--videos", type=int, default=24, help="데이터셋당 영상 수")
    parser.add_argument("--frames", type=int, default=8, help="영상당 얼굴 프레임 수")
    parser.add_argument("--models", default="", help="쉼표로 구분한 모델 id (기본: 내장 후보)")
    args = parser.parse_args()

    models = [m.strip() for m in args.models.split(",") if m.strip()] or CANDIDATES

    # 프레임 추출은 한 번만 하고 모든 모델이 같은 입력을 쓴다
    print("얼굴 프레임 추출 중 (모든 모델에 동일 입력을 준다)")
    samples = {}
    for ds_name, ds_dir in DATASETS:
        if not ds_dir.exists():
            print(f"  {ds_name}: 디렉터리 없음 — 건너뜀")
            continue
        picked, reals, fakes = [], 0, 0
        for p in sorted(ds_dir.glob("*.mp4")):
            lab = label_of(p.name)
            if not lab:
                continue
            # 진짜/가짜를 반반씩
            if lab == "real" and reals >= args.videos // 2:
                continue
            if lab == "fake" and fakes >= args.videos // 2:
                continue
            faces = load_faces(p, args.frames)
            if not faces:
                continue
            picked.append((p, lab, faces))
            reals += lab == "real"
            fakes += lab == "fake"
            if reals + fakes >= args.videos:
                break
        samples[ds_name] = picked
        print(f"  {ds_name}: 진짜 {reals} / 가짜 {fakes}  (프레임 {args.frames}장씩)")

    if not samples:
        print("데이터셋이 없습니다. fetch_ff_samples.py / fetch_dfdc_samples.py 먼저.")
        return 1

    results = []
    for mid in models:
        print(f"\n=== {mid} ===", flush=True)
        t0 = time.time()
        try:
            r = eval_model(mid, samples, args.frames)
        except Exception as exc:
            print(f"  실패: {type(exc).__name__}: {str(exc)[:120]}")
            results.append({"model": mid, "error": f"{type(exc).__name__}: {exc}"})
            continue
        r["elapsed_sec"] = round(time.time() - t0, 1)
        results.append(r)
        print(f"  라벨: {r['id2label']}  (fake={r['fake_index']})")
        for ds, m in r["datasets"].items():
            print(f"  {ds:<6} 정탐 {m['recall']:>5.1f}%  오탐 {m['fpr']:>5.1f}%  "
                  f"정확도 {m['accuracy']:>5.1f}%   분리도 {m['separation']:>+6.1f} "
                  f"(진짜 {m['real_mean']:.1f} / 가짜 {m['fake_mean']:.1f})")

    print("\n" + "=" * 74)
    print("요약 — 분리도(가짜평균 − 진짜평균)가 핵심. 0 근처면 임계값을 못 고른다")
    print("=" * 74)
    print(f"{'모델':<44}{'FF++':>14}{'DFDC':>14}")
    for r in results:
        if "error" in r:
            print(f"{r['model'][:44]:<44}{'실패':>14}")
            continue
        ff = r["datasets"].get("FF++", {}).get("separation")
        df = r["datasets"].get("DFDC", {}).get("separation")
        print(f"{r['model'][:44]:<44}"
              f"{(f'{ff:+.1f}' if ff is not None else '-'):>14}"
              f"{(f'{df:+.1f}' if df is not None else '-'):>14}")

    print("\n※ 현재 정식 경로는 FF++ Xception이다(분리도 FF++ 기준 매우 큼, DFDC에서 +9.5).")
    print("  후보 중 DFDC 분리도가 뚜렷하게 큰 모델이 없으면 교체 이유가 없다.")

    REPORT.write_text(json.dumps({
        "_설명": "공개 딥페이크 탐지 모델 후보를 FF++/DFDC 양쪽에서 비교. "
                 "교체 가치를 판단하기 위한 것. 분리도가 핵심 지표.",
        "videos_per_dataset": args.videos, "frames_per_video": args.frames,
        "results": results,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n상세 저장: {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
