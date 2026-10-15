# Installation du serveur

## Prérequis

- Python 3.12 ou plus récent ;
- Git ;
- Docker Desktop si vous choisissez le déploiement Docker ;
- un compte EcoleDirecte de test autorisé ;
- pour Alexa en production : un domaine DNS et HTTPS public.

## Installation locale Windows

Depuis PowerShell :

```powershell
git clone https://github.com/Flunch4456/EC-to-Alexa.git
Set-Location .\EC-to-Alexa
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
Copy-Item .env.example .env
.\.venv\Scripts\python.exe -m unittest discover -p "test_*.py" -v
.\.venv\Scripts\python.exe .\account_server.py
```

Le site local est disponible sur `http://127.0.0.1:8000`. N'utilisez pas le
serveur standard-library directement sur Internet : il n'a ni TLS ni les
protections nécessaires à un service public.

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

## Déploiement serveur Linux

1. Installer Docker Engine et le plugin `docker compose`.
2. Cloner le dépôt dans un répertoire de déploiement non accessible en
   écriture par le serveur web.
3. Créer `.env` avec des permissions restrictives :

   ```bash
   chmod 600 .env
   ```

4. Configurer le DNS, par exemple `ecole.example.fr`, vers l'adresse du
   serveur.
5. Configurer un reverse proxy (Caddy, Nginx ou Traefik) pour terminer TLS et
   transmettre vers `127.0.0.1:8000`.
6. Activer les sauvegardes chiffrées du volume SQLite avant toute donnée
   réelle.
7. Démarrer avec `docker compose up -d` et vérifier les journaux.

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
