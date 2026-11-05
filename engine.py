"""Audio engine: every channel owns its own output stream, so every channel can use a different output device."""
import os, threading
import numpy as np
import soundfile as sf
import scipy.signal as ss
try:
    import sounddevice as sd
except Exception:  # PortAudio missing (tests / unsupported system)
    sd = None


class Master:
    v = 1.0


class Fx:
    """Equalizer settings shared by all channels: 5 bands in dB + bass boost. ver changes whenever they change."""
    ver = 0
    gains = [0, 0, 0, 0, 0]
    bass = 0.0
    FREQS = [60, 230, 910, 3600, 14000]


def biquad(kind, f, gain_db, sr):
    A = 10 ** (gain_db / 40.0)
    w = 2 * np.pi * f / sr
    cw, sw = np.cos(w), np.sin(w)
    if kind == "peak":
        al = sw / 2.0
        b0, b1, b2, a0, a1, a2 = 1 + al * A, -2 * cw, 1 - al * A, 1 + al / A, -2 * cw, 1 - al / A
    else:
        sq = 2 * np.sqrt(A) * (sw / 2 * np.sqrt(2))
        if kind == "low":
            b0, b1, b2 = A * ((A + 1) - (A - 1) * cw + sq), 2 * A * ((A - 1) - (A + 1) * cw), A * ((A + 1) - (A - 1) * cw - sq)
            a0, a1, a2 = (A + 1) + (A - 1) * cw + sq, -2 * ((A - 1) + (A + 1) * cw), (A + 1) + (A - 1) * cw - sq
        else:
            b0, b1, b2 = A * ((A + 1) + (A - 1) * cw + sq), -2 * A * ((A - 1) + (A + 1) * cw), A * ((A + 1) + (A - 1) * cw - sq)
            a0, a1, a2 = (A + 1) - (A - 1) * cw + sq, 2 * ((A - 1) - (A + 1) * cw), (A + 1) - (A - 1) * cw - sq
    return [b0 / a0, b1 / a0, b2 / a0, 1.0, a1 / a0, a2 / a0]


def fmt(s):
    s = int(max(0, s or 0))
    h, m, c = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{c:02d}" if h else f"{m}:{c:02d}"


def wasapi_index():
    if not sd:
        return None
    try:
        for i, a in enumerate(sd.query_hostapis()):
            if "WASAPI" in a["name"]:
                return i
    except Exception:
        pass
    return None


def outputs():
    """[(device_index, name)]; only WASAPI devices on Windows (other host APIs list the same device again)."""
    if not sd:
        return []
    try:
        w, res = wasapi_index(), []
        for i, d in enumerate(sd.query_devices()):
            if d["max_output_channels"] > 0 and (w is None or d["hostapi"] == w):
                res.append((i, d["name"]))
        return res
    except Exception:
        return []


def default_output():
    if not sd:
        return None
    try:
        w = wasapi_index()
        if w is not None:
            return sd.query_hostapis(w)["default_output_device"]
        d = sd.default.device[1]
        return d if d >= 0 else None
    except Exception:
        return None


def peaks(path, n=400):
    try:
        with sf.SoundFile(path) as f:
            k, out = max(1, f.frames // n), []
            for _ in range(n):
                d = f.read(k, dtype="float32", always_2d=True)
                if len(d) == 0:
                    break
                out.append(float(np.abs(d).max()))
        m = max(out) or 1.0
        return [v / m for v in out]
    except Exception:
        return []


class Chan:
    def __init__(self, path, name=None, dur=0.0, vol=1.0, dev=None):
        self.path = path
        self.name = name or os.path.splitext(os.path.basename(path))[0]
        self.dur, self.vol, self.dev = dur, vol, dev
        self.mute = False                              # per-channel mute (dev = device name, None = default output)
        self.cue = False                               # Pre-listen / Cue state
        self.sf = self.stream = None
        self.sr, self.total = 44100, 0
        self.lock = threading.Lock()
        self.playing = self.ended = False
        self.gain = self.gtarget = 0.0
        self.gstep = 1.0
        self.peaks = None
        self.played = 0.0
        self.start_at = self.stop_at = None            # one-shot schedule (datetime), clears itself when done
        self.fxver, self.sos, self.zi = -1, None, None

    def _mk_filters(self):
        self.fxver = Fx.ver
        kinds = ["low", "peak", "peak", "peak", "high"]
        secs = [biquad(k, f, g, self.sr) for k, f, g in zip(kinds, Fx.FREQS, Fx.gains) if g]
        if Fx.bass > 0:
            secs.append(biquad("low", 100, Fx.bass, self.sr))
        self.sos = np.array(secs, dtype=np.float64) if secs else None
        self.zi = np.zeros((len(secs), 2, 2)) if secs else None

    # ---- stream handling
    def open(self):
        if self.sf is None:
            self.sf = sf.SoundFile(self.path)
            self.sr, self.total = self.sf.samplerate, self.sf.frames
        return self.sf

    def _dev_index(self):
        # अगर Cue ऑन है, तो यह अपने आप हेडफोन/हेडसेट को ढूंढकर आवाज उधर भेज देगा
        if getattr(self, 'cue', False):
            # पहले चेक करेगा कि क्या कोई खास क्यू डिवाइस सेट है
            if hasattr(self, 'cue_dev') and self.cue_dev is not None:
                return self.cue_dev
            
            # ऑटो-डिटेक्ट: डिवाइस के नाम में Headphone या Headset ढूंढकर अपने आप चुन लेगा
            for i, n in outputs():
                if 'headphone' in n.lower() or 'headset' in n.lower() or 'earphone' in n.lower():
                    return i
            
            # अगर नाम से न मिले, तो डिफ़ॉल्ट आउटपुट का उपयोग करेगा
            return default_output()
        
        # सामान्य स्थिति में आपके द्वारा चुने गए मेन डिवाइस पर बजेगा
        if self.dev:
            for i, n in outputs():
                if self.dev == n or self.dev in n:
                    return i
        return default_output()

    def ensure_stream(self):
        if self.stream is not None:
            return
        self.open()
        idx, extra = self._dev_index(), None
        try:
            w = wasapi_index()
            if w is not None and idx is not None and sd.query_devices(idx)["hostapi"] == w:
                extra = sd.WasapiSettings(auto_convert=True)
        except Exception:
            pass
        self.stream = sd.OutputStream(samplerate=self.sr, device=idx, channels=2, dtype="float32",
                                      latency="high", callback=self._cb, extra_settings=extra)
        self.stream.start()

    def close_stream(self):
        s, self.stream = self.stream, None
        if s:
            try:
                s.abort(); s.close()
            except Exception:
                pass

    def close(self):
        self.playing = False
        self.close_stream()
        if self.sf:
            self.sf.close(); self.sf = None

    def set_device(self, name):
        self.dev = name or None
        self.close_stream()

    # ---- audio callback (runs on the audio thread)
    def _cb(self, out, frames, t, status):
        # Cue ऑन होने पर मेन आउटपुट को म्यूट (Silent) करने के लिए
        if getattr(self, 'cue', False):
            out.fill(0)
            return
        if not self.playing and self.gain <= 0.0:
            out.fill(0)
            return
        with self.lock:
            d = self.sf.read(frames, dtype="float32", always_2d=True)
        n = len(d)
        if d.shape[1] == 1:
            d = np.repeat(d, 2, axis=1)
        elif d.shape[1] > 2:
            d = d[:, :2]
        if Fx.ver != self.fxver:
            self._mk_filters()
        if self.sos is not None:
            d, self.zi = ss.sosfilt(self.sos, d, axis=0, zi=self.zi)
        g, tg = self.gain, self.gtarget
        if g != tg:
            step = self.gstep * n
            ng = min(tg, g + step) if tg > g else max(tg, g - step)
            ramp = np.linspace(g, ng, n, dtype=np.float32)[:, None]
            self.gain = ng
        else:
            ramp = g
        out[:n] = np.clip(d * ramp * (0.0 if self.mute else self.vol * Master.v), -1.0, 1.0)
        self.played = min(self.dur or 1e9, self.played + n / self.sr)
        if n < frames:
            out[n:] = 0
            self.ended, self.playing = True, False
            self.gain = self.gtarget = 0.0
            with self.lock:
                self.sf.seek(0)

    # ---- controls
    def play(self, fade=1.2, restart=False):
        self.ensure_stream()
        if restart:
            self.seek(0)
        self.ended = False
        self.gstep = 1.0 / (max(fade, 0.01) * self.sr)
        self.gtarget, self.playing = 1.0, True

    def pause(self, fade=0.15):
        self.gstep = 1.0 / (max(fade, 0.01) * self.sr)
        self.gtarget, self.playing = 0.0, False

    def pos(self):
        return self.sf.tell() / self.sr if self.sf else 0.0

    def seek(self, sec):
        self.open()
        with self.lock:
            self.sf.seek(int(max(0, min(sec * self.sr, self.total - 1))))
