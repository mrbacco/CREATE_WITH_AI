# CREATE_WITH_AI (Flask)

CREATE_WITH_AI is a Flask web app for:

- Single-model chat
- Multi-model side-by-side comparison
- Optional fallback routing across providers
- File and URL indexing for RAG-style grounding
- File analysis flow (text and image)

## Current capabilities

- Single mode endpoint: /bac_tool
- Multiple mode endpoint: /compare
- Dynamic model list endpoint: /models
- App identity/status endpoint: /app_status
- File upload endpoint: /upload
- URL indexing endpoint: /index_url
- Indexed documents listing endpoint: /documents
- File analysis endpoint: /analyze_file (POST and GET)
- Raw indexed file reading endpoint: /read_file

## Supported providers and catalog

Configured in app.py:

- Gemini
  - gemini-2.0-flash
  - gemini-2.0-flash-lite
- Groq
  - llama-3.1-8b-instant
  - llama-3.3-70b-versatile
  - gemma2-9b-it
  - openai/gpt-oss-20b
  - openai/gpt-oss-120b
- OpenRouter
  - openai/gpt-oss-20b:free
- Ollama Cloud
  - gpt-oss:20b
  - gpt-oss:120b

Only models with configured keys are exposed in the UI.

## File analysis behavior

- Text-like files are parsed and analyzed with local text statistics.
- Images (.png, .jpg, .jpeg, .webp, .gif, .bmp, .tiff) are analyzed locally on the app server via Pillow-based heuristics.
- Browser-side OCR is performed with Tesseract.js and sent to the backend as OCR hints during Read File Text analysis.
- OCR text extraction is attempted locally with pytesseract (requires Tesseract OCR installed on the host OS).
- The Read File Text button now prioritizes selected files over document ID lookup.
- If no file is selected, an optional document ID can analyze an already-indexed record.

## Project layout

```text
app.py
config.py
requirements.txt

templates/
  index.html

static/
  script.js
  style.css

llm/
  gemini_client.py
  groq_client.py
  openrouter_client.py
  ollama_cloud_client.py

memory/
  memory_store.py
  document_index.py

data/
  rag_index.json      # created at runtime
```

## Requirements

- Python 3.10+
- Network access to enabled provider APIs
- API keys for the providers you plan to use

Dependencies in requirements.txt:

- flask
- python-dotenv
- ollama
- pypdf

## Setup (Windows PowerShell)

1. Create and activate a venv:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

2. Install packages:

```powershell
pip install -r requirements.txt
```

3. Create a .env file in the project root.

4. Start the app:

```powershell
python .\app.py
```

Alternative launcher:

```powershell
python .\run.py
```

Force start (kills existing process bound to port first):

```powershell
python .\run.py --force
```

5. Open:

```text
http://127.0.0.1:5050
```

## Environment variables (.env)

Example:

```env
ENABLE_BAC_LOGS=true
APP_DEBUG=false

GEMINI_API_KEY=""
GOOGLE_API_KEY=""
GROQ_API_KEY=""
OPENROUTER_API_KEY=""
OLLAMA_API_KEY=""

OLLAMA_CLOUD_BASE_URL="https://ollama.com"
OPENROUTER_SITE_URL="http://localhost:5050"
OPENROUTER_APP_NAME="CREATE_WITH_AI"

UPLOAD_FOLDER="uploads"
RAG_INDEX_FILE="data/rag_index.json"

MAX_CONTEXT_MESSAGES=12
COMPARE_MAX_WORKERS=2
RAG_TOP_K=4
RAG_CHUNK_SIZE=900
RAG_CHUNK_OVERLAP=120
RAG_VECTOR_DIMS=192
RAG_MAX_SNIPPET_CHARS=800
```

## Core API contracts

### GET /

Returns the main UI.

### GET /models

Returns:

- bac_tool_default
- compare_default
- models[]

### GET /app_status

Returns running app metadata including resolved app file path and server time.

### POST /bac_tool

Request body:

```json
{
  "message": "Explain transformers simply",
  "model": "gemini-2.0-flash",
  "use_fallback": true,
  "file_ids": [],
  "output_type": "text"
}
```

Response includes:

- response
- rag_hits
- attached_files

For output_type values video, pdf, and ppt, placeholder URLs are returned.

### POST /compare

Request body:

```json
{
  "message": "Give 3 startup ideas",
  "models": ["gemini-2.0-flash", "llama-3.1-8b-instant"],
  "use_fallback": true,
  "file_ids": []
}
```

Response is a model-to-output map.

### POST /upload

- Stores uploaded file.
- Text-like docs are indexed into RAG store.
- Image files are stored and marked as kind=image.

### POST /index_url

Fetches URL content, stores locally, indexes it, and returns document metadata.

### GET /documents

Lists indexed document records.

### GET or POST /analyze_file

- GET with file_id: analyzes indexed item by ID.
- POST with multipart file(s): analyzes uploaded files directly.
- Image files return vision-style analytical_description.
- Text files return statistical text analysis payload.

### GET /read_file

Returns extracted text for an indexed file_id.

## Error handling

- 400 for validation and unsupported input
- 502 for upstream provider failures
- 500 for unhandled server errors

## Notes for development

- Main entrypoint is app.py.
- Static cache for /static is disabled via after_request headers.
- Chat memory is persisted per model in memory.json.
- RAG index is persisted in data/rag_index.json.
