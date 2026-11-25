FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot ./bot
COPY funnel.json .
VOLUME /app/data
ENV DATABASE_URL=sqlite+aiosqlite:////app/data/leads.db
EXPOSE 8080
CMD ["python", "-m", "bot"]
