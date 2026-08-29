FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml app.py ./
RUN pip install --no-cache-dir "psycopg[binary]>=3.2"
ENV PORT=8080
EXPOSE 8080
CMD ["python", "app.py"]
