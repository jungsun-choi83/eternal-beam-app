# A5 편지 인쇄 폰트 (로컬 라이선스 자산)

Soul Trace 최종 화면과 **같은 글꼴**로 인쇄하기 위한 파일들이다.
원격 Google Fonts 를 **쓰지 않는다** — 인쇄 렌더링이 네트워크와 외부 서비스에
의존하면 배포 중 한 번의 실패로 글자가 대체되고, 그 종이는 회수할 수 없다.

## 왜 저장소에 파일이 없는가

폰트는 라이선스 자산이다. 바이너리를 저장소에 넣지 않고 **경로만** 지정한다
(기존 `PRINT_LETTER_FONT_PATH` 와 같은 방식). 이미지에 함께 굽거나 디스크에
두고 환경변수로 가리킨다.

## 넣어야 할 파일

| 언어 | 웹 스택 (`globals.css`) | 환경변수 | 권장 파일 |
|---|---|---|---|
| 한국어 | `.font-ko` → `"Noto Serif KR", "Nanum Myeongjo", serif` | `PRINT_LETTER_FONT_KO_PATH` | `NotoSerifKR-ExtraLight.ttf` 또는 `-Light` |
| 한국어(대체) | 위 스택의 2순위 | `PRINT_LETTER_FONT_KO_FALLBACK_PATH` | `NanumMyeongjo-Regular.ttf` |
| 영어 | `.font-display-en` → `var(--font-marcellus), "Marcellus", serif` | `PRINT_LETTER_FONT_EN_PATH` | `Marcellus-Regular.ttf` |

해석 순서는 웹 폰트 스택과 **같다**: 한국어는 Noto Serif KR → Nanum Myeongjo →
레거시 `PRINT_LETTER_FONT_PATH`. 하나도 없으면 reportlab 내장 CID 폰트로
떨어지는데, 그것은 **PDF 에 임베드되지 않아** 인쇄소 RIP 에서 글자가 대체될 수
있다. `/ops/production` 구성표의 `letter_fonts` 가 언어별 준비 상태를 보여 준다.

## 굵기에 관하여

화면 본문은 `font-extralight`(200)이다. 한 언어당 파일 하나만 지정하면 그 한
굵기로 조판된다 — 화면과 가장 가까운 굵기의 파일(ExtraLight/Light)을 넣는 것이
좋다. Marcellus 는 단일 굵기 서체라 영어 쪽은 원래 선택지가 없다.

## 확인

```bash
python -c "
from backend.services import print_render as pr
import json; print(json.dumps(pr.font_report(), ensure_ascii=False, indent=2))"
```

`embedded: true` 가 두 언어 모두 나와야 인쇄 준비가 끝난 것이다.
