# [앱용] NFC 영상 ID 기록기 (NFC Writer)

슬롯 배송 전, 휴대폰 앱으로 NFC 태그에 "이 판은 크리스마스 영상용" 같은 영상 ID를 기록합니다.

## 사용 기술
- Flutter + nfc_manager

## 설정
1. Flutter 프로젝트 생성 후 `pubspec.yaml`에 `nfc_manager` 추가
2. Android: `AndroidManifest.xml`에 NFC 권한 추가
3. iOS: `Info.plist`에 NFC 사용 목적 설명 추가
