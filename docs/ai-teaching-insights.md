# AI Teaching Insights

## Data flow

The Analytics page continues to calculate the numeric results locally. The AI service is separate from scanning and grading. It receives an assessment title and subject, question numbers, teacher-entered learning objectives, answer key letters, aggregated answer distributions, and finalized class-level scores. It does not receive student names, student numbers, submission IDs, scanned images, or individual response rows. Draft scans, submissions awaiting review, and submissions with unresolved responses are excluded.

Question and objective performance signals are calculated deterministically before any provider request. Output is explicitly a suggestion; observations and possible interpretations are separate fields. The teacher reviews generated content, can edit its structured JSON, and saves it only to the teaching suggestions record. This feature cannot write or change grades.

## Provider configuration

Set these values in the ignored local `.env` file:

```env
AI_BASE_URL=https://api.meta.ai/v1
AI_MODEL=muse-spark-1.3
AI_REASONING_EFFORT=low
MODEL_API_KEY=your-meta-model-api-key
AI_TIMEOUT_SECONDS=180
```

The service uses Meta Model API's OpenAI-compatible `/chat/completions` JSON interface. `MODEL_API_KEY` is a separate Meta Model API credential; do not reuse `MUSE_API_KEY`, which is for the image locator. The application also accepts `META_MODEL_API_KEY` or the existing generic `AI_API_KEY`. Keep the key in the ignored local `.env` file and out of source control. Requests explicitly set `store: false`. Meta receives only the aggregate teaching payload described above, and only when a teacher requests suggestions. Without a key, Analytics keeps deterministic reporting available and shows the AI feature as unconfigured. No AI request occurs on normal page load.

## Cache and migration

Generated content is cached by assessment, output type, duration/selected focus, and a SHA-256 fingerprint of the finalized analytics payload. A correction or newly finalized submission changes the fingerprint; old suggestions are hidden from the current results and cannot be mistaken for current recommendations. Regeneration updates the cached output for the same fingerprint.

Migration `20261101_0003` adds an assessment-scoped `teaching_insights` table with a cascading foreign key, type/duration constraints, cache uniqueness, and an index. It does not alter student, assessment, question, submission, or response records. Downgrade drops this table and its saved suggestions. Back up the database and review the generated migration SQL before deployment. Apply with:

```powershell
python -m flask --app app db upgrade
```

Do not apply the migration to an existing environment until the deployment owner has approved it. The feature's tests use the isolated PostgreSQL test database and do not need a live provider key.
