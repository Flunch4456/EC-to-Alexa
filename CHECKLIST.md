# Checklist de livraison EC-to-Alexa

Cette checklist est la feuille de route persistante du projet. Les éléments
cochés sont réalisés dans le dépôt ; les éléments non cochés nécessitent
encore une configuration externe, un compte développeur ou une décision de
déploiement.

## Réalisé

- [x] Client EcoleDirecte avec cookie GTK, jeton, renouvellement et QCM
  manuel.
- [x] Masquage des jetons dans les réponses affichées.
- [x] Tests réseau simulés sans identifiants réels.
- [x] Stockage SQLite des utilisateurs et hachage PBKDF2 des mots de passe.
- [x] Séparation des sessions web, codes OAuth et jetons Alexa par `user_id`.
- [x] Codes OAuth à usage unique, expiration et vérification stricte du client
  et de l'URI de redirection.
- [x] Révocation des jetons Alexa.
- [x] Adaptateur Alexa pour devoirs, absences, messages, aide et arrêt.
- [x] Serveur HTTP local des routes OAuth et du webhook Alexa.
- [x] Endpoint `/healthz`, en-têtes `nosniff`/CSP et healthcheck Docker.
- [x] Dockerfile, Compose, variables d'environnement et guides (branches
  `installation-serveur` et `configuration-alexa-skill`).

## Prochaine exécution autonome

- [x] Brancher l'authentification web de `account_server.py` sur `AccountStore`.
- [x] Ajouter une page d'inscription et de connexion avec cookie de session
  `HttpOnly`, `Secure` en production et `SameSite=Lax`.
- [x] Ajouter CSRF et limitation de débit sur les formulaires web.
- [x] Ajouter une table de connexion EcoleDirecte chiffrée via un secret
  externe ; ne jamais stocker le mot de passe en clair.
- [x] Restaurer les services EcoleDirecte au démarrage de l'application par
  utilisateur après une reconnexion explicite.
- [x] Connecter le registre `AlexaSkill` au compte EcoleDirecte réel sans
  mélange entre utilisateurs.
- [x] Associer l'identifiant élève renvoyé par EcoleDirecte au compte interne,
  sans accepter un identifiant élève fourni arbitrairement par le navigateur.
- [x] Ajouter un tableau de bord authentifié et une déconnexion EcoleDirecte
  avec révocation de la liaison.
- [x] Ajouter les intents emploi du temps, prochain cours, notes et
  évaluations du cahier de texte, avec des réponses vocales détaillées
  ([`alexa_speech.py`](./alexa_speech.py)).
- [x] Modèle vocal fr-FR et manifeste dans [`skill-package/`](./skill-package).
- [x] Webhook `/alexa` : jeton lu dans le corps de la requête, carte
  LinkAccount, LaunchRequest, vérification `ALEXA_SKILL_ID` et horodatage.
- [x] Installation en une commande : `install.sh` (Docker, `.env`, secrets,
  Caddy HTTPS intégré au compose, valeurs pour la console Alexa).
- [x] Account Linking : trois URL de redirection Amazon via `ALEXA_VENDOR_ID`,
  secret client vérifié (HTTP Basic ou corps), démarrage sans configuration
  Alexa.
- [x] Vérifier la signature des requêtes Amazon (`Signature-256`, chaîne de
  certificats jusqu'à une racine de confiance, SAN `echo-api.amazon.com`).
- [x] Terminer le QCM École Directe depuis le site et mémoriser l'appareil
  (UUID, cn/cv chiffrés) pour éviter un QCM à chaque requête Alexa.
- [x] Interface web : accueil, tableau de bord en trois étapes, simulateur
  « Essayer sans enceinte », révocation Alexa, page de liaison OAuth.
- [x] Caddy optionnel (`COMPOSE_PROFILES`) pour un reverse proxy existant.
- [x] Profil `router` derrière Zoraxy : une seule règle vers `127.0.0.1:8080`
  (Zoraxy retire le préfixe des répertoires virtuels), ports réglables.
- [x] Skill « Mon Carnet Scolaire » créée dans la console Amazon (fr-FR,
  Custom, Provision your own) et modèle vocal importé.
- [x] Console Amazon : endpoint HTTPS et Account Linking configurés sur le
  domaine public.
- [x] Liaison du compte Amazon depuis le site (« Lier mon compte Amazon ») et
  affichage du compte Alexa associé à chaque École Directe.
- [x] Routeur HTTPS interne (8443) : trajet proxy → serveur chiffré.
- [x] Journal des requêtes Alexa refusées (raison, sans donnée sensible).
- [x] Sauvegarde automatique de la base à chaque `update.sh`.
- [x] Brancher le healthcheck sur le processus web principal.
- [x] Ajouter une configuration reverse proxy avec en-têtes de sécurité,
  HTTPS et limites de taille.
- [x] Ajouter une configuration Caddy séparant les routes comptes et OAuth.
- [x] Ajouter export/suppression de compte et journal d'audit non sensible.
- [ ] Ajouter expiration des données scolaires et purge automatique du cache.
- [x] Tester la skill dans la console Alexa avec un domaine HTTPS réel
  (simulateur : accueil et emploi du temps avec un vrai compte École Directe).
- [ ] Effectuer un audit de sécurité et une validation manuelle EcoleDirecte
  avant toute exposition publique.

## Validation à chaque étape

- [x] `python -m unittest discover -p "test_*.py" -v`
- [x] `git diff --check`
- [x] diagnostics Pylance sans erreur
- [x] aucun secret, jeton ou contenu scolaire dans les journaux
- [x] documentation mise à jour avec toute nouvelle URL ou variable
