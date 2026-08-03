"""
LivePortrait(KwaiVGI/LivePortrait) 단건 추론 래퍼 — "액션 20종" 파이프라인 1단계.

Luma idle 루프(subtle-motion)와는 완전히 다른 경로다: Luma는 프롬프트 기반 I2V이고,
여기는 "소스 이미지(우리 강아지 사진) + 드라이빙 영상(레퍼런스 동작)" 조합으로 모션만
전이시키는 LivePortrait를 쓴다.

★ 왜 Animals 모드(inference_animals.py)인가?
LivePortrait는 사람용 파이프라인(inference.py)과 별도로, 고양이/강아지 등 약 23만
프레임으로 파인튜닝한 "Animals 모드"(inference_animals.py, LivePortraitPipelineAnimal)를
제공한다. 우리 소스가 강아지 사진이므로 사람용이 아니라 이 Animals 모드를 쓴다.
(참고: https://github.com/KwaiVGI/LivePortrait, 2024-08-02 changelog)

★ 정체성(identity) 보존 파라미터 — 왜 이 값들인가
LivePortrait 공식 문서/체인지로그 기준으로 검증한 내용:

  - flag_relative_motion=True (기본값 유지): 소스가 이미지 1장 + 드라이빙이 영상일 때,
    "드라이빙 영상의 첫 프레임 기준 모션 오프셋"을 소스의 모션에 더하는 방식(relative).
    False(absolute)로 두면 표현이 과장되거나 "identity leakage"(드라이빙 인물/개체의
    생김새가 섞여 들어감)가 생길 수 있다고 공식 문서가 명시 — 우리 목표(원본 사진과
    생김새가 동일하게 유지)에 정확히 반대되는 부작용이라 True를 유지한다.
  - flag_stitching=False: Animals 모드는 stitching/retargeting 모듈을 아직 학습하지
    않았다고 공식 changelog(2024-08-02)에 명시되어 있고, 그래서 Animals 모드에는
    --no_flag_stitching을 공식적으로 권장한다. (사람 모드에서는 보통 True가 기본이지만
    "머리 움직임이 크거나 동물이면 False 권장"이라고도 되어 있어 이중으로 일치.)
  - flag_pasteback=False: 마찬가지로 Animals 모드 공식 권장(paste-back 비권장). 게다가
    우리는 LivePortrait 출력 위에 SAM2로 우리만의 블랙 배경 합성을 다시 하므로(3단계),
    LivePortrait가 드라이빙 영상의 배경을 소스에 붙여넣지 않는 편이 오히려 우리
    파이프라인과 더 잘 맞는다 — 배경 오염을 원천적으로 줄여준다.
  - flag_do_crop=True (기본값 유지): 소스를 얼굴/머리 중심의 표준 공간으로 크롭해
    정합(align)한 뒤 워핑하므로, 크롭을 끄면 오히려 워핑 품질이 떨어져 생김새가
    흔들릴 수 있다.
  - driving_multiplier=1.75: 공식 Animals 모드 예시 커맨드가 그대로 1.75를 쓴다
    (`--driving_multiplier 1.75 --no_flag_stitching`). Animals 모드는 stitching이
    꺼져 있어 기본 모션 세기가 약하게 느껴질 수 있어 배율을 키운 값 — 공식 예시값을
    그대로 채택.
  - driving_option="pose-friendly" (기본값 유지): "expression-friendly"는
    driving_multiplier가 표정 강도에 더 강하게 작용하는 모드라, 우리처럼 몸/머리
    동작(앉기, 냄새 맡기 등) 위주인 액션에는 기본값(pose-friendly)이 더 안전하다.

이 파라미터들은 실제 LivePortrait 저장소/체크포인트 없이는 마지막 %까지 검증할 수
없다 — 사용자가 리포를 설치한 뒤 고야(Goya) 사진으로 1~2개 액션을 먼저 시험 생성해
보고 필요시 `LivePortraitIdentityParams`를 조정하는 것을 권장한다(설정 가이드 참고).

실행 위치: 이 모듈은 "어디서 호출되는지"를 모른다 — 로컬 RTX 4090 워커
(backend/workers/live_portrait_worker.py, 1차 경로) 안에서 직접 호출되거나,
Modal GPU 함수(backend/modal_apps/live_portrait_app.py, 선택적 보조 경로) 안에서
호출되거나 동일하게 동작한다. 두 경우 모두 "LivePortrait 리포 + 가중치가 이미
설치되어 있는 프로세스 안에서" 호출된다는 전제만 같다.

환경변수:
  LIVE_PORTRAIT_REPO_DIR     KwaiVGI/LivePortrait를 clone한 로컬 경로
                             (기본: "~/LivePortrait")
  LIVE_PORTRAIT_PYTHON       그 repo용 가상환경의 python 실행파일 경로
                             (기본: "python" — PATH에서 찾음)
  LIVE_PORTRAIT_DRIVING_MULTIPLIER  기본 "1.75" (위 설명 참고)
  LIVE_PORTRAIT_INFERENCE_TIMEOUT_SEC  기본 "600" (영상 1건당 subprocess 타임아웃)
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Union

SourceImage = Union[str, Path, bytes]


@dataclass
class LivePortraitIdentityParams:
    """상단 docstring에서 근거를 설명한 "정체성 보존 최우선" 기본값."""

    flag_relative_motion: bool = True
    flag_stitching: bool = False
    flag_pasteback: bool = False
    flag_do_crop: bool = True
    driving_option: str = "pose-friendly"
    driving_multiplier: float = float(os.getenv("LIVE_PORTRAIT_DRIVING_MULTIPLIER", "1.75"))
    extra_cli_args: list[str] = field(default_factory=list)

    def to_cli_args(self) -> list[str]:
        args = [
            "--flag_relative_motion" if self.flag_relative_motion else "--no_flag_relative_motion",
            "--flag_stitching" if self.flag_stitching else "--no_flag_stitching",
            "--flag_pasteback" if self.flag_pasteback else "--no_flag_pasteback",
            "--flag_do_crop" if self.flag_do_crop else "--no_flag_do_crop",
            "--driving_option", self.driving_option,
            "--driving_multiplier", str(self.driving_multiplier),
        ]
        args.extend(self.extra_cli_args)
        return args


def _resolve_repo_dir(repo_dir: Optional[str] = None) -> Path:
    raw = repo_dir or os.getenv("LIVE_PORTRAIT_REPO_DIR") or "~/LivePortrait"
    resolved = Path(raw).expanduser().resolve()
    if not resolved.is_dir():
        raise RuntimeError(
            f"LivePortrait 리포를 찾을 수 없습니다: {resolved}. "
            "docs/LivePortrait_설치_가이드.md 를 따라 clone/설치한 뒤 "
            "LIVE_PORTRAIT_REPO_DIR 환경변수로 경로를 지정하세요."
        )
    script = resolved / "inference_animals.py"
    if not script.is_file():
        raise RuntimeError(
            f"{resolved} 안에 inference_animals.py가 없습니다 — Animals 모드가 포함된 "
            "LivePortrait 리포인지 확인하세요."
        )
    return resolved


def _is_url(value: str) -> bool:
    return value.strip().lower().startswith(("http://", "https://"))


def resolve_source_image_to_local_path(
    source: SourceImage, *, workdir: Path, filename: str = "source.png"
) -> Path:
    """
    소스 이미지를 로컬 파일 경로로 통일한다.

    지원 입력:
      - bytes: workdir에 파일로 씀
      - "http(s)://..." URL 문자열: 다운로드해서 workdir에 저장
      - 그 외 문자열/Path: 이미 로컬 파일 경로로 간주(윈도우 경로 포함) — 그대로 사용
        (예: 로컬 테스트에서 "누끼딴고야.png"처럼 Supabase 업로드 없이 바로 넘기는 경우)
    """
    if isinstance(source, bytes):
        dest = workdir / filename
        dest.write_bytes(source)
        return dest

    text = str(source)
    if _is_url(text):
        import requests

        dest = workdir / filename
        r = requests.get(text, timeout=60)
        r.raise_for_status()
        dest.write_bytes(r.content)
        return dest

    local = Path(text).expanduser()
    if not local.is_file():
        raise FileNotFoundError(f"소스 이미지 파일을 찾을 수 없습니다: {local}")
    return local


def run_live_portrait_inference(
    source_image: SourceImage,
    driving_video_path: Union[str, Path],
    output_path: Union[str, Path],
    *,
    params: Optional[LivePortraitIdentityParams] = None,
    repo_dir: Optional[str] = None,
    python_exe: Optional[str] = None,
    timeout_sec: Optional[float] = None,
) -> Path:
    """
    LivePortrait Animals 모드 1건 추론: source_image(사진 1장) + driving_video_path
    (레퍼런스 동작 영상) → output_path(mp4).

    source_image는 bytes / URL 문자열 / 로컬 파일 경로 중 무엇이든 받는다(로컬 테스트 시
    Supabase 업로드 없이 바로 파일 경로를 넘길 수 있게 하기 위함 — 예: 고야 사진 테스트).
    """
    resolved_repo = _resolve_repo_dir(repo_dir)
    py = python_exe or os.getenv("LIVE_PORTRAIT_PYTHON") or "python"
    resolved_params = params or LivePortraitIdentityParams()
    driving_path = Path(driving_video_path)
    if not driving_path.is_file():
        raise FileNotFoundError(f"드라이빙 영상을 찾을 수 없습니다: {driving_path}")

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="eb_liveportrait_") as td:
        workdir = Path(td)
        src_local = resolve_source_image_to_local_path(source_image, workdir=workdir)
        lp_out_dir = workdir / "lp_out"
        lp_out_dir.mkdir(parents=True, exist_ok=True)

        cmd = [
            py,
            "inference_animals.py",
            "-s", str(src_local),
            "-d", str(driving_path),
            "-o", str(lp_out_dir),
            *resolved_params.to_cli_args(),
        ]

        try:
            proc = subprocess.run(
                cmd,
                cwd=str(resolved_repo),
                capture_output=True,
                text=True,
                timeout=timeout_sec or float(os.getenv("LIVE_PORTRAIT_INFERENCE_TIMEOUT_SEC", "600")),
            )
        except subprocess.TimeoutExpired as e:
            raise RuntimeError(
                f"LivePortrait 추론 타임아웃({driving_path.name}): {e}"
            ) from e

        if proc.returncode != 0:
            raise RuntimeError(
                f"LivePortrait 추론 실패({driving_path.name}), exit={proc.returncode}\n"
                f"stdout(마지막 2000자): {proc.stdout[-2000:]}\n"
                f"stderr(마지막 2000자): {proc.stderr[-2000:]}"
            )

        produced = sorted(
            lp_out_dir.rglob("*.mp4"), key=lambda p: p.stat().st_mtime, reverse=True
        )
        if not produced:
            raise RuntimeError(
                f"LivePortrait가 mp4를 생성하지 않았습니다({driving_path.name}). "
                f"stdout(마지막 1000자): {proc.stdout[-1000:]}"
            )
        shutil.copyfile(produced[0], out_path)

    return out_path
