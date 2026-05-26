"""
개발용 더미 지갑·모션 데이터.

PET_HYBRID_SEED=1 이면 서버 기동 시 MOCK 저장소에 주입.
"""

from __future__ import annotations

DUMMY_WALLETS: list[dict] = [
  {"user_id": "demo-user", "current_credits": 12},
  {"user_id": "premium-user", "current_credits": 40},
  {"user_id": "broke-user", "current_credits": 2},
]

# demo-user / snow_forest / 4 actions — device/sync 테스트용 (MOCK_LUMA_VIDEO_URL 있을 때 웹훅으로 채움)
DUMMY_MOTIONS: list[dict] = []
