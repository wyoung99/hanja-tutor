"""Runtime environment for the composed app.

``backend/.env`` (path resolved from this package, not the CWD) is read at startup by ``load_runtime_env``.
Rules:
* variables already set in the process environment win over .env (override=False);
* empty values count as unset (an empty ``AWS_SESSION_TOKEN`` is never handed to boto3, an empty
  ``LLM_CONFIG`` keeps the fake LLM);
* AWS credentials are only consumed through the standard boto3 credential chain (process env);
  this module never logs or returns secret values.
* Unit tests set ``HANJA_SKIP_DOTENV=1`` (tests/conftest.py) so they stay hermetic; only the opt-in
  live tests load .env explicitly.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Mapping, MutableMapping, Optional

BACKEND_DIR = Path(__file__).resolve().parents[1]
DOTENV_PATH = BACKEND_DIR / ".env"
DEFAULT_AWS_REGION = "ap-northeast-2"
AWS_CRED_KEYS = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY")


def read_dotenv(path: Path = DOTENV_PATH) -> Dict[str, str]:
    """Non-empty key/values from a .env file ({} if the file is missing)."""
    if not Path(path).is_file():
        return {}
    from dotenv import dotenv_values

    return {k: v for k, v in dotenv_values(path).items() if v not in (None, "")}


def apply_dotenv(path: Path = DOTENV_PATH, environ: Optional[MutableMapping[str, str]] = None) -> Dict[str, str]:
    """Copy non-empty .env values into the process env without overriding existing variables.

    Empty token variables that boto3 would forward (AWS_SESSION_TOKEN="") are removed.
    Returns key -> source ("env" | "dotenv"); values are never returned.
    """
    environ = os.environ if environ is None else environ
    src: Dict[str, str] = {}
    for k, v in read_dotenv(path).items():
        if environ.get(k, "") == "":
            environ[k] = v
            src[k] = "dotenv"
        else:
            src[k] = "env"
    for k in ("AWS_SESSION_TOKEN", "AWS_SECURITY_TOKEN"):
        if k in environ and environ[k] == "":
            del environ[k]
    return src


def clean(env: Mapping[str, str]) -> Dict[str, str]:
    """Drop empty values: empty == unset everywhere in the app."""
    return {k: v for k, v in env.items() if v not in (None, "")}


def load_runtime_env(path: Path = DOTENV_PATH) -> Dict[str, str]:
    """Process env (+ backend/.env unless HANJA_SKIP_DOTENV=1), empty values removed."""
    if os.environ.get("HANJA_SKIP_DOTENV") != "1":
        apply_dotenv(path)
    return clean(os.environ)


def aws_credentials_configured(env: Optional[Mapping[str, str]] = None) -> bool:
    env = os.environ if env is None else env
    return all(env.get(k) for k in AWS_CRED_KEYS)


def aws_region(env: Mapping[str, str]) -> str:
    return env.get("AWS_REGION") or env.get("AWS_DEFAULT_REGION") or DEFAULT_AWS_REGION
