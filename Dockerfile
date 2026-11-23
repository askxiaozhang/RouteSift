FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AUDITOR_BIND_HOST=0.0.0.0 \
    AUDITOR_PORT=18765 \
    AUDITOR_LAN_PORT=18766

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY analyzer.py collector.py deepseek_analysis.py domain_insights.py \
     mihomo_api.py rule_exporter.py server.py shadowrocket.py \
     shadowrocket_live.py shadowrocket_logs.py storage.py ./
COPY deploy/flclash-override.js ./deploy/flclash-override.js
COPY static ./static

RUN mkdir -p /app/data

EXPOSE 18765 18766

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import os, urllib.request; port = os.environ.get('AUDITOR_PORT', '18765'); urllib.request.urlopen(f'http://127.0.0.1:{port}/api/status', timeout=3)"

CMD ["python", "server.py"]
