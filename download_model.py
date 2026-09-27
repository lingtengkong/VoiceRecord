"""The only application operation allowed to download model files."""
import argparse
import os

os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["HF_HUB_DISABLE_SYMLINKS_WARNING"] = "1"
os.environ.pop("HF_HUB_OFFLINE", None)

from huggingface_hub import HfApi, snapshot_download
from meeting_scribe.storage import atomic_json
from meeting_scribe.transcription import MODELS, model_path

parser = argparse.ArgumentParser()
parser.add_argument("model", choices=MODELS, nargs="?", default="large-v3")
args = parser.parse_args()
folder = model_path(args.model)
repo = MODELS[args.model]
revision = HfApi().model_info(repo).sha
print(f"Downloading {repo}@{revision} to {folder}", flush=True)
snapshot_download(repo_id=repo, revision=revision, local_dir=str(folder),
                  allow_patterns=["model.bin", "config.json", "preprocessor_config.json", "tokenizer.json", "vocabulary.*"],
                  max_workers=2)
atomic_json(folder / "ready.json", {"repository": repo, "revision": revision})
print("Model ready. Transcription can run offline.", flush=True)
