"""Install pinned OPUS-MT weights; translation itself never downloads anything."""
from huggingface_hub import snapshot_download
from meeting_scribe.translation import MODEL_DIR, MODEL_REPO, MODEL_REVISION, FILES

if __name__ == '__main__':
    snapshot_download(MODEL_REPO, revision=MODEL_REVISION, local_dir=str(MODEL_DIR), allow_patterns=FILES)
    print('Offline translation model installed:', MODEL_DIR)
