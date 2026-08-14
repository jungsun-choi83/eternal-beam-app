"""
5-dog Luma validation harness — READ-ONLY with respect to the pipeline.

이 스크립트는 파이프라인을 수정하지 않는다. backend/services 의 기존 함수를 그대로
호출해서(같은 키프레임 준비, 같은 프롬프트 빌더, 같은 Luma 호출) 실제로 무엇이
Luma 로 나가고 무엇이 돌아오는지 기록만 한다.

재현하는 경로는 backend/routers/generate.py 의 /api/generate-pet-video 와 동일하다:

    cutout PNG
      → resolve_keyframe_bg_rgb() / flatten_rgba_to_jpeg_bytes()   (luma_keyframe.py)
      → supabase_assets.upload_asset_to_storage()                  (공개 URL 필요)
      → build_idle_action_prompts()                                (luma_service.py)
      → create_generation()          → generation_id
      → poll_until_complete()        → video_url
      → download_video()             → 로컬 mp4 (+ ffprobe 메타)

HTTP 엔드포인트(/api/generate-pet-video)를 거치지 않고 서비스 함수를 직접 부르는
이유는 두 가지다:
  1) 그 엔드포인트는 Luma generation_id 를 응답에 담지 않는다 — 검증 표에 필요함.
  2) ENABLE_GENERATE_API=1 배포 없이 로컬에서 바로 돌릴 수 있다.

사용법:
    # 1) 설정만 점검 (크레딧 소모 없음)
    python scripts/luma_validation_run.py --preflight

    # 2) 무엇이 돌지 미리보기 (크레딧 소모 없음, 프롬프트 전문 출력)
    python scripts/luma_validation_run.py --dogs-dir samples/dogs --motion both

    # 3) 실제 생성 (크레딧 소모!)
    python scripts/luma_validation_run.py --dogs-dir samples/dogs --motion idle --confirm

입력: --dogs-dir 안의 이미지 5장. 이미 누끼된 RGBA PNG 를 권장한다(프론트가
skip_preprocessing=true 로 보내는 것과 동일). 원본 사진을 넣으면 배경이 그대로
키프레임에 들어가므로 결과가 프로덕션과 달라진다.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import subprocess
import sys
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_env() -> None:
    """backend/main.py 와 같은 순서로 .env 를 읽는다(빈 값은 덮어쓰지 않음)."""
    try:
        from dotenv import dotenv_values, load_dotenv
    except ImportError:
        print("[warn] python-dotenv 가 없습니다 — 셸 환경변수만 사용합니다.")
        return

    load_dotenv(ROOT / ".env")
    for name in ("env.local", ".env.local"):
        path = ROOT / name
        if not path.is_file():
            continue
        for key, val in dotenv_values(path).items():
            if val is None:
                continue
            s = str(val).strip().strip('"').strip("'")
            if s:
                os.environ[key] = s


_load_env()

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
PLACEHOLDER_MARKERS = ("your_", "_here", "changeme", "xxx")

MOTION_IDLE = "idle"
MOTION_ACTION = "action"


# ---------------------------------------------------------------------------
# preflight
# ---------------------------------------------------------------------------


@dataclass
class Check:
    name: str
    ok: bool
    detail: str


def _looks_like_placeholder(value: str) -> bool:
    low = value.strip().lower()
    return any(marker in low for marker in PLACEHOLDER_MARKERS)


def _check_ffprobe() -> Check:
    try:
        subprocess.run(["ffprobe", "-version"], capture_output=True, timeout=10, check=True)
        return Check("ffprobe", True, "설치됨 (duration/fps/resolution 측정 가능)")
    except Exception:
        return Check("ffprobe", False, "없음 — duration/fps/resolution 은 기록되지 않습니다")


def _check_luma_key() -> Check:
    key = (os.getenv("LUMA_API_KEY") or "").strip()
    if not key:
        return Check("LUMA_API_KEY", False, "미설정")
    if _looks_like_placeholder(key):
        return Check(
            "LUMA_API_KEY",
            False,
            f"플레이스홀더입니다 ({key[:18]}…). 비어 있지 않아서 create_generation() 의 "
            "'if not key' 가드를 통과하고 Luma 에서 HTTP 401 로 실패합니다.",
        )
    return Check("LUMA_API_KEY", True, f"설정됨 ({key[:6]}…{key[-4:]}, {len(key)}자)")


def _check_luma_auth() -> Check:
    """크레딧을 쓰지 않는 인증 확인 — 생성 목록 조회."""
    key = (os.getenv("LUMA_API_KEY") or "").strip()
    if not key or _looks_like_placeholder(key):
        return Check("Luma 인증", False, "키가 없거나 플레이스홀더 — 호출 생략")
    try:
        import requests
    except ImportError:
        return Check("Luma 인증", False, "requests 미설치")

    from backend.services.luma_service import LUMA_API_BASE

    try:
        r = requests.get(
            f"{LUMA_API_BASE}/generations",
            headers={"Authorization": f"Bearer {key}", "Accept": "application/json"},
            params={"limit": 1},
            timeout=20,
        )
    except Exception as e:
        return Check("Luma 인증", False, f"네트워크 실패: {e}")

    if r.status_code == 200:
        return Check("Luma 인증", True, "HTTP 200 — 키가 유효합니다")
    if r.status_code in (401, 403):
        return Check("Luma 인증", False, f"HTTP {r.status_code} — 키가 거부됨: {r.text[:200]}")
    return Check(
        "Luma 인증",
        True,
        f"HTTP {r.status_code} (목록 API 응답이 예상과 다름 — 키 유효성은 미확인)",
    )


def _check_supabase() -> Check:
    from backend.services import supabase_assets

    client = supabase_assets.get_client()
    if client is None:
        return Check(
            "Supabase Storage",
            False,
            "미설정/URL 형식 불일치 — Luma 는 공개 이미지 URL 이 필요하므로 필수입니다",
        )
    return Check("Supabase Storage", True, f"설정됨 (bucket={supabase_assets.BUCKET})")


def _check_mock() -> Check:
    mock = (os.getenv("LUMA_MOCK") or "").strip().lower()
    if mock in ("1", "true", "yes"):
        return Check(
            "LUMA_MOCK",
            False,
            "켜져 있습니다 — 실제 Luma 를 호출하지 않고 키프레임 정지 영상을 반환합니다. "
            "실검증하려면 끄세요.",
        )
    return Check("LUMA_MOCK", True, "꺼짐 — 실제 Luma 를 호출합니다")


def run_preflight() -> bool:
    checks = [
        _check_luma_key(),
        _check_mock(),
        _check_luma_auth(),
        _check_supabase(),
        _check_ffprobe(),
    ]
    print("\n=== Luma 연결 점검 ===")
    for c in checks:
        print(f"  [{'OK ' if c.ok else 'FAIL'}] {c.name}: {c.detail}")
    blocking = [c for c in checks if not c.ok and c.name != "ffprobe"]
    print()
    if blocking:
        print(f"→ 차단 항목 {len(blocking)}개. 해결 전에는 실제 생성을 돌릴 수 없습니다.")
        return False
    print("→ 모든 항목 통과. --confirm 으로 실제 생성을 돌릴 수 있습니다.")
    return True


# ---------------------------------------------------------------------------
# sample discovery
# ---------------------------------------------------------------------------


def discover_samples(dogs_dir: Path, expected: int = 5) -> list[Path]:
    if not dogs_dir.is_dir():
        raise SystemExit(
            f"샘플 폴더가 없습니다: {dogs_dir}\n"
            f"  mkdir -p {dogs_dir} 후 강아지 누끼 PNG {expected}장을 넣으세요."
        )
    files = sorted(p for p in dogs_dir.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)
    if not files:
        raise SystemExit(f"{dogs_dir} 안에 이미지가 없습니다.")
    if len(files) != expected:
        print(f"[warn] 이미지 {len(files)}장 발견 (기대 {expected}장) — 그대로 진행합니다.")
    return files


# ---------------------------------------------------------------------------
# per-generation record
# ---------------------------------------------------------------------------


@dataclass
class GenerationRecord:
    dog_id: str
    input_image: str
    motion_type: str
    prompt: str = ""
    keyframe_url: str = ""
    keyframe_bg: str = ""
    generation_id: str = ""
    success: bool = False
    video_url: str = ""
    local_video: str = ""
    duration_sec: Optional[float] = None
    fps: Optional[float] = None
    resolution: str = ""
    error: str = ""
    # 사람이 눈으로 보고 채우는 칸 — 자동으로 추정하지 않는다.
    identity_preserved: Optional[bool] = None
    anatomy_stable: Optional[bool] = None
    motion_natural: Optional[bool] = None
    major_issue: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _ffprobe_meta(path: Path) -> dict[str, Any]:
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error",
                "-select_streams", "v:0",
                "-show_entries", "stream=width,height,avg_frame_rate:format=duration",
                "-of", "json",
                str(path),
            ],
            capture_output=True,
            timeout=30,
            check=True,
        ).stdout
        data = json.loads(out)
    except Exception:
        return {}

    stream = (data.get("streams") or [{}])[0]
    fmt = data.get("format") or {}
    fps = None
    raw_fps = stream.get("avg_frame_rate") or ""
    if "/" in raw_fps:
        num, _, den = raw_fps.partition("/")
        try:
            fps = round(float(num) / float(den), 2) if float(den) else None
        except (ValueError, ZeroDivisionError):
            fps = None
    duration = None
    try:
        duration = round(float(fmt.get("duration")), 2)
    except (TypeError, ValueError):
        pass
    w, h = stream.get("width"), stream.get("height")
    return {
        "duration_sec": duration,
        "fps": fps,
        "resolution": f"{w}x{h}" if w and h else "",
    }


async def run_one(
    image_path: Path,
    dog_id: str,
    motion: str,
    run_dir: Path,
    *,
    model: str,
    resolution: str,
    dry_run: bool,
) -> GenerationRecord:
    from backend.services import supabase_assets
    from backend.services.luma_keyframe import (
        flatten_rgba_to_jpeg_bytes,
        resolve_keyframe_bg_rgb,
    )
    from backend.services.luma_service import (
        build_idle_action_prompts,
        create_generation,
        download_video,
        poll_until_complete,
    )

    rec = GenerationRecord(
        dog_id=dog_id,
        input_image=str(image_path.relative_to(ROOT)) if image_path.is_relative_to(ROOT) else str(image_path),
        motion_type=motion,
    )

    raw = image_path.read_bytes()

    # generate.py 와 동일: 프롬프트와 키프레임 배경은 같은 판정(is_black_tan_dog)을 쓴다.
    bg_rgb = resolve_keyframe_bg_rgb(raw)
    rec.keyframe_bg = "white" if bg_rgb == (255, 255, 255) else "black"
    idle_prompt, action_prompt = build_idle_action_prompts(raw)
    rec.prompt = idle_prompt if motion == MOTION_IDLE else action_prompt

    if dry_run:
        rec.error = "dry-run (생성하지 않음)"
        return rec

    cid = f"val_{dog_id}_{motion}_{uuid.uuid4().hex[:8]}"
    try:
        key_jpeg = flatten_rgba_to_jpeg_bytes(raw, bg_rgb=bg_rgb)
        rec.keyframe_url = await supabase_assets.upload_asset_to_storage(
            f"luma_validation/{cid}/keyframe.jpg", key_jpeg, "image/jpeg"
        )
    except Exception as e:
        rec.error = f"키프레임 업로드 실패: {e}"
        return rec

    try:
        rec.generation_id = await create_generation(
            rec.keyframe_url, prompt=rec.prompt, model=model, resolution=resolution
        )
    except Exception as e:
        rec.error = f"create_generation 실패: {e}"
        return rec

    try:
        rec.video_url = await poll_until_complete(
            rec.generation_id,
            poll_interval=float(os.getenv("LUMA_POLL_INTERVAL_SEC", "5")),
            max_wait=float(os.getenv("LUMA_POLL_MAX_SEC", "1200")),
        )
    except Exception as e:
        rec.error = f"폴링 실패: {e}"
        return rec

    try:
        tmp = await download_video(rec.video_url)
        dest = run_dir / f"{dog_id}_{motion}.mp4"
        Path(tmp).replace(dest)
        rec.local_video = str(dest.relative_to(ROOT))
        meta = _ffprobe_meta(dest)
        rec.duration_sec = meta.get("duration_sec")
        rec.fps = meta.get("fps")
        rec.resolution = meta.get("resolution", "")
    except Exception as e:
        rec.error = f"다운로드/검사 실패: {e}"
        return rec

    rec.success = True
    return rec


# ---------------------------------------------------------------------------
# report
# ---------------------------------------------------------------------------


def _yn(value: Optional[bool]) -> str:
    if value is None:
        return "—"
    return "yes" if value else "no"


def _verdict(records: list[GenerationRecord], motion: str) -> str:
    rows = [r for r in records if r.motion_type == motion]
    if not rows:
        return "N/A"
    row = rows[0]
    if not row.success:
        # dry-run 은 실패가 아니라 "아직 안 돌림"이다.
        return "—" if row.error.startswith("dry-run") else "FAIL"
    # 사람이 아직 안 봤으면 판정을 지어내지 않는다.
    if row.identity_preserved is None:
        return "REVIEW"
    if row.identity_preserved and row.anatomy_stable and row.motion_natural:
        return "PASS"
    return "FAIL"


def write_report(records: list[GenerationRecord], run_dir: Path, dry_run: bool) -> None:
    (run_dir / "report.json").write_text(
        json.dumps([r.to_dict() for r in records], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    by_dog: dict[str, list[GenerationRecord]] = {}
    for r in records:
        by_dog.setdefault(r.dog_id, []).append(r)

    lines: list[str] = []
    lines.append(f"# Luma 5-dog 검증 결과 ({datetime.now(timezone.utc).isoformat(timespec='seconds')})")
    lines.append("")
    if dry_run:
        lines.append("> **DRY RUN** — Luma 를 호출하지 않았습니다. 프롬프트/설정 확인용입니다.")
        lines.append("")
    lines.append("| Dog | Idle | Action | Notes |")
    lines.append("|---|---|---|---|")
    for dog_id, rows in by_dog.items():
        idle_v = _verdict(rows, MOTION_IDLE)
        action_v = _verdict(rows, MOTION_ACTION)
        notes = "; ".join(r.error for r in rows if r.error) or ""
        if not notes:
            pending = [r.motion_type for r in rows if r.identity_preserved is None and r.success]
            if pending:
                notes = f"육안 검토 대기: {', '.join(pending)}"
        lines.append(f"| {dog_id} | {idle_v} | {action_v} | {notes} |")

    lines.append("")
    lines.append("## 생성 상세")
    lines.append("")
    for r in records:
        lines.append(f"### {r.dog_id} — {r.motion_type}")
        lines.append("")
        lines.append(f"- input: `{r.input_image}`")
        lines.append(f"- keyframe bg: {r.keyframe_bg}")
        lines.append(f"- keyframe url: {r.keyframe_url or '—'}")
        lines.append(f"- generation id: `{r.generation_id or '—'}`")
        lines.append(f"- success: {r.success}")
        lines.append(f"- video url: {r.video_url or '—'}")
        lines.append(f"- local: `{r.local_video or '—'}`")
        lines.append(
            f"- duration: {r.duration_sec if r.duration_sec is not None else '—'}s"
            f" | fps: {r.fps if r.fps is not None else '—'}"
            f" | resolution: {r.resolution or '—'}"
        )
        lines.append(f"- identity preserved: {_yn(r.identity_preserved)}")
        lines.append(f"- anatomy stable: {_yn(r.anatomy_stable)}")
        lines.append(f"- motion natural: {_yn(r.motion_natural)}")
        lines.append(f"- major issue: {r.major_issue or '—'}")
        if r.error:
            lines.append(f"- error: `{r.error}`")
        lines.append("")
        lines.append("<details><summary>exact prompt sent to Luma</summary>")
        lines.append("")
        lines.append("```")
        lines.append(r.prompt)
        lines.append("```")
        lines.append("</details>")
        lines.append("")

    (run_dir / "report.md").write_text("\n".join(lines), encoding="utf-8")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------


async def _amain(args: argparse.Namespace) -> int:
    images = discover_samples(Path(args.dogs_dir), expected=args.expect)
    motions = [MOTION_IDLE, MOTION_ACTION] if args.motion == "both" else [args.motion]
    dry_run = not args.confirm

    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    run_dir = ROOT / "outputs" / "luma_validation" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    total = len(images) * len(motions)
    print(f"\n샘플 {len(images)}장 × 모션 {len(motions)}종 = 생성 {total}건")
    print(f"모델={args.model} 해상도={args.resolution}")
    print(f"출력: {run_dir}")
    if dry_run:
        print("\n*** DRY RUN — Luma 를 호출하지 않습니다. 실제 생성은 --confirm ***\n")
    else:
        print(f"\n*** 실제 생성 — Luma 크레딧 {total}건이 소모됩니다 ***\n")

    records: list[GenerationRecord] = []
    for idx, image in enumerate(images, start=1):
        dog_id = f"Dog {idx}"
        for motion in motions:
            print(f"[{dog_id}/{motion}] {image.name} …", flush=True)
            rec = await run_one(
                image,
                dog_id,
                motion,
                run_dir,
                model=args.model,
                resolution=args.resolution,
                dry_run=dry_run,
            )
            records.append(rec)
            if rec.success:
                print(f"  ok  gen={rec.generation_id} {rec.duration_sec}s {rec.resolution}")
            elif rec.error and not dry_run:
                print(f"  ERR {rec.error}")

    write_report(records, run_dir, dry_run)
    print(f"\n리포트: {run_dir / 'report.md'}")
    print(f"        {run_dir / 'report.json'}")
    if not dry_run:
        print(
            "\n다음 단계: 각 mp4 를 눈으로 확인하고 report.json 의 "
            "identity_preserved / anatomy_stable / motion_natural / major_issue 를 채운 뒤 "
            "--rerender-report 로 표를 다시 생성하세요."
        )
    return 0


def _rerender(path: Path) -> int:
    """사람이 채운 report.json 으로 report.md 표만 다시 만든다."""
    data = json.loads(path.read_text(encoding="utf-8"))
    records = [GenerationRecord(**row) for row in data]
    write_report(records, path.parent, dry_run=False)
    print(f"갱신: {path.parent / 'report.md'}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description="5-dog Luma 검증 (파이프라인 무수정)")
    p.add_argument("--preflight", action="store_true", help="설정만 점검하고 종료")
    p.add_argument("--dogs-dir", default="samples/dogs", help="강아지 누끼 이미지 폴더")
    p.add_argument("--expect", type=int, default=5, help="기대 샘플 수 (기본 5)")
    p.add_argument("--motion", choices=[MOTION_IDLE, MOTION_ACTION, "both"], default=MOTION_IDLE)
    p.add_argument("--model", default=os.getenv("LUMA_MODEL", "ray-2"))
    p.add_argument("--resolution", default=os.getenv("LUMA_RESOLUTION", "720p"))
    p.add_argument("--confirm", action="store_true", help="실제로 Luma 를 호출 (크레딧 소모)")
    p.add_argument("--rerender-report", metavar="REPORT_JSON", help="채워 넣은 report.json 으로 표 재생성")
    args = p.parse_args()

    if args.rerender_report:
        return _rerender(Path(args.rerender_report))

    if args.preflight:
        return 0 if run_preflight() else 1

    if args.confirm and not run_preflight():
        print("\n차단 항목이 있어 중단합니다.")
        return 1

    return asyncio.run(_amain(args))


if __name__ == "__main__":
    raise SystemExit(main())
