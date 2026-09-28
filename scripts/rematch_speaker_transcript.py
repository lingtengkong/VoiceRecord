"""Re-match saved predictions without re-running models or changing original audio."""
import argparse
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from meeting_scribe.storage import read_json, atomic_json
from meeting_scribe.diarization import assign_speakers
from meeting_scribe.transcription import export_transcript

parser = argparse.ArgumentParser()
parser.add_argument('session', type=Path)
args = parser.parse_args()
source = Path(read_json(args.session/'latest_speaker_transcript.json')['path'])
payload = read_json(source/'transcript.json')
turns = read_json(source/'speaker_turns.json')
assert 'segments' in turns, 'Saved speaker predictions missing'
rows = assign_speakers(payload['segments'], turns['segments'])
assert [(r['start'],r['end'],r['text']) for r in rows] == [(r['start'],r['end'],r['text']) for r in payload['segments']]
metadata = dict(payload['metadata'], source_transcript=str(source), speaker_matching_version='short-overlap-v2')
destination = export_transcript(args.session, rows, metadata)
atomic_json(destination/'speaker_turns.json', turns)
print('Saved new version:', destination)
for before, after in zip(payload['segments'], rows):
    if before['source'] != after['source']:
        print(f"{after['start']:.2f}-{after['end']:.2f}: {before['source']} -> {after['source']}")
