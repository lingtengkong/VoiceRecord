import unittest
from meeting_scribe.refine_speakers import align_words
from meeting_scribe.diarization import assign_speakers


class BoundaryAlignmentTests(unittest.TestCase):
    def test_realign_short_response_without_replacing_original_words(self):
        words=[dict(start=71.94,end=72.14,text=' k'),
               dict(start=72.14,end=73.04,text=' yeah'),
               dict(start=73.04,end=73.52,text=' apologies')]
        recognized=[dict(start=71.94,end=72.14,text=' K.'),
                    dict(start=72.62,end=73.14,text=' Yeah,'),
                    dict(start=73.14,end=73.52,text=' apologies')]
        aligned=align_words(words,recognized)
        row=dict(start=71.94,end=73.52,source='会议声音',text='k yeah apologies',words=aligned)
        turns=[dict(Start=71.59,End=72.53,Speaker=1),dict(Start=72.97,End=75.18,Speaker=2)]
        result=assign_speakers([row],turns)
        self.assertEqual([(r['source'],r['text']) for r in result], [('参会者 2','k'),('参会者 3','yeah apologies')])
        self.assertEqual([w['text'] for w in aligned],[w['text'] for w in words])
        self.assertEqual(words[1]['start'],72.14)

    def test_omitted_or_distant_words_are_retained(self):
        words=[dict(start=1,end=2,text=' hello'),dict(start=3,end=4,text=' world')]
        self.assertEqual(align_words(words,[]),words)
        distant=[dict(start=10,end=11,text=' hello')]
        self.assertEqual(align_words(words,distant),words)
        self.assertEqual(align_words(words,[dict(start=1.1,end=2,text=' Hello!')])[1],words[1])

    def test_repeated_phrases_align_in_sequence(self):
        words=[dict(start=i,end=i+.2,text=text) for i,text in enumerate([' yes',' no',' yes'])]
        recognized=[dict(w,start=w['start']+.1) for w in words]
        self.assertEqual([w['start'] for w in align_words(words,recognized)],[.1,1.1,2.1])
