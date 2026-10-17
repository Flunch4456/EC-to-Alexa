#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Mise à jour du dépôt..."
git pull --ff-only

echo "Reconstruction des images Docker..."
docker compose build --pull

echo "Exécution des tests..."
docker compose run --rm web python -m unittest discover -p "test_*.py" -v

echo "Redémarrage des services..."
docker compose up -d --remove-orphans

echo "État des services :"
docker compose ps

echo "Mise à jour terminée."
