FROM python:3.13-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY stepchaser stepchaser
COPY kb kb
COPY desk desk
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "stepchaser.desk"]
