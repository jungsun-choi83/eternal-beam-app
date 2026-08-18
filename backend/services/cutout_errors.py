"""
누끼(cutout) 파이프라인 실패를 구조화된 오류로 표현.

기존 파이프라인은 실패해도 HTTP 200 + {"error": "..."} 를 돌려줬고, 피사체를
못 찾으면 화면 중앙 80% 사각형으로 대충 잘라 그대로 다음 단계(Luma 유료 생성)
까지 흘려보냈다. 여기 정의한 예외들은 그 두 문제를 막기 위한 것으로,
라우터에서 HTTP 422 + {"detail": {"code", "message"}} 로 매핑된다.

각 예외는 `diagnostics` 를 함께 들고 다닌다 — 실패한 요청도 성공 요청과 같은
진단 필드(detector/segmenter/mask_area_fraction 등)를 남겨야 프로덕션에서
"왜 실패했는지"를 로그만 보고 알 수 있기 때문이다.
"""

from __future__ import annotations

from typing import Any, Optional


class CutoutError(Exception):
    """누끼 실패의 베이스. `code` 는 클라이언트가 분기에 쓰는 안정적인 식별자."""

    code = "CUTOUT_FAILED"
    http_status = 422

    def __init__(
        self,
        message: str,
        *,
        diagnostics: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.diagnostics: dict[str, Any] = dict(diagnostics or {})

    def to_detail(self, *, include_diagnostics: bool = False) -> dict[str, Any]:
        """FastAPI HTTPException(detail=...) 에 그대로 넣을 수 있는 형태."""
        detail: dict[str, Any] = {"code": self.code, "message": self.message}
        if include_diagnostics and self.diagnostics:
            detail["diagnostics"] = self.diagnostics
        return detail


class SubjectNotDetectedError(CutoutError):
    """YOLO가 지원하는 동물 피사체를 하나도 찾지 못함.

    예전에는 이때 중앙 80% 사각형을 SAM2/GrabCut 프롬프트로 넣어 "네모난 누끼"를
    만들어 냈다. 이제는 여기서 멈춘다.
    """

    code = "SUBJECT_NOT_DETECTED"


class MaskTooSmallError(CutoutError):
    code = "CUTOUT_MASK_TOO_SMALL"


class MaskTooLargeError(CutoutError):
    code = "CUTOUT_MASK_TOO_LARGE"


class AlphaEmptyError(CutoutError):
    code = "CUTOUT_ALPHA_EMPTY"


class RectangleLikeMaskError(CutoutError):
    code = "CUTOUT_RECTANGLE_LIKE"


#: 클라이언트가 "이 사진으로는 안 된다"고 사용자에게 알려야 하는 코드들.
#: (서버 다운/콜드스타트 같은 일시적 오류와 구분하기 위한 목록)
SUBJECT_REJECTION_CODES = frozenset(
    {
        SubjectNotDetectedError.code,
        MaskTooSmallError.code,
        MaskTooLargeError.code,
        AlphaEmptyError.code,
        RectangleLikeMaskError.code,
    }
)
