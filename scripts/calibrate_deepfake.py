"""
딥페이크 점수 재척도(rescaling) 파라미터 적합
담당: 이상원

`src/media_detection/calibration.py`가 쓰는 a, b를 구한다.
왜 필요한지는 그 모듈의 docstring에 있다. 요약하면
**"임계값을 얼마로 할까"라는 팀 결정 사항을 없애기 위해서**다.
변환 후에는 코드 전체가 임계값 50 하나만 쓴다.

입력은 `scripts/validate_detector.py --backend ff` 가 남긴
`docs/validation_report.json`이다. 검출기나 샘플이 바뀌면 그걸 먼저 다시 돌릴 것.

## 방법을 두 개 넣은 이유 (실측으로 고른 기록)

처음엔 교과서대로 **Platt scaling**(로지스틱 회귀로 로그손실 최소화)을 썼다.
결과가 나빴다:

    보정 전        정탐 63.3%  오탐  0.0%
    Platt (5-겹)   정탐 80.0%  오탐 25.0%   ← 오탐이 0에서 25%로 폭증

로그손실은 "평균적으로 잘 맞히는" 지점을 고르는데, 사기 경보 제품에서
**오탐과 정탐의 비용은 전혀 대칭이 아니다.** 정상 통화를 사기라고 하면
사용자가 제품을 버린다. 그래서 목적함수를 바꿨다.

**앵커 방식**(기본값)은 두 점을 고정해 1차 변환을 결정한다:

    원점수 t*       -> 50    t* = 오탐률을 --max-fpr 이하로 지키며 정탐이 최대인 임계값
    원점수 t*/5    -> 20    기울기를 정하는 두 번째 앵커 (SLOPE_DIVISOR 주석 참고)

기준을 '진짜 최고점'이 아니라 '분위수'로 잡은 이유는 anchor_points() docstring에
적어뒀다. 요약하면 **진짜 영상 한 건이 튀면 설정 전체가 끌려가기 때문**이다.

## 이건 확률이 아니다

변환 후 점수를 "위조일 확률 73%"라고 읽으면 안 된다. 50건으로 진짜 확률
보정을 할 수는 없다. **50이 판정 경계가 되도록 맞춘 위험도 축**일 뿐이고,
단조 변환이라 순위는 원점수와 같다. 리포트에도 그렇게 쓴다.

실행:
    .venv\\Scripts\\python.exe scripts/calibrate_deepfake.py
    .venv\\Scripts\\python.exe scripts/calibrate_deepfake.py --method platt --dry-run
"""

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORT = PROJECT_ROOT / "docs" / "validation_report.json"
OUT = PROJECT_ROOT / "data_seed" / "deepfake_calibration.json"

EPS = 1e-4
THRESHOLD = 50.0

# 앵커 방식에서 진짜 샘플 최고점이 변환 후 가야 할 위치.
#
# 이 값은 성능에 영향을 주지 않는다. 판정 경계(t*->50)는 다른 앵커가 고정하므로,
# 이 값은 **변환의 기울기만** 바꾼다. 즉 정탐/오탐은 그대로 두고 "점수가 얼마나
# 퍼져 보이는가"만 조절하는 손잡이다.
#   10으로 두면 기울기가 가팔라 원점수 30이 이미 92가 된다(다시 0/100에 몰림).
#   20으로 두면 완만해져 원점수 30이 약 77 — 중간값이 살아난다.
# 신호등 '중간' 경계가 40이라 20은 여전히 확실한 '낮음'이다.
REAL_MAX_TARGET = 20.0

# 기울기를 정하는 두 번째 앵커의 위치: "판정 경계의 1/SLOPE_DIVISOR 인 원점수"가
# REAL_MAX_TARGET(20)으로 가도록 맞춘다.
#
# 왜 데이터에서 뽑지 않고 상수로 두는가: 처음엔 두 번째 앵커도 데이터(진짜 점수
# 분위수)에서 잡았는데, 하필 진짜 22.90 바로 위에 가짜 23.71이 있어서 두 앵커가
# 22.9와 23.3으로 붙어버렸다. 두 점이 붙으면 기울기가 62까지 치솟아 변환이
# 계단 함수가 되고("원점수 30 -> 100") 재척도의 목적인 '중간값 살리기'가 무너진다.
# 위치(정확도)는 t*가 정하고, 기울기(가독성)는 이 상수가 정하도록 분리했다.
SLOPE_DIVISOR = 5.0


def to_logit(score: float) -> float:
    x = min(max(score / 100.0, EPS), 1.0 - EPS)
    return math.log(x / (1.0 - x))


def sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z))


def anchor_points(raw, labels, max_fpr: float):
    """
    "오탐률을 max_fpr 이하로 유지하면서 정탐 최대"가 되는 원점수 임계값 t*와,
    변환 후 REAL_MAX_TARGET으로 보낼 기준점(real_ref)을 고른다.

    ## 왜 '오탐 0%'가 아니라 상한을 두는가 (실측으로 바꾼 부분)

    처음엔 오탐 0%를 강제했다. 검증셋이 50건일 때는 잘 맞았는데, 116건으로 늘리자
    무너졌다. 진짜 영상 50개 중 **하나가 원점수 64.28로 튀었기 때문**이다
    (나머지는 22.9 이하). 오탐 0%를 지키려면 임계값을 64.28 위로 올려야 하고,
    거기에 안전 여유까지 곱하면 t*가 192가 돼 점수 범위(0~100) 밖으로 나간다.
    그 결과 정탐이 66.7% -> 57.6%로 오히려 떨어졌다.

    이상치 하나에 전체 설정이 끌려가면 안 된다. 그래서 기준을 **진짜 점수의
    (1 - max_fpr) 분위수**로 바꿨다. max_fpr=0.02면 50개 중 1개까지는 넘어가도
    좋다고 보는 것이고, 이는 우리가 실제로 보고하는 오탐률 해상도(1/50 = 2%)와도 맞는다.
    """
    reals = sorted(s for s, l in zip(raw, labels) if l == 0)
    if not reals:
        return 1.0, 1.0

    # max_fpr 만큼의 진짜는 임계값을 넘어도 좋다고 본다. 50개에 max_fpr=0.02면
    # 1개를 허용하므로 기준점은 **두 번째로 높은** 값이다.
    #
    # 주의: 여기서 분위수를 (1-max_fpr) 위치로 잡으면 사실상 최댓값이 나와서
    # 앵커 두 점(t* -> 50, real_ref -> 20)의 순서가 뒤집힌다. 그러면 변환이
    # 기울기 62짜리 계단 함수가 돼 "0 아니면 100" 문제가 그대로 돌아온다
    # (실제로 한 번 그렇게 나왔다). real_ref는 반드시 t*보다 **아래**여야 한다.
    # 올림(ceil)을 쓴다. 내림이면 교차검증 훈련 폴드(진짜 40개)에서
    # int(40*0.02) = 0 이 돼 이상치 허용이 사라지고, 폴드마다 t*가 튀어
    # 교차검증 수치가 변환 전과 같아진다(실제로 그랬다).
    allowed = math.ceil(len(reals) * max_fpr) if max_fpr > 0 else 0
    real_ref = reals[max(0, len(reals) - 1 - allowed)]

    fakes_above = sorted(s for s, l in zip(raw, labels) if l == 1 and s > real_ref)
    if not fakes_above:
        return max(real_ref * 1.5, real_ref + 1e-6), real_ref

    # real_ref 와 "잡히는 가장 낮은 가짜" 사이 어디를 골라도 이 데이터에서는 성능이
    # 같다. 기하평균을 잡아 양쪽으로 여유를 남긴다 (점수 축이 로그에 가깝다).
    lo = max(real_ref, EPS * 100)
    return math.sqrt(lo * fakes_above[0]), real_ref


def fit_anchor(raw, labels, max_fpr: float):
    """두 점(t*->50, real_ref->REAL_MAX_TARGET)을 지나는 로짓 1차 변환."""
    t_star, real_ref = anchor_points(raw, labels, max_fpr)

    z1, y1 = to_logit(t_star), to_logit(THRESHOLD)
    z2, y2 = to_logit(max(t_star / SLOPE_DIVISOR, EPS * 100)), to_logit(REAL_MAX_TARGET)

    if abs(z1 - z2) < 1e-9:
        return 1.0, 0.0, t_star, real_ref
    a = (y1 - y2) / (z1 - z2)
    b = y1 - a * z1
    return a, b, t_star, real_ref


def fit_platt(raw, labels, max_fpr: float = 0.0):
    """비교용. 로지스틱 회귀로 로그손실 최소화."""
    import numpy as np
    from sklearn.linear_model import LogisticRegression

    X = np.array([to_logit(s) for s in raw], dtype=float).reshape(-1, 1)
    y = np.array(labels, dtype=int)
    model = LogisticRegression(C=10.0, solver="lbfgs", max_iter=1000)
    model.fit(X, y)
    return float(model.coef_[0][0]), float(model.intercept_[0]), None, None


FITTERS = {"anchor": fit_anchor, "platt": fit_platt}


def apply(raw, a, b):
    return [sigmoid(a * to_logit(s) + b) * 100.0 for s in raw]


def metrics(scores, labels, threshold: float = THRESHOLD) -> dict:
    tp = sum(1 for s, l in zip(scores, labels) if l == 1 and s >= threshold)
    fn = sum(1 for s, l in zip(scores, labels) if l == 1 and s < threshold)
    fp = sum(1 for s, l in zip(scores, labels) if l == 0 and s >= threshold)
    tn = sum(1 for s, l in zip(scores, labels) if l == 0 and s < threshold)
    n = tp + fn + fp + tn
    return {
        "recall": round(100.0 * tp / (tp + fn), 1) if tp + fn else 0.0,
        "fpr": round(100.0 * fp / (fp + tn), 1) if fp + tn else 0.0,
        "precision": round(100.0 * tp / (tp + fp), 1) if tp + fp else 0.0,
        "accuracy": round(100.0 * (tp + tn) / n, 1) if n else 0.0,
        "tp": tp, "fn": fn, "fp": fp, "tn": tn,
    }


def show(title: str, m: dict) -> None:
    if title:
        print(f"  {title}")
    print(f"    정탐률(Recall)    {m['recall']:>5.1f}%   가짜 {m['tp']}/{m['tp'] + m['fn']}개 탐지")
    print(f"    오탐률(FPR)       {m['fpr']:>5.1f}%   진짜 {m['fp']}/{m['fp'] + m['tn']}개 오판")
    print(f"    정확도(Accuracy)  {m['accuracy']:>5.1f}%")


def cross_validate(raw, labels, fitter, max_fpr, folds: int):
    """겹마다 파라미터를 새로 적합해 못 본 데이터로만 평가한다."""
    import numpy as np
    from sklearn.model_selection import StratifiedKFold

    X = np.arange(len(raw)).reshape(-1, 1)
    y = np.array(labels)
    out = [0.0] * len(raw)
    skf = StratifiedKFold(n_splits=folds, shuffle=True, random_state=0)
    for tr, te in skf.split(X, y):
        fa, fb, *_ = fitter([raw[i] for i in tr], [labels[i] for i in tr], max_fpr)
        for i in te:
            out[i] = sigmoid(fa * to_logit(raw[i]) + fb) * 100.0
    return out


def main():
    parser = argparse.ArgumentParser(description="딥페이크 점수 재척도 파라미터 적합")
    parser.add_argument("--report", default=str(REPORT), help="validate_detector 결과 JSON")
    parser.add_argument("--method", default="anchor", choices=sorted(FITTERS),
                        help="anchor(기본) | platt(비교용)")
    parser.add_argument("--max-fpr", type=float, default=0.02,
                        help="허용 오탐률 상한. 이 안에서 정탐을 최대로 만드는 "
                             "임계값을 고른다 (이상치 한 건에 끌려가지 않게)")
    parser.add_argument("--folds", type=int, default=5, help="교차검증 겹 수")
    parser.add_argument("--dry-run", action="store_true", help="파일로 저장하지 않음")
    args = parser.parse_args()

    report_path = Path(args.report)
    if not report_path.exists():
        print(f"검증 결과가 없습니다: {report_path}")
        print("  .venv\\Scripts\\python.exe scripts\\validate_detector.py --backend ff")
        return 1

    data = json.loads(report_path.read_text(encoding="utf-8"))
    entry = data.get("backends", {}).get("ff")
    if not entry:
        print("이 리포트에는 ff 백엔드 결과가 없습니다. --backend ff 로 다시 돌리세요.")
        return 1

    rows = [r for r in entry["results"] if r.get("score") is not None]

    # 이미 재척도가 적용된 상태로 만들어진 리포트일 수 있다. 그 점수로 다시 적합하면
    # 변환이 두 번 걸린다. validate_detector.py가 남기는 score_raw를 우선 쓴다.
    if any(r.get("score_raw") is not None for r in rows):
        raw = [float(r["score_raw"] if r.get("score_raw") is not None else r["score"])
               for r in rows]
        print("원점수(score_raw)로 적합합니다 — 이중 적용 방지.\n")
    else:
        raw = [float(r["score"]) for r in rows]
        print("[주의] 리포트에 score_raw가 없습니다. 이 리포트가 재척도 적용 후에\n"
              "       만들어진 것이라면 변환이 이중으로 걸립니다.\n"
              "       data_seed/deepfake_calibration.json 을 지우고\n"
              "       validate_detector.py 를 다시 돌린 뒤 적합하세요.\n")

    labels = [1 if r["label"] == "fake" else 0 for r in rows]

    n_fake = sum(labels)
    real_max = max(s for s, l in zip(raw, labels) if l == 0)
    print(f"입력: {len(rows)}건 (가짜 {n_fake} / 진짜 {len(labels) - n_fake})")
    print(f"원점수  진짜 최고 {real_max:.2f}   "
          f"가짜 중앙 {sorted(s for s, l in zip(raw, labels) if l == 1)[n_fake // 2]:.2f}\n")

    print("--- 변환 전 (원점수, 임계값 50) ---")
    before = metrics(raw, labels)
    show("", before)

    print(f"\n--- 방법 비교 ({args.folds}-겹 교차검증, 임계값 50) ---")
    compare = {}
    for name, fitter in sorted(FITTERS.items()):
        cv = cross_validate(raw, labels, fitter, args.max_fpr, args.folds)
        compare[name] = metrics(cv, labels)
        mark = " ←기본값" if name == args.method else ""
        print(f"\n  [{name}]{mark}")
        show("", compare[name])

    fitter = FITTERS[args.method]
    a, b, t_star, _ = fitter(raw, labels, args.max_fpr)
    cal = apply(raw, a, b)
    after_in = metrics(cal, labels)
    after_cv = compare[args.method]

    print(f"\n--- 최종 파라미터 ({args.method}) ---")
    print(f"  a = {a:.4f}, b = {b:.4f}")
    if t_star is not None:
        print(f"  판정 경계 원점수 t* = {t_star:.2f}  "
              f"(진짜 {(1-args.max_fpr)*100:.0f}분위 {real_max:.2f}의 {t_star / max(real_max, 1e-9):.1f}배)")
    print(f"  변환 예시:  원점수  0 -> {apply([0.0], a, b)[0]:5.1f}    "
          f"1.48 -> {apply([1.48], a, b)[0]:5.1f}    "
          f"30 -> {apply([30.0], a, b)[0]:5.1f}    "
          f"100 -> {apply([100.0], a, b)[0]:5.1f}")

    print("\n--- 요약 (★ 발표에 쓸 수치는 교차검증) ---")
    print(f"  {'':16}{'정탐률':>8}{'오탐률':>8}{'정확도':>8}")
    print(f"  {'변환 전':16}{before['recall']:>7.1f}%{before['fpr']:>7.1f}%{before['accuracy']:>7.1f}%")
    print(f"  {'변환 후(재적합)':16}{after_in['recall']:>7.1f}%{after_in['fpr']:>7.1f}%"
          f"{after_in['accuracy']:>7.1f}%")
    print(f"  {'변환 후(교차검증)':16}{after_cv['recall']:>7.1f}%{after_cv['fpr']:>7.1f}%"
          f"{after_cv['accuracy']:>7.1f}%")

    if args.dry_run:
        print("\n--dry-run: 저장하지 않았습니다.")
        return 0

    OUT.write_text(json.dumps({
        "_설명": "FF++ Xception 원점수를 로짓 공간 1차 변환으로 재척도한다. "
                 "확률이 아니라 '50이 판정 경계'인 위험도 축이다. "
                 "src/media_detection/calibration.py 참고.",
        "a": a,
        "b": b,
        "method": args.method,
        "max_fpr": args.max_fpr,
        "decision_threshold_raw": t_star,
        "real_max_raw": real_max,
        "n_samples": len(rows),
        "fitted_on": report_path.name,
        "face_detector": "yunet",
        "metrics_before": before,
        "metrics_after_in_sample": after_in,
        f"metrics_after_cv{args.folds}": after_cv,
        "metrics_cv_by_method": compare,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT}")
    print("이제 파이프라인이 변환된 점수를 쓴다. 확인:")
    print("  .venv\\Scripts\\python.exe scripts\\validate_detector.py --backend ff")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
