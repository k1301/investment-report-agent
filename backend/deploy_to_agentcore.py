"""
AgentCore에 에이전트를 배포하는 스크립트.

사전 조건:
1. Docker 이미지를 ECR에 푸시 완료
2. IAM Role 생성 완료 (Bedrock + ECR 접근 권한)

사용법:
    python deploy_to_agentcore.py
"""

import boto3
import os
import time

AWS_REGION = os.getenv("AWS_REGION") or os.getenv("AWS_DEFAULT_REGION") or boto3.session.Session().region_name
AWS_ACCOUNT_ID = os.getenv("AWS_ACCOUNT_ID", "123456789012")
ECR_REPO = os.getenv("ECR_REPO", "eugene-investment-agent")
ROLE_ARN = os.getenv("AGENTCORE_ROLE_ARN",
                     f"arn:aws:iam::{AWS_ACCOUNT_ID}:role/AgentCoreExecutionRole")


def deploy():
    """AgentCore Runtime을 생성합니다."""

    # Control Plane 클라이언트
    control_client = boto3.client(
        "bedrock-agentcore-control",
        region_name=AWS_REGION
    )

    container_uri = (
        f"{AWS_ACCOUNT_ID}.dkr.ecr.{AWS_REGION}.amazonaws.com/"
        f"{ECR_REPO}:latest"
    )

    print(f"배포 시작...")
    print(f"  리전: {AWS_REGION}")
    print(f"  컨테이너: {container_uri}")
    print(f"  역할: {ROLE_ARN}")

    # AgentCore Runtime 생성
    response = control_client.create_agent_runtime(
        agentRuntimeName="eugene-investment-report-agent",
        agentRuntimeArtifact={
            "containerConfiguration": {
                "containerUri": container_uri
            }
        },
        networkConfiguration={"networkMode": "PUBLIC"},
        roleArn=ROLE_ARN,
        lifecycleConfiguration={
            "idleRuntimeSessionTimeout": 300,
            "maxLifetime": 1800
        },
        description="투자분석서 생성 에이전트 - 비정형 인풋을 정형화된 보고서로 변환"
    )

    agent_runtime_arn = response["agentRuntimeArn"]
    status = response["status"]

    print(f"\n✓ Runtime 생성 완료")
    print(f"  ARN: {agent_runtime_arn}")
    print(f"  상태: {status}")

    # 상태가 ACTIVE가 될 때까지 대기
    print("\n배포 상태 확인 중...")
    while status not in ("ACTIVE", "FAILED"):
        time.sleep(10)
        desc = control_client.get_agent_runtime(
            agentRuntimeId=agent_runtime_arn.split("/")[-1]
        )
        status = desc["status"]
        print(f"  상태: {status}")

    if status == "ACTIVE":
        print(f"\n✓ 배포 성공!")
        print(f"  .env에 아래를 추가하세요:")
        print(f"  AGENTCORE_RUNTIME_ARN={agent_runtime_arn}")
    else:
        print(f"\n✗ 배포 실패. AWS 콘솔에서 로그를 확인하세요.")

    return agent_runtime_arn


if __name__ == "__main__":
    deploy()
