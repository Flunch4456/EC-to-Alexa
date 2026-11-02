# EC-to-Alexa

Client Python minimal pour se connecter a l'API Ecole Directe et recuperer sa
reponse JSON. Le client suit le protocole BlocksDirecte actuel : recuperation
du cookie GTK, puis connexion sur `v3/login.awp?v=8.0.0` avec l'en-tete
`X-Gtk` et un User-Agent mobile.
Il utilise aussi un UUID d'appareil genere pour la session et active
`sesouvenirdemoi`, comme le client Papillon.

## Utilisation

1. Copiez `.env.example` en `.env` et remplacez les deux valeurs par vos
	identifiants. Le fichier `.env` est ignore par Git.
2. Exportez les variables dans votre terminal. Sous PowerShell :

```powershell
$env:ECOLE_DIRECTE_IDENTIFIANT = "mon_identifiant"
$env:ECOLE_DIRECTE_MOT_DE_PASSE = "mon_mot_de_passe"
python .\ecole_directe.py
```

Le script appelle `login.awp`, conserve le jeton `X-Token` et affiche la
reponse complete de l'API. Le client peut ensuite appeler un endpoint :

```python
from ecole_directe import EcoleDirecteClient

client = EcoleDirecteClient("mon_identifiant", "mon_mot_de_passe")
client.login()
retour = client.get("eleves/IDENTIFIANT/notes.awp")
print(retour)
```

Les tests n'effectuent aucun appel reseau :

```powershell
python -m unittest
```

## Interface web

Pour ouvrir le formulaire de connexion dans le navigateur :

```powershell
.\.venv\Scripts\python.exe .\web.py
```

Ouvrez ensuite http://127.0.0.1:8000. Par défaut, le serveur écoute en loopback
(`127.0.0.1`) pour éviter une exposition réseau involontaire. Le formulaire lance la connexion avec
les identifiants saisis. Apres la connexion, choisissez la reponse souhaitee :
emploi du temps, notes, absences ou messages. Si Ecole Directe renvoie le code
250, le formulaire affiche d'abord le QCM de securite, puis termine le login
avec les valeurs `cn` et `cv` retournees par l'API avant d'afficher ces choix.
Les identifiants restent en memoire pendant la requete dans `web.py`. Pour le serveur de comptes avec stockage chiffré, utilisez
`account_server.py` et configurez `ECO_TO_ALEXA_SECRET_KEY` ; voir
[`installation.md`](./installation.md).

## Fondation compte et Alexa

Configuration détaillée : [`configuration.md`](./configuration.md).
Installation locale, Docker et serveur : [`installation.md`](./installation.md).
Suivi des étapes : [`CHECKLIST.md`](./CHECKLIST.md).

[`application.py`](./application.py) contient les primitives de la future
plateforme : comptes SQLite, mots de passe hachés avec PBKDF2, jetons web et
jetons Alexa séparés, isolation par `user_id`, ainsi qu'une façade
`EcoleDirecteService` pour les données scolaires. `AlexaSkill` traite
uniquement un jeton Alexa valide et ne renvoie pas de données lorsqu'aucune
connexion EcoleDirecte n'est associée.

Le serveur [`account_server.py`](./account_server.py) fournit les comptes,
cookies (`HttpOnly`, `SameSite=Lax`, `Secure` par défaut), CSRF, limitation de débit et stockage chiffré des connexions.
Le serveur [`oauth_server.py`](./oauth_server.py) fournit les
routes OAuth (`/oauth/authorize`, `/oauth/token`, `/oauth/revoke`) et
le webhook `/alexa`. L'échange OAuth exige `client_id`, `client_secret`,
`grant_type=authorization_code` et l'URI de redirection exacte.
Le webhook Alexa est fail-closed : sans validation de signature déléguée
à un proxy interne approuvé (`ALEXA_PROXY_SIGNATURE_VALIDATION=1` + en-tête
`X-Alexa-Signature-Verified: 1`), la requête est refusée. Aucun contournement de QCM,
CAPTCHA ou autre protection n'est implémenté.

Les fichiers [`Dockerfile`](./Dockerfile) et
[`docker-compose.yml`](./docker-compose.yml) utilisent désormais des copies
explicites, un `.dockerignore` défensif, un utilisateur non-root, et des ports
publiés sur loopback par défaut (`127.0.0.1`). Pour d'autres topologies
(LAN, reverse proxy public), consultez [`installation.md`](./installation.md).

Pour exécuter tous les tests avec les données fictives :

```powershell
python -m unittest discover -p "test_*.py" -v
```
