"""Reapply saved diarization to a new word-timed transcript; keep old versions."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from meeting_scribe.storage import read_json,atomic_json
from meeting_scribe.diarization import assign_speakers,preserve_manual,refine_boundaries
from meeting_scribe.transcription import export_transcript
session=Path(sys.argv[1])
source=Path(read_json(session/'latest_transcript.json')['path'])
previous=Path(read_json(session/'latest_speaker_transcript.json')['path'])
payload=read_json(source/'transcript.json')
old=read_json(previous/'transcript.json')['segments']
turns=read_json(previous/'speaker_turns.json')
rows=preserve_manual(payload['segments'],old)
rows=refine_boundaries(session,dict(payload,segments=rows),turns['segments'])
rows=preserve_manual(assign_speakers(rows,turns['segments']),old)
result=export_transcript(session,rows,dict(payload['metadata'],diarized=True,
    source_transcript=str(source),speaker_matching_version='word-turn-v3',speaker_labels_verified=False))
atomic_json(result/'speaker_turns.json',turns)
print(result)
for r in rows:
    if r['start']<78 and r['end']>60:print(r['start'],r['end'],r['source'],r['text'])
