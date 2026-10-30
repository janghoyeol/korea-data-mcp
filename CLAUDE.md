# korea-data-mcp — 프로젝트 브리핑

- 개발자: 장호열 (`janghoyeol`). FDE 포트폴리오 보강 프로젝트 B (A: 가상 고객사 사내 데이터 에이전트, C: 실제 고객 1곳 도입은 이후).
- 협업 방식·원칙은 trend-agent CLAUDE.md와 같음: 설계 선택지 먼저 합의, ADR 기록, 테스트 먼저, "됐다" 전에 실제 키로 라이브 확인, 키는 환경변수로만.
- 구조: stat/corp/air/realty-agent의 도구 16개를 복사(VENDORED.md)해 저수준 MCP `Server`로 노출 (ADR 0001). SDK는 `mcp` 2.x (`FastMCP` → `MCPServer`로 이름 바뀜. 여기서는 원문 스키마를 그대로 쓰려고 저수준 `Server` 사용).

## 진행 상황
- [x] 16개 도구 MCP 노출, 키 있는 도메인만 켜짐, 원문 스키마 보존 테스트, 오프라인 테스트 11개
- [x] stdio 실제 실행 확인, Claude(Haiku 5.5)가 MCP 도구를 골라 계산 (`scripts/claude_check.py`, $0.0004)
- [ ] 공공 API 키로 `scripts/live_check.py` — 환경변수 `KOSIS_API_KEY`, `OPENDART_API_KEY`, `DATA_GO_KR_SERVICE_KEY` 필요
- [ ] 2차: 원격 서버(Streamable HTTP) — 키 한도·남용 방지·호스팅 결정 후
- [ ] showcase 카드
