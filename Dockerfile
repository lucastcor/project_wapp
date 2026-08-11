# --------------------------------------------------------------------------
# Estágio 1: bundle do Automation Studio.
#
# O frontend é construído dentro da imagem e o resultado é copiado para
# `app/platform/automations`, de onde o FastAPI o serve na mesma origem. Com
# isso o bundle não precisa ser versionado e a imagem nunca sai com um
# frontend desatualizado em relação ao backend.
# --------------------------------------------------------------------------
FROM node:24-slim AS studio

WORKDIR /studio

# `npm ci` exige o lockfile e instala exatamente as versões travadas.
COPY frontend/automation-studio/package.json frontend/automation-studio/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/automation-studio/ ./
# `tsc --noEmit` roda junto: um erro de tipo quebra a imagem, não a produção.
RUN npm run build


# --------------------------------------------------------------------------
# Estágio 2: aplicação.
# --------------------------------------------------------------------------
FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY docs ./docs
COPY README.md ./

# `vite.config.ts` aponta a saída para `../../app/platform/automations`; a
# partir de /studio isso resolve para /app/platform/automations na imagem de
# build. `tests/test_automation_studio_bundle.py` fixa esse caminho.
COPY --from=studio /app/platform/automations ./app/platform/automations

RUN chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--access-log"]
