# hanja-tutor 외부 데이터 출처 기록

조회일: 2026-11-05 · 작업 브랜치 `data/hanja-sources`

이 문서는 `data/` 아래 파일마다 출처, 라이선스, 범위, 알려진 문제, 사람 검수가 필요한 항목을 적는다.
모든 한자 집합은 아래 공식 파일을 내려받아 기계적으로 추출했다. 기억에 의존하거나 재구성한 목록은 없다.
원본 파일은 저장소에 넣지 않았다. 대신 SHA-256을 남겨 같은 파일인지 다시 확인할 수 있게 했다.

> **⚠ 재배포 허가 문제.** 두 기관 모두 이용약관에서 사전 허가 없는 복제·배포를 금지한다(아래 참조).
> **결정(2026-11-05): 로컬 시험 전용으로만 사용하므로 로컬 저장소에 커밋한다.**
> 원격 저장소 push, 외부 배포, 서비스 출시 전에는 두 기관의 사전 허가를 받아야 한다
> (한국어문회 1566-1400, 대한검정회 02-386-4848).

---

## 1. 한국어문회 (body key `korea_eomunhoe`)

### 출처
- 페이지: 급수배정 https://www.hanja.re.kr/kccpt/exam/levelConfirm.do
- 다운로드: `POST https://www.hanja.re.kr/file/downloadDoc.do`
  form `folder_name=levelConfirm`, `file_name=<파일명>`, `_csrf=<페이지 meta 토큰>`. 세션 쿠키 필요, 로그인은 필요 없음.

| 파일명(원문) | 용도 | SHA-256 |
|---|---|---|
| 배정한자 전체 대표훈음 부수 음표 명기본(xls).xls (최종 저장 2020-05-25) | DB(`assignments.csv`, 훈·음) | 97b8db715ebe1dd6e00e8e58d338a9ba70e7420bf5ec84da38f987d60eb64b17 |
| 배정한자8급.hwp | 공식 목록 | 364a9e2a8a7329d3688bd04bf952d57d99b008bfc8b6e1bd5ce809adbcb25756 |
| 배정한자7급II.hwp | 공식 목록 | 2d84ff2710d15bea2a198e56280b911fe1539ba0a36094d899051973f13176ef |
| 배정한자7급.hwp | 공식 목록 | db21e876768729d793fe19cc441aee577d60f0835669e5f0aca5cddf2332d8cb |
| 배정한자6급II.hwp | 공식 목록 | 3fec615c539093e56e397c933bb8e84924e980d43ee1e5c98e4e9038c6bb53f3 |
| 배정한자6급.hwp | 공식 목록 | 1b1aa4a647ad25c1c2280c4e1019ca1c47eb5f62a5d4911c61f79a9c13fa0c20 |
| 배정한자5급II.hwp | 공식 목록 | 6687acabec7c6d96ca010eb156d63485fca90b006c2bca4335b9b2b4b03e7c55 |
| 배정한자5급.hwp | 공식 목록 | b071f45fbbe79a885648c817a5c7709aa9d3b2275871b212fb962c4bece41a89 |
| 배정한자4급II.hwp | 공식 목록 | 93673a68c2d4458af312492792aaadef77bc18251486d25b491f95ec0671710c |
| 배정한자4급.hwp | 공식 목록 | 7a2bafe97bfb5bbd5ea76367a61bf54f7d630a4744d434649fd6ef1c3ccced73 |

### 추출 방법(서로 독립인 공식 파일 두 종을 교차 사용)
- `official_lists/korea_eomunhoe/<급>.txt`: 각 급수 HWP의 "읽기配定漢字"는 누적 목록이다. 여기서 직전 급수의 누적 목록을 빼서 신습 한자만 남겼다. 쓰기配定漢字 절은 제외했다.
- `hanja_db/assignments.csv`: xls `배정한자` 시트의 `급수` 코드로 배정했다. 코드 대응은 80=8, 72=7II, 70=7, 62=6II, 60=6, 52=5II, 50=5, 42=4II, 40=4. 코드 의미는 xls `일러두기` 시트에 있다.
- 두 소스는 9개 급수 모두 누락·초과 0으로 일치한다. HWP 누적 수(50/100/150/225/300/400/500/750/1000)도 페이지 표와 같다.

### 범위
| 급수 | 8 | 7II | 7 | 6II | 6 | 5II | 5 | 4II | 4 | 합계 |
|---|---|---|---|---|---|---|---|---|---|---|
| 신습자 수 | 50 | 50 | 50 | 75 | 75 | 100 | 100 | 250 | 250 | 1000 |

### 라이선스/약관
- 사이트 하단: "본 사이트의 저작권 및 소유권은 사단법인 한국어문회에 있으며, 무단도용 … 허가하지 않은 행위를 금지합니다."
- 이용약관 제14조(https://www.hanja.re.kr/kccpt/footer/terms.do): "서비스를 이용함으로써 얻은 정보를 본회의 사전승인 없이 복제, 전송, 출판, 배포할 수 없습니다."
- **결론: 재배포하려면 허가가 필요하다.** 문의처는 1566-1400.

### 알려진 문제
- **호환 한자 코드포인트.** 원문은 KS X 1001 호환 영역 코드를 쓴다. 해당 글자: 金 U+F90A, 車 U+F902, 不 U+F967, 樂 U+F95C, 宅 U+FA04. NFC 정규화를 하면 통합 한자(U+91D1 등)로 바뀌어 공식 목록과 어긋날 수 있다. 원문 코드를 그대로 두었고, `hanja.csv`의 `char_nfc` 열에 NFC 결과를 따로 적었다.
- xls 曜 행의 원문이 "빛날요:"로 공백이 빠져 있다. 대표음 열('요')을 기준으로 hun=빛날, eum=요로 나눴다.
- 다음음자(多音字), 예: 金 "쇠 금 | 성(姓) 김"은 첫 번째 훈음만 `hun`/`eum`에 넣었다. 전체 원문은 `hunum_raw` 열에 있다. 장음 표시(`:`, `(:)`)는 `eum`에서 제거하고 `hunum_raw`에 남겼다.

---

## 2. 대한검정회 (body key `daegeom`)

### 출처
- 페이지: 자료실 › 등급별 선정한자 https://www.hanja.ne.kr/jupsu/jupsu07_01.asp
  (게시판 iframe: https://www.hanja.ne.kr/board/board/list.asp?tb=inno_12)
- 다운로드: `https://www.hanja.ne.kr/board/board/abc_down.asp?tb=inno_12&num=<N>&down=<1|2>` (로그인 불필요)

| num | 게시물 | 용도 | SHA-256 (HWP) |
|---|---|---|---|
| 15 | 등급별 선정한자(8급 ~ 사범) | 공식 목록 | ee84a859707bf6b708e5d97d55d6e67c061ead4a20724541c81727f344b92a96 |
| 1 | 2012년 8급 신출한자 훈음표 | DB + 훈음 | 466085beb9be537e78c077bf6b8cf571bd132dfa026a91dbf4694dcaf75c8478 |
| 2 | 2012년 7급 신출한자 훈음표 | DB + 훈음 | 10a193a3c566b7ee3d26a1f97aaee5f21b2c5fb03e2d066ff792440538b4821b |
| 3 | 2012년 6급 신출한자 훈음표 | DB + 훈음 | e746cb3b1cbd9ad47e4ba7811242d8743d0f7c30cf9c280e6dbb214408e0bebe |
| 4 | 2012년 준5급 신출한자 훈음표 | (준급) | fa3e4af8b6d8ce55df6b6fcdd7f54ac519b555fa1cd54f83e82c2d73bb68517f |
| 5 | 2012년 5급 신출한자 훈음표 | DB + 훈음 | 8d727a65803e0b7539d6ec95efa770e423d578924fbeac092d1d09e59bfe7a55 |
| 6 | 2012년 준4급 신출한자 훈음표 | (준급) | 621356ad0001f98cac3e6c6e9f27ee0a434a7e8a69323043d9cc6e8b05be0734 |
| 7 | 2012년 4급 신출한자 훈음표 (`down=2`, down=1은 서버 500) | DB + 훈음 | d991021e37ee9e1c838525008e1b4865db5669e107c618c83a64f7eaf4753bf8 |
| 8 | 2012년 준3급 신출한자 훈음표 | (준급) | b8ef0f70854f7d050b0b8f8467d20e55990e0ab160bcea3bee09bb350e15af94 |
| 9 | 2012년 3급 신출한자 훈음표 | DB + 훈음 | 305e6105dca43821e2e04ed27f1ad7d0e4e1877f1fbcb3f502f0a91513061fb9 |

게시물 날짜는 모두 26.07.29로 표시된다. 훈음표 제목은 "2012년"이다. 공식 페이지 상단의 "8급 30자 / 7급 50자(신출 20) / 6급 70자(신출 20)"는 추출 결과와 일치한다.

### 추출 방법
- `official_lists/daegeom/<급>.txt`: num=15 문서의 "N급 총 X자 중 신출 Y자" 절에 나온 한자를 그대로 옮겼다.
- `hanja_db/assignments.csv`: 급수별 "신출한자 표제훈음" 문서(num=1..9)의 표 행에서 가져왔다. 서로 다른 두 공식 문서인데 지원 급수 6개 모두 누락·초과 0이다.

### 범위
| 급수 | 8 | 7 | 6 | (준5) | 5 | (준4) | 4 | (준3) | 3 |
|---|---|---|---|---|---|---|---|---|---|
| 신출자 수 | 30 | 20 | 20 | 30 | 150 | 150 | 200 | 200 | 200 |
| 누적(공식) | 30 | 50 | 70 | 100 | 250 | 400 | 600 | 800 | 1000 |

### ⚠ 급수 체계 불일치 (사용자 결정 필요)
대한검정회에는 **준5급·준4급·준3급**이 있다. `exam_bodies.json`의 `daegeom` 급수는 `8,7,6,5,4,3`뿐이다.
지원 급수의 신출자만 모으면 5급 누적이 220자가 된다(공식 250). 4급은 570자(공식 600), 3급은 970자(공식 1000)가 된다. 준급 신출자가 학습 범위에서 빠지는 것이다.
- 준급 공식 목록은 `official_lists/daegeom/준5.txt`, `준4.txt`, `준3.txt`에 두었다. 현재 verify.py는 미지원 급수의 공식 목록을 비교하지 않으므로 이 파일들은 무시된다.
- 준급 DB 배정은 `hanja_db/assignments_daegeom_jun_grades.csv`로 분리했다. `assignments.csv`에 넣으면 verify가 "DB grade not supported"로 보고하기 때문이다.
- 선택지: (a) `exam_bodies.json`에 준급 추가(권장, Seed 수정 필요), (b) 준급 신출자를 다음 정급에 합치는 규칙을 Seed에 명시.

### 라이선스/약관
- 사이트 하단: "Copyright 2003 사단법인 대한민국한자교육연구회·대한검정회. All Right Reserved."
- 이용약관 제15조(https://www.hanja.ne.kr/service/service05.asp): "본회 홈페이지를 이용함으로써 얻은 정보를 본회의 사전승낙 없이 복제, 전송, 출판, 배포, 방송 기타 방법에 의하여 영리목적으로 이용하거나 제3자에게 이용하게 하여서는 안됩니다."
- **결론: 상용 앱에서 쓰거나 배포하려면 허가가 필요하다.** 문의처는 02-386-4848, webmaster@hanja.net.

### 알려진 문제
- 준3급 훈음표의 원문이 불완전하다. 取 행은 훈·음이 모두 비어 있다. 暴(U+FA06) 행은 "사나울"만 있고 음이 없다. 채워 넣지 않았다(`hunum_by_body.csv`). 지원 급수가 아니어서 현재 verify에는 영향이 없다.
- 호환 한자: 六 U+F9D1(8급), 不 U+F967·車 U+F902(준5), 樂 U+F95C(5), 宅 U+FA04(4), 復 U+F966·暴 U+FA06(준3).
  **같은 글자가 기관에 따라 다른 코드로 들어가 있다.** 六은 어문회 U+516D, 검정회 U+F9D1이다. 暴은 어문회 U+66B4, 검정회 U+FA06이다. 그래서 `hanja.csv`에 NFC 기준으로 같은 글자가 두 행 생긴다(金·六·復·暴). 앱에서 정규화 정책을 정해야 한다.

---

## 3. `hanja_db/` 파일 설명

| 파일 | 내용 |
|---|---|
| `assignments.csv` | body,grade,char. 지원 급수만 있다(어문회 1000행, 검정회 620행). |
| `assignments_daegeom_jun_grades.csv` | 검정회 준5/준4/준3 배정(380행). 정책 결정 전까지 분리해 둔다. |
| `hanja.csv` | char,hun,eum,reviewed,char_nfc,hunum_source,hunum_raw. 1170자, **reviewed는 전부 "0"**. 두 기관에 모두 있는 글자는 어문회 훈음을 우선했다. |
| `hunum_by_body.csv` | body,grade,char,hun,eum,source_raw. 기관별 원문 훈음을 손실 없이 보존한다. |

- **스키마 한계.** `hanja.csv`는 글자당 훈·음을 하나만 담는다. 두 기관 모두에 있는 830자 중 157자는 기관별 대표훈이 다르다(예: 兄 어문회 "형 형" / 검정회 "맏 형"). 대표음이 다른 글자도 3자다(易 역/이, 更 경/갱, 取 원문 누락). 시험이 기관별 표제훈음으로 채점된다면 훈음을 body별로 두도록 스키마를 바꿔야 한다.
- **사람 검수 필요.** 1129자(지원 급수 배정 글자)는 모두 `reviewed=0`이라 `verify.py`가 "not reviewed"로 보고한다. 검수자는 `hunum_raw`/`hunum_by_body.csv`와 원문을 대조한 뒤 `reviewed=1`로 바꾼다.

---

## 4. 획순 데이터 (`data/stroke_data/`): 수집하지 않음

AC7은 "한국 기준 참조 데이터와 100% 일치"를 요구한다. 그런데 **기계 판독 가능하고 재배포가 가능한 한국 표준 필순 데이터를 찾지 못했다.** 중국·일본 데이터를 "Korean reference"라는 이름으로 넣지 않기 위해 `strokes.json`과 `korean_reference.json`은 만들지 않았다.

필요한 스키마는 `backend/tracing/strokes.py` 기준이다. `{ "<char>": { "strokes": [[[x,y],...], ...], "reviewed": bool } }` 형태이고, hanzi-writer 1000×1000 좌표계를 쓰며 각 획은 중심선(median) 폴리라인이다. `korean_reference.json`은 reviewed 기본값이 true이고, 비교 허용오차는 eps=0(완전 일치)이다.

| 후보 | 기준 | 형식 | 라이선스 | 판단 |
|---|---|---|---|---|
| 교육부 한문 교육용 기초한자 1800 | 한국 | 글자 목록만 있고 필순 표준은 고시되지 않음 | 공공 | 필순 데이터 없음 |
| 국가 표준자형/필순 | 한국 | 2025년 연구(KCI)에 "국가 차원의 표준자형 제정이 부재"라는 지적이 있음 | - | 존재하지 않음 |
| 한국어문회 『급수별 지침서』 등 | 한국(기관) | 종이책·PDF 그림, 유료 | 저작권 보유 | 기관 기준이지만 기계 판독 불가, 허가 필요 |
| Make Me a Hanzi / hanzi-writer-data | 중국(PRC) | median+SVG JSON, 앱 좌표계와 같음 | Arphic PL / LGPL | 기반 데이터로 적합하지만 한국 기준이 아님 |
| KanjiVG | 일본 | SVG 경로 | CC BY-SA 3.0 | 한국 필순과 대체로 가깝다는 주장이 있으나 검증 필요, 좌표 변환 필요 |
| hanzi-writer-data-youyin | 혼합(makemeahanzi+jp+animCJK) | hanzi-writer JSON | MIT 표기(상위 라이선스 확인 필요) | 한국 기준 아님 |
| Wikimedia Commons Stroke Order Project | PRC/ROC/일본 3기준 | 그림(GIF/SVG) | CC 계열 | 한국 기준 없음 |

### 권장 계획
1. **기준 결정(사용자).** AC7의 "한국 기준"을 무엇으로 볼지 정한다. 국가 표준이 없으므로 실질적인 선택지는 시행 기관(한국어문회·대한검정회)이 펴낸 필순이다. 두 기관의 필순이 다를 가능성도 함께 확인한다.
2. **기반 데이터.** `strokes.json`은 Make Me a Hanzi(hanzi-writer 형식, 좌표계 일치)로 생성하고 `reviewed=false`로 둔다. 라이선스 고지(LGPL/Arphic)도 포함한다.
3. **한국 참조 구축.** 선택한 기관 교재의 필순(획 순서·방향·획수)을 사람이 글자별로 확인한다. 기반 데이터와 다르면 획 순서를 재배열하거나 분할·병합해 교정하고, 교정본을 `korean_reference.json`으로 만든다. 1차 범위는 쓰기 대상만으로 잡는다. 어문회는 쓰기 배정이 6II급 50자부터 4급 500자까지이고, 검정회는 쓰기 범위를 별도로 확인해야 한다.
4. **AC7 정의 보완 제안.** 현재 비교는 좌표 eps=0 완전 일치라서, 참조를 DB에서 복사하는 순간 동어반복이 된다. 한국 참조는 "획 순서·획 수·방향" 메타데이터로 정의하고 좌표는 허용오차로 비교하도록 Seed 수정을 권장한다.
5. 교재 필순을 데이터화하는 것도 2차적 저작 이용일 수 있다. 기관 허가를 1·2번 항목의 허가 요청과 묶어 진행한다.

참고(조사 출처): https://db.koreascholar.com/Article/Detail/405467 (표준자형 부재 지적), https://ko.wiktionary.org/wiki/부록:한문_교육용_기초_한자_1800 , https://github.com/rycont/hanja-grade-dataset (어문회 급수 데이터 2차 가공본, 저작권은 어문회에 있다고 명시. 사용하지 않음), https://www.wikipedia.org/wiki/Stroke_order

---

## 5. 기타 확인 사항
- **body key 불일치.** 코드(`exam_bodies.json`, `verify.py`, `test_assignment_fidelity.py`)는 모두 `korea_eomunhoe`를 쓴다. 참조 워크트리의 빈 디렉터리 `data/official_lists/kor_eomunhoe/`는 어떤 코드에서도 참조되지 않는 잔여물이다. 이 브랜치에서는 `korea_eomunhoe/`를 사용했다. 참조 워크트리에 병합할 때 `kor_eomunhoe/`를 삭제해야 한다.
- 이 브랜치는 main에서 갈라졌다. 그래서 verify를 실행하려고 `backend/hanja_db/{verify.py,__init__.py}`와 `data/exam_bodies.json`을 참조 워크트리에서 그대로 복사했다(내용 변경 없음).
