"""Read-only comparison and local ASR spot check of a user-selected session."""
import argparse, json, re, sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from meeting_scribe.storage import read_json
from meeting_scribe.transcription import configure_runtime, model_path
parser=argparse.ArgumentParser()
parser.add_argument('session',type=Path)
args=parser.parse_args()
for name in ('latest_live_transcript.json','latest_transcript.json'):
    path=Path(read_json(args.session/name)['path'])
    rows=read_json(path/'transcript.json')['segments']
    print(name,json.dumps({'segments':len(rows),'words':sum(len(re.findall(r"\b[\w'-]+\b",r['text'])) for r in rows)},ensure_ascii=False))
configure_runtime()
import soundfile as sf
from scipy.signal import resample_poly
from math import gcd
from faster_whisper import WhisperModel
model=WhisperModel(str(model_path('turbo')),device='cuda',compute_type='int8_float16',local_files_only=True)
with sf.SoundFile(str(args.session/'system.wav')) as wav:
    rate=wav.samplerate
    wav.seek(63*rate)
    audio=wav.read(48*rate,dtype='float32',always_2d=True).mean(axis=1)
divisor=gcd(rate,16000)
audio=resample_poly(audio,16000//divisor,rate//divisor)
segments,_=model.transcribe(audio,language='en',beam_size=5,vad_filter=True,word_timestamps=True,condition_on_previous_text=False)
for s in segments:
    print(f'{63+s.start:.2f}-{63+s.end:.2f}: {s.text}')
