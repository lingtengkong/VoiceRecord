"""Offline OPUS-MT English-to-Chinese translation using CTranslate2 CPU INT8."""
from __future__ import annotations
import json
import re
from pathlib import Path

MAX_TEXT = 6000
MODEL_DIR = Path(__file__).resolve().parent.parent / 'models' / 'opus-mt-en-zh'
MODEL_REPO = 'gaudi/opus-mt-en-zh-ctranslate2'
MODEL_REVISION = 'dcd22168f08b99dd34c62bc2195e31dc2f04e90b'
FILES = ['model.bin', 'config.json', 'shared_vocabulary.json', 'source.spm', 'target.spm', 'README.md']

class Translator:
    def __init__(self, folder=MODEL_DIR):
        import ctranslate2
        import sentencepiece
        if not all((folder / name).exists() for name in FILES[:5]):
            raise RuntimeError('离线翻译模型尚未安装，请运行 download_translation.py。')
        self.source = sentencepiece.SentencePieceProcessor(model_file=str(folder / 'source.spm'))
        self.target = sentencepiece.SentencePieceProcessor(model_file=str(folder / 'target.spm'))
        self.engine = ctranslate2.Translator(str(folder), device='cpu', compute_type='int8',
                                            inter_threads=1, intra_threads=4)

    def translate(self, text):
        if not text.strip():
            raise ValueError('请先选中需要翻译的文字。')
        if len(text) > MAX_TEXT:
            raise ValueError('单次最多翻译6000个字符，请缩小选择范围。')
        lines = text.split('\n')
        batches, destinations = [], []
        for index, line in enumerate(lines):
            if not re.search('[A-Za-z]', line):
                continue
            sentences = re.split(r'(?<=[.!?])\s+', line.strip())
            for sentence in sentences:
                tokens = self.source.encode(sentence, out_type=str)
                for offset in range(0, len(tokens), 240):
                    batches.append(['>>cmn_Hans<<'] + tokens[offset:offset+240] + ['</s>'])
                    destinations.append(index)
        if not batches:
            return text
        results = self.engine.translate_batch(batches, beam_size=1, max_batch_size=16,
                                               max_decoding_length=512, return_end_token=True)
        translated = {}
        for index, result in zip(destinations, results):
            tokens = result.hypotheses[0]
            if not tokens or tokens[-1] != '</s>':
                raise RuntimeError('译文未完整生成，请缩小选择范围后重试。')
            clean = [t for t in tokens[:-1] if not t.startswith('>>')]
            answer = self.target.decode(clean).strip()
            if not answer:
                raise RuntimeError('模型未返回译文，请重试。')
            translated.setdefault(index, []).append(answer)
        for index, parts in translated.items():
            lines[index] = ' '.join(parts)
        return '\n'.join(lines)

def main():
    import sys
    translator = None
    # A persistent private subprocess keeps weights loaded between selections.
    for line in sys.stdin:
        request = {}
        try:
            request = json.loads(line)
            if translator is None:
                translator = Translator()
            result = {'id': request['id'], 'text': translator.translate(request['text'])}
        except Exception as exc:
            result = {'id': request.get('id'), 'error': str(exc)}
        print(json.dumps(result, ensure_ascii=False), flush=True)

if __name__ == '__main__':
    main()
