"""
검증 스크립트 공용 지표 (정탐률/오탐률/분리도)
담당: 이상원(원 구현) / 강동연(공용 모듈로 분리)

영상·음성·콘텐츠 세 검증 스크립트가 **같은 지표**를 쓴다. 원래 이 함수들은
validate_detector.py 안에 있었고 나머지가 거기서 import했는데, validate_detector가
모듈 최상단에서 cv2/torch를 끌어오기 때문에 **콘텐츠 검증에까지 미디어 스택이
필요해지는 문제**가 있었다. 지표만 여기로 떼어내서 의존성을 끊는다.

라벨 규약: 'fake'가 탐지 대상(양성), 'real'이 정상(음성).
콘텐츠 쪽은 fraud -> fake, normal -> real 로 매핑해서 넘긴다.
"""


def confusion(results: list, threshold: float) -> dict:
    """
    threshold 이상이면 '가짜로 판정'했다고 보고 혼동행렬을 만든다.

      Recall(정탐률)  = 가짜를 가짜라고 맞힌 비율   -> 놓치면 피해 발생
      FPR(오탐률)     = 진짜를 가짜라고 오해한 비율 -> 높으면 사용자 신뢰 붕괴
    """
    tp = sum(1 for r in results if r["label"] == "fake" and r["score"] >= threshold)
    fn = sum(1 for r in results if r["label"] == "fake" and r["score"] < threshold)
    fp = sum(1 for r in results if r["label"] == "real" and r["score"] >= threshold)
    tn = sum(1 for r in results if r["label"] == "real" and r["score"] < threshold)

    n_fake, n_real = tp + fn, fp + tn
    return {
        "threshold": threshold,
        "tp": tp, "fn": fn, "fp": fp, "tn": tn,
        "recall": tp / n_fake if n_fake else None,
        "fpr": fp / n_real if n_real else None,
        "precision": tp / (tp + fp) if (tp + fp) else None,
        "accuracy": (tp + tn) / len(results) if results else None,
    }


def separation(results: list) -> dict:
    """진짜/가짜 점수 분포가 실제로 갈라지는지 본다. 겹치면 threshold를 어디 둬도 소용없다."""
    real = [r["score"] for r in results if r["label"] == "real"]
    fake = [r["score"] for r in results if r["label"] == "fake"]
    if not real or not fake:
        return {}
    return {
        "real_max": max(real), "real_mean": sum(real) / len(real), "real_min": min(real),
        "fake_max": max(fake), "fake_mean": sum(fake) / len(fake), "fake_min": min(fake),
        "gap": min(fake) - max(real),  # 양수면 완전 분리
    }


def best_threshold(results: list) -> dict:
    """샘플 점수들 사이 지점을 모두 훑어 정확도가 가장 높은 threshold를 찾는다."""
    scores = sorted({r["score"] for r in results})
    if len(scores) < 2:
        return confusion(results, 50.0)
    candidates = [(scores[i] + scores[i + 1]) / 2 for i in range(len(scores) - 1)]
    candidates += [0.0, 100.0]
    return max((confusion(results, t) for t in candidates),
               key=lambda c: (c["accuracy"] or 0, -(c["fpr"] or 1)))


def fmt_pct(v) -> str:
    return "   n/a" if v is None else f"{v:6.1%}"
