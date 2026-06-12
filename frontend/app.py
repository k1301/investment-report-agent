"""
투자분석서 자동 생성 시스템 - Streamlit Frontend

파일 업로드 → 문서 파싱 → AgentCore 호출 → 투자분석서 출력 + HTML 다운로드
"""

import streamlit as st
import boto3
import json
import os
import uuid
from pathlib import Path
from datetime import datetime

# --- 설정 ---
AWS_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION")
AGENTCORE_RUNTIME_ARN = os.getenv("AGENTCORE_RUNTIME_ARN")

st.set_page_config(
    page_title="투자분석서 자동 생성",
    page_icon="📊",
    layout="wide"
)

# --- 문서 파싱 ---

def parse_eml(file_bytes, file_name):
    """이메일(.eml) 파일 파싱"""
    import email
    from email import policy

    msg = email.message_from_bytes(file_bytes, policy=policy.default)
    sender = str(msg.get("From", ""))
    subject = str(msg.get("Subject", ""))
    date = str(msg.get("Date", ""))

    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                body = part.get_content()
                break
    else:
        body = msg.get_content()

    return {
        "file_name": file_name,
        "file_type": ".eml",
        "extracted_text": body,
        "metadata": {"sender": sender, "subject": subject, "date": date}
    }


def parse_txt(file_bytes, file_name):
    """텍스트(.txt) 파일 파싱"""
    text = file_bytes.decode("utf-8", errors="replace")
    return {
        "file_name": file_name,
        "file_type": ".txt",
        "extracted_text": text,
        "metadata": {}
    }


def parse_csv(file_bytes, file_name):
    """CSV 파일 파싱"""
    text = file_bytes.decode("utf-8", errors="replace")
    return {
        "file_name": file_name,
        "file_type": ".csv",
        "extracted_text": text,
        "metadata": {}
    }


def parse_pdf_with_textract(file_bytes, file_name):
    """PDF를 Textract로 파싱"""
    try:
        textract = boto3.client("textract", region_name=AWS_REGION)
        response = textract.analyze_document(
            Document={"Bytes": file_bytes},
            FeatureTypes=["TABLES", "FORMS"]
        )
        text_blocks = []
        for block in response.get("Blocks", []):
            if block["BlockType"] == "LINE":
                text_blocks.append(block.get("Text", ""))
        extracted_text = "\n".join(text_blocks)
    except Exception as e:
        extracted_text = f"[PDF 파싱 실패: {str(e)}]"

    return {
        "file_name": file_name,
        "file_type": ".pdf",
        "extracted_text": extracted_text,
        "metadata": {}
    }


def parse_file(file_bytes, file_name):
    """파일 확장자에 따라 적절한 파서 호출"""
    ext = Path(file_name).suffix.lower()
    if ext == ".eml":
        return parse_eml(file_bytes, file_name)
    elif ext == ".txt":
        return parse_txt(file_bytes, file_name)
    elif ext == ".csv":
        return parse_csv(file_bytes, file_name)
    elif ext == ".pdf":
        return parse_pdf_with_textract(file_bytes, file_name)
    else:
        text = file_bytes.decode("utf-8", errors="replace")
        return {
            "file_name": file_name,
            "file_type": ext,
            "extracted_text": text,
            "metadata": {}
        }


# --- AgentCore 호출 ---

def invoke_agentcore(parsed_documents, fund_name=""):
    """AgentCore에 배포된 에이전트를 호출하여 투자분석서 생성"""

    if not AGENTCORE_RUNTIME_ARN:
        raise RuntimeError(
            "AGENTCORE_RUNTIME_ARN 환경변수가 설정되지 않았습니다. "
            "backend/deploy.sh 출력값을 환경변수로 export 한 뒤 다시 실행하세요."
        )

    inputs_text = ""
    for i, doc in enumerate(parsed_documents, 1):
        inputs_text += f"\n---\n## 파일 #{i}: {doc['file_name']} ({doc['file_type']})\n"
        meta = doc.get("metadata", {})
        if meta.get("sender"):
            inputs_text += f"- 발신: {meta['sender']}\n"
        if meta.get("subject"):
            inputs_text += f"- 제목: {meta['subject']}\n"
        if meta.get("date"):
            inputs_text += f"- 일자: {meta['date']}\n"
        inputs_text += f"\n[추출된 내용]\n{doc.get('extracted_text', '')}\n"

    fund_label = f"'{fund_name}' " if fund_name else ""
    prompt = (
        f"아래 {len(parsed_documents)}개 파일에서 추출된 내용을 종합하여 "
        f"{fund_label}투자분석서 데이터를 정리해주세요.\n\n"
        f"# 파싱된 문서 내용\n{inputs_text}"
    )

    payload = json.dumps({"input": {"prompt": prompt}})
    session_id = uuid.uuid4().hex + "x"

    agentcore_client = boto3.client("bedrock-agentcore", region_name=AWS_REGION)

    response = agentcore_client.invoke_agent_runtime(
        agentRuntimeArn=AGENTCORE_RUNTIME_ARN,
        runtimeSessionId=session_id,
        payload=payload,
        qualifier="DEFAULT"
    )

    response_body = response["response"].read()
    response_data = json.loads(response_body)
    report_html = response_data.get("output", {}).get("message", "")

    return report_html


# --- HTML 래핑 ---

def wrap_html(report_html):
    """보고서 HTML을 A4 용지 스타일로 래핑 (다운로드용)"""
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8">
<style>
@page {{ size: A4; margin: 20mm 15mm; }}
body {{
    font-family: 'Apple SD Gothic Neo', 'Malgun Gothic', sans-serif;
    font-size: 11px;
    margin: 0;
    padding: 0;
    background: #f0f0f0;
}}
.page {{
    max-width: 170mm;
    min-height: 297mm;
    margin: 20px auto;
    padding: 25mm 20mm;
    background: white;
    box-shadow: 0 2px 8px rgba(0,0,0,0.15);
    box-sizing: border-box;
}}
@media print {{
    body {{ background: white; }}
    .page {{ margin: 0; box-shadow: none; padding: 20mm; }}
}}
table {{ border-collapse: collapse; width: 100%; margin: 10px 0; table-layout: fixed; }}
th, td {{ border: 1px solid #ccc; padding: 7px 10px; text-align: left; font-size: 10px; word-wrap: break-word; }}
th {{ background: #f5f5f5; font-weight: bold; }}
h1 {{ font-size: 18px; color: #1a1a1a; border-bottom: 2px solid #333; padding-bottom: 8px; margin-top: 0; }}
h2 {{ font-size: 13px; color: #333; margin-top: 22px; border-left: 3px solid #2c5aa0; padding-left: 8px; }}
.summary td {{ font-weight: bold; font-size: 11px; }}
</style></head><body><div class="page">{report_html}</div></body></html>"""


# --- UI ---

st.title("📊 투자분석서 자동 생성")
st.caption("비정형 투자 자료를 업로드하면 AI가 정형화된 투자분석서를 생성합니다.")

st.divider()

col1, col2 = st.columns([1, 2])

with col1:
    st.subheader("파일 업로드")

    fund_name = st.text_input(
        "펀드명 (선택)",
        placeholder="예: 스카이라인 성장펀드 3호"
    )

    if "uploader_key" not in st.session_state:
        st.session_state["uploader_key"] = 0

    uploaded_files = st.file_uploader(
        "투자 자료 업로드",
        type=["pdf", "eml", "txt", "csv"],
        accept_multiple_files=True,
        help="PDF, 이메일(.eml), 텍스트(.txt), CSV 파일을 업로드하세요. 여러 파일 동시 업로드 가능.",
        key=f"file_uploader_{st.session_state['uploader_key']}"
    )

    if uploaded_files:
        st.write(f"**{len(uploaded_files)}개 파일 선택됨:**")
        for f in uploaded_files:
            size_kb = len(f.getvalue()) / 1024
            st.write(f"- {f.name} ({size_kb:.1f} KB)")

    btn_col1, btn_col2 = st.columns(2)
    with btn_col1:
        generate_btn = st.button(
            "투자분석서 생성",
            type="primary",
            disabled=not uploaded_files,
            use_container_width=True
        )
    with btn_col2:
        clear_btn = st.button(
            "전체 삭제",
            use_container_width=True
        )

    if clear_btn:
        st.session_state["uploader_key"] += 1
        if "report_html" in st.session_state:
            del st.session_state["report_html"]
        if "fund_name" in st.session_state:
            del st.session_state["fund_name"]
        st.rerun()

with col2:
    if generate_btn and uploaded_files:
        with st.spinner("분석 중... (1-2분 소요)"):
            # Step 1: 파일 파싱
            st.info("📄 문서 파싱 중...")
            parsed_docs = []
            for f in uploaded_files:
                file_bytes = f.getvalue()
                parsed = parse_file(file_bytes, f.name)
                parsed_docs.append(parsed)

            st.info(f"✅ {len(parsed_docs)}개 파일 파싱 완료. AI 에이전트 호출 중...")

            # Step 2: AgentCore 호출
            try:
                report_html = invoke_agentcore(parsed_docs, fund_name)

                if report_html:
                    st.session_state["report_html"] = report_html
                    st.session_state["fund_name"] = fund_name
                else:
                    st.error("보고서 생성에 실패했습니다.")

            except Exception as e:
                st.error(f"오류 발생: {str(e)}")
                st.caption("AgentCore 연결을 확인하세요.")

    # 결과 표시 (세션에 저장된 보고서가 있으면 항상 표시)
    if "report_html" in st.session_state:
        import re
        report_html = st.session_state["report_html"]
        saved_fund_name = st.session_state.get("fund_name", "")

        st.success("✅ 투자분석서 생성 완료!")

        # HTML에서 펀드명 추출
        detected_name = saved_fund_name
        if not detected_name:
            h1_match = re.search(r'<h1[^>]*>(.*?)</h1>', report_html)
            if h1_match:
                detected_name = h1_match.group(1).replace("투자분석서", "").strip().strip("-").strip(":").strip()
        if not detected_name:
            td_match = re.search(r'<td>펀드명</td>\s*<td>(.*?)</td>', report_html)
            if td_match:
                detected_name = td_match.group(1).strip()
        if not detected_name:
            detected_name = "report"

        file_label = detected_name.replace(" ", "_")

        # 보고서 표시
        st.subheader("생성된 투자분석서")
        styled_html = f"""<style>
table {{ border-collapse: collapse; width: 100%; table-layout: fixed; margin: 12px 0; }}
th, td {{ border: 1px solid #ddd; padding: 8px 10px; text-align: left; font-size: 13px; word-wrap: break-word; }}
th {{ background: #f5f5f5; font-weight: bold; }}
h1 {{ font-size: 20px; border-bottom: 2px solid #333; padding-bottom: 8px; }}
h2 {{ font-size: 16px; color: #333; margin-top: 24px; }}
</style>
{report_html}"""
        st.html(styled_html)

        # 다운로드 버튼
        st.divider()
        st.download_button(
            "📥 HTML 다운로드",
            data=wrap_html(report_html),
            file_name=f"투자분석서_{file_label}_{datetime.now().strftime('%Y%m%d')}.html",
            mime="text/html",
            use_container_width=True
        )

    elif not uploaded_files:
        st.info("👈 왼쪽에서 파일을 업로드하고 '투자분석서 생성' 버튼을 누르세요.")
        st.write("")
        st.write("**지원 파일 형식:**")
        st.write("- 📎 PDF — Textract OCR + 표 인식")
        st.write("- 📧 이메일(.eml) — 발신자/제목/본문 추출")
        st.write("- 📝 텍스트(.txt) — 직접 읽기")
        st.write("- 📊 CSV — 테이블 데이터")
        st.write("")
        st.write("**출력 구조 (항상 동일):**")
        st.write("1. 펀드개요  2. 조합원명부  3. 출자현황")
        st.write("4. 회수현황  5. 배당금/수익현황  6. 코멘트")
