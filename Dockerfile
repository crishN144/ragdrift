FROM python:3.11-slim

WORKDIR /app

# Install base dependencies
COPY pyproject.toml ./
COPY README.md ./
RUN pip install --no-cache-dir -e ".[api]"

# Copy source
COPY ragdrift/ ./ragdrift/
COPY demo/ ./demo/

EXPOSE 8000

CMD ["uvicorn", "ragdrift.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
