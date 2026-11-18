# The Sovereign — 멈추지 않는 연구실

> 주제 하나를 주면, 스스로 조사하고 검증하고 기억하면서 날마다 그 분야의 전문가가 되어 가는 연구 엔진.

[English](README.md) · **한국어** · [웹사이트](https://the-sovereign.pages.dev) · [작동 방식](docs/how-it-works.md) · [모델: 로컬 또는 Claude](docs/models.md) · [문제 해결](docs/troubleshooting.md) · [기여하기](CONTRIBUTING.md)

---

## 무엇인가요

도메인(주제)마다 **지식 그래프**와 **에이전트 팀**이 하나씩 생깁니다.

1. **연구 헌장** — Head 에이전트가 무엇을 알고 싶은지 묻고, 목표·핵심 질문·제외할 것·깊이를 함께 정리합니다.
2. **연구** — 연구원이 웹, 논문(OpenAlex·arXiv·PubMed), 공식 자료를 찾아 읽습니다. robots.txt, 로그인, 유료 장벽은 우회하지 않습니다.
3. **학습** — 읽은 페이지를 사실(fact) 단위로 뽑아 출처와 함께 그래프에 넣습니다. 출처 등급(논문 > 공식 > 뉴스 > 블로그)에 따라 신뢰도를 매기고, 여러 출처가 확인하면 강해지며, 끝내 확인되지 않은 것은 잊습니다.
4. **시험** — 따로 떼어 둔 권위 있는 자료로 만든 시험을 그래프만으로 풀어, 연구실이 실제로 얼마나 알게 됐는지 점수로 봅니다. 틀린 문제는 다음 연구 주제가 됩니다.
5. **답변** — Head에게 물으면 그래프에서 출처와 확신 정도를 붙여 답합니다.

이 과정이 정해진 일정마다(기본: 매일 새벽 3시, 설정 › 학습에서 변경) 반복됩니다. 세상에 행동하지는 않습니다. 메시지 발송, 주문, 예약 같은 건 하지 않고 무엇이 사실인지 알아내서 설명만 합니다.

## 빠른 시작

필요한 것: [Docker](https://docs.docker.com/get-docker/), 그리고 아래 둘 중 하나.

| | 로컬 (기본) | Claude |
|---|---|---|
| 생각하는 모델 | 이 컴퓨터의 [Ollama](https://ollama.com)에서 `qwen3.6:35b` | Anthropic API의 `claude-opus-5-5` |
| 컴퓨터 | 모델용 여유 메모리 ~24 GB (64 GB Apple Silicon Mac이면 넉넉함) | Docker가 잘 돌면 충분 — GPU 불필요 |
| 비용 | 무료 | 토큰당 과금 — [API 키](https://console.anthropic.com) 필요, 콘솔에서 사용 한도 설정 권장 |
| 개인정보 | 검색과 페이지 요청 말고는 컴퓨터 밖으로 나가지 않음 | 읽은 페이지와 질문이 Anthropic으로 전송됨 |

어느 쪽이든 작은 임베딩 모델(~1 GB)은 Ollama에서 돌아갑니다(Anthropic에는 임베딩 API가 없습니다).

```bash
git clone https://github.com/cpldxx/the-sovereign.git && cd the-sovereign
./sovereign up            # 처음이면 로컬/Claude 중 무엇을 쓸지 묻고(키는 가려서 입력), 전부 띄웁니다
./sovereign up --claude   # 묻지 않고 Claude로
```

브라우저에서 **http://localhost:8000**을 열고 첫 계정을 만드세요(관리자가 됩니다). 도메인을 하나 만들면 Head가 헌장을 함께 쓰자고 하고, 동의하면 첫 연구가 시작됩니다.

## 자주 쓰는 명령

```bash
./sovereign up                 # 전부 실행 (Docker)
./sovereign down               # 정지 — 데이터는 ./data와 DB 볼륨에 남음
./sovereign init --claude      # 모델을 Claude로 바꾸기 (--local: 로컬로)
./sovereign up --ollama        # Ollama도 컨테이너로 (Linux, 또는 Claude 사용 시 임베딩만)
./sovereign selfcheck          # 보안 자가 점검
./sovereign test               # 테스트
```

설정은 모두 `.env`에 있습니다(`.env.example` 참고). 다른 기기에서 접속하려면 `SOVEREIGN_BIND=0.0.0.0`으로 열고, 앞에 HTTPS를 두고, `COOKIE_SECURE=true`로 바꾸고, 주소를 `SOVEREIGN_ORIGINS`에 넣으세요.

## 구성

| 부분 | 하는 일 |
|---|---|
| `kg/` | 지식 그래프 API (FastAPI + MCP) — 인제스트, 검색(Graph RAG), 온톨로지, 시험, 학습 |
| `research/` | 연구 서비스 — DeerFlow + SearXNG + crawl4ai, 읽은 페이지를 KG로 |
| `hermes/` | Head 에이전트 (Hermes Agent) — 대화, 팀 지휘, 음성 |
| `frontend/` | 웹 UI — 에이전트 현황, 그래프, Head 채팅, 브리핑, 설정 |
| ArcadeDB | 도메인마다 데이터베이스 하나 (그래프 + 벡터) |

자세한 구조, API, 동작 원리는 [영문 README](README.md)에 있습니다.

## 문제가 생기면

로컬 모델로 밤사이 맥이 멈추거나 서비스가 꺼졌다면 대부분 메모리 문제입니다. 다른 큰 모델과 동시에 돌면 메모리가 부족해집니다. [문제 해결](docs/troubleshooting.md)을 보세요.

## 라이선스

[MIT](LICENSE). 함께 돌아가는 서비스들은 각자의 라이선스를 따릅니다. SearXNG는 AGPL-3.0이며, 수정 없이 별도 컨테이너로 실행됩니다.
