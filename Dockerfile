# 使用 Python 官方镜像
FROM python:3.9-slim

# 设置工作目录
WORKDIR /app

# 设置时区
ENV TZ=Asia/Shanghai

# 更新时区信息
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 复制项目文件到容器
COPY . /app

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt

# 创建备份目录（放在/opt目录下，避免被挂载覆盖）
RUN mkdir -p /opt/app_backup && cp -r /app/* /opt/app_backup/

# 创建启动脚本 - 简化版，主要用于日志和安全检查
RUN echo '#!/bin/bash\n\necho "GridBNB-USDT 启动中..."\n\necho "检查应用文件:"\nls -la /app\n\necho "检查数据目录:"\nif [ -d "/app/data" ]; then\n  ls -la /app/data\nelse\n  echo "警告: 数据目录不存在，将在运行时创建"\nfi\n\nif [ ! -f /app/main.py ]; then\n  echo "警告: main.py不存在，从备份恢复..."\n  cp -r /opt/app_backup/* /app/\n  echo "文件已恢复"\nfi\n\necho "启动应用程序..."\npython /app/main.py' > /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# 设置默认启动命令
ENTRYPOINT ["/app/entrypoint.sh"]
