# Installation du serveur

> **Méthode recommandée :** sur un serveur Debian/Ubuntu, clonez le dépôt et
> lancez `./install.sh`, qui installe Docker, génère `.env`, démarre les
> services avec Caddy en HTTPS et affiche les valeurs pour la console Alexa.
> Toutes les étapes sont dans le [README](./README.md). Ce document détaille
> l'installation manuelle, utile pour le développement ou le dépannage.

## Prérequis

- Python 3.12 ou plus récent ;
- Git ;
- Docker Desktop si vous choisissez le déploiement Docker ;
- un compte EcoleDirecte de test autorisé ;
- pour Alexa en production : un domaine DNS et HTTPS public.

## Installation locale Windows

Cette méthode convient pour le développement ou pour une utilisation sur un
poste Windows. Pour un serveur accessible depuis Internet, utilisez plutôt
le déploiement Debian 13 décrit plus bas.

Depuis PowerShell :

```powershell
# Installer au préalable Python 3.12+ et Git, puis ouvrir PowerShell
git clone https://github.com/Flunch4456/EC-to-Alexa.git
Set-Location .\EC-to-Alexa
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
# Renseigner ECO_TO_ALEXA_SECRET_KEY dans .env avant de lancer le serveur
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
.\.venv\Scripts\python.exe -m unittest discover -p "test_*.py" -v
.\.venv\Scripts\python.exe .\account_server.py
```

Copiez la clé affichée dans `ECO_TO_ALEXA_SECRET_KEY` du fichier `.env`.
Conservez ce fichier hors de Git. Le serveur écoute sur toutes les interfaces :
depuis la machine, utilisez `http://127.0.0.1:8000` ; depuis un autre appareil,
utilisez `http://ADRESSE_IP_DU_PC:8000`. Autorisez le port 8000 dans le pare-feu
Windows si nécessaire. N'utilisez pas le serveur standard-library directement
sur Internet : il n'a ni TLS ni les protections nécessaires à un service public.

Le formulaire de `account_server.py` permet de créer un compte web, de se
connecter, puis d'associer EcoleDirecte. Le tableau de bord est disponible
sur `/dashboard` après connexion. Le compte interne et l'identifiant élève
affiché sont ceux de la session courante ; aucune valeur d'un autre compte
n'est réutilisée.

Pour lancer séparément les routes OAuth de test, définissez d'abord
`ALEXA_OAUTH_CLIENT_ID` et `ALEXA_OAUTH_REDIRECT_URI` dans `.env`, puis
ouvrez un second terminal :

```powershell
.\.venv\Scripts\python.exe .\oauth_server.py
```

Le serveur OAuth écoute sur toutes les interfaces, sur le port 8001. Depuis un
autre appareil, utilisez `http://ADRESSE_IP_DU_PC:8001`. En production, le
reverse proxy doit publier ces routes sous HTTPS ; Alexa ne doit jamais être
configurée avec une URL HTTP ou une adresse loopback.

## Installation Docker

Créez `.env` sans y mettre de valeur dans Git :

```powershell
Copy-Item .env.example .env
docker compose build
docker compose run --rm web python -m unittest discover -p "test_*.py" -v
docker compose up -d
docker compose logs -f web
```

Le compose lance trois services : `web` (comptes, port 8000), `oauth`
(liaison Alexa et webhook, port 8001) et `caddy` (HTTPS sur 80/443 pour le
domaine `DOMAIN` de `.env`). Les ports 8000 et 8001 sont publiés sur
`127.0.0.1` par défaut ; mettez `EXPOSE_ADDR=0.0.0.0` (et
`ECO_TO_ALEXA_SECURE_COOKIES=0`) pour y accéder en HTTP depuis le réseau
local. La base est conservée dans le volume `ec_to_alexa_data` et le
certificat dans `caddy_data`.

Arrêt et mise à jour :

```powershell
docker compose down
docker compose build --pull
docker compose up -d
```

Ne supprimez pas le volume lors d'un arrêt normal.

## Déploiement sur un serveur Debian 13

Les commandes suivantes installent l'application avec Docker Compose et Caddy.
Elles sont à exécuter avec un compte ayant les droits `sudo`, dans un serveur
Debian 13 fraîchement installé.

### 1. Préparer Debian et Docker

```bash
 apt update
 apt full-upgrade -y
 apt install -y ca-certificates curl git openssl ufw
curl -fsSL https://get.docker.com |  sh
 usermod -aG docker "$USER"
```

Déconnectez-vous puis reconnectez-vous pour que l'ajout au groupe `docker`
soit pris en compte, puis vérifiez l'installation :

```bash
docker --version
docker compose version
```

### 2. Installer l'application

```bash
 mkdir -p /opt/ec-to-alexa
 chown "$USER":"$USER" /opt/ec-to-alexa
git clone https://github.com/Flunch4456/EC-to-Alexa.git /opt/ec-to-alexa
cd /opt/ec-to-alexa
cp .env.example .env
chmod 600 .env
```

Générez une clé Fernet et copiez-la dans `ECO_TO_ALEXA_SECRET_KEY` du fichier
`.env`. Utilisez une clé aléatoire différente pour chaque installation :

```bash
# Le « = » final fait partie de la clé : ne le supprimez pas.
openssl rand -base64 32 | tr "+/" "-_"
nano .env
```

Si Alexa est utilisée, renseignez aussi `ALEXA_OAUTH_CLIENT_ID` et
`ALEXA_OAUTH_REDIRECT_URI` avec les valeurs déclarées dans la console Alexa.
Ne renseignez pas de mot de passe réel dans `.env` si la connexion est faite
depuis l'interface web.

### 3. Construire, tester et démarrer les conteneurs

```bash
docker compose build --pull
docker compose run --rm web python -m unittest discover -p "test_*.py" -v
docker compose up -d
docker compose ps
docker compose logs --tail=100 web oauth
```

Le volume Docker `ec_to_alexa_data` conserve la base SQLite lors des mises à
jour. Les ports 8000 et 8001 restent sur `127.0.0.1` (voir `EXPOSE_ADDR`) :
l'accès public passe uniquement par Caddy en HTTPS.

### 4. HTTPS avec Caddy

Caddy est fourni par le service `caddy` de `docker-compose.yml` ; il n'y a
rien à installer avec apt. Renseignez `DOMAIN` dans `.env`, faites pointer le
DNS de ce domaine vers le serveur, puis ouvrez les ports HTTP/HTTPS :

```bash
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
docker compose up -d caddy
docker compose logs -f caddy
```

Caddy obtient et renouvelle automatiquement le certificat TLS. Si Caddy a
déjà été installé avec apt lors d'une version précédente, arrêtez-le pour
libérer les ports : `sudo systemctl disable --now caddy`.

### 5. Mises à jour, arrêt et sauvegardes

Lors de la première installation, rendez le script exécutable :

```bash
cd /opt/ec-to-alexa
chmod +x update.sh
```

Pour appliquer une nouvelle version, lancez ensuite :

```bash
cd /opt/ec-to-alexa
./update.sh
```

Le script effectue un `git pull --ff-only`, reconstruit les images avec les
dernières versions de base, exécute les tests, puis redémarre les services
uniquement si ces étapes réussissent. Il ne supprime pas le volume SQLite.
Si le dépôt contient des modifications locales ou si les tests échouent, la
mise à jour s'arrête et les conteneurs existants ne sont pas redémarrés.

Pour une mise à jour manuelle ou pour vérifier les journaux :

```bash
cd /opt/ec-to-alexa
git pull --ff-only
docker compose build --pull
docker compose up -d
docker compose logs --tail=100 web oauth
```

Pour arrêter l'application, utilisez `docker compose down` sans l'option
`--volumes`, afin de conserver les données. Activez des sauvegardes chiffrées
du volume SQLite avant toute donnée réelle et testez régulièrement leur
restauration. Une sauvegarde applicative peut être réalisée après l'arrêt :

```bash
docker compose down
 mkdir -p /var/backups/ec-to-alexa
 chown "$USER":"$USER" /var/backups/ec-to-alexa
docker run --rm \
  -v ec-to-alexa_ec_to_alexa_data:/data:ro \
  -v /var/backups/ec-to-alexa:/backup \
  alpine tar czf /backup/ec-to-alexa-$(date +%F).tar.gz -C /data .
docker compose up -d
```

Le nom du volume peut varier selon le nom du répertoire Compose ; vérifiez-le
avec `docker volume ls` avant la sauvegarde.

La configuration Caddy est dans [`Caddyfile`](./Caddyfile) : `/oauth/*` et
`/alexa` vont au service `oauth`, le reste au service `web`. La sauvegarde du
volume `caddy_data` est facultative (le certificat est redemandé si besoin).

## Vérifications après installation

```powershell
python -m unittest discover -p "test_*.py" -v
```

Puis :

1. ouvrir la page locale ou l'URL HTTPS ;
2. vérifier que l'écran d'erreur ne contient aucun mot de passe ou jeton ;
3. tester une connexion EcoleDirecte avec un compte de test ;
4. tester les réponses API sans données personnelles dans les journaux ;
5. ne configurer Alexa qu'après implémentation et test de l'Account Linking.

## Sauvegarde et restauration SQLite

Arrêtez le conteneur avant de copier le volume ou utilisez une sauvegarde
SQLite cohérente. Exemple local :

```powershell
Copy-Item .\ec_to_alexa.sqlite3 .\backup\ec_to_alexa.sqlite3
```

En production, chiffrez la sauvegarde, limitez son accès et testez
régulièrement sa restauration. Une suppression de compte doit aussi supprimer
les sessions et jetons associés.

## Ce qui reste avant une mise en production

Cette version est une fondation fonctionnelle, pas encore un service public
complet. Avant exposition Internet, il faut notamment intégrer l'interface
web aux comptes SQLite, implémenter OAuth Account Linking, persister les
secrets EcoleDirecte dans un gestionnaire adapté, ajouter CSRF et limitation
de débit, puis effectuer un audit de sécurité et un test Alexa réel.
