"""
인쇄물 렌더링 (Phase 13) — A5 편지 PDF + 85×55mm 카드.

**결정적이다.** 같은 입력이면 같은 바이트가 나온다(생성 시각을 넣지 않는다).
그래서 패키지 테이블이 파일을 저장하지 않고 입력만 스냅샷해도 된다 — 언제든
같은 인쇄물을 다시 뽑을 수 있다.

── 이 모듈이 만들지 않는 것 ────────────────────────────────────────────────
편지 **문장**을 만들지 않는다. 본문은 Soul Trace 스냅샷에서 그대로 온다.
빈 본문이 오면 렌더링을 거절한다 — 여백만 인쇄된 종이를 보내지 않기 위해서다.
펫도, Shaker 공유도, 생성 작업도 만들지 않는다.

── 한글 폰트 ────────────────────────────────────────────────────────────────
reportlab 내장 CJK CID 폰트(HYSMyeongJo-Medium)를 기본으로 쓴다. 폰트 파일을
저장소에 넣지 않아도 한글이 렌더된다.

⚠️ **CID 폰트는 PDF 에 임베드되지 않는다.** 화면·프리뷰에서는 완벽해 보이지만
   인쇄소 RIP 에 해당 CJK 리소스가 없으면 글자가 깨지거나 대체된다. 실제 인쇄
   전에는 PRINT_LETTER_FONT_PATH 로 라이선스된 한글 TTF 를 지정해 임베드할 것.
   지정하면 이 모듈이 자동으로 그 폰트를 등록해 쓴다.
"""

from __future__ import annotations

import io
import logging
import math
import os
import re
from dataclasses import dataclass
from typing import Optional

from . import letter_print_theme as theme

logger = logging.getLogger(__name__)

#: 85×55mm(명함 규격) @ 300dpi. 사진 카드 · QR 메모리 카드 공통.
CARD_W_MM = 85.0
CARD_H_MM = 55.0
CARD_DPI = 300
CARD_W_PX = int(round(CARD_W_MM / 25.4 * CARD_DPI))  # 1004
CARD_H_PX = int(round(CARD_H_MM / 25.4 * CARD_DPI))  # 650

#: 재단 여유(도련). 인쇄소가 잘라 내는 영역이라 중요한 것을 넣지 않는다.
BLEED_MM = 3.0

_FALLBACK_FONT = "HYSMyeongJo-Medium"


class PrintRenderError(Exception):
    def __init__(self, code: str, message: str, *, status: int = 400):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status


def _mm(v: float) -> float:
    from reportlab.lib.units import mm

    return v * mm


def letter_font_name() -> str:
    """
    본문 폰트. PRINT_LETTER_FONT_PATH 가 있으면 그 TTF 를 **임베드**해 쓴다.

    없으면 내장 CID 폰트로 떨어진다 — 렌더는 되지만 임베드되지 않는다.
    프로덕션 인쇄 전에 반드시 TTF 를 지정할 것(/readiness 가 경고한다).
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    path = (os.getenv("PRINT_LETTER_FONT_PATH") or "").strip()
    if path and os.path.isfile(path):
        name = "EBLetter"
        try:
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, path))
            return name
        except Exception:
            logger.warning("한글 TTF 등록 실패 — 내장 CID 폰트로 진행한다 (%s)", path)

    if _FALLBACK_FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(_FALLBACK_FONT))
    return _FALLBACK_FONT


def font_is_embedded(language: Optional[str] = None) -> bool:
    """
    이 언어의 인쇄가 **임베드된 TTF** 를 쓰는가. 운영 점검용.

    language 를 주지 않으면 두 언어가 **모두** 준비됐을 때만 True 다 — 한쪽만
    설정된 배포에서 "임베드됨"으로 보고하면, 그 반대 언어 편지가 임베드되지 않은
    채 인쇄소로 나간다.
    """
    if language in ("ko", "en"):
        return theme.fonts_are_embedded(language)
    return theme.fonts_are_embedded("ko") and theme.fonts_are_embedded("en")


def font_report() -> dict:
    """언어별 폰트 준비 상태 — manifest·운영 화면이 그대로 싣는다."""
    return {
        lang: {
            "expected_stack": list(theme.font_stack_for(lang)),
            "local_path": theme.font_path_for(lang),
            "embedded": theme.fonts_are_embedded(lang),
        }
        for lang in ("ko", "en")
    }


def wrap_korean(
    text: str, font: str, size: float, max_width: float, *, char_space: float = 0.0
) -> list[str]:
    """
    한글 줄바꿈. **글자 단위**로 자른다.

    공백 단위로만 자르면 한국어에서 거의 동작하지 않는다 — 한 어절이 줄 폭을
    넘으면 그대로 넘쳐 재단선 밖으로 나간다. 영어 단어는 가능한 한 붙여 둔다.

    char_space 는 자간(letter-spacing)이다. 영문 편지는 Marcellus 를 0.3em 으로
    조판하는데(`.font-display-en`), 그 폭을 무시하면 줄이 재단선 밖으로 나간다 —
    한 줄에 들어가는 글자 수가 실제보다 30% 많게 계산되기 때문이다.

    빈 줄(문단 사이)은 그대로 보존한다 — `whitespace-pre-line` 과 같은 동작이고,
    편지의 문단 구조가 인쇄에서도 유지되는 근거다.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    def _w(sample: str) -> float:
        return stringWidth(sample, font, size) + char_space * max(0, len(sample) - 1)

    lines: list[str] = []
    for para in (text or "").split("\n"):
        if not para.strip():
            lines.append("")
            continue
        cur = ""
        for ch in para:
            trial = cur + ch
            if _w(trial) <= max_width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
    return lines


def wrap_with_hanging_indent(
    text: str,
    font: str,
    size: float,
    *,
    full_width: float,
    first_width: float,
    first_lines: int,
    char_space: float = 0.0,
) -> list[tuple[str, bool]]:
    """
    첫 N 줄만 좁은 폭으로 접는다 — `float: left` 한 드롭캡을 인쇄로 옮긴 것.

    돌려주는 값은 `(줄, 들여쓸 것인가)`.

    ── 왜 '잘라서 이어 붙이기' 로는 안 되는가 ──────────────────────────────
    처음에는 좁은 폭으로 한 번 접고, 소비한 글자 수만큼 원문을 잘라 나머지를 다시
    접었다. 그런데 줄바꿈 함수는 문단 구분자(newline)를 **결과에 남기지 않는다.**
    그래서 join 한 문자열이 원문의 접두사와 길이가 달랐고, 자른 위치가 문단
    개수만큼 밀렸다 — 실측에서 세 번째 줄 앞에 공백이 새고 글자가 밀렸다.

    한 번에 걸어가면서 줄 번호에 따라 폭만 바꾸면 그 오차가 아예 생기지 않는다.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    def _w(sample: str) -> float:
        return stringWidth(sample, font, size) + char_space * max(0, len(sample) - 1)

    out: list[tuple[str, bool]] = []

    def _limit() -> tuple[float, bool]:
        indented = len(out) < first_lines
        return (first_width if indented else full_width), indented

    def _emit(line: str) -> None:
        _, indented = _limit()
        out.append((line, indented))

    for para in (text or "").split("\n"):
        if not para.strip():
            _emit("")
            continue

        # ── 어절 단위로 접는다 (`word-break: keep-all`) ──────────────────
        # 화면은 한국어에 `break-keep` 을 걸고 영어에는 기본 워드랩을 쓴다 —
        # 둘 다 **공백에서만** 줄이 바뀐다는 뜻이다. 글자 단위로 접으면
        # "발소리를" 이 "발소리|를" 로, "footsteps" 가 "foots|teps" 로 갈라져
        # 화면과 줄 리듬이 완전히 달라진다(실측으로 드러난 차이다).
        tokens = re.findall(r"\S+\s*", para)
        cur = ""
        for tok in tokens:
            width, _ = _limit()
            trial = cur + tok
            if _w(trial.rstrip()) <= width or not cur.strip():
                cur = trial
                # 한 어절이 줄 폭보다 길면(긴 URL·합성어) 그때만 강제로 자른다.
                while _w(cur.rstrip()) > width and len(cur.strip()) > 1:
                    keep = cur
                    while keep and _w(keep) > width:
                        keep = keep[:-1]
                    if not keep:
                        break
                    _emit(keep)
                    cur = cur[len(keep):]
                    width, _ = _limit()
            else:
                _emit(cur.rstrip())
                cur = tok
        if cur.strip():
            _emit(cur.rstrip())
    return out


@dataclass(frozen=True)
class LetterContent:
    """인쇄할 편지. **전부 밖에서 온다.**"""

    body: str
    child_name: Optional[str] = None
    kicker: Optional[str] = None


def _letter_fonts(language: str) -> tuple[str, str]:
    """
    (본문 폰트, 디스플레이 폰트) 등록 이름.

    두 이름을 나누는 이유: 한국어 편지에서도 아이브로우(kicker)는 웹에서
    `.font-display-en` 을 쓴다(대문자 + 넓은 자간). 실제 화면이 그렇게 생겼다.
    """
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont

    def _register(path: Optional[str], name: str) -> Optional[str]:
        if not path:
            return None
        try:
            if name not in pdfmetrics.getRegisteredFontNames():
                pdfmetrics.registerFont(TTFont(name, path))
            return name
        except Exception:
            logger.warning("인쇄 폰트 등록 실패 — 대체 폰트로 진행 (%s)", path)
            return None

    body = _register(theme.font_path_for(language), f"EBLetter-{language}")
    display = _register(theme.font_path_for("en"), "EBLetter-en") or body

    if body is None:
        # 내장 CID 로 떨어진다. 렌더는 되지만 **임베드되지 않는다** —
        # 인쇄소 RIP 에 해당 리소스가 없으면 글자가 대체된다.
        if _FALLBACK_FONT not in pdfmetrics.getRegisteredFontNames():
            pdfmetrics.registerFont(UnicodeCIDFont(_FALLBACK_FONT))
        body = _FALLBACK_FONT
    return body, (display or body)


def _fill(c, rgb: tuple, alpha: float = 1.0) -> None:
    c.setFillColorRGB(*rgb)
    c.setFillAlpha(alpha)


def composite_letter_background(
    hero: bytes, width_pt: float, height_pt: float, dpi: int = 300
) -> Optional[bytes]:
    """
    히어로 사진 + 화면과 **같은 두 겹의 스크림** → 불투명 JPEG 한 장.

    ── 왜 PDF 그라데이션이 아니라 픽셀 합성인가 ────────────────────────────
    PDF 에는 **정지점별 알파가 없다.** 알파는 그래픽 상태라 한 번에 하나뿐이다.
    그래서 처음에는 페이지를 가로 띠로 잘라 띠마다 알파를 조금씩 다르게 칠했다.
    두 번 실패했다:
      1) 띠가 적으면 알파가 툭 끊겨 지면 한가운데에 가로줄이 보였다
      2) 촘촘하게 나누니 띠 경계에 **머리카락 같은 틈**이 생겨 밑바탕이 새어
         나왔다 — 흰 배경 위에서 밝기 차 131까지 측정됐다
    두 실패 모두 같은 뿌리다: 반투명 사각형을 이어 붙여 연속 그라데이션을
    흉내 내는 것 자체가 무리다.

    브라우저는 이런 일을 하지 않는다 — 픽셀마다 알파를 곱해 합성한다.
    여기서도 그렇게 한다. 수학적으로 정확하고, 띠도 틈도 없고, 결과는 알파가
    없는 평범한 JPEG 이라 PDF 가 그대로 그릴 수 있다.
    """
    try:
        from PIL import Image
    except ImportError:
        return None

    try:
        target_w = max(1, int(round(width_pt / 72.0 * dpi)))
        target_h = max(1, int(round(height_pt / 72.0 * dpi)))

        im = Image.open(io.BytesIO(hero))
        im = im.convert("RGB")

        # 전면 채우기(cover) — contain 은 재단선 안에 흰 띠를 남긴다.
        ratio = max(target_w / im.width, target_h / im.height)
        im = im.resize(
            (max(1, int(round(im.width * ratio))), max(1, int(round(im.height * ratio)))),
            Image.Resampling.LANCZOS,
        )
        left = (im.width - target_w) // 2
        top = (im.height - target_h) // 2
        im = im.crop((left, top, left + target_w, top + target_h))

        im = _apply_scrims(im, (theme.HERO_OVERLAY, theme.TEXT_SCRIM))

        out = io.BytesIO()
        im.save(out, format="JPEG", quality=92)
        return out.getvalue()
    except Exception:
        logger.warning("편지 배경 합성 실패 — 스크림 폴백", exc_info=True)
        return None


def _scrim_columns(h: int, stops):
    """행마다의 (색, 알파) — 화면 CSS 그라데이션과 같은 선형 보간."""
    rows = []
    for y in range(h):
        t = y / max(1, h - 1)
        for k in range(len(stops) - 1):
            p0, (c0, a0) = stops[k]
            p1, (c1, a1) = stops[k + 1]
            if t <= p1 or k == len(stops) - 2:
                span = (p1 - p0) or 1.0
                u = min(1.0, max(0.0, (t - p0) / span))
                rows.append((
                    tuple(c0[i] + (c1[i] - c0[i]) * u for i in range(3)),
                    a0 + (a1 - a0) * u,
                ))
                break
    return rows


def _apply_scrims(im, layers):
    """
    여러 겹의 세로 스크림을 이미지에 곱해 넣는다. `out = src·(1-a) + overlay·a`.

    numpy 가 있으면 벡터 연산으로 한 번에 처리한다 — A5 300dpi 는 430만 픽셀이고,
    픽셀마다 파이썬 루프를 돌면 한 장에 4초쯤 걸린다. 운영자가 편지를 내려받을
    때마다 그만큼 기다리게 할 이유가 없다. numpy 가 없으면 느리지만 같은 결과를
    내는 순수 PIL 경로로 떨어진다.
    """
    w, h = im.size
    try:
        import numpy as np

        arr = np.asarray(im, dtype=np.float32)
        for stops in layers:
            rows = _scrim_columns(h, stops)
            colors = np.array([r[0] for r in rows], dtype=np.float32) * 255.0  # (h,3)
            alphas = np.array([r[1] for r in rows], dtype=np.float32)[:, None]  # (h,1)
            arr = arr * (1.0 - alphas)[:, :, None] + (colors * alphas)[:, None, :]
        from PIL import Image

        return Image.fromarray(np.clip(arr, 0, 255).astype("uint8"), "RGB")
    except ImportError:
        px = im.load()
        for stops in layers:
            rows = _scrim_columns(h, stops)
            for y in range(h):
                (r0, g0, b0), a = rows[y]
                if a <= 0:
                    continue
                inv = 1.0 - a
                orr, org, orb = r0 * 255.0 * a, g0 * 255.0 * a, b0 * 255.0 * a
                for x in range(w):
                    r, g, b = px[x, y]
                    px[x, y] = (int(r * inv + orr), int(g * inv + org), int(b * inv + orb))
        return im


def _draw_background(
    c, width: float, height: float, hero: Optional[bytes] = None
) -> None:
    """
    화면과 **같은 배경**: 히어로 사진 → 그 위에 정확한 스크림 두 겹.

    화면은 사진 위에 두 번 어둡게 한다:
      1) 이미지 전체 오버레이  from-black/.42 via-black/.14 to-black/.48
      2) 텍스트 영역 스크림    rgba(10,11,14,.78) → (8,9,11,.58) → (7,8,10,.72)
    한 겹만 옮기면 밝은 하늘 부분에서 본문이 사라진다 — 글자가 읽히는 것은 두
    겹이 겹친 결과다.

    히어로가 없으면(레거시 편지·복사 실패) 예전처럼 스크림 색만 칠한다 —
    그때의 인쇄물과 같은 결과가 나온다.
    """
    c.saveState()

    if hero:
        composed = composite_letter_background(hero, width, height)
        if composed:
            try:
                from reportlab.lib.utils import ImageReader

                # 이미 페이지 비율로 잘라 두었으므로 그대로 채운다.
                c.drawImage(
                    ImageReader(io.BytesIO(composed)), 0, 0, width=width, height=height
                )
                c.restoreState()
                return
            except Exception:
                logger.warning("편지 배경을 그리지 못했다 — 스크림으로 진행", exc_info=True)

    stops = theme.BG_GRADIENT
    c.linearGradient(
        0, height, 0, 0,
        [rgb for _, rgb in stops],
        positions=[pos for pos, _ in stops],
        extend=True,
    )
    c.restoreState()


def _draw_centered(c, text: str, *, font: str, size: float, y: float,
                   width: float, rgb: tuple, alpha: float = 1.0,
                   tracking_em: float = 0.0, upper: bool = False) -> None:
    """가운데 정렬 한 줄. 자간(tracking)까지 화면과 같게 맞춘다."""
    from reportlab.pdfbase.pdfmetrics import stringWidth

    s = (text or "").strip()
    if not s:
        return
    if upper:
        s = s.upper()
    char_space = size * tracking_em
    # 자간은 **글자 사이**에만 들어간다. 마지막 글자 뒤의 여분을 빼야 가운데가 맞는다.
    w = stringWidth(s, font, size) + char_space * max(0, len(s) - 1)
    t = c.beginText((width - w) / 2.0, y)
    t.setFont(font, size)
    if char_space:
        t.setCharSpace(char_space)
    _fill(c, rgb, alpha)
    t.textOut(s)
    c.drawText(t)
    c.setFillAlpha(1.0)


def _draw_divider(c, *, y: float, width: float, max_w: float) -> None:
    """
    금색 헤어라인. 화면은 양끝이 투명으로 사라지는 그라데이션이다
    (`from-transparent via-[#D4AF37]/32 to-transparent`).
    """
    x0 = (width - max_w) / 2.0
    c.saveState()
    p = c.beginPath()
    p.rect(x0, y, max_w, 0.5)
    c.clipPath(p, stroke=0, fill=0)
    # 양끝은 **투명**이다(`from-transparent ... to-transparent`). PDF 그라데이션에는
    # 정지점별 알파가 없으므로, 어두운 배경 위에서 같은 결과가 나오도록 끝을
    # 배경색으로 두고 가운데를 금색 32% 로 합성한다.
    bg = theme.BG_GRADIENT[1][1]
    gold = theme.COLOR_GOLD
    mid = tuple(gold[i] * theme.ALPHA_DIVIDER + bg[i] * (1 - theme.ALPHA_DIVIDER) for i in range(3))
    c.linearGradient(
        x0, y, x0 + max_w, y,
        [bg, mid, bg],
        positions=[0.0, 0.5, 1.0],
        extend=True,
    )
    c.restoreState()


def _draw_drop_cap(c, ch: str, *, font: str, size: float, x: float, baseline: float) -> float:
    """
    금박 그라데이션 첫 글자. **글자 모양으로 클립한 뒤** 그라데이션을 칠한다 —
    `.drop-cap` 의 `background-clip: text` 를 PDF 로 옮긴 것이다.

    돌려주는 값은 글자 폭(자간·오른쪽 여백 제외).
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    w = stringWidth(ch, font, size)
    c.saveState()
    t = c.beginText(x, baseline)
    t.setFont(font, size)
    t.setTextRenderMode(7)  # 7 = 그리지 않고 클립 경로에 더한다
    t.textOut(ch)
    c.drawText(t)
    stops = theme.DROPCAP_GRADIENT
    # 145deg — 좌상 → 우하. 글자 상자를 대각으로 가로지른다.
    c.linearGradient(
        x, baseline + size * theme.DROPCAP_LINE_HEIGHT, x + w, baseline,
        [rgb for _, rgb in stops],
        positions=[pos for pos, _ in stops],
        extend=True,
    )
    c.restoreState()
    return w


def render_letter_pdf(
    content: LetterContent,
    *,
    order_id: str,
    qr_url: Optional[str] = None,
    qr_png: Optional[bytes] = None,
    language: Optional[str] = None,
    background: Optional[bytes] = None,
) -> bytes:
    """
    A5 편지 PDF (148×210mm) — **Soul Trace 최종 화면과 같은 디자인.**

    ── 템플릿은 하나다 ─────────────────────────────────────────────────────
    LETTER 와 MEMORY_BOX 가 같은 함수를 쓴다. Living/Memorial 도 갈리지 않는다 —
    두 갈래는 화면에서도 **같은 렌더러**를 쓰고 문장만 다르며, 그 문장은 이미
    스냅샷으로 넘어왔다. 갈리는 축은 언어 하나뿐이다(글꼴·자간이 실제로 다르다).

    language 를 넘기지 않으면 본문에서 판정한다 — 과거 주문도 같은 규칙으로
    같은 렌더러를 탄다.

    background 는 편지의 히어로 이미지 바이트다. 없으면(레거시 편지·복사 실패)
    예전처럼 어두운 스크림만 칠한다 — 그때의 인쇄물과 같은 결과다.
    """
    body = (content.body or "").strip()
    if not body:
        # 여백만 인쇄된 종이를 보내지 않는다.
        raise PrintRenderError("LETTER_BODY_EMPTY", "편지 본문이 비어 있습니다.", status=409)

    from reportlab.lib.pagesizes import A5
    from reportlab.pdfgen import canvas

    lang = language if language in ("ko", "en") else theme.detect_language(body)
    body_font, display_font = _letter_fonts(lang)
    typo = theme.typography_for(lang, body_font=body_font, display_font=display_font)

    buf = io.BytesIO()
    # invariant=1 이 **결정성의 핵심**이다. 기본값에서는 reportlab 이 /CreationDate 와
    # 문서 ID 를 매번 새로 박아, 같은 편지를 두 번 렌더하면 바이트가 달라진다.
    c = canvas.Canvas(buf, pagesize=A5, invariant=1)
    c.setTitle(f"Eternal Beam Letter {order_id}")
    c.setAuthor("Eternal Beam")

    width, height = A5
    margin = _mm(theme.MARGIN_MM)
    text_width = width - margin * 2

    _draw_background(c, width, height, background)

    y = height - margin - _mm(4)

    # ── 아이브로우 (kicker) — 화면은 .font-display-en + 대문자 + 0.42em ────
    if content.kicker:
        size = typo.size(theme.RATIO_EYEBROW)
        _draw_centered(
            c, content.kicker, font=display_font, size=size, y=y, width=width,
            rgb=theme.COLOR_GOLD, alpha=theme.ALPHA_EYEBROW,
            tracking_em=theme.EYEBROW_TRACKING_EM, upper=True,
        )
        # space-y-5(20px) 를 본문 비율로 옮기고, 제목의 대문자 높이를 더한다.
        y -= typo.body_pt * (20.0 / theme.WEB_BODY_PX) + typo.size(theme.RATIO_TITLE) * 0.82

    # ── 제목 (아이 이름) — 화면 h1 자리 ───────────────────────────────────
    if content.child_name:
        size = typo.size(theme.RATIO_TITLE)
        _draw_centered(
            c, content.child_name, font=body_font, size=size, y=y, width=width,
            rgb=theme.COLOR_TITLE, tracking_em=typo.tracking_em,
        )
        y -= size * theme.TITLE_LINE_HEIGHT

    # ── 금색 헤어라인 (max-w-xl) ──────────────────────────────────────────
    y -= _mm(4)
    _draw_divider(c, y=y, width=width, max_w=text_width * 0.86)
    y -= _mm(9)

    # ── 본문 ───────────────────────────────────────────────────────────────
    size = typo.body_pt
    leading = typo.body_leading
    char_space = typo.body_char_space
    bottom_limit = margin + _mm(34) if (qr_png or qr_url) else margin + _mm(6)

    cap_size = typo.size(theme.RATIO_DROPCAP)
    cap_char = body[0] if body[:1].strip() else ""
    rest = body[1:] if cap_char else body
    # `float: left` 를 인쇄로 옮긴다 — 첫 글자가 차지하는 높이만큼의 줄을 들여쓴다.
    cap_height = cap_size * theme.DROPCAP_LINE_HEIGHT
    indent_lines = int(math.ceil(cap_height / leading)) if cap_char else 0

    cap_advance = 0.0
    if cap_char:
        from reportlab.pdfbase.pdfmetrics import stringWidth

        cap_advance = stringWidth(cap_char, body_font, cap_size) + cap_size * theme.DROPCAP_MARGIN_RIGHT_EM

    wrapped = wrap_with_hanging_indent(
        rest,
        body_font,
        size,
        full_width=text_width,
        first_width=text_width - cap_advance,
        first_lines=indent_lines,
        char_space=char_space,
    )
    lines = [(ln, cap_advance if ind else 0.0) for ln, ind in wrapped]

    first_baseline = y - size
    if cap_char:
        # margin-top: 0.06em — 화면과 같은 만큼 아래로 민다.
        cap_baseline = first_baseline - (cap_height - size) + cap_size * theme.DROPCAP_MARGIN_TOP_EM
        _draw_drop_cap(
            c, cap_char, font=body_font, size=cap_size, x=margin, baseline=cap_baseline
        )

    y = first_baseline
    for line, indent in lines:
        if y < bottom_limit:
            c.showPage()
            _draw_background(c, width, height, background)
            y = height - margin - size
        t = c.beginText(margin + indent, y)
        t.setFont(body_font, size)
        if char_space:
            t.setCharSpace(char_space)
        _fill(c, theme.COLOR_BODY)
        t.textOut(line)
        c.drawText(t)
        y -= leading

    # 보관된 산출물(qr_png)이 있으면 **그것을 쓴다.** 다시 렌더하지 않는 이유:
    # 이미 인쇄된 QR 과 같은 바이트여야 하기 때문이다(Phase 13.1).
    if qr_png or qr_url:
        _draw_qr(c, qr_url, x=margin, y=margin + _mm(2), size=_mm(24), qr_png=qr_png)
        cap = typo.size(theme.RATIO_TAGS)
        c.setFont(body_font, cap)
        _fill(c, theme.COLOR_TAGS, theme.ALPHA_TAGS)
        if lang == "ko":
            c.drawString(margin + _mm(28), margin + _mm(12), "휴대폰으로 스캔하면")
            c.drawString(margin + _mm(28), margin + _mm(7.5), "아이를 다시 만날 수 있어요.")
        else:
            c.drawString(margin + _mm(28), margin + _mm(12), "Scan with your phone")
            c.drawString(margin + _mm(28), margin + _mm(7.5), "to meet them again.")
        c.setFillAlpha(1.0)

    c.showPage()
    c.save()
    return buf.getvalue()


def _draw_qr(
    c, url: Optional[str], *, x: float, y: float, size: float, qr_png: Optional[bytes] = None
) -> None:
    """
    QR 을 PDF 에 그린다.

    qr_png 가 있으면 **그것을 그대로 쓴다** — 보관된 산출물이며, 이미 인쇄된
    QR 과 같은 바이트여야 한다(Phase 13.1).

    URL 로 렌더할 때는 Shaker URL 만 받는다 — qr_service 가 스토리지/영상 주소를
    거절한다. 인쇄된 QR 은 회수할 수 없으므로 여기서도 같은 규칙을 통과시킨다.
    """
    from reportlab.lib.utils import ImageReader

    from . import qr_service

    data = qr_png
    if data is None:
        data = qr_service.render_qr(url or "", kind="png", scale=8, border=1).data
    c.drawImage(
        ImageReader(io.BytesIO(data)), x, y, width=size, height=size, mask="auto"
    )


def _card_canvas(bg: tuple[int, int, int] = (255, 255, 255)):
    from PIL import Image

    return Image.new("RGB", (CARD_W_PX, CARD_H_PX), bg)


def render_qr_card_png(
    qr_url: Optional[str] = None,
    *,
    pet_name: Optional[str] = None,
    qr_png: Optional[bytes] = None,
) -> bytes:
    """
    85×55mm QR 메모리 카드 (300dpi PNG).

    QR 을 카드 중앙 왼쪽에 크게 배치한다 — 인쇄 후 스캔 성공률이 크기에 가장
    민감하다. 문구는 오른쪽에 작게 둔다.
    """
    from PIL import Image

    from . import qr_service

    # 보관된 산출물이 있으면 그것을 쓴다 — 이미 인쇄된 QR 과 동일해야 한다.
    if qr_png is not None:
        raw = qr_png
    else:
        raw = qr_service.render_qr(qr_url or "", kind="png", scale=12, border=2).data
    qr = Image.open(io.BytesIO(raw)).convert("RGB")

    card = _card_canvas()
    # 카드 높이의 76% 를 QR 에 준다. 재단 여유를 남기고도 스캔에 충분하다.
    qr_size = int(CARD_H_PX * 0.76)
    qr = qr.resize((qr_size, qr_size), Image.LANCZOS)
    left = int(CARD_W_PX * 0.06)
    top = (CARD_H_PX - qr_size) // 2
    card.paste(qr, (left, top))

    _draw_card_caption(card, x=left + qr_size + int(CARD_W_PX * 0.05), pet_name=pet_name)

    out = io.BytesIO()
    card.save(out, format="PNG", dpi=(CARD_DPI, CARD_DPI))
    return out.getvalue()


def _draw_card_caption(card, *, x: int, pet_name: Optional[str]) -> None:
    """
    카드 문구. **폰트가 없으면 조용히 생략한다.**

    PIL 기본 폰트는 한글을 그리지 못한다(네모로 나온다). 네모를 인쇄하느니
    문구 없이 QR 만 있는 카드가 낫다 — QR 이 카드의 본체이기 때문이다.
    """
    from PIL import ImageDraw, ImageFont

    path = (os.getenv("PRINT_CARD_FONT_PATH") or os.getenv("PRINT_LETTER_FONT_PATH") or "").strip()
    if not path or not os.path.isfile(path):
        return

    try:
        title = ImageFont.truetype(path, 46)
        small = ImageFont.truetype(path, 30)
    except Exception:
        return

    d = ImageDraw.Draw(card)
    y = int(CARD_H_PX * 0.36)
    if pet_name:
        d.text((x, y), pet_name, font=title, fill=(30, 30, 30))
        y += 62
    d.text((x, y), "스캔하면 만날 수 있어요", font=small, fill=(110, 110, 110))


def render_photo_card_png(image_bytes: bytes, *, pet_name: Optional[str] = None) -> bytes:
    """
    85×55mm 반려 사진 카드 (300dpi PNG).

    사진을 카드 비율에 맞춰 **잘라 채운다**(contain 이 아니라 cover). 여백을 남기면
    카드가 사진이 아니라 액자처럼 보이고, 재단 오차에서 흰 줄이 생긴다.
    """
    from PIL import Image, ImageOps

    if not image_bytes:
        raise PrintRenderError("PHOTO_MISSING", "사진 이미지가 없습니다.", status=409)

    try:
        src = Image.open(io.BytesIO(image_bytes))
        # 투명 PNG(누끼)는 흰 배경 위에 올린다 — 인쇄에 알파가 없다.
        if src.mode in ("RGBA", "LA", "P"):
            src = src.convert("RGBA")
            bg = Image.new("RGB", src.size, (255, 255, 255))
            bg.paste(src, mask=src.split()[-1])
            src = bg
        else:
            src = src.convert("RGB")
    except PrintRenderError:
        raise
    except Exception as e:
        raise PrintRenderError("PHOTO_UNREADABLE", "사진을 읽을 수 없습니다.", status=409) from e

    card = ImageOps.fit(
        src, (CARD_W_PX, CARD_H_PX), method=Image.LANCZOS, centering=(0.5, 0.4)
    )
    out = io.BytesIO()
    card.save(out, format="PNG", dpi=(CARD_DPI, CARD_DPI))
    return out.getvalue()


# ── 메시지 카드 (Phase 17) ────────────────────────────────────────────────────
#
# MEMORY BOX 구성품에 message_card 가 **선언만** 돼 있고 렌더러가 없었다.
#
# ⚠️ **문구는 아직 승인되지 않았다.** 여기서 지어내지 않는다 — 메시지 카드는
#    상자를 연 사람이 가장 먼저 읽는 문장이고, 그 톤은 제품 결정이지 구현
#    세부가 아니다. 엉뚱한 위로의 말이 인쇄되어 배송되면 회수할 수 없다.
#
# 그래서 지금 만드는 것은 **구조뿐**이다:
#   * 승인된 문구가 설정되면(PRINT_MESSAGE_CARD_TEXT) 그것을 조판해 인쇄한다
#   * 설정되지 않았으면 눈에 띄게 "TBD" 라고 적힌 교정지를 낸다
#
# 교정지는 패키지 ZIP 에 **들어가지 않는다**(production_package.manifest 참고).
# 인쇄소로 넘어가는 한 덩어리에 자리표시자가 섞이면, 언젠가 그대로 찍힌다.

#: 승인된 문구를 넣는 자리. 줄바꿈은 `\n`.
MESSAGE_CARD_ENV = "PRINT_MESSAGE_CARD_TEXT"


def message_card_text() -> Optional[str]:
    """승인된 메시지 카드 문구. 없으면 None — 그러면 카드는 아직 TBD 다."""
    return (os.getenv(MESSAGE_CARD_ENV) or "").strip() or None


def message_card_approved() -> bool:
    return message_card_text() is not None


def _wrap_pil(text: str, font, max_width: float) -> list[str]:
    """
    PIL 폰트 기준 글자 단위 줄바꿈.

    wrap_korean 과 규칙은 같지만 폭을 **실제로 그릴 폰트**로 잰다. 한글은 어절이
    길어 공백 단위로만 자르면 재단선 밖으로 넘친다.
    """
    lines: list[str] = []
    for para in (text or "").split("\n"):
        if not para.strip():
            lines.append("")
            continue
        cur = ""
        for ch in para:
            trial = cur + ch
            if font.getlength(trial) <= max_width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
    return lines


def render_message_card_png(*, pet_name: Optional[str] = None) -> bytes:
    """
    85×55mm 메시지 카드 (300dpi PNG).

    승인 문구가 있으면 그것을, 없으면 **자리표시자 교정지**를 낸다. 교정지는
    한눈에 교정지로 보여야 한다 — 예쁘게 만들면 승인된 art 로 오인된다.
    """
    from PIL import ImageDraw, ImageFont

    approved = message_card_text()
    card = _card_canvas()
    d = ImageDraw.Draw(card)

    path = (os.getenv("PRINT_CARD_FONT_PATH") or os.getenv("PRINT_LETTER_FONT_PATH") or "").strip()
    font_ok = bool(path) and os.path.isfile(path)

    if approved is None:
        # 교정지: 테두리 + 대문자 라틴 문자(폰트 없이도 읽힌다) + 규격 표기.
        d.rectangle(
            [(8, 8), (CARD_W_PX - 9, CARD_H_PX - 9)], outline=(200, 200, 200), width=4
        )
        try:
            big = ImageFont.truetype(path, 54) if font_ok else ImageFont.load_default()
            small = ImageFont.truetype(path, 28) if font_ok else ImageFont.load_default()
        except Exception:
            big = small = ImageFont.load_default()
        # ASCII 만 쓴다 — PIL 기본 폰트는 한글을 네모로 그린다.
        d.text((60, 190), "MESSAGE CARD", font=big, fill=(120, 120, 120))
        d.text((60, 270), "COPY / DESIGN: TBD", font=big, fill=(190, 90, 90))
        d.text(
            (60, 360),
            f"85 x 55 mm @ {CARD_DPI}dpi - not for print",
            font=small,
            fill=(150, 150, 150),
        )
        out = io.BytesIO()
        card.save(out, format="PNG", dpi=(CARD_DPI, CARD_DPI))
        return out.getvalue()

    if not font_ok:
        # 승인 문구는 대개 한글이다. 폰트가 없으면 네모가 인쇄되므로 만들지 않는다.
        raise PrintRenderError(
            "CARD_FONT_MISSING",
            "메시지 카드 문구를 조판할 폰트가 없습니다 (PRINT_CARD_FONT_PATH).",
            status=503,
        )

    body = ImageFont.truetype(path, 40)
    name_font = ImageFont.truetype(path, 34)

    text = approved.replace("{pet_name}", (pet_name or "").strip())
    # wrap_korean 은 reportlab 의 **등록된 폰트 이름**으로 폭을 재므로 PIL 카드에는
    # 쓸 수 없다. 여기서는 실제로 그릴 폰트 객체로 재야 줄이 정확히 맞는다.
    lines = _wrap_pil(text, body, CARD_W_PX - 160)

    y = max(70, (CARD_H_PX - len(lines) * 58) // 2)
    for line in lines:
        d.text((80, y), line, font=body, fill=(40, 40, 40))
        y += 58

    if pet_name:
        d.text((80, CARD_H_PX - 96), pet_name, font=name_font, fill=(130, 130, 130))

    out = io.BytesIO()
    card.save(out, format="PNG", dpi=(CARD_DPI, CARD_DPI))
    return out.getvalue()


@dataclass(frozen=True)
class RenderedFile:
    kind: str
    filename: str
    content_type: str
    data: bytes
