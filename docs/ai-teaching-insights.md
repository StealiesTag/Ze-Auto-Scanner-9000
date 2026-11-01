# AI Teaching Insights

## Data flow

The Analytics page continues to calculate the numeric results locally. The AI service is separate from scanning and grading. It receives an assessment title and subject, question numbers, teacher-entered learning objectives, answer key letters, aggregated answer distributions, and finalized class-level scores. It does not receive student names, student numbers, submission IDs, scanned images, or individual response rows. Draft scans, submissions awaiting review, and submissions with unresolved responses are excluded.

Question and objective performance signals are calculated deterministically before any provider request. Output is explicitly a suggestion; observations and possible interpretations are separate fields. The teacher reviews generated content, can edit its structured JSON, and saves it only to the teaching suggestions record. This feature cannot write or change grades.

## Provider configuration

Set these values in the ignored local `.env` file:

```env
AI_BASE_URL=https://api.openai.com/v1
AI_MODEL=your-json-capable-model
AI_API_KEY=your-provider-key
AI_TIMEOUT_SECONDS=30
```

The service uses the OpenAI-compatible `/chat/completions` JSON interface. Keep credentials out of source control. Check the selected provider's retention and data-use terms before sending assessment aggregates. Without both a key and model, Analytics displays a clear unavailable state and keeps its deterministic reporting available. No AI request occurs on normal page load.

## Cache and migration

Generated content is cached by assessment, output type, duration/selected focus, and a SHA-256 fingerprint of the finalized analytics payload. A correction or newly finalized submission changes the fingerprint; old suggestions are hidden from the current results and cannot be mistaken for current recommendations. Regeneration updates the cached output for the same fingerprint.

Migration `20261101_0003` adds an assessment-scoped `teaching_insights` table with a cascading foreign key, type/duration constraints, cache uniqueness, and an index. It does not alter student, assessment, question, submission, or response records. Downgrade drops this table and its saved suggestions. Back up the database and review the generated migration SQL before deployment. Apply with:

```powershell
python -m flask --app app db upgrade
```

Do not apply the migration to an existing environment until the deployment owner has approved it. The feature's tests use the isolated PostgreSQL test database and do not need a live provider key.
