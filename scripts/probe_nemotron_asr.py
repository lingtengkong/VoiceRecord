"""Synthetic-only cache-aware ASR probe."""
import os, sys, time, json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
os.environ['HF_HUB_OFFLINE']='1'
import torch
import soundfile as sf
import numpy as np
from transformers import AutoProcessor, AutoModelForRNNT
from meeting_scribe.nemotron_stream import ASRStream, asr_path
from meeting_scribe.storage import data_root
torch.set_num_threads(4)
p=AutoProcessor.from_pretrained(str(asr_path()),local_files_only=True)
p.set_num_lookahead_tokens(6)
m=AutoModelForRNNT.from_pretrained(str(asr_path()),local_files_only=True,torch_dtype=torch.float16).to('cuda').eval()
audio,rate=sf.read(data_root()/'verification/nemotron3/three_speakers.wav',dtype='float32')
state=ASRStream(m,p)
tokens=[]
started=time.monotonic()
while True:
    start,size=state.window()
    if start>=len(audio):break
    block=audio[start:start+size]
    block=np.pad(block,(0,max(0,size-len(block))))
    result=state.step(block)
    tokens.extend(t for t,_ in result)
    if result:print(round(start/16000,2),p.decode([t for t,_ in result],skip_special_tokens=True),flush=True)
print(json.dumps({'seconds':time.monotonic()-started,'gpu_mib':torch.cuda.max_memory_allocated()/1024**2,'text':p.decode(tokens,skip_special_tokens=True)},ensure_ascii=False))
# Check our persistent decoder against the library's public streaming generator.
short=audio[:16000*5]
custom=ASRStream(m,p)
expected=[]
chunks=[]
prompt=None
while True:
    start,size=custom.window()
    if start>=len(short):break
    block=np.pad(short[start:start+size],(0,max(0,size-len(short[start:start+size]))))
    first=custom.mel==0
    inputs=p(block,sampling_rate=16000,language='auto',is_streaming=True,is_first_audio_chunk=first)
    n=p.num_mel_frames_first_audio_chunk if first else p.num_mel_frames_per_audio_chunk
    chunks.append(inputs.input_features[:,:n])
    prompt=inputs.prompt_ids.to(m.device)
    expected.extend(t for t,_ in custom.step(block))
def features():
    yield from chunks
with torch.inference_mode():
    reference=m.generate(input_features=features(),prompt_ids=prompt,num_lookahead_tokens=6,max_new_tokens=500)
actual=[t for t in reference.sequences[0].tolist() if t!=m.config.blank_token_id]
assert actual==expected, (actual,expected)
print('PASS: incremental decoder tokens match official streaming generate exactly.')
