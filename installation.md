# Installation du serveur

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
Conservez ce fichier hors de Git. Le site local est ensuite disponible sur
`http://127.0.0.1:8000`. N'utilisez pas le serveur standard-library
directement sur Internet : il n'a ni TLS ni les protections nécessaires à un
service public.

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

Le serveur OAuth écoute sur `http://127.0.0.1:8001` en local. En production,
le reverse proxy doit publier ces routes sous HTTPS ; Alexa ne doit jamais
être configurée avec une URL HTTP ou une adresse loopback.

## Installation Docker

Créez `.env` sans y mettre de valeur dans Git :

```powershell
Copy-Item .env.example .env
docker compose build
docker compose run --rm web python -m unittest discover -p "test_*.py" -v
docker compose up -d
docker compose logs -f web
```

Le compose écoute uniquement sur `127.0.0.1:8000` et `127.0.0.1:8001`, conserve
la base dans le volume `ec_to_alexa_data` et lance les services comptes et
OAuth séparément. Placez un reverse proxy HTTPS devant ces ports.

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
openssl rand -base64 32 | tr "+/" "-_" | tr -d "=\n"; echo
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

Les services restent liés à `0.0.0.0:8000` et `0.0.0.0:8001` ; ils sont
donc exposés directement sur Internet. Le volume Docker
`ec_to_alexa_data` conserve la base SQLite lors des mises à jour.

### 4. Installer Caddy et activer HTTPS

Installez Caddy depuis son dépôt officiel, puis activez-le :

```bash
 apt install -y debian-keyring debian-archive-keyring apt-transport-https gnupg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  |  gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
  |  tee /etc/apt/sources.list.d/caddy-stable.list
 apt update
 apt install -y caddy
 cp Caddyfile /etc/caddy/Caddyfile
 nano /etc/caddy/Caddyfile
 systemctl enable --now caddy
 systemctl reload caddy
```

Dans l'éditeur, remplacez `ecole.example.fr` par votre nom de domaine. Le DNS
de ce domaine doit pointer vers le serveur et les ports HTTP/HTTPS doivent
être ouverts :

```bash
 ufw allow OpenSSH
 ufw allow 80/tcp
 ufw allow 443/tcp
 ufw enable
```

Caddy obtient et renouvelle automatiquement le certificat TLS. Vérifiez
ensuite `https://ecole.example.fr` et les journaux avec
` journalctl -u caddy -f`.

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

Exemple de configuration Caddy (voir aussi [`Caddyfile`](./Caddyfile)) :

```caddyfile
ecole.example.fr {
    reverse_proxy /oauth/* 127.0.0.1:8001
    reverse_proxy /alexa 127.0.0.1:8001
    reverse_proxy 127.0.0.1:8000
}
```

La gestion du certificat est alors faite par Caddy. Vérifiez les exigences
Amazon concernant TLS avant de configurer Alexa.

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
