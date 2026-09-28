"""Optional local Nemotron worker, run in its isolated Python environment."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from .storage import data_root, read_json, atomic_json
from .transcription import emit, export_transcript


def row_key(row):
    return (row['start'], row['end'], row.get('audio_source', row['source']), row['text'])


def preserve_manual(rows, previous):
    confirmed = {row_key(r): r for r in previous if r.get('speaker_manual')}
    return [dict(r, source=confirmed[row_key(r)]['source'], speaker_manual=True,
                 speaker_ids=confirmed[row_key(r)].get('speaker_ids', []))
            if row_key(r) in confirmed else dict(r) for r in rows]


def save_manual(session, transcript, index, label):
    payload = read_json(transcript / 'transcript.json')
    rows = payload['segments']
    row = rows[index]
    row['audio_source'] = row.get('audio_source', row['source'])
    row.update(source=label, speaker_manual=True, speaker_ids=[])
    metadata = dict(payload.get('metadata', {}), diarized=True, speaker_labels_verified=False)
    destination = export_transcript(session, rows, metadata)
    turns = read_json(transcript / 'speaker_turns.json')
    if turns:
        atomic_json(destination / 'speaker_turns.json', turns)
    return destination


def runtime_path():
    return data_root() / "tools" / "nemotron-python" / "Scripts" / "python.exe"


def model_path():
    return data_root() / "models" / "nemotron-3-diarization"


def assign_speakers(rows, turns):
    """Use available word times; retain conservative segment matching for older drafts."""
    result = []
    ordered = sorted(turns, key=lambda t: t["Start"])
    index, active = 0, []
    for original in sorted(rows, key=lambda r: (r["start"], r["end"])):
        row = dict(original)
        source = row.get("audio_source", row["source"])
        row["audio_source"] = source
        if row.get('speaker_manual'):
            result.append(row)
            continue
        if source in ("我 / 麦克风", "本机麦克风"):
            row["source"] = "本机麦克风"
        elif row.get('words') and ''.join(w['text'] for w in row['words']).strip() == row['text'].strip():
            result.extend(split_words(row, ordered))
            continue
        else:
            start, end = row["start"], row["end"]
            active = [t for t in active if t["End"] > start]
            while index < len(ordered) and ordered[index]["Start"] < end:
                active.append(ordered[index])
                index += 1
            scores = {}
            for t in active:
                duration = max(0, min(end, t["End"]) - max(start, t["Start"]))
                scores[int(t["Speaker"])] = scores.get(int(t["Speaker"]), 0) + duration
            span = max(.01, end-start)
            candidates = sorted(s for s, seconds in scores.items() if seconds >= max(.1, span*.2))
            if span <= .8:
                # Supplement normal matching with a short-turn overlap rule.
                short = {int(t['Speaker']) for t in active
                         if (overlap := max(0, min(end, t['End'])-max(start, t['Start']))) + 1e-9 >= .05
                         and overlap/span + 1e-9 >= .2
                         and overlap/max(.01, t['End']-t['Start']) + 1e-9 >= .3}
                candidates = sorted(set(candidates) | short)
            row["speaker_ids"] = candidates
            if len(candidates) == 1:
                row["source"] = f"参会者 {candidates[0]+1}"
            elif candidates:
                row["source"] = "多位参会者（" + "、".join(str(s+1) for s in candidates) + "，待核对）"
            else:
                row["source"] = "会议声音（说话者未确定）"
        result.append(row)
    return result


def split_words(row, turns):
    """Attribute timestamped units before grouping, preserving every character.

    Small alignment gaps use the unique nearest speaker within 200ms. Equally
    close competing speakers and overlaps stay uncertain; no whole-row majority.
    """
    groups = []
    for word in row['words']:
        start, end = word['start'], word['end']
        scores = {}
        for turn in turns:
            overlap = max(0,min(end,turn['End'])-max(start,turn['Start']))
            if overlap:
                s=int(turn['Speaker'])
                scores[s]=scores.get(s,0)+overlap
        if scores:
            best=max(scores.values())
            ids=sorted(s for s,v in scores.items() if v>=best*.5)
        else:
            distances={}
            for turn in turns:
                gap=max(0,turn['Start']-end,start-turn['End'])
                s=int(turn['Speaker'])
                distances[s]=min(distances.get(s,float('inf')),gap)
            closest=min(distances.values(),default=float('inf'))
            ids=sorted(s for s,d in distances.items() if d<=closest+.04) if closest<=.2 else []
        source=(f'参会者 {ids[0]+1}' if len(ids)==1 else
                '多位参会者（'+'、'.join(str(s+1) for s in ids)+'，待核对）' if ids else '会议声音（说话者未确定）')
        if groups and groups[-1]['speaker_ids']==ids:
            groups[-1]['words'].append(dict(word))
            groups[-1]['text']+=word['text']
            groups[-1]['end']=max(groups[-1]['end'],end)
        else:
            groups.append(dict(row,start=start,end=end,text=word['text'],words=[dict(word)],
                               source=source,speaker_ids=ids,speaker_matching_version='word-turn-v3'))
    for group in groups:
        group['text']=group['text'].strip()
    return groups


def infer_turns(audio_path, device):
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
    import math
    import numpy as np
    import soundfile as sf
    import torch
    from scipy.signal import resample_poly
    from transformers import AutoProcessor, AutoModelForAudioFrameClassification

    torch.set_num_threads(4)
    emit("status", message=f"正在加载 Nemotron · {device.upper()}…")
    processor = AutoProcessor.from_pretrained(str(model_path()), local_files_only=True)
    model = AutoModelForAudioFrameClassification.from_pretrained(
        str(model_path()), local_files_only=True).to(device).eval()
    processor.set_streaming_mode("low_latency")
    with sf.SoundFile(audio_path) as audio:
        rate = audio.samplerate
        length = round(len(audio)*16000/rate)
        if length < 800:
            raise ValueError("会议声音不足 0.05 秒，无法区分说话者。")
        def read(start, count):
            first = round(start*rate/16000)
            audio.seek(min(first, len(audio)))
            waveform = audio.read(max(0, math.ceil(count*rate/16000)), dtype="float32", always_2d=True).mean(axis=1)
            if rate != 16000:
                divisor = math.gcd(rate, 16000)
                waveform = resample_poly(waveform, 16000//divisor, rate//divisor)
            return np.asarray(waveform[:count], dtype="float32")

        first_size = processor.num_samples_first_audio_chunk
        def features():
            if length <= first_size:
                yield processor(read(0, length), sampling_rate=16000), length
                return
            yield processor(read(0, first_size), sampling_rate=16000,
                            is_streaming=True, is_first_audio_chunk=True), first_size
            frame = processor.num_mel_frames_per_step
            start = processor.audio_chunk_start(frame)
            size = processor.num_samples_per_audio_chunk
            while start+size <= length:
                yield processor(read(start, size), sampling_rate=16000,
                                is_streaming=True, is_first_audio_chunk=False), start+size
                frame += processor.num_mel_frames_per_step
                start = processor.audio_chunk_start(frame)
            if start < length:
                yield processor(read(start, length-start), sampling_rate=16000, is_streaming=True,
                                is_first_audio_chunk=False, is_last_audio_chunk=True), length

        cache, pieces, last_progress = None, [], -1
        with torch.inference_mode():
            for inputs, position in features():
                inputs = inputs.to(model.device, dtype=model.dtype)
                output = model(**inputs, speaker_cache=cache)
                pieces.append(output.logits.cpu())
                cache = output.speaker_cache
                progress = min(95, int(position/length*95))
                if progress != last_progress:
                    emit("progress", value=progress)
                    last_progress = progress
        turns = processor.extract_speaker_dict(torch.cat(pieces, dim=1))[0]
        for turn in turns:
            turn["End"] = min(turn["End"], length/16000)
        return [t for t in turns if t["End"] > t["Start"]]


def diarize(session, transcript, device):
    payload = read_json(transcript / "transcript.json")
    rows = payload.get("segments", [])
    latest = read_json(session / 'latest_speaker_transcript.json').get('path')
    if latest:
        rows = preserve_manual(rows, read_json(Path(latest) / 'transcript.json').get('segments', []))
    if not rows:
        raise ValueError("请先完成转写，再区分说话者。")
    from .sources import speaker_audio_key
    audio = session / f'{speaker_audio_key(session)}.wav'
    if not audio.is_file():
        raise ValueError(f"找不到本次会议的 {audio.name} 原始音轨。")
    turns = infer_turns(audio, device)
    if payload.get('metadata',{}).get('model') in ('large-v3','turbo'):
        rows = refine_boundaries(session, dict(payload,segments=rows,
            metadata=dict(payload.get('metadata',{}),device=device)), turns)
    metadata = dict(payload.get("metadata", {}))
    metadata.update(diarized=True, diarization_model="nvidia/Nemotron-3-Diarization",
                    source_transcript=str(transcript), speaker_labels_verified=False)
    labeled = assign_speakers(rows, turns)
    if latest:
        labeled = preserve_manual(labeled, read_json(Path(latest) / 'transcript.json').get('segments', []))
    destination = export_transcript(session, labeled, metadata)
    atomic_json(destination / "speaker_turns.json", {"segments": turns, "device": device})
    return destination


def refine_boundaries(session, payload, turns):
    import subprocess
    import tempfile
    project=Path(__file__).resolve().parent.parent
    runtime=read_json(project/'local_paths.json').get('python')
    if not runtime:
        raise RuntimeError('缺少原转写环境，无法核对换人边界。')
    emit('status',message='正在按换人区间复核跨说话者的文字片段…')
    with tempfile.TemporaryDirectory(prefix='speaker-refine-') as temporary:
        source=Path(temporary)/'input.json'
        output=Path(temporary)/'output.json'
        atomic_json(source,{'payload':payload,'turns':turns})
        env=dict(os.environ,PYTHONIOENCODING='utf-8')
        completed=subprocess.run([runtime,'-m','meeting_scribe.refine_speakers',str(session),str(source),str(output),'--parent-pid',str(os.getpid())],
                                 cwd=project,env=env,capture_output=True,text=True,encoding='utf-8')
        if completed.returncode:
            raise RuntimeError('换人边界复核失败：'+completed.stderr[-1000:])
        return read_json(output)['segments']


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("session", type=Path)
    parser.add_argument("transcript", type=Path)
    parser.add_argument("--device", choices=["cpu", "cuda"], default="cuda")
    args = parser.parse_args()
    try:
        result = diarize(args.session, args.transcript, args.device)
        emit("done", path=str(result))
    except Exception as exc:
        emit("error", message=f"说话者区分未完成：{exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()

