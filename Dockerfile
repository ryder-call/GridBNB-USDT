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

# 创建备份目录
RUN cp -r /app /app_backup

# 创建启动脚本
RUN echo '#!/bin/bash\nif [ ! -f /app/main.py ]; then\n  echo "检测到main.py不存在，正在从备份恢复..."\n  cp -r /app_backup/* /app/\n  echo "文件已恢复"\nfi\npython /app/main.py' > /app/entrypoint.sh && chmod +x /app/entrypoint.sh

# 设置默认启动命令
ENTRYPOINT ["/app/entrypoint.sh"]
