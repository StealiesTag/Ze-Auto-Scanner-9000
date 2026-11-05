# ESP32 CameraWebServer poller

Paparazzi can retrieve a JPEG repeatedly from an existing ESP32-S3 CameraWebServer endpoint such as `http://192.168.1.73/capture`. The firmware is unchanged. The poller is a separate process and directly invokes the same `ingest_camera_image_bytes` service used by `POST /api/camera/ingest`; it does not send an HTTP request back to Paparazzi.

## Configuration

Set these values in the server environment (for PowerShell, use `$env:NAME = 'value'`) before starting Paparazzi:

| Setting | Default | Purpose |
| --- | --- | --- |
| `PAPARAZZI_CAMERA_URL` | empty | Fixed CameraWebServer JPEG URL, usually `/capture` |
| `PAPARAZZI_CAMERA_ID` | `paparazzi-esp32-01` | Stable per-camera identity for replacement state and metrics |
| `PAPARAZZI_CAMERA_POLL_INTERVAL` | `3.5` seconds | Delay between retrieval attempts |
| `PAPARAZZI_CAMERA_TIMEOUT` | `5` seconds | HTTP connection/read timeout |
| `CAMERA_CONTROL_TOKEN` | empty | Required secret for dashboard start/stop controls |

For example:

```powershell
$env:PAPARAZZI_CAMERA_URL = 'http://192.168.1.73/capture'
$env:PAPARAZZI_CAMERA_ID = 'paparazzi-esp32-01'
$env:PAPARAZZI_CAMERA_POLL_INTERVAL = '3.5'
$env:PAPARAZZI_CAMERA_TIMEOUT = '5'
$env:CAMERA_CONTROL_TOKEN = 'set-a-long-random-secret-here'
```

The URL is server configuration only; the dashboard has no URL input and control routes do not accept one. Startup accepts HTTP(S) URLs with a literal private, loopback, or link-local IP address only. Hostnames, public IPs, credentials, query strings, and URL fragments are rejected to reduce SSRF and rebinding risk. Keep the configured host limited to the intended camera device and protect the server environment.

## Start services

Apply reviewed migrations to the intended environment first; the development database has not been migrated by this change. Start the Flask app, one scan worker, and one camera poller in separate terminals or managed services:

```powershell
python -m flask --app app run
python -m services.scan_worker
python -m services.camera_poller
```

Select a complete V4 assessment in Automatic Scanning. Enter `CAMERA_CONTROL_TOKEN` in the dashboard form and choose **Start polling**. The separate poller process sees the persisted flag and starts fetching. **Stop polling** disables that flag; SIGINT/SIGTERM also shuts down the poller gracefully. The poller holds a PostgreSQL advisory session lock keyed by camera ID, so a second poller for the same camera exits rather than double-fetching. The V4 scan worker has its own independent lock and continues processing queued drafts.

## Processing and status

Each successful JPEG fetch gets a new idempotency identifier and enters the shared validation flow. HTTP failures, timeouts, and corrupt/non-JPEG responses update connection/error status and are retried on the next interval. Invalid frame bytes are counted as rejected without being saved or treated as a physical marker-disappearance observation. Decodable frames pass through the V3 four-marker detector, visibility/confidence checks, stable-frame logic, persisted per-camera replacement state, and existing PostgreSQL queue. Only accepted originals are saved; the existing scan worker performs alignment, ID/answer recognition, and draft creation.

The Automatic Scanning dashboard reports configured URL and camera ID, poller and connection status, last successful fetch, last error, fetched frames, accepted captures, rejected frames, duplicates, and deferred observations. The URL is read from server configuration, never supplied by a browser. Start/stop POSTs require both Flask CSRF protection and the configured control token. The application currently has no user-account login layer, so restrict dashboard access at the network/reverse-proxy boundary as well.

## Database changes

Revision `20261101_0006` adds poller health fields and counters to `camera_ingestion_state`. It is additive and does not rewrite assessment, scan-draft, or submission rows. The dedicated PostgreSQL test database may be migrated for tests; do not migrate the configured development database without explicit authorization. Back up the target database before deployment. The V3 answer-sheet template and geometry remain unchanged.

## Tests and limits

Automated tests mock HTTP responses and exercise timeout/recovery, content rejection, URL restrictions, polling interval, lock lifecycle, shared ingestion behavior, duplicates, and replacement across frames. They do not validate camera focus, lighting, Wi-Fi stability, feeder timing, paper alignment, or physical exactly-once behavior. Those require the actual camera and repeated physical sheets.
