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
import os
from dataclasses import dataclass
from typing import Optional

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


def font_is_embedded() -> bool:
    """임베드된 TTF 를 쓰고 있는가. 운영 점검용."""
    path = (os.getenv("PRINT_LETTER_FONT_PATH") or "").strip()
    return bool(path and os.path.isfile(path))


def wrap_korean(text: str, font: str, size: float, max_width: float) -> list[str]:
    """
    한글 줄바꿈. **글자 단위**로 자른다.

    공백 단위로만 자르면 한국어에서 거의 동작하지 않는다 — 한 어절이 줄 폭을
    넘으면 그대로 넘쳐 재단선 밖으로 나간다. 영어 단어는 가능한 한 붙여 둔다.
    """
    from reportlab.pdfbase.pdfmetrics import stringWidth

    lines: list[str] = []
    for para in (text or "").split("\n"):
        if not para.strip():
            lines.append("")
            continue
        cur = ""
        for ch in para:
            trial = cur + ch
            if stringWidth(trial, font, size) <= max_width or not cur:
                cur = trial
            else:
                lines.append(cur)
                cur = ch
        if cur:
            lines.append(cur)
    return lines


@dataclass(frozen=True)
class LetterContent:
    """인쇄할 편지. **전부 밖에서 온다.**"""

    body: str
    child_name: Optional[str] = None
    kicker: Optional[str] = None


def render_letter_pdf(
    content: LetterContent,
    *,
    order_id: str,
    qr_url: Optional[str] = None,
    qr_png: Optional[bytes] = None,
) -> bytes:
    """
    A5 편지 PDF (148×210mm).

    QR 을 편지 하단에 넣는다 — 편지 상품의 QR 이 여기다(별도 카드가 없다).
    """
    body = (content.body or "").strip()
    if not body:
        # 여백만 인쇄된 종이를 보내지 않는다.
        raise PrintRenderError("LETTER_BODY_EMPTY", "편지 본문이 비어 있습니다.", status=409)

    from reportlab.lib.pagesizes import A5
    from reportlab.pdfgen import canvas

    font = letter_font_name()
    buf = io.BytesIO()
    # invariant=1 이 **결정성의 핵심**이다. 기본값에서는 reportlab 이 /CreationDate 와
    # 문서 ID 를 매번 새로 박아, 같은 편지를 두 번 렌더하면 바이트가 달라진다.
    # 그러면 "파일을 저장하지 않고 입력만 스냅샷한다"는 설계 전제가 무너지고,
    # "재출력했더니 달라졌다"를 진단할 수 없다. (실측으로 드러난 결함이다.)
    c = canvas.Canvas(buf, pagesize=A5, invariant=1)
    c.setTitle(f"Eternal Beam Letter {order_id}")
    c.setAuthor("Eternal Beam")

    width, height = A5
    margin = _mm(18)
    text_width = width - margin * 2
    y = height - margin - _mm(6)

    if content.kicker:
        c.setFont(font, 9)
        c.setFillGray(0.45)
        c.drawString(margin, y, content.kicker.strip())
        y -= _mm(8)

    if content.child_name:
        c.setFont(font, 16)
        c.setFillGray(0.1)
        c.drawString(margin, y, content.child_name.strip())
        y -= _mm(12)

    c.setFont(font, 11)
    c.setFillGray(0.15)
    leading = _mm(6.4)
    bottom_limit = margin + _mm(34)  # QR 자리를 남긴다
    for line in wrap_korean(body, font, 11, text_width):
        if y < bottom_limit:
            c.showPage()
            c.setFont(font, 11)
            c.setFillGray(0.15)
            y = height - margin
        c.drawString(margin, y, line)
        y -= leading

    # 보관된 산출물(qr_png)이 있으면 **그것을 쓴다.** 다시 렌더하지 않는 이유:
    # 이미 인쇄된 QR 과 같은 바이트여야 하기 때문이다(Phase 13.1).
    if qr_png or qr_url:
        _draw_qr(c, qr_url, x=margin, y=margin + _mm(2), size=_mm(24), qr_png=qr_png)
        c.setFont(font, 7.5)
        c.setFillGray(0.5)
        c.drawString(margin + _mm(28), margin + _mm(12), "휴대폰으로 스캔하면")
        c.drawString(margin + _mm(28), margin + _mm(7.5), "아이를 다시 만날 수 있어요.")

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


@dataclass(frozen=True)
class RenderedFile:
    kind: str
    filename: str
    content_type: str
    data: bytes
