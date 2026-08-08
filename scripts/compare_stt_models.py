"""
faster-whisper 모델 크기 비교 (tiny/base/small) + 배경소음 강건성
담당: 강동연

stt.py는 "small이 타협점"이라고 주석으로만 적혀 있고 실제 비교 수치가 없었다.
이 스크립트가 그 공백을 메운다: 같은 문장을 세 모델로 각각 돌려서 문자 오류율(CER)과
처리 시간을 재고, 배경소음을 섞은 버전에서 성능이 얼마나 떨어지는지도 같이 잰다.

방법:
  1. data_seed/content_test_scenarios.json에서 실제 시나리오 문장 8개를 골라
     Windows TTS(Heami, ko-KR)로 16kHz 모노 음성을 합성한다.
     → 정답 텍스트가 100% 정확하다(TTS가 읽은 그 문장이 곧 ground truth).
       사람 녹음이면 받아쓰기를 직접 해야 하는데, TTS는 그럴 필요가 없다.
  2. 각 음성에 화이트 노이즈를 섞어 SNR 15dB/5dB 버전을 추가로 만든다.
     ⚠ 진짜 배경소음(카페·거리·전화망 잡음)이 아니라 합성 화이트 노이즈다.
     "노이즈가 있으면 성능이 떨어지는가"의 대략적인 하한선 정도로만 볼 것.
  3. tiny/base/small 세 모델로 전부 돌려서 문자 오류율(CER)과 처리 시간을 비교한다.

CER(Character Error Rate)을 쓰는 이유:
  한국어는 STT 결과의 띄어쓰기가 원문과 다르게 나오는 경우가 많다(공백 위치는
  모델마다 다르게 추론하지만 의미는 같음). WER(단어 단위)는 이 흔들림에 과하게
  민감해서, 공백을 제거하고 글자 단위로 비교하는 CER을 쓴다.
  (content_risk.py의 _normalize()가 같은 이유로 공백을 무시하는 것과 같은 논리)

의존성:
  Windows SAPI(Heami 한국어 음성) — Windows 내장이라 별도 설치 불필요.
  ffmpeg 불필요 — SpeechAudioFormatInfo로 16kHz 모노를 직접 합성한다
  (make_korean_call_samples.py는 SSML+ffmpeg 변환을 쓰지만, 여기서는 단문이라
   SSML 없이 바로 16kHz로 뽑아서 ffmpeg 의존성을 없앴다).

실행:
    .venv\\Scripts\\python.exe scripts\\compare_stt_models.py
    .venv\\Scripts\\python.exe scripts\\compare_stt_models.py --models tiny,base,small
"""

import argparse
import json
import subprocess
import sys
import tempfile
import time
import wave
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCENARIOS_PATH = PROJECT_ROOT / "data_seed" / "content_test_scenarios.json"
OUT_DIR = PROJECT_ROOT / "data" / "stt_bench"
REPORT_PATH = PROJECT_ROOT / "docs" / "stt_benchmark_report.json"

# (scenario_id, turn_index) — 어휘가 다양하도록 일부러 골랐다:
# 숫자·전문용어(fraud_01/05/08/09), 외래어·구어체(normal_02), 격식체 안내(normal_01/05).
UTTERANCE_REFS = [
    ("fraud_01", 4),  # "군 소속으로는... 통관업체 계좌로 보내줄 수 있어?" (장문, 전문용어)
    ("fraud_03", 4),  # "응, 지금 아니면 진짜 늦어. 200만 원만..." (숫자)
    ("fraud_05", 2),  # "수술을 받아야 하는데... 골든타임을 놓칠 수도" (의료용어)
    ("fraud_08", 0),  # "로맨스스캠 피해자 지원 변호사입니다..." (전문직 용어)
    ("fraud_09", 2),  # "본인인증 단계에서 오는 인증번호랑..." (IT 용어)
    ("normal_01", 0),  # "OO택배입니다. 오늘 오후 3시경..." (숫자, 격식체)
    ("normal_02", 0),  # "새로 생긴 파스타집 가보고 싶어" (외래어, 구어체)
    ("normal_05", 2),  # "계좌번호나 비밀번호를 절대 요구하지 않으니..." (긴 격식체)
]

SNR_LEVELS_DB = [None, 15.0, 5.0]  # None = 깨끗한 원본


def load_utterances() -> list:
    data = json.loads(SCENARIOS_PATH.read_text(encoding="utf-8"))
    by_id = {s["id"]: s for s in data["scenarios"]}
    out = []
    for sid, idx in UTTERANCE_REFS:
        turn = by_id[sid]["turns"][idx]
        out.append({"id": f"{sid}_t{idx}", "text": turn["text"]})
    return out


def synthesize_16k(text: str, out_wav: Path, voice_hint: str = "Heami") -> None:
    """Windows SAPI로 16kHz 모노를 직접 합성한다 (ffmpeg 불필요)."""
    with tempfile.TemporaryDirectory() as tmp:
        txt_path = Path(tmp) / "line.txt"
        txt_path.write_text(text, encoding="utf-8")

        ps = f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $s.GetInstalledVoices() | Where-Object {{ $_.VoiceInfo.Name -like '*{voice_hint}*' }} | Select-Object -First 1
if (-not $voice) {{ throw '한국어 음성({voice_hint})을 찾지 못했습니다' }}
$s.SelectVoice($voice.VoiceInfo.Name)
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile('{out_wav.as_posix()}', $fmt)
$text = [System.IO.File]::ReadAllText('{txt_path.as_posix()}', [System.Text.Encoding]::UTF8)
$s.Speak($text)
$s.SetOutputToNull()
$s.Dispose()
"""
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True,
        )
        if proc.returncode != 0 or not out_wav.exists():
            raise RuntimeError(f"TTS 실패: {proc.stderr.strip()[:400]}")


def read_wav_int16(path: Path):
    with wave.open(str(path), "rb") as w:
        assert w.getsampwidth() == 2 and w.getnchannels() == 1, "16bit mono만 지원"
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())
    import array
    samples = array.array("h")
    samples.frombytes(raw)
    return samples, rate


def write_wav_int16(path: Path, samples, rate: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())


def add_white_noise(src: Path, dst: Path, snr_db: float, seed: int) -> None:
    """
    합성 화이트 노이즈를 섞어 목표 SNR(dB)을 맞춘다.
    signal_power/noise_power = 10^(SNR/10) 식을 그대로 쓴다.
    """
    import array
    import math
    import random

    samples, rate = read_wav_int16(src)
    n = len(samples)
    signal_power = sum(s * s for s in samples) / n if n else 0.0
    if signal_power <= 0:
        write_wav_int16(dst, samples, rate)
        return

    noise_power = signal_power / (10 ** (snr_db / 10))
    noise_amp = math.sqrt(noise_power)

    rng = random.Random(seed)  # 재현 가능하게 시드 고정
    noisy = array.array("h")
    for s in samples:
        noise = rng.gauss(0, noise_amp)
        v = int(round(s + noise))
        v = max(-32768, min(32767, v))
        noisy.append(v)
    write_wav_int16(dst, noisy, rate)


def cer(reference: str, hypothesis: str) -> float:
    """
    문자 오류율. 공백을 지우고(한국어 STT는 띄어쓰기가 원문과 다르게 나오는 게
    흔하다) 문자 단위 편집거리 / 정답 길이로 계산한다.
    """
    ref = "".join(reference.split())
    hyp = "".join(hypothesis.split())
    if not ref:
        return 0.0 if not hyp else 1.0

    # 표준 편집거리(Levenshtein) DP. 문장이 짧아(수십 자) O(n*m)도 충분히 빠르다.
    n, m = len(ref), len(hyp)
    prev = list(range(m + 1))
    for i in range(1, n + 1):
        curr = [i] + [0] * m
        for j in range(1, m + 1):
            cost = 0 if ref[i - 1] == hyp[j - 1] else 1
            curr[j] = min(prev[j] + 1, curr[j - 1] + 1, prev[j - 1] + cost)
        prev = curr
    return prev[m] / n


def main():
    parser = argparse.ArgumentParser(description="faster-whisper 모델 크기 비교")
    parser.add_argument("--models", default="tiny,base,small")
    parser.add_argument("--out", default=str(REPORT_PATH))
    parser.add_argument("--skip-synthesis", action="store_true",
                        help="이미 만들어둔 data/stt_bench/ 오디오를 재사용")
    args = parser.parse_args()
    model_sizes = [m.strip() for m in args.models.split(",")]

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    utterances = load_utterances()
    print(f"발화 {len(utterances)}개 × 노이즈 레벨 {len(SNR_LEVELS_DB)}개 "
          f"= {len(utterances) * len(SNR_LEVELS_DB)}개 오디오")

    # --- 1. 오디오 준비 ---
    audio_items = []  # {id, snr_label, text, path}
    for u in utterances:
        clean_path = OUT_DIR / f"{u['id']}_clean.wav"
        if not args.skip_synthesis or not clean_path.exists():
            print(f"  합성 중: {u['id']} — {u['text'][:30]}...")
            synthesize_16k(u["text"], clean_path)
        audio_items.append({"id": u["id"], "snr_label": "clean", "text": u["text"],
                            "path": clean_path})

        for snr in SNR_LEVELS_DB:
            if snr is None:
                continue
            noisy_path = OUT_DIR / f"{u['id']}_snr{int(snr)}db.wav"
            if not args.skip_synthesis or not noisy_path.exists():
                add_white_noise(clean_path, noisy_path, snr, seed=hash(u["id"]) & 0xFFFF)
            audio_items.append({"id": u["id"], "snr_label": f"{int(snr)}dB", "text": u["text"],
                                "path": noisy_path})

    # --- 2. 모델별 추론 ---
    from faster_whisper import WhisperModel

    results = []
    for size in model_sizes:
        print(f"\n{'=' * 62}\n 모델: {size}\n{'=' * 62}")
        model = WhisperModel(size, device="cpu", compute_type="int8")
        for item in audio_items:
            t0 = time.time()
            segments, _info = model.transcribe(str(item["path"]), language="ko")
            hyp = " ".join(seg.text.strip() for seg in segments)
            elapsed = time.time() - t0
            err = cer(item["text"], hyp)
            print(f"  [{item['id']:14} {item['snr_label']:6}] "
                  f"CER {err:5.1%}  {elapsed:5.2f}초  \"{hyp[:40]}\"")
            results.append({
                "model": size, "utterance_id": item["id"], "snr_label": item["snr_label"],
                "reference": item["text"], "hypothesis": hyp,
                "cer": round(err, 4), "elapsed_sec": round(elapsed, 3),
            })
        del model  # 다음 모델 로드 전에 메모리 반환 (AGENTS.md 메모리 제약과 같은 이유)

    # --- 3. 집계 ---
    print(f"\n{'=' * 62}\n 요약 (모델 × 노이즈 레벨별 평균 CER / 평균 처리시간)\n{'=' * 62}")
    summary = {}
    for size in model_sizes:
        summary[size] = {}
        for snr_label in ["clean"] + [f"{int(s)}dB" for s in SNR_LEVELS_DB if s is not None]:
            rows = [r for r in results if r["model"] == size and r["snr_label"] == snr_label]
            if not rows:
                continue
            mean_cer = sum(r["cer"] for r in rows) / len(rows)
            mean_time = sum(r["elapsed_sec"] for r in rows) / len(rows)
            summary[size][snr_label] = {"mean_cer": round(mean_cer, 4),
                                        "mean_elapsed_sec": round(mean_time, 3)}
            print(f"  {size:6} / {snr_label:6}   CER {mean_cer:5.1%}   "
                  f"평균 {mean_time:5.2f}초/발화")

    report = {
        "n_utterances": len(utterances),
        "snr_levels_db": [s for s in SNR_LEVELS_DB],
        "models": model_sizes,
        "summary": summary,
        "results": results,
        "주의": "SNR 노이즈는 합성 화이트 노이즈다. 실제 전화망/카페 소음이 아니라 "
                "'노이즈가 있으면 성능이 얼마나 떨어지는가'의 대략적인 하한선으로만 볼 것.",
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n상세 결과 저장: {out}")


if __name__ == "__main__":
    main()
