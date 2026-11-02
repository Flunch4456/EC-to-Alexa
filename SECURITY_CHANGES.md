# Changements de sécurité

## Fichiers modifiés

| Fichier | Problème | Correction | Justification |
| --- | --- | --- | --- |
| `.dockerignore` | Risque d’embarquer secrets/cache/bases dans l’image | Exclusions explicites (`.env*`, SQLite, logs, backups, caches, venv, `.git`) avec exception `.env.example` | Réduit la surface de fuite lors des builds Docker |
| `Dockerfile` | `COPY . .` importait tout le dépôt | Copies explicites des fichiers nécessaires, utilisateur non-root conservé | Build déterministe et plus sûr |
| `docker-compose.yml` | Ports publiés sur toutes interfaces par défaut | Publication sur `127.0.0.1` + réseau interne Docker + bind explicite intra-conteneur | Évite exposition réseau implicite |
| `application.py` | Concurrence SQLite/OAuth, énumération temporelle, révocation partielle | Verrou thread-safe, échange OAuth atomique transactionnel, vérification factice mot de passe, purge bornée des jetons, révocation Alexa par utilisateur | Corrige course OAuth réelle et limite abus/auth timing |
| `oauth_server.py` | Lecture HTTP fragile, contrôles OAuth/Alexa incomplets | Lecture unique du body, validation stricte `Content-Length`/`Content-Type`/charset, `grant_type`/`client_secret`, rate limit partagé, contrôle skill/timestamp/token, mode fail-closed Alexa | Réduit abus HTTP/OAuth et refuse Alexa non validé |
| `account_server.py` | CSRF incomplet (login/logout), rate limit non thread-safe, confiance implicite XFF | CSRF signé stateless (incluant login/logout), logout en POST, rate limit partagé borné, XFF accepté seulement depuis proxy approuvé, cookies `Secure` par défaut | Protection cohérente contre CSRF/bruteforce sans lockout permanent |
| `web.py` | Sessions/QCM non bornés, pas de CSRF, écoute large | TTL + nettoyage borné QCM/sessions, limite essais, CSRF, validation HTTP stricte, en-têtes anti-framing, bind loopback par défaut | Réduit DoS mémoire et actions cross-site |
| `test_application.py` | Non-régression manquante sur OAuth renforcé | Tests `client_secret`, `grant_type`, concurrence de code OAuth | Couvre les correctifs critiques |
| `test_http_security.py` | Pas de tests HTTP sécurité | Tests de validation `Content-Length`, `Content-Type`, CSRF, fail-closed Alexa | Empêche régression sur vérifications serveur |
| `README.md`, `installation.md`, `configuration.md`, `.env.example` | Documentation contradictoire (écoute globale, variables manquantes) | Mise à jour des variables, topologie loopback par défaut, exigences OAuth/Alexa/proxy | Évite configuration dangereuse par défaut |

## Configuration requise

- `ECO_TO_ALEXA_SECRET_KEY` (obligatoire)
- `ALEXA_OAUTH_CLIENT_ID`, `ALEXA_OAUTH_CLIENT_SECRET`, `ALEXA_OAUTH_REDIRECT_URI`
- `ALEXA_SKILL_ID` pour contrôle d’application Alexa
- `ECO_TO_ALEXA_TRUSTED_PROXIES` pour autoriser `X-Forwarded-For`
- `ALEXA_PROXY_SIGNATURE_VALIDATION=1` + proxy interne qui ajoute `X-Alexa-Signature-Verified: 1`

## Migrations / compatibilité

- Pas de migration de schéma SQLite.
- Changement de comportement : cookies `Secure` activés par défaut (désactivation locale explicite via `ECO_TO_ALEXA_ALLOW_INSECURE_HTTP=1`).
- OAuth token/revoke exigent désormais `client_secret`.

## Commandes exécutées

```bash
python -m unittest test_application.py test_http_security.py -v
python -m unittest discover -p "test_*.py" -v
```

## Limites et risques résiduels

- La validation cryptographique complète Amazon (chaîne/certificat/signature) est déléguée à un composant maintenu en frontal ; le serveur applicatif refuse par défaut sans signal explicite de validation.
- Serveurs basés sur `http.server` : adaptés au self-hosting contrôlé derrière proxy, pas à une exposition Internet directe sans durcissement supplémentaire.
- Aucun engagement “zéro faille” : ces corrections réduisent des vulnérabilités avérées, mais un audit continu reste nécessaire.
