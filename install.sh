#!/usr/bin/env bash
# Installation et configuration du serveur EC-to-Alexa (Debian / Ubuntu).
#
#   ./install.sh                      mode interactif
#   DOMAIN=ecole.exemple.fr ./install.sh   sans question pour le domaine
#
# Le script est relançable : les valeurs déjà présentes dans .env sont
# conservées. Relancez-le après avoir créé la skill pour renseigner
# ALEXA_SKILL_ID et ALEXA_VENDOR_ID.

set -Eeuo pipefail
cd "$(dirname -- "$(readlink -f -- "${BASH_SOURCE[0]}")")"

info() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m/!\\\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mErreur :\033[0m %s\n' "$*" >&2; exit 1; }
trap 'die "échec à la ligne $LINENO de install.sh (voir le message ci-dessus)."' ERR

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
	command -v sudo >/dev/null 2>&1 || die "Lancez ce script en root ou installez sudo."
	SUDO="sudo"
fi

# --- 1. Dépendances ---------------------------------------------------------
if ! command -v curl >/dev/null 2>&1 || ! command -v openssl >/dev/null 2>&1; then
	info "Installation de curl et openssl"
	$SUDO apt-get update
	$SUDO apt-get install -y ca-certificates curl openssl
fi
if ! command -v docker >/dev/null 2>&1; then
	info "Installation de Docker"
	curl -fsSL https://get.docker.com | $SUDO sh
fi
DOCKER="docker"
if ! docker info >/dev/null 2>&1; then
	DOCKER="$SUDO docker"
	if [ -n "$SUDO" ] && ! id -nG | grep -qw docker; then
		# Permet ensuite ./update.sh sans sudo (après reconnexion).
		$SUDO usermod -aG docker "$(id -un)"
		warn "Utilisateur ajouté au groupe docker : déconnectez-vous puis reconnectez-vous avant d'utiliser ./update.sh."
	fi
fi
$DOCKER compose version >/dev/null 2>&1 || die "Le plugin « docker compose » est introuvable."

if systemctl is-active --quiet caddy 2>/dev/null; then
	die "Un Caddy installé hors Docker occupe déjà les ports 80/443. Arrêtez-le avec « sudo systemctl disable --now caddy », puis relancez ./install.sh."
fi

# --- 2. Fichier .env --------------------------------------------------------
if [ ! -f .env ]; then
	cp .env.example .env
	info "Fichier .env créé"
fi
chmod 600 .env

get_env() { grep -E "^$1=" .env | tail -n 1 | cut -d= -f2- || true; }
set_env() {
	local key="$1" value="$2" escaped
	if grep -qE "^$key=" .env; then
		escaped="$(printf '%s' "$value" | sed -e 's/[\\&|]/\\&/g')"
		sed -i "s|^$key=.*|$key=$escaped|" .env
	else
		printf '%s=%s\n' "$key" "$value" >> .env
	fi
}
# ask CLÉ "question" : valeur de l'environnement, sinon question, sinon valeur actuelle.
ask() {
	local key="$1" question="$2" current answer=""
	current="$(get_env "$key")"
	if [ -n "${!key:-}" ]; then
		answer="${!key}"
	elif [ -t 0 ]; then
		read -r -p "$question${current:+ [$current]} : " answer || true
	fi
	set_env "$key" "${answer:-$current}"
}
default_env() { [ -n "$(get_env "$1")" ] || set_env "$1" "$2"; }

info "Configuration (Entrée = garder la valeur entre crochets)"
ask DOMAIN "Nom de domaine du serveur (ex. ecole.mondomaine.fr)"
DOMAIN_VALUE="$(get_env DOMAIN)"
[ -n "$DOMAIN_VALUE" ] || die "Un nom de domaine est obligatoire : Alexa n'accepte que HTTPS avec un certificat valide."
ask ALEXA_SKILL_ID "Identifiant de la skill amzn1.ask.skill... (vide si pas encore créée)"
ask ALEXA_VENDOR_ID "Vendor ID Amazon, ex. M2ABC123DEF (vide si pas encore connu)"

default_env ECO_TO_ALEXA_DB "/data/ec_to_alexa.sqlite3"
default_env ECO_TO_ALEXA_SECURE_COOKIES "1"
default_env EXPOSE_ADDR "127.0.0.1"
default_env ALEXA_OAUTH_CLIENT_ID "ec-to-alexa"
# Les affectations propagent l'échec d'openssl à set -e (un argument ne le ferait pas).
if [ -z "$(get_env ECO_TO_ALEXA_SECRET_KEY)" ]; then
	# Clé Fernet : 32 octets en base64 url-safe, avec le « = » final obligatoire.
	secret="$(openssl rand -base64 32 | tr '+/' '-_')"
	set_env ECO_TO_ALEXA_SECRET_KEY "$secret"
fi
if [ -z "$(get_env ALEXA_OAUTH_CLIENT_SECRET)" ]; then
	secret="$(openssl rand -hex 32)"
	set_env ALEXA_OAUTH_CLIENT_SECRET "$secret"
fi
[ -n "$(get_env ECO_TO_ALEXA_SECRET_KEY)" ] && [ -n "$(get_env ALEXA_OAUTH_CLIENT_SECRET)" ] \
	|| die "Impossible de générer les secrets avec openssl."

SKILL_ID="$(get_env ALEXA_SKILL_ID)"
VENDOR_ID="$(get_env ALEXA_VENDOR_ID)"
if [ -n "$SKILL_ID" ] && ! [[ "$SKILL_ID" =~ ^amzn1\.ask\.skill\.[0-9a-f-]+$ ]]; then
	warn "ALEXA_SKILL_ID ne ressemble pas à amzn1.ask.skill.xxxxxxxx-... : vérifiez-le."
fi
if [ -n "$VENDOR_ID" ] && ! [[ "$VENDOR_ID" =~ ^[A-Z0-9]+$ ]]; then
	warn "ALEXA_VENDOR_ID doit contenir uniquement des majuscules et des chiffres (ex. M2ABC123DEF)."
fi

# --- 3. Réseau --------------------------------------------------------------
DOMAIN_IP="$(getent ahostsv4 "$DOMAIN_VALUE" 2>/dev/null | awk 'NR==1 {print $1}' || true)"
PUBLIC_IP="$(curl -fsS --max-time 5 https://api.ipify.org 2>/dev/null || true)"
if [ -z "$DOMAIN_IP" ]; then
	warn "$DOMAIN_VALUE ne pointe vers aucune adresse : créez l'enregistrement DNS A (voir README)."
elif [ -n "$PUBLIC_IP" ] && [ "$DOMAIN_IP" != "$PUBLIC_IP" ]; then
	warn "$DOMAIN_VALUE pointe vers $DOMAIN_IP mais l'IP publique de ce serveur est $PUBLIC_IP."
fi
if command -v ufw >/dev/null 2>&1 && $SUDO env LC_ALL=C ufw status 2>/dev/null | grep -q "Status: active"; then
	info "Ouverture des ports 80 et 443 dans ufw"
	$SUDO ufw allow 80/tcp >/dev/null
	$SUDO ufw allow 443/tcp >/dev/null
	$SUDO ufw allow 443/udp >/dev/null
fi

# --- 4. Construction, tests, démarrage -------------------------------------
info "Construction des images"
$DOCKER compose build --pull
info "Exécution des tests"
$DOCKER compose run --rm --no-deps web python -m unittest discover -p "test_*.py"
info "Démarrage des services"
$DOCKER compose up -d --remove-orphans

info "Vérification"
for port in 8000 8001; do
	ok=""
	for _ in $(seq 1 15); do
		if curl -fsS --max-time 3 "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; then ok=1; break; fi
		sleep 2
	done
	[ -n "$ok" ] || warn "Le service du port $port ne répond pas : $DOCKER compose logs --tail=50"
done
if curl -fsS --max-time 20 "https://$DOMAIN_VALUE/healthz" >/dev/null 2>&1; then
	info "HTTPS opérationnel sur https://$DOMAIN_VALUE"
else
	warn "https://$DOMAIN_VALUE ne répond pas depuis ce serveur. Le certificat peut prendre une minute, et certaines box bloquent l'accès à leur propre IP publique : testez depuis un téléphone en 4G. Sinon vérifiez le DNS, les ports 80/443 de la box et « $DOCKER compose logs caddy »."
fi

# --- 5. Récapitulatif -------------------------------------------------------
cat <<EOF

==================== Valeurs pour la console Alexa ====================
Endpoint (Build > Endpoint > HTTPS)
  URL            : https://$DOMAIN_VALUE/alexa
  Certificat     : My development endpoint has a certificate from a trusted certificate authority

Account Linking (Build > Account Linking > Auth Code Grant)
  Authorization URI     : https://$DOMAIN_VALUE/oauth/authorize
  Access Token URI      : https://$DOMAIN_VALUE/oauth/token
  Client ID             : $(get_env ALEXA_OAUTH_CLIENT_ID)
  Client Secret         : $(get_env ALEXA_OAUTH_CLIENT_SECRET)
  Authentication Scheme : HTTP Basic (Recommended)
  Scope                 : ecole

Compte EC-to-Alexa et connexion École Directe : https://$DOMAIN_VALUE/
=======================================================================
EOF
if [ -z "$SKILL_ID" ] || [ -z "$VENDOR_ID" ]; then
	warn "Skill pas encore renseignée : créez-la (README, étape 4), puis relancez ./install.sh pour saisir ALEXA_SKILL_ID et ALEXA_VENDOR_ID."
else
	info "Skill $SKILL_ID configurée, liaison de compte active."
fi
