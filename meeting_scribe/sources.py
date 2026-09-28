"""Persisted capture mode determines audio routing, including saved recordings."""
from .storage import read_json


def microphone_only(folder):
    return read_json(folder / 'session.json').get('capture_mode') == 'microphone_only'


def audio_sources(folder):
    if microphone_only(folder):
        return {'microphone': '会议声音（麦克风混合）'}
    return {'microphone': '我 / 麦克风', 'system': '会议声音'}


def speaker_audio_key(folder):
    return 'microphone' if microphone_only(folder) else 'system'
