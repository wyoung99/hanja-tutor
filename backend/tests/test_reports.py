import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from accounts.service import Forbidden  # noqa: E402
from chatbot.service import RecordingNotifier  # noqa: E402
from conversations.service import ConversationService  # noqa: E402
from reports import ReportService, SubscriptionRequired  # noqa: E402
from screentime import ScreenTimeService  # noqa: E402
from study.planner import PlanHanja, StudyPlanner  # noqa: E402
from tracing.grader import TracingProgress  # noqa: E402
from billing import RecordingEmail  # noqa: E402
from mockexam.service import MockExamService, cumulative_pool  # noqa: E402
from test_billing import setup as billing_setup  # noqa: E402
from test_mockexam import ASSIGN, BODIES, FORMATS, HANJA, SDB, correct_answers  # noqa: E402

KST = timezone(timedelta(hours=9))
BODY, GRADE = "korea_eomunhoe", "8"


def world(can_push=lambda pid: True):
    bill, bot, usage, p, a, b, clock, gw, mail, push = billing_setup()
    pool = lambda bd, g: cumulative_pool(bd, g, BODIES[bd], ASSIGN, HANJA)  # noqa: E731
    exam = MockExamService(FORMATS, pool, SDB, clock=clock)
    chars = sorted(ASSIGN[(BODY, GRADE)])[:20]
    planners = {c.id: StudyPlanner([PlanHanja(ch, i + 1) for i, ch in enumerate(chars)], clock,
                                   start_date=clock().date()) for c in (a, b)}
    tracings = {a.id: TracingProgress(), b.id: TracingProgress()}
    acc = bot.accounts
    st = ScreenTimeService(acc, clock)
    conv = ConversationService(bot, acc)
    rep = ReportService(acc, bill, st, planners.__getitem__, tracings.__getitem__, exam, conv, usage,
                        push, mail, can_push, clock)
    return dict(rep=rep, bill=bill, usage=usage, p=p, a=a, b=b, clock=clock, exam=exam, st=st,
                planners=planners, tracings=tracings, conv=conv, push=push, mail=mail, chars=chars, acc=acc)


def subscribe(w):
    w["bill"].subscribe(w["p"].id, "monthly", "auth")


def study_a_day(w, cid, minutes):
    st, clock = w["st"], w["clock"]
    st.heartbeat(cid)
    for _ in range(minutes):
        clock.t += timedelta(minutes=1)
        st.heartbeat(cid)


def take_exam(w, cid, wrong=0, seed=1):
    ex = w["exam"].start(cid, BODY, GRADE, seed=seed)
    ans = correct_answers(ex)
    for q in ex.questions[:wrong]:
        ans[q.id] = "틀림"
    for k, v in ans.items():
        w["exam"].answer(ex.exam_id, k, v)
    return w["exam"].submit(ex.exam_id)


# ---- basic report: free for everyone ------------------------------------------------
def test_basic_report_for_free_parent_has_time_completion_and_review_accuracy():
    w = world()
    rep, a, p = w["rep"], w["a"], w["p"]
    assert not w["bill"].is_parent_paid(p.id)
    new = rep.start_day(a.id)
    assert len(new) >= 3
    rep.complete_new(a.id, new[0])
    rep.complete_new(a.id, new[1])
    study_a_day(w, a.id, 10)
    w["clock"].t += timedelta(days=1)  # review is due the next day
    rep.answer_review(a.id, new[0], True)
    rep.answer_review(a.id, new[1], False)
    rep.answer_review(a.id, new[0], True)
    r = rep.basic_report(p.id, a.id)
    assert r["study_minutes_total"] == 10.0
    assert r["todays_hanja"] == {"assigned": len(new), "completed": 2, "completion_rate": 2 / len(new)}
    assert r["review"]["answered"] == 3 and r["review"]["accuracy"] == pytest.approx(2 / 3)
    assert len(r["study_minutes_per_day"]) == 7


def test_basic_report_empty_and_owner_check():
    w = world()
    r = w["rep"].basic_report(w["p"].id, w["a"].id)
    assert r["todays_hanja"]["completion_rate"] is None and r["review"]["accuracy"] is None
    with pytest.raises(Forbidden):
        w["rep"].basic_report("someone-else", w["a"].id)


def test_basic_report_window_is_kst_days():
    w = world()
    w["clock"].t = datetime(2026, 11, 4, 23, 59, tzinfo=KST)
    w["rep"].start_day(w["a"].id)
    w["clock"].t = datetime(2026, 11, 5, 0, 0, tzinfo=KST)
    r = w["rep"].basic_report(w["p"].id, w["a"].id, days=1)
    assert r["from"] == r["to"] == "2026-11-05" and r["todays_hanja"]["assigned"] == 0
    assert w["rep"].basic_report(w["p"].id, w["a"].id, days=2)["todays_hanja"]["assigned"] > 0


def test_conversation_viewing_is_free_for_all_parents():
    w = world()
    assert not w["bill"].is_parent_paid(w["p"].id)
    tid, res = w["conv"].send(w["a"].id, "學이 뭐예요?")
    assert [t.thread_id for t in w["rep"].conversations_list(w["p"].id, w["a"].id)] == [tid]
    msgs = w["rep"].conversation_messages(w["p"].id, w["a"].id, tid)
    assert msgs[0]["content"] == "學이 뭐예요?"
    with pytest.raises(Forbidden):
        w["rep"].conversations_list("other", w["a"].id)


# ---- detailed report: subscribers only ---------------------------------------------------
def test_detailed_report_locked_for_free_parent():
    w = world()
    with pytest.raises(SubscriptionRequired):
        w["rep"].detailed_report(w["p"].id, w["a"].id)
    assert w["usage"].paid_events == []


def test_detailed_report_for_subscriber_has_all_four_parts_and_logs_paid_event():
    w = world()
    subscribe(w)
    rep, a = w["rep"], w["a"]
    new = rep.start_day(a.id)
    rep.complete_new(a.id, new[0])
    rep.complete_new(a.id, new[1])
    w["clock"].t += timedelta(days=1)
    rep.answer_review(a.id, new[0], False)
    tr = w["tracings"][a.id]
    tr.passed_days[new[1]] = {w["clock"]().date(), w["clock"]().date() + timedelta(days=1)}
    tr.retries[new[2]] = 2
    r1 = take_exam(w, a.id, wrong=2, seed=1)
    d = rep.detailed_report(w["p"].id, a.id)
    weak = {x["char"]: x["misses"] for x in d["weak_hanja"]}
    assert weak[new[0]] == 1 and weak[new[2]] == 2
    assert all(x["char"] in weak for x in [{"char": wr["char"]} for wr in r1["wrong"]])
    assert d["mock_exam_trend"][0]["score"] == r1["score"]
    assert d["pass_likelihood"]["likely_pass"] is True
    assert d["writing_completed_count"] == 1
    ev = w["usage"].paid_events
    assert len(ev) == 1 and ev[0].feature == "detailed_report" and ev[0].child_id == a.id


def test_pass_likelihood_uses_last_three_mock_scores_vs_cutoff():
    w = world()
    subscribe(w)
    f = FORMATS[(BODY, GRADE)]
    scores = []
    for i, wrong in enumerate([0, 0, f.total_score - f.pass_score, f.total_score - f.pass_score + 6]):
        w["clock"].t += timedelta(days=1)
        scores.append(take_exam(w, w["a"].id, wrong=wrong, seed=i)["score"])
    d = w["rep"].detailed_report(w["p"].id, w["a"].id)
    pl = d["pass_likelihood"]
    last3 = scores[-3:]
    assert pl["based_on"] == 3 and pl["average_score"] == pytest.approx(sum(last3) / 3)
    assert pl["margin"] == pytest.approx(sum(last3) / 3 - f.pass_score)
    assert pl["likely_pass"] == (pl["margin"] >= 0)
    assert [t["score"] for t in d["mock_exam_trend"]] == scores


def test_pass_likelihood_false_when_below_cutoff_and_none_without_exams():
    w = world()
    subscribe(w)
    assert w["rep"].detailed_report(w["p"].id, w["a"].id)["pass_likelihood"] is None
    f = FORMATS[(BODY, GRADE)]
    take_exam(w, w["b"].id, wrong=f.total_score - f.pass_score + 5)
    d = w["rep"].detailed_report(w["p"].id, w["b"].id)
    assert d["pass_likelihood"]["likely_pass"] is False


def test_detailed_report_locks_again_after_subscription_ends():
    w = world()
    subscribe(w)
    w["rep"].detailed_report(w["p"].id, w["a"].id)
    w["bill"].cancel(w["p"].id)
    w["rep"].detailed_report(w["p"].id, w["a"].id)  # paid period still running
    w["clock"].t += timedelta(days=32)
    w["bill"].run_due()
    with pytest.raises(SubscriptionRequired):
        w["rep"].detailed_report(w["p"].id, w["a"].id)
    # basic report still works
    assert w["rep"].basic_report(w["p"].id, w["a"].id)["child_id"] == w["a"].id


def test_detailed_report_requires_ownership():
    w = world()
    subscribe(w)
    with pytest.raises(Forbidden):
        w["rep"].detailed_report("intruder", w["a"].id)


# ---- weekly summary ---------------------------------------------------------------------------
def test_weekly_push_only_for_subscribers_once_per_kst_week():
    w = world()
    assert w["rep"].send_weekly_summaries() == []
    assert not [x for x in w["push"].sent if x[1] == "weekly_summary"]
    subscribe(w)
    assert w["rep"].send_weekly_summaries() == [w["p"].id]
    assert w["rep"].send_weekly_summaries() == []  # same week: no duplicate
    sent = [x for x in w["push"].sent if x[1] == "weekly_summary"]
    assert len(sent) == 1 and {c["nickname"] for c in sent[0][2]["children"]} == {"별이", "달이"}
    w["clock"].t = datetime(2026, 11, 9, 0, 0, tzinfo=KST)  # next Monday 00:00 KST
    assert w["rep"].send_weekly_summaries() == [w["p"].id]
    assert w["usage"].paid_events == []  # summary is not a refund-relevant "view"


def test_weekly_summary_falls_back_to_email_without_web_push():
    w = world(can_push=lambda pid: False)
    subscribe(w)
    assert w["rep"].send_weekly_summaries() == [w["p"].id]
    assert not [x for x in w["push"].sent if x[1] == "weekly_summary"]
    assert any(m["subject"] == "주간 학습 요약" and m["to"] == w["p"].email for m in w["mail"].sent)
