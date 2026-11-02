#!/usr/bin/env bash
# Mise à jour d'EC-to-Alexa sur le serveur :
#   1. sauvegarde de la base (backups/, 10 dernières conservées) ;
#   2. récupération de la dernière version de la branche suivie (main) ;
#   3. reconstruction des images et exécution des tests ;
#   4. redémarrage des services seulement si tout a réussi.
# Les données (volume Docker), .env et le certificat interne sont conservés.

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

DOCKER="docker"
docker info >/dev/null 2>&1 || DOCKER="sudo docker"
BACKUP_DIR="${BACKUP_DIR:-$SCRIPT_DIR/backups}"
KEEP_BACKUPS="${KEEP_BACKUPS:-10}"

branch="$(git rev-parse --abbrev-ref HEAD)"
echo "Branche suivie : $branch"
if [ "$branch" != "main" ]; then
	echo "Attention : le serveur devrait suivre « main » (git checkout main), voir le guide d'installation."
fi

echo "Sauvegarde de la base..."
if $DOCKER compose ps --status running --services 2>/dev/null | grep -qx web; then
	mkdir -p "$BACKUP_DIR"
	chmod 700 "$BACKUP_DIR"
	stamp="$(date +%Y%m%d-%H%M%S)"
	target="$BACKUP_DIR/ec_to_alexa-$stamp.sqlite3"
	# Copie cohérente via l'API de sauvegarde SQLite, même pendant le service.
	$DOCKER compose exec -T web python - >"$target" <<'PY'
import os, sqlite3, sys, tempfile
source = sqlite3.connect(os.environ.get("ECO_TO_ALEXA_DB", "/data/ec_to_alexa.sqlite3"))
with tempfile.NamedTemporaryFile(suffix=".sqlite3") as tmp:
    copy = sqlite3.connect(tmp.name)
    source.backup(copy)
    copy.close()
    with open(tmp.name, "rb") as file:
        sys.stdout.buffer.write(file.read())
PY
	chmod 600 "$target"
	echo "  → $target"
	# Ne garder que les KEEP_BACKUPS plus récentes.
	ls -1t "$BACKUP_DIR"/ec_to_alexa-*.sqlite3 2>/dev/null | tail -n +"$((KEEP_BACKUPS + 1))" | xargs -r rm --
else
	echo "  service web arrêté : sauvegarde ignorée."
fi

echo "Mise à jour du dépôt..."
git pull --ff-only

# Variables ajoutées par la nouvelle version et absentes du .env.
missing=""
while IFS= read -r key; do
	grep -qE "^$key=" .env || missing="$missing $key"
done < <(grep -oE '^[A-Z_]+=' .env.example | tr -d '=')
if [ -n "$missing" ]; then
	echo "Nouvelles variables absentes de .env :$missing"
	echo "  → relancez ./install.sh après la mise à jour pour les renseigner."
fi

echo "Reconstruction des images Docker..."
$DOCKER compose build --pull

echo "Exécution des tests..."
$DOCKER compose run --rm --no-deps web python -m unittest discover -p "test_*.py"

echo "Redémarrage des services..."
$DOCKER compose up -d --remove-orphans

echo "État des services :"
$DOCKER compose ps

echo "Mise à jour terminée."
