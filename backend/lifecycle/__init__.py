"""Account/profile deletion and dormancy.

* Deleting a child profile or a parent account immediately and permanently removes the child's
  learning records and conversations (checkpointer + thread metadata + safety events).
* Payment records are NOT deleted: they move to a separate archive and are kept 5 years
  (전자상거래법) before being purged.
* Accounts inactive for 1 year get a notice 30 days before dormancy, then become dormant
  (data moved to separate storage). Dormant accounts are deleted 1 year after going dormant;
  logging in again (identity re-verification) restores them.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Callable, Dict, List, Optional

from accounts.service import AccountService, KST, NotFound
from conversations import ConversationService

INACTIVITY_DAYS = 365
NOTICE_DAYS_BEFORE = 30
DORMANT_RETENTION_DAYS = 365
PAYMENT_RETENTION_YEARS = 5


@dataclass
class PaymentRecord:
    id: str
    parent_id: str
    amount: int
    paid_at: datetime
    payload: dict = field(default_factory=dict)


@dataclass
class ArchivedPayment:
    record: PaymentRecord
    archived_at: datetime
    delete_after: datetime


class PaymentStore:
    """Live payment records (operational store)."""

    def __init__(self) -> None:
        self.records: Dict[str, PaymentRecord] = {}

    def add(self, rec: PaymentRecord) -> None:
        self.records[rec.id] = rec


class PaymentArchive:
    """Separate storage for payment records of deleted accounts (5-year retention)."""

    def __init__(self) -> None:
        self.rows: List[ArchivedPayment] = []

    def purge_expired(self, now: datetime) -> int:
        keep = [r for r in self.rows if r.delete_after > now]
        n = len(self.rows) - len(keep)
        self.rows = keep
        return n


class LifecycleService:
    def __init__(
        self,
        accounts: AccountService,
        conversations: ConversationService,
        notifier,
        payments: Optional[PaymentStore] = None,
        archive: Optional[PaymentArchive] = None,
        clock: Callable[[], datetime] = lambda: datetime.now(KST),
    ) -> None:
        self.accounts, self.conversations, self.notifier = accounts, conversations, notifier
        self.payments = payments or PaymentStore()
        self.archive = archive or PaymentArchive()
        self.clock = clock
        # learning records per child (study planners, tracing, etc.): child_id -> store
        self.learning_stores: Dict[str, object] = {}
        # extra per-child purge hooks (e.g. usage counters, tracing)
        self.child_purge_hooks: List[Callable[[str], None]] = []
        self._dormant_vault: Dict[str, dict] = {}  # parent_id -> {thread_id: (meta, messages)}

    # ---- child data ---------------------------------------------------------
    def _purge_child_data(self, child_id: str) -> None:
        conv = self.conversations
        for m in conv.meta.for_child(child_id):
            conv._purge(m.thread_id)
        # checkpoints that lost their metadata row must not survive either
        chat = conv.chatbot
        chat.events[:] = [e for e in chat.events if e.child_id != child_id]
        chat._locks = {k for k in chat._locks if k[0] != child_id}
        self.learning_stores.pop(child_id, None)
        for hook in self.child_purge_hooks:
            hook(child_id)
        self.accounts.remove_child(child_id)

    def delete_child(self, parent_id: str, child_id: str) -> None:
        self.accounts._owned(parent_id, child_id)
        self._purge_child_data(child_id)

    # ---- account ------------------------------------------------------------
    def _archive_payments(self, parent_id: str) -> None:
        now = self.clock()
        keep_until = now.replace(year=now.year + PAYMENT_RETENTION_YEARS) if not (
            now.month == 2 and now.day == 29
        ) else now.replace(year=now.year + PAYMENT_RETENTION_YEARS, day=28)
        for rid in [r.id for r in self.payments.records.values() if r.parent_id == parent_id]:
            rec = self.payments.records.pop(rid)
            self.archive.rows.append(ArchivedPayment(rec, now, keep_until))

    def delete_account(self, parent_id: str) -> None:
        acc = self.accounts
        if parent_id not in acc.parents and parent_id not in acc.dormant_parents:
            raise NotFound("parent")
        for child in [c for c in list(acc.children.values()) + list(acc.dormant_children.values())
                      if c.parent_id == parent_id]:
            self._purge_child_data(child.id)
        self._dormant_vault.pop(parent_id, None)
        self._archive_payments(parent_id)
        acc.remove_parent(parent_id)

    # ---- dormancy -----------------------------------------------------------
    def login_parent(self, verification_token: str) -> str:
        """Login via identity verification; restores a dormant account's data."""
        before = set(self.accounts.dormant_parents)
        session = self.accounts.login_parent(verification_token)
        pid = self.accounts._sessions[session]
        if pid in before:
            self._restore_vault(pid)
        return session

    def _vault_child_threads(self, parent_id: str) -> None:
        conv = self.conversations
        child_ids = {c.id for c in self.accounts.dormant_children.values() if c.parent_id == parent_id}
        vault = {}
        for m in [m for m in conv.meta.all() if m.child_id in child_ids]:
            vault[m.thread_id] = (m, conv.checkpointer.load(m.thread_id))
            conv.checkpointer.delete_thread(m.thread_id)
            conv.meta.delete(m.thread_id)
        self._dormant_vault[parent_id] = vault

    def _restore_vault(self, parent_id: str) -> None:
        conv = self.conversations
        for tid, (meta, msgs) in self._dormant_vault.pop(parent_id, {}).items():
            conv.meta.put(meta)
            conv.checkpointer.save(tid, msgs)

    def run_dormancy_sweep(self) -> Dict[str, List[str]]:
        """Send 30-day notices, make long-inactive accounts dormant, delete expired dormant ones."""
        now = self.clock()
        acc = self.accounts
        out = {"notified": [], "dormant": [], "deleted": []}
        for p in list(acc.parents.values()):
            last = p.last_active_at or p.created_at
            idle = now - last
            if idle >= timedelta(days=INACTIVITY_DAYS - NOTICE_DAYS_BEFORE) and p.dormancy_notice_at is None:
                p.dormancy_notice_at = now
                dormant_on = last + timedelta(days=INACTIVITY_DAYS)
                self.notifier.notify(p.id, "dormancy_notice", {
                    "email": p.email, "dormant_on": dormant_on.astimezone(KST).date().isoformat()})
                out["notified"].append(p.id)
            if (
                idle >= timedelta(days=INACTIVITY_DAYS)
                and p.dormancy_notice_at is not None
                and now - p.dormancy_notice_at >= timedelta(days=NOTICE_DAYS_BEFORE)
            ):
                acc.make_dormant(p.id)
                self._vault_child_threads(p.id)
                out["dormant"].append(p.id)
        for p in list(acc.dormant_parents.values()):
            if p.dormant_at and now - p.dormant_at >= timedelta(days=DORMANT_RETENTION_DAYS):
                self.delete_account(p.id)
                out["deleted"].append(p.id)
        self.archive.purge_expired(now)
        return out
