"""
GEN_AI_TOOL project
Router and AI responses comparison tool done with flask

mrbacco04@gmail.com
Feb 21, 2026

"""

from flask import Flask, render_template, request, jsonify
import os
import urllib.parse
import json
import re
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from werkzeug.exceptions import HTTPException

from config import *
from llm.gemini_client import gemini_bac_tool
from llm.groq_client import groq_bac_tool
from llm.openrouter_client import openrouter_bac_tool
from llm.ollama_cloud_client import ollama_cloud_bac_tool

from memory.memory_store import save_message, save_message_and_get_memory
from memory.document_index import index_file as index_document_file, search_index, list_documents, parse_file


app = Flask(__name__)

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["SEND_FILE_MAX_AGE_DEFAULT"] = 0
MODEL_CATALOG = [
    {"id": "gemini-2.0-flash", "provider": "gemini", "type": "remote", "key_name": "GEMINI_API_KEY"},
    {"id": "gemini-2.0-flash-lite", "provider": "gemini", "type": "remote", "key_name": "GEMINI_API_KEY"},
    {"id": "llama-3.1-8b-instant", "provider": "groq", "type": "remote", "key_name": "GROQ_API_KEY"},
    {"id": "llama-3.3-70b-versatile", "provider": "groq", "type": "remote", "key_name": "GROQ_API_KEY"},
    {"id": "gemma2-9b-it", "provider": "groq", "type": "remote", "key_name": "GROQ_API_KEY"},
    {"id": "openai/gpt-oss-20b", "provider": "groq", "type": "remote", "key_name": "GROQ_API_KEY"},
    {"id": "openai/gpt-oss-120b", "provider": "groq", "type": "remote", "key_name": "GROQ_API_KEY"},
    {"id": "openai/gpt-oss-20b:free", "provider": "openrouter", "type": "remote", "key_name": "OPENROUTER_API_KEY"},
    {"id": "gpt-oss:20b", "provider": "ollama_cloud", "type": "remote", "key_name": "OLLAMA_API_KEY"},
    {"id": "gpt-oss:120b", "provider": "ollama_cloud", "type": "remote", "key_name": "OLLAMA_API_KEY"},
]


def bac_log(message):
    if ENABLE_BAC_LOGS:
        print(message)


def bac_log_major(message):
    if ENABLE_BAC_LOGS:
        print(f"MAJOR: {message}")


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def with_rag_context(messages, file_ids=None):
    latest_user = ""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            latest_user = (msg.get("content") or "").strip()
            break

    hits = search_index(
        latest_user,
        index_file=RAG_INDEX_FILE,
        top_k=RAG_TOP_K,
        vector_dims=RAG_VECTOR_DIMS,
        file_ids=file_ids,
    )
    if not hits:
        return messages, []

    snippets = []
    for hit in hits:
        snippet = (hit.get("text") or "")[:RAG_MAX_SNIPPET_CHARS]
        snippets.append(
            f"[doc:{hit['file_name']}#{hit['chunk_index']} score={hit['score']:.3f}]\n{snippet}"
        )

    grounding = (
        "Use the document snippets below only when relevant. "
        "If you use them, cite the snippet tag like [doc:file#chunk]. "
        "If snippets are insufficient, say what is missing.\n\n"
        + "\n\n".join(snippets)
    )
    enhanced = [{"role": "system", "content": grounding}] + list(messages)
    return enhanced, hits


@app.after_request
def disable_static_cache(response):
    if request.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


@app.errorhandler(ValueError)
def handle_value_error(error):
    text = str(error)
    upstream_prefixes = (
        "Gemini HTTP",
        "Gemini request failed",
        "Gemini returned",
        "Groq HTTP",
        "Groq request failed",
        "Groq returned",
        "OpenRouter HTTP",
        "OpenRouter request failed",
        "OpenRouter returned",
        "Ollama Cloud request failed",
        "Ollama Cloud returned",
    )
    status = 502 if text.startswith(upstream_prefixes) else 400
    return jsonify({"error": text}), status


@app.errorhandler(Exception)
def handle_exception(error):
    if isinstance(error, HTTPException):
        return jsonify({"error": error.description}), error.code
    bac_log(f"BAC: unhandled exception: {error}")
    return jsonify({"error": str(error)}), 500


# -------------------------
# HOME
# -------------------------

@app.route("/")
def home():
    bac_log_major("BAC: GET / requested")
    bac_log("BAC: GET / requested")

    return render_template("index.html")


@app.route("/models", methods=["GET"])
def models():
    enabled = available_models()
    default_model = choose_default_model(enabled)
    compare_defaults = enabled[:2] if len(enabled) >= 2 else enabled
    return jsonify({
        "bac_tool_default": default_model,
        "compare_default": compare_defaults,
        "models": [item for item in MODEL_CATALOG if item["id"] in enabled],
    })


@app.route("/app_status", methods=["GET"])
def app_status():
    import pathlib
    import time
    app_file = pathlib.Path(__file__).resolve()
    return jsonify({
        "status": "running",
        "app_file": str(app_file),
        "app_mtime": time.ctime(app_file.stat().st_mtime),
        "app_size_bytes": app_file.stat().st_size,
        "server_time": time.ctime(),
    })


# -------------------------
# BAC_TOOL
# -------------------------

@app.route("/bac_tool", methods=["POST"])
def bac_tool():
    bac_log_major("BAC: POST /bac_tool started")
    bac_log("BAC: POST /bac_tool started")

    data = request.json or {}
    bac_log(f"BAC: /bac_tool payload keys = {list(data.keys())}")

    model = (data.get("model") or "").strip()
    bac_log(f"BAC: /bac_tool model = {model}")

    message = (data.get("message") or "").strip()
    bac_log(f"BAC: /bac_tool message length = {len(message)}")
    use_fallback = bool(data.get("use_fallback", True))
    bac_log(f"BAC: /bac_tool use_fallback = {use_fallback}")
    file_ids = data.get("file_ids", []) or []
    if not isinstance(file_ids, list):
        return jsonify({"error": "file_ids must be a list"}), 400
    file_ids = [str(item).strip() for item in file_ids if str(item).strip()]
    bac_log(f"BAC: /bac_tool file_ids = {len(file_ids)}")

    output_type = data.get("output_type", "text")
    bac_log(f"BAC: /bac_tool output_type = {output_type}")

    if not message:
        return jsonify({"error": "Message is required"}), 400

    enabled_models = available_models()

    # If there are no configured API keys, allow any catalog model but return informative message.
    if not (GEMINI_API_KEY or GROQ_API_KEY or OPENROUTER_API_KEY or OLLAMA_API_KEY):
        if model not in enabled_models:
            return jsonify({"error": f"Unsupported model: {model}"}), 400
    else:
        if model not in enabled_models:
            return jsonify({"error": f"Unsupported model: {model}"}), 400

    # Validate required provider key is set
    provider = next((item["provider"] for item in MODEL_CATALOG if item["id"] == model), None)
    if provider == "gemini" and not GEMINI_API_KEY:
        return jsonify({"error": "Gemini API key is required for this model."}), 400
    if provider == "groq" and not GROQ_API_KEY:
        return jsonify({"error": "Groq API key is required for this model."}), 400
    if provider == "openrouter" and not OPENROUTER_API_KEY:
        return jsonify({"error": "OpenRouter API key is required for this model."}), 400
    if provider == "ollama_cloud" and not OLLAMA_API_KEY:
        return jsonify({"error": "Ollama Cloud API key is required for this model."}), 400

    memory = save_message_and_get_memory(model, "user", message, MAX_CONTEXT_MESSAGES)
    bac_log(f"BAC: /bac_tool context size = {len(memory)} for model {model}")

    model_messages, rag_hits = with_rag_context(memory, file_ids=file_ids)
    bac_log(f"BAC: /bac_tool rag hits = {len(rag_hits)}")

    response = run_model(model, model_messages, use_fallback=use_fallback)
    bac_log(f"BAC: /bac_tool response length = {len(response) if response else 0}")

    save_message(model, "assistant", response)
    bac_log(f"BAC: /bac_tool saved assistant response for model {model}")
    bac_log("BAC: POST /bac_tool completed")
    bac_log_major(f"BAC: /bac_tool completed for model {model} output_type={output_type} rag_hits={len(rag_hits)}")

    # Stubbed output type handling
    if output_type == "video":
        # TODO: Implement video generation
        return jsonify({
            "response": "Video generation is not yet implemented.",
            "video_url": "/static/sample_video.mp4",
            "rag_hits": len(rag_hits),
            "attached_files": len(file_ids),
        })
    elif output_type == "pdf":
        # TODO: Implement PDF generation
        return jsonify({
            "response": "PDF generation is not yet implemented.",
            "pdf_url": "/static/sample.pdf",
            "rag_hits": len(rag_hits),
            "attached_files": len(file_ids),
        })
    elif output_type == "ppt":
        # TODO: Implement PPT generation
        return jsonify({
            "response": "PPT generation is not yet implemented.",
            "ppt_url": "/static/sample.pptx",
            "rag_hits": len(rag_hits),
            "attached_files": len(file_ids),
        })
    else:
        return jsonify({
            "response": response,
            "rag_hits": len(rag_hits),
            "attached_files": len(file_ids),
        })


# -------------------------
# COMPARE
# -------------------------

@app.route("/compare", methods=["POST"])
def compare():
    bac_log_major("BAC: POST /compare started")
    bac_log("BAC: POST /compare started")

    data = request.json or {}
    bac_log(f"BAC: /compare payload keys = {list(data.keys())}")

    message = data.get("message", "").strip()
    bac_log(f"BAC: /compare message length = {len(message)}")

    models = data.get("models", [])
    bac_log(f"BAC: /compare models = {models}")
    use_fallback = bool(data.get("use_fallback", True))
    bac_log(f"BAC: /compare use_fallback = {use_fallback}")
    file_ids = data.get("file_ids", []) or []
    if not isinstance(file_ids, list):
        return jsonify({"error": "file_ids must be a list"}), 400
    file_ids = [str(item).strip() for item in file_ids if str(item).strip()]
    bac_log(f"BAC: /compare file_ids = {len(file_ids)}")

    if not message:
        bac_log("BAC: /compare validation failed: empty message")
        return jsonify({"error": "Message is required"}), 400

    if not isinstance(models, list) or not models:
        bac_log("BAC: /compare validation failed: invalid models list")
        return jsonify({"error": "At least one model is required"}), 400

    valid_models = set(available_models())
    if not valid_models:
        return jsonify({"error": "No remote models are configured. Set GEMINI_API_KEY, GROQ_API_KEY, or OPENROUTER_API_KEY in .env."}), 400
    invalid_models = [model for model in models if model not in valid_models]
    if invalid_models:
        return jsonify({"error": f"Unsupported model(s): {', '.join(invalid_models)}"}), 400

    result = {}
    workers = max(1, min(len(models), COMPARE_MAX_WORKERS))

    if workers == 1:
        for model in models:
            try:
                result[model] = compare_one_model(model, message, use_fallback=use_fallback, file_ids=file_ids)
                bac_log(f"BAC: /compare completed model {model}")
            except Exception as exc:
                bac_log(f"BAC: /compare error for model {model}: {exc}")
                result[model] = f"Error: {exc}"
    else:
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {
                executor.submit(compare_one_model, model, message, use_fallback, file_ids): model
                for model in models
            }

            for future in as_completed(futures):
                model = futures[future]
                try:
                    result[model] = future.result()
                    bac_log(f"BAC: /compare completed model {model}")
                except Exception as exc:
                    bac_log(f"BAC: /compare error for model {model}: {exc}")
                    result[model] = f"Error: {exc}"

    bac_log("BAC: POST /compare completed")
    bac_log_major(f"BAC: /compare completed models={','.join(models)}")
    return jsonify(result)


# -------------------------
# FILE UPLOAD
# -------------------------

@app.route("/upload", methods=["POST"])
def upload():
    bac_log_major("BAC: POST /upload started")
    bac_log("BAC: POST /upload started")

    file = request.files["file"]
    bac_log(f"BAC: /upload filename = {file.filename}")

    path = os.path.join(

        app.config["UPLOAD_FOLDER"],

        file.filename

    )

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    file.save(path)
    bac_log(f"BAC: /upload saved file to {path}")

    ext = os.path.splitext(file.filename or "")[1].lower()
    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tiff"}
    if ext in image_exts:
        # Images are stored for later vision analysis; no text indexing needed.
        doc_record = {
            "file_id": str(uuid.uuid4()),
            "name": file.filename,
            "path": path,
            "uploaded_at": _now_iso(),
            "size_bytes": os.path.getsize(path) if os.path.exists(path) else 0,
            "chunk_count": 0,
            "text_excerpt": "",
            "kind": "image",
        }
        bac_log(f"BAC: /upload stored image {file.filename}")
        bac_log("BAC: POST /upload completed")
        bac_log_major(f"BAC: /upload completed image={file.filename} doc_id={doc_record.get('file_id')}")
        return jsonify({"status": "ok", "document": doc_record})

    try:
        doc_record = index_document_file(
            path,
            index_file=RAG_INDEX_FILE,
            vector_dims=RAG_VECTOR_DIMS,
            chunk_size=RAG_CHUNK_SIZE,
            chunk_overlap=RAG_CHUNK_OVERLAP,
        )
        doc_record["kind"] = "document"
        bac_log(f"BAC: /upload indexed file {doc_record['name']} chunks={doc_record['chunk_count']}")
    except Exception as exc:
        bac_log(f"BAC: /upload failed to index file {file.filename}: {exc}")
        return jsonify({"error": f"Failed to parse file: {exc}"}), 400

    bac_log("BAC: POST /upload completed")
    bac_log_major(f"BAC: /upload completed file={file.filename} doc_id={doc_record.get('file_id')}")

    return jsonify({"status": "ok", "document": doc_record})


@app.route("/index_url", methods=["POST"])
def index_url():
    bac_log_major("BAC: POST /index_url started")
    bac_log("BAC: POST /index_url started")
    data = request.json or {}
    url = (data.get("url") or "").strip()
    if not url:
        return jsonify({"error": "URL is required"}), 400

    from urllib.parse import urlparse
    from urllib.request import Request, urlopen

    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return jsonify({"error": "Invalid URL"}), 400

    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    base_name = os.path.basename(parsed.path) or "remote"
    if base_name.lower().endswith(".pdf"):
        local_name = f"{uuid.uuid4()}.pdf"
    else:
        local_name = f"{uuid.uuid4()}.txt"
    local_path = os.path.join(app.config["UPLOAD_FOLDER"], local_name)

    try:
        req = Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urlopen(req, timeout=15) as resp:
            content_type = resp.headers.get("Content-Type", "").lower()
            data_bytes = resp.read()

        if "pdf" in content_type or local_name.lower().endswith(".pdf"):
            with open(local_path, "wb") as f:
                f.write(data_bytes)
        else:
            text = data_bytes.decode("utf-8", errors="ignore")
            if "html" in content_type:
                import re
                text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.IGNORECASE)
                text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.IGNORECASE)
                text = re.sub(r"<[^>]+>", " ", text)
            with open(local_path, "w", encoding="utf-8") as f:
                f.write(text)

    except Exception as exc:
        bac_log(f"BAC: /index_url failed fetching URL: {exc}")
        return jsonify({"error": f"Could not fetch URL: {exc}"}), 400

    try:
        doc_record = index_document_file(
            local_path,
            index_file=RAG_INDEX_FILE,
            vector_dims=RAG_VECTOR_DIMS,
            chunk_size=RAG_CHUNK_SIZE,
            chunk_overlap=RAG_CHUNK_OVERLAP,
        )
        doc_record["kind"] = "document"
    except Exception as exc:
        return jsonify({"error": str(exc)}), 500

    bac_log(f"BAC: /index_url indexed url {url} as {local_path}")
    bac_log_major(f"BAC: /index_url completed url={url} doc_id={doc_record.get('file_id')}")
    return jsonify({"status": "ok", "document": doc_record})


def analyze_text_content(text):
    text = (text or "").strip()
    if not text:
        return {
            "summary": "No text available",
            "char_count": 0,
            "word_count": 0,
            "sentence_count": 0,
            "unique_words": 0,
            "top_words": [],
            "excerpt": "",
            "analytical_description": "No text could be extracted from this file.",
        }

    words = re.findall(r"\w+", text.lower())
    word_count = len(words)
    unique_words = len(set(words))
    top_words = Counter(words).most_common(8)
    sentences = [s.strip() for s in re.split(r"[.!?]+", text) if s.strip()]

    top_terms = ", ".join([w for w, _ in top_words[:5]])
    sentence_hint = sentences[0] if sentences else ""

    analytical_description = (
        f"The text contains {word_count} words, {len(sentences)} sentences, and {unique_words} unique terms. "
        f"Main themes appear to include {top_terms if top_terms else 'not enough terms to identify'}; "
        f"the opening content focuses on: {sentence_hint[:200]}" +
        ("..." if len(sentence_hint) > 200 else "")
    )

    if len(text) > 1200:
        analytical_description += " The content is substantial and suitable for expanding into detailed sections or a full article."
    elif len(text) > 300:
        analytical_description += " A focused summary and structured outline are recommended to turn this into expanded material."
    else:
        analytical_description += " Short content may be enriched with examples and context to produce fuller output."

    return {
        "summary": text[:1000] + ("..." if len(text) > 1000 else ""),
        "char_count": len(text),
        "word_count": word_count,
        "sentence_count": len(sentences),
        "unique_words": unique_words,
        "top_words": [{"word": w, "count": c} for w, c in top_words],
        "excerpt": text[:1200],
        "analytical_description": analytical_description,
    }


def analyze_image(path):
    import base64

    ext = os.path.splitext(path)[1].lower()
    mime_types = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".tiff": "image/tiff",
        ".gif": "image/gif"
    }
    mime_type = mime_types.get(ext, "image/jpeg")

    with open(path, "rb") as f:
        image_data = base64.b64encode(f.read()).decode("utf-8")

    if not GEMINI_API_KEY:
        return {"error": "GEMINI_API_KEY not set"}

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={urllib.parse.quote(GEMINI_API_KEY, safe='')}"

    payload = {
        "contents": [
            {
                "parts": [
                    {"text": "Describe this image in detail, including any text visible in the image."},
                    {
                        "inline_data": {
                            "mime_type": mime_type,
                            "data": image_data
                        }
                    }
                ]
            }
        ]
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})

    try:
        with urllib.request.urlopen(req) as response:
            result = json.loads(response.read().decode("utf-8"))
        description = result.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
        return {
            "analytical_description": description,
            "description": description,
        }
    except Exception as e:
        return {"error": str(e), "analytical_description": f"Error analyzing image: {str(e)}"}


@app.route("/documents", methods=["GET"])
def documents():
    docs = list_documents(RAG_INDEX_FILE)
    return jsonify({
        "count": len(docs),
        "documents": docs
    })


@app.route("/analyze_file", methods=["POST", "GET"])
def analyze_file():
    if request.method == "GET":
        file_id = request.args.get("file_id", "").strip()
        if not file_id:
            return jsonify({"error": "file_id is required"}), 400

        docs = list_documents(RAG_INDEX_FILE)
        file = next((item for item in docs if item.get("file_id") == file_id), None)
        if not file:
            return jsonify({"error": "Document not found"}), 404

        path = file.get("path")
        ext = os.path.splitext(path)[1].lower()
        if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".gif"}:
            analysis = analyze_image(path)
        else:
            text = ""
            try:
                text = parse_file(path)
            except Exception as exc:
                bac_log(f"BAC: /analyze_file parse_file failed for {path}: {exc}")
            text = text or file.get("text_excerpt") or ""
            analysis = analyze_text_content(text)
        return jsonify({"status": "ok", "file_id": file_id, "name": file.get("name"), "analysis": analysis})

    files = request.files.getlist("file")
    if not files:
        return jsonify({"error": "No file uploaded"}), 400

    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".gif"}
    results = []
    for file in files:
        filename = file.filename or f"uploaded_{uuid.uuid4()}"
        unique_name = f"{uuid.uuid4()}_{filename}"
        save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        file.save(save_path)

        ext = os.path.splitext(save_path)[1].lower()

        # Handle images directly with Gemini vision — skip text indexing.
        if ext in image_exts:
            analysis = analyze_image(save_path)
            results.append({"filename": filename, "analysis": analysis})
            continue

        try:
            doc_record = index_document_file(
                save_path,
                index_file=RAG_INDEX_FILE,
                vector_dims=RAG_VECTOR_DIMS,
                chunk_size=RAG_CHUNK_SIZE,
                chunk_overlap=RAG_CHUNK_OVERLAP,
            )
            doc_record["kind"] = "document"
        except Exception as exc:
            bac_log(f"BAC: /analyze_file failed to index file {filename}: {exc}")
            results.append({"filename": filename, "error": str(exc)})
            continue

        text = ""
        try:
            text = parse_file(save_path)
        except Exception as exc:
            bac_log(f"BAC: /analyze_file parse_file failed for {save_path}: {exc}")
        analysis = analyze_text_content(text or doc_record.get("text_excerpt") or "")
        results.append({"filename": filename, "document": doc_record, "analysis": analysis})

    overall_description = ""
    if results:
        file_summaries = []
        for item in results:
            if item.get("analysis"):
                file_summaries.append(f"{item['filename']} -> {item['analysis']['analytical_description']}")
        overall_description = "\n---\n".join(file_summaries)

    return jsonify({"status": "ok", "files": results, "overall_analysis": overall_description})


@app.route("/read_file", methods=["GET"])
def read_file():
    file_id = request.args.get("file_id", "").strip()
    if not file_id:
        return jsonify({"error": "file_id is required"}), 400

    docs = list_documents(RAG_INDEX_FILE)
    file = next((item for item in docs if item.get("file_id") == file_id), None)
    if not file:
        return jsonify({"error": "Document not found"}), 404

    bac_log_major(f"BAC: /read_file retrieving text for file_id={file_id} name={file.get('name')}")

    text = ""
    try:
        text = parse_file(file.get("path"))
    except Exception as exc:
        bac_log(f"BAC: /read_file parse_file failed for {file.get('path')}: {exc}")

    if not text:
        text = file.get("text_excerpt") or ""

    if not text:
        return jsonify({"error": "No text available for this document."}), 404

    return jsonify({
        "file_id": file_id,
        "name": file.get("name"),
        "text": text
    })


# -------------------------
# MODEL ROUTER
# -------------------------

def run_model(model, messages, use_fallback=True):
    bac_log_major(f"BAC: run_model called model={model}, messages={len(messages)}, use_fallback={use_fallback}")
    bac_log(f"BAC: run_model called with model = {model}, messages = {len(messages)}")
    if model.startswith("gemini"):
        bac_log("BAC: run_model routing to Gemini")
        if not use_fallback:
            return gemini_bac_tool(model, messages)
        try:
            return gemini_bac_tool(model, messages)
        except ValueError as exc:
            error_text = str(exc)
            should_fallback = (
                model == "gemini-2.0-flash"
                and "HTTP 429" in error_text
                and "RESOURCE_EXHAUSTED" in error_text
            )
            if should_fallback:
                fallback_model = "gemini-2.0-flash-lite"
                bac_log(f"BAC: fallback from {model} to {fallback_model} after quota/rate error")
                try:
                    return gemini_bac_tool(fallback_model, messages)
                except ValueError:
                    if GROQ_API_KEY:
                        groq_fallback = "llama-3.1-8b-instant"
                        bac_log(f"BAC: fallback from Gemini to Groq model {groq_fallback}")
                        try:
                            return groq_bac_tool(groq_fallback, messages)
                        except ValueError:
                            if OPENROUTER_API_KEY:
                                or_fallback = "openai/gpt-oss-20b:free"
                                bac_log(f"BAC: fallback from Groq to OpenRouter model {or_fallback}")
                                return openrouter_bac_tool(or_fallback, messages)
                            raise
                    if OPENROUTER_API_KEY:
                        or_fallback = "openai/gpt-oss-20b:free"
                        bac_log(f"BAC: fallback from Gemini to OpenRouter model {or_fallback}")
                        return openrouter_bac_tool(or_fallback, messages)
                    raise
            if "HTTP 429" in error_text and "RESOURCE_EXHAUSTED" in error_text and GROQ_API_KEY:
                groq_fallback = "llama-3.1-8b-instant"
                bac_log(f"BAC: fallback from Gemini to Groq model {groq_fallback}")
                try:
                    return groq_bac_tool(groq_fallback, messages)
                except ValueError:
                    if OPENROUTER_API_KEY:
                        or_fallback = "openai/gpt-oss-20b:free"
                        bac_log(f"BAC: fallback from Groq to OpenRouter model {or_fallback}")
                        return openrouter_bac_tool(or_fallback, messages)
                    raise
            if "HTTP 429" in error_text and "RESOURCE_EXHAUSTED" in error_text and OPENROUTER_API_KEY:
                or_fallback = "openai/gpt-oss-20b:free"
                bac_log(f"BAC: fallback from Gemini to OpenRouter model {or_fallback}")
                return openrouter_bac_tool(or_fallback, messages)
            raise

    elif model in {
        "llama-3.1-8b-instant",
        "llama-3.3-70b-versatile",
        "gemma2-9b-it",
        "openai/gpt-oss-20b",
        "openai/gpt-oss-120b",
    }:
        bac_log("BAC: run_model routing to Groq")
        if not use_fallback:
            return groq_bac_tool(model, messages)
        try:
            return groq_bac_tool(model, messages)
        except ValueError:
            if OPENROUTER_API_KEY:
                or_fallback = "openai/gpt-oss-20b:free"
                bac_log(f"BAC: fallback from Groq to OpenRouter model {or_fallback}")
                return openrouter_bac_tool(or_fallback, messages)
            raise

    elif model in {"openai/gpt-oss-20b:free"}:
        bac_log("BAC: run_model routing to OpenRouter")
        return openrouter_bac_tool(model, messages)

    elif model in {"gpt-oss:20b", "gpt-oss:120b"}:
        bac_log("BAC: run_model routing to Ollama Cloud")
        return ollama_cloud_bac_tool(model, messages)

    else:
        raise ValueError(f"Unsupported model: {model}.")


def compare_one_model(model, message, use_fallback=True, file_ids=None):
    bac_log(f"BAC: /compare processing model {model}")
    memory = save_message_and_get_memory(model, "user", message, MAX_CONTEXT_MESSAGES)
    bac_log(f"BAC: /compare context size = {len(memory)} for model {model}")
    model_messages, rag_hits = with_rag_context(memory, file_ids=file_ids)
    bac_log(f"BAC: /compare rag hits = {len(rag_hits)} for model {model}")
    answer = run_model(model, model_messages, use_fallback=use_fallback)
    save_message(model, "assistant", answer)
    return answer


def available_models():
    models = []
    for item in MODEL_CATALOG:
        if item["key_name"] == "GEMINI_API_KEY" and GEMINI_API_KEY:
            models.append(item["id"])
        elif item["key_name"] == "GROQ_API_KEY" and GROQ_API_KEY:
            models.append(item["id"])
        elif item["key_name"] == "OPENROUTER_API_KEY" and OPENROUTER_API_KEY:
            models.append(item["id"])
        elif item["key_name"] == "OLLAMA_API_KEY" and OLLAMA_API_KEY:
            models.append(item["id"])

    # Fallback to list catalog models if no API keys are configured (so UI can still show options)
    if not models:
        models = [item["id"] for item in MODEL_CATALOG]

    return models


def choose_default_model(enabled_models):
    if not enabled_models:
        return ""
    if "llama-3.1-8b-instant" in enabled_models:
        return "llama-3.1-8b-instant"
    if "openai/gpt-oss-20b:free" in enabled_models:
        return "openai/gpt-oss-20b:free"
    return enabled_models[0]


# -------------------------

if __name__ == "__main__":
    bac_log(f"BAC: starting Flask app on port 5050 with debug={APP_DEBUG}")

    app.run(debug=APP_DEBUG, port=5050)
