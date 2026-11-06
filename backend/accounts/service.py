"""Accounts: parent sign-up with id + password (no phone verification), parent-created child accounts,
one login for both roles (the server decides the role from the id), sessions by hash.

Passwords are hashed with scrypt (stdlib ``hashlib.scrypt``, N=2^14, r=8, p=1, 16-byte per-password salt),
stored self-describing as ``scrypt$N$r$p$<salt hex>$<hash hex>`` and compared in constant time. Plaintext is
never stored or logged. Login ids are unique across parents AND children, case-insensitively (dormant
accounts keep their ids reserved).

Rules (user decision 2026-11-06):
* parent id/password sign-up + terms / privacy / legal-guardian consents + email (payment-failure notices);
* children cannot self-register: the parent sets the child's id, password and nickname;
* 5 wrong child passwords lock the child account, the parent sets a new password to unlock it;
* logging in with a dormant parent account restores it (dormant children stay dormant until then).
"""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional, Tuple

KST = timezone(timedelta(hours=9))
MAX_CHILD_PASSWORD_FAILURES = 5
MAX_CHILDREN_PER_PARENT = 3
LOGIN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{3,19}$")  # 4-20 chars
PARENT_PASSWORD_MIN = 8   # + at least one letter and one digit
CHILD_PASSWORD_MIN = 4
PASSWORD_MAX = 128
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1

PARENT, CHILD = "parent", "child"


class AccountError(Exception):
    code = "account_error"

    def __init__(self, message: str = ""):
        super().__init__(message or self.code)


class ConsentRequired(AccountError):
    code = "consent_required"


class ValidationFailed(AccountError):
    code = "validation_failed"


class AlreadyRegistered(AccountError):
    code = "login_id_taken"


class NotFound(AccountError):
    code = "not_found"


class Forbidden(AccountError):
    code = "forbidden"


class LoginRequired(Forbidden):
    code = "login_required"


class ParentOnly(Forbidden):
    """A child session called a parent (management) API."""
    code = "parent_only"


class InvalidCredentials(AccountError):
    code = "invalid_credentials"

    def __init__(self, remaining: Optional[int] = None):
        super().__init__("wrong id or password")
        self.remaining = remaining  # child accounts only: attempts left before the lock


class ProfileLocked(AccountError):
    code = "profile_locked"


class AccountDormant(AccountError):
    code = "account_dormant"


class ChatbotDisabled(AccountError):
    code = "chatbot_disabled_no_ai_consent"


def session_key(session: str) -> str:
    """Sessions are stored by hash only (a leaked table row is not a usable bearer token)."""
    return hashlib.sha256((session or "").encode()).hexdigest()


def hash_password(password: str, salt: Optional[bytes] = None) -> str:
    salt = salt or secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=32)
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${dk.hex()}"


_DUMMY_HASH = hash_password("dummy-password-for-timing")


def check_password(password: str, stored: Optional[str]) -> bool:
    """Constant-time check against a ``hash_password`` string (unknown ids are checked against a dummy)."""
    try:
        algo, n, r, p, salt, want = (stored or _DUMMY_HASH).split("$")
        if algo != "scrypt":
            return False
        got = hashlib.scrypt((password or "").encode("utf-8"), salt=bytes.fromhex(salt), n=int(n), r=int(r),
                             p=int(p), dklen=len(want) // 2).hex()
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, want) and stored is not None


def normalize_login_id(login_id: str) -> str:
    return (login_id or "").strip().casefold()


def validate_login_id(login_id: str) -> str:
    login_id = (login_id or "").strip()
    if not LOGIN_ID_RE.match(login_id):
        raise ValidationFailed("id must be 4-20 letters, digits, '.', '_' or '-' (starting with a letter or digit)")
    return login_id


def validate_password(password: str, role: str) -> None:
    password = password or ""
    if len(password) > PASSWORD_MAX:
        raise ValidationFailed(f"password must be at most {PASSWORD_MAX} characters")
    if role == PARENT:
        if len(password) < PARENT_PASSWORD_MIN or not re.search(r"[A-Za-z]", password) \
                or not re.search(r"\d", password):
            raise ValidationFailed("parent password: at least 8 characters with a letter and a digit")
    elif len(password) < CHILD_PASSWORD_MIN:
        raise ValidationFailed("child password: at least 4 characters")


@dataclass
class ParentAccount:
    id: str
    login_id: str
    password_hash: str
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
    login_id: str
    nickname: str  # no real name / contact info is collected
    password_hash: str
    ai_consent: bool
    failed_attempts: int = 0
    locked: bool = False
    created_at: Optional[datetime] = None
    ai_consent_at: Optional[datetime] = None
    extra: dict = field(default_factory=dict)


@dataclass(frozen=True)
class LoginResult:
    session: str
    role: str        # "parent" | "child"
    account_id: str  # parent id or child id
    parent_id: str
    nickname: Optional[str] = None
    restored_from_dormancy: bool = False


class AccountService:
    def __init__(self, clock: Callable[[], datetime] = lambda: datetime.now(KST)) -> None:
        self.clock = clock
        self.parents: Dict[str, ParentAccount] = {}
        self.children: Dict[str, ChildProfile] = {}
        self._sessions: Dict[str, str] = {}        # sha256(session token) -> parent id
        self._child_sessions: Dict[str, str] = {}  # sha256(session token) -> child id
        self._login_index: Dict[str, Tuple[str, str]] = {}  # casefolded login id -> (role, account id)
        # dormant accounts are moved out of the active stores (separate storage)
        self.dormant_parents: Dict[str, ParentAccount] = {}
        self.dormant_children: Dict[str, ChildProfile] = {}
        # called with the parent id after every successful parent login (e.g. screen-time lock reset)
        self.on_parent_login: List[Callable[[str], None]] = []

    # ---- ids -----------------------------------------------------------
    def rebuild_login_index(self) -> None:
        self._login_index = {}
        for p in list(self.parents.values()) + list(self.dormant_parents.values()):
            if p.login_id:
                self._login_index[normalize_login_id(p.login_id)] = (PARENT, p.id)
        for c in list(self.children.values()) + list(self.dormant_children.values()):
            if c.login_id:
                self._login_index[normalize_login_id(c.login_id)] = (CHILD, c.id)

    def login_id_available(self, login_id: str) -> bool:
        return normalize_login_id(login_id) not in self._login_index

    def _claim_login_id(self, login_id: str, role: str, account_id: str) -> str:
        login_id = validate_login_id(login_id)
        key = normalize_login_id(login_id)
        if key in self._login_index:
            raise AlreadyRegistered("this id is already in use")
        self._login_index[key] = (role, account_id)
        return login_id

    # ---- parent -------------------------------------------------------
    def signup_parent(
        self,
        login_id: str,
        password: str,
        email: str,
        agree_terms: bool,
        agree_privacy: bool,
        agree_legal_guardian: bool,
    ) -> ParentAccount:
        if not (agree_terms and agree_privacy and agree_legal_guardian):
            raise ConsentRequired("terms, privacy and guardian consents are required")
        validate_login_id(login_id)
        validate_password(password, PARENT)
        if not EMAIL_RE.match(email or ""):
            raise ValidationFailed("valid email required")
        pid = uuid.uuid4().hex
        login_id = self._claim_login_id(login_id, PARENT, pid)
        now = self.clock()
        parent = ParentAccount(
            id=pid,
            login_id=login_id,
            password_hash=hash_password(password),
            email=email.strip(),
            consents={"terms": True, "privacy": True, "legal_guardian": True},
            created_at=now,
            last_active_at=now,
        )
        self.parents[parent.id] = parent
        return parent

    def login(self, login_id: str, password: str) -> LoginResult:
        """One login for parents and children; the role comes from the account the id belongs to."""
        entry = self._login_index.get(normalize_login_id(login_id))
        if entry is None:
            check_password(password, None)  # same work as a real check (no id probing by timing)
            raise InvalidCredentials()
        role, aid = entry
        if role == PARENT:
            return self._login_parent(aid, password)
        return self._login_child(aid, password)

    def _login_parent(self, pid: str, password: str) -> LoginResult:
        parent = self.parents.get(pid) or self.dormant_parents.get(pid)
        if parent is None or not check_password(password, parent.password_hash):
            raise InvalidCredentials()
        restored = pid in self.dormant_parents
        if restored:
            self.reactivate_parent(pid)  # Seed: logging in with id/password restores a dormant account
        parent.last_active_at = self.clock()
        parent.dormancy_notice_at = None
        session = secrets.token_urlsafe(24)
        self._sessions[session_key(session)] = pid
        for hook in self.on_parent_login:
            hook(pid)
        return LoginResult(session, PARENT, pid, pid, restored_from_dormancy=restored)

    def _login_child(self, cid: str, password: str) -> LoginResult:
        child = self.children.get(cid)
        if child is None:
            if cid in self.dormant_children:
                raise AccountDormant("this account is dormant; a parent login restores it")
            raise InvalidCredentials()
        if child.locked:
            raise ProfileLocked("locked after 5 wrong passwords; ask your parent to set a new one")
        if not check_password(password, child.password_hash):
            child.failed_attempts += 1
            if child.failed_attempts >= MAX_CHILD_PASSWORD_FAILURES:
                child.locked = True
                raise ProfileLocked("locked after 5 wrong passwords; ask your parent to set a new one")
            raise InvalidCredentials(MAX_CHILD_PASSWORD_FAILURES - child.failed_attempts)
        child.failed_attempts = 0
        parent = self.parents.get(child.parent_id)
        if parent is not None:  # a child's use is activity of the family account (dormancy clock)
            parent.last_active_at = self.clock()
            parent.dormancy_notice_at = None
        session = secrets.token_urlsafe(24)
        self._child_sessions[session_key(session)] = cid
        return LoginResult(session, CHILD, cid, child.parent_id, nickname=child.nickname)

    def logout(self, session: str) -> None:
        k = session_key(session)
        self._sessions.pop(k, None)
        self._child_sessions.pop(k, None)

    def principal(self, session: str) -> Optional[Tuple[str, str]]:
        """("parent", parent id) | ("child", child id) | None for a session token."""
        k = session_key(session)
        pid = self._sessions.get(k)
        if pid is not None and pid in self.parents:
            return PARENT, pid
        cid = self._child_sessions.get(k)
        if cid is not None and cid in self.children:
            return CHILD, cid
        return None

    def parent_for_session(self, session: str) -> ParentAccount:
        who = self.principal(session)
        if who is None:
            raise LoginRequired("login required")
        if who[0] != PARENT:
            raise ParentOnly("parent account required")
        return self.parents[who[1]]

    def child_for_session(self, session: str) -> ChildProfile:
        who = self.principal(session)
        if who is None or who[0] != CHILD:
            raise LoginRequired("child login required")
        return self.children[who[1]]

    def verify_parent_password(self, parent_id: str, password: str) -> bool:
        parent = self.parents.get(parent_id)
        return check_password(password, parent.password_hash if parent else None)

    def _drop_sessions(self, parent_id: Optional[str] = None, child_id: Optional[str] = None) -> None:
        if parent_id is not None:
            for k in [k for k, pid in self._sessions.items() if pid == parent_id]:
                del self._sessions[k]
        if child_id is not None:
            for k in [k for k, cid in self._child_sessions.items() if cid == child_id]:
                del self._child_sessions[k]

    # ---- dormancy / deletion ------------------------------------------
    def children_of(self, parent_id: str) -> list:
        return [c for c in self.children.values() if c.parent_id == parent_id]

    def make_dormant(self, parent_id: str) -> None:
        parent = self.parents.pop(parent_id, None)
        if parent is None:
            raise NotFound("parent")
        parent.dormant_at = self.clock()
        self.dormant_parents[parent_id] = parent  # ids stay reserved in the login index
        for c in self.children_of(parent_id):
            self.dormant_children[c.id] = self.children.pop(c.id)
            self._drop_sessions(child_id=c.id)
        self._drop_sessions(parent_id=parent_id)

    def reactivate_parent(self, parent_id: str) -> None:
        parent = self.dormant_parents.pop(parent_id, None)
        if parent is None:
            raise NotFound("dormant parent")
        parent.dormant_at = None
        parent.dormancy_notice_at = None
        parent.last_active_at = self.clock()
        self.parents[parent_id] = parent
        for cid in [c.id for c in self.dormant_children.values() if c.parent_id == parent_id]:
            self.children[cid] = self.dormant_children.pop(cid)

    def remove_child(self, child_id: str) -> None:
        c = self.children.pop(child_id, None) or self.dormant_children.pop(child_id, None)
        if c is not None and c.login_id:
            self._login_index.pop(normalize_login_id(c.login_id), None)
        self._drop_sessions(child_id=child_id)

    def remove_parent(self, parent_id: str) -> None:
        parent = self.parents.pop(parent_id, None) or self.dormant_parents.pop(parent_id, None)
        if parent is not None and parent.login_id:
            self._login_index.pop(normalize_login_id(parent.login_id), None)
        self._drop_sessions(parent_id=parent_id)

    # ---- child (created by the parent only) ------------------------------
    def create_child(
        self, parent_id: str, nickname: str, password: str, ai_consent: bool = False, *,
        login_id: Optional[str] = None,
    ) -> ChildProfile:
        """``login_id`` is required on the HTTP API; service-level callers (tests, scripts) may omit it and
        get a generated one."""
        if parent_id not in self.parents:
            raise NotFound("parent")
        nickname = (nickname or "").strip()
        if not (1 <= len(nickname) <= 20):
            raise ValidationFailed("nickname must be 1-20 chars")
        if login_id is None:
            login_id = "kid-" + secrets.token_hex(6)
        validate_login_id(login_id)
        validate_password(password, CHILD)
        if sum(c.parent_id == parent_id for c in self.children.values()) >= MAX_CHILDREN_PER_PARENT:
            raise ValidationFailed("at most 3 child accounts")
        if any(c.parent_id == parent_id and c.nickname == nickname for c in self.children.values()):
            raise ValidationFailed("nickname already used")
        cid = uuid.uuid4().hex
        login_id = self._claim_login_id(login_id, CHILD, cid)
        now = self.clock()
        child = ChildProfile(
            id=cid,
            parent_id=parent_id,
            login_id=login_id,
            nickname=nickname,
            password_hash=hash_password(password),
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
            raise Forbidden("not your child account")
        return child

    def set_ai_consent(self, parent_id: str, child_id: str, consent: bool) -> ChildProfile:
        child = self._owned(parent_id, child_id)
        child.ai_consent = bool(consent)
        child.ai_consent_at = self.clock() if consent else None
        return child

    def reset_child_password(self, parent_id: str, child_id: str, new_password: str) -> ChildProfile:
        """Parent sets a new child password: unlocks the account and signs out the child's devices."""
        child = self._owned(parent_id, child_id)
        validate_password(new_password, CHILD)
        child.password_hash = hash_password(new_password)
        child.failed_attempts = 0
        child.locked = False
        self._drop_sessions(child_id=child_id)
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
