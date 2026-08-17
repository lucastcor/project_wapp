# --------------------------------------------------------------------------
# Bundle do Automation Studio.
#
# O frontend é construído dentro da imagem e o resultado copiado para
# `app/platform/automations`, de onde o FastAPI o serve na mesma origem. Com
# isso o bundle não precisa ser versionado, e a imagem nunca sai com um
# frontend defasado em relação ao backend que ela carrega.
#
# O estágio vem antes de `base` para que a instalação de dependências Node não
# invalide o cache das camadas Python quando só o Python muda.
# --------------------------------------------------------------------------
FROM node:24-slim AS studio

WORKDIR /studio

# `npm ci` exige o lockfile e instala exatamente as versões travadas.
COPY frontend/automation-studio/package.json frontend/automation-studio/package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY frontend/automation-studio/ ./
# `npm run build` roda `tsc --noEmit` antes do Vite: um erro de tipo quebra a
# imagem, e não a produção.
RUN npm run build


FROM python:3.13-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN addgroup --system app && adduser --system --ingroup app app

FROM base AS api-dependencies

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

FROM api-dependencies AS application

COPY app ./app

# `vite.config.ts` emite em `../../app/platform/automations`; a partir de
# /studio isso resolve para /app/platform/automations na imagem de build.
# `tests/test_automation_studio_bundle.py` fixa esse caminho.
COPY --from=studio /app/platform/automations ./app/platform/automations

RUN chown -R app:app /app

USER app

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

FROM api-dependencies AS admin-dependencies
COPY requirements-admin.txt .
RUN pip install --no-cache-dir -r requirements-admin.txt

FROM admin-dependencies AS admin

COPY app ./app
RUN chown -R app:app /app

USER app

EXPOSE 8501

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8501/_stcore/health')"

CMD ["streamlit", "run", "app/admin_app.py", "--server.address=0.0.0.0", "--server.port=8501"]

FROM application AS api

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--access-log"]
