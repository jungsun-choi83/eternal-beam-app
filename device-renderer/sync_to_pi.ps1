# device-renderer/ 전체를 기존 Raspberry Pi 5(python/*.py가 이미 돌고 있는 그 기기)로
# 복사합니다 — RK3566 보드가 아직 없어도, HardwareInterface/IPetRenderer 추상화 덕분에
# 이 C++ 프로젝트는 (스펠보드 관련 코드는 거의 없이) 그대로 RPi5에서 네이티브 빌드/실행할 수
# 있습니다. python/setup_ssh_once.ps1로 키를 이미 등록해 놨다면 비밀번호 없이 동작합니다.
#
#   .\device-renderer\sync_to_pi.ps1
#
# 그 다음 Pi에서 빌드 (device-renderer/README.md "빌드" 섹션 참고):
#   ssh pi@eternalbeam.local
#   cd ~/eternal-beam/device-renderer
#   sudo apt install build-essential cmake git libgpiod-dev \
#     libdrm-dev libgbm-dev libegl1-mesa-dev libgles2-mesa-dev \
#     ffmpeg libavformat-dev libavcodec-dev libswscale-dev libavutil-dev \
#     libcurl4-openssl-dev
#   cmake -B build -DETERNALBEAM_WITH_FFMPEG=ON -DETERNALBEAM_WITH_CURL=ON \
#     -DETERNALBEAM_WITH_DRM_GL=ON -DETERNALBEAM_BUILD_TESTS=ON
#   cmake --build build -j4 && ctest --test-dir build
param(
    [string]$PiHost = "eternalbeam.local",
    [string]$PiUser = "pi",
    [string]$PiPath = "/home/pi/eternal-beam/device-renderer"
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Remote = "${PiUser}@${PiHost}"

Push-Location $Here
try {
    Write-Host "[*] device-renderer/ 전체를 Pi로 전송 (build*/bin/.git 제외)…"
    ssh -o AddressFamily=inet -o ConnectTimeout=15 $Remote "mkdir -p $PiPath"
    & tar --exclude="build*" --exclude="bin" --exclude=".git" --exclude="libs/spine-cpp/spine-cpp" -cf - . |
        ssh -o AddressFamily=inet $Remote "cd $PiPath && tar -xf -"
    ssh -o AddressFamily=inet $Remote "cd $PiPath && find . -name '*.sh' -exec chmod +x {} \; 2>/dev/null; find . -name '*.sh' -exec sed -i 's/\r$//' {} \; 2>/dev/null"

    Write-Host ""
    Write-Host "[OK] 복사 완료. 다음은 Pi SSH 터미널에서:"
    Write-Host "  ssh $Remote"
    Write-Host "  cd $PiPath"
    Write-Host "  cmake -B build -DETERNALBEAM_WITH_FFMPEG=ON -DETERNALBEAM_WITH_CURL=ON -DETERNALBEAM_WITH_DRM_GL=ON -DETERNALBEAM_BUILD_TESTS=ON"
    Write-Host "  cmake --build build -j4 && ctest --test-dir build"
}
finally {
    Pop-Location
}
