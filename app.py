"""
GEN_AI_TOOL project
Router and AI responses comparison tool done with flask

mrbacco04@gmail.com
Feb 21, 2026

"""

from flask import Flask, render_template, request, jsonify
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from werkzeug.exceptions import HTTPException

from config import *
from llm.gemini_client import gemini_bac_tool
from llm.groq_client import groq_bac_tool
from llm.openrouter_client import openrouter_bac_tool
from llm.ollama_cloud_client import ollama_cloud_bac_tool

from memory.memory_store import save_message, save_message_and_get_memory
from memory.document_index import index_file as index_document_file, search_index, list_documents


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


# -------------------------
# BAC_TOOL
# -------------------------

@app.route("/bac_tool", methods=["POST"])
def bac_tool():
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

    if not message:
        return jsonify({"error": "Message is required"}), 400

    enabled_models = available_models()
    if not enabled_models:
        return jsonify({"error": "No remote models are configured. Set GEMINI_API_KEY, GROQ_API_KEY, or OPENROUTER_API_KEY in .env."}), 400

    if model not in enabled_models:
        return jsonify({"error": f"Unsupported model: {model}"}), 400

    memory = save_message_and_get_memory(model, "user", message, MAX_CONTEXT_MESSAGES)
    bac_log(f"BAC: /bac_tool context size = {len(memory)} for model {model}")

    model_messages, rag_hits = with_rag_context(memory, file_ids=file_ids)
    bac_log(f"BAC: /bac_tool rag hits = {len(rag_hits)}")

    response = run_model(model, model_messages, use_fallback=use_fallback)
    bac_log(f"BAC: /bac_tool response length = {len(response) if response else 0}")

    save_message(model, "assistant", response)
    bac_log(f"BAC: /bac_tool saved assistant response for model {model}")
    bac_log("BAC: POST /bac_tool completed")

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
    return jsonify(result)


# -------------------------
# FILE UPLOAD
# -------------------------

@app.route("/upload", methods=["POST"])
def upload():
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
    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
    if ext in image_exts:
        doc_record = {
            "file_id": "",
            "name": file.filename,
            "path": path,
            "size_bytes": os.path.getsize(path) if os.path.exists(path) else 0,
            "chunk_count": 0,
            "kind": "image",
            "note": "Image uploaded. Image grounding is not enabled yet for this route.",
        }
        bac_log(f"BAC: /upload stored image file {doc_record['name']}")
    else:
        doc_record = index_document_file(
            path,
            index_file=RAG_INDEX_FILE,
            vector_dims=RAG_VECTOR_DIMS,
            chunk_size=RAG_CHUNK_SIZE,
            chunk_overlap=RAG_CHUNK_OVERLAP,
        )
        doc_record["kind"] = "document"
        bac_log(f"BAC: /upload indexed file {doc_record['name']} chunks={doc_record['chunk_count']}")
    bac_log("BAC: POST /upload completed")

    return jsonify({"status": "ok", "document": doc_record})


@app.route("/documents", methods=["GET"])
def documents():
    docs = list_documents(RAG_INDEX_FILE)
    return jsonify({
        "count": len(docs),
        "documents": docs
    })


# -------------------------
# MODEL ROUTER
# -------------------------

def run_model(model, messages, use_fallback=True):
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
        if item["key_name"] == "GROQ_API_KEY" and GROQ_API_KEY:
            models.append(item["id"])
        if item["key_name"] == "OPENROUTER_API_KEY" and OPENROUTER_API_KEY:
            models.append(item["id"])
        if item["key_name"] == "OLLAMA_API_KEY" and OLLAMA_API_KEY:
            models.append(item["id"])
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
