import sounddevice as sd
import soundfile as sf
import numpy as np

def outputs():
    devices = sd.query_devices()
    out_devs = []
    for i, dev in enumerate(devices):
        if dev['max_output_channels'] > 0:
            out_devs.append((i, dev['name']))
    return out_devs

def default_output():
    return sd.default.device[1]

class Chan:
    def __init__(self, path):
        self.path = path
        self.sf = None
        self.stream = None
        self.dev = None
        self.cue = False          # Cue स्टेटस यहाँ इनिशियलाइज है
        self.volume = 1.0
        self.pos = 0
        self.total = 0
        self.sr = 44100

    def open(self):
        if self.sf is None:
            self.sf = sf.SoundFile(self.path)
            self.sr, self.total = self.sf.samplerate, self.sf.frames

    def _dev_index(self):
        # यदि Cue ऑन है, तो इसे प्री-लिस्निंग या डिफ़ॉल्ट हेडफोन डिवाइस पर भेजें
        if getattr(self, 'cue', False):
            return default_output()
        if self.dev:
            for i, n in outputs():
                if self.dev == n or self.dev in n:
                    return i
        return default_output()

    def callback(self, outdata, frames, time_info, status):
        if self.sf is None:
            outdata.fill(0)
            return

        # **मुख्य लॉजिक:** अगर Cue ऑन है, तो मेन आउटपुट पर आवाज पूरी तरह बंद (Silent) हो जाएगी
        if getattr(self, 'cue', False):
            outdata.fill(0)
            return

        data = self.sf.read(frames, dtype='float32', always_2d=True)
        if len(data) == 0:
            outdata.fill(0)
            return

        # वॉल्यूम और ऑडियो डेटा सेट करना
        if len(data) < frames:
            padded = np.zeros((frames, data.shape[1]), dtype='float32')
            padded[:len(data)] = data
            data = padded

        outdata[:] = data * self.volume

    def play(self):
        self.open()
        if self.stream is None or not self.stream.active:
            device_idx = self._dev_index()
            self.stream = sd.OutputStream(
                device=device_idx,
                samplerate=self.sr,
                channels=2,
                callback=self.callback
            )
            self.stream.start()

    def stop(self):
        if self.stream:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        if self.sf:
            self.sf.seek(0)

    class Master:
        def __init__(self):
            self.volume = 1.0

        def set_volume(self, v):
            self.volume = float(v)
