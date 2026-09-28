import unittest
from types import SimpleNamespace
from meeting_scribe.live_nemotron import TextTrack, Engine, timed_units
from unittest.mock import patch
import numpy as np


class NemotronLiveTests(unittest.TestCase):
    def test_token_fragments_keep_whole_words_and_chinese(self):
        processor=SimpleNamespace(decode=lambda tokens,**kw: ''.join(tokens))
        tokens=[' Thom','as',' got',' item',' K',' yeah','你','好']
        units=timed_units(processor,tokens,list(range(len(tokens))),9)
        self.assertEqual([w['text'] for w in units],[' Thomas',' got',' item',' K',' yeah','你','好'])
        self.assertEqual(''.join(w['text'] for w in units),''.join(tokens))

    def track(self):
        track=TextTrack.__new__(TextTrack)
        track.tokens,track.times,track.rows,track.pending=[],[],[],None
        track.source='会议声音'
        track.stream=SimpleNamespace(processor=SimpleNamespace(decode=lambda tokens,**kw: ''.join(tokens)))
        return track

    def test_partial_then_silence_commit_and_tail_clamp(self):
        track=self.track()
        track.accept([('Hello',1)],1.2)
        self.assertEqual(track.rows,[])
        self.assertEqual(track.pending['text'],'Hello')
        track.accept([],2)
        self.assertEqual(track.rows[0]['text'],'Hello')
        self.assertIsNone(track.pending)
        track.accept([('end',2.9)],3,final=True)
        self.assertLessEqual(track.rows[-1]['end'],3)

    def test_audio_waits_for_complete_chunk_and_pads_final_tail(self):
        engine=Engine.__new__(Engine)
        from pathlib import Path
        engine.folder=Path('.')
        stream=SimpleNamespace(offset=0,window=lambda:(0,16000))
        capture={'position':.25,'tracks':{'system':{'rate':16000,'frames':4000}}}
        self.assertIsNone(engine.block(stream,'system',capture,float('inf'),False)[0])
        with patch('meeting_scribe.live_nemotron.read_audio',return_value=np.ones(4000,dtype=np.float32)):
            block,end,done=engine.block(stream,'system',capture,float('inf'),True)
        self.assertEqual(len(block),16000)
        self.assertEqual(np.count_nonzero(block),4000)
        self.assertTrue(done)
        self.assertEqual(end,.25)

    def test_five_second_revision_window_freezes_old_labels(self):
        engine=Engine.__new__(Engine)
        old={'start':0,'end':1,'source':'会议声音','text':'old'}
        new={'start':8,'end':9,'source':'会议声音','text':'new'}
        track=SimpleNamespace(seen=10,rows=[old,new],pending=None)
        engine.tracks={'system':track}
        engine.frozen=[]
        engine.diar_seen=10
        engine.diar=SimpleNamespace(turns=[{'Start':0,'End':10,'Speaker':1}])
        rows,_=engine.rows()
        self.assertEqual(rows[0]['source'],'参会者 2')
        engine.diar.turns=[{'Start':0,'End':10,'Speaker':2}]
        rows,_=engine.rows()
        self.assertEqual(rows[0]['source'],'参会者 2')
        self.assertEqual(rows[1]['source'],'参会者 3')
