"""
Fraud Risk Score 통합 스코어링 모듈
담당: 이상원 (BE/미디어 분석)

콘텐츠 위험도(강동연 담당, STT->LLM 8대 사회공학 기법 분류 결과)와
미디어 위험도(AASIST+딥페이크 탐지 결과, 아직 모델 미완성이라 더미값 사용)를
받아 최종 Fraud Risk Score(0~100)를 산출한다.

기획서 두 버전에 서로 다른 통합 공식이 적혀 있어 둘 다 구현해뒀다.

  [버전 A - 원페이지 기획서 PDF]  ScoringStrategy.THRESHOLD_BONUS
    Fraud Risk Score = w1*content + w2*media + cross_bonus
    cross_bonus = +15 if content > 70 and media > 70 else 0

  [버전 B - 상세 기획서 DOCX]    ScoringStrategy.MULTIPLICATIVE_BONUS  ← 기본값
    Fraud Risk Score = w1*content + w2*media + 15*(content/100)*(media/100)
    두 신호가 동시에 높을수록 교차 보너스가 곱연산으로 커지는 구조.
    content=media=100일 때 보너스가 정확히 +15가 되어 버전 A의 상한과 맞아떨어짐.

## 어느 쪽을 쓸지 (실측으로 확정, 2026-08-09)

`scripts/decide_scoring.py`가 실측 점수 격자 1,050쌍으로 두 공식을 비교했다.

  정확도      두 공식이 사실상 동률 (같은 조건에서 69.7% / 69.7%).
              최적 조건까지 풀면 A가 75.3%, B가 72.7%로 A가 2.6%p 앞선다.
  연속성      **여기서 갈렸다.** 입력을 69.9 -> 70.1로 0.2만 움직였을 때
              A는 점수가 +15.20 튀고(계단), B는 +0.24로 매끄럽다.

기획서가 "확률적 표현으로 과신을 방지한다"고 못 박았는데, 임계값 하나 넘었다고
점수가 15점 뛰면 그 원칙과 정면으로 충돌한다. 구간 타임라인 그래프에도 설명할 수
없는 계단이 생긴다. 합성 격자에서 얻은 2.6%p보다 이 성질이 중요하다고 보고
**버전 B를 확정**했다.

## 가중치 (실측으로 확정, 2026-08-09)

같은 스윕에서 콘텐츠 0.65 / 미디어 0.35가 가장 좋았다. 0.5/0.5 대비
사기를 '낮음'으로 놓치는 건수가 **84 -> 21**로 줄고 헛경보는 0으로 유지된다.
화법이 사기면 목소리가 진짜여도 위험하다는 판단이 반영된 결과다.
(주의: 정답 라벨을 그렇게 정의했으므로 이 방향은 부분적으로 설계에 내재한다.
 `scripts/decide_scoring.py` docstring에 가정을 명시해뒀다.)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class ScoringStrategy(str, Enum):
    THRESHOLD_BONUS = "threshold_bonus"   # 버전 A (PDF)
    MULTIPLICATIVE_BONUS = "multiplicative_bonus"  # 버전 B (DOCX, 기본값)


# 실측으로 확정한 기본값 (근거는 모듈 docstring). 여기 한 곳만 바꾸면
# 파이프라인·API·데모 스크립트가 모두 따라온다.
DEFAULT_STRATEGY = ScoringStrategy.MULTIPLICATIVE_BONUS
DEFAULT_CONTENT_WEIGHT = 0.65
DEFAULT_MEDIA_WEIGHT = 1.0 - DEFAULT_CONTENT_WEIGHT


@dataclass
class RiskBreakdown:
    """대시보드에 표시할 근거용 세부 내역."""
    content_risk: float
    media_risk: float
    base_score: float
    cross_bonus: float
    fraud_risk_score: float
    strategy: ScoringStrategy
    media_risk_is_dummy: bool = False
    content_weight: float = 0.5
    media_weight: float = 0.5

    def as_dict(self) -> dict:
        return {
            "content_risk": round(self.content_risk, 2),
            "media_risk": round(self.media_risk, 2),
            "base_score": round(self.base_score, 2),
            "cross_bonus": round(self.cross_bonus, 2),
            "fraud_risk_score": round(self.fraud_risk_score, 2),
            "strategy": self.strategy.value,
            "media_risk_is_dummy": self.media_risk_is_dummy,
        }


def _clamp(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, value))


def compute_fraud_risk_score(
    content_risk: float,
    media_risk: float,
    strategy: ScoringStrategy = DEFAULT_STRATEGY,
    content_weight: float = DEFAULT_CONTENT_WEIGHT,
    media_weight: float = DEFAULT_MEDIA_WEIGHT,
    threshold: float = 70.0,
    threshold_bonus: float = 15.0,
    media_risk_is_dummy: bool = False,
) -> RiskBreakdown:
    """
    콘텐츠/미디어 위험도(각 0~100)를 받아 통합 Fraud Risk Score를 계산한다.

    Args:
        content_risk: 강동연 담당 콘텐츠 분석 엔진 출력 (0~100)
        media_risk: 이상원 담당 미디어 분석 엔진 출력 (0~100). 모델 완성 전에는
                    media_risk_dummy 모듈의 더미값을 사용.
        strategy: 두 기획서 버전 중 어떤 통합 공식을 쓸지 선택.
        media_risk_is_dummy: 더미값 사용 여부를 결과에 함께 표시 (대시보드/로그용).
    """
    if not (0 <= content_risk <= 100):
        raise ValueError(f"content_risk는 0~100 범위여야 합니다: {content_risk}")
    if not (0 <= media_risk <= 100):
        raise ValueError(f"media_risk는 0~100 범위여야 합니다: {media_risk}")

    base_score = content_weight * content_risk + media_weight * media_risk

    if strategy == ScoringStrategy.THRESHOLD_BONUS:
        cross_bonus = threshold_bonus if (content_risk > threshold and media_risk > threshold) else 0.0
    elif strategy == ScoringStrategy.MULTIPLICATIVE_BONUS:
        cross_bonus = threshold_bonus * (content_risk / 100.0) * (media_risk / 100.0)
    else:
        raise ValueError(f"알 수 없는 strategy: {strategy}")

    fraud_risk_score = _clamp(base_score + cross_bonus)

    return RiskBreakdown(
        content_risk=content_risk,
        media_risk=media_risk,
        base_score=base_score,
        cross_bonus=cross_bonus,
        fraud_risk_score=fraud_risk_score,
        strategy=strategy,
        media_risk_is_dummy=media_risk_is_dummy,
        content_weight=content_weight,
        media_weight=media_weight,
    )


def compute_timeline(
    segments: list,
    strategy: ScoringStrategy = DEFAULT_STRATEGY,
    media_risk_is_dummy: bool = False,
) -> list:
    """
    구간별(타임라인) 위험도 리스트를 받아 각 구간의 Fraud Risk Score를 계산한다.

    segments: [{"start": 0.0, "end": 5.0, "content_risk": 20, "media_risk": 15}, ...]
    반환: 각 segment에 breakdown(dict)을 덧붙인 리스트 (대시보드 이중 라인 그래프용)
    """
    results = []
    for seg in segments:
        breakdown = compute_fraud_risk_score(
            content_risk=seg["content_risk"],
            media_risk=seg["media_risk"],
            strategy=strategy,
            media_risk_is_dummy=media_risk_is_dummy,
        )
        results.append({**seg, **breakdown.as_dict()})
    return results


if __name__ == "__main__":
    # 간단한 수동 확인용 예시 (pytest 아님, scripts/demo_fraud_risk_score.py에서 더 자세히 다룸)
    example = compute_fraud_risk_score(content_risk=80, media_risk=75, media_risk_is_dummy=True)
    print(example.as_dict())
