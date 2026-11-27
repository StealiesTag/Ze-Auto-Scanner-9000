# Paparazzi ESP32-S3 USB setup

## Install and flash once

1. On the Paparazzi computer, update its virtual environment with `python -m pip install -r requirements-local.txt` for SQLite or `python -m pip install -r requirements.txt` for PostgreSQL. The Windows launcher installs missing PySerial and psutil automatically. No database migration is required.
2. In Arduino IDE, install the Espressif ESP32 board package and ArduinoJson **7.x**. The sketch also uses WiFi, ESPmDNS, HTTPClient, Preferences, and the camera driver from the ESP32 board package.
3. Copy the CameraWebServer example that matches the installed ESP32 package. Retain its companion files, including `board_config.h`, `app_httpd.cpp`, and `camera_index.h`. Replace the example sketch with `PaparazziCamera.ino` and give the sketch and directory the same name.
4. Select the exact board and camera model from the working installation. Retain its confirmed flash, partition, PSRAM, upload, and clock settings. If the board uses native USB for `Serial`, use its USB data connector and enable **USB CDC On Boot**. For a USB-to-UART connector, select the corresponding serial port and keep the working UART settings. The repository does not include the original board configuration, so verify these values with the camera builder.
5. Flash over USB, then close Arduino Serial Monitor. The firmware uses 115200 baud. It preserves the original GPIO mapping, 20 MHz camera clock, framebuffer rules, SVGA/quality 20 defaults, and sensor adjustments. The upgrade reads earlier saved Wi-Fi credentials when present. Device IDs now use the full chip MAC, so select the camera again after upgrading older firmware.

## Set up and use the camera

1. Start Paparazzi on the computer connected to the camera. Open it at `http://127.0.0.1:5000`, choose **Device setup**, select the camera port, and click **Connect**. Use a USB data cable.
2. Enter the 2.4 GHz Wi-Fi network and password. Leaving the password unchanged reuses the saved value. Select **Replace saved password or use an open network** to explicitly clear it for an open network.
3. Check the suggested server address. If the computer has moved networks, choose the current interface under **Advanced network and image settings**. For manual entry use an RFC1918 LAN IPv4 address and the running Flask port, ending in `/api/camera/ingest`.
4. Use **Test Wi-Fi**, **Test server**, or **Test and save setup**. The server probe uses a dedicated authenticated heartbeat and creates no scan jobs or submissions. Check the Wi-Fi, camera sensor, and server indicators.
5. Choose **Use for scanning** to save this camera's current capture and stream URLs. Disconnect USB after it checks in wirelessly. Start a scan from **Auto scan**. The existing printer-controlled still capture supplies images to the queue. A longer minimum wait in advanced settings applies to printer captures while the camera's heartbeat is current. The printer's normal settling time still applies.

The Windows launcher binds Flask for LAN heartbeat access and opens the browser on loopback. A manual launch can set `HOST=0.0.0.0` and the desired `PORT`. Keep teacher access local or protect it using the app's existing authentication. USB management requires a loopback connection and a loopback HTTP host. On Windows, allow the application on the private network if the firewall prompts. Some school networks isolate wireless clients. Ask the network administrator for a compatible network if the camera joins Wi-Fi but the server test fails.

If the COM port is unavailable, close serial-monitor apps and reconnect the data cable. If a camera reset is detected during driver initialization, the next boot skips camera startup to make USB recovery possible. A failed camera initialization does not prevent saving working network settings with the existing image defaults. Restore or check camera hardware before running scans.

## USB protocol and API

Use UTF-8 JSON objects followed by a newline. Maximum frame size is 2,047 bytes before the newline. Requests carry `request_id`, `command`, and optional `data`. Responses echo the ID and return `success`, optional `result`, or an error `code`. Boot noise and unmatched responses are ignored by the host.

| Command | Data or response |
| --- | --- |
| `PING` | Acknowledgment |
| `GET_DEVICE_INFO`, `GET_CONFIG`, `GET_STATUS` | Protocol version 1, public settings, Wi-Fi and sensor status |
| `TEST_WIFI`, `TEST_SERVER`, `SET_CONFIG` | SSID, optional password, server URL, device name, interval, quality, resolution, and host-issued heartbeat token |
| `RESTART` | Acknowledgment before reboot |
| `FACTORY_RESET` | `confirmation` equal to the device ID |

The host exposes `GET /api/devices/ports`, `GET /api/devices`, `GET /api/devices/network?peer=...`, and `POST /api/devices/connect` with a port. Use `POST /api/devices/<id>/command` with the command and settings, `POST /api/devices/<id>/disconnect` to release USB, or `POST /api/devices/<id>/use-camera` to select the camera for scanning. Browser mutations require the existing CSRF token.

The camera posts its status about every 30 seconds to `/api/devices/heartbeat`. The per-device bearer token grants heartbeat access only. The status view times out after 75 seconds and tracks the most recent queued capture when it is associated with that camera ID. The USB session and heartbeat registry are process-local. Run the local installation as one web process.

Wi-Fi passwords travel through the local browser and USB connection during setup and are stored on the ESP32 only. NVS is unencrypted by default. Use a trusted setup computer and physical access controls. LAN heartbeat traffic uses HTTP and is appropriate for a trusted local network. A changed Paparazzi application secret invalidates old heartbeat tokens until the device is provisioned again. Configuration writes occur after successful verification and only when the serialized settings differ.

## Physical validation checklist

- [ ] Flash the actual board and enumerate it through USB on a clean setup.
- [ ] Connect to 2.4 GHz Wi-Fi and verify the server probe without adding a queue job.
- [ ] Power cycle and verify the saved configuration, stable ID, and wireless heartbeat.
- [ ] Change networks and update the computer's server address.
- [ ] Disconnect USB and capture a real sheet through the existing wireless scanner.
- [ ] Try invalid credentials and an unreachable server. Verify that the old configuration reconnects.
- [ ] Unplug USB mid-configuration and interrupt power during a save. Reconnect and verify recovery.
- [ ] Test USB setup with a camera sensor initialization failure.
- [ ] Test VGA and SVGA with the installed sensor and memory while scanning is stopped.
- [ ] Verify the original OMR, teacher review, and grading workflow on a printed sheet.

Firmware compilation, flashing, and these physical checks need the board-specific project and hardware.
