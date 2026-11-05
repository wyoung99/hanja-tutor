"""Local review screen for the AC6 sample sheet (reports/exam_sample_review.csv).

Shows one question at a time (prompt, options with the keyed answer, explanation, the DB 훈·음/부수/획수 it was
checked against, and a stroke figure for 筆順/漢字 쓰기), and writes the reviewer's verdict straight back into the
CSV's human_verdict / human_note columns. Standard library only; listens on 127.0.0.1.

    .venv/Scripts/python backend/scripts/review_server.py            # http://127.0.0.1:8765
    .venv/Scripts/python backend/scripts/review_server.py --port 9000 --csv path/to/copy.csv

Keys: 1 ok · 2 wrong_answer · 3 wrong_option · 4 other · ←/→ previous/next · N note · U next unjudged.
"집계" runs the same tally as `exam_sample_review.py --tally` (updates reports/exam_sample_review_summary.json).
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
import threading
import unicodedata
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CSV = ROOT / "reports" / "exam_sample_review.csv"
STROKES = ROOT / "data" / "stroke_data" / "strokes.json"
EOMUN_OFFICIAL = ROOT / "data" / "official_lists" / "korea_eomunhoe" / "exam_formats_official.json"
VERDICTS = ("ok", "wrong_answer", "wrong_option", "other")
APP_TYPE_LABELS = {"eum": "음", "hun_eum": "훈음", "hun_to_hanja": "한자 고르기", "writing": "漢字 쓰기",
                   "pilsun": "筆順", "busu": "部首"}
BODY_LABELS = {"korea_eomunhoe": "한국어문회", "daegeom": "대한검정회"}


class Sheet:
    """The CSV in memory; every save rewrites it atomically with the original encoding and line endings."""

    def __init__(self, path: Path):
        self.path = path
        raw = path.read_bytes()
        self.bom = raw.startswith(b"\xef\xbb\xbf")
        self.newline = "\r\n" if b"\r\n" in raw[:4096] else "\n"
        text = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text, newline=""))
        self.fields = list(reader.fieldnames or [])
        self.rows = list(reader)
        for col in ("human_verdict", "human_note"):
            if col not in self.fields:
                raise SystemExit(f"{path} has no {col} column")
        self.lock = threading.Lock()

    def save(self) -> None:
        buf = io.StringIO(newline="")
        w = csv.DictWriter(buf, fieldnames=self.fields, lineterminator=self.newline)
        w.writeheader()
        w.writerows(self.rows)
        data = ("﻿" if self.bom else "") + buf.getvalue()
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        tmp.write_text(data, encoding="utf-8", newline="")
        os.replace(tmp, self.path)

    def set_verdict(self, index: int, verdict: str, note: str) -> dict:
        if verdict not in VERDICTS and verdict != "":
            raise ValueError(f"verdict must be one of {VERDICTS} or empty")
        with self.lock:
            row = self.rows[index]
            row["human_verdict"], row["human_note"] = verdict, note.strip()
            self.save()
            return row


def load_type_labels() -> dict:
    labels = dict(APP_TYPE_LABELS)
    try:
        official = json.loads(EOMUN_OFFICIAL.read_text(encoding="utf-8"))
        labels.update(official.get("type_labels", {}))
    except (OSError, ValueError):
        pass
    return labels


def load_strokes() -> dict:
    try:
        chars = json.loads(STROKES.read_text(encoding="utf-8")).get("chars", {})
    except (OSError, ValueError):
        return {}
    return {unicodedata.normalize("NFC", k): v.get("strokes", []) for k, v in chars.items()}


def make_handler(sheet: Sheet, strokes: dict, labels: dict):
    page = PAGE.replace("__LABELS__", json.dumps(labels, ensure_ascii=False)) \
               .replace("__BODIES__", json.dumps(BODY_LABELS, ensure_ascii=False))

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # keep the console quiet
            pass

        def _send(self, code: int, body: bytes, ctype: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _json(self, code: int, obj) -> None:
            self._send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

        def do_GET(self):
            url = urlparse(self.path)
            if url.path == "/":
                return self._send(200, page.encode("utf-8"), "text/html; charset=utf-8")
            if url.path == "/api/items":
                return self._json(200, {"rows": sheet.rows, "csv": str(sheet.path)})
            if url.path.startswith("/api/strokes/"):
                ch = unicodedata.normalize("NFC", unquote(url.path[len("/api/strokes/"):]))
                return self._json(200, {"char": ch, "strokes": strokes.get(ch, [])})
            self._json(404, {"error": "not found"})

        def do_POST(self):
            url = urlparse(self.path)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._json(400, {"error": "bad json"})
            if url.path == "/api/verdict":
                try:
                    idx = int(body["index"])
                    if not 0 <= idx < len(sheet.rows):
                        raise IndexError(idx)
                    row = sheet.set_verdict(idx, str(body.get("verdict", "")), str(body.get("note", "")))
                except (KeyError, ValueError, IndexError) as e:
                    return self._json(400, {"error": str(e)})
                return self._json(200, {"ok": True, "row": row})
            if url.path == "/api/tally":
                if sheet.path.resolve() != DEFAULT_CSV.resolve():
                    return self._json(400, {"error": "tally only runs on reports/exam_sample_review.csv"})
                sys.path.insert(0, str(Path(__file__).resolve().parent))
                import exam_sample_review  # noqa: E402  (same tally as --tally)

                return self._json(200, exam_sample_review.tally())
            self._json(404, {"error": "not found"})

    return Handler


PAGE = r"""<!doctype html>
<html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>AC6 문항 검수</title>
<style>
:root{--bg:#f7f7f5;--card:#fff;--ink:#1d1d1f;--mute:#6b6b70;--line:#e3e3e0;--accent:#2f6fed;--ok:#1f8a4c;--bad:#c4352b;--warn:#b26b00;--chip:#eef1f6}
@media (prefers-color-scheme:dark){:root{--bg:#141416;--card:#1d1d20;--ink:#ececee;--mute:#9a9aa2;--line:#33333a;--accent:#6c9bff;--ok:#4cc27e;--bad:#ff6b5e;--warn:#f0a03a;--chip:#26262c}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 system-ui,"Malgun Gothic","Apple SD Gothic Neo",sans-serif}
header{position:sticky;top:0;background:var(--card);border-bottom:1px solid var(--line);padding:10px 16px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;z-index:2}
h1{font-size:16px;margin:0 8px 0 0}.bar{flex:1;min-width:160px;height:8px;background:var(--chip);border-radius:4px;overflow:hidden}.bar>i{display:block;height:100%;background:var(--accent)}
select,button,input{font:inherit;color:inherit}select,input[type=number]{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:5px 8px}
main{max-width:860px;margin:16px auto;padding:0 16px}.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:18px}
.meta{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}.chip{background:var(--chip);border-radius:999px;padding:2px 10px;font-size:13px}.chip.sub{color:var(--warn)}.chip.fail{color:var(--bad)}
.prompt{font-size:20px;margin:6px 0 12px}.big{font-size:44px;line-height:1.1}
.opts{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:8px;margin:8px 0 14px}.opt{border:1px solid var(--line);border-radius:10px;padding:10px 12px;font-size:20px}
.opt.ans{border-color:var(--ok);box-shadow:inset 0 0 0 1px var(--ok)}.opt.ans::after{content:"  ✓ 정답";font-size:13px;color:var(--ok)}
.kv{display:grid;grid-template-columns:120px 1fr;gap:4px 12px;font-size:14px;border-top:1px solid var(--line);padding-top:10px;margin-top:6px}.kv dt{color:var(--mute)}.kv dd{margin:0}
.note{color:var(--warn);font-size:13px;margin-top:8px}svg.fig{width:200px;height:200px;border:1px solid var(--line);border-radius:10px;background:var(--card);float:right;margin:0 0 8px 12px}
.verdicts{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:16px}.verdicts button{border:1px solid var(--line);background:var(--card);border-radius:10px;padding:10px 6px;cursor:pointer}
.verdicts button.on[data-v=ok]{background:var(--ok);color:#fff;border-color:var(--ok)}.verdicts button.on:not([data-v=ok]){background:var(--bad);color:#fff;border-color:var(--bad)}
.verdicts kbd{opacity:.7;font-size:12px;margin-right:4px}textarea{width:100%;margin-top:10px;min-height:56px;border:1px solid var(--line);border-radius:10px;padding:8px;background:var(--card);color:var(--ink);font:inherit}
nav{display:flex;justify-content:space-between;gap:8px;margin-top:12px}nav button{border:1px solid var(--line);background:var(--card);border-radius:10px;padding:8px 14px;cursor:pointer}
#status{font-size:13px;color:var(--mute)}#tally{white-space:pre-wrap;font-size:13px;margin-top:12px}
@media (max-width:560px){.verdicts{grid-template-columns:repeat(2,1fr)}svg.fig{float:none;display:block;margin:0 auto 10px}.kv{grid-template-columns:96px 1fr}}
</style></head><body>
<header><h1>AC6 문항 검수</h1><span id="count"></span><div class="bar"><i id="prog"></i></div>
<select id="filter"><option value="all">전체</option><option value="todo">미판정</option><option value="bad">문제 있음</option></select>
<label>번호 <input id="jump" type="number" min="1" style="width:80px"></label><button id="tallyBtn">집계</button></header>
<main><div class="card" id="card"></div>
<nav><button id="prev">← 이전</button><span id="status"></span><button id="next">다음 →</button></nav>
<div id="tally"></div></main>
<script>
const LABELS=__LABELS__, BODIES=__BODIES__;
const V=[["ok","정상"],["wrong_answer","정답 오류"],["wrong_option","보기 오류"],["other","기타"]];
let rows=[], order=[], pos=0, strokeCache={};
const $=s=>document.querySelector(s), esc=s=>String(s??"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
async function load(){const r=await fetch("/api/items");const j=await r.json();rows=j.rows;$("#status").textContent=j.csv;refilter(true);}
function refilter(keep){const f=$("#filter").value;const cur=order[pos];
 order=rows.map((_,i)=>i).filter(i=>f==="all"||(f==="todo"&&!rows[i].human_verdict)||(f==="bad"&&rows[i].human_verdict&&rows[i].human_verdict!=="ok"));
 pos=keep&&order.includes(cur)?order.indexOf(cur):0;render();}
function progress(){const done=rows.filter(r=>r.human_verdict).length,bad=rows.filter(r=>r.human_verdict&&r.human_verdict!=="ok").length;
 $("#count").textContent=`${done}/${rows.length} 판정 · 문제 ${bad}`;$("#prog").style.width=(100*done/rows.length)+"%";}
async function figure(r){const t=r.type;if(t!=="pilsun"&&t!=="writing")return"";const ch=t==="pilsun"?(r.prompt.match(/[㐀-鿿豈-﫿]/g)||[]).pop():r.answer;if(!ch)return"";
 if(!(ch in strokeCache)){const j=await (await fetch("/api/strokes/"+encodeURIComponent(ch))).json();strokeCache[ch]=j.strokes;}
 const st=strokeCache[ch];if(!st.length)return`<div class="note">획 데이터 없음: ${esc(ch)}</div>`;const hi=r.stroke_index===""?-1:+r.stroke_index;
 const paths=st.map((s,i)=>`<polyline points="${s.map(p=>p.join(",")).join(" ")}" fill="none" stroke="${i===hi?"#e0352b":(t==="writing"?"currentColor":"#9a9aa2")}" stroke-width="${i===hi?46:34}" stroke-linecap="round" stroke-linejoin="round"/>`
  +(t==="writing"?`<text x="${s[0][0]}" y="${s[0][1]}" font-size="60" fill="#2f6fed">${i+1}</text>`:"")).join("");
 return`<svg class="fig" viewBox="0 0 1000 1000" aria-label="획순 그림 ${esc(ch)}">${paths}</svg>`;}
async function render(){progress();const card=$("#card");if(!order.length){card.innerHTML="<p>이 필터에 해당하는 문항이 없어요.</p>";return;}
 const i=order[pos],r=rows[i];const opts=(r.options||"").split(" | ").filter(Boolean);const typ=LABELS[r.official_type]||LABELS[r.type]||r.type;
 const sub=r.substituted&&r.substituted!=="False"&&r.substituted!=="0";
 card.innerHTML=`${await figure(r)}<div class="meta"><span class="chip">#${esc(r.no)}</span><span class="chip">${esc(BODIES[r.body]||r.body)} ${esc(r.grade)}급</span>
  <span class="chip">${esc(typ)}</span>${sub?`<span class="chip sub">대체 출제 (${esc(LABELS[r.type]||r.type)})</span>`:""}<span class="chip">${esc(r.source)}</span>
  ${r.auto_check&&r.auto_check!=="pass"?`<span class="chip fail">자동 대조: ${esc(r.auto_check)}</span>`:""}</div>
  <div class="prompt">${esc(r.prompt)}</div>
  ${opts.length?`<div class="opts">${opts.map(o=>`<div class="opt${o===r.answer?" ans":""}">${esc(o)}</div>`).join("")}</div>`:`<div>정답: <span class="big">${esc(r.answer)}</span></div>`}
  <dl class="kv"><dt>해설</dt><dd>${esc(r.explanation)}</dd><dt>DB 훈·음</dt><dd>${esc(r.db_hun)} ${esc(r.db_eum)}</dd>
  ${r.db_radical?`<dt>DB 부수</dt><dd>${esc(r.db_radical)}</dd>`:""}${r.db_total_strokes?`<dt>DB 총획수</dt><dd>${esc(r.db_total_strokes)}</dd>`:""}
  ${r.stroke_index!==""?`<dt>표시한 획</dt><dd>${+r.stroke_index+1}번째 (빨간 획)</dd>`:""}${r.auto_problems?`<dt>자동 대조</dt><dd>${esc(r.auto_problems)}</dd>`:""}</dl>
  ${r.data_note?`<div class="note">${esc(r.data_note)}</div>`:""}
  <div class="verdicts">${V.map(([v,l],k)=>`<button data-v="${v}" class="${r.human_verdict===v?"on":""}"><kbd>${k+1}</kbd>${l}</button>`).join("")}</div>
  <textarea id="note" placeholder="메모 (선택, N 키로 이동)">${esc(r.human_note)}</textarea>`;
 card.querySelectorAll(".verdicts button").forEach(b=>b.onclick=()=>judge(b.dataset.v));
 $("#note").onchange=()=>save(rows[order[pos]].human_verdict);$("#jump").value=r.no;}
async function save(v){const i=order[pos];const note=$("#note")?$("#note").value:"";
 const res=await fetch("/api/verdict",{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({index:i,verdict:v,note})});
 const j=await res.json();if(!res.ok){$("#status").textContent="저장 실패: "+j.error;return false;}rows[i]=j.row;$("#status").textContent=`#${rows[i].no} 저장됨`;return true;}
async function judge(v){if(await save(v)){const f=$("#filter").value;if(f==="all"&&pos<order.length-1)pos++;else if(f!=="all")refilter(false);render();}}
function nextTodo(){const k=order.findIndex((i,p)=>p>pos&&!rows[i].human_verdict);if(k>=0){pos=k;render();}}
$("#prev").onclick=()=>{if(pos>0){pos--;render();}};$("#next").onclick=()=>{if(pos<order.length-1){pos++;render();}};
$("#filter").onchange=()=>refilter(true);
$("#jump").onchange=e=>{const i=rows.findIndex(r=>String(r.no)===e.target.value);if(i>=0){$("#filter").value="all";refilter(false);pos=order.indexOf(i);render();}};
$("#tallyBtn").onclick=async()=>{const r=await fetch("/api/tally",{method:"POST"});const j=await r.json();
 $("#tally").textContent=r.ok?`집계: 판정 ${rows.filter(x=>x.human_verdict).length}/${rows.length} · 오류 ${j.human_errors??"-"} · 오류율 ${j.human_error_rate==null?"-":(100*j.human_error_rate).toFixed(2)+"%"} · 기준(1% 미만) ${j.human_pass===true?"충족":j.human_pass===false?"미충족":"판정 미완료"}`:"집계 실패: "+j.error;};
document.addEventListener("keydown",e=>{if(e.target.tagName==="TEXTAREA"||e.target.tagName==="INPUT"){if(e.key==="Escape")e.target.blur();return;}
 if(e.key>="1"&&e.key<="4")judge(V[+e.key-1][0]);else if(e.key==="ArrowRight")$("#next").click();else if(e.key==="ArrowLeft")$("#prev").click();
 else if(e.key==="n"||e.key==="N"){e.preventDefault();$("#note")?.focus();}else if(e.key==="u"||e.key==="U")nextTodo();});
load();
</script></body></html>"""


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--csv", type=Path, default=DEFAULT_CSV)
    a = ap.parse_args()
    sheet = Sheet(a.csv.resolve())
    server = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(sheet, load_strokes(), load_type_labels()))
    done = sum(1 for r in sheet.rows if r["human_verdict"].strip())
    print(f"AC6 review: http://127.0.0.1:{a.port}  ({done}/{len(sheet.rows)} judged, sheet {sheet.path})  Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
