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

#### Avec un reverse proxy existant (Zoraxy, Nginx Proxy Manager…)

Si les ports 80/443 sont déjà utilisés par un autre proxy, utilisez le
profil `router` : Caddy ne gère plus HTTPS et sert seulement de routeur HTTP
interne sur `EXPOSE_ADDR:ROUTER_PORT` (`127.0.0.1:8080` par défaut).

```bash
COMPOSE_PROFILES=router ./install.sh
```

Dans le proxy, créez **une seule règle** pour le domaine public vers
`http://127.0.0.1:8080`, avec un certificat Let's Encrypt, **sans répertoire
virtuel**. Zoraxy retire le préfixe d'un répertoire virtuel avant de
transmettre la requête (`/oauth/token` deviendrait `/token`), ce qui
casserait la liaison de compte et le webhook ; le routeur interne répartit
donc lui-même `/oauth/*` et `/alexa` vers le service `oauth`, et le reste
vers `web`.

- Proxy sur une autre machine : `EXPOSE_ADDR` = IP locale du serveur.
- Conflit de port (l'interface de Zoraxy utilise 8000 par défaut) : changez
  `WEB_PORT`, `OAUTH_PORT` ou `ROUTER_PORT` dans `.env`.
- `install.sh` arrête le Caddy HTTPS s'il tournait encore : derrière un autre
  proxy, il provoquerait une boucle de redirection.

Pas à pas complet (box, DNS, écrans Zoraxy, vérifications) :
[Derrière Zoraxy : installation pas à pas](#derrière-zoraxy--installation-pas-à-pas).

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

## Derrière Zoraxy : installation pas à pas

Cette section décrit le cas d'un Zoraxy qui sert déjà d'autres sites sur les
ports 80/443. L'exemple utilise `ec-to-alexa.nial-tech.com` ; remplacez-le
par votre domaine.

```
Alexa / navigateur ──HTTPS 443──▶ box ──▶ Zoraxy (certificat Let's Encrypt)
                                              │ HTTP, chemin intact
                                              ▼
                               router :8080 (Caddy, HTTP seul)
                                 ├─ /oauth/*, /alexa ─▶ oauth :8001
                                 └─ tout le reste ────▶ web   :8000
```

### Z1. Box : quels ports rediriger ?

**Seulement TCP 80 et TCP 443, vers la machine qui fait tourner Zoraxy.**
Si Zoraxy publie déjà d'autres sites de votre domaine depuis Internet, ces
redirections existent déjà : il n'y a **rien à ajouter** pour EC-to-Alexa.

| Port public | Protocole | Destination | Rôle |
| --- | --- | --- | --- |
| 443 | TCP | IP locale de Zoraxy, port 443 | Alexa (webhook et liaison de compte), site |
| 80 | TCP | IP locale de Zoraxy, port 80 | Validation et renouvellement Let's Encrypt, redirection vers HTTPS |

Ne redirigez **jamais** 8000, 8001, 8080 ni le port d'administration de
Zoraxy : ils restent accessibles seulement depuis la machine ou le réseau
local. Le port 443/UDP (HTTP/3) est inutile ici.

Sur une **Freebox** : Freebox OS → **Paramètres de la Freebox** → mode
**avancé** → **Gestion des ports** → **Ajouter une redirection** (IP de
destination = IP locale de Zoraxy, protocole TCP, port de début = port de fin
= port de destination = 443, puis la même chose pour 80). Deux vérifications
propres à Free :

- **IPv4 partagée** : certains abonnements reçoivent une IPv4 partagée avec
  une plage de ports réduite, qui exclut 80 et 443. Si c'est le cas, demandez
  une **IPv4 full-stack** dans l'espace abonné Free (Ma Freebox → Demander
  une adresse IP fixe V4 full-stack), puis redémarrez la Freebox.
- **Accès à distance de Freebox OS** : son port HTTPS ne doit pas être 443.

Pensez aussi à donner une IP fixe (bail DHCP statique) à la machine Zoraxy,
sinon la redirection cassera au prochain changement d'adresse.

### Z2. DNS Cloudflare

Dans Cloudflare → **DNS** → **Records** :

| Type | Nom | Contenu | Proxy |
| --- | --- | --- | --- |
| A | `ec-to-alexa` | IP publique de la box | **DNS only** (nuage gris), recommandé |

Un `CNAME ec-to-alexa → nial-tech.com` convient aussi si `nial-tech.com`
pointe déjà vers la box. Si vous gardez le nuage orange (proxied), réglez
**SSL/TLS** sur **Full (strict)**. Ne créez pas d'enregistrement `AAAA`
(IPv6) sauf si la box laisse passer le 443 en IPv6 vers Zoraxy : Alexa
pourrait s'y connecter.

### Z3. Serveur EC-to-Alexa

```bash
git clone -b feature/alexa-skill https://github.com/Flunch4456/EC-to-Alexa.git ec-to-alexa
cd ec-to-alexa
cp .env.example .env
```

Selon l'emplacement de Zoraxy, éditez `.env` **avant** l'installation :

| Zoraxy est… | Dans `.env` | Cible à donner à Zoraxy |
| --- | --- | --- |
| sur la **même machine** | `WEB_PORT=18000` (l'interface de Zoraxy occupe déjà 8000) | `127.0.0.1:8080` |
| sur une **autre machine** du réseau | `EXPOSE_ADDR=IP_LOCALE_DU_SERVEUR` | `IP_LOCALE_DU_SERVEUR:8080` |

Dans le second cas, les ports 8000, 8001 et 8080 deviennent joignables en HTTP
depuis tout le réseau local (Docker contourne `ufw`) : c'est acceptable sur un
réseau domestique, mais ne les redirigez jamais sur la box. Inutile de toucher
à `ECO_TO_ALEXA_TRUSTED_PROXIES` : le routeur transmet déjà l'IP réelle reçue
de Zoraxy.

Puis lancez l'installation en mode `router` :

```bash
COMPOSE_PROFILES=router \
DOMAIN=ec-to-alexa.nial-tech.com \
ALEXA_SKILL_ID=amzn1.ask.skill.3150ba4f-b976-4537-887d-d8bdfbff929f \
ALEXA_VENDOR_ID=MH3SRCH5C9AMF \
./install.sh
```

Le script se termine par la règle Zoraxy à créer et par les valeurs de la
console Alexa, dont le **Client Secret**. Gardez ce terminal ouvert.

### Z4. Zoraxy

Les intitulés peuvent varier légèrement selon la version de Zoraxy.

1. **Status** (réglages globaux) : écoute sur le port **443** avec **Use TLS
   to serve proxy request** activé, écoute HTTP sur le port **80** et
   **Force HTTPS redirect** activé. C'est normalement déjà le cas si Zoraxy
   sert d'autres sites.
2. **TLS / SSL certificates** → **ACME** : demandez un certificat Let's
   Encrypt pour `ec-to-alexa.nial-tech.com` (défi HTTP, qui passe par le
   port 80) et activez le **renouvellement automatique**. Avec un certificat
   wildcard `*.nial-tech.com` déjà présent, cette étape est inutile ; mettez
   alors dans la console Alexa (Endpoint) le type de certificat « sub-domain of
   a domain that has a wildcard certificate ».
3. **Create Proxy Rules** :
   - type : **Host** (sous-domaine), domaine `ec-to-alexa.nial-tech.com` ;
   - cible : `127.0.0.1:8080` (ou `IP_LOCALE_DU_SERVEUR:8080`) ;
   - **Proxy Target require TLS** : **désactivé** (le routeur parle en HTTP) ;
   - **aucun répertoire virtuel** : Zoraxy retirerait le préfixe
     (`/oauth/token` arriverait en `/token`) ;
   - créez la règle.
4. **Access Control** : si une règle globale filtre par pays (ex. France
   uniquement) ou bloque des hébergeurs, elle bloquera Amazon, dont les
   serveurs Alexa sont aux États-Unis et en Irlande (AWS). N'appliquez pas ce
   filtrage à ce domaine, sinon la liaison de compte et le webhook échouent
   sans message clair.
5. N'activez pas d'authentification Zoraxy (Basic Auth, SSO) sur cette
   règle : Alexa ne pourrait plus appeler `/alexa` ni `/oauth/token`.

### Z5. Vérifier

Sur le serveur :

```bash
curl -s http://127.0.0.1:8080/healthz          # {"status":"ok"}  (routeur → web)
docker compose ps                               # web, oauth, router : running/healthy
```

Depuis l'extérieur (téléphone en 4G, ou n'importe quel ordinateur) :

```bash
curl -s https://ec-to-alexa.nial-tech.com/healthz
# {"status":"ok"}  → Zoraxy, certificat et routeur fonctionnent
curl -s "https://ec-to-alexa.nial-tech.com/oauth/authorize"
# {"error": "invalid_request"} → /oauth arrive bien au service oauth, préfixe intact
curl -s -X POST -H "Content-Type: application/json" -d '{}' https://ec-to-alexa.nial-tech.com/alexa
# {"error": "invalid_alexa_request"} → /alexa arrive au webhook, qui refuse
# une requête non signée (normal)
```

| Réponse obtenue | Cause probable |
| --- | --- |
| Erreur de certificat | Certificat Zoraxy pas encore obtenu, ou DNS qui ne pointe pas vers la box. |
| Délai dépassé | Redirection 443 absente sur la box, ou IPv4 partagée (Free). |
| « Cette page n'existe pas » ou `{"error": "not_found"}` sur `/oauth/authorize` | Répertoire virtuel Zoraxy qui retire le préfixe : supprimez-le, une seule règle d'hôte suffit. |
| `{"error": "oauth_not_configured"}` | `ALEXA_VENDOR_ID` ou le secret client manquent : relancez `./install.sh`. |
| `{"error": "alexa_not_configured"}` | `ALEXA_SKILL_ID` manquant : relancez `./install.sh`. |
| Trop de redirections (308) | Le Caddy HTTPS intégré tourne encore : relancez avec `COMPOSE_PROFILES=router`. |
| `502 Bad Gateway` dans Zoraxy | Cible erronée (port 8080, IP) ou option « require TLS » activée. |

### Z6. Terminer dans la console Alexa

1. **Build → Account Linking** : collez le **Client Secret** affiché par
   `install.sh` dans **Your Secret**, vérifiez les autres champs
   (`/oauth/authorize`, `/oauth/token`, client `ec-to-alexa`, HTTP Basic,
   scope `ecole`), puis **Save**.
2. **Build → Interaction Model** → **Build skill**, attendez « Build
   Successful ».
3. Onglet **Test** : **Development**.
4. Sur `https://ec-to-alexa.nial-tech.com/` : créez votre compte, connectez
   École Directe, essayez une question dans « Essayer sans enceinte ».
5. Application Alexa → **Skills et jeux** → **Vos skills** → **Développeur**
   → **Mon Carnet Scolaire** → **Activer**, puis connectez-vous avec le
   compte EC-to-Alexa. Dites « Alexa, ouvre mon carnet scolaire ».

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
