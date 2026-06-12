#!/usr/bin/env bash
#
# AgentCore 풀 셋업 자동화 스크립트.
#
# 동작:
#   1. ECR 리포지토리 (없으면 생성)
#   2. Docker 이미지 빌드 (linux/arm64) + ECR push
#   3. IAM Role + 인라인 정책 (없으면 생성, 있으면 정책만 업데이트)
#   4. AgentCore Runtime (없으면 생성, 있으면 새 이미지로 업데이트)
#
# 환경변수:
#   AWS_REGION         (기본: ~/.aws/config 의 region)
#   ECR_REPO           (기본: eugene-investment-agent)
#   AGENT_NAME         (기본: eugeneInvestmentReportAgent)
#   IAM_ROLE_NAME      (기본: AgentCoreExecutionRole)
#
# 사용법:
#   cd backend && ./deploy.sh

set -euo pipefail

REGION="${AWS_REGION:-${AWS_DEFAULT_REGION:-$(aws configure get region)}}"
if [[ -z "${REGION}" ]]; then
  echo "❌ AWS 리전을 결정할 수 없습니다. AWS_REGION 환경변수 또는 'aws configure'로 설정하세요." >&2
  exit 1
fi

ACCOUNT_ID="$(aws sts get-caller-identity --query Account --output text)"
ECR_REPO="${ECR_REPO:-eugene-investment-agent}"
AGENT_NAME="${AGENT_NAME:-eugeneInvestmentReportAgent}"
IAM_ROLE_NAME="${IAM_ROLE_NAME:-AgentCoreExecutionRole}"
IMAGE_TAG="latest"
IMAGE_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}:${IMAGE_TAG}"
ROLE_ARN="arn:aws:iam::${ACCOUNT_ID}:role/${IAM_ROLE_NAME}"

echo "═══════════════════════════════════════════════════════════"
echo "AgentCore 배포 시작"
echo "  계정:     ${ACCOUNT_ID}"
echo "  리전:     ${REGION}"
echo "  ECR 리포: ${ECR_REPO}"
echo "  에이전트: ${AGENT_NAME}"
echo "  IAM Role: ${IAM_ROLE_NAME}"
echo "═══════════════════════════════════════════════════════════"

# ── 1. ECR 리포지토리 ────────────────────────────────────────
echo ""
echo "[1/4] ECR 리포지토리 확인..."
if aws ecr describe-repositories --repository-names "${ECR_REPO}" --region "${REGION}" >/dev/null 2>&1; then
  echo "  ✓ 이미 존재: ${ECR_REPO}"
else
  echo "  + 생성: ${ECR_REPO}"
  aws ecr create-repository --repository-name "${ECR_REPO}" --region "${REGION}" >/dev/null
fi

# ── 2. Docker 빌드 + push ────────────────────────────────────
echo ""
echo "[2/4] Docker 빌드 + ECR push..."
aws ecr get-login-password --region "${REGION}" \
  | docker login --username AWS --password-stdin "${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com" >/dev/null

docker build --platform linux/arm64 -t "${ECR_REPO}:${IMAGE_TAG}" .
docker tag "${ECR_REPO}:${IMAGE_TAG}" "${IMAGE_URI}"
docker push "${IMAGE_URI}"
echo "  ✓ 푸시 완료: ${IMAGE_URI}"

# ── 3. IAM Role ──────────────────────────────────────────────
echo ""
echo "[3/4] IAM Role 확인..."
TRUST_POLICY=$(cat <<EOF
{"Version":"2012-10-17","Statement":[{"Effect":"Allow","Principal":{"Service":"bedrock-agentcore.amazonaws.com"},"Action":"sts:AssumeRole"}]}
EOF
)

INLINE_POLICY=$(cat <<EOF
{"Version":"2012-10-17","Statement":[
  {"Effect":"Allow","Action":["bedrock:InvokeModel","bedrock:InvokeModelWithResponseStream"],
   "Resource":["arn:aws:bedrock:*::foundation-model/anthropic.*","arn:aws:bedrock:*:${ACCOUNT_ID}:inference-profile/*"]},
  {"Effect":"Allow","Action":["ecr:GetDownloadUrlForLayer","ecr:BatchGetImage","ecr:GetAuthorizationToken"],"Resource":"*"},
  {"Effect":"Allow","Action":["logs:CreateLogGroup","logs:CreateLogStream","logs:PutLogEvents"],"Resource":"*"}
]}
EOF
)

if aws iam get-role --role-name "${IAM_ROLE_NAME}" >/dev/null 2>&1; then
  echo "  ✓ Role 존재: ${IAM_ROLE_NAME}"
else
  echo "  + Role 생성: ${IAM_ROLE_NAME}"
  aws iam create-role \
    --role-name "${IAM_ROLE_NAME}" \
    --assume-role-policy-document "${TRUST_POLICY}" >/dev/null
fi

echo "  + 인라인 정책 업데이트"
aws iam put-role-policy \
  --role-name "${IAM_ROLE_NAME}" \
  --policy-name AgentCorePermissions \
  --policy-document "${INLINE_POLICY}"

# IAM Role propagation 대기 (신규 생성 시)
sleep 8

# ── 4. AgentCore Runtime ────────────────────────────────────
echo ""
echo "[4/4] AgentCore Runtime 확인..."
RUNTIME_ID=$(aws bedrock-agentcore-control list-agent-runtimes --region "${REGION}" \
  --query "agentRuntimes[?agentRuntimeName=='${AGENT_NAME}'].agentRuntimeId | [0]" --output text 2>/dev/null || echo "")

ARTIFACT_JSON='{"containerConfiguration":{"containerUri":"'"${IMAGE_URI}"'"}}'
NETWORK_JSON='{"networkMode":"PUBLIC"}'

if [[ -z "${RUNTIME_ID}" || "${RUNTIME_ID}" == "None" ]]; then
  echo "  + Runtime 생성: ${AGENT_NAME}"
  CREATE_OUT=$(aws bedrock-agentcore-control create-agent-runtime \
    --region "${REGION}" \
    --agent-runtime-name "${AGENT_NAME}" \
    --agent-runtime-artifact "${ARTIFACT_JSON}" \
    --network-configuration "${NETWORK_JSON}" \
    --role-arn "${ROLE_ARN}")
  RUNTIME_ID=$(echo "${CREATE_OUT}" | grep -o '"agentRuntimeId": "[^"]*' | cut -d'"' -f4)
else
  echo "  ✓ Runtime 존재: ${RUNTIME_ID}"
  echo "  + 새 이미지로 업데이트"
  aws bedrock-agentcore-control update-agent-runtime \
    --region "${REGION}" \
    --agent-runtime-id "${RUNTIME_ID}" \
    --agent-runtime-artifact "${ARTIFACT_JSON}" \
    --network-configuration "${NETWORK_JSON}" \
    --role-arn "${ROLE_ARN}" >/dev/null
fi

# READY 대기
echo "  · READY 상태 대기..."
for i in $(seq 1 60); do
  STATUS=$(aws bedrock-agentcore-control get-agent-runtime \
    --agent-runtime-id "${RUNTIME_ID}" --region "${REGION}" \
    --query 'status' --output text)
  echo "    [$(date +%H:%M:%S)] ${STATUS}"
  if [[ "${STATUS}" == "READY" ]]; then break; fi
  if [[ "${STATUS}" == *FAILED* ]]; then
    echo "  ❌ 배포 실패. CloudWatch 로그를 확인하세요." >&2
    exit 1
  fi
  sleep 10
done

RUNTIME_ARN="arn:aws:bedrock-agentcore:${REGION}:${ACCOUNT_ID}:runtime/${RUNTIME_ID}"

echo ""
echo "═══════════════════════════════════════════════════════════"
echo "✅ 배포 완료"
echo "  Runtime ID:  ${RUNTIME_ID}"
echo "  Runtime ARN: ${RUNTIME_ARN}"
echo ""
echo "프론트엔드 실행 전 환경변수로 설정:"
echo "  export AGENTCORE_RUNTIME_ARN=\"${RUNTIME_ARN}\""
echo "═══════════════════════════════════════════════════════════"
