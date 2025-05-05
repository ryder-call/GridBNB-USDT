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

# 创建启动脚本
RUN echo '#!/bin/bash\n\necho "启动脚本开始执行..."\n\necho "检查当前目录内容:"\nls -la /app\n\necho "检查备份目录内容:"\nls -la /opt/app_backup\n\nif [ ! -f /app/main.py ]; then\n  echo "main.py不存在，正在从备份恢复..."\n  cp -r /opt/app_backup/* /app/\n  echo "恢复后的目录内容:"\n  ls -la /app\nfi\n\nif [ -f /app/main.py ]; then\n  echo "main.py文件存在，开始执行程序"\n  python /app/main.py\nelse\n  echo "错误：恢复后main.py仍然不存在"\n  exit 1\nfi' > /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# 设置默认启动命令
ENTRYPOINT ["/app/entrypoint.sh"]
