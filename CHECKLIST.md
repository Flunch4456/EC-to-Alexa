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
- [x] Dockerfile, Compose, variables d'environnement et guides
  [`configuration.md`](./configuration.md) et
  [`installation.md`](./installation.md).

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
- [ ] Ajouter les intents emploi du temps, prochain cours et évaluations
  uniquement à partir des réponses API réellement disponibles.
- [x] Brancher le healthcheck sur le processus web principal.
- [x] Ajouter une configuration reverse proxy avec en-têtes de sécurité,
  HTTPS et limites de taille.
- [x] Ajouter une configuration Caddy séparant les routes comptes et OAuth.
- [x] Ajouter export/suppression de compte et journal d'audit non sensible.
- [ ] Ajouter expiration des données scolaires et purge automatique du cache.
- [ ] Tester la skill dans la console Alexa avec un domaine HTTPS réel.
- [ ] Effectuer un audit de sécurité et une validation manuelle EcoleDirecte
  avant toute exposition publique.

## Validation à chaque étape

- [x] `python -m unittest discover -p "test_*.py" -v`
- [x] `git diff --check`
- [x] diagnostics Pylance sans erreur
- [x] aucun secret, jeton ou contenu scolaire dans les journaux
- [x] documentation mise à jour avec toute nouvelle URL ou variable
