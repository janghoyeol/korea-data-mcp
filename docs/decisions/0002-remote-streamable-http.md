# 0002. 원격 서버(Streamable HTTP) 설계 선택지

- 상태: **채택** (2026-10-30, 사용자 합의) — 키는 **A(운영자 키 공유)**, 접근은 **a(무인증 공개)**, 호스팅은 **① Render 무료**.
  아래 선택지 표는 결정 당시 비교 기록으로 남긴다. 결정과 이유는 맨 아래 "결정".

## 맥락

- 1차(ADR 0001)는 로컬 stdio라서 쓰려면 Python 설치 + 공공 API 키 3개 발급이 필요하다. 면접관·고객이 "한번 꽂아 보기"엔 문턱이 높다.
- 원격 URL 하나면 claude.ai 커넥터나 `claude mcp add --transport http <URL>` 한 줄로 끝난다. FDE 포트폴리오로 보여주고 싶은 장면이 이것.
- 대신 서버가 **내 키로** 공공 API를 부르게 되므로 한도 소진·남용을 막아야 하고, 상시 떠 있는 호스팅이 필요하다.
- 질문 하나의 비용 감각 (2026-10-30 실측, "카카오 최근 3년 매출 추이"): 도구 7회 중 실제 공공 API 호출은 DART 4회
  (+ 첫 `dart_resolve_company` 때 고유번호 파일 0.7MB 한 번). 서버는 LLM을 부르지 않으니 Claude 비용은 0.

## SDK에서 확인한 것 (`mcp` 2.3.0)

- 저수준 `Server.streamable_http_app(...)`이 Starlette 앱을 돌려준다. 지금 `build_server()`를 그대로 쓰고 transport만 바꾸면 된다.
- 옵션: `stateless_http`, `json_response`, `max_request_body_size`, `max_sessions`, `session_idle_timeout`,
  `transport_security`(Host/Origin 허용 목록), `auth` + `token_verifier`(Bearer), `auth_server_provider`(OAuth 서버 내장).
- host가 localhost일 때만 DNS rebinding 보호가 자동으로 켜진다. 원격(0.0.0.0)에선 `allowed_hosts`를 직접 줘야 한다.

## 선택지

### 1. 누구의 키로 공공 API를 부르나

| | 방식 | 장점 | 단점 |
|---|---|---|---|
| A | 내 키를 모두가 공유 | 사용자는 URL만 있으면 됨 | 한도를 남이 다 쓸 수 있음. 일일 상한이 필수 |
| B | 사용자가 자기 키를 헤더로 (BYOK) | 내 한도와 무관, 남용 걱정 거의 없음 | 키 발급 문턱이 그대로라 원격의 장점이 반감. 헤더를 못 넣는 클라이언트가 있음 |
| C | **A + B 혼합**: 기본은 내 키(낮은 상한), 헤더로 자기 키를 주면 그 키로 | 데모는 바로, 실사용은 자기 키로 | 키 경로가 둘이라 테스트할 게 조금 늘어남 |

- 헤더로 받은 키는 저장·로깅하지 않는다 (요청 단위로만 쓰고 버림).
- 확인 필요: claude.ai 커넥터 설정 화면에서 임의 헤더를 넣을 수 있는지. 안 되면 B는 Claude Code/사내 에이전트 전용이 된다.

### 2. 접근 통제

| | 방식 | 장점 | 단점 |
|---|---|---|---|
| a | **무인증 공개 + 레이트리밋·일일 상한** | claude.ai 커넥터에 URL만 붙이면 됨. 데모에 최적 | 누구나 호출 가능 → 상한이 유일한 방어선 |
| b | 정적 Bearer 토큰 (내가 발급) | 구현 간단(SDK `token_verifier`) | 토큰을 나눠줘야 함. 커넥터에 따라 넣을 칸이 없을 수 있음 |
| c | OAuth (외부 IdP 또는 SDK 내장 서버) | 정석, 사용자별 한도 가능 | 구현·운영이 무거움. 조회 전용 공공데이터엔 과함 |

- 모든 도구가 조회 전용이고 데이터도 공개 데이터라, 뚫려도 잃는 건 **한도뿐**이다. 그래서 a + 아래 남용 방지가 비용 대비 맞다고 본다.
  사내 데이터를 다루는 프로젝트 A에서는 c가 맞다 (그때 OAuth를 제대로 해 본다).

### 3. 남용 방지 (선택지가 아니라 원격이면 다 넣을 것)

- **일일 전체 상한**: 도메인별 하루 공공 API 호출 수 상한. 넘으면 "오늘 공용 한도 소진, 자기 키를 쓰세요"를 에러 결과로 (trend-agent의 하루 비용 상한과 같은 발상).
- **IP별 레이트리밋**: 분당 N회. 프록시 뒤라면 `X-Forwarded-For` 처리 주의.
- **응답 캐시**: 공공데이터는 대부분 하루~분기 단위로 바뀐다. (도구, 인자) → 결과를 TTL 캐시하면 같은 질문 반복에 한도를 안 쓴다.
  대기질만 TTL을 짧게(측정 주기 1시간).
- `max_request_body_size`·`max_sessions`를 작게, `allowed_hosts`를 내 도메인으로.
- 상한 값은 각 포털 마이페이지의 실제 일일 한도를 보고 그 절반 정도로 잡는다 (한도 수치는 결정 시점에 재확인).

### 4. 세션 방식

- **stateless + JSON 응답** 추천. 16개 도구가 전부 요청 하나로 끝나는 조회라 세션 상태가 필요 없고,
  서버가 잠들었다 깨거나 재배포돼도 클라이언트 세션이 깨지지 않는다. 스트리밍(SSE)·서버→클라이언트 요청도 안 쓴다.

### 5. 호스팅

| | 곳 | 장점 | 단점 |
|---|---|---|---|
| ① | Render 무료 | 이미 에이전트 6개가 있음, 비용 0 | 15분 미접속 시 잠들고 깨는 데 ~1분 → MCP 클라이언트가 연결 단계에서 타임아웃될 가능성 큼 |
| ② | Render 유료 인스턴스 | 배포 방식 그대로, 안 잠듦 | 월 고정비 |
| ③ | Fly.io (auto stop/start) | curiosity-feed로 이미 써 봄. 멈췄다 깨는 게 수 초라 MCP 타임아웃 안쪽 | 설정 파일·리전 관리 하나 더 |
| ④ | Cloudflare Workers | showcase와 같은 곳, 잠듦 없음 | Python Workers + 동기 `requests` + 0.7MB 고유번호 파일 → 사실상 재작성. 기각 쪽 |

- 가격은 결정 시점에 각 서비스 요금표로 재확인.
- 어디든 컨테이너 디스크가 휘발성이라 DART 고유번호 파일은 콜드 스타트마다 다시 받는다 (0.7MB, 1회 호출이라 허용).

## 추천안 (합의 전 초안)

**C(내 키 + BYOK 헤더) · a(무인증 공개) · 남용 방지 전부 · stateless JSON · ③ Fly.io**.
claude.ai 커넥터에 URL만 붙여 바로 써 볼 수 있게 하고, 잃을 수 있는 건 공공 API 일일 한도뿐이라 상한과 캐시로 막는다.

## 결정 (2026-10-30)

1. **키: A (운영자 키만)**. 사용량이 많지 않을 것으로 보고 BYOK 헤더는 넣지 않는다. 자기 키가 필요한 사람은 로컬 stdio로.
   대신 같은 키를 원래 에이전트 4개도 쓰므로, 하루 상한을 포털 한도보다 넉넉히 낮게 둔다 (`guard.py`의 `DEFAULT_LIMITS`).
2. **접근: a (무인증 공개)** + 남용 방지 전부: 도메인별 하루 상한(KST), (도구, 인자) TTL 캐시, IP별 분당 60회, 요청 본문 64KB,
   `RENDER_EXTERNAL_HOSTNAME`로 Host/Origin 허용 목록.
   - IP는 `X-Forwarded-For` 첫 값. 위조로 IP 제한은 피할 수 있지만 최종선은 하루 상한이라 받아들인다.
   - 상한·카운터는 메모리에 있어 재시작(잠듦 포함) 때 초기화된다. 잠들려면 15분 동안 아무도 안 써야 하므로 남용 중엔 초기화되지 않는다.
3. **호스팅: ① Render 무료**. 다른 에이전트와 같은 방식(`render.yaml` Blueprint).
   - 알려진 비용: 잠든 서버는 깨는 데 ~1분이라 **첫 연결이 클라이언트 타임아웃으로 실패할 수 있다**. 완화: 루트(`/`)가 상태 확인을 겸해
     showcase 카드의 깨우기 배지가 서버를 미리 깨운다. README에 "실패하면 1분 뒤 다시"를 적는다.
   - 계속 깨워 두는 핑은 하지 않는다 (Render 무료 인스턴스 시간은 워크스페이스 전체가 나눠 쓴다).
4. 세션: stateless + JSON 응답 (위 4번 그대로).

## 확인 기록

- [x] 오프라인 테스트: 캐시·하루 상한·KST 날짜 경계·에러 미캐시·로컬 도구 우회·IP 제한·HTTP 429·uvicorn 위 Streamable HTTP (`tests/test_remote.py`)
- [x] 로컬 HTTP + 실제 키: `live_check.py --url` 4개 도메인 성공 (KOSIS·실거래 첫 시도는 연결 끊김 → 재실행 성공)
- [x] 로컬 HTTP + Claude: 평택시 인구 질문 → stat-agent와 같은 답(+12,493명, DT_1YL20651E), Haiku 5.5 $0.0042
- [x] Host 위조 421, Origin 위조 403, 허용 Host 200
- [x] Render 배포 (2026-10-30, `https://korea-data-mcp.onrender.com`, 싱가포르): `live_check.py --url` 4개 도메인 첫 시도 성공,
      `claude_check.py --url` "강남구 지금 미세먼지 어때?" → air-agent와 같은 경로(관측 → 기상 → 조언 도구), Haiku 5.5 $0.0028
- [ ] Claude Code 앱 `claude mcp add --transport http`, claude.ai 커넥터에 실제 등록 (로컬 PC에서)

## 구현 순서 (테스트 먼저) — 1~3 완료

1. 상한·캐시를 transport와 무관한 층으로 (`guard.py`) (`run_tool` 앞단) — 오프라인 테스트로 먼저
2. `korea-data-mcp --http` 진입점: `streamable_http_app(stateless_http=True, json_response=True, transport_security=...)`
3. 로컬에서 `mcp` Client의 Streamable HTTP로 `scripts/live_check.py`·`claude_check.py` 재실행
4. 배포 → 실제 URL로 Claude Code `--transport http` 연결 확인 → claude.ai 커넥터 연결 확인
5. showcase 카드 배지를 "원격 URL"로, README에 연결 한 줄 추가
