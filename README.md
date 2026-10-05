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

Ouvrez ensuite http://127.0.0.1:8000. Le formulaire lance la connexion avec
les identifiants saisis. Apres la connexion, choisissez la reponse souhaitee :
emploi du temps, notes, absences ou messages. Si Ecole Directe renvoie le code
250, le formulaire affiche d'abord le QCM de securite, puis termine le login
avec les valeurs `cn` et `cv` retournees par l'API avant d'afficher ces choix.
Les identifiants restent en memoire pendant la requete et ne sont pas ecrits
dans un fichier.
