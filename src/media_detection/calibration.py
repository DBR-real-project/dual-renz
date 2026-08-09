"""
딥페이크 점수 보정 (Platt scaling)
담당: 이상원

## 왜 필요한가

FF++ Xception의 원점수는 **0 아니면 100에 몰린다.** 검증 50건 실측:

    진짜 20개: 0.0 0.0 0.0 ... 1.01 1.40 1.48      (최대 1.48)
    가짜 30개: 0.0 ... 1.71 10.32 26.29 ... 100.0  (24개가 94 이상)

이게 두 가지 문제를 만들었다.

1. **임계값을 못 정한다.** 50으로 자르면 정탐 63.3%, 오탐 0%.
   1.6으로 자르면 정탐 80%, 오탐 0%. 후자가 낫지만 진짜 최대(1.48)와 0.12밖에
   차이가 안 나 조금만 환경이 달라져도 무너진다. 그래서 기획서 검토 때
   "50 vs 7.5"를 팀 결정 사항으로 남겨뒀었다.
2. **"위험 가능성 약 OO%"로 쓸 수가 없다.** 원점수 100은 "확률 100%"가 아니라
   그냥 소프트맥스가 포화된 값이다. 확정 판정을 피하겠다는 기획서 원칙과 어긋난다.

## 어떻게 고쳤나

로짓 공간에서 1차 변환을 하는 **Platt scaling**이다.

    z          = logit(원점수 / 100)
    보정점수    = sigmoid(a·z + b) × 100

파라미터는 a, b 두 개뿐이라 50건으로 적합해도 과적합 위험이 작다.
그래도 같은 데이터로 적합하고 같은 데이터로 성능을 보고하면 부풀려지므로,
`scripts/calibrate_deepfake.py`는 **5-겹 교차검증**으로 정직한 수치를 따로 낸다.

보정 후에는 임계값 50이 곧 최적 지점이 되도록 a, b가 맞춰진다. 즉
**"임계값을 얼마로 할까"라는 결정 자체가 없어진다** — 코드 전체가 50 하나만 쓴다.

## 안 한 것

음성(AASIST)은 보정하지 않는다. 실측 분포가 이미 깨끗해서(진짜 최대 2.22,
가짜 30개 중 29개가 95 이상) 임계값 50이 그대로 최적이고, 손대면 오히려
멀쩡한 분리를 흐린다. 근거 없이 일관성만 맞추려고 건드리지 않았다.
"""

import json
import math
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PARAMS_PATH = PROJECT_ROOT / "data_seed" / "deepfake_calibration.json"

# 로짓 계산에서 0과 1을 피하기 위한 클리핑. 원점수가 정확히 0/100으로 나오는
# 경우가 흔해서 없으면 무한대가 된다.
_EPS = 1e-4

_params: Optional[dict] = None
_loaded = False


def _load() -> Optional[dict]:
    global _params, _loaded
    if _loaded:
        return _params
    _loaded = True
    if not PARAMS_PATH.exists():
        _params = None
        return None
    try:
        data = json.loads(PARAMS_PATH.read_text(encoding="utf-8"))
        if "a" in data and "b" in data:
            _params = data
        else:
            _params = None
    except (json.JSONDecodeError, OSError):
        # 파라미터 파일이 깨졌다고 탐지를 멈출 이유는 없다. 원점수로 돌아간다.
        _params = None
    return _params


def is_calibrated() -> bool:
    return _load() is not None


def params() -> Optional[dict]:
    return _load()


def calibrate(raw_score: float) -> float:
    """
    원점수(0~100)를 보정 점수(0~100)로. 파라미터가 없으면 원점수를 그대로 돌려준다.

    단조 증가 변환이라 순위는 바뀌지 않는다. 즉 보정이 정탐/오탐의 *순서*를
    바꾸지는 못하고, **임계값 50이 의미를 갖도록 축을 옮기는 것**이 목적이다.
    """
    p = _load()
    if p is None:
        return raw_score

    x = min(max(raw_score / 100.0, _EPS), 1.0 - _EPS)
    z = math.log(x / (1.0 - x))
    y = 1.0 / (1.0 + math.exp(-(p["a"] * z + p["b"])))
    return y * 100.0


def describe() -> str:
    """리포트에 남길 한 줄 설명. 확률이 아니라는 점을 여기서도 분명히 한다."""
    p = _load()
    if p is None:
        return "재척도 안 함 (원점수 그대로)"
    method = {"anchor": "앵커 방식", "platt": "Platt scaling"}.get(
        p.get("method", "anchor"), p.get("method"))
    t = p.get("decision_threshold_raw")
    boundary = f", 판정 경계 원점수 {t:.2f}" if isinstance(t, (int, float)) else ""
    return (f"로짓 1차 변환 · {method} (a={p['a']:.3f}, b={p['b']:.3f}"
            f"{boundary}, {p.get('n_samples', '?')}건으로 적합) — 확률이 아니라 위험도 축")
