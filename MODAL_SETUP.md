# Modal GPU 배경 제거 연동

## 1. Modal 배포

```bash
pip install modal
modal token new   # 토큰 발급 (웹 열림)
modal deploy modal_cutout.py
```

## 2. 환경변수 설정 (.env)

```
MODAL_TOKEN_ID=your_token_id
MODAL_TOKEN_SECRET=your_token_secret
```

또는 `~/.modal.toml` (modal token new 시 자동 생성)

## 3. GPU 변경 (A10G)

`modal_cutout.py` 79번째 줄:

```python
gpu=os.getenv("MODAL_GPU", "T4"),  # → "A10G" 로 변경
```

또는 배포 전 `MODAL_GPU=A10G modal deploy modal_cutout.py`

## 4. API 사용

- **POST /api/generate-pet-video**: 사진 업로드 → Luma I2V → Modal GPU 누끼 → pet_video_url 반환
- **POST /api/cutout-video**: 영상 업로드 → Modal GPU 누끼 → RGBA MP4 다운로드

Modal 토큰이 있으면 자동으로 GPU 사용. 없으면 로컬 CPU.

## 5. 비용

- T4: ~$0.0016/영상 (30프레임)
- 작업 완료 시 인스턴스 즉시 종료 (과금 중단)
