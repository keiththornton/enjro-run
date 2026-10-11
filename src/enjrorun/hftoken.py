"""Hugging Face token handling via huggingface_hub's own token file.

Enjro Run never stores the token itself and never returns it: callers only learn
whether one is set and where it comes from. HF_TOKEN in the environment wins
over the file, as it does for every other Hugging Face tool.
"""

import os
import re
from pathlib import Path

from huggingface_hub import constants

TOKEN_RE = re.compile(r"^hf_[A-Za-z0-9]{20,100}$")


def token_status() -> dict:
    if os.environ.get("HF_TOKEN"):
        return {"set": True, "source": "env"}
    path = Path(constants.HF_TOKEN_PATH)
    if path.is_file() and path.read_text().strip():
        return {"set": True, "source": "store"}
    return {"set": False, "source": None}


def set_token(token: str | None) -> dict:
    path = Path(constants.HF_TOKEN_PATH)
    if token is None:
        path.unlink(missing_ok=True)
        return token_status()
    token = token.strip()
    if not TOKEN_RE.match(token):
        raise ValueError("that doesn't look like a Hugging Face access token (hf_...)")
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(token)
    return token_status()
