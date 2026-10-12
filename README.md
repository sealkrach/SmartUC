# UseCase Hub

Intake, normalisation, registre et intelligence de portefeuille pour les use cases IA.

Un demandeur décrit son idée en langage naturel (chat ou brief collé) → un agent LangGraph l'interviewe,
remplit en direct un **canvas normalisé** (template YAML versionné) et signale ce qui manque → la fiche entre
dans un **workflow de gouvernance à portes d'approbation** → des **automations** détectent doublons,
recoupements et dépendances (embeddings + juge LLM + entités partagées), proposent un **tier EU AI Act**
et calculent un score **Valeur × Faisabilité**.

Inspirations : Backstage (registry d'entités + relations typées), Credo AI / watsonx.governance (fiche de
gouvernance, risque EU AI Act), Productboard / Jira Product Discovery (intake, scoring, dédoublonnage),
Typeform conversationnel (intake assisté).

---

## Démarrage rapide (Docker)

```bash
cp .env.example .env
# Renseigne LLM_MODEL + la clé du fournisseur (ANTHROPIC_API_KEY, OPENAI_API_KEY, OLLAMA_API_BASE…)
docker compose up --build
```

- Frontend : http://localhost:3000
- API + Swagger : http://localhost:8000/docs
- Comptes de démo : `admin@example.com / admin1234` (admin), `marie.dupont@example.com / demo1234` (reviewer),
  `karim.benali@example.com / demo1234` (requester)

Au premier démarrage, 26 use cases de démo (banque/assurance, doublons et dépendances volontaires) sont
chargés et analysés **sans LLM** (entités partagées + similarité lexicale). Avec une clé LLM, clique
**Analyze portfolio** sur le dashboard pour obtenir les relations jugées par le LLM.

### Sans Docker (dev)

```bash
# Backend — Postgres avec pgvector requis (ou SQLite pour jouer : DATABASE_URL=sqlite+aiosqlite:///./dev.db)
cd backend && uv venv && source .venv/bin/activate && uv pip install -e ".[dev]"
uvicorn app.main:app --reload          # lit ../.env

# Frontend
cd frontend && npm install && npm run dev   # proxy /api -> localhost:8000

# Tests backend (SQLite, LLM scripté, aucune clé nécessaire)
cd backend && pytest
```

---

## Fonctionnalités

| Bloc | Ce que ça fait |
|---|---|
| **Intake conversationnel** | Agent LangGraph `extract → assess → ask / wrap_up`. Une invocation = un tour ; l'humain est toujours dans la boucle. Extraction structurée à chaque message, questions groupées (3 max) par priorité (`ask_priority`, `required`, `weight`), hypothèses listées pour confirmation. Le demandeur peut éditer le canvas à tout moment. |
| **Paste a brief** | Normalisation one-shot d'un email / brief / slide → draft, puis l'interview ne porte que sur les trous. |
| **Blank canvas** | Saisie manuelle, bouton ✨ « Improve with AI » par champ. |
| **Templates YAML versionnés** | `backend/app/templates/*.yaml`. Sections, types (`text, longtext, enum, multi_enum, list, number, date, person, entity_list`), `required`, `weight` (complétude), `ai_hint` (guidage LLM), `entity_kind` (extraction d'entités), règles de scoring, champs d'embedding. Nouveau fichier ou `version` supérieure + **Sync**. Les fiches gardent la version avec laquelle elles ont été créées. |
| **Registry** | Table filtrable (statut, BU, capacité, tier), tri par scores, complétude, nombre de relations à revoir. |
| **Workflow** | `draft → submitted → qualified → approved → in_build → in_production → retired` (+ `rejected`, retour en `draft`). Portes : qualification (reviewer), investissement (reviewer, tier EU AI Act validé obligatoire), go-live. Journal des décisions avec commentaire, versions des champs. |
| **Scoring** | Valeur (urgence, drivers, valeur annuelle log-scale, population) × Faisabilité (data readiness, complexité, impact change, HITL) → priorité. Entièrement déclaré dans le YAML. |
| **EU AI Act** | Suggestion LLM (tier + rationale + mitigations + confiance) ; validation par un reviewer requise avant approbation. |
| **Recoupements & dépendances** | 1) **Structurel** : entités (systèmes, données, équipes, modèles) canonicalisées et partagées → relations `shares_entity`. 2) **Sémantique** : embeddings (pgvector) → candidats au-dessus d'un seuil → juge LLM classe `duplicate / overlap / complementary / depends_on / enables / none` avec explication et action suggérée. Les relations suggérées passent par une **inbox** (confirmer / requalifier / rejeter) ; les décisions humaines ne sont jamais écrasées. Lancement automatique à chaque création/modification, ou sur tout le portefeuille. |
| **Graphe** | React Flow : use cases + entités partagées, arêtes typées et colorées, suggestions en pointillés, focus au clic. |
| **Import CSV/XLSX** | Mapping colonnes → champs proposé par heuristique + LLM, modifiable ; colonnes non mappées injectées en texte libre pour enrichissement IA ; analyse du portefeuille en fin d'import. |
| **Auth & rôles** | JWT, `requester` (ses fiches), `reviewer` (portes, risque, relations, import), `admin` (users, templates). |

---

## Interface MCP

Le backend expose un serveur **MCP (Model Context Protocol)** sur `/mcp` (transport Streamable HTTP) : Claude
Desktop, Claude Code, Cursor ou un agent LangGraph peuvent lire le registre, créer et faire avancer des use cases,
mener un intake conversationnel, revoir les relations suggérées et lancer l'analyse de portefeuille — avec les
mêmes règles de rôles que l'API REST.

1. Dans l'UI, page **MCP & API** → *Create key* (clé personnelle `uch_…`, affichée une seule fois, hash stocké).
2. Côté client :

```bash
# Claude Code
claude mcp add --transport http usecase-hub http://localhost:3000/mcp \
  --header "Authorization: Bearer uch_..."
```

```json
// Claude Desktop (via mcp-remote)
{ "mcpServers": { "usecase-hub": {
  "command": "npx",
  "args": ["-y", "mcp-remote", "http://localhost:3000/mcp", "--header", "Authorization:Bearer uch_..."] } } }
```

| Tools | Rôle |
|---|---|
| `list_use_cases`, `get_use_case`, `get_history`, `search_similar` | lecture du registre, recherche sémantique anti-doublon |
| `normalize_text`, `create_use_case`, `update_use_case`, `improve_field` | texte libre → canvas, création, mise à jour |
| `start_intake`, `continue_intake`, `convert_intake` | interview guidée pilotée depuis n'importe quel client |
| `transition_use_case`, `suggest_risk`, `validate_risk` | workflow et tier EU AI Act |
| `list_relations`, `review_relation`, `add_relation` | doublons, recoupements, dépendances |
| `analyze_use_case`, `analyze_portfolio`, `portfolio_dashboard`, `list_entities` | intelligence de portefeuille |
| `list_templates`, `get_template` | schéma des champs |

Resources : `usecase://UC-0001`, `template://ai-usecase-canvas`. Prompts : `qualify_use_case`, `register_from_brief`.
La clé API fonctionne aussi sur l'API REST (`Authorization: Bearer uch_…`).

Implémentation : `backend/app/mcp_server.py` (protocole, auth) et `backend/app/mcp_tools.py` (fonctions qui
réutilisent les endpoints FastAPI, donc mêmes permissions et validations).

---

## Architecture

```
backend/app
├── main.py                 FastAPI, lifespan (init DB, sync templates, admin, seed)
├── config.py               pydantic-settings (.env)
├── models.py               SQLAlchemy : users, templates, use_cases, versions, decisions,
│                           entities, use_case_entities, relations, intake_sessions, import_jobs, analysis_runs
├── auth.py                 JWT + bcrypt + rôles
├── api/                    auth (+ clés API), templates, usecases, intake, portfolio, imports
├── mcp_server.py           serveur MCP (Streamable HTTP sur /mcp, auth bearer, tools/resources/prompts)
├── mcp_tools.py            implémentations des tools, réutilisent les endpoints
├── services/
│   ├── templates.py        moteur de template : coercition, complétude, scoring, texte d'embedding
│   ├── llm.py              LiteLLM (complete_json robuste) + embeddings (LiteLLM ou fallback "hash")
│   ├── intake_agent.py     graphe LangGraph de l'interview
│   ├── normalizer.py       texte libre → champs ; amélioration de champ ; tier EU AI Act
│   ├── analysis.py         entités, relations structurelles, candidats sémantiques, juge LLM
│   ├── importer.py         lecture CSV/XLSX, mapping IA, import par lignes
│   ├── usecases.py         création / mise à jour / analyse en tâche de fond
│   └── workflow.py         machine à états et portes
└── templates/default.yaml  AI Use Case Canvas v1

frontend/src
├── pages/   Dashboard, Registry, UseCaseDetail (canvas / relations / entities / history),
│            Intake (chat + canvas live), NewUseCase, RelationsInbox, Graph, Import, Templates, Admin
├── components/ Canvas (rendu + édition de tout template), Layout, ui
└── lib/     api.ts (client + types), auth.tsx
```

### Choix LLM (LiteLLM)

`LLM_MODEL` accepte n'importe quelle chaîne LiteLLM : `anthropic/claude-sonnet-4-5`, `openai/gpt-4.1-mini`,
`ollama/llama3.1`, `azure/<deployment>`, `mistral/mistral-large-latest`… `LLM_FAST_MODEL` (optionnel) sert
aux appels courts (questions, mapping, risque). `EMBEDDING_MODEL=hash` fonctionne sans clé (bag-of-words
hashé, suffisant pour la démo) ; en production, préférer `openai/text-embedding-3-small`,
`voyage/voyage-3-lite` ou `ollama/nomic-embed-text` et ajuster `EMBEDDING_DIMS`.

### Déploiement k3s / Hostinger

Les deux images (`backend`, `frontend`) sont stateless ; seul Postgres (image `pgvector/pgvector:pg16`)
porte l'état, plus un volume `uploads` pour les fichiers d'import. Le frontend nginx proxifie `/api`
vers le service `backend:8000`. Variables sensibles : `SECRET_KEY`, clés LLM, `ADMIN_PASSWORD`.

---

## Feuille de route suggérée

- SSO OIDC (Keycloak / Entra ID) en remplacement de l'auth locale
- Notifications (Teams / mail) sur passage de porte et nouvelles suggestions
- Export du registre (CSV, Excel, rapport PDF) et API webhook
- Templates par type de use case (GenAI vs ML classique) et champs conditionnels
- Évaluations LangSmith de l'agent d'intake et du juge de recoupements
