# Eternal Beam — S23 Unity Renderer (정본 프로젝트)

기계 안 S23 이 펫(피사체)을 그리는 Unity 프로젝트. 구 `EternalBeam/` 폴더는
프로젝트가 아니라 자산 조각 모음이었고(설치된 APK 의 소스는 오프레포), 이
프로젝트가 그것을 대체하는 **정본**이다.

- Unity 6000.0 LTS + URP + Newtonsoft JSON
- 수신 계약: UDP :5005, 데이터그램 = 컴팩트 JSON 1개
  (`python/pi_sse_server.py` 의 화이트리스트가 송신측 정본)
- `Reference~/legacy-fragments/` — 구 폴더에서 가져온 렌더링 조각
  (VideoLayer / PetHologram.shader 등). `~` 폴더라 Unity 가 임포트하지 않는다 —
  Milestone 2 에서 검토 후 Assets/ 로 승격한다.

## Milestone 1 — UDP 수신 + 로그 (완료 기준)

    UDP_HOST=127.0.0.1 python3 python/eternal_beam_pi.py   # 브리지 (Mac)
    # Unity 에서 아무 씬이나 Play — DeviceBootstrap 이 수신기를 자동 생성
    curl -X POST http://127.0.0.1:8787/demo/pet-ready -H 'Content-Type: application/json' -d '{...}'

배치 검증:

    Unity -batchmode -projectPath device/s23-unity \
      -executeMethod EternalBeam.Device.EditorTools.Milestone1Proof.Run -logFile m1.log

## 다음 마일스톤
2. video_url 평범 재생 (VideoPlayer 전체화면)
3. packed-alpha (legacy VideoLayer + PetHologram.shader 승격, delivery_format 명시 우선)
4. 모션 전환 / Android APK (D1 물리 검증 절차: ~/Desktop/D1-physical-test.md)
