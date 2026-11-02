# EC-to-Alexa — Configuration de la skill Alexa

> **Branche de documentation.** Le code à jour et la description du projet
> sont sur [`main`](https://github.com/Flunch4456/EC-to-Alexa/tree/main).
> L'installation du serveur est sur la branche
> [`installation-serveur`](https://github.com/Flunch4456/EC-to-Alexa/tree/installation-serveur) :
> **le serveur doit être installé et joignable en HTTPS avant de configurer la
> skill.**

Exemple utilisé dans ce guide : domaine `ec-to-alexa.mondomaine.fr`.
Les identifiants Amazon sont remplacés par des exemples (`amzn1.ask.skill.XXXX…`,
`M2ABC123DEF`) : utilise ceux de ta console, et ne les publie pas avec les
secrets.

## Sommaire

1. [Ce qu'il faut avant de commencer](#1-ce-quil-faut-avant-de-commencer)
2. [Créer la skill](#2-créer-la-skill)
3. [Modèle d'interaction](#3-modèle-dinteraction)
4. [Endpoint](#4-endpoint)
5. [Account Linking](#5-account-linking)
6. [Liaison depuis le site (app-to-app)](#6-liaison-depuis-le-site-app-to-app)
7. [Reporter les valeurs sur le serveur](#7-reporter-les-valeurs-sur-le-serveur)
8. [Tester](#8-tester)
9. [Lier le compte d'un élève](#9-lier-le-compte-dun-élève)
10. [Modifier la skill](#10-modifier-la-skill)
11. [Publier (facultatif)](#11-publier-facultatif)
12. [Dépannage](#12-dépannage)

---

## 1. Ce qu'il faut avant de commencer

- Le serveur installé (branche `installation-serveur`), et
  `https://ec-to-alexa.mondomaine.fr/healthz` qui répond `{"status":"ok"}`
  depuis Internet.
- Le **compte Amazon de l'Echo** : en mode développement, la skill n'est
  disponible que sur les appareils de ce compte. Connecte-toi avec lui sur
  <https://developer.amazon.com/alexa/console/ask>. L'Echo doit être réglé en
  **français (France)**.
- Les valeurs affichées à la fin de `./install.sh`, bloc **« Valeurs pour la
  console Alexa »**. Pour les revoir : relance `./install.sh` (rien n'est
  régénéré), ou lis `.env` sur le serveur.

## 2. Créer la skill

Console → **Create Skill** :

| Écran | Choix |
| --- | --- |
| Name, Locale | `Mon Carnet Scolaire`, **French (FR)** |
| Experience, Model, Hosting | **Other** → **Custom** → **Provision your own** |
| Template | **Start from Scratch** |

Valide avec **Create Skill**. La console ouvre l'onglet **Build**.

## 3. Modèle d'interaction

Le modèle est dans le dépôt :
[`skill-package/interactionModels/custom/fr-FR.json`](https://github.com/Flunch4456/EC-to-Alexa/blob/main/skill-package/interactionModels/custom/fr-FR.json).

1. **Build → Interaction Model → JSON Editor**.
2. Remplace tout le contenu par celui du fichier, puis **Save**.
3. **Build skill**, et attends « Build successful » (1 à 2 minutes).

Nom d'invocation : **« mon carnet scolaire »**. Intentions incluses :

| Intention | Exemples de phrases |
| --- | --- |
| `GetTodayScheduleIntent` | « mon emploi du temps », « quel est mon emploi du temps aujourd'hui » |
| `GetTomorrowScheduleIntent` | « mon emploi du temps de demain », « j'ai quoi demain » |
| `GetScheduleIntent` (créneau `jour`, `AMAZON.DATE`) | « mon emploi du temps lundi » |
| `GetNextClassIntent` | « quel est mon prochain cours » |
| `GetHomeworkIntent` (créneau `jour`) | « mes devoirs », « mes devoirs pour demain » |
| `GetGradesIntent` | « mes dernières notes » |
| `GetAbsencesIntent` | « mes absences et mes retards » |
| `GetMessagesIntent` | « est-ce que j'ai des messages » |
| `GetAssessmentsIntent` | « mes prochaines évaluations », « mes contrôles » |
| `AMAZON.*` | aide, annuler, stop, retour, phrase non comprise |

## 4. Endpoint

**Build → Endpoint** :

| Champ | Valeur |
| --- | --- |
| Service Endpoint Type | **HTTPS** |
| Default Region | `https://ec-to-alexa.mondomaine.fr/alexa` |
| SSL certificate type | **My development endpoint has a certificate from a trusted certificate authority** (certificat Let's Encrypt du nom exact). Avec un certificat wildcard `*.mondomaine.fr` : **…is a sub-domain of a domain that has a wildcard certificate…** |

**Save**. En haut de cette page, copie le **Skill ID**
(`amzn1.ask.skill.…`) : il servira à la [section 7](#7-reporter-les-valeurs-sur-le-serveur).

## 5. Account Linking

**Build → Tools → Account Linking** :

| Champ | Valeur |
| --- | --- |
| Do you allow users to create an account or link to an existing account with you? | **Activé** |
| Allow users to enable skill without account linking | **Désactivé** |
| Allow users to link their account to your skill from within your application or website | **Activé** (voir [section 6](#6-liaison-depuis-le-site-app-to-app)) |
| Authorization Grant Type | **Auth Code Grant** |
| Your Web Authorization URI | `https://ec-to-alexa.mondomaine.fr/oauth/authorize` |
| Access Token URI | `https://ec-to-alexa.mondomaine.fr/oauth/token` |
| Your Client ID | `ec-to-alexa` (valeur `ALEXA_OAUTH_CLIENT_ID`) |
| Your Secret | valeur `ALEXA_OAUTH_CLIENT_SECRET` affichée par `install.sh` |
| Your Authentication Scheme | **HTTP Basic (Recommended)** |
| Scope | `ecole` |
| Domain List | vide |
| Default Access Token Expiration Time | vide |

**Save**. La console exige de ressaisir **Your Secret** à chaque
enregistrement de cette page.

En bas de la page, la section **Alexa Redirect URLs** liste trois adresses
de la forme `https://layla.amazon.com/api/skill/link/M2ABC123DEF`. La partie
finale (`M2ABC123DEF`) est ton **Vendor ID** : note-le pour la
[section 7](#7-reporter-les-valeurs-sur-le-serveur). Le serveur n'accepte
que ces trois adresses de redirection.

## 6. Liaison depuis le site (app-to-app)

Ce réglage permet au bouton **« Lier mon compte Amazon »** du site d'associer
directement le compte Amazon de l'élève à son compte École Directe, sans
passer par l'application Alexa. Toujours sur la page **Account Linking**,
dans la partie **« Allow users to link their account to your skill from
within your application or website »** :

| Champ | Valeur |
| --- | --- |
| Your Redirect URLs | `https://ec-to-alexa.mondomaine.fr/amazon/callback` |
| Alexa Client Id | affiché par la console (`amzn1.application-oa2-client.…`) : à copier |
| Alexa Client Secret | bouton **SHOW** : à saisir directement dans `install.sh` (saisie masquée) |

**Save** (en ressaisissant **Your Secret**), puis **Build skill**.

> L'Alexa Client Secret donne accès à l'API de liaison au nom de la skill :
> ne le colle ni dans un chat, ni dans un ticket, ni dans Git. Saisis-le
> uniquement dans `install.sh`, sur le serveur.

## 7. Reporter les valeurs sur le serveur

Sur le serveur :

```bash
cd /opt/ec-to-alexa
./install.sh
```

| Question de `install.sh` | Où la trouver dans la console | Variable `.env` |
| --- | --- | --- |
| Identifiant de la skill | Build → Endpoint (en haut), ou la liste des skills → **Copy Skill ID** | `ALEXA_SKILL_ID` |
| Vendor ID Amazon | Account Linking → Alexa Redirect URLs (fin de l'adresse) | `ALEXA_VENDOR_ID` |
| Alexa Client Id | Account Linking → section « within your application or website » | `ALEXA_APP_CLIENT_ID` |
| Alexa Client Secret | même section, bouton **SHOW** | `ALEXA_APP_CLIENT_SECRET` |

Dans l'autre sens, ces valeurs viennent du serveur et vont dans la console :

| Variable `.env` | Champ de la console |
| --- | --- |
| `https://DOMAIN/alexa` | Endpoint → Default Region |
| `https://DOMAIN/oauth/authorize`, `https://DOMAIN/oauth/token` | Account Linking → Web Authorization URI, Access Token URI |
| `ALEXA_OAUTH_CLIENT_ID` | Account Linking → Your Client ID |
| `ALEXA_OAUTH_CLIENT_SECRET` | Account Linking → Your Secret |
| `https://DOMAIN/amazon/callback` | Account Linking → Your Redirect URLs |

Tant que `ALEXA_SKILL_ID` est vide, le serveur refuse toutes les requêtes
`/alexa` (`alexa_not_configured`). Tant que `ALEXA_VENDOR_ID` est vide, la
liaison est refusée (`oauth_not_configured`).

## 8. Tester

1. Onglet **Test** → **Skill testing is enabled in : Development**.
2. Dans le simulateur, tape ou dis : **« ouvre mon carnet scolaire »**.
   - Compte non lié : Alexa demande de lier le compte sur le site EC to
     Alexa (une carte avec l'adresse du site apparaît dans l'application
     Alexa).
   - Compte lié : message d'accueil.
3. Puis : **« quel est mon emploi du temps demain »**.
4. Coche **Device Log** et **Skill I/O** pour voir la requête envoyée au
   serveur et la réponse. Sur le serveur, chaque requête apparaît dans
   `docker compose logs oauth | grep ec-to-alexa`.

Sur l'Echo (même compte Amazon) : « Alexa, ouvre mon carnet scolaire », ou
directement « Alexa, demande à mon carnet scolaire mes devoirs ».

## 9. Lier le compte d'un élève

Le compte Amazon se lie **depuis le site**, ce qui permet au serveur de savoir
quel compte Amazon correspond à quel compte École Directe :

1. Sur `https://ec-to-alexa.mondomaine.fr/` : créer un compte EC-to-Alexa
   (étape 1).
2. Connecter École Directe (étape 2), et répondre à la question de sécurité
   si elle est posée.
3. Étape 3 : **« Lier mon compte Amazon »** → page de connexion Amazon →
   **Autoriser**. Le site affiche « Ton compte Amazon est lié » et la fin de
   l'identifiant Alexa (`…STUSER42`). La skill est activée automatiquement sur
   le compte Amazon.

Le tableau de bord indique ensuite la date de la dernière requête Alexa.
**« Révoquer l'accès d'Alexa »** coupe la liaison ; il suffit de relier pour
la rétablir. En mode développement, seul le compte Amazon propriétaire de la
skill (et les testeurs bêta invités) peut lier.

## 10. Modifier la skill

| Changement | À faire |
| --- | --- |
| Phrases ou intentions | Modifier `fr-FR.json` dans le dépôt (sur `main`), le recoller dans le **JSON Editor**, **Save**, **Build skill**. Une nouvelle intention demande aussi du code serveur. |
| Nom d'invocation | `invocationName` dans le JSON (en minuscules, au moins deux mots), puis **Build skill**. |
| Nom de domaine | Endpoint, Web Authorization URI, Access Token URI et Your Redirect URLs avec le nouveau nom, **Save** (en ressaisissant Your Secret), **Build skill**. Procédure complète côté serveur : branche `installation-serveur`, section « Changer de nom de domaine ». |
| Secret OAuth changé sur le serveur | Account Linking → Your Secret → **Save**. |

## 11. Publier (facultatif)

Pour un usage familial, le mode **Development** suffit : la skill marche sur
tous les Echo du compte Amazon. Pour l'ouvrir à d'autres comptes :

- **Bêta** : onglet **Distribution** → remplir la fiche (description, icônes
  108 × 108 et 512 × 512, URL de politique de confidentialité, par exemple
  `https://ec-to-alexa.mondomaine.fr/`) → **Availability → Beta Test**, puis
  inviter les adresses e-mail des testeurs.
- **Publication** : onglet **Certification** → validation → soumission.
  Après publication, mettre `ALEXA_SKILL_STAGE=live` dans `.env` et relancer
  `./install.sh`, sinon le bouton « Lier mon compte Amazon » active la version
  de développement.

## 12. Dépannage

| Symptôme | Cause et solution |
| --- | --- |
| « Je ne parviens pas à accéder à la Skill demandée », Device Log `SKILL_ENDPOINT_ERROR`, rien dans les journaux du serveur | La négociation TLS échoue avant le serveur. Dans Zoraxy : **Minimum TLS Version = TLS v1.2**. Vérifie aussi le type de certificat de l'Endpoint (section 4) et qu'aucun filtrage (captcha, pays, authentification) ne s'applique au domaine. |
| Même message, et la ligne `/alexa refusée : Identifiant de skill inattendu` dans les journaux | `ALEXA_SKILL_ID` ≠ Skill ID de la console : relance `./install.sh` avec le bon. |
| `/alexa refusée : Requête Alexa périmée` | Horloge du serveur décalée : active la synchronisation NTP. |
| Alexa demande de lier le compte alors que c'est fait | La liaison a été révoquée, ou faite sur un autre compte Amazon que celui de l'Echo. Relier depuis le site avec le bon compte. |
| Le bouton « Lier mon compte Amazon » n'apparaît pas | École Directe pas connecté (étape 2), ou `ALEXA_APP_CLIENT_ID`/`ALEXA_APP_CLIENT_SECRET` vides : relance `./install.sh`. |
| Après « Autoriser » chez Amazon : erreur de redirection | **Your Redirect URLs** différente de `https://DOMAIN/amazon/callback`, ou pas enregistrée : corriger, **Save**, **Build skill**. |
| Le site indique un échec de liaison | Vendor ID absent ou faux sur le serveur (`oauth_not_configured`), Your Secret de la console ≠ `ALEXA_OAUTH_CLIENT_SECRET`, ou Access Token URI fausse. |
| `{"error": "invalid_request"}` sur `/oauth/authorize` | Normal quand on ouvre l'adresse à la main : seule Amazon l'appelle avec les bons paramètres. |
| `{"error": "alexa_not_configured"}` | `ALEXA_SKILL_ID` vide : section 7. |
| Alexa ne comprend pas une phrase | Le modèle n'a pas été reconstruit (**Build skill**), ou la phrase n'est pas dans le modèle : l'ajouter (section 10). |
| « École Directe pose une question de sécurité que je ne connais pas encore » | Sur le site, bouton « Répondre à la question de sécurité » : la réponse est mémorisée (hachée) et Alexa y répondra seule les fois suivantes. |
| « Je n'arrive pas à joindre École Directe » | École Directe indisponible ou trop lente, ou mot de passe changé : déconnecter puis reconnecter École Directe sur le site. |
