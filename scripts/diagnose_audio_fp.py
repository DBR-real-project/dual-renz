"""
음성 엔진이 실제 통화 녹취를 '합성'으로 오판하는 원인 추적
담당: 이상원

## 무엇을 쫓는가

`scripts/validate_real_calls.py`에서 금감원 공개 실제 녹취 5건이 전부
미디어 위험도 100(합성)으로 나왔다. **사람이 직접 말한 통화인데도.**
ASVspoof 검증에서는 오탐 0%인 엔진이라 앞뒤가 맞지 않는다.

이 스크립트는 진짜 사람 음성(`real_normal_call.wav`, Zeroth-Korean)에
의심되는 처리를 하나씩 걸어 **어느 처리가 점수를 100 쪽으로 밀어내는지** 찾는다.
통제 실험이므로 원본과 비교해서만 의미가 있다.

## 결론 (2026-08-18, 규명 완료)

**원인은 코덱도 편집도 아니라 "학습 도메인 밖"이다. 입력 음량이 방아쇠 역할을 한다.**

기각된 가설:

| 가설 | 결과 |
|---|---|
| 전화망 대역 제한 (300~3400Hz) | 기각 — 오히려 내려간다 |
| G.711 / G.726 (파형 코덱) | 기각 — 5~7점 |
| AMR-NB, GSM, Speex (CELP 보코더) | 기각 — 6~14점 |
| mp3 재인코딩 세대 손실 | 기각 — 12~15점 |
| 잡음 제거 / 마스킹 삐- 삽입 | 기각 — 5~8점 |

확인된 원인 — 같은 RMS로 맞추고 비교하면 명확하다(`--level-sweep`):

| 그룹 | -39dB | -30dB | -25dB | -20dB |
|---|---|---|---|---|
| ASVspoof 진짜 (**학습 도메인**) | 0.8 | 0.4 | 0.2 | **0.3** |
| Zeroth 낭독 (도메인 밖) | 16.9 | 81.1 | 98.9 | **100.0** |
| 실제 통화 녹취 (도메인 밖) | 45.4 | 98.1 | 100.0 | **100.0** |

읽는 법:
- **학습 도메인 안(ASVspoof)에서는 레벨을 어떻게 바꿔도 0점대**로 안정적이다.
- **도메인 밖 진짜 음성은 레벨이 올라갈수록 합성으로 판정된다.**
- 실제 통화 녹취의 RMS는 -17.7~-24.1dB — 정확히 100이 나오는 구간이다.

즉 영상 엔진(DFDC 오탐 55%)과 **같은 종류의 문제**다. 벤치마크 안에서만 동작한다.

> ⚠ **레벨 정규화로 "고치면" 안 된다.** 조용한 입력을 100으로 밀어 올릴 뿐이다.
> 지금 우리 초록 시연 샘플이 낮은 점수인 것도 **조용해서**(-39dB)이지
> "진짜 목소리라서"가 아니다. 이 사실을 발표에서 숨기지 말 것.

## ⚠ 이 스크립트가 detector를 직접 부르는 이유

`media_risk.get_audio_spoof_score()`는 실패하면 **조용히 더미값으로 폴백**한다.
그래서 파일 변환이 실패했는데도 그럴듯한 숫자(47.2)가 나와 오독할 뻔했다.
여기서는 detector를 직접 부르고 실패를 예외로 드러낸다.

실행:
    .venv\\Scripts\\python.exe scripts/diagnose_audio_fp.py
    .venv\\Scripts\\python.exe scripts/diagnose_audio_fp.py --only 재인코딩
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))
sys.path.insert(0, str(SCRIPT_DIR.parent / "src"))

from _console import setup_console  # noqa: E402

setup_console()  # cp949 콘솔에서 유니코드 출력 중 죽는 문제 방지 (_console.py)

PROJECT_ROOT = SCRIPT_DIR.parent
CLEAN = PROJECT_ROOT / "data" / "korean_calls" / "real_normal_call.wav"
REAL_DIR = PROJECT_ROOT / "data" / "real_calls"
REPORT = PROJECT_ROOT / "docs" / "audio_fp_diagnosis.json"

TEL = "highpass=f=300,lowpass=f=3400"   # 전화망 대역


def ff(*args) -> None:
    """ffmpeg 실행. 실패하면 예외를 던진다 — 조용한 폴백을 만들지 않기 위해서."""
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패: {proc.stderr.strip()[:200]}")


def to16k(src: Path, dst: Path) -> Path:
    ff("-i", str(src), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(dst))
    if not dst.exists() or dst.stat().st_size < 1000:
        raise RuntimeError(f"변환 결과가 비었습니다: {dst.name}")
    return dst


def score(path: Path) -> float:
    """detector를 직접 부른다(더미 폴백 없음)."""
    from media_detection.audio_spoof_detector import get_shared_detector
    from media_detection.deepfake_detector import FrameAggregation

    r = get_shared_detector().score_audio(
        str(path), aggregation=FrameAggregation.TOPK_MEAN, max_windows=8)
    return r.spoof_score


# ---------------------------------------------------------------- 실험 정의
def exp_codec(tmp: Path, name: str, enc: list, ext: str) -> Path:
    mid = tmp / f"c_{name}{ext}"
    # wav 컨테이너에 넣을 때는 -f wav 를 명시해야 한다. 안 하면 확장자만 보고
    # 컨테이너를 고르다 "Codec not supported in WAVE format"으로 죽는다.
    extra = ["-f", "wav"] if ext == ".wav" else []
    ff("-i", str(CLEAN), "-af", TEL, "-ar", "8000", "-ac", "1", *enc, *extra, str(mid))
    return to16k(mid, tmp / f"c_{name}.wav")


def exp_regen(tmp: Path, generations: int, kbps: int) -> Path:
    """mp3 재인코딩을 여러 세대 반복한다. 배포 전 편집·재인코딩을 흉내 낸다."""
    cur = CLEAN
    for g in range(generations):
        m = tmp / f"g{g}_{kbps}.mp3"
        ff("-i", str(cur), "-ar", "16000", "-ac", "1",
           "-c:a", "libmp3lame", "-b:a", f"{kbps}k", str(m))
        w = tmp / f"g{g}_{kbps}.wav"
        cur = to16k(m, w)
    return cur


def exp_mix(tmp: Path, ratio: float) -> Path:
    """
    두 화자가 한 트랙에 섞인 상황. 실제 통화 녹음은 내 목소리와 상대 목소리를
    서로 다른 경로로 받아 하나로 믹싱한다. 그 상황을 흉내 낸다.
    (자기 자신을 지연시켜 섞으면 서로 다른 화자 두 명에 가깝다)

    ⚠ 이 케이스는 끝에서 피크 정규화를 하므로 **레벨 상승이 섞여 있다.**
    결과가 100으로 나오는 건 혼합 때문이 아니라 커진 음량 때문이다
    (레벨을 고정한 비교는 level_sweep()). 혼합 자체의 효과를 보려면
    정규화를 빼고 다시 재야 한다.
    """
    import numpy as np
    import soundfile as sf

    d, sr = sf.read(str(CLEAN))
    d = d if d.ndim == 1 else d.mean(axis=1)
    shifted = np.roll(d, sr * 7)          # 7초 밀어 다른 발화가 겹치게
    mixed = (1 - ratio) * d + ratio * shifted
    mixed = mixed / (np.abs(mixed).max() + 1e-9) * 0.9
    out = tmp / f"mix_{int(ratio*100)}.wav"
    sf.write(str(out), mixed.astype("float32"), sr, subtype="PCM_16")
    return out


def exp_agc(tmp: Path) -> Path:
    """
    통화 녹음 앱의 자동이득조절(AGC)/컴프레서를 흉내 낸다.

    ⚠ loudnorm이 포함돼 **레벨이 크게 올라간다.** 100이 나오는 건 AGC 때문이 아니라
    음량 때문이다 — 컴프레서만 걸고 레벨을 유지하면 15.3으로 원본과 같다.
    """
    out = tmp / "agc.wav"
    ff("-i", str(CLEAN), "-af",
       f"{TEL},acompressor=threshold=-24dB:ratio=9:attack=5:release=120,loudnorm=I=-16",
       "-ar", "16000", "-ac", "1", str(out))
    return out


def exp_denoise(tmp: Path) -> Path:
    """배포 전 잡음 제거를 흉내 낸다(마스킹 편집 시 흔히 함께 적용된다)."""
    out = tmp / "denoise.wav"
    ff("-i", str(CLEAN), "-af", f"{TEL},afftdn=nf=-25",
       "-ar", "16000", "-ac", "1", str(out))
    return out


def exp_beep(tmp: Path) -> Path:
    """개인정보 마스킹 삐- 소리를 중간중간 삽입한다."""
    out = tmp / "beep.wav"
    # 1kHz 톤을 4초마다 0.4초씩 덮어씌운다
    ff("-i", str(CLEAN), "-f", "lavfi", "-i", "sine=frequency=1000:sample_rate=16000",
       "-filter_complex",
       "[1:a]volume=0.5,atrim=0:0.4,apad=whole_dur=4,aloop=loop=20:size=64000[b];"
       "[0:a][b]amix=inputs=2:duration=first:weights=1 0.9[o]",
       "-map", "[o]", "-ar", "16000", "-ac", "1", str(out))
    return out


def build_cases(tmp: Path, only: str):
    cases = [
        ("기준", "원본 (16kHz 스튜디오)", lambda: CLEAN),
        ("코덱", "G.711 μ-law (파형)",
         lambda: exp_codec(tmp, "g711", ["-c:a", "pcm_mulaw"], ".wav")),
        ("코덱", "G.726 ADPCM 32k (파형)",
         lambda: exp_codec(tmp, "g726", ["-c:a", "adpcm_g726", "-b:a", "32k"], ".wav")),
        ("코덱", "AMR-NB 12.2k (ACELP 보코더)",
         lambda: exp_codec(tmp, "amr122", ["-c:a", "libopencore_amrnb", "-b:a", "12.2k"], ".amr")),
        ("코덱", "AMR-NB 4.75k (최저 비트레이트)",
         lambda: exp_codec(tmp, "amr475", ["-c:a", "libopencore_amrnb", "-b:a", "4.75k"], ".amr")),
        ("코덱", "GSM 06.10 (RPE-LTP 보코더)",
         lambda: exp_codec(tmp, "gsm", ["-c:a", "libgsm"], ".gsm")),
        ("코덱", "Speex (CELP 보코더)",
         lambda: exp_codec(tmp, "speex", ["-c:a", "libspeex"], ".ogg")),
        ("재인코딩", "mp3 64k 1세대", lambda: exp_regen(tmp, 1, 64)),
        ("재인코딩", "mp3 64k 3세대", lambda: exp_regen(tmp, 3, 64)),
        ("재인코딩", "mp3 32k 3세대", lambda: exp_regen(tmp, 3, 32)),
        ("편집", "화자 혼합 30%", lambda: exp_mix(tmp, 0.30)),
        ("편집", "화자 혼합 50%", lambda: exp_mix(tmp, 0.50)),
        ("편집", "AGC + 라우드니스 정규화", lambda: exp_agc(tmp)),
        ("편집", "잡음 제거(afftdn)", lambda: exp_denoise(tmp)),
        ("편집", "마스킹 삐- 삽입", lambda: exp_beep(tmp)),
    ]
    return [c for c in cases if not only or only in c[0] or only in c[1]]


def level_sweep():
    """
    레벨을 동일하게 맞춘 뒤 그룹별로 비교한다.

    이게 원인을 확정한 실험이다. 음량을 통일하면 "조용해서 낮다"는 요인이 사라지고
    **데이터 도메인 차이만** 남는다. 학습 도메인(ASVspoof)만 안정적으로 낮게 나온다.
    """
    import numpy as np
    import soundfile as sf

    def rms_db(x):
        return 20 * np.log10(np.sqrt((x ** 2).mean()) + 1e-12)

    def rescale(src, target_db, dst):
        d, sr = sf.read(str(src))
        d = d if d.ndim == 1 else d.mean(axis=1)
        d = np.clip(d * (10 ** ((target_db - rms_db(d)) / 20)), -0.99, 0.99)
        sf.write(str(dst), d.astype("float32"), sr, subtype="PCM_16")
        return dst

    levels = [-39, -30, -25, -20]
    groups = [
        ("ASVspoof 진짜 (학습 도메인)",
         sorted((PROJECT_ROOT / "data" / "asvspoof_samples").glob("bonafide__*"))[:5]),
        ("Zeroth 낭독 (도메인 밖)", [CLEAN]),
        ("실제 통화 녹취 (도메인 밖)", sorted(REAL_DIR.glob("*.wav"))[:4]),
    ]
    print("\n=== 레벨 스윕 — 음량을 통일하면 무엇이 남는가 ===\n")
    print(f"{'그룹':<28}" + "".join(f"{lv}dB".rjust(9) for lv in levels))
    print("-" * 64)
    rows = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for name, files in groups:
            if not files:
                print(f"{name:<28}  (샘플 없음)")
                continue
            vals = []
            for lv in levels:
                got = [score(rescale(p, lv, tmp / f"{lv}_{i}_{p.stem[:10]}.wav"))
                       for i, p in enumerate(files)]
                vals.append(round(float(sum(got) / len(got)), 1))
            print(f"{name:<28}" + "".join(f"{v:>9.1f}" for v in vals))
            rows.append({"group": name, "n": len(files),
                         "levels_db": levels, "scores": vals})
    print("\n  학습 도메인 안에서는 레벨과 무관하게 낮다. 도메인 밖은 레벨을 올리면 100이 된다.")
    print("  → 레벨은 방아쇠일 뿐이고, 근본 원인은 도메인 일반화 실패다.")
    return rows


def main():
    parser = argparse.ArgumentParser(description="음성 오탐 원인 추적")
    parser.add_argument("--only", default="", help="특정 그룹/이름만 (예: 재인코딩)")
    parser.add_argument("--level-sweep", action="store_true",
                        help="레벨 통일 비교만 실행 (원인을 확정한 실험)")
    args = parser.parse_args()

    if args.level_sweep:
        import json
        rows = level_sweep()
        REPORT.write_text(json.dumps(
            {"_설명": "레벨을 통일해 그룹별로 비교한 결과. 도메인 일반화 실패를 보인다.",
             "level_sweep": rows}, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n상세 저장: {REPORT}")
        return 0

    if not CLEAN.exists():
        print(f"기준 음성이 없습니다: {CLEAN}")
        print("  .venv\\Scripts\\python.exe scripts\\fetch_korean_speech_samples.py")
        return 1
    if shutil.which("ffmpeg") is None:
        print("ffmpeg이 필요합니다.")
        return 1

    print("음성 엔진 오탐 원인 추적 — 진짜 사람 음성에 처리를 하나씩 걸어 본다")
    print(f"기준 파일: {CLEAN.name}\n")

    rows = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        base = None
        print(f"{'그룹':<8}{'처리':<34}{'AASIST':>8}{'기준대비':>10}")
        print("-" * 62)
        for group, label, make in build_cases(tmp, args.only):
            try:
                s = score(make())
            except Exception as exc:
                print(f"{group:<8}{label:<34}{'실패':>8}  {type(exc).__name__}: {str(exc)[:40]}")
                rows.append({"group": group, "case": label, "score": None,
                             "error": f"{type(exc).__name__}: {exc}"})
                continue
            if base is None:
                base = s
            delta = s - base
            mark = "  ←  올라감" if delta > 20 else ""
            print(f"{group:<8}{label:<34}{s:>8.1f}{delta:>+10.1f}{mark}")
            rows.append({"group": group, "case": label, "score": round(s, 2),
                         "delta": round(delta, 2)})

    # 실제 녹취 참고값
    real = sorted(REAL_DIR.glob("*.wav"))
    if real:
        print("-" * 62)
        for p in real[:3]:
            try:
                print(f"{'실측':<8}{p.name[:34]:<34}{score(p):>8.1f}")
            except Exception as exc:
                print(f"{'실측':<8}{p.name[:34]:<34}{'실패':>8}  {type(exc).__name__}")

    ok = [r for r in rows if r.get("score") is not None]
    risen = [r for r in ok if r.get("delta", 0) > 20]
    print("\n--- 판정 ---")
    if risen:
        print("  점수를 크게 올린 처리:")
        for r in risen:
            print(f"    {r['case']}  ({r['score']:.1f}, {r['delta']:+.1f})")
    else:
        print("  **어떤 처리도 점수를 크게 올리지 못했다.**")
        print("  실제 녹취의 100은 여기서 재현한 어떤 단일 처리로도 설명되지 않는다.")
        print("  → 여러 요인이 겹쳤거나, 아직 재현하지 못한 요인이 있다.")

    sweep = level_sweep()

    import json
    REPORT.write_text(json.dumps({
        "_설명": "진짜 사람 음성에 처리를 하나씩 걸어 AASIST 점수 변화를 본 통제 실험 + "
                 "레벨을 통일한 그룹 비교. 결론은 '학습 도메인 밖에서 무너진다'.",
        "baseline_file": str(CLEAN),
        "rows": rows,
        "level_sweep": sweep,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n상세 저장: {REPORT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
