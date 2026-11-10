# QueryPilot

**AI-Powered PostgreSQL Performance Investigator**

QueryPilot analyzes SQL statements and provides evidence-backed PostgreSQL optimization recommendations using static analysis, hybrid retrieval, and an AI-generated investigation report.

## Features

- SQL parsing and static analysis across supported SQL statement types.
- Deterministic SQL optimization rules.
- PostgreSQL documentation retrieval using hybrid semantic and keyword search.
- Curated PostgreSQL optimization cases.
- LangGraph investigation workflow with Groq.
- Structured investigation reports with findings and recommendations.
- Supabase-backed knowledge storage and vector search.
- Application-level AI usage budgeting and reliability controls.

## Architecture

- **Frontend:** React, TypeScript, Vite, Tailwind CSS.
- **Backend:** Python, FastAPI, Pydantic.
- **AI workflow:** LangGraph and Groq.
- **Knowledge platform:** Supabase PostgreSQL and pgvector.
- **Retrieval:** Supabase Edge Functions.
- **SQL parsing:** pglast.

## Safety

QueryPilot is an analysis and recommendation tool.

It does not execute submitted SQL against a user's database. Its investigation reports are based on static SQL analysis, retrieved evidence, and deterministic optimization rules.

Execution plans and actual database runtime measurements are not collected by the static analysis workflow.

## Project structure

- `apps/web/` — React frontend.
- `services/api/` — FastAPI backend.
- `supabase/` — database migrations and Edge Functions.
- `scripts/` — project scripts.
- `.benchmarks/` — benchmark artifacts.
- `docs/` — project documentation.
- `knowledge/` — knowledge resources.

## Local development

### Backend

From the repository root:

```powershell
python -m pip install -r requirements.txt
cd services/api
python -m uvicorn app.main:app --reload
```

### Frontend

In another terminal:

```powershell
cd apps/web
npm ci
npm run dev
```

Configure the required Supabase and Groq environment variables using `.env.example`. Never commit actual credentials.

## Tests

Run backend tests from the repository root:

```powershell
python -m pytest services/api/tests -q
```

Run frontend checks:

```powershell
cd apps/web
npm run build
npm run lint
```

## Configuration

The backend uses environment-based configuration for Supabase, Groq, and application settings.

Keep credentials in local environment files. Do not expose service-role keys or API tokens in frontend code.

## Deployment status

The repository currently documents the local development architecture. Production hosting configuration must be completed and independently verified before describing the application as publicly deployed.

## Project status

Core implementation and end-to-end integration verification are complete. See `docs/` and `.benchmarks/` for supporting project documentation and benchmark results.