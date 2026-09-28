"""Isolated Nemotron trial on synthetic voices; does not modify meeting recordings."""
import argparse
import json
import os
import socket
import threading
import time
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

import numpy as np
import psutil
import soundfile as sf
import torch
from transformers import AutoModelForAudioFrameClassification, AutoProcessor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cuda")
    parser.add_argument("--streaming", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    torch.set_num_threads(4)
    # Prove that inference cannot connect to a remote service.
    def denied(*unused, **kwargs):
        raise RuntimeError("Network disabled for local inference verification")
    socket.socket.connect = denied
    socket.create_connection = denied
    process = psutil.Process()
    peak_ram = [process.memory_info().rss]
    stop = threading.Event()
    def sample():
        while not stop.wait(.05):
            peak_ram[0] = max(peak_ram[0], process.memory_info().rss)
    thread = threading.Thread(target=sample, daemon=True)
    thread.start()
    started = time.perf_counter()
    processor = AutoProcessor.from_pretrained(str(args.model), local_files_only=True)
    model = AutoModelForAudioFrameClassification.from_pretrained(
        str(args.model), local_files_only=True).to(args.device).eval()
    if args.device == "cuda":
        torch.cuda.synchronize()
        torch.cuda.reset_peak_memory_stats()
    loaded = time.perf_counter()
    audio, rate = sf.read(args.audio, dtype="float32")
    assert rate == 16000 and audio.ndim == 1
    with torch.inference_mode():
        if args.streaming:
            processor.set_streaming_mode("low_latency")
            def features():
                yield processor(audio[:processor.num_samples_first_audio_chunk], sampling_rate=rate,
                                is_streaming=True, is_first_audio_chunk=True)
                frame = processor.num_mel_frames_per_step
                start = processor.audio_chunk_start(frame)
                while (end := start + processor.num_samples_per_audio_chunk) <= len(audio):
                    yield processor(audio[start:end], sampling_rate=rate, is_streaming=True,
                                    is_first_audio_chunk=False)
                    frame += processor.num_mel_frames_per_step
                    start = processor.audio_chunk_start(frame)
                yield processor(audio[start:], sampling_rate=rate, is_streaming=True,
                                is_first_audio_chunk=False, is_last_audio_chunk=True)
            cache, pieces = None, []
            for inputs in features():
                inputs = inputs.to(model.device, dtype=model.dtype)
                output = model(**inputs, speaker_cache=cache)
                pieces.append(output.logits)
                cache = output.speaker_cache
            logits = torch.cat(pieces, dim=1)
            attention_mask = None
        else:
            inputs = processor(audio, sampling_rate=rate).to(model.device, dtype=model.dtype)
            logits = model(**inputs).logits
            attention_mask = inputs.attention_mask
    if args.device == "cuda":
        torch.cuda.synchronize()
    inferred = time.perf_counter()
    segments = processor.extract_speaker_dict(logits, attention_mask)[0]
    report = {"device": args.device, "mode": "streaming" if args.streaming else "full_attention", "audio_seconds": len(audio)/rate,
              "load_seconds": loaded-started, "inference_seconds": inferred-loaded,
              "parameters": sum(p.numel() for p in model.parameters()),
              "peak_ram_mib": peak_ram[0]/1024**2,
              "peak_cuda_allocated_mib": torch.cuda.max_memory_allocated()/1024**2 if args.device == "cuda" else 0,
              "peak_cuda_reserved_mib": torch.cuda.max_memory_reserved()/1024**2 if args.device == "cuda" else 0,
              "network_blocked": True, "segments": segments}
    if args.reference:
        ref = json.loads(args.reference.read_text())
        counts = {}
        for turn in ref["turns"]:
            votes = {}
            for seg in segments:
                overlap = max(0, min(turn["end"], seg["End"])-max(turn["start"], seg["Start"]))
                if overlap:
                    key = str(seg["Speaker"])
                    votes[key] = votes.get(key, 0)+overlap
            turn["predicted_seconds"] = votes
            turn["dominant_prediction"] = max(votes, key=votes.get) if votes else None
            counts.setdefault(str(turn["speaker"]), set()).add(turn["dominant_prediction"])
        report["reference_turns"] = ref["turns"]
        report["predicted_speaker_count"] = len({str(s["Speaker"]) for s in segments})
        report["consistent_repeated_voices"] = all(len(v)==1 and None not in v for v in counts.values())
        report["distinct_reference_voices"] = len({next(iter(v)) for v in counts.values() if len(v)==1}) == len(counts)
    stop.set()
    thread.join()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k:v for k,v in report.items() if k not in ("segments","reference_turns")}, indent=2))
    for segment in segments:
        print(segment)


if __name__ == "__main__":
    main()
