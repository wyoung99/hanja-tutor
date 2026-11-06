# 한자 튜터 (hanja-tutor)

초등학생이 **한국어문회(8~4급)** 또는 **대한검정회(8~3급, 준5·준4·준3급 포함)** 한자 시험을 골라 공부하는 PWA입니다.
오늘의 한자·간격 복습·획순 따라쓰기·실전형 모의고사·한자/어휘 챗봇을 제공하고, 학부모는 자녀 계정과 학습 계획,
리포트, 대화 열람, 사용 시간 제한을 관리합니다.

- 프론트엔드: Next.js(PWA) — `frontend/`
- 백엔드: Python FastAPI + LangChain/LangGraph — `backend/`
- LLM: AWS Bedrock (챗봇·안전 분류는 Claude Haiku 4.5, 모의고사 AI 문항 생성은 Claude Sonnet 4.6)
- 저장소: PostgreSQL (로컬은 pip 내장형 PostgreSQL 사용 가능, 시스템 설치 불필요)
- 명세: `hanja-tutor.seed.yaml` (Ouroboros Seed, 수락 기준 AC1~AC17)

> **로컬 시험 전용입니다.** `data/official_lists`, `data/hanja_db`에는 시험 기관 자료에서 만든 배정한자·훈음 데이터가
> 들어 있으며, 두 기관 약관상 허가 없는 복제·배포가 금지되어 있습니다. 저장소의 pre-push hook이 이 데이터가 들어 있는
> 커밋의 push를 막습니다. 자세한 출처와 조건은 `data/SOURCES.md`를 보세요.

---

## 1. 준비물

| 항목 | 버전·내용 |
|---|---|
| Python | 3.12 이상 (개발 환경: 3.14) |
| Node.js | npm 포함 (Next.js 15 빌드 가능 버전) |
| AWS 계정 | Bedrock `InvokeModel`, `InvokeModelWithResponseStream` 권한, 서울 리전(ap-northeast-2)에서 Claude Haiku 4.5·Sonnet 4.6 모델 접근 허용 |
| 우리말샘 오픈 API 키 | 선택. https://opendict.korean.go.kr 에서 발급 (없으면 사전 조회 없이 동작) |

## 2. 설치

저장소 루트(`C:\code\mini-pjt`)에서 실행합니다. 예시는 Windows PowerShell 기준입니다(macOS/Linux는 `.venv/bin/python`).

```powershell
# 백엔드: 저장소 루트에 가상환경을 만들고 의존성 설치
python -m venv .venv
.venv\Scripts\python -m pip install -r backend\requirements.txt

# 프론트엔드 의존성 설치
npm --prefix frontend ci
```

> 저장소 루트의 `.venv`는 필수입니다. npm 테스트 스크립트는 `python`을 그대로 호출하므로 `.venv\Scripts`가 PATH 앞에
> 있어야 하고, Ouroboros 평가기도 이 `.venv`를 찾아서 씁니다.

## 3. 환경 설정 (`backend/.env`)

`backend/.env.example`을 `backend/.env`로 복사하고 값을 채웁니다. `.env`는 git에서 제외되며 **비밀 값을 커밋하거나
공유하지 마세요.** 앱은 **실행하는 체크아웃의 `backend/.env`** 를 읽고, 이미 설정된 환경 변수가 있으면 그 값이 우선합니다.

```powershell
Copy-Item backend\.env.example backend\.env
```

| 키 | 설명 |
|---|---|
| `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY` | Bedrock 호출용 IAM 키 (필수: 실제 LLM 사용 시) |
| `AWS_SESSION_TOKEN` | 임시 자격 증명일 때만 입력 |
| `AWS_REGION`, `AWS_DEFAULT_REGION` | `ap-northeast-2` (서울: `global.*` 추론 프로필 사용) |
| `LLM_CONFIG` | `{"provider":"bedrock","region":"ap-northeast-2"}` — 비우면 가짜 LLM으로 동작 |
| `LLM_HEDGE_AFTER_S` | 첫 응답이 이 시간(초) 안에 안 오면 같은 요청을 한 번 더 보냄. 기본 1.5, `0`이면 끔 |
| `OPENDICT_API_KEY` | 우리말샘 API 키. 비우면 사전 조회 없이 "잘 모르겠어요" 등으로 답함 |
| `DATABASE_URL` | 외부 PostgreSQL 접속 URL (지정하면 내장 PostgreSQL 대신 사용) |
| `PG_EMBEDDED_DIR` | 내장 PostgreSQL 데이터 폴더. 둘 다 비우면 모든 데이터가 메모리에만 있음(재시작 시 사라짐) |
| `VAPID_PUBLIC_KEY`, `VAPID_PRIVATE_KEY`, `VAPID_SUBJECT` | Web Push 키와 연락 주소(`mailto:...`). 비우면 앱 안 알림함·이메일로만 알림 |

그 밖에 실행 시 환경 변수로 줄 수 있는 설정:

| 변수 | 설명 |
|---|---|
| `ADMIN_TOKEN` | 운영자 화면(`/admin`, 환불 검토)과 `/api/admin/*` 호출에 쓰는 토큰 |
| `HANJA_ENV` | `production`이면 개발용 시계 이동 API(`/api/dev/clock`)가 꺼짐 |
| `USAGE_CHATBOT_DAILY_LIMIT`, `USAGE_MOCKEXAM_WEEKLY_LIMIT` | 무료 플랜 한도 (기본 챗봇 20회/일, 모의고사 1회/주) |
| `BILLING_MONTHLY_PRICE`, `BILLING_YEARLY_PRICE` | 구독 요금 표시값 |
| `TOSS_SECRET_KEY` | 토스페이먼츠 키. 없으면 가짜 결제 게이트웨이 사용(결제는 구현만, 검증 제외) |
| `STROKE_TOL_POSITION`, `STROKE_TOL_LENGTH_MIN`, `STROKE_TOL_LENGTH_MAX`, `STROKE_TOL_DIRECTION_DEG` | 따라쓰기 획 판정 허용치 |

## 4. 실행

터미널 두 개를 엽니다.

**① 백엔드 (포트 8000)**

```powershell
cd C:\code\mini-pjt\backend
$env:PG_EMBEDDED_DIR = "$PWD\.pgdata\dev"     # 데이터를 재시작 후에도 유지 (backend/.pgdata 는 git 제외)
$env:ADMIN_TOKEN = "dev-admin"                # 운영자 화면을 쓸 때만
..\.venv\Scripts\python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`PG_EMBEDDED_DIR`는 `backend/.env`에 넣어 두어도 됩니다. 정상이면 http://127.0.0.1:8000/api/health 가
`{"ok": true, "state_store": "postgres", ...}`를 돌려줍니다.

**② 프론트엔드 (포트 3000)**

```powershell
cd C:\code\mini-pjt\frontend
npm run build                                  # 코드가 바뀌었을 때만
$env:BACKEND_URL = "http://127.0.0.1:8000"     # 기본값과 같으면 생략 가능
npx next start -p 3000
```

개발 중에는 `npm run dev`(핫 리로드)를 써도 됩니다. 브라우저에서 **http://localhost:3000** 을 엽니다.

**종료:** 각 터미널에서 `Ctrl+C`. 내장 PostgreSQL이 남아 있으면 다음으로 정상 종료합니다.

```powershell
.venv\Lib\site-packages\pixeltable_pgserver\pginstall\bin\pg_ctl.exe stop -D backend\.pgdata\dev -m fast
```

## 5. 사용 방법

### 학부모

1. 첫 화면은 **로그인**입니다. 처음이면 **"회원가입 (학부모)"** 링크로 `/signup`에 갑니다.
2. 아이디(4~20자, 영문·숫자·`._-`), 비밀번호(8자 이상, 영문+숫자), 이메일을 입력하고 약관·개인정보·법정대리인 동의에
   체크해 가입합니다. 휴대폰 본인인증은 없습니다.
3. 로그인하면 **관리 메뉴**만 보입니다.
   - **자녀 관리:** 자녀 계정 만들기(아이디, 비밀번호 4자 이상, 닉네임, AI 처리 동의, 시험 기관·급수), 학습 계획
     (시험일, 하루 새 한자 수), 사용 시간(하루 총 시간·사용 가능 시간대), 비밀번호 재설정, 삭제
   - **리포트·대화:** 기본 리포트(학습 시간·완료율·복습 정답률), 자녀 대화 열람·삭제, 안전 이벤트(원문 없이 표시).
     상세 리포트와 주간 푸시 요약은 구독자 전용
   - **알림:** 자녀의 사용 시간 연장 요청 승인·거절, 브라우저 푸시 알림 켜기
   - **구독:** 월간·연간 요금제, 해지, 7일 환불 요청 (결제는 로컬에서 가짜 게이트웨이)
   - **계정:** 부모 계정 정보, 로그아웃
4. 자녀가 비밀번호를 5번 틀리면 자녀 계정이 잠깁니다. **자녀 관리 → 비밀번호 재설정**으로 새 비밀번호를 정하면 풀립니다.

### 자녀

1. 같은 로그인 화면에서 부모가 만들어 준 아이디·비밀번호로 로그인합니다.
2. **학습 메뉴만** 보입니다.
   - **오늘의 한자:** 시험일까지 남은 배정한자를 나눈 하루 분량(3~15자). 훈·음은 선택한 기관 기준
     (예: 兄 — 한국어문회 "형 형", 대한검정회 "맏 형")
   - **복습:** 틀린 한자가 1·3·7·14일 간격으로 다시 나오고, 서로 다른 날 3번 연속 맞히면 익힘 완료
   - **따라쓰기:** 획 순서대로 그리면 통과. 틀린 획은 빨간색, 같은 획을 3번 틀리면 정답 획을 초록색으로 보여 줌
   - **모의고사:** 기관·급수의 공식 형식(문항 수·시간·합격선). 중간에 나가도 이어서 볼 수 있고, 시간이 끝나면 자동 제출.
     결과에 유형별 정답률·해설이 나오고 틀린 한자는 복습에 들어감. 한국어문회의 어휘형 문항(讀音 등)은 데이터가
     생길 때까지 비슷한 유형으로 대체되며 화면에 안내됨
   - **챗봇:** 한자·낱말 질문에 3~5문장으로 답함. "학교", "학교 뜻"처럼 짧은 낱말도 한자(學校)를 찾아 설명함.
     주제 밖 질문은 정중히 거절, 욕설·개인정보는 차단되고 부모에게 알림
3. 무료 플랜 한도(챗봇 하루 20회, 모의고사 주 1회)를 넘으면 해당 기능만 다음 초기화(자정·월요일 0시)까지 잠깁니다.
4. 부모가 정한 사용 시간이 끝나기 5분 전에 예고가 나오고, 시간이 되면 앱이 잠깁니다. 잠금 화면에서 연장을 요청하거나,
   옆에 있는 부모가 **부모 비밀번호**를 입력해 시간을 추가할 수 있습니다.

### 운영자

`ADMIN_TOKEN`을 설정하고 http://localhost:3000/admin 에서 환불 이의 제기(운영자 검토)를 승인·거절합니다.

### PWA 설치

브라우저 주소창의 설치 아이콘으로 앱처럼 설치할 수 있습니다.

## 6. 개발·시험용 기능

**시계 이동** (`HANJA_ENV`가 `production`이 아닐 때): 자정 초기화, 시험 자동 제출, 사용 시간 잠금을 바로 확인할 수 있습니다.

```powershell
# 60분 앞으로
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/dev/clock -ContentType 'application/json' -Body '{"advance_minutes": 60}'
# 원래 시간으로
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/dev/clock -ContentType 'application/json' -Body '{"reset": true}'
```

**AC6 표본 문항 검수 화면:** `.venv\Scripts\python backend\scripts\review_server.py` → http://127.0.0.1:8765
(키 1~4로 판정, "집계"로 오류율 계산)

**훈·음 데이터 재대조:** `.venv\Scripts\python -I backend\hanja_db\cross_verify.py` (결과: `data/hanja_db/review_report.csv`)

## 7. 테스트

PowerShell에서 먼저 `$env:PATH = "$PWD\.venv\Scripts;$env:PATH"` 를 실행한 뒤 저장소 루트에서:

| 명령 | 내용 | 비용 |
|---|---|---|
| `npm --prefix frontend run test:unit` | vitest + 백엔드 pytest (`.env`를 읽지 않음, 가짜 LLM) | 없음 |
| `npm --prefix frontend run test:smoke` | 실제 Bedrock·우리말샘 소량 호출 + 첫 토큰 p95 측정(30문항, 3초 미만) → `reports/p95-eval.json` | Bedrock 소액 |
| `npm --prefix frontend run test:all` | 위 두 개 (Ouroboros 평가의 1단계 검사 명령) | Bedrock 소액 |
| `npm --prefix frontend run e2e` | Playwright 전체 흐름 (자체 포트 8100/3100, 가짜 LLM) → `reports/e2e-results.json` | 없음 |
| `npm --prefix frontend run test:live` | 실제 Bedrock·우리말샘 전체 세트(안전 세트 114건 포함) | Bedrock |
| `npm --prefix frontend run lighthouse` | PWA 설치 가능성 검사 → `reports/lighthouse-pwa.json` | 없음 |
| `npm --prefix frontend run test:env` | 환경 변수를 비운 상태의 내장 PostgreSQL 시작 검사(선택) | 없음 |

자세한 백엔드 설정(영속화 테이블, Web Push, 측정 스크립트)은 `backend/README.md`를 참고하세요.

## 8. 폴더 구조

```
backend/          FastAPI 앱(app/), 계정·학습·모의고사·챗봇·알림·결제 모듈, tests/, scripts/
frontend/         Next.js 앱(app/, components/, lib/), e2e/, public/(manifest, service worker)
data/             배정한자 공식 목록·DB, 시험 형식, 획순 데이터, SOURCES.md (로컬 전용)
reports/          측정·검수 결과 (p95, E2E, Lighthouse, 표본 검수)
hanja-tutor.seed.yaml   명세(Seed)와 수락 기준
```

## 9. 문제 해결

- **로그인이 안 돼요(예전 계정):** 아이디·비밀번호 방식 이전에 만든 개발 DB 계정은 로그인할 수 없습니다. 새로 가입하세요.
  데이터를 비우려면 서버와 PostgreSQL을 끈 뒤 `backend/.pgdata/dev` 폴더를 지우세요.
- **챗봇이 실제로 답하지 않고 같은 문장만 나와요:** `backend/.env`의 `LLM_CONFIG`가 비어 있으면 가짜 LLM이 동작합니다.
- **`ModuleNotFoundError` (psycopg 등):** 저장소 루트 `.venv`에 `backend/requirements.txt`가 설치됐는지,
  `.venv\Scripts`가 PATH 앞에 있는지 확인하세요.
- **포트가 이미 사용 중이에요:** 이전에 띄운 서버가 남아 있는지 확인하세요
  (`Get-NetTCPConnection -LocalPort 3000,8000 -State Listen`).
