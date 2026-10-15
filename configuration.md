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

### Ce qui devra être configuré avant un vrai test sur un appareil

1. Créer une skill personnalisée en français dans la [console Alexa
   Developer](https://developer.amazon.com/alexa/console).
2. Définir les intents du modèle vocal : `GetHomeworkIntent`,
   `GetAbsencesIntent`, `GetMessagesIntent`, `HelpIntent`, `CancelIntent` et
   `StopIntent`.
3. Déployer une API HTTPS publique avec un certificat valide. Alexa n'accepte
   pas le serveur local `127.0.0.1`.
4. Déployer `oauth_server.py` derrière HTTPS. Les chemins OAuth sont :
   `/oauth/authorize`, `/oauth/token` et `/oauth/revoke`.
5. Déclarer dans Alexa l'URL d'autorisation, l'URL de jeton, le client OAuth,
   les scopes et l'URL de redirection exactement fournie par Amazon.
6. Après consentement, émettre un jeton opaque aléatoire dont seule
   l'empreinte est stockée côté serveur, puis retrouver l'utilisateur par son
   `user_id` interne.
7. Configurer l'endpoint HTTPS de la skill et vérifier la signature ou le
   certificat de la requête selon les exigences Amazon actuelles.
8. Tester d'abord en mode développement avec un compte fictif et les tests
   simulés du dépôt.

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
