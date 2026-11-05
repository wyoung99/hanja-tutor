from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Optional, Protocol

KST = timezone(timedelta(hours=9))
MAX_PIN_FAILURES = 5
MAX_CHILDREN_PER_PARENT = 3
PIN_RE = re.compile(r"^\d{4,6}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AccountError(Exception):
    code = "account_error"

    def __init__(self, message: str = ""):
        super().__init__(message or self.code)


class IdentityNotVerified(AccountError):
    code = "identity_not_verified"


class ConsentRequired(AccountError):
    code = "consent_required"


class ValidationFailed(AccountError):
    code = "validation_failed"


class AlreadyRegistered(AccountError):
    code = "already_registered"


class NotFound(AccountError):
    code = "not_found"


class Forbidden(AccountError):
    code = "forbidden"


class ProfileLocked(AccountError):
    code = "profile_locked"


class InvalidPin(AccountError):
    code = "invalid_pin"

    def __init__(self, remaining: int):
        super().__init__(f"invalid pin, {remaining} attempts left")
        self.remaining = remaining


class ChatbotDisabled(AccountError):
    code = "chatbot_disabled_no_ai_consent"


@dataclass(frozen=True)
class VerifiedIdentity:
    ci: str  # connecting information issued by the identity provider


class IdentityVerifier(Protocol):
    def verify(self, verification_token: str) -> Optional[VerifiedIdentity]:
        """Return the identity for a completed phone verification, else None."""


class FakeIdentityVerifier:
    """Test/dev verifier: tokens are issued by complete_verification()."""

    def __init__(self) -> None:
        self._done: Dict[str, VerifiedIdentity] = {}

    def complete_verification(self, ci: str) -> str:
        token = secrets.token_hex(8)
        self._done[token] = VerifiedIdentity(ci=ci)
        return token

    def verify(self, verification_token: str) -> Optional[VerifiedIdentity]:
        return self._done.get(verification_token)


def hash_pin(pin: str, salt: bytes) -> str:
    return hashlib.scrypt(pin.encode(), salt=salt, n=2**12, r=8, p=1).hex()


@dataclass
class ParentAccount:
    id: str
    ci_hash: str  # hash of CI; phone number / real name are never stored
    email: str
    consents: Dict[str, bool]
    created_at: datetime
    last_active_at: Optional[datetime] = None
    dormancy_notice_at: Optional[datetime] = None
    dormant_at: Optional[datetime] = None


@dataclass
class ChildProfile:
    id: str
    parent_id: str
    nickname: str  # no real name / contact info is collected
    pin_salt: bytes
    pin_hash: str
    ai_consent: bool
    failed_attempts: int = 0
    locked: bool = False
    created_at: Optional[datetime] = None
    ai_consent_at: Optional[datetime] = None
    extra: dict = field(default_factory=dict)


class AccountService:
    def __init__(
        self,
        verifier: IdentityVerifier,
        clock: Callable[[], datetime] = lambda: datetime.now(KST),
    ) -> None:
        self.verifier = verifier
        self.clock = clock
        self.parents: Dict[str, ParentAccount] = {}
        self.children: Dict[str, ChildProfile] = {}
        self._ci_index: Dict[str, str] = {}
        self._sessions: Dict[str, str] = {}
        self._used_tokens: set = set()
        # dormant accounts are moved out of the active stores (separate storage)
        self.dormant_parents: Dict[str, ParentAccount] = {}
        self.dormant_children: Dict[str, ChildProfile] = {}
        self._dormant_ci_index: Dict[str, str] = {}

    # ---- parent -------------------------------------------------------
    def signup_parent(
        self,
        verification_token: str,
        email: str,
        agree_terms: bool,
        agree_privacy: bool,
        agree_legal_guardian: bool,
    ) -> ParentAccount:
        identity = (
            None
            if verification_token in self._used_tokens
            else self.verifier.verify(verification_token or "")
        )
        if identity is None:
            raise IdentityNotVerified("phone verification required to sign up")
        if not (agree_terms and agree_privacy and agree_legal_guardian):
            raise ConsentRequired("terms, privacy and guardian consents are required")
        if not EMAIL_RE.match(email or ""):
            raise ValidationFailed("valid email required")
        ci_hash = hashlib.sha256(identity.ci.encode()).hexdigest()
        if ci_hash in self._ci_index:
            raise AlreadyRegistered()
        self._used_tokens.add(verification_token)
        parent = ParentAccount(
            id=uuid.uuid4().hex,
            ci_hash=ci_hash,
            email=email,
            consents={"terms": True, "privacy": True, "legal_guardian": True},
            created_at=self.clock(),
            last_active_at=self.clock(),
        )
        self.parents[parent.id] = parent
        self._ci_index[ci_hash] = parent.id
        return parent

    def login_parent(self, verification_token: str) -> str:
        identity = self.verifier.verify(verification_token or "")
        if identity is None:
            raise IdentityNotVerified()
        ci_hash = hashlib.sha256(identity.ci.encode()).hexdigest()
        pid = self._ci_index.get(ci_hash)
        if pid is None and ci_hash in self._dormant_ci_index:
            pid = self._dormant_ci_index[ci_hash]
            self.reactivate_parent(pid)  # identity was just re-verified
        if pid is None:
            raise NotFound("not registered")
        self.parents[pid].last_active_at = self.clock()
        self.parents[pid].dormancy_notice_at = None
        session = secrets.token_urlsafe(24)
        self._sessions[session] = pid
        return session

    def parent_for_session(self, session: str) -> ParentAccount:
        pid = self._sessions.get(session)
        if pid is None:
            raise Forbidden("login required")
        return self.parents[pid]

    # ---- dormancy / deletion ------------------------------------------
    def children_of(self, parent_id: str) -> list:
        return [c for c in self.children.values() if c.parent_id == parent_id]

    def make_dormant(self, parent_id: str) -> None:
        parent = self.parents.pop(parent_id, None)
        if parent is None:
            raise NotFound("parent")
        parent.dormant_at = self.clock()
        self.dormant_parents[parent_id] = parent
        self._dormant_ci_index[parent.ci_hash] = parent_id
        self._ci_index.pop(parent.ci_hash, None)
        for c in self.children_of(parent_id):
            self.dormant_children[c.id] = self.children.pop(c.id)
        for tok in [t for t, pid in self._sessions.items() if pid == parent_id]:
            del self._sessions[tok]

    def reactivate_parent(self, parent_id: str) -> None:
        parent = self.dormant_parents.pop(parent_id, None)
        if parent is None:
            raise NotFound("dormant parent")
        parent.dormant_at = None
        parent.dormancy_notice_at = None
        parent.last_active_at = self.clock()
        self.parents[parent_id] = parent
        self._ci_index[parent.ci_hash] = parent_id
        self._dormant_ci_index.pop(parent.ci_hash, None)
        for cid in [c.id for c in self.dormant_children.values() if c.parent_id == parent_id]:
            self.children[cid] = self.dormant_children.pop(cid)

    def remove_child(self, child_id: str) -> None:
        self.children.pop(child_id, None)
        self.dormant_children.pop(child_id, None)

    def remove_parent(self, parent_id: str) -> None:
        parent = self.parents.pop(parent_id, None) or self.dormant_parents.pop(parent_id, None)
        if parent is not None:
            self._ci_index.pop(parent.ci_hash, None)
            self._dormant_ci_index.pop(parent.ci_hash, None)
        for tok in [t for t, pid in self._sessions.items() if pid == parent_id]:
            del self._sessions[tok]

    # ---- child --------------------------------------------------------
    def create_child(
        self, parent_id: str, nickname: str, pin: str, ai_consent: bool = False
    ) -> ChildProfile:
        if parent_id not in self.parents:
            raise NotFound("parent")
        nickname = (nickname or "").strip()
        if not (1 <= len(nickname) <= 20):
            raise ValidationFailed("nickname must be 1-20 chars")
        if not PIN_RE.match(pin or ""):
            raise ValidationFailed("PIN must be 4-6 digits")
        if sum(c.parent_id == parent_id for c in self.children.values()) >= MAX_CHILDREN_PER_PARENT:
            raise ValidationFailed("at most 3 child profiles")
        if any(c.parent_id == parent_id and c.nickname == nickname for c in self.children.values()):
            raise ValidationFailed("nickname already used")
        salt = secrets.token_bytes(16)
        now = self.clock()
        child = ChildProfile(
            id=uuid.uuid4().hex,
            parent_id=parent_id,
            nickname=nickname,
            pin_salt=salt,
            pin_hash=hash_pin(pin, salt),
            ai_consent=bool(ai_consent),
            created_at=now,
            ai_consent_at=now if ai_consent else None,
        )
        self.children[child.id] = child
        return child

    def _owned(self, parent_id: str, child_id: str) -> ChildProfile:
        child = self.children.get(child_id)
        if child is None:
            raise NotFound("child")
        if child.parent_id != parent_id:
            raise Forbidden("not your child profile")
        return child

    def set_ai_consent(self, parent_id: str, child_id: str, consent: bool) -> ChildProfile:
        child = self._owned(parent_id, child_id)
        child.ai_consent = bool(consent)
        child.ai_consent_at = self.clock() if consent else None
        return child

    def verify_pin(self, child_id: str, pin: str) -> ChildProfile:
        child = self.children.get(child_id)
        if child is None:
            raise NotFound("child")
        if child.locked:
            raise ProfileLocked("locked; ask your parent to reset the PIN")
        if hmac.compare_digest(hash_pin(pin or "", child.pin_salt), child.pin_hash):
            child.failed_attempts = 0
            return child
        child.failed_attempts += 1
        if child.failed_attempts >= MAX_PIN_FAILURES:
            child.locked = True
            raise ProfileLocked("locked after 5 wrong PIN attempts")
        raise InvalidPin(MAX_PIN_FAILURES - child.failed_attempts)

    def reset_pin(self, parent_id: str, child_id: str, new_pin: str) -> ChildProfile:
        child = self._owned(parent_id, child_id)
        if not PIN_RE.match(new_pin or ""):
            raise ValidationFailed("PIN must be 4-6 digits")
        child.pin_salt = secrets.token_bytes(16)
        child.pin_hash = hash_pin(new_pin, child.pin_salt)
        child.failed_attempts = 0
        child.locked = False
        return child

    # ---- feature gating ----------------------------------------------
    def features(self, child_id: str) -> Dict[str, bool]:
        child = self.children.get(child_id)
        if child is None:
            raise NotFound("child")
        return {
            "chatbot": child.ai_consent,
            "todays_hanja": True,
            "review": True,
            "tracing": True,
            "mock_exam": True,
            "reports": True,
        }

    def require_chatbot(self, child_id: str) -> None:
        if not self.features(child_id)["chatbot"]:
            raise ChatbotDisabled("AI processing consent missing")
