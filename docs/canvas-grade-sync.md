# Canvas grade sync

## Supported local workflow

This app is a localhost-only, single-teacher prototype with no application login. Canvas routes reject non-loopback requests and use the app's CSRF protection. A teacher connects with a Canvas personal access token, then selects an accessible teacher course and an ordinary published points assignment. The connection page stores the token encrypted using Windows DPAPI for the current Windows account, or Fernet when `CANVAS_TOKEN_ENCRYPTION_KEY` and the `cryptography` package are configured.

For a Canvas account configured with a public institutional host:

1. Open **Canvas** while using the app on the Paparazzi computer.
2. Enter the institution's HTTPS Canvas base URL and the teacher's personal access token. The token is sent to the local Flask server and is never returned to the browser after storage.
3. Link an assessment to a course and published points assignment.
4. Confirm roster matching. SIS IDs are matched as strings, preserving leading zeroes. Ambiguous or unmatched IDs require a manual mapping to a user in that course roster.
5. Review the grade preview. Only finalized submissions are listed. Existing Canvas grades are skipped unless the teacher checks the explicit overwrite option.
6. Select rows and submit. The service uses the individual submission grade endpoint, reads the grade back, and records success only after the stored score matches. Failed writes are visible in history and may be retried after the current score/mapping is checked.

Canvas scores are proportional to the assessment result and rounded half-up to two decimal places. The sync is opt-in, does not bulk-submit, and does not include unanswered/review-required records. API calls use HTTPS, same-origin pagination, bounded response sizes, and server-side timeouts. Tokens and error messages are not written to logs.

## Limitations and deployment

The current integration uses a manually supplied personal access token for a local single-teacher installation. Canvas documents these tokens as password-equivalent credentials and recommends OAuth for multi-user applications. Do not expose this unauthenticated Flask app to a network or deploy this PAT flow as an institutional multi-teacher service. Production deployment needs user authentication, per-teacher OAuth, protected key management, audit policy, and institution approval. No real Canvas institution credentials or grade writes were used in development; tests use mocked API responses.

Set `CANVAS_TOKEN_ENCRYPTION_KEY` only when not using Windows DPAPI. Generate a Fernet key with Python's `cryptography` package and keep it in the secret manager/environment, not source control. Losing/changing that key makes Fernet-encrypted tokens unreadable. DPAPI tokens are decryptable only by the same Windows account/machine data protection context; reconnect Canvas after moving the database to another account or machine.

The app supports only ordinary, individual, published assignments graded in points. Quizzes, group assignments, unpublished assignments, percentage/letter grading, and zero-point assignments are rejected. A teacher must verify matching and the preview before choosing which grades to submit.

## Database

Revision `20261105_0008` adds encrypted Canvas connection state, assessment/course/assignment links, teacher-verified roster mapping, and per-student grade-sync audit history. Revision `20261105_0009` adds source-PDF question mapping metadata. Back up PostgreSQL and review the migration chain before upgrading an existing installation:

```powershell
python -m flask --app app db history
python -m flask --app app db upgrade
```
