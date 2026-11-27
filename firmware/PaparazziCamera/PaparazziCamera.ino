#include <Arduino.h>
#include "esp_camera.h"
#include <WiFi.h>
#include <ESPmDNS.h>
#include <HTTPClient.h>
#include <Preferences.h>
#include <ArduinoJson.h>
#include <esp_attr.h>
#include <esp_system.h>
#include "board_config.h"

void startCameraServer();
void setupLedFlash();

namespace {
constexpr size_t kLimit = 2048;
constexpr uint32_t kWifiTimeout = 25000;
constexpr uint32_t kHeartbeatInterval = 30000;
struct Line { char text[kLimit]; };
struct Settings {
  String ssid, password, server, token, name = "Paparazzi Camera", resolution = "SVGA";
  int interval = 1000, quality = 20;
};

QueueHandle_t commands;
SemaphoreHandle_t serialOutput;
String cameraId;
Settings settings;
String stagedWifiSsid, stagedWifiPassword;
bool stagedWifiValidated = false;
bool cameraReady = false, cameraServerStarted = false, serverConnected = false;
bool durable = false;
uint32_t lastHeartbeat = 0;
volatile uint8_t lastWifiDisconnectReason = 0;
RTC_NOINIT_ATTR uint32_t cameraInitMarker;
constexpr uint32_t kCameraInitMarker = 0x507A4361;

void rememberWifiDisconnect(WiFiEvent_t event, WiFiEventInfo_t info) {
  if (event == ARDUINO_EVENT_WIFI_STA_DISCONNECTED) {
    const uint8_t reason = info.wifi_sta_disconnected.reason;
    // Ignore the disconnect requested by join() before a new attempt starts.
    if (reason != 8) lastWifiDisconnectReason = reason;
  }
}

void reply(const char *id, bool success, const char *code = nullptr, JsonObjectConst result = JsonObjectConst()) {
  JsonDocument response;
  response["request_id"] = id;
  response["success"] = success;
  if (code) response["code"] = code;
  if (!result.isNull()) response["result"] = result;
  xSemaphoreTake(serialOutput, portMAX_DELAY);
  serializeJson(response, Serial);
  Serial.println();
  xSemaphoreGive(serialOutput);
}

bool safeText(const String &value, size_t limit) {
  if (value.length() > limit) return false;
  for (size_t i = 0; i < value.length(); ++i) {
    const auto ch = static_cast<unsigned char>(value[i]);
    if (ch < 32 || ch == 127) return false;
  }
  return true;
}

bool validURL(const String &url) {
  const String suffix = "/api/camera/ingest";
  if (!url.startsWith("http://") || !url.endsWith(suffix)) return false;
  const String authority = url.substring(7, url.length() - suffix.length());
  const int colon = authority.indexOf(':');
  const String hostname = colon < 0 ? authority : authority.substring(0, colon);
  if (colon >= 0) {
    const String port = authority.substring(colon + 1);
    if (port.isEmpty() || port.length() > 5) return false;
    for (size_t i = 0; i < port.length(); ++i) if (port[i] < '0' || port[i] > '9') return false;
    if (port.toInt() < 1 || port.toInt() > 65535) return false;
  }
  IPAddress ip;
  if (!ip.fromString(hostname) || ip.toString() != hostname) return false;
  return ip[0] == 10 || (ip[0] == 172 && ip[1] >= 16 && ip[1] <= 31)
      || (ip[0] == 192 && ip[1] == 168);
}

bool parseSettings(JsonObjectConst data, Settings &next) {
  for (const char *key : {"ssid", "server_url", "heartbeat_token", "device_name", "resolution"})
    if (!data[key].is<const char *>()) return false;
  if (data.containsKey("password") && !data["password"].is<const char *>()) return false;
  if (!data["capture_interval_ms"].is<int>() || !data["jpeg_quality"].is<int>()) return false;
  next.ssid = data["ssid"].as<String>();
  if (data.containsKey("password")) next.password = data["password"].as<String>();
  next.server = data["server_url"].as<String>();
  next.token = data["heartbeat_token"].as<String>();
  next.name = data["device_name"].as<String>();
  next.interval = data["capture_interval_ms"].as<int>();
  next.quality = data["jpeg_quality"].as<int>();
  next.resolution = data["resolution"].as<String>();
  if (next.token.length() != 64) return false;
  for (size_t i = 0; i < next.token.length(); ++i)
    if (!isxdigit(static_cast<unsigned char>(next.token[i]))) return false;
  return !next.ssid.isEmpty() && safeText(next.ssid, 32) && safeText(next.password, 63)
      && (next.password.isEmpty() || next.password.length() >= 8)
      && !next.name.isEmpty() && safeText(next.name, 48)
      && safeText(next.server, 240) && validURL(next.server)
      && next.interval >= 500 && next.interval <= 60000 && next.quality >= 10 && next.quality <= 40
      && (next.resolution == "VGA" || next.resolution == "SVGA");
}

void publicSettings(JsonObject output, const Settings &value) {
  output["ssid"] = value.ssid;
  output["device_name"] = value.name;
  output["server_url"] = value.server;
  output["capture_interval_ms"] = value.interval;
  output["jpeg_quality"] = value.quality;
  output["resolution"] = value.resolution;
  output["password_set"] = !value.password.isEmpty();
}

void loadSettings() {
  char mac[13];
  snprintf(mac, sizeof(mac), "%012llx", static_cast<unsigned long long>(ESP.getEfuseMac() & 0xFFFFFFFFFFFFULL));
  cameraId = String("paparazzi-") + mac;
  Preferences nvs;
  if (!nvs.begin("paparazzi", true)) return;
  JsonDocument saved;
  if (!deserializeJson(saved, nvs.getString("usb_config", ""))) {
    Settings loaded;
    if (parseSettings(saved.as<JsonObjectConst>(), loaded)) {
      settings = loaded;
      durable = true;
    }
  }
  if (!durable) {
    settings.ssid = nvs.getString("wifi_ssid", "");
    settings.password = nvs.getString("wifi_pass", "");
  }
  nvs.end();
}

bool saveSettings(const Settings &next) {
  JsonDocument document;
  publicSettings(document.to<JsonObject>(), next);
  document.remove("password_set");
  document["password"] = next.password;
  document["heartbeat_token"] = next.token;
  String serialized;
  serializeJson(document, serialized);
  Preferences nvs;
  if (!nvs.begin("paparazzi", false)) return false;
  const bool saved = nvs.getString("usb_config", "") == serialized
      || nvs.putString("usb_config", serialized) == serialized.length();
  if (saved && !durable) {
    nvs.remove("wifi_ssid");
    nvs.remove("wifi_pass");
  }
  nvs.end();
  return saved;
}

void join(const Settings &value) {
  WiFi.setAutoReconnect(false);
  WiFi.disconnect(false, false);
  lastWifiDisconnectReason = 0;
  if (!value.ssid.isEmpty()) WiFi.begin(value.ssid.c_str(), value.password.c_str());
}

bool heartbeat(const Settings &value, bool probe) {
  if (value.server.isEmpty() || WiFi.status() != WL_CONNECTED) return false;
  String url = value.server.substring(0, value.server.length() - strlen("/api/camera/ingest"));
  url += "/api/devices/heartbeat";
  WiFiClient socket;
  HTTPClient http;
  http.setConnectTimeout(2500);
  http.setTimeout(2500);
  http.setFollowRedirects(HTTPC_DISABLE_FOLLOW_REDIRECTS);
  if (!http.begin(socket, url)) return false;
  http.addHeader("Content-Type", "application/json");
  http.addHeader("Authorization", String("Bearer ") + value.token);
  JsonDocument payload;
  payload["device_id"] = cameraId;
  payload["camera_ready"] = cameraReady;
  payload["device_name"] = value.name;
  payload["ssid"] = value.ssid;
  payload["capture_interval_ms"] = value.interval;
  payload["probe"] = probe;
  String body;
  serializeJson(payload, body);
  const int result = http.POST(body);
  http.end();
  return result == 200;
}

bool applyImageSettings(const Settings &value) {
  sensor_t *sensor = cameraReady ? esp_camera_sensor_get() : nullptr;
  if (!sensor) return false;
  return sensor->set_framesize(sensor, value.resolution == "VGA" ? FRAMESIZE_VGA : FRAMESIZE_SVGA) == 0
      && sensor->set_quality(sensor, value.quality) == 0;
}

void usbTask(void *) {
  Line line{};
  size_t used = 0;
  bool discard = false;
  uint32_t lastByte = millis();
  for (;;) {
    if (millis() - lastByte > 1500) { used = 0; discard = false; }
    while (Serial.available()) {
      const int ch = Serial.read();
      lastByte = millis();
      if (ch == '\n') {
        if (discard) {
          reply("", false, "message_too_large");
        } else if (used) {
          line.text[used] = 0;
          JsonDocument request;
          if (deserializeJson(request, static_cast<const char *>(line.text))) {
            reply("", false, "malformed_json");
          } else {
            const char *id = request["request_id"] | "";
            const char *action = request["command"] | "";
            if (!*id || strlen(id) > 64 || !*action) reply("", false, "invalid_request");
            else if (!strcmp(action, "PING")) reply(id, true);
            else if (xQueueSend(commands, &line, 0) != pdTRUE) reply(id, false, "busy");
          }
        }
        used = 0;
        discard = false;
      } else if (ch != '\r' && !discard) {
        if (used < sizeof(line.text) - 1) line.text[used++] = static_cast<char>(ch);
        else discard = true;
      }
    }
    vTaskDelay(pdMS_TO_TICKS(10));
  }
}

void runCommand(const Line &line) {
  JsonDocument request, response;
  if (deserializeJson(request, static_cast<const char *>(line.text))) return;
  const char *id = request["request_id"] | "";
  const String action = request["command"].as<String>();
  const JsonObjectConst data = request["data"].as<JsonObjectConst>();
  JsonObject result = response.to<JsonObject>();
  if (action == "GET_DEVICE_INFO" || action == "GET_CONFIG" || action == "GET_STATUS") {
    publicSettings(result, settings);
    result["device_id"] = cameraId;
    result["product"] = "Paparazzi";
    result["firmware_version"] = "1.0.2";
    result["protocol_version"] = 1;
    result["wifi_connected"] = WiFi.status() == WL_CONNECTED;
    result["server_connected"] = serverConnected && WiFi.status() == WL_CONNECTED;
    result["camera_ready"] = cameraReady;
    result["ip"] = WiFi.localIP().toString();
    result["saved"] = durable;
    reply(id, true, nullptr, result);
    return;
  }
  if (action == "RESTART" || action == "FACTORY_RESET") {
    if (action == "FACTORY_RESET") {
      if (cameraId != (data["confirmation"] | "")) { reply(id, false, "confirmation_required"); return; }
      Preferences nvs;
      if (!nvs.begin("paparazzi", false) || !nvs.clear()) {
        reply(id, false, "storage_failed");
        return;
      }
      nvs.end();
    }
    reply(id, true);
    delay(150);
    ESP.restart();
    return;
  }
  if (action != "SET_CONFIG" && action != "TEST_WIFI" && action != "TEST_SERVER") {
    reply(id, false, "unknown_command");
    return;
  }
  Settings candidate = settings;
  if (!parseSettings(data, candidate)) { reply(id, false, "invalid_config"); return; }
  const bool alreadyOnTestedWifi = stagedWifiValidated
      && stagedWifiSsid == candidate.ssid && stagedWifiPassword == candidate.password
      && WiFi.status() == WL_CONNECTED;
  const bool changedWifi = candidate.ssid != settings.ssid || candidate.password != settings.password
      || WiFi.status() != WL_CONNECTED || WiFi.SSID() != candidate.ssid;
  if (changedWifi && !alreadyOnTestedWifi) join(candidate);
  const uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - start < kWifiTimeout) delay(50);
  const char *error = nullptr;
  const uint8_t wifiFailureReason = lastWifiDisconnectReason;
  const int wifiFailureStatus = static_cast<int>(WiFi.status());
  if (WiFi.status() != WL_CONNECTED) error = "wifi_failed";
  else if (action != "TEST_WIFI" && !heartbeat(candidate, true)) error = "server_failed";
  if (!error && action == "SET_CONFIG") {
    const bool changedImage = candidate.quality != settings.quality || candidate.resolution != settings.resolution;
    if (changedImage && !applyImageSettings(candidate)) error = "camera_failed";
    else if (!saveSettings(candidate)) error = "storage_failed";
    if (error && changedImage && cameraReady) applyImageSettings(settings);
    if (!error) {
      settings = candidate;
      durable = true;
      stagedWifiValidated = false;
      stagedWifiSsid = "";
      stagedWifiPassword = "";
      serverConnected = true;
      lastHeartbeat = millis() - kHeartbeatInterval;
    }
  }
  if (error && changedWifi) {
    join(settings);
    stagedWifiValidated = false;
    stagedWifiSsid = "";
    stagedWifiPassword = "";
  } else if (!error && action != "SET_CONFIG") {
    // Keep the successfully tested connection alive so Test and save setup
    // can persist it without forcing a second Wi-Fi authentication handshake.
    stagedWifiSsid = candidate.ssid;
    stagedWifiPassword = candidate.password;
    stagedWifiValidated = true;
  }
  WiFi.setAutoReconnect(true);
  if (error && !strcmp(error, "wifi_failed")) {
    JsonDocument diagnostic;
    JsonObject details = diagnostic.to<JsonObject>();
    details["wifi_reason"] = wifiFailureReason;
    details["wifi_status"] = wifiFailureStatus;
    reply(id, false, error, details);
  } else if (error) reply(id, false, error);
  else {
    result["test_passed"] = true;
    result["saved"] = action == "SET_CONFIG";
    reply(id, true, nullptr, result);
  }
}

bool startCamera() {
  camera_config_t config = {};
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;
  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;
  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;
  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.frame_size = FRAMESIZE_SVGA;
  config.pixel_format = PIXFORMAT_JPEG;
  config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
  config.fb_location = CAMERA_FB_IN_PSRAM;
  config.jpeg_quality = 20;
  config.fb_count = 1;

  if (config.pixel_format == PIXFORMAT_JPEG) {
    if (psramFound()) {
      config.jpeg_quality = 20;
      config.fb_count = 2;
      config.grab_mode = CAMERA_GRAB_LATEST;
    } else {
      config.frame_size = FRAMESIZE_SVGA;
      config.fb_location = CAMERA_FB_IN_DRAM;
    }
  } else {
    config.frame_size = FRAMESIZE_240X240;
#if CONFIG_IDF_TARGET_ESP32S3
    config.fb_count = 2;
#endif
  }

#if defined(CAMERA_MODEL_ESP_EYE)
  pinMode(13, INPUT_PULLUP);
  pinMode(14, INPUT_PULLUP);
#endif

  const esp_err_t error = esp_camera_init(&config);
  if (error != ESP_OK) {
    Serial.printf("Camera init failed with error 0x%x\n", error);
    return false;
  }

  sensor_t *sensor = esp_camera_sensor_get();
  if (sensor->id.PID == OV3660_PID) {
    sensor->set_vflip(sensor, 1);
  }
  sensor->set_framesize(sensor, FRAMESIZE_SVGA);
  sensor->set_quality(sensor, 20);
  sensor->set_brightness(sensor, 2);
  sensor->set_contrast(sensor, 2);
  sensor->set_saturation(sensor, 0);

#if defined(CAMERA_MODEL_M5STACK_WIDE) || defined(CAMERA_MODEL_M5STACK_ESP32CAM)
  sensor->set_vflip(sensor, 1);
  sensor->set_hmirror(sensor, 1);
#endif
#if defined(CAMERA_MODEL_ESP32S3_EYE)
  sensor->set_vflip(sensor, 1);
#endif
#if defined(LED_GPIO_NUM)
  setupLedFlash();
#endif
  return true;
}

}  // namespace

void setup() {
  Serial.begin(115200);
  Serial.setDebugOutput(false);
  commands = xQueueCreate(1, sizeof(Line));
  serialOutput = xSemaphoreCreateMutex();
  if (commands && serialOutput) xTaskCreate(usbTask, "paparazzi-usb", 8192, nullptr, 2, nullptr);
  loadSettings();
  WiFi.mode(WIFI_STA);
  WiFi.onEvent(rememberWifiDisconnect, ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
  WiFi.persistent(false);
  WiFi.setSleep(false);
  join(settings);
  WiFi.setAutoReconnect(true);
  // A reset inside the driver can otherwise prevent USB recovery on every boot.
  const bool recoveryBoot = esp_reset_reason() != ESP_RST_POWERON && cameraInitMarker == kCameraInitMarker;
  if (!recoveryBoot) {
    cameraInitMarker = kCameraInitMarker;
    cameraReady = startCamera();
  }
  cameraInitMarker = 0;
  if (cameraReady && durable && !applyImageSettings(settings)) {
    // Keep the hardware defaults available for USB recovery.
    applyImageSettings(Settings{});
  }
}

void loop() {
  Line line{};
  if (commands && xQueueReceive(commands, &line, 0) == pdTRUE) runCommand(line);
  if (WiFi.status() == WL_CONNECTED) {
    if (cameraReady && !cameraServerStarted) {
      if (MDNS.begin(cameraId.c_str())) {
        MDNS.addService("paparazzi-camera", "tcp", 80);
        MDNS.addServiceTxt("paparazzi-camera", "tcp", "camera_id", cameraId);
      }
      startCameraServer();
      cameraServerStarted = true;
    }
    if (durable && millis() - lastHeartbeat >= kHeartbeatInterval) {
      serverConnected = heartbeat(settings, false);
      lastHeartbeat = millis();
    }
  } else {
    serverConnected = false;
  }
  delay(20);
}
