"""Send one local JPEG or PNG to a running Paparazzi camera ingestion API."""
from __future__ import annotations

import argparse
import json
import mimetypes
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path, help="Local V3 answer sheet JPEG or PNG")
    parser.add_argument("--url", default="http://127.0.0.1:5000/api/camera/ingest")
    parser.add_argument("--camera-id", default="local-test-camera")
    parser.add_argument("--capture-id", default=None)
    args = parser.parse_args()

    if not args.image.is_file():
        parser.error(f"Image does not exist: {args.image}")
    content_type = mimetypes.guess_type(args.image.name)[0]
    if content_type not in {"image/jpeg", "image/png"}:
        parser.error("Choose a .jpg, .jpeg, or .png file")

    boundary = "----PaparazziCamera" + uuid.uuid4().hex
    image_bytes = args.image.read_bytes()
    fields = {"camera_id": args.camera_id, "capture_id": args.capture_id or uuid.uuid4().hex}
    parts = []
    for name, value in fields.items():
        parts.extend((
            f"--{boundary}\r\n".encode(),
            f'Content-Disposition: form-data; name="{name}"\r\n\r\n'.encode(),
            value.encode(), b"\r\n",
        ))
    parts.extend((
        f"--{boundary}\r\n".encode(),
        f'Content-Disposition: form-data; name="image"; filename="{args.image.name}"\r\n'.encode(),
        f"Content-Type: {content_type}\r\n\r\n".encode(), image_bytes, b"\r\n",
        f"--{boundary}--\r\n".encode(),
    ))
    request = Request(
        args.url, data=b"".join(parts), method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "Accept": "application/json"},
    )
    try:
        with urlopen(request, timeout=30) as response:
            print(json.dumps(json.loads(response.read()), indent=2))
            return 0
    except HTTPError as error:
        print(error.read().decode("utf-8", errors="replace"))
        return 1
    except URLError as error:
        parser.error(f"Could not reach Paparazzi at {args.url}: {error.reason}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
