# EC-to-Alexa — skill « Mon Carnet Scolaire »

EC-to-Alexa relie un compte **École Directe** à **Alexa**. Un élève demande à
son enceinte son emploi du temps, ses devoirs ou ses notes, et Alexa lui lit
la réponse, construite à partir de ses vraies données École Directe.

> « Alexa, demande à mon carnet scolaire mon emploi du temps demain. »
>
> — « Demain, tu as 3 cours, de 8 heures 10 à 17 heures. À 8 heures 10,
> Pratique professionnelle en salle C305, à 10 heures 20, Français en salle
> B309 et à 15 heures 40, Prévention santé environnement en salle B309.
> Attention, le cours d'Économie gestion de 14 heures est annulé. »

## Les guides

Ce dépôt est organisé en trois branches :

| Branche | Contenu |
| --- | --- |
| **`main`** (celle-ci) | Le code à jour et la description du projet. C'est la branche que le serveur doit suivre. |
| [**`installation-serveur`**](https://github.com/Flunch4456/EC-to-Alexa/tree/installation-serveur) | Installer, configurer et mettre à jour le serveur : Docker, Zoraxy ou Caddy, box, DNS, mises à jour, sauvegardes, changement de nom de domaine, dépannage. |
| [**`configuration-alexa-skill`**](https://github.com/Flunch4456/EC-to-Alexa/tree/configuration-alexa-skill) | Créer et configurer la skill dans la console Amazon : modèle vocal, endpoint, liaison de compte, tests, dépannage côté Alexa. |

Ordre conseillé pour une nouvelle installation : **installation du
serveur**, puis **configuration de la skill**, puis le site pour lier son
compte.

## Ce que sait faire la skill

| Tu dis à Alexa | Elle répond |
| --- | --- |
| « Alexa, ouvre mon carnet scolaire » | Un message d'accueil, puis elle attend ta question. |
| « … mon emploi du temps », « j'ai quoi demain », « j'ai cours lundi » | Les cours du jour demandé, avec l'heure, la matière, la salle et les cours annulés. |
| « … mon prochain cours » | La matière, l'heure, la salle et le professeur. |
| « … mes devoirs », « mes devoirs pour jeudi » | Le contenu de chaque devoir, par défaut pour le prochain jour de cours. |
| « … mes prochaines évaluations » | Les contrôles annoncés dans les 14 prochains jours. |
| « … mes notes » | Les dernières notes. |
| « … mes absences » | Absences, retards et ce qui n'est pas justifié. |
| « … mes messages » | Les nouveaux messages, avec l'expéditeur et l'objet. |
| « aide » | La liste des questions possibles. |
| « stop », « annuler » | « À bientôt. » |

Les phrases sont reconnues sous plusieurs formes (« j'ai quoi après », « le
cahier de texte pour lundi », « à quelle heure je commence demain »…) : la
liste complète est dans
[`skill-package/interactionModels/custom/fr-FR.json`](./skill-package/interactionModels/custom/fr-FR.json).
Le nom d'invocation est **« mon carnet scolaire »** ; « aujourd'hui » et
« demain » sont calculés à l'heure de Paris (`ECO_TO_ALEXA_TIMEZONE`).

Cas particuliers :

| Situation | Ce que fait Alexa |
| --- | --- |
| Compte Amazon pas encore lié | Elle demande de lier le compte sur le site EC to Alexa et envoie une carte avec l'adresse du site dans l'application Alexa. |
| École Directe pas connecté sur le site | Elle demande de le connecter depuis le site. |
| École Directe repose une question de sécurité **déjà connue** | Elle y répond toute seule et donne la réponse demandée, sans rien demander à l'élève. |
| École Directe pose une question de sécurité **nouvelle** | Elle demande d'aller y répondre une fois sur le site, puis de redemander. |
| École Directe ne répond pas | Elle le dit, et propose de reconnecter le compte sur le site si le problème dure. |
| Phrase non comprise | « Je n'ai pas compris », puis l'aide. |

Les intitulés École Directe sont reformulés pour être lus à voix haute :
« Prevent.-sante-env. » devient « Prévention santé environnement », et
« B309 38pl » devient « en salle B309 ».

## Le site web

Chaque élève utilise le site du serveur (par exemple
`https://ec-to-alexa.mondomaine.fr`) pour :

1. **créer son compte EC-to-Alexa** (e-mail et mot de passe propres au
   service) ;
2. **connecter École Directe** : identifiants vérifiés auprès d'École
   Directe, puis chiffrés sur le serveur. Si École Directe pose sa question
   de sécurité (QCM), elle s'affiche sur la page. La réponse acceptée est
   mémorisée **sous forme hachée** : quand École Directe repose une question
   déjà connue, le serveur y répond tout seul, à la voix comme sur le site.
   Si la question est nouvelle, Alexa demande d'aller y répondre une fois sur
   le site (bouton « Répondre à la question de sécurité ») ;
3. **lier son compte Amazon** avec le bouton « Lier mon compte Amazon » :
   connexion sur la page officielle d'Amazon, puis la skill est activée et
   liée automatiquement. Le tableau de bord affiche ensuite le compte Alexa
   associé, la date de liaison et la dernière demande. C'est ce qui permet de
   savoir quel compte Amazon lit quel École Directe.

Le site propose aussi :

- **« Essayer sans enceinte »** : affiche la réponse qu'Alexa donnerait, avec
  les vraies données, pour chaque type de question ;
- **« Répondre à la question de sécurité »** : apparaît quand Alexa a
  rencontré une question qu'elle ne connaissait pas ;
- **« Oublier ces réponses »** : efface les réponses de sécurité mémorisées ;
- **« Révoquer l'accès d'Alexa »** et **« Déconnecter École Directe »** ;
- l'**export** des données du compte (fichier JSON) et la **suppression** du
  compte, qui efface tout.

## Données conservées

| Donnée | Forme dans la base |
| --- | --- |
| E-mail du compte EC-to-Alexa | en clair (identifiant de connexion) |
| Mot de passe du site | haché (PBKDF2-SHA256, sel aléatoire) |
| Identifiant et mot de passe École Directe, appareil mémorisé (UUID, `cn`/`cv`) | chiffrés (Fernet) |
| Réponses aux questions de sécurité École Directe | hachées (HMAC-SHA256 avec clé secrète) |
| Identifiant élève École Directe | en clair (affiché sur le tableau de bord) |
| Jetons de session, codes OAuth, jetons Alexa, jetons CSRF | empreinte SHA-256 seulement |
| Compte Alexa lié (`amzn1.ask.account…`), date de liaison et de dernière demande | en clair |
| Journal d'audit (création de compte, liaisons, QCM rempli automatiquement…) | type d'événement et date, sans contenu |

Les données scolaires (emploi du temps, notes, devoirs, messages) ne sont
**jamais enregistrées** : elles sont lues à la demande auprès d'École Directe.
La clé de chiffrement et de hachage (`ECO_TO_ALEXA_SECRET_KEY`) est dans
`.env`, pas dans la base. Une sauvegarde de la base est faite à chaque mise à
jour (`update.sh`, dossier `backups/`).

## Architecture

```
                  Internet (HTTPS 443)
 Alexa · navigateur ─────────▶ reverse proxy (Zoraxy, ou Caddy intégré)
                                  │  certificat Let's Encrypt
                                  ▼
                        routeur interne (Caddy)
                     ┌────────────┴─────────────┐
       /oauth/*, /alexa                   tout le reste
                     ▼                          ▼
             oauth_server.py            account_server.py
     (liaison OAuth + webhook Alexa)    (site : comptes, École Directe,
                     │                    liaison Amazon, simulateur)
                     └──────── SQLite (volume Docker) ─┘
                                  │
                                  ▼
                        API École Directe
```

| Fichier | Rôle |
| --- | --- |
| `account_server.py` | Site web : comptes, connexion École Directe et QCM, bouton « Lier mon compte Amazon », simulateur, export et suppression. |
| `oauth_server.py` | Liaison de compte OAuth 2.0 (`/oauth/authorize`, `/oauth/token`, `/oauth/revoke`) et webhook de la skill (`/alexa`). |
| `alexa_verify.py` | Vérification de chaque requête Alexa selon les règles d'Amazon. |
| `amazon_link.py` | Liaison lancée depuis le site (Login with Amazon et API Skill Enablement). |
| `application.py` | Stockage SQLite (comptes, jetons, liaisons chiffrées), logique de la skill. |
| `alexa_speech.py` | Construction des phrases lues par Alexa. |
| `ecole_directe.py` | Client de l'API École Directe (QCM, appareil mémorisé). |
| `ui.py` | Gabarit HTML et feuille de style du site (sans JavaScript). |
| `skill-package/` | Manifeste et modèle vocal fr-FR de la skill. |
| `install.sh` | Installation (Docker, secrets, certificat interne, questions sur la skill) ; relançable sans risque. |
| `update.sh` | Mise à jour : sauvegarde de la base, `git pull`, tests, redémarrage. |
| `.env.example` | Modèle de configuration (le vrai `.env` n'est jamais commité). |
| `test_*.py` | Tests automatiques (site, OAuth, vérification Alexa, phrases, client École Directe, sécurité). |
| `CHECKLIST.md` | Suivi des fonctionnalités. |
| `docker-compose.yml`, `Caddyfile`, `Caddyfile.router` | Déploiement Docker : Caddy HTTPS intégré, ou routeur interne derrière un proxy existant. |
| `web.py` | Outil local de test de l'API École Directe (réponses brutes). |

Technologies : Python 3.12 (serveurs HTTP de la bibliothèque standard, sans
framework), SQLite, `cryptography`, `certifi`, `tzdata`, Docker Compose et
Caddy. Le site n'utilise aucun JavaScript.

## Sécurité

- **Identifiants École Directe** : chiffrés (Fernet) avec une clé propre au
  serveur, jamais affichés ni journalisés. L'identifiant élève provient
  uniquement de la réponse d'École Directe, jamais d'un champ de formulaire.
- **Mots de passe du site** : hachés avec PBKDF2-SHA256 (210 000 itérations).
  Les jetons de session, OAuth et Alexa ne sont stockés que sous forme
  d'empreinte.
- **Webhook Alexa** : chaque requête est vérifiée comme l'exige Amazon
  (signature `Signature-256`, certificat `echo-api.amazon.com` remontant à
  une autorité de confiance, horodatage de moins de 150 secondes, identifiant
  de la skill). Il reste fermé tant que la skill n'est pas configurée.
- **Liaison de compte** : codes OAuth à usage unique, secret client
  obligatoire, PKCE pour la liaison depuis le site, `state` lié à la session.
- **Site** : aucun JavaScript, CSP stricte, jeton CSRF sur chaque formulaire,
  cookies `HttpOnly`/`Secure`/`SameSite=Lax`, limitation de débit.
- **Réponses au QCM** : jamais en clair. Question et réponse sont stockées
  en HMAC-SHA256, avec une clé dérivée de `ECO_TO_ALEXA_SECRET_KEY` (absente
  de la base) et liées au compte et à l'identifiant École Directe. Le serveur
  ne fait que reconnaître, parmi les propositions d'École Directe, celle qui a
  déjà été acceptée. Une réponse refusée est oubliée, et l'élève peut tout
  effacer depuis le site (« Oublier ces réponses »).
- **Isolation** : chaque compte ne voit que ses données. Un compte Alexa
  n'est lié qu'à un seul compte EC-to-Alexa.
- **Réseau** : seuls les ports 80 et 443 du proxy sont exposés à Internet.
  Le trajet proxy → serveur peut rester chiffré (routeur HTTPS interne).

## Journaux

Le service `oauth` écrit une ligne `[ec-to-alexa] …` pour chaque requête
Alexa (acceptée, ou refusée avec la raison) et pour chaque liaison de compte,
sans jeton, mot de passe ni donnée scolaire. Voir le guide
`installation-serveur` pour les lire et pour le dépannage.

## Limites connues

- Tant que la skill n'est pas publiée (mode **Development**), elle ne marche
  que sur les appareils du compte Amazon développeur et des testeurs bêta
  invités.
- Français (France) uniquement.
- L'API d'École Directe n'est pas officielle : si École Directe la modifie, il
  peut falloir mettre à jour `ecole_directe.py`.
- Pensé pour un **compte élève** École Directe (un compte parent n'utilise
  que le premier profil renvoyé).
- Alexa attend une réponse en moins de 8 secondes : si École Directe est très
  lente, Alexa dit qu'elle n'arrive pas à la joindre.

EC-to-Alexa n'est affilié ni à École Directe ni à Amazon. Il ne contourne
aucune protection : chaque réponse au QCM d'École Directe est d'abord donnée
par l'élève, le serveur ne fait que la rejouer quand la même question revient.

## Développement

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt        # Windows : .venv\Scripts\pip
.venv/bin/python -m unittest discover -p "test_*.py"
```

Les tests n'effectuent aucun appel réseau : École Directe et Amazon sont
simulés. Ils sont aussi lancés par `update.sh` avant chaque redémarrage du
serveur. Pour lancer le site en local, mettez `ECO_TO_ALEXA_SECURE_COOKIES=0`
et une clé `ECO_TO_ALEXA_SECRET_KEY` dans `.env`, puis lancez
`python account_server.py` (http://127.0.0.1:8000).

Suivi des fonctionnalités : [`CHECKLIST.md`](./CHECKLIST.md).
