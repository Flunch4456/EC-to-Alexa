# Changements de sécurité

## Périmètre et état

Cette révision corrige des défauts observés dans le dépôt ; elle ne prétend
pas garantir l'absence de vulnérabilité. Le fichier `.env` local n'est pas
une preuve de fuite et aucun secret réel n'est inclus dans le dépôt. Aucun
pull, merge ou déploiement n'est effectué.

## Fichiers modifiés

- [`application.py`](./application.py) : ajoute la lecture HTTP bornée, la
  limitation thread-safe, les adresses proxy approuvées, les jetons CSRF
  persistants, le faux calcul PBKDF2 pour les comptes absents, le nettoyage
  borné et l'échange de code OAuth conditionnel dans une transaction. La
  déconnexion EcoleDirecte supprime aussi les jetons Alexa ; Alexa recharge
  désormais la liaison courante au lieu de conserver un client obsolète.
- [`account_server.py`](./account_server.py) : applique la limitation aux
  formulaires, refuse les corps invalides, exige CSRF pour toute modification
  et la déconnexion, utilise des cookies sécurisés par défaut et écoute sur
  loopback hors configuration explicite.
- [`oauth_server.py`](./oauth_server.py) : valide `grant_type`, accepte le
  secret client OAuth par Basic ou formulaire, applique la même limitation,
  borne les corps, et refuse Alexa sans skill ID, fraîcheur et signature
  configurés. Les URL de certificat sont limitées à HTTPS et aux hôtes Amazon
  attendus.
- [`web.py`](./web.py) : borne les défis QCM et sessions, nettoie les entrées
  expirées, refuse les corps ambigus et les origines non locales, et écoute
  sur loopback par défaut.
- [`Dockerfile`](./Dockerfile) : remplace `COPY . .` par des copies explicites,
  conserve l'utilisateur non-root et n'embarque plus les fichiers ignorés.
- [`.dockerignore`](./.dockerignore) : exclut environnements, caches, bases,
  journaux, sauvegardes, `.git` et `.env` avec exception de `.env.example`.
- [`docker-compose.yml`](./docker-compose.yml) : publie les ports sur
  `127.0.0.1` par défaut et écoute sur toutes les interfaces uniquement à
  l'intérieur du conteneur.
- [`.env.example`](./.env.example) : documente les paramètres sans valeur
  secrète, dont le secret OAuth, le skill ID et les proxies approuvés.
- [`test_application.py`](./test_application.py), [`test_security.py`](./test_security.py) :
  régression de concurrence OAuth, révocation et validation des corps HTTP
  sans comptes ni services réels.
- [`README.md`](./README.md), [`configuration.md`](./configuration.md),
  [`installation.md`](./installation.md) : corrigent les affirmations sur la
  persistance, loopback, topologies Docker, proxy et limites.

## Configuration obligatoire

En production, fournir par gestionnaire de secrets ou fichier monté avec
permissions strictes : `ECO_TO_ALEXA_SECRET_KEY`,
`ALEXA_OAUTH_CLIENT_SECRET` et `ALEXA_SKILL_ID`. Configurer HTTPS sur le
reverse proxy et renseigner `ECO_TO_ALEXA_TRUSTED_PROXIES` seulement avec ses
adresses immédiates. `ECO_TO_ALEXA_ALLOW_UNSIGNED_LOCAL=1` est réservé aux
tests locaux et ne doit jamais être activé en production.

## Migration et sauvegarde

La migration est additive : création de `csrf_tokens` et ajout des colonnes
existantes sont effectués par SQLite au démarrage. Sauvegarder le volume
SQLite et conserver la clé Fernet avant la mise à jour ; sans cette clé, les
connexions chiffrées ne sont pas récupérables. Tester la restauration sur une
copie avant toute migration réelle.

## Commandes exécutées

```text
python -m unittest discover -p 'test_*.py' -v
python -m py_compile application.py account_server.py oauth_server.py web.py ecole_directe.py
git diff --check
```

Les tests réussissent. Aucun appel à EcoleDirecte, Alexa ou compte réel n'a
été effectué. Aucun outil de scan externe n'est configuré dans ce dépôt.

## Limites et risques résiduels

Le serveur standard-library reste destiné au loopback ou à un reverse proxy
maintenu, avec timeouts et limites à appliquer aussi au proxy. La validation
cryptographique complète de la chaîne de certificat Amazon doit encore être
branchée et vérifiée contre la documentation Alexa actuelle avant production ;
le endpoint refuse par défaut les requêtes non signées mais cette protection
ne constitue pas une implémentation complète de la chaîne Amazon. Le partage
du rate limiter entre plusieurs processus nécessite un stockage partagé
externe ou une terminaison d'authentification commune ; ne déployer qu'un
seul worker tant que cette politique n'est pas fournie.
