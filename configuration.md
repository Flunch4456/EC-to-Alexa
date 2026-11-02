# Configuration EcoleDirecte Connect

Ce document décrit la configuration nécessaire pour tester le projet sans
exposer de secret.

## 1. Variables d'environnement

Sur un serveur, `./install.sh` crée et remplit `.env` (voir le
[README](./README.md)). En développement, copiez `.env.example` vers `.env` :

```powershell
Copy-Item .env.example .env
```

Ne mettez jamais un vrai mot de passe dans Git. Le fichier `.env` est ignoré
par Git. Les variables reconnues sont :

| Variable | Obligatoire | Description |
| --- | --- | --- |
| `DOMAIN` | Serveur | Nom de domaine public utilisé par Caddy pour le certificat HTTPS. |
| `ECO_TO_ALEXA_SECRET_KEY` | Oui | Clé Fernet de chiffrement des identifiants École Directe. |
| `ECO_TO_ALEXA_DB` | Non | Chemin de la base SQLite de la fondation comptes. |
| `ECO_TO_ALEXA_SECURE_COOKIES` | Non | `1` derrière HTTPS (cookies `Secure`), `0` en HTTP local. |
| `ALEXA_OAUTH_CLIENT_ID` | Pour OAuth | Identifiant client déclaré dans Alexa (`ec-to-alexa` par défaut). |
| `ALEXA_OAUTH_CLIENT_SECRET` | Recommandé | Secret client déclaré dans Alexa, vérifié sur `/oauth/token`. |
| `ALEXA_VENDOR_ID` | Pour OAuth | Vendor ID Amazon ; autorise les trois URL de redirection Alexa (Europe, Amérique, Japon). |
| `ALEXA_OAUTH_REDIRECT_URI` | Non | URL de redirection supplémentaires, séparées par des virgules. |
| `ALEXA_SKILL_ID` | Recommandé | Identifiant `amzn1.ask.skill...` ; les requêtes d'une autre skill sont refusées. |
| `EXPOSE_ADDR` | Non | Adresse d'écoute des ports 8000/8001 sur l'hôte (`127.0.0.1` par défaut). |
| `ECOLE_DIRECTE_IDENTIFIANT` | Non | Identifiant utilisé par le script CLI. |
| `ECOLE_DIRECTE_MOT_DE_PASSE` | Non | Mot de passe utilisé par le script CLI. |

Tant que `ALEXA_OAUTH_CLIENT_ID` et `ALEXA_VENDOR_ID` (ou
`ALEXA_OAUTH_REDIRECT_URI`) ne sont pas renseignés, `oauth_server.py` démarre
quand même : le webhook `/alexa` répond, et les routes `/oauth/*` renvoient
`503 oauth_not_configured`.

Générez une clé Fernet une seule fois et stockez-la uniquement dans `.env` ou
dans le gestionnaire de secrets du serveur :

```powershell
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Placez la valeur obtenue dans `ECO_TO_ALEXA_SECRET_KEY`. Sans cette clé, le
serveur refuse de démarrer afin de ne jamais enregistrer des identifiants
EcoleDirecte en clair.

Pour un test ponctuel sous PowerShell, préférez les variables de session :

```powershell
$env:ECOLE_DIRECTE_IDENTIFIANT = "votre_identifiant"
$env:ECOLE_DIRECTE_MOT_DE_PASSE = "votre_mot_de_passe"
python .\ecole_directe.py
```

Les identifiants sont transmis à EcoleDirecte uniquement pendant cette
connexion et ne sont pas enregistrés par `ecole_directe.py`.

## 2. Ce qui est actuellement testable

La connexion réelle EcoleDirecte et le QCM de sécurité sont utilisables avec
un compte autorisé :

```powershell
python .\web.py
```

Ouvrez `http://127.0.0.1:8000`, connectez-vous, puis choisissez un service.
Le projet ne contourne ni CAPTCHA ni QCM : si EcoleDirecte demande une
vérification, la réponse doit être fournie manuellement.

La fondation de [`application.py`](./application.py) peut être testée sans
compte réel :

```powershell
python -m unittest discover -p "test_*.py" -v
```

Elle crée des utilisateurs, hache les mots de passe et sépare les jetons web
des jetons Alexa. Elle ne persiste volontairement pas encore les identifiants
EcoleDirecte.

## 3. Configuration Amazon Alexa

### État actuel

Un fournisseur OAuth 2.0 avec code d'autorisation est maintenant disponible
dans `oauth_server.py` pour le test derrière un reverse proxy HTTPS. Le serveur
local écoute sur `127.0.0.1:8001` et expose `/oauth/authorize`,
`/oauth/token`, `/oauth/revoke` et `/alexa`. Il faut encore brancher le
registre de services EcoleDirecte persistant et effectuer la validation
Amazon sur un domaine réel avant publication.

Pour un test local, lancez `account_server.py` sur le port 8000 et `oauth_server.py` sur
le port 8001. La contrainte HTTPS de l'URI de redirection reste volontaire :
utilisez un tunnel HTTPS de développement ou testez le fournisseur directement
avec les tests unitaires.

### Créer la skill « Mon Carnet Scolaire »

Le modèle vocal est prêt dans [`skill-package/`](./skill-package) :
[`skill.json`](./skill-package/skill.json) (manifeste) et
[`fr-FR.json`](./skill-package/interactionModels/custom/fr-FR.json)
(phrases et intents). Phrase d'ouverture : « Alexa, ouvre mon carnet
scolaire ».

| Ce que l'élève dit | Intent | Réponse |
| --- | --- | --- |
| « mon emploi du temps », « j'ai quoi demain », « j'ai cours lundi » | `GetTodayScheduleIntent`, `GetTomorrowScheduleIntent`, `GetScheduleIntent` | Nombre de cours, heures de début et de fin, matière et salle de chaque cours, cours annulés. |
| « mon prochain cours » | `GetNextClassIntent` | Matière, heure, salle et professeur. |
| « mes devoirs », « mes devoirs pour jeudi » | `GetHomeworkIntent` | Contenu de chaque devoir par matière (par défaut : prochain jour de cours), devoirs déjà faits, évaluations. |
| « mes notes » | `GetGradesIntent` | Les trois dernières notes avec matière et intitulé. |
| « mes absences » | `GetAbsencesIntent` | Absences, retards, nombre non justifié, le plus récent. |
| « mes messages » | `GetMessagesIntent` | Nouveaux messages avec expéditeur et objet. |

La création de la skill dans la console Amazon, l'Account Linking et
l'activation dans l'application Alexa sont décrits pas à pas dans le
[README](./README.md) (étapes 4 à 6). `./install.sh` affiche les valeurs exactes
à saisir (URL, Client ID, Client Secret) et enregistre ensuite
`ALEXA_SKILL_ID` et `ALEXA_VENDOR_ID`.

Le point de terminaison `/oauth/token` accepte les identifiants client en
HTTP Basic (recommandé par Amazon) ou dans le corps de la requête, et vérifie
`ALEXA_OAUTH_CLIENT_SECRET` lorsqu'il est défini. Le Vendor ID suffit pour
autoriser les trois URL de redirection d'Amazon, quelle que soit la région du
compte Alexa.

Alexa envoie le jeton OAuth dans le corps de la requête
(`context.System.user.accessToken`) : le webhook le lit là, et répond avec
une carte **LinkAccount** si le compte n'est pas lié.

Chaque requête reçue sur `/alexa` est vérifiée par
[`alexa_verify.py`](./alexa_verify.py) selon les règles d'Amazon :
`SignatureCertChainUrl` en `https://s3.amazonaws.com/echo.api/…` (port 443),
certificat valide dont le SAN contient `echo-api.amazon.com` et dont la
chaîne remonte à une autorité racine de confiance (paquet `certifi`),
signature `Signature-256` (SHA-256) du corps brut, horodatage
`request.timestamp` à moins de 150 secondes et `applicationId` égal à
`ALEXA_SKILL_ID`. Le certificat est mis en cache une heure.
`ECO_TO_ALEXA_ALLOW_UNSIGNED_LOCAL=1` désactive uniquement la signature,
pour des tests locaux ; ne l'activez jamais sur un serveur exposé.

Le serveur `account_server.py` vérifie la connexion EcoleDirecte avant de
chiffrer les identifiants. Si EcoleDirecte demande son QCM, la question est
affichée sur le tableau de bord ; l'élève y répond lui-même (aucun
contournement). L'UUID d'appareil et les valeurs `cn`/`cv` obtenues sont
chiffrés avec la liaison afin que les reconnexions d'Alexa ne redéclenchent
pas le QCM.

Après une connexion réussie, l'identifiant interne du compte web est affiché
dans le tableau de bord, et l'identifiant élève est enregistré uniquement
depuis la réponse de connexion EcoleDirecte. Il n'est jamais accepté comme
champ libre du formulaire : cela empêche d'utiliser l'identifiant scolaire
d'une autre personne pour construire les endpoints.

Ne liez jamais un compte sur la base d'un nom, d'un e-mail transmis par Alexa
ou d'un identifiant élève seul. N'utilisez pas de mot de passe EcoleDirecte
comme secret OAuth.

## 4. Limites de sécurité à respecter

- Utiliser HTTPS derrière un reverse proxy en production.
- Définir une clé de secrets hors du dépôt avant de persister des connexions.
- Ajouter CSRF, limitation de débit, rotation/révocation des sessions et
  vérification d'e-mail avant une mise en ligne.
- Ne pas écrire les requêtes scolaires, mots de passe ou jetons dans les
  journaux.
- Ne jamais utiliser les données d'un autre `user_id` pour répondre à Alexa.
- Ne pas activer de fonctionnalité qui répond à la place d'un élève dans une
  évaluation officielle.
