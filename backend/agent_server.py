"""
AgentCore에 배포할 Strands Agent 서버.

처리 흐름:
1. 모델은 HTML이 아니라 정형 JSON만 반환 (스키마 고정)
2. 서버가 Jinja2 템플릿(template.html.j2)에 JSON 데이터 렌더링
   → 모든 펀드가 동일한 HTML 골격으로 출력됨
"""

from fastapi import FastAPI, Request
import json
import logging
import re
import traceback
from pathlib import Path
from jinja2 import Environment, FileSystemLoader, select_autoescape

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="투자분석서 에이전트 (AgentCore)", version="2.0.0")

strands_agent = None

TEMPLATE_DIR = Path(__file__).parent
jinja_env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html", "j2"]),
)


SYSTEM_PROMPT = """당신은 사모펀드 LP 포트폴리오 관리 전문가입니다.
비정형 인풋(정기보고서, 이메일, CSV 등)을 받아 아래 스키마의 JSON 하나만 반환합니다.

스키마:
{
  "fund_name": str,
  "summary": {"nav": str, "tvpi": str, "irr": str, "as_of": str},
  "overview": {"fund_name": str, "fund_type": str, "gp": str, "vintage": str,
               "fund_size": str, "our_commitment": str, "fund_period": str,
               "strategy": str, "fee_structure": str, "gp_commitment": str,
               "key_man": str, "custodian": str},
  "lps": [{"name": str, "commitment": str, "ratio": str, "type": str}],
  "lp_total": {"commitment": str, "ratio": str},
  "capital_calls": [{"round": str, "date": str, "call_ratio": str,
                     "total_amount": str, "our_amount": str,
                     "cumulative_ratio": str, "purpose": str}],
  "distributions": [{"round": str, "date": str, "type": str,
                     "total_amount": str, "our_amount": str, "source": str}],
  "dividends": [{"period": str, "date": str, "type": str,
                 "total_amount": str, "our_amount": str, "source": str}],
  "comments": [{"quarter": str, "source": str,
                "events": [str], "narrative": str}]
}

규칙:
- 9개 최상위 키 모두 포함. 값 없으면 "" 또는 [].
- 금액은 억원 단위 (해외펀드는 원본 통화 그대로).
- 입력에 명시된 사실만. 추측·과장·환각 금지.
- comments는 분기 단위로 묶고 최신 분기가 배열 앞.
  narrative는 4~6문장의 분기별 운용 서술 (포트폴리오 회사명 자연스럽게 언급).
  events는 해당 분기의 단발 이벤트(IPO/LOI/M&A/구주매출 등) 한 줄씩.
- 이메일은 본문 내용으로 정기/비정기 판단해서 narrative 또는 events에 반영.
- JSON 외의 텍스트 절대 금지: 인사말·설명·"먼저 ~하겠습니다"·마크다운 ```. 곧바로 '{' 로 시작.
- 한 번에 완성된 JSON 하나만. 자가 수정·재작성·반복 금지.
"""


def get_agent():
    global strands_agent
    if strands_agent is None:
        logger.info("Initializing Strands Agent...")
        try:
            from strands import Agent
            from strands.models import BedrockModel

            model = BedrockModel(
                model_id="us.anthropic.claude-sonnet-4-5-20250929-v1:0",
                temperature=0.2,
                max_tokens=16384,
            )

            strands_agent = Agent(
                model=model,
                system_prompt=SYSTEM_PROMPT,
            )
            logger.info("Strands Agent initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize agent: {e}")
            logger.error(traceback.format_exc())
            raise
    return strands_agent


def extract_json(text: str) -> dict:
    """모델 출력에서 JSON 객체만 안전하게 추출.

    중괄호 균형을 추적하며 가장 큰 최상위 객체를 찾습니다.
    여러 객체가 섞여 있으면 가장 긴 것을 선택.
    """
    text = text.strip()
    text = re.sub(r"```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```", "", text)

    candidates = []
    depth = 0
    start = -1
    in_string = False
    escape = False
    for i, ch in enumerate(text):
        if escape:
            escape = False
            continue
        if ch == "\\":
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0 and start != -1:
                candidates.append(text[start:i + 1])
                start = -1

    if not candidates:
        raise ValueError(f"No JSON object found in model output (length={len(text)})")

    candidates.sort(key=len, reverse=True)
    last_err = None
    for cand in candidates:
        try:
            return json.loads(cand)
        except json.JSONDecodeError as e:
            last_err = e
    raise last_err


def normalize(data: dict) -> dict:
    """누락된 키를 빈 값으로 채워 템플릿이 안전하게 렌더링되도록 정규화."""
    return {
        "fund_name": data.get("fund_name", "") or "",
        "summary": data.get("summary") or {},
        "overview": data.get("overview") or {},
        "lps": data.get("lps") or [],
        "lp_total": data.get("lp_total") or {},
        "capital_calls": data.get("capital_calls") or [],
        "distributions": data.get("distributions") or [],
        "dividends": data.get("dividends") or [],
        "comments": data.get("comments") or [],
    }


def render_html(data: dict) -> str:
    """JSON 데이터를 Jinja2 템플릿으로 HTML 렌더링."""
    template = jinja_env.get_template("template.html.j2")
    return template.render(**normalize(data)).strip()


@app.post("/invocations")
async def invoke_agent(request: Request):
    """AgentCore가 호출하는 메인 엔드포인트."""
    try:
        body = await request.body()
        logger.info(f"Received invocation, body length: {len(body)}")

        data = json.loads(body)
        prompt = data.get("input", {}).get("prompt", "")

        if not prompt:
            return {"output": {"message": "Error: No prompt provided"}}

        logger.info(f"Invoking agent with prompt length: {len(prompt)}")
        agent = get_agent()
        result = agent(prompt)

        msg = result.message
        if isinstance(msg, dict):
            content = msg.get("content", [])
            if isinstance(content, list):
                raw = "\n".join(
                    blk.get("text", "") for blk in content
                    if isinstance(blk, dict) and blk.get("text")
                )
            else:
                raw = str(content)
        else:
            raw = str(msg)

        logger.info(f"Model raw output length: {len(raw)}")

        try:
            payload = extract_json(raw)
        except (ValueError, json.JSONDecodeError) as je:
            logger.error(f"JSON parse failed: {je}; raw head: {raw[:300]}")
            return {"output": {"message": f"<p class='error'>모델이 JSON을 반환하지 않았습니다: {je}</p>"}}

        report_html = render_html(payload)
        logger.info(f"Rendered HTML length: {len(report_html)}")
        return {"output": {"message": report_html}}

    except Exception as e:
        logger.error(f"Invocation error: {e}")
        logger.error(traceback.format_exc())
        return {"output": {"message": f"Error: {str(e)}"}}


@app.get("/ping")
async def ping():
    return {"status": "healthy"}
