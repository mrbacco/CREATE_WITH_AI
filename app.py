"""
GEN_AI_TOOL project
Router and AI responses comparison tool done with flask

mrbacco04@gmail.com
Feb 21, 2026

"""

from flask import Flask, render_template, request, jsonify, send_file
import os
import urllib.parse
import json
import re
import uuid
import sqlite3
import subprocess
import shutil
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from werkzeug.exceptions import HTTPException
import ollama

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
VIDEO_DB_FILE = os.getenv("VIDEO_DB_FILE", os.path.join("data", "video_analysis.db"))
VIDEO_EXTS = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
_WHISPER_MODEL = None
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


def _is_under_uploads(path):
    uploads_root = os.path.abspath(app.config["UPLOAD_FOLDER"])
    target = os.path.abspath(path or "")
    return bool(target) and target.startswith(uploads_root)


def ensure_video_db():
    os.makedirs(os.path.dirname(VIDEO_DB_FILE), exist_ok=True)
    conn = sqlite3.connect(VIDEO_DB_FILE)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS video_analyses (
                analysis_id TEXT PRIMARY KEY,
                filename TEXT NOT NULL,
                file_path TEXT NOT NULL,
                transcript TEXT,
                summary TEXT,
                keyframes_json TEXT,
                metadata_json TEXT,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def extract_audio_ffmpeg(video_path, audio_path):
    bac_log(f"BAC: extract_audio_ffmpeg start video={video_path} audio={audio_path}")
    ffmpeg_exe = os.getenv("FFMPEG_PATH", "").strip() or shutil.which("ffmpeg")
    if not ffmpeg_exe:
        try:
            import importlib
            imageio_ffmpeg = importlib.import_module("imageio_ffmpeg")
            ffmpeg_exe = imageio_ffmpeg.get_ffmpeg_exe()
        except Exception:
            ffmpeg_exe = ""

    if not ffmpeg_exe:
        raise RuntimeError(
            "ffmpeg executable not found. Install ffmpeg or set FFMPEG_PATH environment variable."
        )

    bac_log(f"BAC: extract_audio_ffmpeg using ffmpeg={ffmpeg_exe}")

    cmd = [
        ffmpeg_exe,
        "-y",
        "-i",
        video_path,
        "-vn",
        "-acodec",
        "pcm_s16le",
        "-ar",
        "16000",
        "-ac",
        "1",
        audio_path,
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError as exc:
        raise RuntimeError(f"ffmpeg executable was not found: {ffmpeg_exe}") from exc
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.strip() or proc.stdout.strip()}")
    bac_log("BAC: extract_audio_ffmpeg completed successfully")


def transcribe_audio_whisper(audio_path):
    global _WHISPER_MODEL
    import wave
    import numpy as np

    bac_log(f"BAC: transcribe_audio_whisper start audio={audio_path}")

    try:
        import whisper
    except ImportError as exc:
        raise RuntimeError("whisper is not installed") from exc

    if _WHISPER_MODEL is None:
        bac_log("BAC: transcribe_audio_whisper loading Whisper model=base")
        _WHISPER_MODEL = whisper.load_model("base")

    try:
        with wave.open(audio_path, "rb") as wf:
            channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            frame_count = wf.getnframes()
            raw = wf.readframes(frame_count)
    except Exception as exc:
        raise RuntimeError(f"failed to load wav audio: {exc}") from exc

    if sample_width != 2:
        raise RuntimeError(f"unsupported wav sample width: {sample_width}")

    audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0

    if channels > 1:
        audio = audio.reshape(-1, channels).mean(axis=1)

    if sample_rate != 16000:
        raise RuntimeError(f"unexpected sample rate {sample_rate}; expected 16000")

    bac_log(
        f"BAC: transcribe_audio_whisper audio props channels={channels} sample_width={sample_width} sample_rate={sample_rate} samples={audio.size}"
    )

    if audio.size == 0:
        return "No audio samples were extracted from this video."

    # Normalize low-volume tracks to improve transcription reliability.
    peak = float(np.max(np.abs(audio)))
    if peak > 0:
        audio = (audio / peak) * 0.95

    language_hint = os.getenv("WHISPER_LANGUAGE", "").strip().lower() or None

    attempts = [
        {
            "task": "transcribe",
            "fp16": False,
            "temperature": 0,
            "condition_on_previous_text": False,
        },
        {
            "task": "transcribe",
            "fp16": False,
            "temperature": 0.2,
            "condition_on_previous_text": False,
            "initial_prompt": "Transcribe any spoken words exactly as heard.",
        },
    ]

    if language_hint:
        for params in attempts:
            params["language"] = language_hint

    last_error = None
    for attempt_index, params in enumerate(attempts, start=1):
        bac_log(f"BAC: transcribe_audio_whisper attempt={attempt_index} language={params.get('language', 'auto')}")
        try:
            result = _WHISPER_MODEL.transcribe(audio, **params)
            text = (result.get("text") or "").strip()
            if not text:
                segments = result.get("segments") or []
                text = " ".join((seg.get("text") or "").strip() for seg in segments).strip()
            if text:
                bac_log(f"BAC: transcribe_audio_whisper success chars={len(text)} attempt={attempt_index}")
                return text
        except Exception as exc:
            last_error = exc
            bac_log(f"BAC: transcribe_audio_whisper attempt={attempt_index} failed: {exc}")

    duration_seconds = round(float(audio.size) / float(sample_rate), 2)
    if last_error is not None:
        return f"Whisper could not produce a transcript. Duration: {duration_seconds}s. Error: {last_error}"
    return f"No speech detected by Whisper. Duration: {duration_seconds}s."


def extract_keyframes(video_path, output_dir, interval_seconds=3):
    bac_log(f"BAC: extract_keyframes start video={video_path} output_dir={output_dir} interval={interval_seconds}s")
    try:
        import cv2
    except ImportError as exc:
        raise RuntimeError("opencv-python is not installed") from exc

    os.makedirs(output_dir, exist_ok=True)
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError("could not open video file for keyframe extraction")

    fps = cap.get(cv2.CAP_PROP_FPS) or 0.0
    effective_fps = fps if fps > 0 else 1.0
    frame_interval = max(1, int(effective_fps * interval_seconds))

    keyframes = []
    frame_index = 0
    extracted = 0
    while True:
        ok, frame = cap.read()
        if not ok:
            break
        if frame_index % frame_interval == 0:
            timestamp = round(frame_index / effective_fps, 2)
            out_name = f"keyframe_{extracted:04d}.jpg"
            out_path = os.path.join(output_dir, out_name)
            cv2.imwrite(out_path, frame)
            keyframes.append({
                "index": extracted,
                "timestamp_seconds": timestamp,
                "path": out_path,
            })
            extracted += 1
        frame_index += 1

    cap.release()
    bac_log(f"BAC: extract_keyframes completed count={len(keyframes)}")
    return keyframes


def summarize_transcript_with_ollama(transcript):
    transcript = (transcript or "").strip()
    if not transcript:
        bac_log("BAC: summarize_transcript_with_ollama skipped because transcript is empty")
        return "No speech transcript detected in audio."

    def local_summary_fallback(text, reason=""):
        clean = re.sub(r"\s+", " ", text).strip()
        if not clean:
            return "No speech transcript detected in audio."
        segments = re.split(r"(?<=[.!?])\s+", clean)
        top_points = [s.strip() for s in segments if s.strip()][:4]
        snippet = "\n".join(f"- {point}" for point in top_points) if top_points else "- (no clear sentence boundaries)"
        note = f"\n\nNote: Ollama was unavailable ({reason}). Generated local transcript summary." if reason else ""
        return (
            "Main Topic\n"
            f"{clean[:220]}\n\n"
            "Key Moments\n"
            f"{snippet}\n\n"
            "Entities Mentioned\n"
            "- (heuristic mode does not perform entity extraction)\n\n"
            "Final Summary\n"
            f"{clean[:600]}"
            f"{note}"
        )

    prompt = (
        "Summarize this video transcript offline in a concise and structured way. "
        "Return 4 sections: Main Topic, Key Moments, Entities Mentioned, and Final Summary.\n\n"
        f"Transcript:\n{transcript[:12000]}"
    )

    try:
        bac_log(f"BAC: summarize_transcript_with_ollama calling model={OLLAMA_DEFAULT_MODEL} chars={len(transcript)}")
        response = ollama.chat(
            model=OLLAMA_DEFAULT_MODEL,
            messages=[
                {"role": "system", "content": "You create factual summaries from transcripts."},
                {"role": "user", "content": prompt},
            ],
        )
        content = (response.get("message") or {}).get("content", "").strip()
        bac_log(f"BAC: summarize_transcript_with_ollama completed chars={len(content)}")
        return content or local_summary_fallback(transcript, reason="empty response")
    except Exception as exc:
        bac_log(f"BAC: summarize_transcript_with_ollama fallback used because: {exc}")
        return local_summary_fallback(transcript, reason=str(exc))


def store_video_analysis(record):
    bac_log(f"BAC: store_video_analysis start analysis_id={record.get('analysis_id')} filename={record.get('filename')}")
    ensure_video_db()
    conn = sqlite3.connect(VIDEO_DB_FILE)
    try:
        # Keep only the latest analysis per filename to avoid repeated list growth.
        conn.execute("DELETE FROM video_analyses WHERE filename = ?", (record["filename"],))
        conn.execute(
            """
            INSERT OR REPLACE INTO video_analyses
            (analysis_id, filename, file_path, transcript, summary, keyframes_json, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["analysis_id"],
                record["filename"],
                record["file_path"],
                record.get("transcript", ""),
                record.get("summary", ""),
                json.dumps(record.get("keyframes", []), ensure_ascii=False),
                json.dumps(record.get("metadata", {}), ensure_ascii=False),
                record.get("created_at", _now_iso()),
            ),
        )
        conn.commit()
        bac_log(f"BAC: store_video_analysis committed analysis_id={record.get('analysis_id')}")
    finally:
        conn.close()


def analyze_video_offline(video_path, filename):
    bac_log_major(f"BAC: analyze_video_offline started filename={filename}")
    analysis_id = str(uuid.uuid4())
    created_at = _now_iso()
    work_dir = os.path.join(app.config["UPLOAD_FOLDER"], "video_work", analysis_id)
    keyframe_dir = os.path.join(work_dir, "keyframes")
    audio_path = os.path.join(work_dir, "audio.wav")
    transcript_path = os.path.join(work_dir, "transcript.txt")
    os.makedirs(work_dir, exist_ok=True)

    try:
        bac_log(f"BAC: analyze_video_offline [{analysis_id}] extracting audio")
        extract_audio_ffmpeg(video_path, audio_path)
        bac_log(f"BAC: analyze_video_offline [{analysis_id}] transcribing audio")
        transcript = transcribe_audio_whisper(audio_path)
        with open(transcript_path, "w", encoding="utf-8") as f:
            f.write(transcript or "")
        bac_log(f"BAC: analyze_video_offline [{analysis_id}] transcript saved to {transcript_path}")
        bac_log(f"BAC: analyze_video_offline [{analysis_id}] summarizing transcript")
        summary = summarize_transcript_with_ollama(transcript)
        bac_log(f"BAC: analyze_video_offline [{analysis_id}] extracting keyframes")
        keyframes = extract_keyframes(video_path, keyframe_dir, interval_seconds=3)

        record = {
            "analysis_id": analysis_id,
            "filename": filename,
            "file_path": video_path,
            "transcript": transcript,
            "summary": summary,
            "keyframes": keyframes,
            "metadata": {
                "pipeline": ["ffmpeg_audio", "whisper_transcription", "opencv_keyframes", "ollama_summary"],
                "keyframe_count": len(keyframes),
                "offline": True,
                "transcript_path": transcript_path,
                "keyframes_dir": keyframe_dir,
            },
            "created_at": created_at,
        }
        store_video_analysis(record)
        bac_log_major(f"BAC: analyze_video_offline completed analysis_id={analysis_id} keyframes={len(keyframes)} transcript_chars={len(transcript)}")

        return {
            "analysis_id": analysis_id,
            "filename": filename,
            "transcript": transcript,
            "summary": summary,
            "keyframes": keyframes,
            "created_at": created_at,
            "analysis_method": "offline_video_pipeline",
            "analytical_description": (
                f"Offline video analysis completed. Extracted {len(keyframes)} keyframes, generated transcript, "
                "and summarized content with local Ollama."
            ),
        }
    except Exception as exc:
        bac_log(f"BAC: analyze_video_offline failed analysis_id={analysis_id}: {exc}")
        return {
            "analysis_id": analysis_id,
            "filename": filename,
            "error": str(exc),
            "analysis_method": "offline_video_pipeline",
            "analytical_description": f"Offline video analysis failed: {exc}",
            "created_at": created_at,
        }
    finally:
        if os.path.exists(audio_path):
            try:
                os.remove(audio_path)
            except Exception:
                pass


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
    if ext in image_exts or ext in VIDEO_EXTS:
        # Images/videos are stored for later local analysis; no text indexing needed.
        kind = "image" if ext in image_exts else "video"
        doc_record = {
            "file_id": str(uuid.uuid4()),
            "name": file.filename,
            "path": path,
            "uploaded_at": _now_iso(),
            "size_bytes": os.path.getsize(path) if os.path.exists(path) else 0,
            "chunk_count": 0,
            "text_excerpt": "",
            "kind": kind,
        }
        bac_log(f"BAC: /upload stored {kind} {file.filename}")
        bac_log("BAC: POST /upload completed")
        bac_log_major(f"BAC: /upload completed {kind}={file.filename} doc_id={doc_record.get('file_id')}")
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


def analyze_image(path, ocr_hint=""):
    try:
        from PIL import Image, ImageStat
    except ImportError:
        return {
            "error": "Pillow is not installed",
            "analytical_description": "Image analysis unavailable because Pillow is not installed on the server.",
            "description": "Image analysis unavailable because Pillow is not installed on the server.",
        }

    def color_name(rgb):
        r, g, b = rgb
        if r < 35 and g < 35 and b < 35:
            return "black"
        if r > 220 and g > 220 and b > 220:
            return "white"
        if abs(r - g) < 15 and abs(g - b) < 15:
            return "gray"
        if r >= g and r >= b:
            if g > 110 and b < 90:
                return "yellow-orange"
            return "red"
        if g >= r and g >= b:
            return "green"
        return "blue"

    try:
        with Image.open(path) as img:
            width, height = img.size
            img_format = (img.format or "unknown").upper()
            mode = img.mode

            orientation = "square"
            if width > height:
                orientation = "landscape"
            elif height > width:
                orientation = "portrait"

            rgb = img.convert("RGB")
            sample = rgb.resize((120, 120))
            stat = ImageStat.Stat(sample)
            mean_r, mean_g, mean_b = stat.mean
            brightness = 0.2126 * mean_r + 0.7152 * mean_g + 0.0722 * mean_b
            contrast = sum(stat.stddev) / 3.0

            if brightness < 70:
                light_label = "dark"
            elif brightness > 180:
                light_label = "bright"
            else:
                light_label = "balanced lighting"

            if contrast < 25:
                contrast_label = "low contrast"
            elif contrast > 55:
                contrast_label = "high contrast"
            else:
                contrast_label = "medium contrast"

            quant = sample.convert("P", palette=Image.ADAPTIVE, colors=5)
            palette = quant.getpalette() or []
            color_counts = quant.getcolors() or []
            color_counts = sorted(color_counts, key=lambda item: item[0], reverse=True)

            dominant_colors = []
            for _, idx in color_counts[:4]:
                base = int(idx) * 3
                if base + 2 < len(palette):
                    rgb_triplet = (palette[base], palette[base + 1], palette[base + 2])
                    dominant_colors.append(color_name(rgb_triplet))

            # Preserve order but remove duplicates.
            seen = set()
            dominant_colors = [c for c in dominant_colors if not (c in seen or seen.add(c))]

            visible_text = (ocr_hint or "").strip()
            if visible_text:
                ocr_status = "OCR provided by frontend (Tesseract.js)"
            else:
                ocr_status = "OCR not attempted"
                try:
                    import pytesseract
                    visible_text = (pytesseract.image_to_string(rgb) or "").strip()
                    ocr_status = "OCR attempted via pytesseract"
                except ImportError:
                    ocr_status = "OCR unavailable (pytesseract not installed)"
                except Exception as exc:
                    ocr_status = f"OCR failed: {exc}"

            brief_description = (
                f"{orientation.title()} {img_format} image ({width}x{height}) with {light_label} and {contrast_label}."
            )

            color_text = ", ".join(dominant_colors) if dominant_colors else "mixed tones"
            detailed_description = (
                f"The image is {orientation} in orientation, rendered in {mode} mode, with dominant colors around {color_text}. "
                f"Overall appearance suggests {light_label} and {contrast_label}."
            )
            if visible_text:
                detailed_description += f" Detected text: {visible_text[:600]}"

            uncertainty = (
                "This is a heuristic local analysis (metadata, color, tone, and optional OCR) and does not perform semantic object recognition."
            )

            confidence = 0.55 if visible_text else 0.45

            return {
                "analytical_description": detailed_description,
                "description": detailed_description,
                "brief_description": brief_description,
                "detailed_description": detailed_description,
                "scene": "not inferred by local heuristic analyzer",
                "objects": [],
                "actions": [],
                "people": [],
                "visible_text": visible_text,
                "style_and_mood": f"{light_label}, {contrast_label}",
                "safety_notes": "No remote AI service used for this analysis.",
                "uncertainty": uncertainty,
                "confidence": confidence,
                "analysis_method": "local_server_heuristics",
                "ocr_status": ocr_status,
                "width": width,
                "height": height,
                "format": img_format,
                "mode": mode,
                "dominant_colors": dominant_colors,
            }
    except Exception as exc:
        return {
            "error": str(exc),
            "analytical_description": f"Error analyzing image locally: {exc}",
            "description": f"Error analyzing image locally: {exc}",
        }


@app.route("/documents", methods=["GET"])
def documents():
    docs = list_documents(RAG_INDEX_FILE)
    return jsonify({
        "count": len(docs),
        "documents": docs
    })


@app.route("/video_analyses", methods=["GET"])
def video_analyses():
    ensure_video_db()
    limit = max(1, min(int(request.args.get("limit", "50")), 500))
    bac_log(f"BAC: GET /video_analyses limit={limit}")
    conn = sqlite3.connect(VIDEO_DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT analysis_id, filename, file_path, transcript, summary, keyframes_json, metadata_json, created_at
            FROM video_analyses
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        conn.close()

    items = []
    for row in rows:
        metadata = json.loads(row["metadata_json"] or "{}")
        items.append({
            "analysis_id": row["analysis_id"],
            "filename": row["filename"],
            "file_path": row["file_path"],
            "transcript": row["transcript"],
            "summary": row["summary"],
            "keyframes": json.loads(row["keyframes_json"] or "[]"),
            "metadata": metadata,
            "transcript_url": f"/video_transcript?analysis_id={urllib.parse.quote(row['analysis_id'])}",
            "keyframes_url": f"/video_keyframes?analysis_id={urllib.parse.quote(row['analysis_id'])}",
            "created_at": row["created_at"],
        })

    bac_log(f"BAC: GET /video_analyses returned count={len(items)}")
    return jsonify({"count": len(items), "items": items})


@app.route("/video_transcript", methods=["GET"])
def video_transcript():
    analysis_id = request.args.get("analysis_id", "").strip()
    bac_log(f"BAC: GET /video_transcript analysis_id={analysis_id}")
    if not analysis_id:
        return jsonify({"error": "analysis_id is required"}), 400

    ensure_video_db()
    conn = sqlite3.connect(VIDEO_DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT transcript, metadata_json FROM video_analyses WHERE analysis_id = ?",
            (analysis_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({"error": "Video analysis not found"}), 404

    metadata = json.loads(row["metadata_json"] or "{}")
    transcript_path = metadata.get("transcript_path", "")
    if transcript_path and _is_under_uploads(transcript_path) and os.path.exists(transcript_path):
        bac_log(f"BAC: /video_transcript serving file {transcript_path}")
        return send_file(transcript_path, mimetype="text/plain; charset=utf-8")

    bac_log("BAC: /video_transcript serving transcript from DB field")
    return (row["transcript"] or "", 200, {"Content-Type": "text/plain; charset=utf-8"})


@app.route("/video_keyframes", methods=["GET"])
def video_keyframes():
    analysis_id = request.args.get("analysis_id", "").strip()
    bac_log(f"BAC: GET /video_keyframes analysis_id={analysis_id}")
    if not analysis_id:
        return jsonify({"error": "analysis_id is required"}), 400

    ensure_video_db()
    conn = sqlite3.connect(VIDEO_DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT keyframes_json FROM video_analyses WHERE analysis_id = ?",
            (analysis_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({"error": "Video analysis not found"}), 404

    keyframes = json.loads(row["keyframes_json"] or "[]")
    cards = []
    for frame in keyframes:
        idx = int(frame.get("index", -1))
        ts = frame.get("timestamp_seconds", 0)
        src = f"/video_keyframe?analysis_id={urllib.parse.quote(analysis_id)}&index={idx}"
        cards.append(
            f'<div style="display:inline-block;margin:8px;text-align:center;">'
            f'<img src="{src}" alt="frame {idx}" style="width:220px;height:130px;object-fit:cover;border:1px solid #ddd;border-radius:6px;">'
            f'<div style="font-family:Segoe UI,Tahoma,sans-serif;font-size:12px;color:#334;">frame {idx} @ {ts}s</div>'
            f"</div>"
        )

    html = (
        "<html><head><meta charset='utf-8'><title>Video Keyframes</title></head><body "
        "style='margin:16px;font-family:Segoe UI,Tahoma,sans-serif;'>"
        f"<h3 style='margin:0 0 12px;'>Keyframes ({len(cards)})</h3>"
        f"{''.join(cards) if cards else '<p>No keyframes found.</p>'}"
        "</body></html>"
    )
    bac_log(f"BAC: /video_keyframes returning gallery count={len(cards)}")
    return html


@app.route("/video_keyframe", methods=["GET"])
def video_keyframe():
    analysis_id = request.args.get("analysis_id", "").strip()
    index_raw = request.args.get("index", "").strip()
    bac_log(f"BAC: GET /video_keyframe analysis_id={analysis_id} index={index_raw}")
    if not analysis_id:
        return jsonify({"error": "analysis_id is required"}), 400

    try:
        frame_index = int(index_raw)
    except ValueError:
        return jsonify({"error": "index must be an integer"}), 400

    ensure_video_db()
    conn = sqlite3.connect(VIDEO_DB_FILE)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT keyframes_json FROM video_analyses WHERE analysis_id = ?",
            (analysis_id,),
        ).fetchone()
    finally:
        conn.close()

    if not row:
        return jsonify({"error": "Video analysis not found"}), 404

    keyframes = json.loads(row["keyframes_json"] or "[]")
    frame = next((item for item in keyframes if int(item.get("index", -1)) == frame_index), None)
    if not frame:
        return jsonify({"error": "Keyframe not found"}), 404

    frame_path = os.path.abspath(frame.get("path") or "")
    if not frame_path or not os.path.exists(frame_path):
        return jsonify({"error": "Keyframe image file is missing"}), 404

    if not _is_under_uploads(frame_path):
        return jsonify({"error": "Keyframe path is outside allowed directory"}), 403

    bac_log(f"BAC: /video_keyframe serving {frame_path}")
    return send_file(frame_path)


@app.route("/analyze_file", methods=["POST", "GET"])
def analyze_file():
    bac_log_major(f"BAC: /analyze_file started method={request.method}")
    if request.method == "GET":
        file_id = request.args.get("file_id", "").strip()
        bac_log(f"BAC: /analyze_file GET file_id={file_id}")
        if not file_id:
            return jsonify({"error": "file_id is required"}), 400

        docs = list_documents(RAG_INDEX_FILE)
        file = next((item for item in docs if item.get("file_id") == file_id), None)
        if not file:
            return jsonify({"error": "Document not found"}), 404

        path = file.get("path")
        ext = os.path.splitext(path)[1].lower()
        if ext in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".gif"}:
            bac_log(f"BAC: /analyze_file GET routing to image analysis path={path}")
            analysis = analyze_image(path)
        else:
            bac_log(f"BAC: /analyze_file GET routing to text analysis path={path}")
            text = ""
            try:
                text = parse_file(path)
            except Exception as exc:
                bac_log(f"BAC: /analyze_file parse_file failed for {path}: {exc}")
            text = text or file.get("text_excerpt") or ""
            analysis = analyze_text_content(text)
        bac_log_major(f"BAC: /analyze_file GET completed file_id={file_id}")
        return jsonify({"status": "ok", "file_id": file_id, "name": file.get("name"), "analysis": analysis})

    files = request.files.getlist("file")
    bac_log(f"BAC: /analyze_file POST file_count={len(files)}")
    if not files:
        return jsonify({"error": "No file uploaded"}), 400
    ocr_hints = request.form.getlist("ocr_text")

    image_exts = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tiff", ".gif"}
    results = []
    for index, file in enumerate(files):
        filename = file.filename or f"uploaded_{uuid.uuid4()}"
        unique_name = f"{uuid.uuid4()}_{filename}"
        save_path = os.path.join(app.config["UPLOAD_FOLDER"], unique_name)
        os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
        file.save(save_path)
        bac_log(f"BAC: /analyze_file POST saved file[{index}] name={filename} path={save_path}")

        ext = os.path.splitext(save_path)[1].lower()
        bac_log(f"BAC: /analyze_file POST file[{index}] ext={ext}")

        if ext in VIDEO_EXTS:
            bac_log(f"BAC: /analyze_file POST file[{index}] routing to video analysis")
            analysis = analyze_video_offline(save_path, filename)
            results.append({"filename": filename, "analysis": analysis})
            continue

        # Handle images directly with local analysis — skip text indexing.
        if ext in image_exts:
            bac_log(f"BAC: /analyze_file POST file[{index}] routing to image analysis")
            hint = ocr_hints[index] if index < len(ocr_hints) else ""
            analysis = analyze_image(save_path, ocr_hint=hint)
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
        bac_log(f"BAC: /analyze_file POST file[{index}] text analysis completed")
        results.append({"filename": filename, "document": doc_record, "analysis": analysis})

    overall_description = ""
    if results:
        file_summaries = []
        for item in results:
            analysis = item.get("analysis") or {}
            if isinstance(analysis, dict):
                desc = analysis.get("analytical_description") or analysis.get("description") or analysis.get("error")
                if desc:
                    file_summaries.append(f"{item['filename']} -> {desc}")
        overall_description = "\n---\n".join(file_summaries)

    bac_log_major(f"BAC: /analyze_file POST completed results={len(results)}")
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
    try:
        app.run(debug=APP_DEBUG, port=5050)
    except OSError as exc:
        # Provide a clear, user-friendly message when a previous process owns the port.
        if getattr(exc, "errno", None) in {48, 98, 10013, 10048}:
            print("ERROR: Port 5050 is already in use.")
            print("Stop the running process first, or run: python run.py --force")
        raise
