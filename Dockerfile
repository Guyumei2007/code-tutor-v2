# 免费平台（Render / Railway / Zeabur 等）部署用镜像
# 平台会注入 PORT 环境变量，server.py 会自动读取
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HOST=0.0.0.0 \
    NO_BROWSER=1 \
    DATA_DIR=/app/data

# gcc + libc 头文件：在线编译并运行用户提交的 C 代码
RUN apt-get update \
 && apt-get install -y --no-install-recommends gcc libc6-dev \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . .

# 用非 root 用户跑服务：容器里要执行陌生人的代码，权限越小越好
RUN useradd --create-home --uid 1000 runner \
 && mkdir -p /app/data /app/workspace \
 && chown -R runner:runner /app

USER runner

EXPOSE 7860

CMD ["python", "server.py"]
