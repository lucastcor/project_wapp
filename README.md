# project_wapp

# Chatbot API

API backend em FastAPI para experimentação de orquestração de mensagens, gerenciamento de estado simples e integração futura com providers de LLM.

## Objetivo
Este projeto foi criado como base para evoluir um sistema de chatbot com arquitetura modular, separando rotas, orquestração, estado, providers de geração de resposta e camada de armazenamento.

## Stack
- Python
- FastAPI
- Pydantic
- Uvicorn
- Pytest

## Estrutura
```text
app/
  api/
  core/
  llm/
  storage/
  tools/
  main.py
tests/
README.md
requirements.txt