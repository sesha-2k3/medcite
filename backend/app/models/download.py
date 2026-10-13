"""Download MedCPT encoder and cross-encoder weights into ``HF_HOME``.

    uv run python -m app.models.download

Runs during the Docker build so containers start without network access to Hugging Face.
Only the files transformers needs are fetched (no duplicate TF/Flax weights).
"""

from __future__ import annotations

import logging
import os

from app.config import get_settings
from app.observability import configure_logging

logger = logging.getLogger(__name__)

ALLOW_PATTERNS = ["*.json", "*.txt", "*.safetensors", "*.model", "vocab*"]
FALLBACK_PATTERNS = ["*.bin"]  # some repos only publish pytorch_model.bin


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    os.environ.setdefault("HF_HOME", settings.hf_home)

    from huggingface_hub import list_repo_files, snapshot_download

    for repo in (settings.embed_query_model, settings.embed_article_model, settings.rerank_model):
        files = list_repo_files(repo)
        patterns = list(ALLOW_PATTERNS)
        if not any(f.endswith(".safetensors") for f in files):
            patterns += FALLBACK_PATTERNS
        path = snapshot_download(repo_id=repo, allow_patterns=patterns)
        logger.info("model downloaded", extra={"repo": repo, "path": path})


if __name__ == "__main__":
    main()
