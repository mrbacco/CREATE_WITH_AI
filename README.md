# LLMs comparison tool and router

A Flask app to:

- run a single prompt against one selected LLM
- run the same prompt across multiple LLMs and compare outputs
- route requests across providers with fallback behavior
- keep per-model chat memory for short context continuity

## Features

- Single model mode (`/bac_tool`)
- Multi-model compare mode (`/compare`)
- Dynamic model list from configured API keys (`/models`)
- Provider routing:
  - Gemini
  - Groq
  - OpenRouter
  - Ollama Cloud
- Optional fallback toggle from UI
- Per-model memory persistence (`memory.json`)
- Simple web UI with model picker and compare checkboxes

## Current model catalog

Configured in `app.py`:

- Gemini
  - `gemini-2.0-flash`
  - `gemini-2.0-flash-lite`
- Groq
  - `llama-3.1-8b-instant`
  - `llama-3.3-70b-versatile`
  - `gemma2-9b-it`
  - `openai/gpt-oss-20b`
  - `openai/gpt-oss-120b`
- OpenRouter
  - `openai/gpt-oss-20b:free`
- Ollama Cloud
  - `gpt-oss:20b`
  - `gpt-oss:120b`

Only models with a configured key are exposed in the UI.

## Tech stack

- Python + Flask
- Plain JS frontend
- `python-dotenv` for `.env` configuration
- `ollama` Python package for Ollama Cloud client

## Project structure

```text
app.py                       # Flask server, routes, model router
config.py                    # env loading and app settings
templates/index.html         # UI shell
static/script.js             # UI logic, fetch requests, rendering
static/style.css             # UI styles
llm/
  gemini_client.py
  groq_client.py
  openrouter_client.py
  ollama_cloud_client.py
memory/
  memory_store.py            # save/load memory history
memory.json                  # persisted memory store
requirements.txt
```

## Requirements

- Python 3.10+
- Network access for remote model APIs
- Valid API keys for providers you want to use

## Setup

1. Create and activate a virtual environment:

```powershell
python -m venv venv
.\venv\Scripts\activate
```

2. Install dependencies:

```powershell
pip install -r requirements.txt
```

3. Create `.env` in project root (example below).

4. Start the app:

```powershell
python app.py
```

5. Open:

```text
http://127.0.0.1:5050
```

## `.env` example

Do not commit this file.

```env
ENABLE_BAC_LOGS=true
APP_DEBUG=false

GEMINI_API_KEY=""
GOOGLE_API_KEY=""   # optional alias used by config fallback

GROQ_API_KEY=""
OPENROUTER_API_KEY=""

OLLAMA_API_KEY=""
OLLAMA_CLOUD_BASE_URL="https://ollama.com"

OPENROUTER_SITE_URL="http://localhost:5050"
OPENROUTER_APP_NAME="LLMs comparison tool and router"

MAX_CONTEXT_MESSAGES=12
COMPARE_MAX_WORKERS=2
UPLOAD_FOLDER="uploads"
```

## How routing works

`run_model(...)` in `app.py` decides provider by model ID.

- Gemini models -> `gemini_client.py`
- Groq models -> `groq_client.py`
- OpenRouter model -> `openrouter_client.py`
- Ollama Cloud models -> `ollama_cloud_client.py`

### Fallback behavior

- Controlled by `use_fallback` (UI checkbox, on by default).
- Gemini: on specific quota/rate errors, tries:
  1. `gemini-2.0-flash-lite`
  2. Groq fallback (`llama-3.1-8b-instant`) if configured
  3. OpenRouter fallback (`openai/gpt-oss-20b:free`) if configured
- Groq: on error, tries OpenRouter fallback if configured.
- OpenRouter and Ollama Cloud: no additional fallback chain.

## API endpoints

### `GET /`

Returns the main HTML page.

### `GET /models`

Returns enabled models based on configured keys:

- `bac_tool_default`
- `compare_default`
- `models` (id/provider/type/key_name)

### `POST /bac_tool`

Single-model chat call.

Request:

```json
{
  "message": "Explain transformers simply",
  "model": "llama-3.3-70b-versatile",
  "use_fallback": true
}
```

Response:

```json
{
  "response": "..."
}
```

### `POST /compare`

Parallel comparison across selected models.

Request:

```json
{
  "message": "Give 3 startup ideas",
  "models": ["gemini-2.0-flash", "openai/gpt-oss-20b:free"],
  "use_fallback": true
}
```

Response:

```json
{
  "gemini-2.0-flash": "...",
  "openai/gpt-oss-20b:free": "..."
}
```

### `POST /upload`

Uploads a file to `UPLOAD_FOLDER` and returns `{"status":"ok"}`.

## Error semantics

- Validation/user errors -> HTTP `400`
- Upstream provider/request errors -> HTTP `502`
- Unhandled server errors -> HTTP `500`

Provider failures are surfaced with detailed messages in UI output.

## UI behavior

- Dropdown shows model name + provider-count suffix (e.g. `(5 Groq)`).
- `SINGLE_LLM` button calls `/bac_tool`.
- `Multiple LLMs` button calls `/compare`.
- Compare panel allows selecting one or more models.
- `Use fallback` checkbox toggles fallback logic.

## Security notes

- Keep `.env` out of version control.
- Rotate any API key that was ever committed or shared.
- Prefer scoped keys with quotas/limits per provider.

## Troubleshooting

- No models in UI:
  - confirm `.env` exists and keys are non-empty
  - restart Flask after editing `.env`
- `400` unsupported model:
  - check model ID exists in `MODEL_CATALOG`
- `502` provider failure:
  - check key validity
  - verify provider model availability and account limits
  - inspect response text printed in UI/logs
- Groq `403` / Cloudflare `1010`:
  - key/network reputation/proxy issues are common causes
  - try another network or provider fallback

## Development notes

- Main entrypoint in this project is `app.py` (Flask on port `5050`).
- Static cache is disabled for `/static/*` responses in `app.py`.
- Memory is persisted in `memory.json` by model.
