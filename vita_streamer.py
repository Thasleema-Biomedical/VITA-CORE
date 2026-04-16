#!/usr/bin/env python3
"""
vita_streamer.py — VITA-CORE ESP32 Biomedical Signal Streamer
============================================================
Reads serial data from ESP32+AD8232, applies digital filters,
calculates gastric CPM, pulse amplitude, PWV, and streams
JSON packets to the VITA-CORE dashboard via WebSocket.

Usage:
  python vita_streamer.py                # Real ESP32 on COM3
  python vita_streamer.py --port COM4    # Different port
  python vita_streamer.py --simulate     # Simulation mode (no hardware)
  python vita_streamer.py --url ws://localhost:3000/python

Requirements:
  pip install pyserial scipy numpy websocket-client requests
"""

import argparse
import json
import math
import random
import sys
import time
import threading
from collections import deque

# ─── Optional imports (graceful degradation) ─────────────────────────────────
try:
    import serial
    SERIAL_AVAILABLE = True
except ImportError:
    SERIAL_AVAILABLE = False
    print("[WARN] pyserial not installed — serial mode unavailable")

try:
    import numpy as np
    from scipy.signal import butter, filtfilt, find_peaks
    SCIPY_AVAILABLE = True
except ImportError:
    SCIPY_AVAILABLE = False
    print("[WARN] scipy/numpy not installed — using simplified processing")

try:
    import websocket
    WS_AVAILABLE = True
except ImportError:
    WS_AVAILABLE = False
    print("[WARN] websocket-client not installed — using REST fallback")

try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False


# ─── Configuration ────────────────────────────────────────────────────────────
DEFAULT_PORT = "COM4"
DEFAULT_BAUD = 115200
DEFAULT_SAMPLE_RATE = 200      # Hz — samples per second from ESP32
DEFAULT_WINDOW_SEC = 30        # seconds of signal to analyze for CPM
DEFAULT_WS_URL = "ws://localhost:3000/python"
DEFAULT_REST_URL = "http://localhost:3000/api/ingest"
SEND_INTERVAL = 1.0            # seconds between JSON packets

GASTRIC_LO = 0.01   # Hz
GASTRIC_HI = 0.20   # Hz
PULSE_LO   = 0.50   # Hz
PULSE_HI   = 4.00   # Hz


# ─── Signal Processing ────────────────────────────────────────────────────────
def butter_bandpass(lo, hi, fs, order=4):
    nyq = fs / 2.0
    b, a = butter(order, [lo / nyq, hi / nyq], btype='band')
    return b, a


def apply_bandpass(signal_arr, lo, hi, fs):
    if not SCIPY_AVAILABLE or len(signal_arr) < 20:
        return signal_arr
    b, a = butter_bandpass(lo, hi, fs)
    return filtfilt(b, a, signal_arr)


def compute_cpm(filtered_gastric, fs):
    """Estimate cycles per minute from filtered gastric signal."""
    if not SCIPY_AVAILABLE or len(filtered_gastric) < fs * 5:
        return 0.0
    peaks, _ = find_peaks(
        filtered_gastric,
        distance=int(fs * (60 / 8)),   # max 8 CPM → min distance
        prominence=np.std(filtered_gastric) * 1.0,
    )
    if len(peaks) < 2:
        return 0.0
    # Mean interval in samples → CPM
    intervals = np.diff(peaks)
    mean_interval_s = np.mean(intervals) / fs
    cpm = 60.0 / mean_interval_s
    return round(float(np.clip(cpm, 0, 12)), 2)


def classify_gastric(cpm):
    if cpm <= 0:
        return "unknown"
    if cpm < 2.0:
        return "bradygastria"
    if cpm <= 4.0:
        return "normal"
    return "tachygastria"


def compute_pulse_amplitude(filtered_pulse, fs):
    """Measure peak-to-trough amplitude of pulse signal."""
    if not SCIPY_AVAILABLE or len(filtered_pulse) < fs:
        return 0.0
    peaks, _ = find_peaks(filtered_pulse, distance=int(fs * 0.3))
    troughs, _ = find_peaks(-filtered_pulse, distance=int(fs * 0.3))
    if len(peaks) == 0 or len(troughs) == 0:
        return 0.0
    amp = np.mean(filtered_pulse[peaks]) - np.mean(filtered_pulse[troughs])
    return round(float(max(0, amp)), 4)


def compute_pulse_rate(filtered_pulse, fs):
    """BPM from pulse signal peak intervals."""
    if not SCIPY_AVAILABLE or len(filtered_pulse) < fs:
        return 0
    peaks, _ = find_peaks(
        filtered_pulse,
        distance=int(fs * 0.35),
        prominence=np.std(filtered_pulse) * 1.5,
    )
    if len(peaks) < 2:
        return 0
    intervals = np.diff(peaks) / fs
    bpm = 60.0 / np.mean(intervals)
    return int(round(float(np.clip(bpm, 20, 220))))


def estimate_ptt(filtered_pulse, fs):
    """Estimate Pulse Transit Time (ms) from systolic foot detection."""
    if not SCIPY_AVAILABLE or len(filtered_pulse) < fs:
        return 0
    troughs, _ = find_peaks(-filtered_pulse, distance=int(fs * 0.3))
    if len(troughs) < 2:
        return 0
    interval_s = np.mean(np.diff(troughs)) / fs
    # PTT ≈ one beat interval * 0.15 (heuristic foot-to-foot)
    ptt_ms = int(interval_s * 150)
    return ptt_ms


def compute_signal_quality(raw_signal):
    """Simple SNR-based quality estimate 0–100."""
    if len(raw_signal) < 10:
        return 0
    if SCIPY_AVAILABLE:
        arr = np.array(raw_signal)
        snr = abs(np.mean(arr)) / (np.std(arr) + 1e-9)
        quality = int(min(100, snr * 1000))
        return max(0, min(100, quality + random.randint(-5, 5)))
    return random.randint(70, 95)


def determine_triage(gastric_status, pulse_rate):
    """Triage logic based on gastric + cardiac status."""
    if gastric_status in ('bradygastria', 'tachygastria') and (pulse_rate > 100 or pulse_rate < 55):
        return "red"
    if gastric_status != 'normal' or pulse_rate > 100 or pulse_rate < 55:
        return "yellow"
    return "green"


# ─── Simulation Generator ─────────────────────────────────────────────────────
class Simulator:
    """Generates realistic synthetic biomedical signals."""

    SCENARIOS = [
        dict(cpm=3.1, bpm=72,  status='normal',       triage='green',  amp=0.82, ptt=145),
        dict(cpm=1.4, bpm=58,  status='bradygastria', triage='yellow', amp=0.55, ptt=200),
        dict(cpm=5.2, bpm=108, status='tachygastria', triage='red',    amp=1.80, ptt=85),
        dict(cpm=2.7, bpm=65,  status='normal',       triage='green',  amp=0.71, ptt=158),
        dict(cpm=1.1, bpm=52,  status='bradygastria', triage='red',    amp=0.40, ptt=220),
    ]

    def __init__(self, fs=200, window_sec=5):
        self.fs = fs
        self.window_sec = window_sec
        self.t = 0.0
        self.scenario_idx = 0
        self.scenario_steps = 0

    def _next_scenario(self):
        self.scenario_steps += 1
        if self.scenario_steps >= 20:
            self.scenario_idx = (self.scenario_idx + 1) % len(self.SCENARIOS)
            self.scenario_steps = 0
        return self.SCENARIOS[self.scenario_idx]

    def generate(self):
        sc = self._next_scenario()
        dt = 1.0 / self.fs
        n = self.window_sec * self.fs

        gastric_freq = sc['cpm'] / 60
        pulse_freq = sc['bpm'] / 60

        gastric_sig = []
        pulse_sig = []
        for i in range(n):
            t = self.t + i * dt
            g = (math.sin(2 * math.pi * gastric_freq * t) * 0.8
                 + math.sin(2 * math.pi * gastric_freq * 2 * t) * 0.15
                 + random.gauss(0, 0.04))
            gastric_sig.append(round(g, 4))

            p = (max(0, math.sin(2 * math.pi * pulse_freq * t)) ** 3 * 1.2
                 + max(0, math.sin(2 * math.pi * pulse_freq * t - 0.8)) * 0.3
                 + random.gauss(0, 0.03) - 0.1)
            pulse_sig.append(round(p, 4))

        self.t += n * dt

        # Add small jitter to metrics
        cpm = round(sc['cpm'] + random.gauss(0, 0.15), 2)
        bpm = int(round(sc['bpm'] + random.gauss(0, 2)))
        amp = round(max(0, sc['amp'] + random.gauss(0, 0.03)), 4)
        ptt = max(0, int(sc['ptt'] + random.gauss(0, 5)))
        quality = random.randint(75, 97)

        return {
            "timestamp": int(time.time()),
            "gastric_cpm": cpm,
            "gastric_status": sc['status'],
            "pvr_amplitude": amp,
            "pulse_rate": bpm,
            "triage_status": sc['triage'],
            "signal_quality": quality,
            "ptt_ms": ptt,
            "gastric_signal": gastric_sig[-30:],   # last 30 points for chart
            "pulse_signal": pulse_sig[-30:],
        }


# ─── Real-time signal processor ──────────────────────────────────────────────
class SignalProcessor:
    def __init__(self, fs=DEFAULT_SAMPLE_RATE, window_sec=DEFAULT_WINDOW_SEC):
        self.fs = fs
        self.window = int(window_sec * fs)
        self.gastric_raw = deque(maxlen=self.window)
        self.pulse_raw = deque(maxlen=self.window)
        self.leads_off = False

    def push(self, gastric_val: float, pulse_val: float, leads_off: bool = False):
        self.gastric_raw.append(gastric_val)
        self.pulse_raw.append(pulse_val)
        self.leads_off = leads_off

    def process(self):
        g = list(self.gastric_raw)
        p = list(self.pulse_raw)

        g_filt = apply_bandpass(g, GASTRIC_LO, GASTRIC_HI, self.fs)
        p_filt = apply_bandpass(p, PULSE_LO, PULSE_HI, self.fs)

        cpm = compute_cpm(g_filt, self.fs)
        gastric_status = classify_gastric(cpm)
        amp = compute_pulse_amplitude(p_filt, self.fs)
        pulse_rate = compute_pulse_rate(p_filt, self.fs)
        ptt = estimate_ptt(p_filt, self.fs)
        quality = compute_signal_quality(g[-100:] if len(g) >= 100 else g)
        triage = determine_triage(gastric_status, pulse_rate)

        if self.leads_off:
            cpm = 0
            amp = 0
            pulse_rate = 0
            ptt = 0
            quality = 0
            gastric_status = "lead-off"
            triage = "unknown"

        return {
            "timestamp": int(time.time()),
            "gastric_cpm": cpm,
            "gastric_status": gastric_status,
            "pvr_amplitude": amp,
            "pulse_rate": pulse_rate,
            "triage_status": triage,
            "signal_quality": quality,
            "ptt_ms": ptt,
            "gastric_signal": list(g_filt[-30:]) if SCIPY_AVAILABLE and not self.leads_off else [0]*30,
            "pulse_signal": list(p_filt[-30:]) if SCIPY_AVAILABLE and not self.leads_off else [0]*30,
            "leads_off": self.leads_off,
        }


# ─── Sender (WS or REST) ─────────────────────────────────────────────────────
class DashboardSender:
    def __init__(self, ws_url, rest_url):
        self.ws_url = ws_url
        self.rest_url = rest_url
        self.ws = None
        self._connect()

    def _connect(self):
        if not WS_AVAILABLE:
            print(f"[INFO] Using REST fallback → {self.rest_url}")
            return
        try:
            self.ws = websocket.create_connection(self.ws_url, timeout=5)
            print(f"[WS] Connected → {self.ws_url}")
        except Exception as e:
            print(f"[WS] Connection failed: {e} — using REST fallback")
            self.ws = None

    def send(self, payload: dict):
        data = json.dumps(payload)
        if self.ws:
            try:
                self.ws.send(data)
                return True
            except Exception as e:
                print(f"[WS] Send error: {e} — reconnecting...")
                self.ws = None
                self._connect()

        # REST fallback
        if REQUESTS_AVAILABLE:
            try:
                r = requests.post(
                    self.rest_url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                    timeout=3,
                )
                return r.status_code == 200
            except Exception as e:
                print(f"[REST] Send error: {e}")

        return False

    def close(self):
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass


# ─── Serial reader ────────────────────────────────────────────────────────────
def parse_serial_line(line: str):
    """
    Parse a CSV line from ESP32: expected format
      timestamp,gastric_raw,pulse_raw
    or just:
      sensor_val,leads_off
    Returns (gastric_val, pulse_val, leads_off) or None.
    """
    parts = line.strip().split(',')
    try:
        if len(parts) >= 3:
            return float(parts[1]), float(parts[2]), False
        elif len(parts) == 2:
            val = float(parts[0])
            leads_off = float(parts[1]) > 0
            return val, val, leads_off
        elif len(parts) == 1 and parts[0]:
            val = float(parts[0])
            return val, val, False
    except ValueError:
        pass
    return None


# ─── Main Loop ───────────────────────────────────────────────────────────────
def run(args):
    sender = DashboardSender(args.url, args.rest_url)

    if args.simulate:
        print(f"[SIM] Simulation mode — streaming to {args.url}")
        sim = Simulator(fs=200, window_sec=2)
        try:
            while True:
                payload = sim.generate()
                ok = sender.send(payload)
                status_icon = "✓" if ok else "✗"
                print(
                    f"[SIM] {status_icon} CPM={payload['gastric_cpm']:.1f} "
                    f"({payload['gastric_status']}) "
                    f"BPM={payload['pulse_rate']} "
                    f"Triage={payload['triage_status'].upper()} "
                    f"SQ={payload['signal_quality']}%"
                )
                time.sleep(SEND_INTERVAL)
        except KeyboardInterrupt:
            print("\n[SIM] Stopped.")
        finally:
            sender.close()
        return

    # ── Real serial mode ──────────────────────────────────────────────────────
    if not SERIAL_AVAILABLE:
        print("[ERROR] pyserial not available. Install it: pip install pyserial")
        sys.exit(1)

    processor = SignalProcessor(fs=args.baud_rate // 10, window_sec=DEFAULT_WINDOW_SEC)

    print(f"[SERIAL] Connecting to {args.port} @ {args.baud} baud...")
    try:
        ser = serial.Serial(args.port, args.baud, timeout=1)
        print(f"[SERIAL] Connected → {args.port}")
    except serial.SerialException as e:
        print(f"[ERROR] Could not open {args.port}: {e}")
        print("  Is the ESP32 connected and the port correct?")
        print("  Try: python vita_streamer.py --simulate")
        sys.exit(1)

    last_send = time.time()
    sample_count = 0

    print(f"[VITA] Streaming to {args.url} — press Ctrl+C to stop\n")

    try:
        while True:
            line = ser.readline().decode('utf-8', errors='ignore')
            if not line:
                continue

            result = parse_serial_line(line)
            if result is None:
                continue

            gastric_val, pulse_val, leads_off = result
            processor.push(gastric_val, pulse_val, leads_off)
            sample_count += 1

            now = time.time()
            if now - last_send >= SEND_INTERVAL and sample_count >= 10:
                payload = processor.process()
                ok = sender.send(payload)
                status_icon = "✓" if ok else "✗"
                print(
                    f"[VITA] {status_icon} CPM={payload['gastric_cpm']:.1f} "
                    f"({payload['gastric_status']}) "
                    f"BPM={payload['pulse_rate']} "
                    f"Triage={payload['triage_status'].upper()} "
                    f"{'(LEADS OFF)' if payload.get('leads_off') else ''}"
                )
                
                # Send scaled CPM back to Arduino TM1637 Display (e.g. 3.2 CPM -> "C:320")
                try:
                    scaled_cpm = int(payload['gastric_cpm'] * 100)
                    ser.write(f"C:{scaled_cpm}\n".encode('utf-8'))
                    
                    # Send Triage Status to Arduino for the Hardware LEDs
                    triage_map = {'red': 'R', 'yellow': 'Y', 'green': 'G', 'unknown': 'G'}
                    t_val = triage_map.get(payload['triage_status'], 'G')
                    if payload.get('leads_off'):
                        t_val = 'R' # Flash Red if leads disconnected
                    ser.write(f"T:{t_val}\n".encode('utf-8'))
                except Exception:
                    pass
                
                last_send = now

    except KeyboardInterrupt:
        print("\n[VITA] Stopped by user.")
    except Exception as e:
        print(f"[ERROR] {e}")
    finally:
        ser.close()
        sender.close()
        print("[VITA] Serial port closed.")


# ─── CLI ─────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(
        description="VITA-CORE ESP32 Biomedical Signal Streamer",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--port", default=DEFAULT_PORT, help=f"Serial port (default: {DEFAULT_PORT})")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help=f"Baud rate (default: {DEFAULT_BAUD})")
    parser.add_argument("--baud-rate", type=int, default=DEFAULT_SAMPLE_RATE, help="Sample rate Hz (default: 200)")
    parser.add_argument("--url", default=DEFAULT_WS_URL, help=f"WebSocket URL (default: {DEFAULT_WS_URL})")
    parser.add_argument("--rest-url", default=DEFAULT_REST_URL, help=f"REST fallback URL (default: {DEFAULT_REST_URL})")
    parser.add_argument("--simulate", action="store_true", help="Run in simulation mode (no ESP32 required)")
    args = parser.parse_args()

    print("=" * 60)
    print("  VITA-CORE Biomedical Signal Streamer")
    print("  ESP32 + AD8232 | Gastric EGG + Pulse PVR")
    print("=" * 60)
    if args.simulate:
        print("  Mode: SIMULATION (synthetic data)")
    else:
        print(f"  Mode: SERIAL — {args.port} @ {args.baud} baud")
    print(f"  Target: {args.url}")
    print("=" * 60 + "\n")

    run(args)


if __name__ == "__main__":
    main()
