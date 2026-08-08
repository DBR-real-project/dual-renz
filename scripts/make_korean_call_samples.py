"""
한국어 통화 샘플 생성 (Windows TTS)
담당: 이상원

왜 필요한가:
  공개 데이터셋(FF++, ASVspoof)은 전부 영어권이다. 그런데 우리 타겟은 한국어 통화라
  STT -> 8대 사회공학 기법 분류 -> 통합 스코어까지 이어지는 **콘텐츠 파이프라인을
  한국어로 검증할 자료가 없었다.** 기획서 데이터 확보 방안에도
  *"팀 자체 작성 테스트 대화 시나리오(8대 기법 포함 사기 대화 8~10개, 정상 대화 5개)"*
  가 들어 있다.

  Windows 내장 TTS(Microsoft Heami, ko-KR)로 시나리오를 읽혀 오디오를 만든다.

부수 효과 - 이게 오히려 현실적이다:
  TTS로 만든 음성은 **실제로 합성 음성**이다. 그래서 AASIST가 spoof로 판정해야 정상이고,
  이건 기획서가 상정한 *"AI로 합성한 금융감독원 직원의 목소리로 전화"* 시나리오와
  정확히 같은 상황이다. 즉 이 샘플은 콘텐츠 파이프라인 검증용인 동시에
  음성 스푸핑 탐지의 실사용 케이스이기도 하다.

한계:
  - 실제 사람이 연기한 통화가 아니라 억양·잡음·전화망 압축이 없다.
  - 정상 통화 샘플도 TTS라 AASIST는 이것도 spoof로 판정한다.
    (진짜 사람 목소리 정상 통화가 필요하면 팀원이 직접 녹음해야 한다)

실행:
    .venv\\Scripts\\python.exe scripts/make_korean_call_samples.py
"""

import subprocess
import sys
import tempfile
from pathlib import Path
from xml.sax.saxutils import escape

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "data" / "korean_calls"

# 8대 사회공학 기법이 순서대로 등장하도록 구성한 기관사칭형 시나리오.
# (기획서 3장의 8대 카테고리: 긴급성/권위사칭/금전이체/개인정보·OTP/
#  비밀유지/감정압박/신뢰구축/제3자확인회피)
SCAM_SCRIPT = [
    # ⑦ 신뢰 구축 + ② 권위 사칭
    "안녕하세요. 서울중앙지방검찰청 첨단범죄수사부 김민수 수사관입니다. "
    "고객님 성함과 생년월일 조회 결과 확인했습니다.",
    # ② 권위 사칭 + ⑥ 감정적 압박
    "고객님 명의 계좌가 대포통장으로 범죄에 연루되어 조사가 필요합니다. "
    "사건번호는 이천이십육 형제 삼사공오 번입니다.",
    # ① 긴급성 조성
    "지금 즉시 조치하지 않으면 오늘 안에 모든 계좌가 동결됩니다. 시간이 없습니다.",
    # ⑤ 비밀 유지 강요
    "수사 기밀 사항이라 가족을 포함해 아무에게도 알리지 마세요. "
    "발설하시면 수사 방해로 처벌받으실 수 있습니다.",
    # ⑧ 제3자 확인 회피 유도
    "지금 전화 끊지 마시고 계속 통화 유지해 주세요. "
    "은행에 직접 가시거나 백십이에 신고하시면 공범으로 간주됩니다.",
    # ③ 금전 이체 요구
    "고객님 자산 보호를 위해 안전계좌로 예치금을 이체해 주셔야 합니다. "
    "무혐의 확인 후 즉시 반환해 드립니다.",
    # ④ 개인정보·OTP 요구
    "본인 확인을 위해 보안카드 번호와 오티피 번호를 불러주세요. "
    "계좌번호도 함께 알려주시기 바랍니다.",
    # ⑥ 감정적 압박
    "협조하지 않으시면 구속 수사로 전환될 수 있습니다. 가족분들께도 불이익이 갑니다.",
]

NORMAL_SCRIPT = [
    "여보세요, 어제 보내주신 자료 잘 받았습니다. 감사합니다.",
    "다음 주 화요일 회의는 오후 세 시로 하면 어떨까요? 저는 그때가 편합니다.",
    "아 그리고 점심은 회사 근처 김치찌개 집 어떠세요? 거기 괜찮더라고요.",
    "네 그럼 그때 뵙겠습니다. 좋은 하루 보내세요.",
]

SAMPLES = [
    ("scam_call", SCAM_SCRIPT, "기관사칭형 보이스피싱 (8대 기법 전부 포함)"),
    ("normal_call", NORMAL_SCRIPT, "일상 업무 통화"),
]


def build_ssml(lines, break_ms: int = 700) -> str:
    """
    문장 사이에 명시적 쉼을 넣은 SSML을 만든다.
    쉼이 없으면 Whisper VAD가 전체를 한 덩어리로 묶어버려서, 구간별 타임라인을
    만들 수 없다. 700ms면 실제 통화의 문장 간격과 비슷하다.
    """
    body = f'<break time="{break_ms}ms"/>'.join(escape(l) for l in lines if l.strip())
    return (
        '<speak version="1.0" '
        'xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="ko-KR">'
        f"{body}</speak>"
    )


def synthesize(lines, out_wav: Path, voice_hint: str = "Heami", rate: int = 0) -> None:
    """Windows SAPI로 한국어 음성을 합성한다."""
    with tempfile.TemporaryDirectory() as tmp:
        ssml_path = Path(tmp) / "script.ssml"
        ssml_path.write_text(build_ssml(lines), encoding="utf-8")

        ps = f"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$voice = $s.GetInstalledVoices() | Where-Object {{ $_.VoiceInfo.Name -like '*{voice_hint}*' }} | Select-Object -First 1
if (-not $voice) {{ throw '한국어 음성({voice_hint})을 찾지 못했습니다' }}
$s.SelectVoice($voice.VoiceInfo.Name)
$s.Rate = {rate}
$s.SetOutputToWaveFile('{out_wav.as_posix()}')
$ssml = [System.IO.File]::ReadAllText('{ssml_path.as_posix()}', [System.Text.Encoding]::UTF8)
$s.SpeakSsml($ssml)
$s.SetOutputToNull()
$s.Dispose()
"""
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
            capture_output=True, text=True,
        )
        if proc.returncode != 0 or not out_wav.exists():
            raise RuntimeError(f"TTS 실패: {proc.stderr.strip()[:400]}")


def to_16k_mono(src: Path, dst: Path) -> None:
    """AASIST와 Whisper 둘 다 16kHz 모노를 기대한다."""
    cmd = ["ffmpeg", "-nostdin", "-loglevel", "error", "-y",
           "-i", str(src), "-ac", "1", "-ar", "16000", str(dst)]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 변환 실패: {proc.stderr.strip()[:300]}")


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print("Windows TTS(Microsoft Heami, ko-KR)로 한국어 통화 샘플 생성\n")

    made = []
    for name, lines, desc in SAMPLES:
        raw = OUT_DIR / f"_{name}_raw.wav"
        out = OUT_DIR / f"{name}.wav"
        try:
            synthesize(lines, raw)
            to_16k_mono(raw, out)
            raw.unlink(missing_ok=True)
            sec = out.stat().st_size / (16000 * 2)
            print(f"  {out.name:18} 약 {sec:5.1f}초, {len(lines)}문장   {desc}")
            made.append(out)
        except Exception as exc:
            print(f"  {name}: 실패 - {exc}", file=sys.stderr)

    # 스크립트를 함께 저장해둔다. STT 결과와 대조해 인식률을 볼 수 있다.
    ref = OUT_DIR / "reference_scripts.txt"
    with open(ref, "w", encoding="utf-8") as f:
        for name, lines, desc in SAMPLES:
            f.write(f"### {name} - {desc}\n")
            for i, l in enumerate(lines, 1):
                f.write(f"{i}. {l}\n")
            f.write("\n")
    print(f"\n  {ref.name:18} 원본 스크립트 (STT 인식률 대조용)")

    print(f"\n저장 위치: {OUT_DIR}")
    print("\n다음 단계:")
    print("  .venv\\Scripts\\python.exe scripts\\demo_content_pipeline.py")


if __name__ == "__main__":
    main()
