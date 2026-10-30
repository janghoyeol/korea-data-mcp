# 가져온 코드 (vendored)

도구 코드는 각 에이전트 레포에서 **복사**해 왔다 (ADR 0001). 원본을 고치면 여기도 맞춰야 한다.

| 폴더 | 원본 레포 | 커밋 | 가져온 파일 |
|---|---|---|---|
| `src/korea_data_mcp/kosis/` | janghoyeol/stat-agent | `bf3edb7` | src/stat_agent/kosis_client.py, tools.py |
| `src/korea_data_mcp/dart/` | janghoyeol/corp-agent | `08efc4a` | src/corp_agent/dart_client.py, corp_codes.py, tools.py, agent.py의 TOOL_DEFINITIONS → dart/schemas.py |
| `src/korea_data_mcp/air/` | janghoyeol/air-agent | `d95b7d2` | src/air_agent/airkorea_client.py, kma_client.py, regions.py, tools.py |
| `src/korea_data_mcp/realty/` | janghoyeol/realty-agent | `bd31d73` | src/realty_agent/molit_client.py, lawd.py, tools.py, data/lawd_codes.tsv |

원본에서 바꾼 것:

- `realty/lawd.py`: `DATA_PATH`를 레포 루트 `data/`에서 패키지 안 `realty/data/`로 (설치형 패키지라서)
- `dart/adapter.py`(새 파일): corp-agent의 `CorpAgent._dispatch`를 옮김. 고유번호 TSV는 미리 받는 대신 첫 호출 때 받아 `~/.cache/korea-data-mcp/`에 1주일 캐시
- 도구 이름에 도메인 접두어(`kosis_` 등). 설명문 안의 같은 도메인 도구 이름도 같이 바뀜 (`registry.py`, 테스트로 그 외 변경 없음을 확인)
