# Voice AI Patient Registration

A voice agent that registers new U.S. patients over a real phone call, saves them to a database, and exposes them through a REST API and a simple dashboard.

| | |
|---|---|
| **Phone number** | +1 (386) 293-8018 |
| **API base URL** | https://voice-patient-registration-jzke.onrender.com |
| **Dashboard** | https://voice-patient-registration-jzke.onrender.com/dashboard |
| **Swagger docs** | https://voice-patient-registration-jzke.onrender.com/docs |
| **Health check** | https://voice-patient-registration-jzke.onrender.com/health |
| **Repository** | https://github.com/sidramasoodahmed-hub/voice-patient-registration |

## Testing notes

- **No credentials are needed** to use the REST API or dashboard.
- A seed patient exists: Jane Doe, 555-555-0100.
- **Cold starts:** the server runs on Render's free tier, which sleeps after about 15 minutes of inactivity. An uptime monitor pings `/health` every 5 minutes to keep it awake. If the first request is slow, wait 30-60 seconds and retry.
- **Telephony note:** the number is a Vapi-provisioned U.S. number. Calls from outside the U.S. may not connect because of carrier restrictions on this kind of number. The author could not place a call from an international SIM and verified the full flow (conversation, tool calls, database write, dashboard) with Vapi's browser-based "Talk to Assistant" test. U.S. callers should connect normally.
- Please use fake data only.

## Architecture

```
Caller -> Vapi (telephony + Deepgram STT + GPT-4o + TTS)
              | HTTPS tool calls (lookup_patient, save_patient)
              v
        FastAPI on Render  ->  PostgreSQL on Neon
              ^
        REST API + dashboard
```

| File | Responsibility |
|---|---|
| `schemas.py` | Validation and sanitisation (single source of truth, used by both the REST API and the voice tools) |
| `db.py` | SQLAlchemy models, DB constraints, seed data |
| `main.py` | REST API, Vapi webhook (`/vapi/webhook`), dashboard |
| `prompts/system_prompt.md` | The voice agent's system prompt (also configured in Vapi) |

Separation of concerns: telephony and speech are handled by Vapi, conversation logic by the LLM prompt, validation by `schemas.py`, and persistence by `db.py`. The voice agent never writes to the database directly. It calls the same validated service code as the REST API.

Nothing is written until the caller verbally confirms the read-back, so a dropped call never leaves a partial record.

## Tech choices

- **Vapi:** the fastest way to get a real U.S. number with STT/TTS handled, so time goes into the prompt and backend.
- **GPT-4o:** reliable tool calling and good at handling corrections.
- **FastAPI + Pydantic:** strict server-side validation with per-field error messages that the LLM can use to re-ask only the failing field.
- **PostgreSQL on Neon:** permanent storage that survives restarts. Render's free disk is wiped on restart, so SQLite there would lose data.
- **psycopg 3:** the Postgres driver, selected explicitly in `db.py` (`postgresql+psycopg://`) so it works on newer Python versions.
- **Render:** deploys directly from GitHub and gives a public HTTPS URL.
- **SQLite fallback:** used automatically for local development when `DATABASE_URL` is not set.

## Setup (local)

```bash
python -m venv venv
venv\Scripts\activate          # Mac/Linux: source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload
```

Open http://localhost:8000/dashboard and http://localhost:8000/docs.

## Environment variables

| Variable | Required | Description |
|---|---|---|
| `DATABASE_URL` | Yes (production) | Postgres connection string (Neon). Defaults to `sqlite:///./patients.db` locally. |
| `VAPI_SECRET` | Yes (production) | Shared secret. The webhook rejects requests that lack a matching `X-Vapi-Secret` header. Configured on the Vapi tools and in Render. If unset, the check is skipped (local development). |
| `PYTHON_VERSION` | Render only | `3.11.8` |

No API keys or secrets are stored in the source code.

## Deployment

1. **Neon:** create a project and copy the connection string.
2. **Render:** New Web Service from the GitHub repo.
   - Build command: `pip install -r requirements.txt`
   - Start command: `uvicorn main:app --host 0.0.0.0 --port $PORT`
   - Set the environment variables above.
3. **Vapi:**
   - Create the `lookup_patient` and `save_patient` tools with Server URL `https://voice-patient-registration-jzke.onrender.com/vapi/webhook` and the header `X-Vapi-Secret` set to the shared secret.
   - Create the assistant (GPT-4o, Deepgram transcriber) with the system prompt from `prompts/system_prompt.md`.
   - Attach a U.S. phone number to the assistant.
4. **Uptime monitor:** ping `/health` every 5 minutes to avoid free-tier cold starts.

Tables are created and the seed record is inserted automatically on first start.

## REST API

All responses use the envelope `{ "data": ..., "error": null }`.

| Method | Endpoint | Description |
|---|---|---|
| GET | `/patients` | List patients. Optional filters: `?last_name=`, `?date_of_birth=`, `?phone_number=` |
| GET | `/patients/{id}` | Get one patient by UUID |
| POST | `/patients` | Create a patient (201) |
| PUT | `/patients/{id}` | Partial update |
| DELETE | `/patients/{id}` | Soft delete (sets `deleted_at`) |
| GET | `/patients/{id}/calls` | Call summary and transcript for a patient |
| GET | `/health` | Health check |

Status codes: 200, 201, 400 (bad UUID or filter), 404, 422 (validation, with per-field details), 500.

Example:

```bash
curl -X POST https://voice-patient-registration-jzke.onrender.com/patients \
  -H "Content-Type: application/json" \
  -d '{"first_name":"John","last_name":"Smith","date_of_birth":"01/15/1985","sex":"male","phone_number":"(415) 555-0123","address_line_1":"1 Main St","city":"Austin","state":"tx","zip_code":"78701"}'
```

## Prompt design

The full prompt is in `prompts/system_prompt.md`. Key decisions:

- **Persona and voice style:** a warm intake coordinator, one short question per turn, varied acknowledgements, digits spoken one at a time.
- **Step-based flow:** name, phone, duplicate check, demographics and address, optional extras offer, read-back, save, result.
- **Early duplicate check:** `lookup_patient` runs as soon as a valid phone number is captured.
- **Confirmation gate:** the agent must never call `save_patient` before an explicit "yes" to the read-back.
- **Field-specific validation:** the prompt tells the agent how to re-prompt for a short phone number, a future date of birth, and so on.
- **Structured tool errors:** the server returns which fields failed and an instruction, so the agent re-asks only those fields.
- **Corrections and restart:** the latest value wins, spelled-out letters override earlier guesses, and "start over" discards everything.
- **Safety:** discloses being an AI if asked, gives no medical advice, and directs emergencies to 911.

## Edge cases handled

| Scenario | Behaviour |
|---|---|
| Invalid or future date of birth | Rejected in the prompt and again server-side. The agent re-asks only that field. |
| Invalid phone number | Same two-layer validation. |
| Database write fails | The tool returns `system_error`. The agent apologises, retries once, then asks the caller to call back. It never claims success and never goes silent. |
| Call drops mid-call | Nothing is saved until confirmation, so no partial records exist. The call end is still logged. |
| Caller wants to start over | The prompt discards collected data and restarts from the name. |
| Duplicate phone number | The agent offers to update the existing record instead of creating a new one. |
| Corrections | The latest value wins, and the agent repeats back the corrected field. |
| Wrong or missing webhook secret | The webhook returns 401 and logs a warning. |

## Observability

- The final collected payload is logged to stdout on every save (`FINAL_PAYLOAD call=... data=...`).
- Call-end events are logged with their end reason and summary (`CALL_END`).
- Rejected webhook requests are logged (`Webhook rejected`).
- Transcripts and summaries are stored in the `call_logs` table, linked to the patient.

## Bonus features implemented

- Duplicate detection by phone number, offering update instead of create
- Dashboard at `/dashboard` (search, click a row for details and call history)
- Call transcript and summary stored and linked to the patient
- Spanish support through the prompt (if the caller says "Hablo español", the agent switches to Spanish and stores `preferred_language` as Spanish)

## Known limitations and trade-offs

- **Free-tier cold starts:** Render sleeps after inactivity. An uptime monitor mitigates this, but the first call after a long idle period could still be slow.
- **No authentication on the REST API or dashboard** (demo with fake data only). Only the Vapi webhook is protected, by a shared secret.
- **International callers:** the Vapi-provisioned number may not accept calls from outside the U.S. (see Testing notes).
- **Phone-based duplicate detection** can match family members who share a number, and it relies on the caller giving the number accurately.
- **Spanish** depends on the STT/TTS language configuration in Vapi. It is lightly tested.
- **No rate limiting.**
- **No automated tests.**
- **Simple schema management:** tables are created with `create_all`, with no migrations.
- **Not HIPAA compliant.** This is a technical demo, and no real patient data should be used.

## Next steps

1. Authentication and role-based access for the API and dashboard
2. pytest suite for the API and tool handlers
3. Appointment scheduling tool after registration
4. Alembic migrations
5. Retry queue for failed database writes
6. Fuzzy duplicate matching on name and date of birth, not just phone
