from __future__ import annotations

import math
import queue
import shutil
import threading
import time
import wave
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
import pyaudiowpatch as pa
import soundfile as sf

from .storage import atomic_json, read_json


@lru_cache(maxsize=1)
def _resampler():
    # Most sessions keep the device's native sample rate. Do not load all of
    # scipy.signal just to display the window or begin native-rate recording.
    from scipy.signal import resample_poly
    return resample_poly


@dataclass
class Device:
    index: int
    name: str
    rate: int
    channels: int
    loopback: bool
    default: bool = False


def list_devices() -> tuple[list[Device], list[Device]]:
    with pa.PyAudio() as engine:
        host = engine.get_host_api_info_by_type(pa.paWASAPI)
        try:
            default_loop = engine.get_default_wasapi_loopback()["index"]
        except OSError:
            default_loop = -1
        devices = []
        for info in engine.get_device_info_generator():
            if info["hostApi"] != host["index"] or not info["maxInputChannels"]:
                continue
            loop = bool(info.get("isLoopbackDevice"))
            devices.append(Device(info["index"], info["name"], int(info["defaultSampleRate"]),
                                  min(2, int(info["maxInputChannels"])), loop,
                                  info["index"] == (default_loop if loop else host["defaultInputDevice"])))
        return ([d for d in devices if not d.loopback], [d for d in devices if d.loopback])


class Timeline:
    def __init__(self):
        self.origin = time.monotonic()
        self.intervals: list[list[float | None]] = []
        self.lock = threading.Lock()

    def pause(self):
        with self.lock:
            if not self.intervals or self.intervals[-1][1] is not None:
                self.intervals.append([time.monotonic(), None])

    def resume(self):
        with self.lock:
            if self.intervals and self.intervals[-1][1] is None:
                self.intervals[-1][1] = time.monotonic()

    def position(self, now: float | None = None) -> float:
        now = time.monotonic() if now is None else now
        with self.lock:
            paused = sum(max(0, min(now, end if end is not None else now) - start)
                         for start, end in self.intervals)
        return max(0, now - self.origin - paused)


class TrackWriter:
    """Bounded queue, timestamped placement, patched WAV headers and durable flushes."""
    def __init__(self, path: Path, rate: int, on_error):
        self.path, self.rate, self.on_error = path, rate, on_error
        self.queue = queue.Queue(maxsize=300)
        self.frames = 0
        self.published_frames = 0
        self.peak = 0.0
        self.gaps = 0
        self.error: Exception | None = None
        self.file = path.open("wb")
        self.wav = wave.open(self.file, "wb")
        self.wav.setparams((1, 2, rate, 0, "NONE", "not compressed"))
        self.wav.writeframes(b"")
        self.last_flush = time.monotonic()
        self.thread = threading.Thread(target=self._run, daemon=True)
        self.thread.start()

    def push(self, position: float, samples: np.ndarray):
        try:
            self.queue.put_nowait((position, samples))
        except queue.Full:
            self.on_error("磁盘写入跟不上录音，已停止以避免继续丢失音频。")

    def _write(self, samples: np.ndarray):
        if not len(samples):
            return
        if (self.frames + len(samples)) * 2 > 3_900_000_000:
            raise RuntimeError("单条音轨接近 WAV 文件上限，请开始新会议。")
        data = (np.clip(samples, -1, 1) * 32767).astype("<i2")
        self.wav.writeframes(data.tobytes())
        self.frames += len(samples)
        self.peak = max(self.peak, float(np.max(np.abs(samples))))
        if time.monotonic() - self.last_flush >= .5:
            self.flush()

    def flush(self):
        self.file.flush()
        import os
        os.fsync(self.file.fileno())
        self.published_frames = self.frames
        self.last_flush = time.monotonic()

    def seal(self, position: float):
        # Queue behind previously captured audio, never block the audio callback.
        try:
            self.queue.put_nowait((position, None))
        except queue.Full:
            self.on_error("磁盘写入队列已满，无法封存暂停边界。")

    def pad_to(self, target: int):
        while target > self.frames:
            self._write(np.zeros(min(target-self.frames, self.rate), dtype=np.float32))

    def _run(self):
        try:
            while True:
                item = self.queue.get()
                if item is None:
                    break
                position, samples = item
                if samples is None:
                    self.pad_to(round(position * self.rate))
                    self.flush()
                    continue
                target = round(position * self.rate)
                delta = target - self.frames
                if delta > self.rate * .12:
                    self.gaps += 1
                    self.pad_to(target)
                elif delta < -self.rate * .12:
                    samples = samples[min(len(samples), -delta):]
                self._write(samples)
        except Exception as exc:
            self.error = exc
            self.on_error(f"录音写入失败：{exc}")

    def finish(self, duration: float):
        if self.thread.is_alive():
            self.queue.put(None, timeout=10)
            self.thread.join(timeout=15)
        if self.thread.is_alive():
            raise RuntimeError("音频文件仍在写入，暂时不能关闭。")
        try:
            if not self.error:
                self.pad_to(round(duration*self.rate))
                self.flush()
        finally:
            self.wav.close()
            self.file.close()


class Recorder:
    def __init__(self, folder: Path, microphone: Device, system: Device | None):
        self.folder = folder
        self.devices = {"microphone": microphone}
        if system is not None:
            self.devices['system'] = system
        self.engine = None
        self.streams = {}
        self.stream_gates = {}
        self.extra_engines = {}
        self.switching = set()
        self.callback_locks = {key: threading.Lock() for key in self.devices}
        self.writers = {}
        self.levels = {"microphone": 0., "system": 0.}
        self.last_signal = {"microphone": 0., "system": 0.}
        self.last_callback = {"microphone": 0., "system": 0.}
        self.status_flags = {"microphone": 0, "system": 0}
        self.timeline = Timeline()
        self.paused = False
        self.accepting = False
        self.error = ""
        self.active = False
        self.meta = read_json(folder / "session.json")
        self.meta['capture_mode'] = 'dual' if system is not None else 'microphone_only'
        self.last_save = 0.0
        self.last_live = 0.0
        self.pause_boundaries = []
        self.capture_state = "preparing"
        self.live_warning = ""

    def fail(self, message: str):
        if not self.error:
            self.error = message

    def start(self):
        if shutil.disk_usage(self.folder).free < 1024**3:
            raise RuntimeError("磁盘可用空间不足 1GB，请先释放空间。")
        self.engine = pa.PyAudio()
        try:
            for key, device in self.devices.items():
                # Never silently attach a changed device index after Bluetooth reconnects.
                current = self.engine.get_device_info_by_index(device.index)
                if current["name"] != device.name or bool(current.get("isLoopbackDevice")) != device.loopback:
                    raise RuntimeError("音频设备已变化，请刷新设备并重新选择。")
                self.writers[key] = TrackWriter(self.folder / f"{key}.wav", device.rate, self.fail)
                self.stream_gates[key] = threading.Event()
                self.stream_gates[key].set()
                self.streams[key] = self.engine.open(
                    format=pa.paFloat32, channels=device.channels, rate=device.rate,
                    input=True, input_device_index=device.index,
                    frames_per_buffer=max(256, device.rate // 20), start=False,
                    stream_callback=self._callback(key, device, self.stream_gates[key]),
                )
            self.timeline = Timeline()
            self.accepting = True
            for stream in self.streams.values():
                stream.start_stream()
            self.active = True
            self.meta.update(status="recording", devices={k: vars(d) for k, d in self.devices.items()})
            atomic_json(self.folder / "session.json", self.meta)
            self.capture_state = "recording"
            self.publish_live()
        except Exception:
            self.error = "启动失败：请检查麦克风权限和设备是否被独占。"
            self.stop(create_mix=False)
            raise

    def switch_device(self, key: str, device: Device):
        """Open the replacement first; retain writers, timeline and the other stream."""
        if not self.active or key not in self.devices or self.switching:
            raise RuntimeError("当前无法切换设备，请等待录音操作完成。")
        old_device = self.devices[key]
        if device == old_device:
            return
        if device.loopback != (key == 'system'):
            raise ValueError("设备类型与录音音轨不匹配。")
        self.switching.add(key)
        engine, stream = None, None
        gate = threading.Event()
        started = self.timeline.position()
        committed = False
        try:
            if device.rate != self.writers[key].rate:
                # Load before enabling the replacement stream, never for the
                # first time in its real-time audio callback.
                _resampler()
            engine = pa.PyAudio()
            current = engine.get_device_info_by_index(device.index)
            if current['name'] != device.name or bool(current.get('isLoopbackDevice')) != device.loopback:
                raise RuntimeError("音频设备已变化，请刷新后重新选择。")
            stream = engine.open(format=pa.paFloat32, channels=device.channels, rate=device.rate,
                                 input=True, input_device_index=device.index,
                                 frames_per_buffer=max(256, device.rate//20), start=False,
                                 stream_callback=self._callback(key, device, gate))
            stream.start_stream()
            if not stream.is_active():
                raise RuntimeError("新设备未能启动。")
            old_stream = self.streams[key]
            old_engine = self.extra_engines.get(key)
            old_gate = self.stream_gates[key]
            with self.callback_locks[key]:
                old_gate.clear()
                self.streams[key] = stream
                self.stream_gates[key] = gate
                self.extra_engines[key] = engine
                self.devices[key] = device
                self.levels[key] = 0.
                self.last_callback[key] = time.monotonic()
                committed = True
                gate.set()
            self.meta['devices'] = {k: vars(d) for k, d in self.devices.items()}
            self.meta.setdefault('device_changes', []).append(dict(
                track=key, requested_at=started, position=self.timeline.position(),
                previous=vars(old_device), current=vars(device), track_rate=self.writers[key].rate))
            try:
                if old_stream.is_active():
                    old_stream.stop_stream()
                old_stream.close()
            finally:
                if old_engine:
                    old_engine.terminate()
            atomic_json(self.folder / 'session.json', self.meta)
            self.publish_live()
        except Exception as exc:
            if not committed:
                if stream:
                    try:
                        stream.close()
                    except Exception:
                        pass
                if engine:
                    engine.terminate()
                raise RuntimeError(f"切换失败，原设备继续录音：{exc}") from exc
            raise RuntimeError(f"新设备已启用，但旧设备清理或切换记录保存失败：{exc}") from exc
        finally:
            self.switching.discard(key)

    def _callback(self, key: str, device: Device, gate=None):
        def process_block(data, count, timing, flags):
            if gate is not None and not gate.is_set():
                return (None, pa.paContinue)
            self.last_callback[key] = time.monotonic()
            if flags:
                self.status_flags[key] += 1
            if not self.accepting or self.paused:
                self.levels[key] = 0
                return (None, pa.paContinue)
            try:
                samples = np.frombuffer(data, dtype=np.float32).reshape(-1, device.channels).mean(axis=1)
                self.levels[key] = float(np.max(np.abs(samples))) if len(samples) else 0.
                if self.levels[key] > .001:
                    self.last_signal[key] = time.monotonic()
                now = time.monotonic()
                latency = timing.get("current_time", 0) - timing.get("input_buffer_adc_time", 0)
                if not (0 <= latency < 2) or timing.get("input_buffer_adc_time", 0) <= 0:
                    latency = count / device.rate
                position = self.timeline.position(now - latency)
                target_rate = self.writers[key].rate
                if device.rate != target_rate:
                    divisor = math.gcd(device.rate, target_rate)
                    samples = _resampler()(samples, target_rate//divisor, device.rate//divisor)
                if gate is not None and not gate.is_set():
                    return (None, pa.paContinue)
                self.writers[key].push(position, samples.copy())
            except Exception as exc:
                self.fail(f"音频采集失败：{exc}")
                return (None, pa.paAbort)
            return (None, pa.paContinue)
        def callback(data, count, timing, flags):
            with self.callback_locks[key]:
                return process_block(data, count, timing, flags)
        return callback

    def pause(self):
        self.paused = True
        self.levels = {"microphone": 0., "system": 0.}
        self.timeline.pause()
        position = self.timeline.position()
        self.pause_boundaries.append(position)
        for writer in self.writers.values():
            writer.seal(position)
        self.capture_state = "paused"
        self.publish_live()

    def resume(self):
        self.timeline.resume()
        self.paused = False
        self.capture_state = "recording"
        self.publish_live()

    def publish_live(self, duration=None):
        # The live worker reads durable audio only. It never touches the callback
        # or its queue, so a slow/crashed recognizer cannot hold up recording.
        try:
            atomic_json(self.folder / "live_capture.json", {
                "state": self.capture_state,
                "position": self.timeline.position() if duration is None else duration,
                "updated_at": time.time(), "pause_boundaries": self.pause_boundaries,
                "tracks": {key: {"rate": w.rate, "frames": w.published_frames}
                           for key, w in self.writers.items()},
            })
            self.live_warning = ""
        except OSError as exc:
            self.live_warning = f"实时状态暂时无法更新，录音继续保存：{exc}"

    def heartbeat(self):
        if not self.active:
            return
        if time.monotonic() - self.last_live >= .5:
            self.last_live = time.monotonic()
            self.publish_live()
        for key in self.streams:
            with self.callback_locks[key]:
                if key in self.switching:
                    continue
                stream = self.streams[key]
                if time.monotonic() - self.last_callback[key] > .3:
                    self.levels[key] = 0.
                if not stream.is_active():
                    self.fail(f"{'麦克风' if key == 'microphone' else '会议声音'}设备已停止响应。录音将保存，请重新选择设备。")
        if time.monotonic() - self.last_save >= 3:
            self.last_save = time.monotonic()
            if shutil.disk_usage(self.folder).free < 256 * 1024**2:
                self.fail("磁盘可用空间不足 256MB，正在保存录音。")
            if self.timeline.position() > 8 * 3600:
                self.fail("本次录音已达到 8 小时，请开始新会议。")
            self.meta.update(duration=self.timeline.position(), status="paused" if self.paused else "recording")
            atomic_json(self.folder / "session.json", self.meta)

    def silence_seconds(self) -> float:
        """Silence on meeting audio (or sole microphone), excluding paused time."""
        if not self.active or self.paused:
            return 0.
        key = 'system' if 'system' in self.devices else 'microphone'
        last = self.last_signal[key]
        last_position = self.timeline.position(last) if last else 0.
        return max(0., self.timeline.position() - last_position)

    def stop(self, create_mix=True) -> dict:
        duration = self.timeline.position()
        self.accepting = False
        self.capture_state = "stopping"
        self.publish_live(duration)
        for stream in self.streams.values():
            try:
                if stream.is_active():
                    stream.stop_stream()
                stream.close()
            except Exception as exc:
                self.fail(str(exc))
        self.streams.clear()
        for engine in self.extra_engines.values():
            engine.terminate()
        self.extra_engines.clear()
        if self.engine:
            self.engine.terminate()
            self.engine = None
        for writer in self.writers.values():
            try:
                writer.finish(duration)
            except Exception as exc:
                self.fail(str(exc))
        self.active = False
        self.meta.update(duration=duration, status="interrupted" if self.error else "saved", error=self.error,
                         audio_stats={k: {"frames": w.frames, "rate": w.rate, "peak": w.peak,
                                          "gaps": w.gaps, "capture_status_flags": self.status_flags[k]}
                                      for k, w in self.writers.items()})
        atomic_json(self.folder / "session.json", self.meta)
        self.capture_state = "finished"
        self.publish_live(duration)
        if create_mix and len(self.writers) == 2:
            try:
                mix_tracks(self.folder)
            except Exception as exc:
                self.meta["mix_error"] = str(exc)
                atomic_json(self.folder / "session.json", self.meta)
        return self.meta


def mix_tracks(folder: Path) -> Path:
    from scipy.signal import resample_poly
    destination = folder / "mixed.wav"
    temporary = folder / "mixed.partial.wav"
    inputs = [sf.SoundFile(str(folder / f"{name}.wav")) for name in ("microphone", "system")]
    try:
        duration = max(len(f) / f.samplerate for f in inputs)
        remaining = round(duration * 48000)
        with sf.SoundFile(str(temporary), mode="w", samplerate=48000, channels=1, subtype="PCM_16") as output:
            while remaining > 0:
                count = min(48000, remaining)
                mixed = np.zeros(count, dtype=np.float32)
                for f in inputs:
                    data = f.read(f.samplerate, dtype="float32", always_2d=True).mean(axis=1)
                    if f.samplerate != 48000 and len(data):
                        divisor = math.gcd(48000, f.samplerate)
                        data = resample_poly(data, 48000 // divisor, f.samplerate // divisor)
                    n = min(count, len(data))
                    mixed[:n] += data[:n] * .5
                output.write(mixed)
                remaining -= count
        temporary.replace(destination)
    finally:
        for f in inputs:
            f.close()
    return destination


def recover_session(folder: Path) -> dict:
    """Patch only our uncompressed PCM WAV headers; never alter sample payload."""
    import struct
    meta = read_json(folder / "session.json")
    if meta.get("status") not in ("recording", "paused", "preparing"):
        return meta
    durations = []
    for name in ("microphone.wav", "system.wav"):
        path = folder / name
        if not path.exists() or path.stat().st_size < 44:
            continue
        with path.open("r+b") as f:
            header = f.read(44)
            if header[:4] != b"RIFF" or header[8:12] != b"WAVE" or header[36:40] != b"data":
                continue
            size = (path.stat().st_size - 44) // 2 * 2
            f.seek(4)
            f.write(struct.pack("<I", size + 36))
            f.seek(40)
            f.write(struct.pack("<I", size))
            rate = struct.unpack("<I", header[24:28])[0]
            durations.append(size / 2 / rate)
    meta.update(status="recovered", duration=max(durations, default=meta.get("duration", 0)),
                error="上次录音未正常结束，已恢复已落盘的音轨；末尾可能不完整。")
    atomic_json(folder / "session.json", meta)
    return meta
