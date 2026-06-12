# Investment Report Generator

비정형 투자 자료(PDF, 이메일, CSV, 텍스트)를 업로드하면, AI 에이전트가 정형화된 투자분석서(6개 섹션)를 자동 생성하는 시스템입니다.

운용사(GP)마다 양식이 다른 자료를 하나의 통일된 형식으로 변환합니다.

## 작동 방식

```
파일 업로드 (Streamlit 웹 UI)
    │
    ▼
문서 파싱 (Amazon Textract OCR/표 인식 + 이메일/CSV/텍스트 직접 파싱)
    │
    ▼
AI 에이전트 (Strands Agent SDK → Bedrock AgentCore에서 실행)
    │  └ 모델은 정형 JSON만 반환 (스키마 고정)
    ▼
서버 측 Jinja2 템플릿 렌더링 (모든 펀드가 동일한 HTML 골격)
    │
    ▼
정형 투자분석서 출력 (HTML 다운로드)
```

**출력 구조 (어떤 인풋이든 동일):**
1. 펀드개요
2. 조합원명부
3. 출자현황
4. 회수현황
5. 배당금/수익현황
6. 코멘트 (분기별 업데이트)

> **성과 요약의 "기준일"이란?**
> 상단 성과 요약 테이블(NAV / TVPI / IRR / 기준일)의 기준일은 시스템이 임의로 정한 날짜가 아니라, **운용사(GP)가 보낸 정기보고서에 명시된 평가 기준일**을 그대로 가져옵니다. 분기 정기보고서는 일반적으로 분기말(예: Q1 → 3/31, Q2 → 6/30) 기준으로 NAV·TVPI·IRR을 산출하므로, 같은 분기 보고서를 입력하면 운용사가 다르더라도 동일한 분기말 날짜가 표시됩니다. 입력 자료에 기준일이 없으면 빈 칸으로 둡니다.

## 아키텍처

```
+-----------------------------+        +-------------------------------------+
|  Streamlit Frontend         |        |        Bedrock AgentCore            |
|  (frontend/app.py)          |        |  +-------------------------------+  |
|                             | invoke |  |  Docker Container (backend/)  |  |
|  - File upload              | -----> |  |   Strands Agent SDK           |  |
|  - Document parsing         | agent  |  |    + Bedrock Claude  (JSON)   |  |
|  - Result display/download  | runtime|  |   Jinja2  ->  HTML render     |  |
+-----------------------------+        |  +-------------------------------+  |
                                       +-------------------------------------+
```

| 구성 요소 | 역할 |
|-----------|------|
| Streamlit | 파일 업로드 UI + 문서 파싱(Textract) + AgentCore 호출 + 결과 표시·다운로드 |
| Bedrock AgentCore | Strands Agent 컨테이너를 관리형 인프라에서 실행 (자동 스케일링, 세션 관리) |
| Strands Agent SDK | 모델 호출 + 시스템 프롬프트 + Bedrock 클라이언트 추상화 |
| Amazon Bedrock (Claude Sonnet 4.5) | LLM — 비정형 텍스트를 정형 JSON으로 변환 |
| Jinja2 | JSON → 동일 골격의 HTML 보고서 렌더링 (AgentCore 컨테이너 내) |
| Amazon Textract | PDF OCR + 표 인식 |

## 사전 준비

- Python 3.11+
- Docker (AgentCore 배포 시)
- AWS CLI 설정 완료 (`aws configure`)
- AWS 계정 권한: ECR, Bedrock AgentCore, Bedrock(모델 접근), Textract, IAM

## 1단계 — AgentCore 배포

`backend/deploy.sh` 한 번에 ECR 리포지토리, Docker 이미지 빌드/푸시, IAM Role/정책, AgentCore Runtime을 모두 생성합니다. 같은 스크립트를 다시 실행하면 기존 리소스를 그대로 두고 새 이미지로 Runtime만 업데이트합니다 (멱등성).

```bash
cd backend
./deploy.sh
```

종료 시 출력되는 `Runtime ARN`을 메모해 둡니다 (다음 단계에서 사용).

| 환경변수 | 기본값 |
|------|--------|
| `AWS_REGION` | `~/.aws/config` 의 region |
| `ECR_REPO` | `eugene-investment-agent` |
| `AGENT_NAME` | `eugeneInvestmentReportAgent` |
| `IAM_ROLE_NAME` | `AgentCoreExecutionRole` |

> 사용할 Bedrock 모델 ID가 `backend/agent_server.py`의 `BedrockModel(model_id=...)`와 일치해야 합니다 (현재 `us.anthropic.claude-sonnet-4-5-20250929-v1:0`).

## 2단계 — 프론트엔드 실행

**의존성 설치**

```bash
cd frontend
pip install -r requirements.txt
```

**AgentCore Runtime ARN 환경변수 설정**

1단계에서 출력된 ARN을 환경변수로 설정합니다.

```bash
# Linux / macOS
export AGENTCORE_RUNTIME_ARN="arn:aws:bedrock-agentcore:<REGION>:<ACCOUNT_ID>:runtime/<RUNTIME_ID>"
```

```powershell
# Windows (PowerShell)
$env:AGENTCORE_RUNTIME_ARN = "arn:aws:bedrock-agentcore:<REGION>:<ACCOUNT_ID>:runtime/<RUNTIME_ID>"
```

**Streamlit 실행**

```bash
streamlit run app.py
```

브라우저에서 `http://localhost:8501` 접속 → 파일 업로드 → 투자분석서 생성 + HTML 다운로드.

### 사내 네트워크에서 접근하기

`localhost:8501`은 실행한 PC 본인만 접속할 수 있습니다. 같은 사내망의 다른 사람이 접속하게 하려면 모든 네트워크 인터페이스에 바인딩하세요.

```bash
streamlit run app.py --server.address 0.0.0.0 --server.port 8501
```

같은 사내망 사용자는 출력된 `Network URL`(`http://<머신_IP>:8501`)로 접속합니다. (방화벽에서 8501 포트 허용 필요)

## 프로젝트 구조

```
├── frontend/                   # Streamlit 프론트엔드
│   ├── app.py                 # 메인 앱 (파일 업로드 + 파싱 + AgentCore 호출 + 결과 표시)
│   └── requirements.txt
│
├── backend/                    # Strands Agent + AgentCore 컨테이너
│   ├── agent_server.py        # 에이전트 서버 (POST /invocations, GET /ping)
│   ├── template.html.j2       # Jinja2 HTML 템플릿 (모든 펀드 동일 골격)
│   ├── Dockerfile             # 컨테이너 이미지 정의 (linux/arm64)
│   ├── requirements.txt
│   ├── deploy.sh              # 풀 셋업 스크립트 (ECR + IAM + Runtime 한 번에)
│   └── deploy_to_agentcore.py # Python 기반 Runtime 생성 스크립트
│
└── data/raw_inputs/            # 샘플 데이터 (12개)
    ├── skyline/               # 블라인드 펀드 (.pdf, .eml, .txt)
    ├── pacific/               # 해외 펀드 - 영문 (.eml, .txt)
    ├── mirae/                 # 부동산 펀드 (.pdf, .eml)
    ├── ds_secondary/          # 세컨더리 (.csv, .eml)
    ├── nh_bio/                # 바이오 (.eml)
    ├── hanyang/               # 인프라 (.eml, .txt)
    ├── asiana/                # 상장주식 (.txt)
    ├── golden_bridge/         # 해외 테크 (영문, .txt)
    ├── nordic_infra/          # 해외 인프라 (영문, .txt)
    ├── gangnam_reits/         # 부동산 (한국어, .txt)
    ├── bluepoint_venture/     # 벤처/AI (한국어, .txt)
    └── asia_mezzanine/        # 메자닌/사모대출 (영문, .txt)
```

## 주요 코드 설명

### `frontend/app.py` — Streamlit 프론트엔드

| 함수 | 역할 |
|------|------|
| `parse_eml()` / `parse_txt()` / `parse_csv()` / `parse_pdf_with_textract()` | 파일 형식별 텍스트 추출 |
| `invoke_agentcore()` | AgentCore Runtime 호출 (`invoke_agent_runtime` API) |
| `wrap_html()` | A4 스타일 HTML 래핑 (여백, 폰트, 테이블 스타일) — 다운로드용 |

### `backend/agent_server.py` — Strands Agent + AgentCore 컨테이너

| 엔드포인트 | 역할 |
|-----------|------|
| `POST /invocations` | AgentCore가 호출하는 메인 엔드포인트. Strands Agent 실행 후 Jinja2 렌더링 |
| `GET /ping` | 헬스체크 |

처리 흐름:
1. `get_agent()` — Strands `Agent` lazy 초기화 (`BedrockModel`로 Claude Sonnet 4.5 호출)
2. 모델은 정형 JSON만 반환 (SYSTEM_PROMPT에 스키마 명시)
3. `extract_json()` — 모델 출력에서 JSON 객체 추출 (중괄호 균형 추적)
4. `render_html()` — `template.html.j2`에 데이터 끼워넣어 HTML 생성

### `backend/template.html.j2` — Jinja2 HTML 템플릿

모든 펀드가 동일한 HTML 골격으로 출력되도록 보장합니다:
- 상단 성과 요약 표 (NAV / TVPI / Net IRR / 기준일) — 가로 1행 고정
- 6개 섹션 (1~5는 테이블, 6번 코멘트는 분기별 헤딩 + bullet + 단락)
- 빈 데이터는 "해당 없음" / "-" fallback

## 지원 파일 형식

| 형식 | 확장자 | 파싱 방식 |
|------|--------|-----------|
| PDF | `.pdf` | Amazon Textract (OCR + 표 인식) |
| 이메일 | `.eml` | Python email 파서 (발신자/제목/본문 분리) |
| CSV | `.csv` | 직접 파싱 |
| 텍스트 | `.txt` | 직접 읽기 |

## 기술 스택

- **에이전트 프레임워크**: [Strands Agent SDK](https://github.com/strands-agents/sdk-python) (AWS 오픈소스)
- **에이전트 호스팅**: Amazon Bedrock AgentCore (관리형 런타임)
- **AI 모델**: Amazon Bedrock (Claude Sonnet 4.5)
- **문서 파싱**: Amazon Textract
- **프론트엔드**: Streamlit
- **HTML 렌더링**: Jinja2 (서버 측 템플릿)
- **컨테이너**: Docker + Amazon ECR

## 환경 변수

| 변수 | 사용처 | 설명 |
|------|--------|------|
| `AWS_REGION` | 프론트엔드 / 배포 스크립트 | AWS 리전. 미설정 시 `~/.aws/config`의 region 사용 |
| `AGENTCORE_RUNTIME_ARN` | Streamlit(`frontend/app.py`) | AgentCore Runtime ARN (1단계 배포 출력값) |
