# 方案 A：kagent BYO Harness 镜像
# 构建后必须推送并取 sha256 digest 填入 Harness（CRD 强制 digest 锁定，
# tag 会被 CEL 校验拒绝）：
#   docker build -t <registry>/news-odagent:0.1.0 .
#   docker push <registry>/news-odagent:0.1.0
#   docker buildx imagetools inspect <registry>/news-odagent:0.1.0
#   # 取 "sha256:..." 填入 deploy/kagent/harness.yaml 的 workload.image
FROM python:3.12-slim

# 容器内强制 UTF-8（等价 python -X utf8，避免 Windows/容器编码差异）
ENV PYTHONUTF8=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖（利用层缓存），再拷贝源码
COPY pyproject.toml README.md ./
COPY src/ src/
COPY config/ config/

# kagent: kagent-crewai + uvicorn；nacos: 远程模型配置 + 服务注册
RUN pip install --no-cache-dir -e ".[kagent,nacos]"

# 输出目录（news_push.json/md/txt 生成于此；容器内为 ephemeral，
# 持久化需挂 PVC 或依赖 A2A 响应返回结果）
RUN mkdir -p /app/output

EXPOSE 8080

# BYO Harness CRD 要求 workload.command 必填，与此处保持一致
CMD ["python", "-m", "news_crew.server"]
