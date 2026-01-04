![RedactLy.AI](assets/header_logo.png)

# RedactLy.AI

A document redaction system that helps protect sensitive information in your documents using AI-powered redaction techniques.

## 🏗️ Architecture Overview

### Backend Architecture (server/)

The backend is built with Flask and Gunicorn, designed for reliability and security.

#### Core Components (`server/src/`)

1. **app.py** - Main Application Entry Point
   - Handles HTTP routes and API endpoints.
   - Manages file uploads and document processing.
   - **Production Ready**: Runs via Gunicorn.
   - **Automated Cleanup**: Includes background scheduler to delete old temporary files (older than 1 hour).
   - Key endpoints:
     - `/redact` - Handles document redaction (PDF processing).
     - `/health` - Service health check.
     - `/entity-types` - Returns supported PII entities.

2. **redaction_service.py**
   - Core redaction logic implementation.
   - orchestrates the text extraction, PII detection, and redaction process.

3. **hybrid_detector.py** 🧠
   - **Advanced PII Detection**: Combines Presidio Analyzer, Regex, and custom logic.
   - **Context Awareness**: Special logic for table structures (e.g., detecting names following "Father Name" or "Student Name" while ignoring the headers themselves).
   - **Smart Filtering**: Deny-lists to prevent false positive redactions of common headers.

4. **ocr_redaction.py**
   - **Visual Redaction**: Handles both text layer redaction and image-based redaction (burning redactions into the document).
   - **Metadata Scrubbing**: Removes XML metadata from processed PDFs.

5. **auto_emailer.py**
   - Email notification system for completed jobs (if configured).

6. **config.py**
   - Centralized configuration management and environment variables.

#### Storage
- **Ephemeral Storage**: Uploads are stored in `temp_uploads/` and automatically cleaned up after 1 hour. No permanent database is required for the core redaction workflow.

### Frontend Architecture (client/)

The frontend is built with React, TypeScript, and Vite, featuring a modern, responsive UI.

- **Environment Config**: Uses `.env` for API URL configuration (`VITE_API_URL`).
- **Components**: Built with reusable React components (in `client/src/components/`).
- **State Management**: Uses React Query for efficient data fetching.

## 🚀 Getting Started

### Prerequisites
- Docker
- Ollama (optional, for local LLM features)

### Running with Docker

We provide a robust startup script to build and run the application containers.

1. **Start the application:**
   ```bash
   ./start.sh
   ```
   *This script handles building the images, setting up the network, and starting the containers for both Client and Server.*

2. **Access the application:**
   - **Frontend**: http://localhost:3000
   - **Backend API**: http://localhost:5000

## 📝 API Documentation

### Document Redaction

**Endpoint**: `POST /redact`

**Body (Multipart Form Data):**
- `files`: One or more PDF files.
- `method`: `full_redact` (blackout), `replace` (text replacement), or `obfuscate`.
- `replace_text`: Text to use if method is `replace`.
- `custom_keywords`: JSON list of extra words to redact.
- `match_mode`: `exact` or `fuzzy`.
- `fuzzy_threshold`: 0-100 (for fuzzy matching).
- `enabled_entities`: JSON list of entities to detect (e.g., `["PERSON", "AADHAAR_IN"]`).

**Response:**
- Returns a ZIP file containing the redacted PDF(s).

### Health Check
**Endpoint**: `GET /health`
- Returns `{"status": "healthy"}` if the server is running.

## 🔐 Security & Reliability Features

1.  **Non-Root Execution**: Server container runs as a non-privileged user (`appuser`, UID 5000) for enhanced security.
2.  **Production Server**: Uses **Gunicorn** instead of the Flask development server for better concurrency and stability.
3.  **Automated Cleanup**: Background scheduler automatically removes temporary files to prevent disk space exhaustion.
4.  **Smart Redaction**:
    - **Context-Aware**: Distinguishes between labels ("Name") and values ("John Doe") in tabular data.
    - **False Positive Prevention**: Includes deny-lists for common form headers.
5.  **Secure Processing**: Original tokens are removed from the PDF stream, and images are processed to ensure underlying data is destroyed.

## 🛠️ Development

- **Server**: Python 3.12, Flask, PyMuPDF, Presidio.
- **Client**: Node.js 22, React, Vite, TypeScript.

## 📄 License
This project is licensed under the MIT License.