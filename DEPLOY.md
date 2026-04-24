# Deploy da API

## Variaveis de ambiente

- `OPENAI_API_KEY`: obrigatoria para gerar embeddings e respostas.
- `PUBLIC_BASE_URL`: opcional. Se definida, a API usa essa URL para montar os links publicos dos PDFs.
- `CORS_ALLOW_ORIGINS`: lista separada por virgula. Exemplo: `https://meuapp.com,https://admin.meuapp.com`
- `OPENAI_CHAT_MODEL`: opcional. Padrao `gpt-4o`.
- `OPENAI_EMBEDDING_MODEL`: opcional. Padrao `text-embedding-3-small`.

## Rodando localmente

```bash
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

## Regerando o banco vetorial

```bash
python rebuild_vectorstore.py
```

## Docker

```bash
docker build -t analista-vagas-api .
docker run --env-file .env -p 8000:8000 analista-vagas-api
```

## Endpoint de health check

- `GET /health`
