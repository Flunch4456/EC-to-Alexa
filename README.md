# EC-to-Alexa — Installation et maintenance du serveur

> **Branche de documentation.** Le code à jour et la description du projet
> sont sur [`main`](https://github.com/Flunch4456/EC-to-Alexa/tree/main) :
> c'est **toujours `main` que le serveur doit suivre**. La configuration de
> la skill dans la console Amazon est sur la branche
> [`configuration-alexa-skill`](https://github.com/Flunch4456/EC-to-Alexa/tree/configuration-alexa-skill).

Exemple utilisé dans ce guide : domaine `ec-to-alexa.mondomaine.fr`, serveur
`192.168.1.156`, Zoraxy sur une autre machine, dépôt cloné dans
`/opt/ec-to-alexa`. Remplace par tes valeurs (si le dépôt est ailleurs, par
exemple `/opt/ec-to-alexa/ec-to-alexa`, adapte les `cd`).

## Sommaire

1. [Vue d'ensemble](#1-vue-densemble)
2. [Prérequis](#2-prérequis)
3. [Réseau : box et DNS](#3-réseau--box-et-dns)
4. [Installer le serveur](#4-installer-le-serveur)
5. [Configurer Zoraxy](#5-configurer-zoraxy)
6. [Vérifier](#6-vérifier)
7. [Mettre à jour](#7-mettre-à-jour)
8. [Changer de nom de domaine](#8-changer-de-nom-de-domaine)
9. [Changer d'IP ou de machine](#9-changer-dip-ou-de-machine)
10. [Sauvegarder et restaurer](#10-sauvegarder-et-restaurer)
11. [Changer les secrets](#11-changer-les-secrets)
12. [Référence du fichier `.env`](#12-référence-du-fichier-env)
13. [Journaux et dépannage](#13-journaux-et-dépannage)

---

## 1. Vue d'ensemble

Le serveur fait tourner trois conteneurs Docker :

| Service | Port interne | Rôle |
| --- | --- | --- |
| `web` | 8000 | Site : comptes, École Directe, « Lier mon compte Amazon ». |
| `oauth` | 8001 | Liaison OAuth avec Alexa (`/oauth/*`) et webhook de la skill (`/alexa`). |
| `router` **ou** `caddy` | 8443 / 8080, ou 80 / 443 | Répartit les chemins entre `web` et `oauth`. |

Deux modes, au choix, enregistrés dans `.env` (`COMPOSE_PROFILES`) :

| Mode | Quand l'utiliser | HTTPS public assuré par |
| --- | --- | --- |
| **`router`** (recommandé chez soi) | Un reverse proxy (Zoraxy, Nginx Proxy Manager…) occupe déjà les ports 80/443. | Le proxy existant. Le routeur interne écoute en HTTPS sur 8443 (certificat interne) et en HTTP sur 8080. |
| **`caddy`** | Le serveur est seul derrière la box, ou c'est un VPS. | Caddy intégré, qui obtient lui-même le certificat Let's Encrypt. |

```
Alexa / navigateur ──HTTPS 443──▶ box ──▶ Zoraxy (Let's Encrypt)
                                              │ HTTPS interne, chemin intact
                                              ▼
                            routeur :8443 (EC-to-Alexa)
                              ├─ /oauth/*, /alexa ─▶ oauth :8001
                              └─ tout le reste ────▶ web   :8000
```

Les données (base SQLite) sont dans le volume Docker `ec-to-alexa_ec_to_alexa_data`.
Les secrets sont dans `.env` (jamais dans Git).

## 2. Prérequis

- Une machine **Debian 12/13 ou Ubuntu** allumée en permanence (VM, conteneur
  LXC Proxmox, Raspberry Pi 64 bits ou VPS), avec un accès `root` ou `sudo`.
  - **Conteneur LXC Proxmox** : activer les options **nesting** et
    **keyctl** du conteneur (Options → Features), nécessaires à Docker.
- Un **nom de domaine** dont tu gères le DNS (Cloudflare ou autre).
- Une **IPv4 publique** avec les ports 80 et 443 disponibles. Chez Free,
  vérifier que l'IPv4 n'est pas « partagée » (voir §3).
- Le **compte Amazon** de l'Echo, pour la console développeur (guide de la
  branche `configuration-alexa-skill`).

## 3. Réseau : box et DNS

### Ports à rediriger sur la box

**Seulement TCP 80 et TCP 443, vers la machine du reverse proxy.**

| Port public | Destination | Rôle |
| --- | --- | --- |
| TCP 443 | IP locale de Zoraxy (ou du serveur en mode `caddy`), port 443 | Alexa, liaison de compte, site. |
| TCP 80 | même IP, port 80 | Obtention et renouvellement Let's Encrypt, redirection vers HTTPS. |

Ne redirige **jamais** 8000, 8001, 8080, 8443 ni le port d'administration de
Zoraxy. Si Zoraxy publie déjà d'autres sites, ces redirections existent : rien
à ajouter.

**Freebox** : Freebox OS → Paramètres de la Freebox → mode avancé →
**Gestion des ports** → Ajouter une redirection (TCP, port 443 → IP de
Zoraxy:443, puis 80 → IP de Zoraxy:80).
- **IPv4 partagée** : si la plage de ports de l'abonnement exclut 80/443,
  demande une **IPv4 full-stack** dans l'espace abonné Free, puis redémarre la
  Freebox.
- Le port HTTPS de l'**accès à distance** de Freebox OS ne doit pas être 443.
- Donne une **IP fixe** (bail DHCP statique) à Zoraxy et au serveur.

### DNS (Cloudflare)

| Type | Nom | Contenu | Proxy |
| --- | --- | --- | --- |
| A | `ec-to-alexa` | IP publique de la box | **DNS only** (nuage gris) |

Un `CNAME` vers un nom qui pointe déjà sur la box convient aussi. Si tu
proxifies (nuage orange), règle SSL/TLS sur **Full (strict)**. Pas
d'enregistrement `AAAA` tant que le 443 n'est pas ouvert en IPv6 vers le
proxy.

## 4. Installer le serveur

### 4.1 Récupérer le code (branche `main`)

```bash
apt update && apt install -y git
git clone https://github.com/Flunch4456/EC-to-Alexa.git /opt/ec-to-alexa
cd /opt/ec-to-alexa
cp .env.example .env
```

### 4.2 Adapter `.env` avant la première installation

Seulement en mode `router`, selon l'emplacement du proxy :

| Le proxy (Zoraxy) est… | À mettre dans `.env` | Cible à donner au proxy |
| --- | --- | --- |
| sur **une autre machine** | `EXPOSE_ADDR=192.168.1.156` (IP du serveur) | `192.168.1.156:8443` (TLS) |
| sur **la même machine** | `WEB_PORT=18000` (Zoraxy occupe déjà 8000) | `127.0.0.1:8443` (TLS) |

### 4.3 Lancer l'installation

```bash
COMPOSE_PROFILES=router ./install.sh        # ou COMPOSE_PROFILES=caddy
```

Le script installe Docker si besoin, génère les secrets, crée le certificat
interne du routeur (`router-tls/`), construit les images, lance les tests et
démarre les services. Il pose ces questions (Entrée = garder la valeur entre
crochets) :

| Question | Première installation | Après création de la skill |
| --- | --- | --- |
| Nom de domaine | `ec-to-alexa.mondomaine.fr` | Entrée |
| Identifiant de la skill | Entrée (vide) | `amzn1.ask.skill.…` |
| Vendor ID Amazon | Entrée (vide) | ex. `M2ABC123DEF` |
| Alexa Client Id | Entrée (vide) | `amzn1.application-oa2-client.…` |
| Alexa Client Secret (saisie masquée) | — | bouton **SHOW** de la console |

Ces valeurs viennent de la console Amazon : voir le guide de la branche
`configuration-alexa-skill`. Relance simplement `./install.sh` quand tu les
as. Les secrets existants ne sont jamais régénérés.

À la fin, le script affiche la règle à créer dans le proxy et le bloc
**« Valeurs pour la console Alexa »** (URL de l'endpoint, URL OAuth, Client
ID et **Client Secret** à coller dans Account Linking).

## 5. Configurer Zoraxy

1. **Certificat** : TLS / SSL certificates → ACME → certificat Let's Encrypt
   pour `ec-to-alexa.mondomaine.fr`, avec renouvellement automatique. Avec un
   wildcard `*.mondomaine.fr` déjà présent, rien à faire, mais choisis alors
   le type « wildcard » dans l'endpoint de la console Alexa.
2. **Règle** : Create Proxy Rule →
   - type **Host**, domaine `ec-to-alexa.mondomaine.fr` ;
   - cible `192.168.1.156:8443` ;
   - **Require TLS** : activé, **Skip Verification** : activé (certificat
     interne auto-signé) ;
   - **Destinations → Health-check URI** : `/healthz` ;
   - **aucun répertoire virtuel** : Zoraxy retire le préfixe d'un répertoire
     virtuel (`/oauth/token` arriverait en `/token`), le routeur interne
     répartit lui-même les chemins.
3. **Status → Advance Settings → Minimum TLS Version : TLS v1.2.**
   ⚠️ Avec TLS 1.3 minimum, la liaison de compte fonctionne, mais **chaque
   requête de la skill échoue** pendant la négociation TLS, sans aucune trace
   dans les journaux. Alexa répond alors « Je ne parviens pas à accéder à la
   Skill demandée ».
4. **Sécurité de la règle** : pas d'authentification (Basic Auth, SSO), pas de
   captcha, pas de filtrage par pays ni de blocage des hébergeurs sur ce
   domaine. Les serveurs d'Amazon sont chez AWS (États-Unis, Irlande).

En mode `caddy`, cette étape n'existe pas : Caddy obtient le certificat tout
seul dès que le DNS et les ports 80/443 pointent vers le serveur.

## 6. Vérifier

Sur le serveur :

```bash
cd /opt/ec-to-alexa
docker compose ps                                     # web, oauth, router : running
curl -sk https://192.168.1.156:8443/healthz           # {"status":"ok"}
```

Depuis l'extérieur (téléphone en 4G ou autre réseau) :

```bash
curl -s https://ec-to-alexa.mondomaine.fr/healthz
# {"status":"ok"}                         → proxy, certificat et routeur OK
curl -s https://ec-to-alexa.mondomaine.fr/oauth/authorize
# {"error": "invalid_request"}            → /oauth arrive au service oauth
curl -s -X POST -H "Content-Type: application/json" -d '{}' https://ec-to-alexa.mondomaine.fr/alexa
# {"error": "invalid_alexa_request"}      → /alexa arrive au webhook (refus normal : non signé)
```

Puis, sur le site : créer un compte, connecter École Directe, poser une
question dans « Essayer sans enceinte ».

## 7. Mettre à jour

### Mise à jour courante

```bash
cd /opt/ec-to-alexa
./update.sh
```

Le script :

1. **sauvegarde la base** dans `backups/` (copie SQLite cohérente, les 10
   dernières sont gardées) ;
2. récupère la dernière version de `main` (`git pull --ff-only`) ;
3. signale les **nouvelles variables** de `.env.example` absentes de ton
   `.env`. Relance alors `./install.sh` pour les renseigner ;
4. reconstruit les images et **lance les tests**. En cas d'échec, il
   s'arrête et les services en place continuent de tourner ;
5. redémarre les services.

Les données, `.env` et le certificat interne sont conservés.

### Quand relancer `install.sh`

- `update.sh` signale de nouvelles variables ;
- tu changes le domaine, le mode (`router`/`caddy`), l'IP ou les ports ;
- tu ajoutes ou changes un identifiant de la console Amazon.

`install.sh` est relançable sans risque : il garde toutes les valeurs déjà
présentes.

### Revenir à la version précédente

```bash
cd /opt/ec-to-alexa
git log --oneline -5                   # repérer le commit précédent
git checkout <commit>                  # code de la version précédente
docker compose up -d --build
# si la base a été modifiée par la nouvelle version : restaurer la sauvegarde (§10)
git checkout main                      # pour reprendre les mises à jour ensuite
```

### Passer d'une ancienne branche à `main` (une seule fois)

Si le serveur a été installé depuis `feature/alexa-skill` :

```bash
cd /opt/ec-to-alexa
git fetch origin
git checkout main
git pull --ff-only
./update.sh
```

## 8. Changer de nom de domaine

Exemple : passage de `ancien.mondomaine.fr` à `nouveau.mondomaine.fr`.

1. **DNS** : crée l'enregistrement `A` du nouveau nom (§3). Garde l'ancien
   quelques jours.
2. **Zoraxy** : ajoute une règle pour le nouveau nom (même cible
   `192.168.1.156:8443`, mêmes options, §5) et obtiens son certificat. Tu peux
   ajouter l'ancien nom en **alias** de la nouvelle règle pendant la
   transition.
3. **Serveur** :
   ```bash
   cd /opt/ec-to-alexa
   sed -i 's#^ECO_TO_ALEXA_PUBLIC_URL=.*#ECO_TO_ALEXA_PUBLIC_URL=#' .env   # si tu l'avais renseigné
   rm -rf router-tls                       # le certificat interne sera recréé pour le nouveau nom
   DOMAIN=nouveau.mondomaine.fr COMPOSE_PROFILES=router ./install.sh
   ```
   Le script affiche les nouvelles valeurs pour la console Alexa.
4. **Console Amazon** (détails dans le guide `configuration-alexa-skill`) :
   - **Endpoint** : `https://nouveau.mondomaine.fr/alexa` ;
   - **Account Linking** : Web Authorization URI
     `https://nouveau.mondomaine.fr/oauth/authorize`, Access Token URI
     `https://nouveau.mondomaine.fr/oauth/token`, **Your Redirect URLs** :
     `https://nouveau.mondomaine.fr/amazon/callback`. Ressaisis le **Your
     Secret**, la console l'exige à chaque enregistrement. **Save** ;
   - **Build skill**.
5. **Utilisateurs** : les comptes déjà liés continuent de fonctionner (leurs
   jetons sont sur le serveur). Amazon conseille de relier pour utiliser la
   nouvelle URL de jeton : sur le site, « Révoquer l'accès d'Alexa » puis
   « Lier mon compte Amazon ».
6. **Vérifier** (§6) avec le nouveau nom, puis retire l'ancien nom de Zoraxy
   et du DNS.

## 9. Changer d'IP ou de machine

### Le serveur change d'IP locale

```bash
cd /opt/ec-to-alexa
sed -i 's/^EXPOSE_ADDR=.*/EXPOSE_ADDR=NOUVELLE_IP/' .env
rm -rf router-tls
COMPOSE_PROFILES=router ./install.sh
```

Puis, dans Zoraxy, mets la nouvelle cible `NOUVELLE_IP:8443`.

### L'IP publique de la box change

Mets à jour l'enregistrement `A` du DNS. Rien à changer sur le serveur ni
dans la console Amazon.

### Déplacer l'installation sur une autre machine

1. Sur l'ancienne : `./update.sh` (crée une sauvegarde fraîche), puis copie
   **`.env`** et le dernier fichier de **`backups/`** en lieu sûr.
   ⚠️ Sans le `.env` d'origine (clé `ECO_TO_ALEXA_SECRET_KEY`), les liaisons
   École Directe chiffrées sont illisibles.
2. Sur la nouvelle : §4.1, copie le `.env` sauvegardé à la place de
   `.env.example`, adapte `EXPOSE_ADDR`, lance `COMPOSE_PROFILES=router
   ./install.sh`, puis restaure la base (§10).
3. Dans Zoraxy, change la cible de la règle.

## 10. Sauvegarder et restaurer

### Sauvegarde

`./update.sh` en fait une à chaque mise à jour. Pour une sauvegarde à la
demande, sans arrêter le service :

```bash
cd /opt/ec-to-alexa
mkdir -p backups
docker compose exec -T web python -c "import sqlite3,sys,tempfile; s=sqlite3.connect('/data/ec_to_alexa.sqlite3'); t=tempfile.NamedTemporaryFile(); d=sqlite3.connect(t.name); s.backup(d); d.close(); sys.stdout.buffer.write(open(t.name,'rb').read())" > "backups/ec_to_alexa-$(date +%Y%m%d-%H%M%S).sqlite3"
```

Sauvegarde aussi **`.env`** (il contient la clé de chiffrement) ailleurs que
sur le serveur, de façon sécurisée. Les fichiers de `backups/` contiennent
des données personnelles : garde-les privés.

### Restauration

```bash
cd /opt/ec-to-alexa
docker compose stop web oauth
docker compose run --rm --no-deps --user root -v "$PWD/backups:/restore:ro" web sh -c '
  rm -f /data/ec_to_alexa.sqlite3-wal /data/ec_to_alexa.sqlite3-shm &&
  cp /restore/ec_to_alexa-AAAAMMJJ-HHMMSS.sqlite3 /data/ec_to_alexa.sqlite3 &&
  chown appuser:appuser /data/ec_to_alexa.sqlite3'
docker compose up -d
```

La restauration doit se faire avec le `.env` qui a servi à créer la
sauvegarde (même `ECO_TO_ALEXA_SECRET_KEY`).

## 11. Changer les secrets

| Secret | Comment le changer | Conséquence |
| --- | --- | --- |
| `ALEXA_OAUTH_CLIENT_SECRET` (secret OAuth) | Supprime la ligne dans `.env`, relance `./install.sh`, colle le nouveau dans la console (Account Linking → Your Secret) puis **Save**. | Aucune pour les comptes déjà liés. |
| `ALEXA_APP_CLIENT_SECRET` (Alexa Client Secret) | Régénère-le dans la console si besoin, puis relance `./install.sh` (saisie masquée). | Seulement le bouton « Lier mon compte Amazon ». |
| `ECO_TO_ALEXA_SECRET_KEY` (clé de chiffrement) | Supprime la ligne, relance `./install.sh`. | **Toutes les liaisons École Directe deviennent illisibles** : chaque élève doit reconnecter École Directe sur le site. À ne faire qu'en cas de fuite. |

Ne colle jamais un secret dans un ticket, un chat ou un dépôt Git.

## 12. Référence du fichier `.env`

`install.sh` le remplit. Ne le commite jamais.

| Variable | Rôle |
| --- | --- |
| `DOMAIN` | Domaine public (certificat Caddy, URL affichées, certificat interne). |
| `COMPOSE_PROFILES` | `router` (derrière un proxy) ou `caddy` (HTTPS intégré). |
| `ECO_TO_ALEXA_SECRET_KEY` | Clé Fernet de chiffrement des liaisons École Directe. |
| `ECO_TO_ALEXA_DB` | Chemin de la base dans le conteneur (`/data/ec_to_alexa.sqlite3`). |
| `ECO_TO_ALEXA_SECURE_COOKIES` | `1` en HTTPS (défaut). `0` seulement pour un test local en HTTP. |
| `ALEXA_OAUTH_CLIENT_ID` | Client OAuth déclaré dans la console (`ec-to-alexa`). |
| `ALEXA_OAUTH_CLIENT_SECRET` | Secret OAuth (Your Secret dans la console). |
| `ALEXA_VENDOR_ID` | Vendor ID Amazon : autorise les trois URL de redirection Alexa. |
| `ALEXA_OAUTH_REDIRECT_URI` | URL de redirection supplémentaires (rarement utile). |
| `ALEXA_SKILL_ID` | Identifiant de la skill. Tant qu'il est vide, `/alexa` reste fermé. |
| `ALEXA_APP_CLIENT_ID`, `ALEXA_APP_CLIENT_SECRET` | « Alexa Client Id / Secret » pour le bouton « Lier mon compte Amazon ». |
| `ALEXA_SKILL_STAGE` | `development` tant que la skill n'est pas publiée, puis `live`. |
| `EXPOSE_ADDR` | Adresse d'écoute des ports sur l'hôte (`127.0.0.1`, ou l'IP du serveur si le proxy est ailleurs). |
| `WEB_PORT`, `OAUTH_PORT`, `ROUTER_PORT`, `ROUTER_TLS_PORT` | Ports sur l'hôte (8000, 8001, 8080, 8443). |
| `ECO_TO_ALEXA_TRUSTED_PROXIES` | Proxys autorisés à transmettre l'IP réelle (réseau Docker par défaut). |
| `ECO_TO_ALEXA_PUBLIC_URL` | URL publique du site si elle diffère de `https://DOMAIN`. |
| `ECO_TO_ALEXA_TIMEZONE` | Fuseau pour « aujourd'hui » et « demain » (`Europe/Paris`). |
| `ECO_TO_ALEXA_ALLOW_UNSIGNED_LOCAL` | `1` désactive la vérification de signature : **tests locaux uniquement**. |

## 13. Journaux et dépannage

```bash
cd /opt/ec-to-alexa
docker compose ps
docker compose logs --tail=50 oauth | grep ec-to-alexa    # requêtes Alexa acceptées/refusées, liaisons
docker compose logs --tail=50 web router
```

Le service `oauth` écrit une ligne `[ec-to-alexa] …` pour chaque requête
Alexa (acceptée, ou refusée avec la raison) et pour chaque liaison de compte,
sans jeton ni donnée scolaire.

| Symptôme | Cause probable et solution |
| --- | --- |
| Alexa : « Je ne parviens pas à accéder à la Skill demandée » et **aucune** ligne `/alexa` dans les journaux `oauth` | La requête n'arrive pas au serveur. Dans Zoraxy, **Minimum TLS Version = TLS v1.2** (§5). Vérifie aussi qu'aucun captcha, filtrage ou authentification ne s'applique au domaine. Le Device Log de la console Alexa affiche `SKILL_ENDPOINT_ERROR`. |
| Ligne `/alexa refusée : Identifiant de skill inattendu` | `ALEXA_SKILL_ID` du `.env` différent de la skill de la console : relance `./install.sh`. |
| Ligne `/alexa refusée : Requête Alexa périmée` | Horloge du serveur fausse (`date -u`) : active la synchronisation NTP de l'hôte. |
| Ligne `/alexa refusée : … certificat …` ou `Signature …` | Le serveur ne peut pas télécharger ou valider le certificat d'Amazon : vérifie l'accès sortant HTTPS vers `s3.amazonaws.com`. |
| Page Zoraxy 521 / 502 | Zoraxy ne joint pas le serveur : `EXPOSE_ADDR` resté à `127.0.0.1` alors que Zoraxy est ailleurs, mauvaise cible, ou « Require TLS » vers le port HTTP 8080 (utilise 8443). |
| « Trop de redirections » (308) | Le Caddy HTTPS intégré tourne derrière Zoraxy : relance `COMPOSE_PROFILES=router ./install.sh`. |
| `{"error": "not_found"}` ou « Cette page n'existe pas » sur `/oauth/authorize` | Répertoire virtuel dans Zoraxy : supprime-le, une seule règle d'hôte. |
| `{"error": "oauth_not_configured"}` | Vendor ID ou secret OAuth manquant : relance `./install.sh`. |
| `{"error": "alexa_not_configured"}` | `ALEXA_SKILL_ID` manquant : relance `./install.sh`. |
| Le bouton « Lier mon compte Amazon » n'apparaît pas | École Directe pas encore connecté, ou `ALEXA_APP_CLIENT_ID`/`SECRET` vides : relance `./install.sh`. |
| Conflit sur le port 8000 | Zoraxy sur la même machine : `WEB_PORT=18000` dans `.env`, puis `./install.sh`. |
| Conteneurs en boucle : « Fernet key must be 32 url-safe base64-encoded bytes » | Clé tronquée dans `.env`. Si aucune liaison n'existe encore : supprime la ligne `ECO_TO_ALEXA_SECRET_KEY=` et relance `./install.sh`. |
| « Je n'arrive pas à joindre École Directe » | Mot de passe École Directe changé ou QCM demandé : reconnecter École Directe sur le site. |
| Page vide quelques secondes après une mise à jour | Normal pendant le redémarrage des conteneurs : recharge la page. |
