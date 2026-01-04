import shutil
import json
import time
from threading import Thread
from flask import Flask, jsonify, request, send_file
from apscheduler.schedulers.background import BackgroundScheduler
import atexit
from flask_cors import CORS
from werkzeug.utils import secure_filename
import os
import uuid
import io
import zipfile
from src.redaction_service import process_pdf_redaction
from src.model import get_entity_types, get_default_entities

app = Flask(__name__)
CORS(app, resources={
    r"/*": {
        "origins": "*",
        "methods": ["GET", "POST", "OPTIONS"],
        "allow_headers": ["Content-Type", "Accept"]
    }
})

UPLOAD_FOLDER = 'temp_uploads'
ALLOWED_EXTENSIONS = {'pdf'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# --- Background Cleanup Task ---
def clean_old_uploads():
    """Delete folders in UPLOAD_FOLDER older than 1 hour"""
    now = time.time()
    cutoff = 3600  # 1 hour in seconds
    try:
        if not os.path.exists(UPLOAD_FOLDER):
            return
            
        for filename in os.listdir(UPLOAD_FOLDER):
            file_path = os.path.join(UPLOAD_FOLDER, filename)
            # Check if it's a directory (session folder)
            if os.path.isdir(file_path):
                # Check creation time
                if os.stat(file_path).st_mtime < now - cutoff:
                    try:
                        shutil.rmtree(file_path, ignore_errors=True)
                        print(f"[Cleanup] Removed old session: {filename}")
                    except Exception as e:
                        print(f"[Cleanup] Failed to remove {filename}: {e}")
    except Exception as e:
        print(f"[Cleanup] Error during scan: {e}")

# Initialize Scheduler
scheduler = BackgroundScheduler()
scheduler.add_job(func=clean_old_uploads, trigger="interval", minutes=30)
scheduler.start()

# Shut down the scheduler when exiting the app
atexit.register(lambda: scheduler.shutdown())



def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint."""
    return jsonify({'status': 'healthy'})


@app.route('/redact', methods=['POST'])
def redact_pdfs():
    """
    Redact PII from uploaded PDF files.
    
    Accepts multipart form data with:
        - files: PDF files to redact
        - method: Redaction method ('full_redact', 'obfuscate', 'replace')
        - replace_text: Replacement text for 'replace' method
        - keywords: JSON array of custom keywords to redact
        - match_mode: Keyword matching mode ('exact', 'fuzzy', 'regex')
        - fuzzy_threshold: Similarity threshold for fuzzy matching (0-100)
        - enabled_entities: JSON array of Presidio entity types to enable
    
    Returns:
        ZIP file containing redacted PDFs
    """
    # Parse form data
    method = request.form.get('method', 'full_redact')
    replace_text = request.form.get('replace_text', '[REDACTED]')
    
    # Parse custom keywords
    keywords_raw = request.form.get('keywords', '')
    custom_keywords = None
    if keywords_raw:
        try:
            custom_keywords = json.loads(keywords_raw)
            if not isinstance(custom_keywords, list):
                custom_keywords = [custom_keywords]
        except json.JSONDecodeError:
            custom_keywords = [k.strip() for k in keywords_raw.split(',') if k.strip()]
    
    # Parse match mode
    match_mode = request.form.get('match_mode', 'exact')
    if match_mode not in ['exact', 'fuzzy', 'regex']:
        match_mode = 'exact'
    
    # Parse fuzzy threshold
    try:
        fuzzy_threshold = int(request.form.get('fuzzy_threshold', '85'))
        fuzzy_threshold = max(0, min(100, fuzzy_threshold))
    except ValueError:
        fuzzy_threshold = 85
    
    # Parse enabled entities
    entities_raw = request.form.get('enabled_entities', '')
    enabled_entities = None
    if entities_raw:
        try:
            enabled_entities = json.loads(entities_raw)
            if not isinstance(enabled_entities, list):
                enabled_entities = None
        except json.JSONDecodeError:
            enabled_entities = None

    # Create session folder
    session_id = str(uuid.uuid4())
    session_folder = os.path.join(UPLOAD_FOLDER, session_id)
    os.makedirs(session_folder, exist_ok=True)

    try:
        # Validate files
        if 'files' not in request.files:
            return jsonify({'error': 'No files provided'}), 400

        files = request.files.getlist('files')
        if not files or len(files) == 0:
            return jsonify({'error': 'No files selected'}), 400

        # Save uploaded files
        uploaded_paths = []
        for file in files:
            if file and file.filename and allowed_file(file.filename):
                filename = secure_filename(file.filename)
                file_path = os.path.join(session_folder, filename)
                file.save(file_path)
                uploaded_paths.append(file_path)

        if not uploaded_paths:
            return jsonify({'error': 'No valid PDF files uploaded'}), 400

        # Log request
        print(f"[Redact] Files: {len(uploaded_paths)}, Method: {method}, " 
              f"Keywords: {custom_keywords}, Mode: {match_mode}")

        # Process PDFs
        output_paths = process_pdf_redaction(
            input_files=uploaded_paths,
            output_folder=session_folder,
            method=method,
            replace_text=replace_text,
            custom_keywords=custom_keywords,
            match_mode=match_mode,
            fuzzy_threshold=fuzzy_threshold,
            enabled_entities=enabled_entities
        )

        # Create ZIP response
        memory_file = io.BytesIO()
        with zipfile.ZipFile(memory_file, 'w', zipfile.ZIP_DEFLATED) as zf:
            for output_path in output_paths:
                zf.write(output_path, os.path.basename(output_path))
        memory_file.seek(0)

        # Cleanup
        shutil.rmtree(session_folder, ignore_errors=True)

        return send_file(
            memory_file,
            mimetype='application/zip',
            as_attachment=True,
            download_name=f'redacted_{session_id[:8]}.zip'
        )

    except Exception as e:
        shutil.rmtree(session_folder, ignore_errors=True)
        print(f"[Error] {str(e)}")
        return jsonify({'error': f'Processing failed: {str(e)}'}), 500


@app.route('/entity-types', methods=['GET'])
def get_available_entity_types():
    """Get available Presidio entity types."""
    return jsonify({
        'entity_types': get_entity_types(),
        'default_entities': get_default_entities()
    })


if __name__ == '__main__':
    # Use Gunicorn for production, but this block is for local dev
    app.run(host='0.0.0.0', port=5000, debug=False)
