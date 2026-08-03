# Eternal Beam

Memorial hologram experience: upload a pet photo on the web app, generate idle/action video, and play it on the physical kiosk (Raspberry Pi touch display + in-device phone running PetVFX).

**Production:** [device.eternalbeam.com](https://device.eternalbeam.com)  
**API:** [eternal-beam-video-api.onrender.com](https://eternal-beam-video-api.onrender.com)

---

## What this repo contains

| Area | Path | Role |
|------|------|------|
| **Web app** | `src/` | React + Vite memorial flow (theme, upload, AI processing, device sync) |
| **Video API** | `backend/` | FastAPI — cutout, compose, Luma/LivePortrait, PayPal, Supabase jobs |
| **Pi kiosk** | `python/` | NFC, ToF touch, mic → UDP bridge; background video on touch screen |
| **Unity / VFX** | `EternalBeam/` | Pet hologram scenes (S23 APK) |
| **Device renderer** | `device-renderer/` | C++ Spine/video renderer for Pi / RK3566 (WIP) |
| **Database** | `supabase/migrations/` | Jobs, wallet, rigging schema |

---

## Architecture (kiosk)

The machine uses **two displays**:

```
Phone web app  ──HTTP──►  Pi (:8787)  ──UDP :9999──►  Pi touch screen (forest background)
                              │
                              └──UDP :5005──►  S23 PetVFX (pet idle / run action)

Pi sensors (ToF + mic)  ──UDP :5005──►  S23 PetVFX (touch → RUN, voice → reaction)
```

| Layer | Device | Trigger |
|-------|--------|---------|
| Background | Pi touch screen | App **Play on device** or NFC / theme sync |
| Pet idle | S23 PetVFX | Idle ready → `/demo/pet-ready` |
| Touch / voice | Pi → S23 | `eternal_beam_pi.py` (always on) |

Details: [`docs/S23_PI_2DISPLAY.md`](docs/S23_PI_2DISPLAY.md)

---

## Local development

### Prerequisites

- Node.js 20+
- Python 3.10+
- FFmpeg (for backend compose)

### Web app

```bash
npm install
cp .env.example .env.local   # fill Vite / Supabase vars
npm run dev
```

Open `http://localhost:5173`. To target a Pi on your LAN: `?pi=192.168.0.104`

### Video API

```bash
cd backend
python -m venv .venv
.venv\Scripts\activate          # Windows
pip install -r requirements.txt
uvicorn backend.main:app --reload --port 8000
```

Or from repo root: `npm run video-api`

See [`backend/README.md`](backend/README.md) for API endpoints.

---

## Raspberry Pi setup

1. Clone on the Pi and install services:

```bash
cd ~/eternal-beam-app/python
sudo bash systemd/install.sh
```

2. Set S23 Wi‑Fi IP in `python/systemd/eternal-beam-pi.env`:

```ini
UDP_HOST=192.168.0.102
UDP_PORT=5005
```

3. Start services:

```bash
sudo systemctl enable --now pi-display-bg.service eternal-beam-pi.service
```

4. Quick tests:

```bash
# Forest background on Pi screen
python3 pi_display_bg.py --videos-dir ./backgrounds --test-forest

# Run action on S23 (VFX must be open)
bash send_s23_action.sh 192.168.0.102 approach
```

PC → Pi sync: `python/sync_pc_to_pi.ps1 -PiHost eternalbeam.local`

More: [`python/systemd/README.md`](python/systemd/README.md)

---

## Deployment

- **Frontend:** Vercel (`vercel.json` proxies `/api/*` to Render)
- **Backend:** Render Docker (`render.yaml`)

Full guide: [`DEPLOY.md`](DEPLOY.md)

---

## Environment variables (summary)

| Location | Examples |
|----------|----------|
| `.env.local` (web) | `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY` |
| Render (API) | `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `LUMA_API_KEY` |
| Pi `eternal-beam-pi.env` | `UDP_HOST`, `UDP_PORT`, `BG_DISPLAY_PORT` |

Never commit `.env`, `.env.vercel.prod`, or service role keys.

---

## Docs

| Topic | File |
|-------|------|
| Pi + S23 dual display | [`docs/S23_PI_2DISPLAY.md`](docs/S23_PI_2DISPLAY.md) |
| Deploy (Vercel + Render) | [`DEPLOY.md`](DEPLOY.md) |
| LivePortrait pipeline | [`docs/LivePortrait_파이프라인_진행상황.md`](docs/LivePortrait_파이프라인_진행상황.md) |
| RK3566 porting | [`docs/RK3566_이식_가이드.md`](docs/RK3566_이식_가이드.md) |
| Local testing | [`docs/로컬_테스트_가이드.md`](docs/로컬_테스트_가이드.md) |

---

## License

Private project — all rights reserved unless otherwise noted.
