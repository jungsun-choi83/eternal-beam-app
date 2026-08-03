#!/usr/bin/env bash
# 촬영 모드 — 부팅 로고 최소화 + 검정 대기 + NFC 시 숲만
#   sudo bash setup_film_boot.sh
#   sudo bash setup_film_boot.sh 192.168.0.109
set -euo pipefail
cd "$(dirname "$0")"

S23_IP="${1:-172.30.1.54}"
APP_DIR="$(cd .. && pwd)"

echo "[*] 부팅 스플래시 끄기…"
for CFG in /boot/firmware/config.txt /boot/config.txt; do
  if [[ -f "$CFG" ]]; then
    grep -q '^disable_splash=1' "$CFG" || echo 'disable_splash=1' | sudo tee -a "$CFG" >/dev/null
    echo "  OK $CFG"
  fi
done

for CMDLINE in /boot/firmware/cmdline.txt /boot/cmdline.txt; do
  if [[ -f "$CMDLINE" ]]; then
    sudo sed -i 's/ splash//g' "$CMDLINE" 2>/dev/null || true
    grep -q 'logo.nologo' "$CMDLINE" || sudo sed -i 's/$/ logo.nologo/' "$CMDLINE"
    echo "  OK $CMDLINE"
  fi
done

echo "[*] 환경 파일…"
ENV_DIR="$APP_DIR/python/systemd"
mkdir -p "$ENV_DIR"
if [[ ! -f "$ENV_DIR/s23-bridge.env" ]]; then
  cp "$ENV_DIR/s23-bridge.env.example" "$ENV_DIR/s23-bridge.env" 2>/dev/null || true
fi
if [[ -f "$ENV_DIR/eternal-beam-pi.env" ]]; then
  sed -i "s/^UDP_HOST=.*/UDP_HOST=${S23_IP}/" "$ENV_DIR/eternal-beam-pi.env" || true
fi

# eternal-beam-pi.env for display + sensor
cat >"$ENV_DIR/eternal-beam-pi.env" <<EOF
UDP_HOST=${S23_IP}
UDP_PORT=5005
BG_DISPLAY_HOST=127.0.0.1
BG_DISPLAY_PORT=9999
NFC_FALLBACK_THEME=forest
PI_SSE_PORT=8787
DISPLAY=:0
XAUTHORITY=/home/pi/.Xauthority
BG_MPV_FILL=simple
EOF

echo "[*] systemd 설치 (배경 대기 + NFC 브리지)…"
if [[ "${EUID}" -ne 0 ]]; then
  echo "sudo bash setup_film_boot.sh ${S23_IP}"
  exit 1
fi

install_svc() {
  sed "s#__APP_DIR__#${APP_DIR}#g" "$ENV_DIR/${1}" >"/etc/systemd/system/${1}"
}

install_svc "pi-display-bg.service"

cat >"/etc/systemd/system/eternal-beam-film.service" <<EOF
[Unit]
Description=Eternal Beam film mode (NFC->S23, touch/voice, no ToF)
After=network-online.target pi-display-bg.service
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${APP_DIR}/python
EnvironmentFile=${ENV_DIR}/eternal-beam-pi.env
ExecStart=/usr/bin/python3 -u ${APP_DIR}/python/eternal_beam_pi.py --host ${S23_IP} --no-tof
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

# 로그인 시 바탕화면 검정 (데스크톱은 뜨지만 배경만 검정)
AUTOSTART="/home/pi/.config/autostart/eternalbeam-black.desktop"
mkdir -p "$(dirname "$AUTOSTART")"
cat >"$AUTOSTART" <<'EOF'
[Desktop Entry]
Type=Application
Name=EternalBeam Black Idle
Exec=sh -c 'sleep 2; export DISPLAY=:0; xsetroot -solid "#000000" 2>/dev/null || true'
X-GNOME-Autostart-enabled=true
EOF
chown pi:pi "$AUTOSTART" 2>/dev/null || true

systemctl daemon-reload
systemctl enable pi-display-bg.service eternal-beam-film.service
systemctl restart pi-display-bg.service
systemctl restart eternal-beam-film.service

echo ""
echo "============================================"
echo " 촬영 모드 설치 완료"
echo "  전원 ON → 검정 대기 (--wait-nfc)"
echo "  NFC 태그 → 숲 배경 (mpv)"
echo "  S23 IP   : ${S23_IP}"
echo "  재부팅   : sudo reboot"
echo "============================================"
echo "  journalctl -u pi-display-bg.service -f"
echo "  journalctl -u eternal-beam-film.service -f"
echo "  mpv 오류 : tail -f /tmp/mpv-bg.err"
