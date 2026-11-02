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

Ouvrez ensuite http://127.0.0.1:8000. Le serveur écoute par défaut sur
loopback. Pour un autre appareil, configurez explicitement
`ECO_TO_ALEXA_HOST` derrière un reverse proxy HTTPS ; ne comptez pas sur le
pare-feu seul. Le formulaire lance la connexion avec
les identifiants saisis. Apres la connexion, choisissez la reponse souhaitee :
emploi du temps, notes, absences ou messages. Si Ecole Directe renvoie le code
250, le formulaire affiche d'abord le QCM de securite, puis termine le login
avec les valeurs `cn` et `cv` retournees par l'API avant d'afficher ces choix.
Les identifiants de cette interface restent en mémoire pendant la session
bornée et ne sont pas écrits dans un fichier. Le serveur de comptes chiffre
la liaison persistante avec Fernet. Pour ce serveur, utilisez
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
cookies, CSRF, limitation de débit et stockage chiffré des connexions.
Le serveur [`oauth_server.py`](./oauth_server.py) fournit maintenant les
routes OAuth de test (`/oauth/authorize`, `/oauth/token`, `/oauth/revoke`) et
le webhook `/alexa`. Le certificat HTTPS, l'identifiant de skill et la
validation Amazon doivent être configurés avec un vrai compte développeur. Les
identifiants EcoleDirecte associés au compte sont chiffrés par Fernet avec une
clé injectée hors du code ; une déconnexion supprime la liaison et révoque les
jetons Alexa associés. Aucun contournement de QCM,
CAPTCHA ou autre protection n'est implémenté.

Les fichiers [`Dockerfile`](./Dockerfile) et
[`docker-compose.yml`](./docker-compose.yml) préparent un lancement derrière
un reverse proxy HTTPS maintenu. La validation Alexa et les limites
documentées doivent être complétées avant toute exposition publique.

Pour exécuter tous les tests avec les données fictives :

```powershell
python -m unittest discover -p "test_*.py" -v
```
