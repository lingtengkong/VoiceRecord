"""Offline near-real-time recognition in an expendable worker process.

The recorder publishes durable WAV frame counts. This process only reads them;
there is no microphone, network service, or backpressure into recording here.
"""
from __future__ import annotations

import argparse
import math
import time
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .storage import atomic_json, read_json
from .transcription import configure_runtime, emit, model_path, model_ready, export_transcript

SOURCES = {"microphone": "我 / 麦克风", "system": "会议声音"}
UPDATE_SECONDS = 2.0
WINDOW_SECONDS = 12.0
CONTEXT_SECONDS = 1.0
MAX_BACKLOG = 30.0


def read_audio(path: Path, rate: int, start: float, end: float) -> np.ndarray:
    """Our recorder writes a fixed 44-byte PCM16 mono WAV header."""
    first, last = round(start*rate), round(end*rate)
    if last <= first:
        return np.empty(0, dtype=np.float32)
    with path.open("rb") as f:
        header = f.read(44)
        if header[:4] != b"RIFF" or header[36:40] != b"data":
            raise RuntimeError("实时转写只支持本程序生成的 PCM WAV 音轨。")
        f.seek(44+first*2)
        raw = f.read((last-first)*2)
    data = np.frombuffer(raw[:len(raw)//2*2], dtype="<i2").astype(np.float32) / 32768
    if rate != 16000 and len(data):
        from scipy.signal import resample_poly
        divisor = math.gcd(rate, 16000)
        data = resample_poly(data, 16000//divisor, rate//divisor).astype(np.float32)
    return data


@dataclass
class Track:
    key: str
    cursor: float = 0.0
    seen: float = 0.0
    context_floor: float = 0.0
    partial: list[dict] = field(default_factory=list)


def next_window(track: Track, capture: dict):
    spec = capture.get("tracks", {}).get(track.key)
    if not spec:
        return None
    available = min(spec["frames"] / spec["rate"], capture["position"])
    # A pause is a hard context boundary, even if it was already resumed before
    # inference catches up. Never decode across that edit in the audio timeline.
    boundary = next((b for b in capture.get("pause_boundaries", []) if b > track.cursor + .001), None)
    terminal = capture["state"] == "finished"
    end = min(available, track.cursor + WINDOW_SECONDS + CONTEXT_SECONDS)
    if boundary is not None:
        end = min(end, boundary)
    hard_end = ((boundary is not None and abs(end-boundary) < .001)
                or (terminal and abs(end-available) < .001))
    if end <= track.cursor + .001:
        return None
    at_window_limit = end-track.cursor >= WINDOW_SECONDS+CONTEXT_SECONDS-.01
    if end-track.seen < UPDATE_SECONDS and not hard_end and not at_window_limit:
        return None
    return max(track.context_floor, track.cursor-CONTEXT_SECONDS), end, hard_end, boundary


def rows_from_segments(segments, left: float, start: float, end: float, source: str):
    rows = []
    for segment in segments:
        words = [w for w in (segment.words or []) if start <= left+(w.start+w.end)/2 < end]
        if not words:
            continue
        text = "".join(w.word for w in words).strip()
        if text:
            rows.append({"start": round(max(start, left+words[0].start), 3),
                         "end": round(min(end, left+words[-1].end), 3),
                         "source": source, "text": text})
    return rows


class LiveEngine:
    def __init__(self, folder: Path, model, language="auto", vocabulary="", on_event=None):
        self.folder, self.model = folder, model
        self.language, self.vocabulary = language, vocabulary
        self.on_event = on_event or emit
        from .sources import audio_sources
        self.sources = audio_sources(folder)
        self.tracks = {key: Track(key) for key in self.sources}
        self.rows = []
        self.skipped = []
        self.last_checkpoint = 0.0
        self.changed = False
        self.stopped = False
        self.metadata = {"mode": "live", "model": "turbo", "compute_type": "int8_float16",
                         "device": "cuda", "speaker_diarization": False, "complete": False,
                         "started_at": time.time()}

    def checkpoint(self, force=False):
        if not force and (not self.changed or time.monotonic()-self.last_checkpoint < 1):
            return
        atomic_json(self.folder / "live_draft.json", {
            "metadata": self.metadata, "segments": self.rows,
            "partial": {k: t.partial for k, t in self.tracks.items()}, "skipped_ranges": self.skipped,
        })
        self.last_checkpoint = time.monotonic()
        self.changed = False

    def step(self, capture: dict):
        from faster_whisper.vad import get_speech_timestamps, VadOptions
        for key, track in self.tracks.items():
            spec = capture.get("tracks", {}).get(key)
            if not spec:
                continue
            available = min(capture["position"], spec["frames"]/spec["rate"])
            lag = max(0., available-track.seen)
            if lag > MAX_BACKLOG:
                # Keep preview live instead of growing an unbounded RAM/job queue.
                # Report every skipped range; full audio stays available for redo.
                start = max(track.cursor, available-8)
                missing = {"source": self.sources[key], "start": track.cursor, "end": start}
                self.skipped.append(missing)
                track.cursor = start
                track.seen = start
                track.context_floor = start
                track.partial = []
                self.changed = True
                self.on_event("live_gap", **missing,
                              message="实时转写积压超过 30 秒，已跳到最近音频；跳过部分的原声完整保留，请会后重转。")
            window = next_window(track, capture)
            if window is None:
                continue
            left, end, hard_end, boundary = window
            audio = read_audio(self.folder / f"{key}.wav", spec["rate"], left, end)
            if not len(audio):
                continue
            started = time.monotonic()
            speech = get_speech_timestamps(audio, VadOptions(min_speech_duration_ms=120,
                                           min_silence_duration_ms=550, speech_pad_ms=100))
            # Context belongs to the previous committed block; it cannot by itself
            # count as new speech or hallucinate a repeated line in following silence.
            has_new_speech = any(left+s["end"]/16000 > track.cursor+.08 for s in speech)
            trailing_silence = ((len(audio)-speech[-1]["end"])/16000) if speech else end-left
            finalize = hard_end or trailing_silence >= .65 or end-track.cursor >= WINDOW_SECONDS+CONTEXT_SECONDS-.01
            cut = min(end, track.cursor+WINDOW_SECONDS) if end-track.cursor >= WINDOW_SECONDS+CONTEXT_SECONDS-.01 else end
            if has_new_speech:
                segments, _info = self.model.transcribe(
                    audio, language=None if self.language == "auto" else self.language,
                    task="transcribe", beam_size=1, vad_filter=True,
                    vad_parameters={"min_silence_duration_ms": 550},
                    condition_on_previous_text=False, initial_prompt=self.vocabulary.strip() or None,
                    word_timestamps=True,
                )
                segments = list(segments)
                if finalize and not hard_end:
                    if trailing_silence >= .65:
                        # VAD can miss the first fraction of a following phrase.
                        # Retain half a second rather than irrevocably skipping it.
                        cut = min(cut, end-.5)
                    # End a committed block on a recognized word boundary, not
                    # in the middle of a Chinese word or English syllable.
                    ends = [left+w.end for s in segments for w in (s.words or [])
                            if left+w.start < cut and left+w.end <= end-.25
                            and left+w.end > track.cursor]
                    if ends:
                        cut = max(ends)
                result = rows_from_segments(segments, left, track.cursor,
                                            cut if finalize else end, self.sources[key])
            else:
                result = []
                # Keep a short undecided tail so a very brief new word can acquire context.
                finalize = True
                cut = end if hard_end else max(track.cursor, end-.5)
            track.seen = end
            if finalize:
                self.rows.extend(result)
                for row in result:
                    self.on_event("live_commit", **row)
                track.partial = []
                track.cursor = cut
                if boundary is not None and abs(cut-boundary) < .001:
                    track.context_floor = boundary
            else:
                track.partial = result
            self.changed = True
            self.on_event("live_partial", source=self.sources[key], rows=track.partial,
                          processed_until=end, inference_seconds=round(time.monotonic()-started, 3))
        self.checkpoint()

    def drained(self, capture: dict):
        return all(t.cursor >= min(capture["position"], capture["tracks"].get(k, {}).get("frames", 0)
                                   / max(1, capture["tracks"].get(k, {}).get("rate", 1))) - .002
                   for k, t in self.tracks.items())

    def finish(self, complete=True, reason=""):
        self.metadata.update(complete=complete, preview_has_gaps=bool(self.skipped),
                             skipped_ranges=self.skipped, reason=reason)
        self.checkpoint(force=True)
        # A paused/cancelled worker does not promote unconfirmed partial words.
        destination = export_transcript(self.folder, self.rows, self.metadata)
        self.on_event("live_done", path=str(destination), segments=len(self.rows), complete=complete)
        return destination


def run_live(folder: Path, language="auto", vocabulary=""):
    configure_runtime()
    if not model_ready("turbo"):
        raise RuntimeError("Turbo 本地模型未下载。录音仍可独立保存；请先下载模型。")
    from faster_whisper import WhisperModel
    emit("live_status", message="正在加载 Turbo；录音已独立开始保存…")
    model = WhisperModel(str(model_path("turbo")), device="cuda", compute_type="int8_float16",
                         local_files_only=True, num_workers=1, cpu_threads=2)
    engine = LiveEngine(folder, model, language, vocabulary)
    emit("live_ready", message="Turbo 已就绪 · 临时文字会持续修订")
    last_heartbeat = 0.0
    while True:
        if (folder / "live_cancel.flag").exists():
            return engine.finish(False, "用户停止实时预览；录音继续独立保存")
        capture = read_json(folder / "live_capture.json")
        if not capture:
            time.sleep(.15)
            continue
        age = time.time()-capture.get("updated_at", 0)
        if age > 20 and capture.get("state") != "finished":
            return engine.finish(False, "录音进程心跳中断；已保留实时草稿，原始音频可恢复后重转")
        engine.step(capture)
        if capture["state"] == "finished" and engine.drained(capture):
            return engine.finish()
        if time.monotonic()-last_heartbeat >= .5:
            last_heartbeat = time.monotonic()
            latest = read_json(folder / "live_capture.json") or capture
            backlog = max((max(0, min(latest["position"], latest["tracks"].get(k, {}).get("frames", 0)
                        / max(1, latest["tracks"].get(k, {}).get("rate", 1))) - t.seen)
                          for k, t in engine.tracks.items()), default=0)
            emit("live_health", backlog_seconds=round(backlog, 1), state=latest["state"],
                 committed=len(engine.rows), gaps=len(engine.skipped))
        time.sleep(.12)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("session", type=Path)
    parser.add_argument("--language", choices=("auto", "zh", "en"), default="auto")
    parser.add_argument("--vocabulary", default="")
    args = parser.parse_args()
    try:
        run_live(args.session, args.language, args.vocabulary)
    except Exception as exc:
        emit("live_error", message=str(exc))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
