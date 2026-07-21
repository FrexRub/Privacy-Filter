# AGENTS.md

## Project Overview

This repository contains a local HTTP privacy filter service for n8n workflows.
The service is intended to run on a VPS next to n8n, inside the same Docker
network, and redact personal data before text is sent to external AI APIs.

The implementation uses:

- FastAPI as the HTTP wrapper.
- The official OpenAI Privacy Filter CLI (`opf`) from `openai/privacy-filter`.
- Extra regex rules for Russian resume data.
- Docker Compose for deployment through Dokploy.

The main service file is `app/main.py`.

## Runtime Architecture

The container starts FastAPI with Uvicorn:

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The `/redact` endpoint writes incoming text to a temporary UTF-8 file and calls
the official `opf` CLI with:

```bash
opf --device <device> --output-mode <mode> --format json --no-print-color-coded-text -f <temp-file>
```

The `--format json` option is required. Without it, `opf` can print plain text
instead of machine-readable JSON.

After `opf` returns, the service optionally applies the Russian regex layer to
catch common resume fields that the base model may miss.

## Endpoints

### `GET /health`

Returns:

```json
{"status":"ok"}
```

This confirms FastAPI is running and reachable.

### `POST /redact`

Primary endpoint for n8n.

Example body:

```json
{
  "text": "Иванов Иван Иванович, email ivan@example.com, телефон +7 999 123-45-67. Родился 12.05.1990. Python developer.",
  "device": "cpu",
  "output_mode": "typed"
}
```

### `POST /mask`

Compatibility alias for `/redact`.

## Authentication

Authentication is controlled by `PRIVACY_FILTER_API_TOKEN`.

If `PRIVACY_FILTER_API_TOKEN` is empty, bearer auth is disabled.

If it is set, requests to `/redact` and `/mask` must include:

```text
Authorization: Bearer <token>
```

## Environment Variables

Variables used by the project:

```env
PRIVACY_FILTER_API_TOKEN=change-me
HF_TOKEN=
HF_HUB_DISABLE_XET=1
HF_XET_NUM_CONCURRENT_RANGE_GETS=1
HF_XET_RECONSTRUCT_WRITE_SEQUENTIALLY=1
OPF_CHECKPOINT=
OPF_DEVICE=cpu
OPF_OUTPUT_MODE=typed
OPF_FALLBACK_TO_RU_RULES=true
OPF_TIMEOUT_SECONDS=300
```

Notes:

- `OPF_DEVICE=cpu` is the expected setting for a normal VPS.
- `OPF_DEVICE=cuda` only makes sense on a GPU server with Docker GPU runtime.
- `OPF_CHECKPOINT` should normally remain empty.
- If `OPF_CHECKPOINT` is set, `opf` expects a complete checkpoint already
  present at that path and does not use the default auto-download path.
- `HF_TOKEN` is optional but can help with Hugging Face rate limits.
- `HF_HUB_DISABLE_XET=1` and related Xet settings reduce resource pressure
  during checkpoint download/reconstruction.
- `OPF_FALLBACK_TO_RU_RULES=true` prevents n8n from receiving HTTP 500 when
  official `opf` fails; the API returns regex-only masking instead.

## Docker Compose

The service is deployed through `docker-compose.yml`.

Important deployment choices:

- No public `ports` mapping is used.
- The service uses `expose: "8000"` and is reachable only inside the Docker
  network.
- The service is attached to the external n8n network:

```yaml
networks:
  n8n-n8nrunnerpostgresollama-uxqm0a:
    external: true
```

The n8n HTTP Request node should call:

```text
http://privacy-filter-api:8000/redact
```

Do not add a trailing space to the URL.

## Model Cache

The official OPF checkpoint is downloaded on the first `/redact` request.

The checkpoint is stored in:

```text
/root/.opf/privacy_filter
```

The Docker volume persists it:

```yaml
volumes:
  - opf-model-cache:/root/.opf
```

The checkpoint download is about 2.8 GB. The image and runtime also need
additional disk space.

## Known Dokploy Issues And Fixes

### Port 8000 Already Allocated

Symptom:

```text
Bind for 0.0.0.0:8000 failed: port is already allocated
```

Fix:

- Do not use `ports: "8000:8000"` in this project.
- Use `expose: "8000"` because n8n calls the service through the shared Docker
  network.

### Incomplete Checkpoint

Symptom:

```text
Default OPF checkpoint at /root/.opf/privacy_filter is incomplete: missing config.json
```

Cause:

- A previous checkpoint download was interrupted.
- The volume contains a partial checkpoint folder.

Implemented mitigation:

- `app/main.py` removes an incomplete default checkpoint before running `opf`.

Manual fix:

```bash
docker volume rm test-privacy-filter-goxibd_opf-model-cache
```

Adjust the volume name if the Dokploy project name changes. Find it with:

```bash
docker volume ls | grep opf
```

### `opf failed with exit code -9`

Symptom:

```text
opf failed with exit code -9
```

Meaning:

- The `opf` process was killed by the system, usually by OOM killer or a
  Dokploy/container memory limit.

Observed behavior:

- First it happened during checkpoint download.
- Later the checkpoint downloaded successfully, but `opf` was killed right
  after download, likely while loading the model into memory.

Fix options:

- Increase VPS RAM or Dokploy memory limit.
- Add swap on the VPS.
- Ensure enough free disk space.
- Keep `OPF_DEVICE=cpu` on non-GPU VPS.
- Keep CPU-only PyTorch in the Dockerfile.
- Use `OPF_FALLBACK_TO_RU_RULES=true` so n8n still receives a redacted response.

Resource expectation:

- Checkpoint size is about 2.8 GB.
- For official OPF inference on CPU, start with at least 6-8 GB RAM, preferably
  more.

## Fallback Behavior

If official `opf` fails and `OPF_FALLBACK_TO_RU_RULES=true`, the API returns a
regex-only redaction result instead of HTTP 500.

Fallback responses include:

```json
{
  "summary": {
    "source": "ru_rules_fallback",
    "fallback_reason": "opf_failed"
  }
}
```

This is intended to keep n8n workflows running on resource-constrained VPS
instances, while making it visible that the official OPF model was not used for
that request.

## Russian Regex Layer

The extra rules target:

- Full name / ФИО.
- Email.
- Russian phone numbers.
- Date of birth labels and dates.
- Address lines.
- Passport numbers.
- SNILS.
- INN.
- Similar document/account numbers.

These rules are not a full replacement for official OPF, but they provide a
useful minimum protection layer for Russian resumes and n8n automation.

## n8n HTTP Request Node

Recommended settings:

- Method: `POST`
- URL: `http://privacy-filter-api:8000/redact`
- Body type: JSON
- Timeout: at least `300000` ms for first run

Example JSON body:

```json
{
  "text": "Иванов Иван Иванович, email ivan@example.com, телефон +7 999 123-45-67. Родился 12.05.1990. Python developer.",
  "device": "cpu",
  "output_mode": "typed"
}
```

Do not send the `Authorization` header if `PRIVACY_FILTER_API_TOKEN` is empty.

## Development Notes

- Keep edits scoped and simple.
- Prefer changing `app/main.py`, `docker-compose.yml`, `.env.example`,
  `Dockerfile`, and `README.md` only when relevant.
- Use `python -m py_compile app/main.py` for a quick syntax check.
- Docker CLI may not be available in the local Codex environment, so Compose
  validation may need to be done on the VPS/Dokploy side.
- Do not reintroduce public port binding unless the user explicitly wants the
  service exposed outside Docker.
