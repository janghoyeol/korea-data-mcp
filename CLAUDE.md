# korea-data-mcp — 프로젝트 브리핑

- 개발자: 장호열 (`janghoyeol`). FDE 포트폴리오 보강 프로젝트 B (A: 가상 고객사 사내 데이터 에이전트, C: 실제 고객 1곳 도입은 이후).
- 협업 방식·원칙은 trend-agent CLAUDE.md와 같음: 설계 선택지 먼저 합의, ADR 기록, 테스트 먼저, "됐다" 전에 실제 키로 라이브 확인, 키는 환경변수로만.
- 구조: stat/corp/air/realty-agent의 도구 16개를 복사(VENDORED.md)해 저수준 MCP `Server`로 노출 (ADR 0001). SDK는 `mcp` 2.x (`FastMCP` → `MCPServer`로 이름 바뀜. 여기서는 원문 스키마를 그대로 쓰려고 저수준 `Server` 사용).

## 진행 상황
- [x] 16개 도구 MCP 노출, 키 있는 도메인만 켜짐, 원문 스키마 보존 테스트, 오프라인 테스트 11개
- [x] stdio 실제 실행 확인, Claude(Haiku 5.5)가 MCP 도구를 골라 계산 (`scripts/claude_check.py`, $0.0004)
- [x] 공공 API 라이브 확인 (2026-10-30): `scripts/live_check.py`로 4개 도메인 모두 실제 호출 성공
  - 공공 API가 가끔 15초 타임아웃(KOSIS·에어코리아), 클라우드 세션에선 프록시 재시작으로 DART 다운로드가 끊긴 적 있음 → 재실행하면 성공. 원래 에이전트의 타임아웃 값 그대로 둠
  - DART 고유번호는 첫 `dart_resolve_company` 때 약 0.7MB 받아 `~/.cache/korea-data-mcp/`에 저장
- [x] Claude ↔ MCP 끝까지: "카카오 최근 3년 매출 추이" → 도구 7회, corp-agent와 같은 답·접수번호, Haiku 5.5 $0.0043
- 키 환경변수: `KOSIS_API_KEY`, `OPENDART_API_KEY`, `DATA_GO_KR_SERVICE_KEY` (+ Claude 확인용 `ANTHROPIC_API_KEY` 또는 `TREND_ANTHROPIC_API_KEY`)
- [ ] 2차: 원격 서버(Streamable HTTP) — 키 한도·남용 방지·호스팅 결정 후
- [ ] showcase 카드 (모음집 `janghoyeol/showcase`, main 푸시 = 배포. MCP는 웹 화면이 없으니 Claude 대화 화면 캡처를 '작동 예시'로)
- [ ] 그다음: 프로젝트 A (가상 고객사 사내 데이터 에이전트: NL2SQL + 문서 RAG + 권한 + 승인 흐름 + Docker/CI/관측)
