# EC-to-Alexa — skill « Mon Carnet Scolaire »

Une skill Alexa qui lit tes données École Directe à voix haute :

> « Alexa, demande à mon carnet scolaire mes devoirs pour demain. »
> — « Pour demain, tu as 2 devoirs. En Français : lire le chapitre 3. En Mathématiques : exercices 4 et 5 page 32. »

Elle répond aussi pour l'emploi du temps, le prochain cours, les dernières
notes, les prochaines évaluations, les absences et les nouveaux messages.

## Ce qui est déjà préconfiguré

Après un `git clone` de cette branche, le script `install.sh` s'occupe de tout
ce qui se passe **sur le serveur** :

- installe Docker si besoin ;
- crée le fichier `.env` et génère les secrets (clé de chiffrement des
  identifiants École Directe, secret OAuth) ;
- démarre trois conteneurs : le site de comptes (`web`), la liaison Alexa et
  le webhook de la skill (`oauth`), et **Caddy**, qui obtient tout seul le
  certificat HTTPS exigé par Alexa (désactivable si tu as déjà un reverse
  proxy comme Zoraxy, voir [Dépannage](#dépannage)) ;
- ouvre les ports 80/443 dans `ufw` s'il est actif ;
- lance les tests, puis affiche **les valeurs exactes à coller dans la console
  Amazon**.

Le modèle vocal de la skill est prêt dans
[`skill-package/interactionModels/custom/fr-FR.json`](./skill-package/interactionModels/custom/fr-FR.json).

Ce qui ne peut pas être automatisé, parce que ça dépend de **tes** comptes et
de **ton** réseau, est décrit ci-dessous, dans l'ordre.

## Ce qu'il te faut

- Un serveur **Debian 12/13 ou Ubuntu** allumé en permanence (PC, machine
  virtuelle, Raspberry Pi 64 bits ou VPS), avec un compte `sudo`.
- Un **nom de domaine** ou un sous-domaine gratuit (par exemple
  [DuckDNS](https://www.duckdns.org)).
- Si le serveur est chez toi : l'accès à l'interface de ta **box**.
- Le **compte Amazon** utilisé par ton Echo, pour la
  [console Alexa Developer](https://developer.amazon.com/alexa/console/ask).
- Ton identifiant et ton mot de passe **École Directe**.

---

## Étape 1 — Nom de domaine et réseau

1. Crée un enregistrement DNS de type **A** qui pointe ton domaine (par
   exemple `ecole.mondomaine.fr`) vers l'**adresse IP publique** de ta
   connexion. Avec DuckDNS, choisis un sous-domaine : il est créé
   automatiquement avec ton IP actuelle.
2. Si le serveur est chez toi, donne-lui une **IP locale fixe** (bail DHCP
   statique dans la box), puis crée dans la box une **redirection de ports** :
   - TCP **80** → IP locale du serveur, port 80 ;
   - TCP **443** → IP locale du serveur, port 443.
3. Si ton IP publique change régulièrement, configure la mise à jour
   automatique de DuckDNS (voir leur page « install »).

> Certaines connexions (box 4G/5G, certains opérateurs fibre en CGNAT)
> n'ont pas d'IP publique propre : la redirection de ports est alors
> impossible. Dans ce cas, utilise un petit VPS à la place du serveur maison.

## Étape 2 — Cloner et installer sur le serveur

```bash
sudo apt update && sudo apt install -y git
git clone -b feature/alexa-skill https://github.com/Flunch4456/EC-to-Alexa.git ~/ec-to-alexa
cd ~/ec-to-alexa
./install.sh
```

Le script pose trois questions :

| Question | Réponse la première fois |
| --- | --- |
| Nom de domaine | ton domaine, par exemple `ecole.mondomaine.fr` |
| Identifiant de la skill | laisser vide (Entrée) |
| Vendor ID Amazon | laisser vide (Entrée) |

À la fin, il affiche un bloc **« Valeurs pour la console Alexa »** : garde-le
sous la main pour l'étape 4. Tu peux le réafficher à tout moment en relançant
`./install.sh` (les secrets ne sont jamais régénérés).

Vérifie depuis ton téléphone, **en 4G** (hors du Wi-Fi), que
`https://ton-domaine/` affiche la page EC-to-Alexa avec le cadenas HTTPS.

## Étape 3 — Créer ton compte et connecter École Directe

1. Ouvre `https://ton-domaine/`.
2. **Créer un compte** : une adresse e-mail et un mot de passe d'au moins
   12 caractères. C'est le compte EC-to-Alexa, celui que tu saisiras dans
   l'application Alexa ; il est différent du compte École Directe.
3. Saisis ton identifiant et ton mot de passe **École Directe**, puis
   **Connecter École Directe**. Si École Directe pose sa **question de
   sécurité** (QCM), elle s'affiche sur la page : réponds-y. La réponse est
   mémorisée (chiffrée) pour que la question ne revienne pas à chaque demande
   à Alexa.
4. Le tableau de bord affiche « École Directe est connecté ». La section
   **Essayer sans enceinte** permet de poser une question et de lire ce
   qu'Alexa répondrait, avec tes vraies données, avant même d'avoir lié la
   skill.

## Étape 4 — Créer la skill dans la console Amazon

Connecte-toi à la [console Alexa Developer](https://developer.amazon.com/alexa/console/ask)
**avec le même compte Amazon que ton Echo** : une skill en développement
n'est disponible que sur les appareils de ce compte.

1. **Create Skill** :
   - Name : `Mon Carnet Scolaire` ; Primary locale : **French (FR)** ;
   - Type of experience : **Other** ; Model : **Custom** ;
     Hosting services : **Provision your own** ;
   - Template : **Start from Scratch**, puis **Create Skill**.
2. **Build → Interaction Model → JSON Editor** : remplace tout le contenu par
   celui de
   [`fr-FR.json`](https://raw.githubusercontent.com/Flunch4456/EC-to-Alexa/main/skill-package/interactionModels/custom/fr-FR.json),
   puis **Save** et **Build skill**. Attends le message « Build Successful ».
3. **Build → Endpoint** :
   - choisis **HTTPS** ;
   - Default Region : `https://ton-domaine/alexa` ;
   - certificat : **My development endpoint has a certificate from a trusted
     certificate authority** ;
   - **Save**.
4. **Build → Account Linking** :
   - active **Do you allow users to create an account or link to an existing
     account with you?** ;
   - choisis **Auth Code Grant** ;
   - remplis les champs avec le bloc affiché par `install.sh` :

     | Champ de la console | Valeur |
     | --- | --- |
     | Your Web Authorization URI | `https://ton-domaine/oauth/authorize` |
     | Access Token URI | `https://ton-domaine/oauth/token` |
     | Your Client ID | `ec-to-alexa` |
     | Your Secret | le **Client Secret** affiché par `install.sh` |
     | Your Authentication Scheme | **HTTP Basic (Recommended)** |
     | Scope | `ecole` (puis **+ Add scope**) |

   - **Save**.
5. Toujours sur la page Account Linking, en bas, la section **Alexa Redirect
   URLs** liste trois adresses comme
   `https://layla.amazon.com/api/skill/link/M2ABC123DEF`. La fin de l'adresse
   (`M2ABC123DEF`) est ton **Vendor ID** : note-le.
6. Note l'**identifiant de la skill** (`amzn1.ask.skill.…`) : il apparaît sur
   la page **Endpoint** (« Your Skill ID ») et dans la liste des skills
   (**Copy Skill ID**).

## Étape 5 — Donner la skill au serveur

Sur le serveur :

```bash
cd ~/ec-to-alexa
./install.sh
```

Appuie sur Entrée pour garder le domaine, puis colle l'**identifiant de la
skill** et le **Vendor ID**. Le script redémarre les services ; il doit se
terminer par « Skill … configurée, liaison de compte active ».

## Étape 6 — Activer la skill et lier ton compte

1. Dans la console Amazon, onglet **Test** : passe **Skill testing is enabled
   in** sur **Development**.
2. Dans l'**application Alexa** du téléphone (même compte Amazon) :
   **Plus → Skills et jeux → Vos skills → Dév.** → **Mon Carnet Scolaire** →
   **Activer**.
3. Une page **« Autoriser Alexa »** s'ouvre : saisis l'e-mail et le mot de
   passe du **compte EC-to-Alexa** créé à l'étape 3, puis **Autoriser**.
   L'application doit confirmer que le compte est associé.

## Étape 7 — Parler à Alexa

| Tu dis | Alexa répond |
| --- | --- |
| « Alexa, ouvre mon carnet scolaire » | Message d'accueil, puis attend ta question |
| « … mon emploi du temps » / « j'ai quoi demain » / « j'ai cours lundi » | Les cours, avec heure, matière et salle, et les cours annulés |
| « … mon prochain cours » | Matière, heure, salle et professeur |
| « … mes devoirs » / « mes devoirs pour jeudi » | Le contenu de chaque devoir (par défaut pour le prochain jour de cours) et les évaluations |
| « … mes notes » | Les trois dernières notes |
| « … mes absences » | Absences, retards et ce qui n'est pas justifié |
| « … mes messages » | Les nouveaux messages, avec l'expéditeur et l'objet |

En une seule phrase : « Alexa, demande à mon carnet scolaire mes devoirs
pour demain ».

---

## Mettre à jour

```bash
cd ~/ec-to-alexa
./update.sh
```

Le script récupère la dernière version de la branche, reconstruit, lance les
tests et ne redémarre que si tout passe. Les données et le certificat sont
conservés.

## Dépannage

Les journaux se consultent avec `docker compose logs --tail=100 web oauth caddy`
(ajoute `sudo` devant si ton utilisateur n'est pas dans le groupe `docker`).

| Problème | Cause probable et solution |
| --- | --- |
| Le site ne s'ouvre pas en HTTPS | DNS qui ne pointe pas vers ton IP, ou ports 80/443 non redirigés dans la box. Regarde `docker compose logs caddy`. |
| `install.sh` dit qu'un Caddy tourne déjà | Une ancienne installation de Caddy hors Docker occupe les ports : `sudo systemctl disable --now caddy`, puis relance `./install.sh`. |
| Les conteneurs redémarrent en boucle avec « Fernet key must be 32 url-safe base64-encoded bytes » | `.env` vient d'une ancienne installation avec une clé sans le `=` final. Supprime la ligne `ECO_TO_ALEXA_SECRET_KEY=` du `.env` et relance `./install.sh` (puis reconnecte École Directe). |
| Alexa demande de lier le compte | Fais l'étape 6. Si ça persiste, vérifie que `curl http://127.0.0.1:8001/healthz` renvoie `"oauth": true` ; sinon le Vendor ID manque (étape 5). |
| Page de liaison : `invalid_request` | Le Client ID de la console n'est pas `ec-to-alexa`, ou le Vendor ID saisi est faux. |
| « Il y a eu un problème avec la réponse de la skill » | L'identifiant de skill dans `.env` ne correspond pas, ou le serveur est arrêté. Relance `./install.sh`. |
| « Je n'arrive pas à joindre École Directe » | Mot de passe École Directe changé ou question de sécurité demandée : reconnecte École Directe sur `https://ton-domaine/`. |
| J'ai déjà un reverse proxy (Zoraxy, Nginx Proxy Manager…) sur 80/443 | Dans `.env`, mets `COMPOSE_PROFILES=` (vide) pour ne pas lancer Caddy, et `EXPOSE_ADDR` à l'adresse que ton proxy peut joindre (`127.0.0.1` s'il est sur la même machine, sinon l'IP locale du serveur). Dans le proxy : `https://ton-domaine/oauth/*` et `https://ton-domaine/alexa` → port **8001**, tout le reste → port **8000**, avec un certificat Let's Encrypt. Relance `./install.sh`. |
| Accéder au site en HTTP depuis le réseau local | Dans `.env`, mets `EXPOSE_ADDR=0.0.0.0` et `ECO_TO_ALEXA_SECURE_COOKIES=0`, puis `docker compose up -d`. Ports 8000/8001 en clair : à éviter sur un réseau partagé. |

## Sécurité

- Les identifiants École Directe sont chiffrés (Fernet) avec la clé de `.env`.
  Ne partage jamais ce fichier et sauvegarde-le avec la base.
- Le webhook vérifie chaque requête comme l'exige Amazon : signature
  `Signature-256`, certificat `echo-api.amazon.com` remontant à une autorité
  de confiance, identifiant de skill et horodatage de moins de 150 secondes.
  Il reste fermé tant que `ALEXA_SKILL_ID` n'est pas renseigné.
- Le site n'utilise aucun JavaScript ; formulaires protégés par jeton CSRF,
  cookies `HttpOnly`/`Secure`/`SameSite=Lax`, limitation de débit.
- Sauvegardes, détails Docker et configuration avancée :
  [`installation.md`](./installation.md) et
  [`configuration.md`](./configuration.md). Suivi : [`CHECKLIST.md`](./CHECKLIST.md).

## Développement (Windows)

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m unittest discover -p "test_*.py" -v
```

Les tests n'effectuent aucun appel réseau. Pour lancer le site en local :
`ECO_TO_ALEXA_SECURE_COOKIES=0` et une clé `ECO_TO_ALEXA_SECRET_KEY` dans
`.env`, puis `python account_server.py` (http://127.0.0.1:8000).
[`web.py`](./web.py) ouvre une
interface locale de test sur `http://127.0.0.1:8000` (connexion École
Directe, QCM, réponses brutes de l'API). Le client
[`ecole_directe.py`](./ecole_directe.py) s'utilise aussi directement :

```python
from ecole_directe import EcoleDirecteClient

client = EcoleDirecteClient("mon_identifiant", "mon_mot_de_passe")
client.login()
print(client.get(f"eleves/{client.student_id}/notes.awp?verbe=get"))
```
