# Serveur MCP Early avec FastMCP (projet d'apprentissage)

Serveur MCP pour l'API d'Early (ex-Timeular, suivi du temps), écrit avec FastMCP 4.x.
On s'inspire des serveurs existants pour comprendre, mais on n'en copie pas le code.

## Où on en est
- [x] Projet `uv`, FastMCP 4.1.4, serveur d'exercice (`exercice.py`)
- [x] Client Early v4 (`early_client.py`) : connexion clé/secret -> jeton, renouvelé si expiré
- [x] 5 outils en **lecture seule** : `lister_activites`, `lister_tags_clients`, `suivi_en_cours`, `temps_de_la_semaine`, `trous_a_remplir`
- [x] 1 outil d'**écriture** : `creer_entree_temps` (activité et clients tags par **nom**, mode `simuler`, refus si chevauchement)
- [x] Mode HTTP protégé par jeton (`serveur-early-http`), `Dockerfile`, modèle Compose
- [x] 33 tests avec un faux Early (aucun réseau, aucune clé)
- [ ] Essai réel en local avec une vraie clé (dans un `.env` local)
- [ ] Construction de l'image Docker et déploiement sur le VPS (accord explicite requis)

## Architecture : qui fait quoi
| Fichier | Rôle |
|---|---|
| `early_client.py` | Parler à Early (HTTP, jeton). Ne connaît pas MCP. |
| `analyse.py` | Calculs purs : heures par activité, trous. Ne connaît ni Early ni MCP. |
| `resolution.py` | Nom -> identifiant Early. Jamais de devinette : exact, sinon erreur avec suggestions. |
| `securite.py` | Jeton secret (Bearer) pour le mode HTTP. |
| `serveur.py` | Décrit les outils au LLM. Fin : récupère, calcule, renvoie. |
| `tests/` | Un faux Early (`httpx.MockTransport`) remplace le réseau. |

## Commandes
```bash
uv sync                      # installer
uv run pytest -q             # tests
uv run serveur-early         # lancer le serveur en local (stdio, sans jeton)
uv run serveur-early-http    # mode hébergé : exige MCP_AUTH_TOKEN, sinon refuse de démarrer
uv run fastmcp inspect src/serveur_early/serveur.py   # voir les outils exposés
```

## Configuration (secrets)
Copier `.env.example` en `.env` (ignoré par git) et y mettre `EARLY_API_KEY` et `EARLY_API_SECRET`,
créés sur product.early.app (le secret n'est affiché qu'une fois). Jamais dans le code ni dans git.

## Ce qu'on a appris
- `@mcp.tool` expose une fonction ; **types + docstring = le mode d'emploi lu par le LLM**.
- `annotations={"readOnlyHint": True}` signale un outil qui ne modifie rien (en 4.x, on le lit `read_only_hint`).
- `ToolError` : le message d'erreur arrive au LLM, qui peut corriger son appel. Les erreurs d'Early sont converties en `ToolError`.
- **Séparer** client, calculs et outils : chaque couche se teste seule.
- Tester sans réseau : `httpx.MockTransport` (faux Early) + `fastmcp.Client(mcp)` (en mémoire).
- Early : « pas de suivi en cours » = HTTP 404 (pas une réponse vide). Jeton expiré = 401 -> on se reconnecte une fois.
- Early n'expose pas l'usage des applications par l'API : on détecte donc les **trous** a posteriori.
- `uv run fichier.py` ne trouve pas un paquet `src/` non installé : d'où `[build-system]` et la commande `serveur-early`.
- Piège à venir : créer une entrée qui chevauche les existantes les **découpe ou les supprime** côté Early.
- Un serveur MCP exposé sur Internet **doit** être authentifié : `auth=` dans `FastMCP(...)`. Notre `JetonUnique` compare avec `hmac.compare_digest` (temps constant). Le mode HTTP refuse de démarrer sans jeton.
- `host_origin_protection` + `allowed_hosts` : le serveur refuse (421) une requête dont le `Host` n'est pas le sien.
- Outil d'écriture : valider **avant** d'écrire (noms, durée, chevauchement), proposer un mode `simuler`, et annoter `destructiveHint` pour que le client demande confirmation.
- Early référence les tags dans la note par identifiant : `<{{|t|ID|}}>`. Le serveur construit ce texte ; le LLM ne manipule jamais d'identifiants.

## Déploiement (sur le VPS Hostinger, à côté de n8n)
Le VPS exécute déjà un projet Compose `root` (n8n + Traefik). Notre serveur sera un projet **séparé** (`early-mcp`),
derrière Traefik, sur un sous-domaine dédié (qui doit pointer vers le VPS).
Voir `docker-compose.exemple.yml`. Les secrets (clé et secret Early, jeton) sont passés en variables d'environnement
au déploiement, jamais dans le dépôt. Reste à décider comment l'image Docker est construite (dépôt privé).

## Sécurité (audit avant publication)
Contrôles faits : recherche de secrets (aucun), `pip-audit` (aucune vulnérabilité connue), `bandit`, relecture du code d'authentification, du Dockerfile, du Compose et du workflow.
- **Authentification** : jeton Bearer unique, comparaison en temps constant, 32 caractères minimum ; le mode HTTP refuse de démarrer sans `MCP_AUTH_TOKEN` ni `MCP_PUBLIC_HOST`.
- **Protection Host/Origin** active : une requête avec un autre `Host` est refusée (421).
- **Écriture** : refus des noms inconnus ou ambigus, des durées aberrantes et des chevauchements ; **l'écrasement est désactivé** sauf `MCP_AUTORISER_ECRASEMENT=1` ; une note ne peut pas contenir de balise `<{{...}}>` (sinon le texte libre pourrait rattacher des tags non voulus).
- **Secrets** : uniquement en variables d'environnement ; jamais dans les logs ni dans les messages d'erreur.
- **Conteneur** : utilisateur non-root, système de fichiers en lecture seule, aucune capacité Linux, mémoire bornée, port non publié (seul Traefik y accède). L'image `uv` est épinglée.
- **Workflow GitHub** : lecture seule par défaut ; seul le job d'image peut publier.
- **Limites connues** : pas de limitation de débit (à ajouter côté Traefik si besoin) ; un seul jeton partagé (pas de rôles) ; actions GitHub épinglées par version et non par empreinte.
