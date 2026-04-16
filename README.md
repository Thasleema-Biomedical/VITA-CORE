# VITA-CORE
VITA-CORE: A low-cost biomedical signal monitoring system for gastric (EGG) and pulse (PVR) analysis using ESP32 and Python, featuring real-time signal processing, classification, and dashboard visualization.
# VITA-CORE Biomedical Monitoring Dashboard

Live web dashboard for real-time Electrogastrography (EGG) and Pulse Wave analysis using **Arduino Nano + Modified AD8232**.

> [!IMPORTANT]  
> For full hardware wiring, software dependencies, and detailed project roadmap, see [**VITA_CORE_SPECIFICATIONS.md**](./VITA_CORE_SPECIFICATIONS.md).

## Quick Start

### 1. Install & Run Dashboard

```powershell
cd d:\VITAcore
npm install
npm run dev
```

Open: [http://localhost:3000](http://localhost:3000)

### 2. Install Python Dependencies

```bash
cd python
pip install -r requirements.txt
```

### 3a. Test Without Hardware (Simulation Mode)

```bash
python vita_streamer.py --simulate
```

### 3b. Run with Arduino Nano on COM4 (Standard)

```bash
python vita_streamer.py --port COM4
```

### 3c. Specific Configuration

```bash
python vita_streamer.py --port COM6 --baud 115200 --url ws://localhost:3000/python
```

---

## ESP32 Serial Data Format

The Python script expects comma-separated values:

```
<gastric_raw>,<pulse_raw>
```

or with timestamp:

```
<timestamp_ms>,<gastric_raw>,<pulse_raw>
```

Example Arduino sketch output:
```cpp
Serial.print(sensorValue); // Analog Reading (A6)
Serial.print(",");
Serial.println(leadsOff ? 1 : 0); // Leads-off safety check (D10/D11)
```

---

## System Architecture

```
ESP32 (AD8232) ──serial──> vita_streamer.py ──WS──> server.js ──WS──> Browser
                                            └──REST (fallback)─────────────┘
```

### Signal Processing Pipeline

| Stage | Gastric Channel | Pulse Channel |
|---|---|---|
| Raw ADC | 12-bit ESP32 ADC | 12-bit ESP32 ADC |
| Filter | Butterworth BP 0.01–0.2 Hz | Butterworth BP 0.5–4.0 Hz |
| Detect | Peak→CPM calculation | Peak→BPM + PTT |
| Classify | Brady / Normal / Tachy | Normal / Tachy / Brady |

---

## Dashboard Features

- **Live waveforms** — Gastric EGG approximation + Pulse PVR
- **CPM Gauge** — Arc gauge with real-time classification  
- **Triage Status** — Green / Yellow / Red with animated alerts
- **PVR Panel** — Amplitude bar + PWV estimate + HR
- **Metrics Panel** — CPM, BPM, Amplitude, Signal Quality
- **ON/OFF Controls** — Freeze/resume data stream

---

## Vercel Deployment (REST Polling Fallback)

1. Push to GitHub
2. Import to Vercel
3. Set Python `--rest-url` to your Vercel URL:
   ```bash
   python vita_streamer.py --port COM3 --rest-url https://your-app.vercel.app/api/ingest
   ```

Note: Vercel free tier uses polling (every 1s), not WebSocket push.
