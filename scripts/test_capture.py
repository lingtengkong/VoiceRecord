"""Actual WASAPI dual capture and pause test; plays short quiet test tones."""
import sys
import time
from pathlib import Path

project = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(project))
import numpy as np
import pyaudiowpatch as pa
import soundfile as sf
from meeting_scribe.audio import list_devices, Recorder
from meeting_scribe.storage import new_session, atomic_json, data_root

microphones, systems = list_devices()
mic = next(d for d in microphones if "P2418HZm" in d.name)
system = next(d for d in systems if d.default)
folder = new_session("硬件验证 · 测试音", data_root() / "verification")
recorder = Recorder(folder, mic, system)
recorder.start()


def play_tone(hz, seconds=1.5):
    host = recorder.engine.get_host_api_info_by_type(pa.paWASAPI)
    info = recorder.engine.get_device_info_by_index(host["defaultOutputDevice"])
    rate = int(info["defaultSampleRate"])
    tone = .08 * np.sin(2 * np.pi * hz * np.arange(round(seconds*rate)) / rate)
    ramp = min(1000, len(tone)//10)
    tone[:ramp] *= np.linspace(0, 1, ramp)
    tone[-ramp:] *= np.linspace(1, 0, ramp)
    channels = min(2, info["maxOutputChannels"])
    stream = recorder.engine.open(format=pa.paFloat32, channels=channels, rate=rate,
                                  output=True, output_device_index=info["index"])
    try:
        stream.write(np.repeat(tone[:, None], channels, axis=1).astype(np.float32).tobytes())
    finally:
        stream.stop_stream()
        stream.close()


try:
    time.sleep(.6)
    play_tone(440)
    time.sleep(.3)
    recorder.pause()
    time.sleep(.2)
    play_tone(880)
    time.sleep(.3)
    recorder.resume()
    time.sleep(.6)
    play_tone(660)
    time.sleep(.4)
finally:
    meta = recorder.stop()

data, rate = sf.read(folder / "system.wav")
mic_data, mic_rate = sf.read(folder / "microphone.wav")
frequencies = np.fft.rfftfreq(len(data), 1/rate)
power = np.abs(np.fft.rfft(data))**2
def band(hz):
    return float(np.sum(power[np.abs(frequencies-hz)<5]))
assert band(440) > 1000, (band(440), meta)
assert band(660) > 1000, (band(660), meta)
assert band(880) < min(band(440), band(660)) * .01, (band(440),band(660),band(880))
assert abs(len(mic_data)/mic_rate - len(data)/rate) < .2
assert not meta.get("error"), meta
assert (folder / "mixed.wav").exists()
report = {"microphone": mic.name, "system": system.name, "recorded_seconds": len(data)/rate,
          "capture_stats": meta["audio_stats"], "tones_440_and_660_captured": True,
          "paused_880_tone_excluded": True, "dual_track_duration_aligned": True,
          "limits": "Physical monitor microphone opened; headset and real meeting speech not tested",
          "session": str(folder)}
atomic_json(project / "artifacts" / "capture_test.json", report)
print(report)
