import json
import random
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mockexam import formats as F  # noqa: E402
from mockexam import questions as Q  # noqa: E402
from mockexam.service import MockExamService, ActiveExamError, ExamClosed, FormatPending, cumulative_pool  # noqa: E402
from hanja_db.verify import load_exam_bodies  # noqa: E402
from study.planner import StudyPlanner, PlanHanja, REASON_MOCK_WRONG  # noqa: E402
from tracing.strokes import StrokeData  # noqa: E402

KST = timezone(timedelta(hours=9))
T0 = datetime(2026, 3, 2, 10, 0, tzinfo=KST)
TOP = ((200, 300), (500, 300), (800, 300))
BOT = ((100, 700), (500, 700), (900, 700))

FORMATS = F.load_formats()
PENDING = F.load_pending()
BODIES = load_exam_bodies()
PER_GRADE = 60


def synthetic_world():
    """Synthetic reviewed DB: each (body, grade) newly assigns 60 chars; stroke data for all."""
    hanja, assign, n = {}, {}, 0
    for bi, (body, grades) in enumerate(BODIES.items()):
        for g in grades:
            chars = set()
            for _ in range(PER_GRADE):
                c = chr(0x4E00 + n)
                hanja[c] = Q.Hanja(c, f"훈{n}", f"음{n % 57}")
                chars.add(c)
                n += 1
            assign[(body, g)] = chars
    return hanja, assign


HANJA, ASSIGN = synthetic_world()
SDB = {c: StrokeData(c, (TOP, BOT)) for c in HANJA}


class Clock:
    def __init__(self, t=T0):
        self.t = t

    def __call__(self):
        return self.t


def make(clock=None, sdb=SDB, **kw):
    clock = clock or Clock()
    pool = lambda b, g: cumulative_pool(b, g, BODIES[b], ASSIGN, HANJA)  # noqa: E731
    kw.setdefault("pending", PENDING)
    return MockExamService(FORMATS, pool, sdb, clock=clock, **kw), clock


def correct_answers(exam):
    return {q.id: (q.answer if q.type != "writing" else [list(TOP), list(BOT)]) for q in exam.questions}


def test_formats_cover_every_supported_grade_independently():
    """Every supported grade has a format OR is explicitly declared format_pending (never both, never neither)."""
    supported = {(b, g) for b, gs in BODIES.items() for g in gs}
    assert set(FORMATS) | set(PENDING) == supported
    assert set(FORMATS) & set(PENDING) == set()
    for key in FORMATS:
        f = FORMATS[key]
        assert f.question_count == f.total_score and f.minutes > 0 and 0 < f.pass_score <= f.total_score
    for key, reason in PENDING.items():
        assert reason.strip() and F.exam_status(FORMATS, PENDING, *key) == "format_pending"
    assert not FORMATS[("korea_eomunhoe", "8")].has_writing


def test_jun_grades_have_no_invented_format():
    # decision 2026-11-05: no official format known for 대한검정회 준5·준4·준3
    for g in ("준5", "준4", "준3"):
        assert ("daegeom", g) not in FORMATS and ("daegeom", g) in PENDING
    assert F.exam_status(FORMATS, PENDING, "daegeom", "5") == "available"
    assert F.exam_status(FORMATS, PENDING, "daegeom", "2") == "unsupported"


@pytest.mark.parametrize("grade", ["준5", "준4", "준3"])
def test_mock_exam_start_blocked_for_format_pending_grade(grade):
    charged = []
    svc, _ = make(charge=charged.append)
    with pytest.raises(FormatPending, match="형식 준비 중"):
        svc.start("p1", "daegeom", grade, seed=1)
    assert svc.exams == {} and charged == []  # nothing created, nothing deducted
    # learning pool for the grade still exists (cumulative, same body only)
    assert len(cumulative_pool("daegeom", grade, BODIES["daegeom"], ASSIGN, HANJA)) > 0


@pytest.mark.parametrize("body,grade", sorted(FORMATS))
def test_exam_matches_selected_format(body, grade):
    svc, _ = make()
    ex = svc.start("p1", body, grade, seed=1)
    f = FORMATS[(body, grade)]
    assert len(ex.questions) == f.question_count
    for spec in f.types:
        got = [q for q in ex.questions if q.type == spec.type]
        assert len(got) == spec.count and all(q.points == spec.points for q in got)
    assert ex.deadline - ex.started_at == timedelta(minutes=f.minutes)
    assert Q.filter_questions(ex.questions, HANJA)[1] == []
    # questions only use chars of this body, up to this grade
    pool = {h.char for h in cumulative_pool(body, grade, BODIES[body], ASSIGN, HANJA)}
    assert {q.char for q in ex.questions} <= pool


def test_pass_fail_uses_real_cutoff():
    body, grade = "korea_eomunhoe", "8"
    f = FORMATS[(body, grade)]
    svc, _ = make()
    ex = svc.start("p1", body, grade, seed=2)
    ans = correct_answers(ex)
    wrong_ct = f.total_score - f.pass_score  # exactly at the cutoff
    for q in ex.questions[:wrong_ct]:
        ans[q.id] = "틀림"
    for qid, v in ans.items():
        svc.answer(ex.exam_id, qid, v)
    r = svc.submit(ex.exam_id)
    assert r["score"] == f.pass_score and r["passed"] is True
    svc2, _ = make()
    ex2 = svc2.start("p2", body, grade, seed=2)
    for q in ex2.questions[: wrong_ct + 1]:
        svc2.answer(ex2.exam_id, q.id, "틀림")
    for q in ex2.questions[wrong_ct + 1:]:
        svc2.answer(ex2.exam_id, q.id, q.answer)
    r2 = svc2.submit(ex2.exam_id)
    assert r2["score"] == f.pass_score - 1 and r2["passed"] is False


def test_result_has_score_type_rates_wrong_explanations_and_feeds_review():
    body, grade = "korea_eomunhoe", "5II"
    planner_chars = [PlanHanja(h.char, i) for i, h in enumerate(cumulative_pool(body, grade, BODIES[body], ASSIGN, HANJA))]
    planner = StudyPlanner(planner_chars, clock=lambda: T0)
    svc, _ = make(on_wrong_char=lambda pid, c: planner.add_review(c, REASON_MOCK_WRONG))
    ex = svc.start("p1", body, grade, seed=3)
    ans = correct_answers(ex)
    missed = [q for q in ex.questions if q.type == "hun_eum"][:4]
    for q in missed:
        ans[q.id] = "틀림"
    for k, v in ans.items():
        svc.answer(ex.exam_id, k, v)
    r = svc.submit(ex.exam_id)
    assert r["score"] == r["total"] - 4 and set(r["by_type"]) == {"eum", "hun_eum", "hun_to_hanja", "writing"}
    he = r["by_type"]["hun_eum"]
    assert he["correct"] == he["total"] - 4 and abs(he["rate"] - he["correct"] / he["total"]) < 1e-9
    assert r["by_type"]["eum"]["rate"] == 1.0
    assert {w["id"] for w in r["wrong"]} == {q.id for q in missed}
    assert all(w["explanation"] and w["answer"] for w in r["wrong"])
    assert {m.char for m in missed} <= set(planner.reviews)
    assert all(planner.reviews[m.char].reason == REASON_MOCK_WRONG for m in missed)


def test_public_view_hides_answers():
    svc, _ = make()
    ex = svc.start("p1", "daegeom", "8", seed=1)
    blob = json.dumps(svc.public_view(ex), ensure_ascii=False)
    assert "explanation" not in blob and '"answer"' not in blob


def test_resume_keeps_questions_answers_and_timer_runs_while_away():
    charged = []
    svc, clock = make(charge=charged.append)
    ex = svc.start("p1", "korea_eomunhoe", "8", seed=4)
    svc.answer(ex.exam_id, "q1", ex.questions[0].answer)
    clock.t = T0 + timedelta(minutes=20)  # app closed for 20 minutes
    again = svc.start("p1", "korea_eomunhoe", "8", seed=99)
    assert again is ex and again.answers == {"q1": ex.questions[0].answer}
    assert svc.remaining_seconds(ex.exam_id) == 30 * 60
    assert charged == ["p1"]  # resume never charges again
    with pytest.raises(ActiveExamError):
        svc.start("p1", "korea_eomunhoe", "7", seed=1)


def test_auto_submit_at_deadline_scores_saved_answers_and_no_second_charge():
    charged = []
    svc, clock = make(charge=charged.append)
    ex = svc.start("p1", "korea_eomunhoe", "8", seed=5)
    for q in ex.questions[:10]:
        svc.answer(ex.exam_id, q.id, q.answer)
    clock.t = T0 + timedelta(minutes=50, seconds=1)
    assert svc.remaining_seconds(ex.exam_id) == 0
    r = svc.result(ex.exam_id)
    assert r["auto_submitted"] and r["score"] == 10 and r["passed"] is False
    assert ex.submitted_at == ex.deadline
    with pytest.raises(ExamClosed):
        svc.answer(ex.exam_id, "q11", "x")
    assert charged == ["p1"]
    # a later start is a *new* exam and is charged
    ex2 = svc.start("p1", "korea_eomunhoe", "8", seed=6)
    assert ex2 is not ex and charged == ["p1", "p1"]


def test_charge_failure_creates_no_exam():
    def deny(_):
        raise RuntimeError("limit")
    svc, _ = make(charge=deny)
    with pytest.raises(RuntimeError):
        svc.start("p1", "daegeom", "8")
    assert svc.exams == {}


def test_writing_is_blank_cell_stroke_graded_and_only_for_gradable_chars():
    half = list(HANJA)[::2]
    # every other char lacks stroke data; one char with data is flagged exclude_from_grading
    sdb = {c: StrokeData(c, (TOP, BOT)) for c in half}
    flagged = half[0]
    sdb[flagged] = StrokeData(flagged, (TOP, BOT), exclude_from_grading=True)
    svc, _ = make(sdb=sdb)
    ex = svc.start("p1", "korea_eomunhoe", "4", seed=7)
    w = [q for q in ex.questions if q.type == "writing"]
    assert len(w) == 40 and all(q.char in sdb and not sdb[q.char].exclude_from_grading for q in w)
    assert all(q.options == () and "직접 쓰세요" in q.prompt for q in w)
    q0 = w[0]
    svc.answer(ex.exam_id, q0.id, [list(TOP), list(BOT)])  # right
    svc.answer(ex.exam_id, w[1].id, [list(BOT), list(TOP)])  # wrong order
    svc.answer(ex.exam_id, w[2].id, [list(TOP)])  # incomplete
    r = svc.submit(ex.exam_id)
    wrong_ids = {x["id"] for x in r["wrong"]}
    assert q0.id not in wrong_ids and w[1].id in wrong_ids and w[2].id in wrong_ids
    assert r["by_type"]["writing"]["correct"] == 1


def test_no_stroke_data_blocks_writing_exam_but_not_nonwriting():
    svc, _ = make(sdb={})
    with pytest.raises(Q.ExamUnavailable):
        svc.start("p1", "korea_eomunhoe", "4")
    assert svc.exams == {}
    assert len(svc.start("p1", "korea_eomunhoe", "8").questions) == 50  # 8급 has no writing


def test_verify_rejects_db_mismatch_and_filter_discards():
    q = Q.build_question("a", "hun_eum", 1, HANJA["一"], list(HANJA.values()), random.Random(1))
    h = HANJA[q.char]
    bad_answer = Q.Question("b", "eum", 1, f"다음 한자의 음(소리)은? {h.char}", h.char,
                            (h.eum, "음x", "음y", "음z"), "음x", Q.explanation_for(h), "ai")
    bad_expl = Q.Question("c", "eum", 1, f"다음 한자의 음(소리)은? {h.char}", h.char,
                          (h.eum, "음x", "음y", "음z"), h.eum, f"'{h.char}'의 훈음은 '틀린 훈 음'입니다.", "ai")
    unknown = Q.Question("d", "eum", 1, "다음 한자의 음(소리)은? 龘", "龘", ("a", "b"), "a", "x", "ai")
    mentions_extra = Q.Question("e", "eum", 1, f"다음 한자의 음(소리)은? {h.char}", h.char,
                                (h.eum, "음x", "음y", "음z"), h.eum, Q.explanation_for(h) + " 龘과 비슷해요.", "ai")
    ok = Q.build_question("f", "eum", 1, h, list(HANJA.values()), random.Random(2))
    kept, dropped = Q.filter_questions([bad_answer, bad_expl, unknown, mentions_extra, ok], HANJA)
    assert [k.id for k in kept] == ["f"] and {d[0].id for d in dropped} == {"b", "c", "d", "e"}


def test_audit_sample_of_200_plus_has_error_rate_below_1_percent():
    sample = []
    for body, grade in sorted(FORMATS):
        svc, _ = make()
        sample += svc.start("audit", body, grade, seed=11).questions
        svc.exams.clear()
    assert len(sample) >= 200
    checked, bad, rate = Q.audit_error_rate(sample, HANJA)
    assert checked >= 200 and rate < 0.01 and bad == 0
    # the audit really detects errors: corrupt 3 of 200 -> 1.5% flagged
    corrupt = [Q.Question(q.id, q.type, q.points, q.prompt, q.char, q.options, "음없음", q.explanation)
               if i < 3 and q.type == "eum" else q for i, q in enumerate(sample)]
    assert Q.audit_error_rate(corrupt, HANJA)[1] >= 1
