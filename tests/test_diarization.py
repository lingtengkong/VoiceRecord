import tempfile
import unittest
from pathlib import Path

from meeting_scribe.diarization import assign_speakers, preserve_manual, save_manual
from meeting_scribe.storage import atomic_json, read_json
from meeting_scribe.transcription import export_transcript


class DiarizationTests(unittest.TestCase):
    def test_words_split_short_speaker_changes_inside_long_asr_row(self):
        words=[{'start':56.24,'end':60.8,'text':' previous sentence'},
               {'start':64.8,'end':65.2,'text':' H I and J'},
               {'start':70.4,'end':72.4,'text':' Thomas got item K'},
               {'start':73,'end':74.1,'text':' yeah apologies'}]
        row={'start':56.24,'end':74.1,'source':'会议声音','text':''.join(w['text'] for w in words).strip(),'words':words}
        turns=[{'Start':56,'End':60.82,'Speaker':0}, {'Start':64.78,'End':68.36,'Speaker':1},
               {'Start':70.35,'End':72.53,'Speaker':1},{'Start':72.97,'End':75.18,'Speaker':2}]
        out=assign_speakers([row],turns)
        self.assertEqual([r['source'] for r in out],['参会者 1','参会者 2','参会者 3'])
        self.assertEqual(' '.join(r['text'] for r in out),row['text'])
        self.assertEqual(assign_speakers(out,turns),out)
        manual=dict(row,source='Alice',audio_source='会议声音',speaker_manual=True)
        self.assertEqual(assign_speakers([manual],turns),[manual])

    def test_word_overlap_and_ambiguous_gap_are_not_guessed(self):
        row={'start':1,'end':1.1,'source':'会议声音','text':'yeah',
             'words':[{'start':1,'end':1.1,'text':'yeah'}]}
        overlap=[{'Start':0,'End':2,'Speaker':s} for s in (0,1)]
        self.assertIn('多位参会者',assign_speakers([row],overlap)[0]['source'])
        gap=[{'Start':0,'End':.95,'Speaker':0},{'Start':1.15,'End':2,'Speaker':1}]
        self.assertIn('多位参会者',assign_speakers([row],gap)[0]['source'])
        self.assertIn('未确定',assign_speakers([row],[])[0]['source'])

    def test_short_yeah_and_competing_or_absent_speaker(self):
        row = {'start':39.78,'end':40.14,'source':'会议声音','text':'Yeah.'}
        turn = {'Start':40.06,'End':40.27,'Speaker':1}
        self.assertEqual(assign_speakers([row],[turn])[0]['source'], '参会者 2')
        competing = dict(turn, Speaker=2)
        self.assertIn('多位参会者',assign_speakers([row],[turn, competing])[0]['source'])
        self.assertIn('未确定',assign_speakers([row],[dict(turn, Start=40.15)])[0]['source'])
        self.assertIn('未确定',assign_speakers([row],[dict(turn, End=41)])[0]['source'])

    def test_manual_label_survives_rerun_and_exports(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            rows = [{'start':0,'end':1,'source':'会议声音','text':'hello'}]
            original = export_transcript(folder, rows, {})
            edited = save_manual(folder, original, 0, '参会者 2')
            previous = read_json(edited/'transcript.json')['segments']
            retained = assign_speakers(preserve_manual(rows, previous), [])
            self.assertEqual(retained[0]['source'],'参会者 2')
            self.assertTrue(retained[0]['speaker_manual'])
            self.assertEqual(read_json(folder/'latest_transcript.json')['path'],str(original))
            changed = [dict(rows[0], text='changed')]
            self.assertNotIn('speaker_manual',preserve_manual(changed, previous)[0])

    def test_uncertain_rows_and_microphone_preserve_words(self):
        rows = [{"start":s,"end":e,"source":source,"text":"原文"} for s,e,source in
                [(0,2,"我 / 麦克风"),(0,2,"会议声音"),(2,4,"会议声音"),(5,6,"会议声音")]]
        turns = [{"Start":0,"End":2,"Speaker":0},{"Start":2,"End":3,"Speaker":0},
                 {"Start":3,"End":4,"Speaker":1}]
        result = assign_speakers(rows, turns)
        self.assertEqual(result[0]["source"], "本机麦克风")
        self.assertEqual(result[1]["source"], "参会者 1")
        self.assertIn("多位参会者",result[2]["source"])
        self.assertIn("未确定",result[3]["source"])
        self.assertEqual([r["text"] for r in result],[r["text"] for r in rows])
        self.assertEqual(rows[0]["source"],"我 / 麦克风")
        self.assertEqual(assign_speakers(result,turns),result)

    def test_speaker_export_does_not_replace_original_or_live(self):
        with tempfile.TemporaryDirectory() as temp:
            folder=Path(temp)
            atomic_json(folder/'session.json',{'title':'test'})
            rows=[{'start':0,'end':1,'source':'会议声音','text':'测试'}]
            original=export_transcript(folder,rows,{})
            live=export_transcript(folder,rows,{'mode':'live','complete':True})
            result=export_transcript(folder,assign_speakers(rows,[]),{'mode':'live','diarized':True})
            self.assertEqual(read_json(folder/'latest_transcript.json')['path'],str(original))
            self.assertEqual(read_json(folder/'latest_live_transcript.json')['path'],str(live))
            self.assertEqual(read_json(folder/'latest_speaker_transcript.json')['path'],str(result))
            self.assertIn('待核对',(result/'transcript.txt').read_text(encoding='utf-8-sig'))
