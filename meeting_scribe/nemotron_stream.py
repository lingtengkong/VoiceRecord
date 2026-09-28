"""Native cache-aware Nemotron ASR and diarization states (isolated runtime).

The ASR greedy decoder follows the public Transformers RNNT decoder/cache API.
Each source owns its caches; model weights are shared and invoked sequentially.
This is timestamp attribution, not NeMo's speaker-conditioned multi-ASR pipeline.
"""
from pathlib import Path
import numpy as np
from .storage import data_root


def asr_path():
    return data_root() / 'models/nemotron-3.5-asr-streaming-0.6b'


class ASRStream:
    def __init__(self, model, processor, language='auto', offset=0):
        import torch
        from transformers.models.nemotron3_5_asr.generation_nemotron3_5_asr import Nemotron3_5AsrRNNTDecoderCache
        self.model, self.processor, self.language = model, processor, language
        self.offset = offset
        self.mel = 0
        self.frames = 0
        self.encoder_cache = None
        self.padding_cache = None
        self.decoder_cache = Nemotron3_5AsrRNNTDecoderCache(model.config)
        self.token = torch.tensor([[model.config.blank_token_id]], device=model.device)

    def window(self):
        p = self.processor
        if self.mel == 0:
            return 0, p.num_samples_first_audio_chunk
        return self.mel*p.feature_extractor.hop_length-p.feature_extractor.n_fft//2, p.num_samples_per_audio_chunk

    def step(self, audio):
        import torch
        p, model = self.processor, self.model
        first = self.mel == 0
        features = p(audio, sampling_rate=16000, language=self.language,
                     is_streaming=True, is_first_audio_chunk=first).to(model.device, dtype=model.dtype)
        n = p.num_mel_frames_first_audio_chunk if first else p.num_mel_frames_per_audio_chunk
        inputs = features.input_features[:, :n]
        with torch.inference_mode():
            encoded = model.get_audio_features(input_features=inputs, prompt_ids=features.prompt_ids,
                past_key_values=self.encoder_cache, padding_cache=self.padding_cache,
                num_lookahead_tokens=p.default_num_lookahead_tokens, use_cache=True, output_attention_mask=False)
            self.encoder_cache, self.padding_cache = encoded.past_key_values, encoded.padding_cache
            output = []
            for i in range(encoded.pooler_output.shape[1]):
                for _ in range(model.max_symbols_per_step):
                    decoded = model.decoder(self.token, cache=self.decoder_cache)
                    logits = model.joint(encoded.pooler_output[:, i:i+1, None, :], decoded[:, None, :, :])
                    self.token = logits.reshape(1, -1).argmax(-1).reshape(1, 1)
                    value = self.token.item()
                    if value == model.config.blank_token_id:
                        break
                    output.append((value, self.offset+(self.frames+i)*.08))
            self.frames += encoded.pooler_output.shape[1]
        self.mel += n
        return output


class DiarStream:
    def __init__(self, model, processor, offset=0, cache=None):
        self.model, self.processor = model, processor
        self.offset, self.cache = offset, cache
        self.frame = 0
        self.pieces = None
        self.dropped = 0
        self.turns = []

    def window(self):
        p = self.processor
        return (0, p.num_samples_first_audio_chunk) if self.frame == 0 else (
            p.audio_chunk_start(self.frame), p.num_samples_per_audio_chunk)

    def step(self, audio):
        import torch
        first = self.frame == 0
        inputs = self.processor(audio, sampling_rate=16000, is_streaming=True,
            is_first_audio_chunk=first).to(self.model.device, dtype=self.model.dtype)
        with torch.inference_mode():
            output = self.model(**inputs, speaker_cache=self.cache)
        self.cache = output.speaker_cache
        logits = output.logits.cpu()
        self.pieces = logits if self.pieces is None else torch.cat([self.pieces, logits], dim=1)
        # 60 seconds of postprocessing history; model speaker cache is persistent.
        if self.pieces.shape[1] > 6000:
            excess = self.pieces.shape[1]-6000
            self.pieces = self.pieces[:, excess:]
            self.dropped += excess
        self.turns = self.processor.extract_speaker_dict(self.pieces)[0]
        for turn in self.turns:
            turn['Start'] += self.offset+self.dropped*.01
            turn['End'] += self.offset+self.dropped*.01
        self.frame += self.processor.num_mel_frames_per_step
