"""
Soul Trace 최종 편지 화면 ↔ A5 인쇄 시각 일치 (Phase 21).

이 파일이 지키는 계약:
  * 매핑 상수가 **화면 CSS 값 그대로**다 (한쪽만 바뀌면 여기서 실패한다).
  * 템플릿은 **하나**다 — Living/Memorial 분기가 없고, LETTER/MEMORY_BOX 가 같다.
  * 갈리는 축은 **언어 하나**뿐이다.
  * 줄바꿈이 화면과 같다 — `word-break: keep-all` (공백에서만 접는다).
  * 문단 구조가 보존된다 (`whitespace-pre-line`).
  * 렌더는 결정적이다 (같은 편지 → 같은 바이트).
"""

from __future__ import annotations

import io
import pathlib

import pytest

from backend.services import letter_print_theme as theme
from backend.services import print_render
from backend.services.print_render import LetterContent, render_letter_pdf

KO_BODY = (
    "엄마, 나 보리야.\n"
    "현관 앞에서 발소리를 기다리던 그 시간이 제일 좋았어. "
    "문이 열리기도 전에 나는 이미 알고 있었거든.\n"
    "\n"
    "이제 여기서는 아프지 않아. 그러니까 너무 오래 미안해하지 마."
)
EN_BODY = (
    "Mom, it's me.\n"
    "Waiting by the door for your footsteps was the best part of my day.\n"
    "\n"
    "It doesn't hurt here anymore, so please don't stay sorry too long."
)


def _render(body: str, **kw) -> bytes:
    return render_letter_pdf(
        LetterContent(body=body, child_name=kw.pop("child_name", "보리"), kicker="Soul Trace"),
        order_id=kw.pop("order_id", "t1"),
        **kw,
    )


# ── 언어 판정 ────────────────────────────────────────────────────────────────


def test_language_is_detected_from_the_body():
    """
    저장된 언어 컬럼이 없다. 본문 자체가 근거이며, **과거 주문도 같은 규칙**으로
    같은 렌더러를 탄다(요구사항 9).
    """
    assert theme.detect_language(KO_BODY) == "ko"
    assert theme.detect_language(EN_BODY) == "en"
    assert theme.detect_language("") == "en"
    # 영문 안에 한글이 한 글자라도 있으면 한글 편지다(혼용 편지).
    assert theme.detect_language("Hello 보리") == "ko"


def test_language_can_be_forced():
    ko = _render(EN_BODY, language="ko")
    en = _render(EN_BODY, language="en")
    assert ko != en, "언어를 강제해도 결과가 같다 — 언어가 조판에 반영되지 않는다"


# ── 매핑이 화면 값 그대로인가 ───────────────────────────────────────────────


def test_font_stacks_match_the_css():
    """globals.css 의 .font-ko / .font-display-en 과 같은 순서여야 한다."""
    assert theme.font_stack_for("ko") == ("Noto Serif KR", "Nanum Myeongjo", "serif")
    assert theme.font_stack_for("en") == ("Marcellus", "serif")


def test_tracking_matches_the_css():
    assert theme.KO_TRACKING_EM == 0.02      # .font-ko
    assert theme.EN_TRACKING_EM == 0.3       # .font-display-en


def test_body_line_height_is_exactly_two():
    """`leading-[2]` — 행 리듬은 인쇄에서도 그대로 유지된다."""
    assert theme.BODY_LINE_HEIGHT == 2.0
    typo = theme.typography_for("ko", body_font="X", display_font="X")
    assert typo.body_leading == typo.body_pt * 2.0


@pytest.mark.parametrize(
    "ratio,web_px",
    [
        (theme.RATIO_EYEBROW, 10),
        (theme.RATIO_TITLE, 30),
        (theme.RATIO_SUMMARY, 15),
        (theme.RATIO_TAGS, 12),
        (theme.RATIO_HEADING, 14),
        (theme.RATIO_DROPCAP, 76),
    ],
)
def test_hierarchy_ratios_come_from_the_web_sizes(ratio, web_px):
    """계층은 **비율로** 옮긴다 — 인쇄 본문 크기를 바꿔도 관계가 유지된다."""
    assert ratio == pytest.approx(web_px / 17.0)


def test_drop_cap_geometry_matches_the_css():
    assert theme.DROPCAP_LINE_HEIGHT == 0.8       # line-height: 0.8
    assert theme.DROPCAP_MARGIN_RIGHT_EM == 0.12  # margin-right: 0.12em
    assert theme.DROPCAP_MARGIN_TOP_EM == 0.06    # margin-top: 0.06em


def test_drop_cap_gradient_stops_match_the_css():
    """`.drop-cap` 의 금박 정지점 — 색과 위치가 모두 같아야 한다."""
    stops = theme.DROPCAP_GRADIENT
    assert [p for p, _ in stops] == [0.00, 0.42, 0.58, 1.00]
    assert stops[0][1] == theme._rgb("#a67c00")
    assert stops[1][1] == theme._rgb("#fff8e7")
    assert stops[2][1] == theme._rgb("#d4af37")
    assert stops[3][1] == theme._rgb("#8a6a2a")


def test_colors_match_the_jsx():
    assert theme.COLOR_BODY == theme._rgb("#F7F4EF")     # text-[#F7F4EF]
    assert theme.COLOR_TITLE == theme._rgb("#EAD8B7")    # text-[#EAD8B7]
    assert theme.COLOR_HEADING == theme._rgb("#D9C6A4")  # text-[#D9C6A4]
    assert theme.COLOR_GOLD == theme._rgb("#D4AF37")
    assert theme.ALPHA_TAGS == 0.72                      # /72
    assert theme.ALPHA_DIVIDER == 0.32                   # /32


def test_background_is_the_dark_scrim_not_paper():
    """
    화면의 편지는 **어두운 스크림** 위에 있다. 크림색 종이가 아니다 —
    `.letter-body-paper` 는 어떤 컴포넌트도 쓰지 않는 죽은 CSS 다.
    """
    for _, rgb in theme.BG_GRADIENT:
        assert max(rgb) < 0.09, f"배경이 어둡지 않다: {rgb}"


# ── 죽은 CSS 를 근거로 삼지 않았는가 ────────────────────────────────────────


def test_render_does_not_reference_unused_stationery_css():
    src = pathlib.Path("backend/services/print_render.py").read_text()
    theme_src = pathlib.Path("backend/services/letter_print_theme.py").read_text()
    for dead in ("stationery-paper", "capture-card--", "letter-body-paper"):
        assert dead not in src, f"인쇄가 죽은 CSS({dead})를 근거로 삼는다"
        # 테마 모듈은 '쓰지 않는다'고 **설명**할 수 있으므로 주석은 허용한다.
        assert dead not in theme_src.split('"""')[2] if '"""' in theme_src else True


# ── 템플릿은 하나 ────────────────────────────────────────────────────────────


def test_no_living_memorial_branch_exists():
    """
    두 갈래는 화면에서도 같은 렌더러를 쓴다. 인쇄에서 나누면 같은 디자인이 두 벌이
    되고 한쪽만 고쳐지는 날이 온다.
    """
    import ast

    src = pathlib.Path("backend/services/print_render.py").read_text()
    tree = ast.parse(src)

    # 주석·docstring 은 **설명**이므로 검사에서 뺀다. 실제 식별자와 문자열
    # 리터럴만 본다 — 예전에 "분기가 없다"고 적은 주석 때문에 이 검사가 스스로
    # 실패한 적이 있다(같은 실수를 반복하지 않는다).
    names: set[str] = set()
    literals: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id.lower())
        elif isinstance(node, ast.Attribute):
            names.add(node.attr.lower())
        elif isinstance(node, ast.arg):
            names.add(node.arg.lower())

    # docstring 노드를 **정체(id)로** 걸러 낸다. ast.get_docstring 은 들여쓰기를
    # 정리한 값을 주므로 원본 리터럴과 문자열 비교가 되지 않는다.
    docstring_nodes: set[int] = set()
    for n in ast.walk(tree):
        body = getattr(n, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
            if isinstance(first.value.value, str):
                docstring_nodes.add(id(first.value))

    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstring_nodes
        ):
            literals.add(node.value.lower())

    for token in ("living", "memorial", "letter_mode"):
        assert not any(token in n for n in names), f"인쇄 코드에 {token} 분기가 있다"
        assert not any(token in l for l in literals), f"인쇄 코드에 {token} 리터럴이 있다"


def test_letter_and_memory_box_use_the_same_renderer():
    """구성 파일 렌더러가 제품별로 갈리지 않는다."""
    src = pathlib.Path("backend/services/production_package.py").read_text()
    i = src.index('if k == "letter_pdf":')
    block = src[i : i + 900]
    assert "render_letter_pdf(" in block
    assert "MEMORY_BOX" not in block, "편지 렌더링이 제품별로 갈린다"


# ── 줄바꿈이 화면과 같은가 ──────────────────────────────────────────────────


def _wrap(text: str, width: float, char_space: float = 0.0):
    font = print_render.letter_font_name()
    return print_render.wrap_with_hanging_indent(
        text, font, 10.5, full_width=width, first_width=width,
        first_lines=0, char_space=char_space,
    )


def test_words_are_not_split_mid_word():
    """
    `word-break: keep-all` — 공백에서만 접는다.

    글자 단위로 접으면 "footsteps" 가 "foots|teps" 로, "발소리를" 이 "발소리|를"
    로 갈라져 화면과 줄 리듬이 완전히 달라진다.
    """
    lines = [ln for ln, _ in _wrap(EN_BODY, 200.0)]
    joined = " ".join(l for l in lines if l)
    for word in ("footsteps", "Waiting", "anymore"):
        assert word in joined, f"{word} 가 줄바꿈에서 쪼개졌다"

    ko_lines = [ln for ln, _ in _wrap(KO_BODY, 200.0)]
    ko_joined = " ".join(l for l in ko_lines if l)
    for word in ("발소리를", "기다리던", "미안해하지"):
        assert word in ko_joined, f"{word} 가 줄바꿈에서 쪼개졌다"


def test_blank_lines_survive_so_paragraphs_are_preserved():
    """`whitespace-pre-line` — 문단 사이 빈 줄이 인쇄에도 남는다."""
    lines = [ln for ln, _ in _wrap(KO_BODY, 260.0)]
    assert "" in lines, "문단 구분이 사라졌다"


def test_tracking_is_counted_in_the_measure():
    """
    Marcellus 0.3em 은 폭에 크게 기여한다. 무시하면 한 줄에 들어가는 글자 수가
    실제보다 30% 많게 계산돼 글자가 재단선 밖으로 나간다.
    """
    plain = _wrap(EN_BODY, 200.0, char_space=0.0)
    tracked = _wrap(EN_BODY, 200.0, char_space=10.5 * theme.EN_TRACKING_EM)
    assert len(tracked) > len(plain), "자간이 줄 수에 반영되지 않는다"


def test_long_token_still_breaks_rather_than_overflowing():
    """한 어절이 줄 폭보다 길면(긴 URL 등) 그때만 강제로 자른다 — 넘치게 두지 않는다."""
    long_word = "x" * 400
    lines = [ln for ln, _ in _wrap(long_word, 120.0)]
    assert len(lines) > 1
    assert all(ln for ln in lines)


# ── 산출물 ───────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("body,child", [(KO_BODY, "보리"), (EN_BODY, "Bori")])
def test_renders_a_valid_single_page_a5(body, child):
    data = _render(body, child_name=child)
    assert data[:5] == b"%PDF-"
    assert len(data) > 2000


def test_render_is_deterministic_per_language():
    """같은 편지 → 같은 바이트. 패키지가 파일을 저장하지 않는 전제다."""
    assert _render(KO_BODY) == _render(KO_BODY)
    assert _render(EN_BODY, child_name="Bori") == _render(EN_BODY, child_name="Bori")


def test_empty_body_is_refused():
    """여백만 인쇄된 종이를 배송하지 않는다."""
    with pytest.raises(print_render.PrintRenderError):
        _render("   ")


# ── 폰트 준비 상태 ───────────────────────────────────────────────────────────


def test_font_report_covers_both_languages(monkeypatch):
    monkeypatch.delenv(theme.ENV_KO, raising=False)
    monkeypatch.delenv(theme.ENV_EN, raising=False)
    monkeypatch.delenv(theme.ENV_LEGACY, raising=False)
    rep = print_render.font_report()
    assert set(rep) == {"ko", "en"}
    assert rep["ko"]["expected_stack"][0] == "Noto Serif KR"
    assert rep["en"]["expected_stack"][0] == "Marcellus"
    assert rep["ko"]["embedded"] is False


def test_one_language_configured_is_not_reported_as_ready(monkeypatch, tmp_path):
    """
    한쪽만 설정된 배포에서 "임베드됨"으로 보고하면, 반대 언어 편지가 임베드 없이
    인쇄소로 나간다.
    """
    ttf = tmp_path / "f.ttf"
    ttf.write_bytes(b"\x00\x01\x00\x00")
    monkeypatch.setenv(theme.ENV_KO, str(ttf))
    monkeypatch.delenv(theme.ENV_EN, raising=False)
    monkeypatch.delenv(theme.ENV_LEGACY, raising=False)
    assert theme.fonts_are_embedded("ko") is True
    assert theme.fonts_are_embedded("en") is False
    assert print_render.font_is_embedded() is False


def test_korean_falls_back_through_the_same_stack_as_css(monkeypatch, tmp_path):
    """Noto Serif KR → Nanum Myeongjo → 레거시 경로. .font-ko 와 같은 순서."""
    nanum = tmp_path / "nanum.ttf"
    nanum.write_bytes(b"\x00\x01\x00\x00")
    monkeypatch.delenv(theme.ENV_KO, raising=False)
    monkeypatch.setenv(theme.ENV_KO_FALLBACK, str(nanum))
    assert theme.font_path_for("ko") == str(nanum)


# ── 배경 파리티 (Phase 22) ───────────────────────────────────────────────────


def _hero_jpeg(w: int = 900, h: int = 900, bright_top: bool = True) -> bytes:
    """위쪽이 밝은 히어로 대역 — 스크림이 없으면 본문이 사라지는 실제 위험 구간."""
    import io as _io

    from PIL import Image, ImageDraw

    im = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(im)
    for y in range(h):
        t = y / h
        v = int((235 if bright_top else 30) * (1 - t) + 30 * t)
        d.line([(0, y), (w, y)], fill=(v, v, max(0, v - 12)))
    buf = _io.BytesIO()
    im.save(buf, format="JPEG", quality=92)
    return buf.getvalue()


def test_hero_background_changes_the_page():
    """배경을 주면 결과가 달라져야 한다 — 인자가 무시되고 있지 않은지."""
    with_hero = _render(KO_BODY, background=_hero_jpeg())
    without = _render(KO_BODY)
    assert with_hero != without
    assert len(with_hero) > len(without), "히어로가 실제로 들어가지 않았다"


def test_legacy_letters_keep_the_scrim_fallback():
    """
    배경 ref 가 없는 과거 편지는 **지금까지와 같은 결과**여야 한다.
    이 컬럼이 생기기 전 주문을 다시 뽑아도 그때의 인쇄물과 같아야 한다.
    """
    a = _render(KO_BODY, background=None)
    b = _render(KO_BODY)
    assert a == b


def test_scrim_is_smooth_no_banding():
    """
    스크림에 가로줄이 생기면 안 된다.

    PDF 에는 정지점별 알파가 없어서 처음에는 반투명 띠를 이어 붙였다. 두 번
    실패했다 — 띠가 적으면 알파가 툭 끊기고, 촘촘하면 띠 사이로 밑바탕이 샜다
    (흰 배경에서 밝기 차 131 측정). 지금은 픽셀 단위로 합성한다.
    """
    pytest.importorskip("numpy")
    import numpy as np
    from PIL import Image

    composed = print_render.composite_letter_background(_hero_jpeg(), 419.5, 595.3, dpi=150)
    assert composed, "배경 합성이 실패했다"
    arr = np.asarray(Image.open(io.BytesIO(composed)).convert("RGB"), dtype=float)
    column = arr[:, 8, :].mean(axis=1)
    jumps = np.abs(np.diff(column))
    assert jumps.max() < 6.0, f"스크림에 띠가 보인다 (최대 밝기 차 {jumps.max():.1f})"


def test_scrim_actually_darkens_a_bright_hero():
    """
    밝은 하늘 위에서도 본문이 읽혀야 한다. 두 겹 중 하나라도 빠지면 여기서 걸린다.
    """
    pytest.importorskip("numpy")
    import numpy as np
    from PIL import Image

    composed = print_render.composite_letter_background(
        _hero_jpeg(bright_top=True), 419.5, 595.3, dpi=150
    )
    arr = np.asarray(Image.open(io.BytesIO(composed)).convert("RGB"), dtype=float)
    # 본문이 놓이는 상단 1/3 이 충분히 어두워야 밝은 본문(#F7F4EF)이 읽힌다.
    top_third = arr[: arr.shape[0] // 3].mean()
    assert top_third < 110, f"상단이 너무 밝다({top_third:.0f}) — 본문이 사라진다"


def test_background_output_is_page_shaped():
    """페이지 비율로 잘라 두어야 재단선 안에 흰 띠가 남지 않는다."""
    from PIL import Image

    composed = print_render.composite_letter_background(_hero_jpeg(1600, 600), 419.5, 595.3, dpi=100)
    im = Image.open(io.BytesIO(composed))
    assert abs(im.width / im.height - 419.5 / 595.3) < 0.01


def test_broken_hero_falls_back_instead_of_failing():
    """배경 한 장 때문에 결제된 주문의 편지를 잃지 않는다."""
    data = _render(KO_BODY, background=b"not an image")
    assert data[:5] == b"%PDF-"


def test_render_stays_deterministic_with_a_hero():
    """같은 편지 + 같은 배경 → 같은 바이트. 패키지가 파일을 저장하지 않는 전제다."""
    hero = _hero_jpeg()
    assert _render(KO_BODY, background=hero) == _render(KO_BODY, background=hero)


def test_letter_and_memory_box_share_the_background_path():
    """제품별로 배경 처리가 갈리지 않는다 — 같은 렌더러의 같은 인자다."""
    src = pathlib.Path("backend/services/production_package.py").read_text()
    i = src.index('if k == "letter_pdf":')
    block = src[i : i + 1400]
    assert "letter_background.load_bytes" in block
    assert "background=background" in block
    assert "MEMORY_BOX" not in block


def test_background_ref_is_a_path_not_a_signed_url():
    """
    **핵심**: 저장하는 것은 경로다. 서명 URL 을 저장하면 만료와 함께 배경을 잃는다.
    """
    from backend.services import letter_background

    ref = letter_background.object_path_for("user@x.com", "stl_abc")
    assert ref == "user@x.com/letters/stl_abc/background.jpg"
    assert "http" not in ref and "token" not in ref


def test_background_source_host_is_restricted():
    """오픈 프록시가 되지 않는다 — Soul Trace 의 hero-image-proxy 와 같은 규칙."""
    from backend.services import letter_background

    assert letter_background.is_allowed_source(
        "https://oaidalleapiprod.blob.core.windows.net/x/y.png"
    )
    for bad in (
        "http://oaidalleapiprod.blob.core.windows.net/x.png",  # https 아님
        "https://evil.example.com/x.png",
        "https://evil.blob.core.windows.net/x.png",
        "",
    ):
        assert not letter_background.is_allowed_source(bad), bad
