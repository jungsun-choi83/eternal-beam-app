# Pi 2디스플레이 — 배경 mp4 (터치스크린)

| 파일 | 역할 |
|------|------|
| `idle.mp4` | 부팅 기본 배경 |
| `fresh_forest.mp4` | NFC `forest` 테마 (숲) |

PC에서 복사:
```
forest.mp4 → backgrounds/fresh_forest.mp4
forest.mp4 → backgrounds/idle.mp4
```

Pi에서 테스트 (NFC 없이):
```bash
echo '{"event":"nfc_tagged","theme_id":"forest"}' | nc -u -w1 127.0.0.1 9999
```
