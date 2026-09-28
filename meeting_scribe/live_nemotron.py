"""Two-source, native streaming ASR with live timestamp-based speaker labels.

Reads durable capture files only. Pauses reset ASR acoustic/language caches;
diarization speaker cache survives pauses. No audio is sent over the network.
"""
import argparse
import os
import time
import re
from pathlib import Path
import numpy as np
from .storage import atomic_json, read_json
from .transcription import emit, export_transcript
from .live import read_audio
from .sources import audio_sources, speaker_audio_key
from .diarization import model_path, assign_speakers
from .nemotron_stream import ASRStream, DiarStream, asr_path


def timed_units(processor, tokens, times, until):
    """Map decoded characters to RNNT emission frames, then group whole words.

    Never split English subwords into different speakers. Emission times are
    approximate, not forced alignment. If decoding rewrites a prefix, fall back.
    """
    text, positions = '', []
    for i, when in enumerate(times):
        decoded=processor.decode(tokens[:i+1],skip_special_tokens=True)
        if not decoded.startswith(text):
            return []
        positions.extend([min(until,when)]*(len(decoded)-len(text)))
        text=decoded
    return [{'text':m.group(), 'start':round(positions[m.start()],3),
             'end':round(min(until,positions[m.end()-1]+.08),3)}
            for m in re.finditer(r'\s*(?:[\u3400-\u9fff]|[^\s\u3400-\u9fff]+)',text)]


class TextTrack:
    def __init__(self, model, processor, language, source, offset=0):
        self.stream = ASRStream(model, processor, language, offset)
        self.source, self.offset = source, offset
        self.tokens, self.times = [], []
        self.rows = []
        self.pending = None
        self.seen = offset
        self.closed = False

    def accept(self, tokens, until, final=False):
        for token, when in tokens:
            self.tokens.append(token)
            self.times.append(when)
        text = self.stream.processor.decode(self.tokens, skip_special_tokens=True).strip() if self.tokens else ''
        if text:
            self.pending = {'start':round(min(until, self.times[0]),3),
                            'end':round(min(until, max(self.times[-1]+.08, self.times[0]+.01)),3),
                            'source':self.source, 'audio_source':self.source, 'text':text,
                            'words':timed_units(self.stream.processor,self.tokens,self.times,until)}
            ended = text.endswith(('.', '?', '!', '。', '？', '！'))
            if final or until-self.times[-1] > .8 or (ended and until-self.times[-1] > .2) or self.times[-1]-self.times[0] > 10:
                self.rows.append(self.pending)
                self.pending = None
                self.tokens, self.times = [], []
        elif final:
            self.tokens, self.times = [], []


class Engine:
    def __init__(self, folder, language):
        os.environ['HF_HUB_OFFLINE']='1'
        os.environ['HF_HUB_DISABLE_TELEMETRY']='1'
        import torch
        from transformers import AutoProcessor, AutoModelForRNNT, AutoModelForAudioFrameClassification
        torch.set_num_threads(4)
        self.folder = folder
        self.sources = audio_sources(folder)
        self.speaker_key = speaker_audio_key(folder)
        self.started = time.monotonic()
        emit('live_status', message='正在加载 Nemotron 3.5 ASR 和说话者模型；原声独立保存…')
        self.processor = AutoProcessor.from_pretrained(str(asr_path()), local_files_only=True)
        self.processor.set_num_lookahead_tokens(6)
        self.model = AutoModelForRNNT.from_pretrained(str(asr_path()), local_files_only=True,
                                                    dtype=torch.float16).to('cuda').eval()
        self.dp = AutoProcessor.from_pretrained(str(model_path()),local_files_only=True)
        self.dp.set_streaming_mode('low_latency')
        self.dm = AutoModelForAudioFrameClassification.from_pretrained(str(model_path()),local_files_only=True).to('cuda').eval()
        self.diar = DiarStream(self.dm, self.dp)
        self.language = {'en':'en-US','zh':'zh-CN'}.get(language,'auto')
        self.tracks = {k:TextTrack(self.model,self.processor,self.language,v) for k,v in self.sources.items()}
        self.history = []
        self.frozen = []
        self.last_emit = 0
        self.diar_seen = 0
        self.diar_closed = False
        self.steps = []
        self.metadata = {'mode':'live','model':'nemotron-3.5-asr','device':'cuda',
                         'speaker_diarization':True, 'speaker_labels_verified':False,
                         'timestamp_mode':'rnnt-emission-approximate',
                         'integration':'parallel-asr-and-diarization-time-match', 'complete':False}
        emit('live_ready',message='Nemotron 3.5 已就绪 · 实时说话者标签待核对')

    def block(self, stream, key, capture, boundary, terminal):
        spec = capture['tracks'][key]
        available = min(capture['position'],spec['frames']/spec['rate'],boundary)
        start, size = stream.window()
        left = stream.offset+start/16000
        right = left+size/16000
        if left >= available:
            return None, available, terminal
        if right > available and not terminal:
            return None, available, False
        audio = read_audio(self.folder/f'{key}.wav',spec['rate'],left,min(right,available))
        audio = np.pad(audio[:size],(0,max(0,size-len(audio))))
        return audio, min(right,available), terminal and right >= available

    def step(self, capture):
        begun = time.monotonic()
        base = min(t.offset for t in self.tracks.values())
        boundaries = [b for b in capture.get('pause_boundaries',[]) if b > base+.001]
        boundary = min(boundaries) if boundaries else float('inf')
        terminal = capture['state']=='finished' or boundary <= capture['position']
        if not self.diar_closed:
            block, end, done = self.block(self.diar,self.speaker_key,capture,boundary,terminal)
            if block is not None:
                self.diar.step(block)
                self.diar_seen = end
            self.diar_closed = done
        for key, track in self.tracks.items():
            if track.closed:
                continue
            block, end, done = self.block(track.stream,key,capture,boundary,terminal)
            if block is not None:
                tokens = track.stream.step(block)
                track.seen = end
                track.accept(tokens,end,final=done)
            elif done:
                track.accept([],end,final=True)
            track.closed = done
        self.steps.append(time.monotonic()-begun)
        if len(self.steps)>1000:self.steps=self.steps[-1000:]
        complete = all(t.closed for t in self.tracks.values()) and self.diar_closed
        self.publish(capture,force=complete)
        if complete and boundary != float('inf'):
            self.history.extend(r for t in self.tracks.values() for r in t.rows)
            # Freeze completed-region labels before resetting the feature frontend.
            self.history = assign_speakers(self.history, self.diar.turns)
            self.frozen.extend(self.history)
            self.history = []
            self.tracks = {k:TextTrack(self.model,self.processor,self.language,v,boundary) for k,v in self.sources.items()}
            self.diar = DiarStream(self.dm,self.dp,boundary,self.diar.cache)
            self.diar_seen, self.diar_closed = boundary, False
            return False
        return complete

    def rows(self):
        watermark = min(self.diar_seen, *(t.seen for t in self.tracks.values()))-5
        pending, candidates = [], []
        for track in self.tracks.values():
            old = [r for r in track.rows if r['end'] < watermark]
            self.frozen.extend(assign_speakers(old,self.diar.turns))
            track.rows = [r for r in track.rows if r['end'] >= watermark]
            candidates.extend(track.rows)
            if track.pending:pending.append(track.pending)
        return sorted(self.frozen+assign_speakers(candidates,self.diar.turns),key=lambda r:(r['start'],r['end'])), assign_speakers(pending,self.diar.turns)

    def publish(self,capture,force=False):
        if not force and time.monotonic()-self.last_emit < .5:return
        self.last_emit = time.monotonic()
        rows, pending = self.rows()
        atomic_json(self.folder/'live_draft.json',{'metadata':self.metadata,'segments':rows,'partial':{'all':pending},'skipped_ranges':[]})
        lag = max(0,capture['position']-min(self.diar_seen,*(t.seen for t in self.tracks.values())))
        emit('live_snapshot',rows=rows,partial=pending)
        emit('live_health',backlog_seconds=round(lag,1),state=capture['state'],committed=len(rows),gaps=0)

    def finish(self,complete,reason=''):
        import torch
        rows,_ = self.rows()
        self.metadata.update(complete=complete,reason=reason)
        result = export_transcript(self.folder,rows,self.metadata)
        atomic_json(result/'stream_metrics.json',{'elapsed_seconds':time.monotonic()-self.started,
            'peak_cuda_allocated_mib':torch.cuda.max_memory_allocated()/1024**2,
            'step_mean_seconds':sum(self.steps)/max(1,len(self.steps)),
            'step_max_seconds':max(self.steps,default=0), 'complete':complete})
        emit('live_done',path=str(result),segments=len(rows),complete=complete)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('session',type=Path)
    parser.add_argument('--language',default='auto',choices=['auto','zh','en'])
    parser.add_argument('--vocabulary',default='')
    parser.add_argument('--replay',action='store_true',help='Mark output as rebuilt from saved PCM, not a new live capture')
    args=parser.parse_args()
    try:
        engine=Engine(args.session,args.language)
        engine.metadata['live_replay']=args.replay
        if args.vocabulary:
            emit('live_status',message='Nemotron 试用模式暂不使用术语提示；实时标签及时间戳需核对。')
        while True:
            if (args.session/'live_cancel.flag').exists():
                engine.finish(False,'用户停止实时预览；原始录音保留')
                return
            capture=read_json(args.session/'live_capture.json')
            if not capture:
                time.sleep(.1)
                continue
            if capture['state']!='finished' and time.time()-capture.get('updated_at',0)>20:
                engine.finish(False,'录音心跳中断，请会后重转')
                return
            if engine.step(capture):
                engine.finish(True)
                return
            lag=capture['position']-min(t.seen for t in engine.tracks.values())
            if lag>30 and capture['state']!='finished':
                engine.finish(False,'实时识别积压超过30秒，已停止预览；原声保留，请会后转写')
                return
            time.sleep(.02)
    except Exception as exc:
        emit('live_error',message=str(exc))
        raise SystemExit(1)


if __name__=='__main__':main()
