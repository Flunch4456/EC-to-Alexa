# Configuration EcoleDirecte Connect

Ce document décrit la configuration nécessaire pour tester le projet sans
exposer de secret.

## 1. Variables d'environnement

Copiez `.env.example` vers `.env` en développement :

```powershell
Copy-Item .env.example .env
```

Ne mettez jamais un vrai mot de passe dans Git. Le fichier `.env` est ignoré
par Git. Les variables reconnues par le client actuel sont :

| Variable | Obligatoire | Description |
| --- | --- | --- |
| `ECOLE_DIRECTE_IDENTIFIANT` | Non | Identifiant utilisé par le script CLI. |
| `ECOLE_DIRECTE_MOT_DE_PASSE` | Non | Mot de passe utilisé par le script CLI. |
| `ECO_TO_ALEXA_DB` | Non | Chemin de la base SQLite de la fondation comptes. |
| `ALEXA_OAUTH_CLIENT_ID` | Pour OAuth | Identifiant client déclaré dans Alexa. |
| `ALEXA_OAUTH_REDIRECT_URI` | Pour OAuth | URI HTTPS autorisée par Alexa, exactement à l'identique. |
| `ALEXA_SKILL_ID` | Recommandé | Identifiant `amzn1.ask.skill...` ; les requêtes d'une autre skill sont refusées. |

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

1. Dans la [console Alexa Developer](https://developer.amazon.com/alexa/console/ask),
   cliquez sur **Create Skill**, nom « Mon Carnet Scolaire », langue
   **French (FR)**, type **Custom**, hébergement **Provision your own**.
2. Onglet **Build → Interaction Model → JSON Editor** : collez le contenu de
   `skill-package/interactionModels/custom/fr-FR.json`, puis **Save** et
   **Build skill**.
3. **Build → Endpoint** : choisissez **HTTPS**, URL
   `https://<votre-domaine>/alexa`, certificat « My development endpoint has a
   certificate from a trusted certificate authority ». Alexa n'accepte pas
   `127.0.0.1` : utilisez le domaine du `Caddyfile` ou un tunnel HTTPS
   (Cloudflare Tunnel, ngrok) vers le port 8001.
4. Copiez l'**identifiant de la skill** (`amzn1.ask.skill...`) dans
   `ALEXA_SKILL_ID` : le webhook refuse les requêtes d'une autre skill et
   celles de plus de 150 secondes.
5. **Build → Account Linking** : activez-le, type **Auth Code Grant** :
   - Authorization URI : `https://<votre-domaine>/oauth/authorize`
   - Access Token URI : `https://<votre-domaine>/oauth/token`
   - Client ID : la valeur choisie pour `ALEXA_OAUTH_CLIENT_ID`
   - Client Secret : une valeur quelconque (non vérifiée par cette version)
   - Authentication Scheme : **Credentials in request body**
   - Scope : `ecole`
   Copiez l'une des **Alexa Redirect URLs** affichées en bas de page
   (`https://pitangui.amazon.com/api/skill/link/...` ou
   `https://layla.amazon.com/...`) dans `ALEXA_OAUTH_REDIRECT_URI`, puis
   redémarrez `oauth_server.py`.
6. Créez votre compte et connectez École Directe sur `account_server.py`,
   puis dans l'application Alexa : **Skills → Vos skills → Développement →
   Mon Carnet Scolaire → Activer**, et connectez-vous avec ce compte.
7. Onglet **Test** de la console (mode Development) : tapez « ouvre mon
   carnet scolaire » puis « mes devoirs pour demain ».

Alexa envoie le jeton OAuth dans le corps de la requête
(`context.System.user.accessToken`) : le webhook le lit là, et répond avec
une carte **LinkAccount** si le compte n'est pas lié. La vérification de
signature des requêtes Amazon (`Signature-256`) reste à ajouter avant une
publication publique ; elle n'est pas nécessaire en mode Development.

Le serveur `account_server.py` vérifie la connexion EcoleDirecte avant de
chiffrer les identifiants. Un compte qui déclenche le QCM doit d'abord
terminer ce QCM manuellement avec `web.py`, car aucun contournement n'est
autorisé.

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
