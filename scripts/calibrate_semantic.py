"""
의미 분류기 임계값 보정 진단
담당: 이상원

semantic_classifier.py의 SIM_LOW / SIM_HIGH / BENIGN_MARGIN을 감으로 정하면
정탐과 오탐이 같이 움직인다. 이 스크립트는 **실제 유사도 분포**를 찍어서
어디에 경계를 둘지 근거를 준다.

프로토타입을 늘리거나 임베딩 모델을 바꾸면 분포가 달라지므로 다시 돌릴 것.

실행:
    .venv\\Scripts\\python.exe scripts/calibrate_semantic.py
    .venv\\Scripts\\python.exe scripts/calibrate_semantic.py --show-sentences
"""

import argparse
import json
import sys
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

from content_analysis import semantic_classifier as sem  # noqa: E402

DATASET = PROJECT_ROOT / "data_seed" / "content_test_scenarios.json"


def pct(a, q):
    return float(np.percentile(a, q)) if len(a) else float("nan")


def main():
    ap = argparse.ArgumentParser(description="의미 분류기 유사도 분포 진단")
    ap.add_argument("--dataset", default=str(DATASET))
    ap.add_argument("--show-sentences", action="store_true",
                    help="경계에 걸린 문장을 직접 출력")
    args = ap.parse_args()

    clf = sem.get_shared_classifier()
    clf._ensure_loaded()

    print("프로토타입 규모")
    for cat, texts in clf._cat_texts.items():
        print(f"   {cat:<16} {len(texts):>3}개")
    print(f"   {'benign':<16} {len(clf._benign_texts):>3}개\n")

    data = json.loads(Path(args.dataset).read_text(encoding="utf-8"))
    rows = []
    for s in data["scenarios"]:
        text = "\n".join(f"{t['speaker']}: {t['text']}" for t in s["turns"])
        for sent in sem.split_sentences(text):
            v = clf._model.encode([sent], normalize_embeddings=True)[0]
            best_cat, best_sim = None, -1.0
            for cat, mat in clf._cat_vecs.items():
                if not len(mat):
                    continue
                m = float(np.max(mat @ v))
                if m > best_sim:
                    best_cat, best_sim = cat, m
            ben = float(np.max(clf._benign_vecs @ v)) if clf._benign_vecs is not None else 0.0
            rows.append({
                "label": s["label"], "id": s["id"], "sent": sent,
                "cat": best_cat, "sim": best_sim, "benign": ben,
                "margin": best_sim - ben,
                "disclaimer": clf._is_disclaimer(sent),
            })

    fraud = [r for r in rows if r["label"] == "fraud"]
    normal = [r for r in rows if r["label"] == "normal"]
    print(f"문장 수: 사기 {len(fraud)}, 정상 {len(normal)}\n")

    def dist(name, arr, key):
        vals = np.array([r[key] for r in arr])
        print(f"   {name:<8} p10={pct(vals,10):.3f} p25={pct(vals,25):.3f} "
              f"중앙={pct(vals,50):.3f} p75={pct(vals,75):.3f} p90={pct(vals,90):.3f}")

    print("최고 카테고리 유사도 (sim)")
    dist("사기", fraud, "sim"); dist("정상", normal, "sim")
    print("\n정상 프로토타입 유사도 (benign)")
    dist("사기", fraud, "benign"); dist("정상", normal, "benign")
    print("\n마진 (sim - benign)  ← 이 값이 클수록 위험 쪽")
    dist("사기", fraud, "margin"); dist("정상", normal, "margin")

    print("\n마진 임계값별 문장 분리 성능")
    print(f"   {'마진':>6} {'사기문장 포착':>12} {'정상문장 오탐':>12}")
    for th in [-0.05, 0.0, 0.05, 0.10, 0.15, 0.20, 0.25, 0.30]:
        f_hit = sum(1 for r in fraud if r["margin"] >= th) / max(1, len(fraud))
        n_hit = sum(1 for r in normal if r["margin"] >= th) / max(1, len(normal))
        print(f"   {th:>6.2f} {f_hit:>11.1%} {n_hit:>12.1%}")

    print("\n면책 문장 탐지")
    print(f"   사기 대화 중 {sum(1 for r in fraud if r['disclaimer'])}문장, "
          f"정상 대화 중 {sum(1 for r in normal if r['disclaimer'])}문장")

    if args.show_sentences:
        print("\n--- 정상인데 마진이 높은 문장 (오탐 원인) ---")
        for r in sorted(normal, key=lambda x: -x["margin"])[:12]:
            print(f"   margin={r['margin']:+.3f} sim={r['sim']:.3f} [{r['cat']}] "
                  f"({r['id']}) {r['sent'][:56]}")
        print("\n--- 사기인데 마진이 낮은 문장 (미탐 원인) ---")
        for r in sorted(fraud, key=lambda x: x["margin"])[:8]:
            print(f"   margin={r['margin']:+.3f} sim={r['sim']:.3f} [{r['cat']}] "
                  f"({r['id']}) {r['sent'][:56]}")


if __name__ == "__main__":
    main()
