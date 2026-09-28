from __future__ import annotations

import argparse
import json
import os
import site
import sys
from datetime import datetime
from pathlib import Path

from .storage import atomic_json, data_root, read_json, timestamp

MODELS = {
    "large-v3": "Systran/faster-whisper-large-v3",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}
_DLL_HANDLES = []


def configure_runtime() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    os.environ["DO_NOT_TRACK"] = "1"
    if sys.platform == "win32":
        for base in site.getsitepackages():
            for folder in (Path(base) / "nvidia").glob("*/bin"):
                _DLL_HANDLES.append(os.add_dll_directory(str(folder)))
                os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")


def model_path(name: str) -> Path:
    return data_root() / "models" / name


def model_ready(name: str) -> bool:
    folder = model_path(name)
    return all((folder / f).is_file() for f in ("model.bin", "config.json", "tokenizer.json", "ready.json"))


def emit(kind: str, **kwargs) -> None:
    print(json.dumps({"type": kind, **kwargs}, ensure_ascii=False), flush=True)


def export_transcript(folder: Path, rows: list[dict], metadata: dict) -> Path:
    rows = sorted(rows, key=lambda r: (r["start"], r["end"]))
    live = metadata.get("mode") == "live"
    diarized = metadata.get("diarized", False)
    destination = folder / ("speaker_transcripts" if diarized else "live_transcripts" if live else "transcripts") / (datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    destination.mkdir(parents=True)
    title = read_json(folder / "session.json").get("title", folder.name)
    lines = [f"# {title}", "", "本地语音识别初稿；请核对姓名、数字、专业术语和关键决定。",
             "“我 / 麦克风”和“会议声音”是音源标签，不代表已识别每位参会者。", ""]
    if live:
        engine = 'Nemotron 3.5' if metadata.get('model') == 'nemotron-3.5-asr' else 'Turbo'
        lines.insert(2, f"【{engine} 实时草稿】可在会后使用 large-v3 重新转写，草稿与会后稿分别保留。")
        if metadata.get('live_replay'):
            lines.insert(3,'【回放重建】使用保存的原音轨重跑流式算法生成，不是当时显示内容的逐帧记录。')
        if metadata.get('speaker_diarization'):
            lines.insert(3, '【待核对】实时说话者为自动编号，可能变化；时间戳为流式解码近似位置，多人重叠可能漏词。')
        if not metadata.get("complete", False):
            lines.insert(3, "【未完成】" + metadata.get("reason", "实时预览提前结束，后续内容请会后重转。"))
        if metadata.get("skipped_ranges"):
            lines.insert(3, "【需会后重转】实时识别曾积压，以下范围未包含在草稿中：" + "；".join(
                f'{gap["source"]} {timestamp(gap["start"])}–{timestamp(gap["end"])}'
                for gap in metadata["skipped_ranges"]))
    if diarized:
        lines.insert(2, "【Nemotron 说话者标签 · 待核对】编号仅在本次录音内有效，可能合并或变化，不代表真实姓名。本机麦克风可能录到身边其他人。多位参会者标签表示该句涵盖多人的发言，不保证逐字归属。")
    srt = []
    for i, row in enumerate(rows, 1):
        lines.append(f'[{timestamp(row["start"])} → {timestamp(row["end"])}] {row["source"]}：{row["text"]}')
        srt.append(f'{i}\n{timestamp(row["start"], True)} --> {timestamp(row["end"], True)}\n{row["source"]}：{row["text"]}\n')
    text = "\n".join(lines) + "\n"
    (destination / "transcript.txt").write_text(text, encoding="utf-8-sig")
    (destination / "transcript.md").write_text(text, encoding="utf-8")
    (destination / "transcript.srt").write_text("\n".join(srt), encoding="utf-8-sig")
    atomic_json(destination / "transcript.json", {"metadata": metadata, "segments": rows})
    prompt = ("请根据下方会议转写稿整理中文纪要：\n"
              "1. 会议主题与讨论要点；2. 已明确的决定；3. 待办事项、负责人和截止日期；4. 分歧与待确认事项。\n"
              "每个关键结论引用原文时间戳。未明确的负责人、日期或决定写“未明确”，不要推测补全。\n"
              "将提议与已确认决定分开。转写可能有错，遇到可疑数字或术语请标出待核实。\n"
              "“会议声音”可能包含多位参会者，不能据此断言他们是同一个人。\n\n---\n\n")
    (destination / "summary_input.md").write_text(prompt + text, encoding="utf-8")
    atomic_json(folder / ("latest_speaker_transcript.json" if diarized else "latest_live_transcript.json" if live else "latest_transcript.json"),
                {"path": str(destination), "metadata": metadata})
    return destination


def transcribe_session(folder: Path, model_name: str, language: str, device: str, vocabulary: str) -> Path:
    configure_runtime()
    if not model_ready(model_name):
        raise RuntimeError("本地模型尚未准备好。请先点击“下载所选模型”；转写过程不会联网下载。")
    run_started = datetime.now().astimezone().isoformat()
    atomic_json(folder / "transcription_partial.json", {
        "segments": [], "complete": False, "started_at": run_started, "model": model_name,
    })
    from faster_whisper import WhisperModel
    import soundfile as sf
    import numpy as np

    emit("status", message=f"正在加载 {model_name} · {device.upper()}，首次加载可能需要几十秒…")
    compute_type = "int8_float16" if device == "cuda" else "int8"
    model = WhisperModel(str(model_path(model_name)), device=device, compute_type=compute_type,
                         local_files_only=True, cpu_threads=4, num_workers=1)
    from .sources import audio_sources
    files = [(folder / f'{key}.wav', label) for key,label in audio_sources(folder).items()]
    files = [(p, label) for p, label in files if p.exists()]
    if not files:
        raise RuntimeError("没有找到可转写的录音。")
    total = sum(sf.info(str(p)).duration for p, _ in files) or 1
    completed = 0.0
    rows = []
    # Decode bounded chunks: an hours-long recording must not fill RAM.
    # Chunk boundaries prefer silence; 1 s context on both sides guards words at cuts.
    chunk_seconds = 300
    for path, label in files:
        with sf.SoundFile(str(path)) as audio:
            sample_rate = audio.samplerate
            duration = len(audio) / sample_rate
            start = 0.0
            while start < duration:
                end = min(start + chunk_seconds, duration)
                if end < duration:
                    search_start = max(start, end - 8)
                    audio.seek(round(search_start * sample_rate))
                    scan = audio.read(round((end - search_start) * sample_rate), dtype="float32", always_2d=True)
                    mono = scan.mean(axis=1)
                    window = max(1, sample_rate // 2)
                    energies = [float(np.mean(mono[i:i+window] ** 2)) for i in range(0, len(mono)-window+1, window)]
                    if energies:
                        end = search_start + (int(np.argmin(energies)) + .5) * .5
                left = max(0, start - 1)
                right = min(duration, end + 1)
                audio.seek(round(left * sample_rate))
                block = audio.read(round((right-left) * sample_rate), dtype="float32", always_2d=True).mean(axis=1)
                if sample_rate != 16000:
                    from scipy.signal import resample_poly
                    from math import gcd
                    divisor = gcd(sample_rate, 16000)
                    block = resample_poly(block, 16000 // divisor, sample_rate // divisor).astype(np.float32)
                emit("status", message=f"正在转写 {label} · {timestamp(start)} / {timestamp(duration)}")
                if len(block) and np.max(np.abs(block)) > .0003:
                    segments, info = model.transcribe(
                        block, language=None if language == "auto" else language,
                        task="transcribe", beam_size=5, vad_filter=True,
                        vad_parameters={"min_silence_duration_ms": 500},
                        condition_on_previous_text=False, initial_prompt=vocabulary.strip() or None,
                        word_timestamps=True,
                    )
                    for segment in segments:
                        # Own words by midpoint, so context overlap is not exported twice.
                        words = [w for w in (segment.words or []) if start <= left+(w.start+w.end)/2 < end]
                        if not words:
                            continue
                        row = {"start": round(left + words[0].start, 3),
                               "end": round(left + words[-1].end, 3), "source": label,
                               "text": "".join(w.word for w in words).strip(),
                               "language": info.language,
                               "words": [{"start": round(left+w.start,3), "end": round(left+w.end,3),
                                          "text": w.word} for w in words]}
                        if row["text"]:
                            rows.append(row)
                            emit("segment", **row)
                            # A checkpoint is retained if the user cancels or GPU fails.
                            atomic_json(folder / "transcription_partial.json", {
                                "segments": rows, "complete": False, "started_at": run_started, "model": model_name,
                            })
                completed += end-start
                emit("progress", value=min(99, int(100 * completed / total)))
                start = end
    result = export_transcript(folder, rows, {"model": model_name, "device": device,
                               "compute_type": compute_type, "language": language,
                               "created_at": datetime.now().astimezone().isoformat(),
                               "complete": True, "speaker_diarization": False})
    emit("done", path=str(result), segments=len(rows))
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("session", type=Path)
    parser.add_argument("--model", choices=MODELS, default="large-v3")
    parser.add_argument("--language", choices=("auto", "zh", "en"), default="auto")
    parser.add_argument("--device", choices=("cuda", "cpu"), default="cuda")
    parser.add_argument("--vocabulary", default="")
    args = parser.parse_args()
    try:
        transcribe_session(args.session, args.model, args.language, args.device, args.vocabulary)
    except Exception as exc:
        emit("error", message=str(exc))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
