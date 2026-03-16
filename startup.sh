#!/bin/bash
echo "📥 Running ESAC registry ingestion..."
python ingest_esac.py
echo "🚀 Starting PaperPath server..."
uvicorn main:app --host 0.0.0.0 --port $PORT
