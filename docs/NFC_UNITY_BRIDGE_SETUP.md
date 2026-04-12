# NFC → Unity Bridge Setup

When Raspberry Pi scans an NFC tag, it sends the slot number (and optional content_id) to Unity running on PC. Unity updates the background video and/or pet model immediately.

## Architecture

```
[NFC Tag] → [Pi NFC Reader] → pi_nfc_to_unity.py
                                    ↓
                    ┌───────────────┴───────────────┐
                    │  Socket (TCP)  │  Serial (USB) │
                    └───────────────┴───────────────┘
                                    ↓
                    [Unity PC] ← NfcUnityBridge.cs
                                    ↓
                    SlotContentController → Swap VideoClips
                    BackgroundSlotManager → Apply shader preset
```

## 1. Python (Raspberry Pi)

### Install
```bash
cd eternal-beam-app/python
pip install pyserial   # for serial mode only
```

### Modes

| Mode | Command | Use case |
|------|---------|----------|
| Socket | `python pi_nfc_to_unity.py --mode socket --host 192.168.1.100 --port 9999` | Pi and PC on same WiFi |
| Serial | `python pi_nfc_to_unity.py --mode serial --serial-port /dev/ttyUSB0` | Pi connected to PC via USB serial |
| Mock | `python pi_nfc_to_unity.py --mode mock --host 127.0.0.1 --port 9999` | Test on PC (press 1–4 to simulate) |

### Replace PC IP
- Find your PC IP: `ipconfig` (Windows) or `ifconfig` (Mac/Linux)
- Use that IP for `--host` when running Pi script

### Real NFC (RC522/PN532)
Edit `read_nfc_rc522()` in `pi_nfc_to_unity.py` to use your NFC library, e.g.:
```python
# Example with mfrc522
import mfrc522
reader = mfrc522.MFRC522()
uid = reader.read_id()
return (uid_to_slot(uid), str(uid))
```

## 2. Unity (PC)

### Setup

1. **Add NfcUnityBridge**
   - Create empty GameObject `NfcBridge`
   - Add component `NfcUnityBridge`
   - Set **Mode**: Socket (default) or Serial
   - Set **Port**: 9999 (Socket)
   - Set **Serial Port**: COM3 (Serial; check Device Manager for actual port)

2. **Add SlotContentController**
   - Create empty GameObject `SlotController` (or add to existing)
   - Add component `SlotContentController`
   - Drag **BackgroundQuad**’s `VideoLayer` → `Background Video Layers`
   - Drag **SubjectQuad**’s `VideoLayer` → `Subject Video Layers`
   - For each slot (1–4), assign:
     - `Slot N Background Clips`: VideoClip(s) for background
     - `Slot N Subject Clips`: VideoClip(s) for pet

3. **Wire references**
   - Select `NfcBridge`
   - Drag `SlotController` → **Slot Content Controller**
   - (Optional) Drag `BackgroundQuad`’s `BackgroundSlotManager` → **Slot Manager**

4. **Start Unity first**, then run the Pi script.

## 3. Test (No Pi/NFC)

1. Start Unity, enter Play mode
2. On PC, run:
   ```bash
   python python/pi_nfc_to_unity.py --mode mock --host 127.0.0.1 --port 9999
   ```
3. Press `1`, `2`, `3`, or `4` to simulate NFC scan
4. Unity should swap videos immediately

## 4. Serial (Pi → PC via USB)

- Connect Pi to PC with USB cable (USB‑to‑TTL adapter or USB gadget)
- On PC, find COM port (e.g. COM3)
- Unity: Mode = Serial, Serial Port = COM3
- Pi: `python pi_nfc_to_unity.py --mode serial --serial-port /dev/ttyS0`
- Pi writes to serial; Unity reads from COM port
