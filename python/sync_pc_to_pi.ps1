# PC → Pi 파일 동기화 (비밀번호 1회 — 키 등록 후 0회)
#   .\setup_ssh_once.ps1   ← 최초 1번만 (이후 비번 없음)
#   .\sync_pc_to_pi.ps1
param(
    [string]$PiHost = "eternalbeam.local",
    [string]$PiUser = "pi",
    [string]$PiPath = "/home/pi/eternal-beam/python"
)

$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
$Remote = "${PiUser}@${PiHost}"

$Files = @(
    "voice_to_unity.py",
    "eternal_beam_pi.py",
    "pi_display_bg.py",
    "s23_bridge_simple.py",
    "pi_sse_server.py",
    "film_display_simple.py",
    "pi_sensors_to_unity_udp.py",
    "pi_nfc_slot.py",
    "sensor_check.py",
    "requirements-pi.txt",
    "hardware_config.yaml",
    "hardware/__init__.py",
    "hardware/config.py",
    "hardware/i2c_bus.py",
    "hardware/gpio.py",
    "bg_theme_map.json",
    "nfc_theme_map.json",
    "fix_forest_only.sh",
    "fix_voicehat_alsa.sh",
    "start_pi_forest.sh",
    "update_s23_ip.sh",
    "test_touch_voice.sh",
    "start_touch_voice.sh",
    "run_voice_only.sh",
    "free_mic.sh",
    "send_s23_action.sh",
    "udp_action.sh",
    "restore_pi_today.sh",
    "systemd/s23-bridge.env.example",
    "backgrounds/fresh_forest.mp4"
)

Push-Location $Here
try {
    $toSend = @()
    foreach ($f in $Files) {
        if (Test-Path $f) {
            $toSend += $f
            Write-Host "[+] $f"
        } else {
            Write-Warning "SKIP (없음): $f"
        }
    }
    if ($toSend.Count -eq 0) {
        throw "보낼 파일이 없습니다."
    }

    Write-Host "[*] Pi에 한 번에 전송 (tar + ssh, 비밀번호 1회)…"
    ssh -o AddressFamily=inet -o ConnectTimeout=15 $Remote "mkdir -p $PiPath/backgrounds $PiPath/systemd $PiPath/hardware"
    & tar -cf - @toSend | ssh -o AddressFamily=inet $Remote "cd $PiPath && tar -xf -"
    ssh -o AddressFamily=inet $Remote "cd $PiPath && sed -i 's/\r$//' *.sh 2>/dev/null; sed -i 's/\r$//' systemd/*.sh 2>/dev/null; chmod +x *.sh 2>/dev/null; head -1 voice_to_unity.py eternal_beam_pi.py"

    Write-Host ""
    Write-Host "[OK] 복사 완료. Pi SSH 터미널에서:"
    Write-Host "  cd ~/eternal-beam/python && bash run_voice_only.sh 172.30.1.54"
}
finally {
    Pop-Location
}
