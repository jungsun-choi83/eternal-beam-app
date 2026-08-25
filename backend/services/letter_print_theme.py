"""
Soul Trace 최종 편지 화면 → A5 인쇄. **매핑의 단일 출처.**

── 무엇을 베끼는가 ─────────────────────────────────────────────────────────
고객이 실제로 보는 마지막 화면(components/soul-trace-flow.tsx 결과 히어로)의
스타일만 옮긴다. 그 화면에서 **실제로 적용되는** 클래스는 이것뿐이다:

    .font-ko / .font-display-en      글꼴·자간·굵기
    .result-hero-text-ko / -en       텍스트 그림자 (화면 전용)
    .drop-cap                        금박 그라데이션 첫 글자
    인라인 Tailwind                   크기·행간·색·정렬

⚠️ `.stationery-*`, `.capture-card--*`, `.letter-body-paper` 는 **어떤 컴포넌트도
   쓰지 않는다**(전 저장소 grep 으로 확인). 죽은 CSS 이므로 인쇄의 근거가 될 수
   없다. 그것을 보고 만들면 화면에 없는 종이 질감을 인쇄하게 된다.

── Living / Memorial 은 갈리지 않는다 ──────────────────────────────────────
두 갈래는 **같은 렌더러**를 쓴다. 다른 것은 편지 문장뿐이고, 그 문장은 Soul Trace
가 이미 만들어 스냅샷으로 넘겼다. 그래서 여기에는 mode 분기가 없다 —
템플릿 id 를 나누면 같은 디자인이 두 벌이 되고, 한쪽만 고쳐지는 날이 온다.

갈리는 축은 **언어 하나**다(글꼴·자간이 실제로 다르다).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Optional, Tuple

# ── 언어 ─────────────────────────────────────────────────────────────────────

#: 한글 음절 + 자모. 본문에 하나라도 있으면 한국어 편지로 본다.
_HANGUL = re.compile(r"[가-힣ᄀ-ᇿ㄰-㆏]")


def detect_language(body: str) -> str:
    """
    편지 본문 → 'ko' | 'en'.

    ── 왜 저장된 값을 쓰지 않는가 ──────────────────────────────────────────
    soul_trace_letters 에는 언어 컬럼이 없다. 스키마를 늘리면 **이미 들어온
    편지들**은 그 값이 비어 있어 결국 여기서 추론해야 하고, 추론과 컬럼이 두 벌로
    남는다. 본문 자체가 가장 확실한 근거다 — 한글이 있으면 한글 편지다.

    과거 주문도 같은 렌더러를 타고, 언어만으로 글꼴이 정해진다(요구사항 9).
    """
    return "ko" if _HANGUL.search(body or "") else "en"


# ── 웹 값 (soul-trace-flow.tsx / globals.css 에서 그대로 옮김) ───────────────

#: 본문 기준 크기. 이 값이 모든 비율의 분모다 (`text-[17px]`).
WEB_BODY_PX = 17.0

#: `leading-[2]` — 본문 행간 비율. 인쇄에서도 **그대로** 유지한다.
BODY_LINE_HEIGHT = 2.0

#: `.font-ko { letter-spacing: 0.02em }`
KO_TRACKING_EM = 0.02
#: `.font-display-en { letter-spacing: 0.3em }` — Marcellus 의 서명 같은 넓은 자간.
EN_TRACKING_EM = 0.3

#: 웹 px → 본문 대비 배율. 인쇄 크기는 이 비율에 인쇄 본문 크기를 곱해 얻는다.
RATIO_EYEBROW = 10.0 / WEB_BODY_PX      # text-[10px]
RATIO_TITLE = 30.0 / WEB_BODY_PX        # text-3xl
RATIO_SUMMARY = 15.0 / WEB_BODY_PX      # text-[15px]
RATIO_TAGS = 12.0 / WEB_BODY_PX         # text-xs
RATIO_HEADING = 14.0 / WEB_BODY_PX      # text-sm
#: `.drop-cap { font-size: clamp(3.25rem, 11vw, 4.75rem) }` — 편지 화면 폭에서는
#: 언제나 상한(4.75rem = 76px)에 걸린다.
RATIO_DROPCAP = 76.0 / WEB_BODY_PX

#: 각 요소의 행간 비율 (웹 클래스 그대로).
SUMMARY_LINE_HEIGHT = 1.9               # leading-[1.9]
TITLE_LINE_HEIGHT = 1.375               # leading-snug
#: `.drop-cap { line-height: 0.8 }`
DROPCAP_LINE_HEIGHT = 0.8
#: `.drop-cap { margin-right: 0.12em; margin-top: 0.06em }`
DROPCAP_MARGIN_RIGHT_EM = 0.12
DROPCAP_MARGIN_TOP_EM = 0.06

#: 요소별 자간 (tracking-*). em 단위.
EYEBROW_TRACKING_EM = 0.42              # tracking-[0.42em]
TAGS_TRACKING_EM = 0.16                 # tracking-[0.16em]
HEADING_TRACKING_EM = 0.08              # tracking-[0.08em]


def _rgb(hex_color: str) -> Tuple[float, float, float]:
    h = hex_color.lstrip("#")
    return tuple(int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


#: 색 — JSX 에 박힌 값 그대로.
COLOR_BODY = _rgb("#F7F4EF")            # text-[#F7F4EF]
COLOR_TITLE = _rgb("#EAD8B7")           # text-[#EAD8B7]
COLOR_SUMMARY = _rgb("#F2EFE6")         # text-[#F2EFE6]
COLOR_HEADING = _rgb("#D9C6A4")         # text-[#D9C6A4]
COLOR_TAGS = _rgb("#CDB894")            # text-[#CDB894]/72
COLOR_GOLD = _rgb("#D4AF37")            # text-[#D4AF37]/72, divider

#: 불투명도 — Tailwind 의 `/72`, `/32`.
ALPHA_TAGS = 0.72
ALPHA_EYEBROW = 0.72
ALPHA_DIVIDER = 0.32

#: `.drop-cap` 금박 그라데이션 (145deg) — 정지점까지 그대로.
DROPCAP_GRADIENT = (
    (0.00, _rgb("#a67c00")),
    (0.42, _rgb("#fff8e7")),
    (0.58, _rgb("#d4af37")),
    (1.00, _rgb("#8a6a2a")),
)

#: 히어로 **없이** 인쇄할 때의 배경(레거시 편지·복사 실패). 스크림이 수렴하는
#: 어두운 색을 불투명하게 칠한다 — 이 컬럼이 생기기 전 주문은 계속 이 결과다.
BG_GRADIENT = (
    (0.00, _rgb("#0A0B0E")),            # rgba(10,11,14,0.78)
    (0.50, _rgb("#08090B")),            # rgba(8,9,11,0.58)
    (1.00, _rgb("#07080A")),            # rgba(7,8,10,0.72)
)

# ── 히어로 위에 얹는 두 겹 (Phase 22) ───────────────────────────────────────
#
# 화면은 사진 위에 **두 번** 어둡게 한다. 한 겹만 옮기면 밝은 하늘·눈 배경에서
# 본문이 사라진다 — 글자가 읽히는 것은 두 겹이 겹친 결과다.
#
#   (위치, (색, 알파)) — 위치 0 = 페이지 상단.

#: 이미지 전체 오버레이. `from-black/[0.42] via-black/[0.14] to-black/[0.48]`
HERO_OVERLAY = (
    (0.00, (_rgb("#000000"), 0.42)),
    (0.50, (_rgb("#000000"), 0.14)),
    (1.00, (_rgb("#000000"), 0.48)),
)

#: 텍스트 영역 스크림. 화면에서는 본문 상자에만 깔리지만, A5 는 본문이 지면
#: 대부분을 차지하므로 전면에 깐다 — 상자 경계를 흉내 내면 재단에서 그 선이 보인다.
#: `from-[rgba(10,11,14,0.78)] via-[rgba(8,9,11,0.58)] to-[rgba(7,8,10,0.72)]`
TEXT_SCRIM = (
    (0.00, (_rgb("#0A0B0E"), 0.78)),
    (0.50, (_rgb("#08090B"), 0.58)),
    (1.00, (_rgb("#07080A"), 0.72)),
)


# ── 인쇄 치수 ────────────────────────────────────────────────────────────────

#: A5 본문 크기(pt). 웹의 17px 을 그대로 쓸 수 없다 — A5 는 데스크톱 뷰포트보다
#: 물리적으로 훨씬 좁아서, 같은 크기로는 한 줄에 들어가는 글자 수가 급감한다.
#: 인쇄 관례(A5 본문 10~11pt)와 행 리듬 유지 사이의 값으로 10.5pt 를 쓴다.
BODY_PT = float(os.getenv("PRINT_LETTER_BODY_PT", "10.5"))

#: 좌우 여백(mm). 웹의 측정폭(max-w-2xl ≈ 39.5em)에 최대한 가깝게 잡는다.
MARGIN_MM = float(os.getenv("PRINT_LETTER_MARGIN_MM", "16"))


def scaled_pt(ratio: float, body_pt: float = BODY_PT) -> float:
    """웹 비율 → 인쇄 pt. 계층(hierarchy)이 화면과 같은 비로 유지된다."""
    return round(ratio * body_pt, 2)


@dataclass(frozen=True)
class LetterTypography:
    """한 언어의 인쇄 조판 값 한 벌."""

    language: str
    body_font: str
    display_font: str
    body_pt: float
    tracking_em: float

    @property
    def body_leading(self) -> float:
        return self.body_pt * BODY_LINE_HEIGHT

    @property
    def body_char_space(self) -> float:
        return self.body_pt * self.tracking_em

    def size(self, ratio: float) -> float:
        return scaled_pt(ratio, self.body_pt)


# ── 폰트 ─────────────────────────────────────────────────────────────────────
#
# 원격 Google Fonts 를 쓰지 않는다(요구사항 3·4). 프로덕션은 **로컬 라이선스
# 자산**을 경로로 지정한다. 저장소에 폰트 바이너리를 넣지 않는 이유는 라이선스
# 때문이고, 이 규약은 기존 PRINT_LETTER_FONT_PATH 와 같은 방식이다.

#: 한국어 본문 — "Noto Serif KR" (.font-ko 의 1순위).
ENV_KO = "PRINT_LETTER_FONT_KO_PATH"
#: 한국어 대체 — "Nanum Myeongjo" (.font-ko 의 2순위). 실무에서 가능한 만큼 보존한다.
ENV_KO_FALLBACK = "PRINT_LETTER_FONT_KO_FALLBACK_PATH"
#: 영문 본문 — "Marcellus" (.font-display-en).
ENV_EN = "PRINT_LETTER_FONT_EN_PATH"
#: 레거시 단일 경로. 예전 배포가 이것만 갖고 있으므로 한국어 기본값으로 계속 존중한다.
ENV_LEGACY = "PRINT_LETTER_FONT_PATH"


def _first_existing(*env_names: str) -> Optional[str]:
    for name in env_names:
        p = (os.getenv(name) or "").strip()
        if p and os.path.isfile(p):
            return p
    return None


def font_path_for(language: str) -> Optional[str]:
    """
    이 언어의 임베드용 TTF 경로. 없으면 None(내장 CID 폰트로 떨어진다).

    한국어는 Noto Serif KR → Nanum Myeongjo → 레거시 경로 순으로 본다.
    `.font-ko` 의 폰트 스택과 **같은 순서**다.
    """
    if language == "ko":
        return _first_existing(ENV_KO, ENV_KO_FALLBACK, ENV_LEGACY)
    return _first_existing(ENV_EN)


def font_stack_for(language: str) -> tuple[str, ...]:
    """진단·리포트용 — 이 언어가 기대하는 폰트 스택(웹과 같은 순서)."""
    if language == "ko":
        return ("Noto Serif KR", "Nanum Myeongjo", "serif")
    return ("Marcellus", "serif")


def fonts_are_embedded(language: str) -> bool:
    """이 언어의 인쇄가 임베드 폰트를 쓰는가. 운영 점검용."""
    return font_path_for(language) is not None


def typography_for(language: str, *, body_font: str, display_font: str) -> LetterTypography:
    lang = "ko" if language == "ko" else "en"
    return LetterTypography(
        language=lang,
        body_font=body_font,
        display_font=display_font,
        body_pt=BODY_PT,
        tracking_em=KO_TRACKING_EM if lang == "ko" else EN_TRACKING_EM,
    )
