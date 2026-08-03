# SSH 키 등록 — 비밀번호는 이번 한 번만 입력
#   cd C:\Users\choi jungsun\Desktop\eternal-beam-app\python
#   .\setup_ssh_once.ps1
param(
    [string]$PiHost = "172.30.1.68",
    [string]$PiUser = "pi"
)

$ErrorActionPreference = "Stop"
$key = Join-Path $env:USERPROFILE ".ssh\id_ed25519"
$pub = "$key.pub"

if (-not (Test-Path $pub)) {
    Write-Host "[*] SSH 키 생성…"
    ssh-keygen -t ed25519 -f $key -N '""' -C "choi-pc-eternalbeam"
}

Write-Host "[*] Pi에 공개키 등록 (비밀번호 1회만)…"
$pubContent = Get-Content $pub -Raw
ssh -o AddressFamily=inet -o ConnectTimeout=15 "${PiUser}@${PiHost}" @"
mkdir -p ~/.ssh && chmod 700 ~/.ssh
grep -qF '$($pubContent.Trim())' ~/.ssh/authorized_keys 2>/dev/null || echo '$($pubContent.Trim())' >> ~/.ssh/authorized_keys
chmod 600 ~/.ssh/authorized_keys
echo '[OK] authorized_keys 등록됨'
"@

Write-Host "[*] 연결 테스트 (비밀번호 없이)…"
ssh -o BatchMode=yes -o AddressFamily=inet "${PiUser}@${PiHost}" "echo OK — 이제 비밀번호 없이 접속됩니다"

Write-Host ""
Write-Host "이후 파일 복사:"
Write-Host "  .\sync_pc_to_pi.ps1"
