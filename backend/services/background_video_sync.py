"""
배경 애니메이션 영상 ↔ 강아지(LivePortrait/Luma idle) 영상 fps/duration 동기화.

두 영상을 브라우저/기기에서 겹쳐(alpha-composite) 재생하려면 프레임 수·재생
속도가 어긋나면 안 되므로, 둘 다 같은 fps/길이 규칙을 따르게 ffmpeg로 강제한다.

## 고정 규칙(convention)을 어떻게 정했는가

LivePortrait 액션 20종(`backend/services/live_portrait_batch.py`)은 각 드라이빙
영상의 원본 fps/길이를 그대로 쓰므로(아직 실제 드라이빙 영상 파일이 없어 값이
확정되어 있지 않음), 배경 파이프라인이 그 값을 미리 알 방법이 없다. 그래서:

  - fps: 24로 고정. `live_portrait_postprocess.force_black_background()`도
    `cap.get(cv2.CAP_PROP_FPS) or 24.0`로 24를 기본값(fallback)으로 쓰고 있어
    이 프로젝트 안에서 이미 암묵적 기본값으로 쓰이던 숫자와 맞춘다.
  - duration: 강아지 쪽이 "액션 1건"이 아니라 "대기(idle) 상태에서 계속 반복
    재생되는" 배경이라는 성격상, 이미 존재하는 Luma idle 루프 관례
    (`seamless_loop_service.py`의 SEAMLESS_LOOP_MAX_SEC 기본값 4.0초)를 그대로
    가져와 기본 목표 길이로 쓴다 — 이 배경 영상도 같은 "계속 반복되는 배경/대기
    레이어"이기 때문에 이질감이 없다.

이 두 상수는 고정값이 아니라 "기본값"이다 — 실제 서비스에서 특정 콘텐츠(다른
에이전트가 만드는 LivePortrait 액션)의 정확한 duration/fps를 나중에 알게 되면,
`sync_video_fps_duration()`을 그 값으로 다시 호출해 정확히 맞출 수 있다
(enqueue API의 target_fps/target_duration_sec 파라미터로 노출해 둠 — 관련 라우터
참고). LivePortrait 쪽 파일은 이번 작업에서 건드리지 않는다(다른 에이전트 담당).

환경변수:
  BACKGROUND_VIDEO_TARGET_FPS            기본 "24"
  BACKGROUND_VIDEO_TARGET_DURATION_SEC   기본 "4.0"
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

DEFAULT_TARGET_FPS = 24.0
DEFAULT_TARGET_DURATION_SEC = 4.0


def target_fps() -> float:
    return float(os.getenv("BACKGROUND_VIDEO_TARGET_FPS", str(DEFAULT_TARGET_FPS)))


def target_duration_sec() -> float:
    return float(
        os.getenv("BACKGROUND_VIDEO_TARGET_DURATION_SEC", str(DEFAULT_TARGET_DURATION_SEC))
    )


def probe_duration_sec(path: str) -> float:
    """ffmpeg -i 스텁 출력(stderr)에서 Duration을 파싱 — ffprobe 없는 환경도 대응
    (seamless_loop_service.py의 동일 패턴 재사용)."""
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", path],
        capture_output=True,
        text=True,
        timeout=30,
    )
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+)[.](\d+)", proc.stderr or "")
    if not m:
        return 0.0
    h, mi, s, cs = map(int, m.groups())
    return h * 3600 + mi * 60 + s + cs / 100.0


def probe_fps(path: str) -> float:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", path],
        capture_output=True,
        text=True,
        timeout=30,
    )
    m = re.search(r",\s*([\d.]+)\s*fps", proc.stderr or "")
    if not m:
        return 0.0
    try:
        return float(m.group(1))
    except ValueError:
        return 0.0


def sync_video_fps_duration(
    input_path: str,
    output_path: str,
    *,
    fps: Optional[float] = None,
    duration_sec: Optional[float] = None,
) -> Path:
    """
    입력 mp4를 (fps, duration_sec)에 정확히 맞춰 재인코딩.

      - fps: `-r fps`로 프레임레이트를 강제 변환(재타이밍, 프레임 보간이 아니라
        가장 가까운 프레임 반복/드롭 — LivePortrait 쪽도 특별한 보간을 안 쓰므로
        일관성을 위해 같은 방식 사용).
      - duration_sec: 원본이 더 길면 잘라내고(-t), 더 짧으면 `-stream_loop`로
        영상을 반복 재생해 정확히 목표 길이까지 채운다(Luma 출력 1건은 보통
        수 초라 목표(기본 4초)보다 짧을 수 있어 이 케이스가 실제로 자주 발생함).
    """
    out_fps = fps if fps is not None else target_fps()
    out_dur = duration_sec if duration_sec is not None else target_duration_sec()

    src_dur = probe_duration_sec(input_path)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    needs_loop = src_dur > 0 and src_dur < out_dur
    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if needs_loop:
        # 목표 길이를 채울 만큼 넉넉히 반복한 뒤 정확히 -t로 잘라낸다.
        loops = max(1, int(out_dur // max(src_dur, 0.1)) + 2)
        cmd += ["-stream_loop", str(loops)]
    cmd += ["-i", input_path]
    cmd += [
        "-r", f"{out_fps:g}",
        "-t", f"{out_dur:.3f}",
        "-c:v", "libx264", "-preset", "fast", "-crf", "20",
        "-pix_fmt", "yuv420p",
        "-an",
        "-movflags", "+faststart",
        str(out),
    ]

    subprocess.run(cmd, capture_output=True, timeout=180, check=True)
    return out


def sync_bytes_fps_duration(
    mp4_bytes: bytes,
    *,
    fps: Optional[float] = None,
    duration_sec: Optional[float] = None,
) -> bytes:
    """sync_video_fps_duration()의 bytes-in/bytes-out 래퍼(호출자가 임시파일을 안 다뤄도 되게)."""
    with tempfile.TemporaryDirectory(prefix="eb_bg_sync_") as td:
        inp = Path(td) / "in.mp4"
        outp = Path(td) / "out.mp4"
        inp.write_bytes(mp4_bytes)
        sync_video_fps_duration(str(inp), str(outp), fps=fps, duration_sec=duration_sec)
        return outp.read_bytes()
