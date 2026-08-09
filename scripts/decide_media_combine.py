"""
오디오·영상 점수 결합 방식을 실측으로 결정한다 (max vs 가중평균)
담당: 이상원

README "팀이 정해야 할 것" 3번. 화상통화는 음성 스푸핑 점수와 영상 딥페이크 점수가
둘 다 나오는데, 이걸 하나의 media_risk로 어떻게 합칠지의 문제다.

  max()          : 둘 중 하나라도 위조면 그 값을 그대로 쓴다 (현재 기본값)
  weighted_average: 두 점수의 가중평균

가중평균을 쓰자는 근거는 "영상만 위조와 둘 다 위조가 똑같이 100이 되는 게 이상하다"
였다. 맞는 지적이지만, 그 대가가 무엇인지는 재보지 않았다. 이 스크립트가 그걸 잰다.

data/demo_clips/ 의 4조합(진짜+진짜 / 영상만 위조 / 음성만 위조 / 둘 다)을 쓴다.
없으면 먼저: .venv\\Scripts\\python.exe scripts/make_demo_clips.py

실행:
    .venv\\Scripts\\python.exe scripts/decide_media_combine.py
"""

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "src"))

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

from media_detection.media_risk import get_media_risk  # noqa: E402
from media_detection.media_risk_dummy import MediaCombineMode  # noqa: E402
from orchestration.pipeline import LEVEL_THRESHOLDS  # noqa: E402
from scoring.fraud_risk_score import (  # noqa: E402
    DEFAULT_CONTENT_WEIGHT,
    DEFAULT_STRATEGY,
    compute_fraud_risk_score,
)

PROJECT_ROOT = SCRIPT_DIR.parent
CLIP_DIR = PROJECT_ROOT / "data" / "demo_clips"
OUT = PROJECT_ROOT / "docs" / "media_combine_decision.json"

CLIPS = [
    ("both_real.mp4", "진짜 영상 + 진짜 음성", False),
    ("video_fake.mp4", "위조 영상 + 진짜 음성", True),
    ("audio_fake.mp4", "진짜 영상 + 합성 음성", True),
    ("both_fake.mp4", "위조 영상 + 합성 음성", True),
]

# 정상 대화(콘텐츠 0)와 함께 들어왔을 때를 본다. 화법이 사기면 어차피 높음이 되므로
# 결합 방식의 차이가 드러나지 않는다. **매체만으로 잡아야 하는 상황**이 관건이다.
CONTENT_RISK = 0.0


def level_of(score: float) -> str:
    if score >= LEVEL_THRESHOLDS["높음"]:
        return "높음"
    if score >= LEVEL_THRESHOLDS["중간"]:
        return "중간"
    return "낮음"


def main():
    parser = argparse.ArgumentParser(description="오디오·영상 결합 방식 실측 결정")
    parser.add_argument("--clip-dir", default=str(CLIP_DIR))
    args = parser.parse_args()

    clip_dir = Path(args.clip_dir)
    missing = [n for n, _, _ in CLIPS if not (clip_dir / n).exists()]
    if missing:
        print(f"클립이 없습니다: {missing}")
        print("  .venv\\Scripts\\python.exe scripts\\make_demo_clips.py")
        return 1

    print(f"콘텐츠 위험도 {CONTENT_RISK:.0f} (정상 대화)로 고정하고 매체만 본다.")
    print(f"신호등 경계: 높음 {LEVEL_THRESHOLDS['높음']:.0f} / "
          f"중간 {LEVEL_THRESHOLDS['중간']:.0f}\n")

    rows = []
    print(f"{'클립':<22}{'음성':>7}{'영상':>7}   "
          f"{'max':>7}{'등급':>6}   {'가중평균':>9}{'등급':>6}")
    print("-" * 72)

    for name, desc, should_warn in CLIPS:
        path = str(clip_dir / name)
        by_mode = {}
        for mode in (MediaCombineMode.MAX, MediaCombineMode.WEIGHTED_AVERAGE):
            r = get_media_risk(video_path=path, mode=mode)
            frs = compute_fraud_risk_score(
                CONTENT_RISK, r["media_risk"],
                strategy=DEFAULT_STRATEGY,
                content_weight=DEFAULT_CONTENT_WEIGHT,
                media_weight=1.0 - DEFAULT_CONTENT_WEIGHT,
            ).fraud_risk_score
            by_mode[mode.value] = {
                "media_risk": r["media_risk"],
                "audio": r["audio_spoof_score"],
                "video": r["deepfake_score"],
                "frs": round(frs, 1),
                "level": level_of(frs),
            }

        mx = by_mode[MediaCombineMode.MAX.value]
        wa = by_mode[MediaCombineMode.WEIGHTED_AVERAGE.value]
        print(f"{desc:<22}{mx['audio']:>7.1f}{mx['video']:>7.1f}   "
              f"{mx['frs']:>7.1f}{mx['level']:>6}   {wa['frs']:>9.1f}{wa['level']:>6}")
        rows.append({"clip": name, "desc": desc, "should_warn": should_warn,
                     "modes": by_mode})

    # 경고해야 하는데 '낮음'으로 떨어진 건수 = 놓침
    print("\n--- 판정 ---")
    verdicts = {}
    for mode in (MediaCombineMode.MAX, MediaCombineMode.WEIGHTED_AVERAGE):
        missed = [r["desc"] for r in rows
                  if r["should_warn"] and r["modes"][mode.value]["level"] == "낮음"]
        false_alarm = [r["desc"] for r in rows
                       if not r["should_warn"] and r["modes"][mode.value]["level"] != "낮음"]
        verdicts[mode.value] = {"missed": missed, "false_alarm": false_alarm}
        print(f"  {mode.value:<18} 놓침 {len(missed)}건  헛경보 {len(false_alarm)}건"
              + (f"   놓친 것: {', '.join(missed)}" if missed else ""))

    print("\n결론:")
    mx_missed = len(verdicts[MediaCombineMode.MAX.value]["missed"])
    wa_missed = len(verdicts[MediaCombineMode.WEIGHTED_AVERAGE.value]["missed"])
    if mx_missed < wa_missed:
        print("  max() 유지. 가중평균은 한쪽 채널만 위조된 경우 점수를 절반으로 깎아")
        print("  경고를 놓친다. '영상만 위조와 둘 다 위조가 같은 100'이라는 지적은 맞지만,")
        print("  사용자 행동(끊을지 말지)은 어차피 같으므로 구분 실익이 없다.")
    elif wa_missed < mx_missed:
        print("  가중평균이 낫다. 결과에 따라 기본값을 바꿀 것.")
    else:
        print("  이 4조합만으로는 우열이 갈리지 않는다. 표본을 늘려 다시 볼 것.")

    OUT.write_text(json.dumps({
        "_설명": "오디오·영상 결합 방식 비교. scripts/decide_media_combine.py 참고.",
        "content_risk": CONTENT_RISK,
        "level_thresholds": LEVEL_THRESHOLDS,
        "rows": rows,
        "verdicts": verdicts,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n저장: {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
