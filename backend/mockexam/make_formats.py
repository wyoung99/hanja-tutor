"""Regenerates data/exam_formats.json from the two official format files (kept as a script so it stays reviewable).

    python -I backend/mockexam/make_formats.py

Inputs (the only source of the numbers):
  data/official_lists/korea_eomunhoe/exam_formats_official.json  한국어문회 8급~4급 (hanja.re.kr)
  data/official_lists/daegeom/exam_formats_official.json         대한검정회 8급~3급, 준급 포함 (사용자 제공 표)

한국어문회: every official type keeps its official per-grade count. Types the app builds from its DB are real
question types (訓音 -> hun_eum, 漢字 쓰기 -> writing, 筆順 -> pilsun, 部首 -> busu). Types that need vocabulary
data the app does not have yet are filled by the closest supported type and recorded in ``substitutions``
(type_mix "partial_substitution"). No vocabulary item is invented.

대한검정회: official totals / pass line / points / minutes; all multiple choice (3급 이하 객관식). The per-type
mix is not published, so the app-defined split over eum / hun_eum / hun_to_hanja is marked ``app_defined``.
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OFFICIAL = ROOT / "data" / "official_lists"
EOMUNHOE_FILE = "data/official_lists/korea_eomunhoe/exam_formats_official.json"
DAEGEOM_FILE = "data/official_lists/daegeom/exam_formats_official.json"

# official type -> app type that builds it from the DB (real implementations)
IMPLEMENTED = {"hunum": "hun_eum", "hanja_sseugi": "writing", "pilsun": "pilsun", "busu": "busu"}

# official type -> (closest supported app type, why). Used until the vocabulary data exists.
SUBSTITUTE = {
    "dokeum": ("eum", "讀音(한자어 읽기) → 한 글자 음 읽기: 같은 '소리 읽기' 능력, 한자어 어휘 데이터 없음"),
    "jangdaneum": ("eum", "長短音(소리 길이) → 음: 소리 관련 유형, 장단음 데이터 없음"),
    "baneuieo": ("hun_to_hanja", "反義語 → 뜻(훈음)으로 한자 고르기: 뜻으로 한자를 찾는 능력, 반의어 쌍 데이터 없음"),
    "wanseong": ("hun_to_hanja", "完成型(成語) 빈칸 한자 → 뜻(훈음)으로 한자 고르기, 성어 데이터 없음"),
    "dongeuieo": ("hun_to_hanja", "同義語 → 뜻(훈음)으로 한자 고르기: 뜻이 같은 한자 찾기, 유의어 데이터 없음"),
    "dongeumieuieo": ("hun_to_hanja", "同音異義語 → 뜻(훈음)으로 한자 고르기: 소리가 아닌 뜻으로 구별, 한자어 데이터 없음"),
    "tteutpuri": ("hun_eum", "뜻풀이(한자어 뜻) → 한 글자 훈음(뜻): 뜻 풀이 능력, 한자어 데이터 없음"),
    "yakja": ("hun_to_hanja", "略字 → 한자 고르기: 글자 모양 식별, 약자 데이터 없음"),
    "hanmun": ("hun_eum", "漢文 → 훈음: 문장 데이터 없음 (8~4급에는 출제 없음)"),
}
ORDER = ["dokeum", "hunum", "jangdaneum", "baneuieo", "wanseong", "busu", "dongeuieo", "dongeumieuieo",
         "tteutpuri", "yakja", "hanja_sseugi", "pilsun", "hanmun"]
APP_LABELS = {"eum": "음(소리) 고르기", "hun_eum": "훈음 고르기", "hun_to_hanja": "한자 고르기"}
SUBSTITUTION_NOTICE = "일부 유형은 준비 중이라 비슷한 유형으로 대체됐어요"


def load(rel: str) -> dict:
    return json.loads((ROOT / rel).read_text(encoding="utf-8"))


def eomunhoe(off: dict) -> dict:
    labels = off["type_labels"]
    out = {}
    for grade, v in off["grades"].items():
        types, subs = [], {}
        for key in sorted(v["types"], key=ORDER.index):
            n = v["types"][key]
            if key in IMPLEMENTED:
                types.append({"official": key, "label": labels[key], "type": IMPLEMENTED[key], "count": n,
                              "points": 1})
            else:
                with_, why = SUBSTITUTE[key]
                types.append({"official": key, "label": labels[key], "type": with_, "count": n, "points": 1,
                              "substituted": True})
                subs[key] = {"count": n, "with": with_, "reason": why}
        out[grade] = {
            "total": v["total"], "minutes": v["minutes"], "pass_questions": v["pass"], "pass_score": v["pass"],
            "points_per_question": 1,
            "type_mix": "partial_substitution" if subs else "official",
            "notice": SUBSTITUTION_NOTICE if subs else None,
            "types": types, "substitutions": subs,
            # type counts per official type are official; which app question fills a substituted slot is not
            "verified": {"total": True, "pass": True, "minutes": True, "type_counts": True,
                         "question_kinds": not subs},
        }
    return out


def split3(n: int):
    a = round(n / 3)
    b = (n - a + 1) // 2
    return [("eum", a), ("hun_eum", b), ("hun_to_hanja", n - a - b)]


def daegeom(off: dict) -> dict:
    out = {}
    for grade, v in off["grades"].items():
        p = v["points_per_question"]
        out[grade] = {
            "total": v["total"], "minutes": v["minutes"], "pass_questions": v["pass_questions"],
            "pass_score": v["pass_points"], "points_per_question": p,
            "type_mix": "app_defined", "notice": None,
            "types": [{"type": t, "label": APP_LABELS[t], "count": c, "points": p} for t, c in split3(v["total"])],
            "substitutions": {},
            # official: total, pass line (points and questions), points per question, minutes, 객관식 only
            "verified": {"total": True, "pass": True, "minutes": True, "points": True, "all_multiple_choice": True,
                         "type_counts": False},
        }
    return out


def build() -> dict:
    eo, dg = load(EOMUNHOE_FILE), load(DAEGEOM_FILE)
    return {
        "_comment": "Generated by backend/mockexam/make_formats.py from the official format files (do not edit by "
                    "hand). Per body/grade, grades are NOT mapped across bodies. pass_score is in points; "
                    "pass_questions is the official pass line in questions. verified marks which values match the "
                    "official source. type_mix: official | partial_substitution (한국어문회 types needing vocabulary "
                    "data are filled by the closest supported type, see substitutions) | app_defined (대한검정회 "
                    "per-type mix is not published). format_pending lists supported grades with no known official "
                    "format (mock exam blocked) — currently none.",
        "sources": {
            "korea_eomunhoe": {"file": EOMUNHOE_FILE, "retrieved": eo["retrieved"], **eo["sources"]},
            "daegeom": {"file": DAEGEOM_FILE, "reference": dg["source"]},
        },
        "bodies": {"korea_eomunhoe": eomunhoe(eo), "daegeom": daegeom(dg)},
        # Supported grades whose official format is unknown get learning features only ("형식 준비 중").
        # Never invent a format: add a grade here only until its published format is obtained.
        "format_pending": {},
    }


if __name__ == "__main__":
    p = ROOT / "data" / "exam_formats.json"
    p.write_text(json.dumps(build(), ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
