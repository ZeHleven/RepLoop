#!/usr/bin/env bash
# Imports knowledge base text files into the database.
# Run once after initial deployment: bash scripts/init_knowledge.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
BACKEND_DIR="$PROJECT_ROOT/backend"
KNOWLEDGE_DIR="$PROJECT_ROOT/knowledge"

cd "$BACKEND_DIR"

echo "=== Importing nutrition_basics.txt ==="
python scripts/import_knowledge.py \
  --file "$KNOWLEDGE_DIR/nutrition_basics.txt" \
  --topic nutrition \
  --source nutrition_basics

echo ""
echo "=== Importing training_principles.txt ==="
python scripts/import_knowledge.py \
  --file "$KNOWLEDGE_DIR/training_principles.txt" \
  --topic training \
  --source training_principles

echo ""
echo "=== Knowledge base initialization complete ==="
