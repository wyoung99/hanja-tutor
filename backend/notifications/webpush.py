"""Real Web Push delivery with pywebpush (VAPID, aes128gcm payload encryption).

Configuration (backend/.env): ``VAPID_PUBLIC_KEY`` (base64url uncompressed P-256 point, handed to the
browser as applicationServerKey), ``VAPID_PRIVATE_KEY`` (base64url raw 32-byte private key) and
``VAPID_SUBJECT`` (``mailto:`` or https URL of the operator). Missing keys -> push disabled, inbox +
email fallback only. ``python -m notifications.webpush keygen`` prints a fresh key pair.
"""
from __future__ import annotations

import base64
import json
import logging
from dataclasses import dataclass
from typing import Mapping, Optional

from .service import PushSubscription

log = logging.getLogger("hanja.webpush")
DEFAULT_TTL = 24 * 3600


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def generate_vapid_keys() -> dict:
    """New P-256 key pair: {"public_key": applicationServerKey, "private_key": raw private key} (base64url)."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    k = ec.generate_private_key(ec.SECP256R1())
    raw_priv = k.private_numbers().private_value.to_bytes(32, "big")
    pub = k.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return {"public_key": _b64url(pub), "private_key": _b64url(raw_priv)}


@dataclass(frozen=True)
class VapidConfig:
    public_key: str
    private_key: str
    subject: str

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Optional["VapidConfig"]:
        pub, priv = env.get("VAPID_PUBLIC_KEY", ""), env.get("VAPID_PRIVATE_KEY", "")
        if not (pub and priv):
            return None
        sub = env.get("VAPID_SUBJECT") or "mailto:admin@example.com"
        if not sub.startswith(("mailto:", "https://")):
            raise ValueError("VAPID_SUBJECT must start with mailto: or https://")
        return cls(pub, priv, sub)


class WebPushSender:
    def __init__(self, cfg: VapidConfig, timeout: float = 10.0, ttl: int = DEFAULT_TTL) -> None:
        from py_vapid import Vapid

        self.cfg, self.timeout, self.ttl = cfg, timeout, ttl
        self._vapid = Vapid.from_string(private_key=cfg.private_key)

    def send(self, sub: PushSubscription, message: dict) -> int:
        from pywebpush import WebPushException, webpush

        try:
            r = webpush(sub.info(), json.dumps(message, ensure_ascii=False), vapid_private_key=self._vapid,
                        vapid_claims={"sub": self.cfg.subject}, timeout=self.timeout, ttl=self.ttl,
                        headers={"Urgency": "high" if message.get("kind") in ("safety_event", "extension_request")
                                 else "normal"})
            return r.status_code
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            log.info("push service answered %s", status)
            return int(status or 0)


if __name__ == "__main__":  # python -m notifications.webpush keygen
    import sys

    if sys.argv[1:] == ["keygen"]:
        keys = generate_vapid_keys()
        print(f"VAPID_PUBLIC_KEY={keys['public_key']}\nVAPID_PRIVATE_KEY={keys['private_key']}")
