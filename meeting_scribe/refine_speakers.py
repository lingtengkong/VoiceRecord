"""Locally re-align words near speaker changes, preserving the original text."""
import argparse
import re
from difflib import SequenceMatcher
from pathlib import Path
from .storage import read_json, atomic_json
from .transcription import configure_runtime, model_path


def align_words(original, recognized):
    normalize = lambda text: re.sub(r'[^\w]', '', text.casefold())
    before = [normalize(w['text']) for w in original]
    after = [normalize(w['text']) for w in recognized]
    result = [dict(w) for w in original]
    for match in SequenceMatcher(None, before, after, autojunk=False).get_matching_blocks():
        for i, j in zip(range(match.a, match.a+match.size), range(match.b, match.b+match.size)):
            candidate = recognized[j]
            if before[i] and abs(candidate['start']-original[i]['start']) <= 5:
                result[i].update(start=candidate['start'], end=candidate['end'])
    # Reject isolated matches that would put words out of their spoken order.
    for i in range(len(result)):
        if (i and result[i]['start'] < result[i-1]['start']) or (i+1<len(result) and result[i]['start'] > result[i+1]['start']):
            result[i] = dict(original[i])
    return result


def refine(session, payload, turns):
    configure_runtime()
    import soundfile as sf
    from scipy.signal import resample_poly
    from math import gcd
    from faster_whisper import WhisperModel
    model = None
    result = []
    for row in payload['segments']:
        if row.get('speaker_manual') or row.get('audio_source',row['source']) in ('我 / 麦克风','本机麦克风') or not row.get('words'):
            result.append(row)
            continue
        relevant = [t for t in turns if t['End']>row['start'] and t['Start']<row['end']]
        if len({t['Speaker'] for t in relevant})<2:
            result.append(row)
            continue
        if model is None:
            name=payload.get('metadata',{}).get('model','large-v3')
            if name not in ('large-v3','turbo'): name='large-v3'
            device=payload.get('metadata',{}).get('device','cpu')
            if device not in ('cpu','cuda'): device='cpu'
            model=WhisperModel(str(model_path(name)),device=device,
                compute_type='int8_float16' if device=='cuda' else 'int8',local_files_only=True)
        from .sources import speaker_audio_key
        with sf.SoundFile(str(session / f'{speaker_audio_key(session)}.wav')) as audio:
            rate=audio.samplerate
            left=max(0,row['start']-3)
            right=min(len(audio)/rate,row['end']+3)
            audio.seek(round(left*rate))
            block=audio.read(round((right-left)*rate),dtype='float32',always_2d=True).mean(axis=1)
            divisor=gcd(rate,16000)
            block=resample_poly(block,16000//divisor,rate//divisor)
            segments,_=model.transcribe(block,language=row.get('language') or None,
                beam_size=5,vad_filter=False,word_timestamps=True,condition_on_previous_text=False)
            recognized=[{'start':round(left+w.start,3),'end':round(left+w.end,3),'text':w.word}
                        for s in segments for w in (s.words or [])]
        words=align_words(row['words'],recognized)
        result.append(dict(row,words=words,start=min(w['start'] for w in words),end=max(w['end'] for w in words),speaker_boundary_realigned=True))
    return result


def watch_parent(pid):
    """Stop the GPU worker when the cancellable parent worker exits (Windows)."""
    import os
    if os.name != 'nt' or not pid: return
    import ctypes
    import threading
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
    kernel.OpenProcess.restype=wintypes.HANDLE
    kernel.WaitForSingleObject.argtypes=[wintypes.HANDLE,wintypes.DWORD]
    kernel.CloseHandle.argtypes=[wintypes.HANDLE]
    handle=kernel.OpenProcess(0x00100000,False,pid)
    if not handle: raise RuntimeError('Parent worker is no longer available')
    def wait():
        if kernel.WaitForSingleObject(handle,0xffffffff)==0:
            os._exit(1)
        kernel.CloseHandle(handle)
    threading.Thread(target=wait,daemon=True).start()


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('session',type=Path)
    parser.add_argument('input',type=Path)
    parser.add_argument('output',type=Path)
    parser.add_argument('--parent-pid',type=int)
    args=parser.parse_args()
    watch_parent(args.parent_pid)
    data=read_json(args.input)
    atomic_json(args.output,{'segments':refine(args.session,data['payload'],data['turns'])})


if __name__=='__main__': main()
