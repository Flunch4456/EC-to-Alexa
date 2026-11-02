#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

DOCKER="docker"
docker info >/dev/null 2>&1 || DOCKER="sudo docker"

echo "Mise à jour du dépôt..."
git pull --ff-only

echo "Reconstruction des images Docker..."
$DOCKER compose build --pull

echo "Exécution des tests..."
$DOCKER compose run --rm --no-deps web python -m unittest discover -p "test_*.py" -v

echo "Redémarrage des services..."
$DOCKER compose up -d --remove-orphans

echo "État des services :"
$DOCKER compose ps

echo "Mise à jour terminée."
