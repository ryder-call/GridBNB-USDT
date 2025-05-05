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

# 添加调试命令查看文件是否存在
RUN ls -la /app

# 设置默认启动命令，先检查文件是否存在
CMD ["/bin/bash", "-c", "ls -la /app && echo '检查main.py是否存在' && (test -f /app/main.py && echo 'main.py存在' || echo 'main.py不存在') && python main.py"]
