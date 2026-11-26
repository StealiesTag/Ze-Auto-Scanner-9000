#include <Arduino.h>
#include "esp_camera.h"
#include <WiFi.h>
#include <ESPmDNS.h>
#include <DNSServer.h>
#include <WebServer.h>
#include <Preferences.h>
#include "board_config.h"

// Copy this sketch into the Espressif CameraWebServer example folder so its
// board_config.h, app_httpd.cpp, and camera_index.h remain available.
void startCameraServer();
void setupLedFlash();

namespace {
constexpr char kSetupServiceName[] = "paparazzi-camera";
constexpr char kSetupServiceProtocol[] = "tcp";
constexpr char kSetupKeyAlphabet[] = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
constexpr uint16_t kDnsPort = 53;
constexpr uint32_t kWifiConnectTimeoutMs = 18000;

Preferences preferences;
DNSServer dnsServer;
WebServer setupServer(80);
String cameraId;
String setupSsid;
String setupPassword;

String escapeHtml(const String &value) {
  String escaped;
  escaped.reserve(value.length());
  for (size_t i = 0; i < value.length(); ++i) {
    switch (value[i]) {
      case '&': escaped += F("&amp;"); break;
      case '<': escaped += F("&lt;"); break;
      case '>': escaped += F("&gt;"); break;
      case '"': escaped += F("&quot;"); break;
      case '\'': escaped += F("&#39;"); break;
      default: escaped += value[i]; break;
    }
  }
  return escaped;
}

String makeSetupPassword() {
  String result;
  result.reserve(16);
  const size_t alphabetLength = sizeof(kSetupKeyAlphabet) - 1;
  for (size_t i = 0; i < 16; ++i) {
    result += kSetupKeyAlphabet[esp_random() % alphabetLength];
  }
  return result;
}

void loadDeviceNames() {
  const uint64_t mac = ESP.getEfuseMac();
  char suffix[7];
  snprintf(suffix, sizeof(suffix), "%06llx", static_cast<unsigned long long>(mac & 0xFFFFFF));
  cameraId = String("paparazzi-") + suffix;
  setupSsid = String("Paparazzi-Setup-") + suffix;

  preferences.begin("paparazzi", false);
  setupPassword = preferences.getString("setup_key", "");
  if (setupPassword.length() < 12) {
    setupPassword = makeSetupPassword();
    preferences.putString("setup_key", setupPassword);
  }
  preferences.end();
}

bool connectToSavedWifi() {
  preferences.begin("paparazzi", true);
  const String ssid = preferences.getString("wifi_ssid", "");
  const String password = preferences.getString("wifi_pass", "");
  preferences.end();
  if (ssid.isEmpty()) return false;

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.setAutoReconnect(true);
  WiFi.begin(ssid.c_str(), password.c_str());
  Serial.printf("Connecting to saved Wi-Fi: %s\n", ssid.c_str());
  const uint32_t started = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - started < kWifiConnectTimeoutMs) {
    delay(250);
    Serial.print('.');
  }
  Serial.println();
  return WiFi.status() == WL_CONNECTED;
}

String setupPage() {
  const int networkCount = WiFi.scanNetworks(false, true);
  String options;
  for (int i = 0; i < networkCount; ++i) {
    const String ssid = WiFi.SSID(i);
    if (!ssid.isEmpty()) options += "<option value=\"" + escapeHtml(ssid) + "\"></option>";
  }

  String page = F("<!doctype html><html lang='en'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                  "<meta charset='utf-8'><title>Paparazzi camera setup</title>"
                  "<style>body{font:16px system-ui;max-width:34rem;margin:2rem auto;padding:0 1rem;color:#202b27}"
                  "label{display:block;margin:1rem 0 .3rem}input,button{box-sizing:border-box;width:100%;padding:.8rem;font:inherit}"
                  "button{margin-top:1.2rem;background:#285c4b;color:white;border:0}small{color:#68736e}</style>"
                  "<h1>Connect Paparazzi camera</h1><p>Choose the classroom Wi-Fi. The camera stores these details and reconnects automatically.</p>"
                  "<form method='post' action='/save'><label for='ssid'>Wi-Fi network</label>"
                  "<input id='ssid' name='ssid' list='networks' maxlength='32' autocomplete='off' required>"
                  "<datalist id='networks'>");
  page += options;
  page += F("</datalist><label for='password'>Wi-Fi password</label>"
            "<input id='password' name='password' type='password' maxlength='63' autocomplete='new-password'>"
            "<small>This setup page is available only through the camera's password-protected setup network.</small>"
            "<button type='submit'>Save Wi-Fi and connect camera</button></form></html>");
  WiFi.scanDelete();
  return page;
}

void handleSetupRoot() {
  setupServer.sendHeader("Cache-Control", "no-store");
  setupServer.send(200, "text/html; charset=utf-8", setupPage());
}

void handleWifiSave() {
  String ssid = setupServer.arg("ssid");
  String password = setupServer.arg("password");
  ssid.trim();
  if (ssid.isEmpty() || ssid.length() > 32 || password.length() > 63 ||
      (!password.isEmpty() && password.length() < 8)) {
    setupServer.send(400, "text/plain; charset=utf-8", "Enter a valid Wi-Fi name and password.");
    return;
  }

  preferences.begin("paparazzi", false);
  preferences.putString("wifi_ssid", ssid);
  preferences.putString("wifi_pass", password);
  preferences.end();

  setupServer.send(200, "text/html; charset=utf-8",
                   "<!doctype html><meta name='viewport' content='width=device-width'>"
                   "<h2>Wi-Fi saved</h2><p>The camera is restarting and will join the selected network.</p>");
  delay(1200);
  ESP.restart();
}

void redirectToSetup() {
  setupServer.sendHeader("Location", "http://192.168.4.1/", true);
  setupServer.send(302, "text/plain", "Opening camera setup");
}

void startWifiSetupPortal() {
  WiFi.mode(WIFI_AP_STA);
  WiFi.setSleep(false);
  if (!WiFi.softAP(setupSsid.c_str(), setupPassword.c_str())) {
    Serial.println("Could not start the protected camera setup network.");
    delay(2000);
    ESP.restart();
  }

  dnsServer.start(kDnsPort, "*", WiFi.softAPIP());
  setupServer.on("/", HTTP_GET, handleSetupRoot);
  setupServer.on("/save", HTTP_POST, handleWifiSave);
  setupServer.onNotFound(redirectToSetup);
  setupServer.begin();

  Serial.println("Wi-Fi is not configured. Use the password label on the camera.");
  Serial.printf("Setup network: %s\n", setupSsid.c_str());
  Serial.printf("Setup network password: %s\n", setupPassword.c_str());
  Serial.println("Setup page: http://192.168.4.1");
  while (true) {
    dnsServer.processNextRequest();
    setupServer.handleClient();
    delay(2);
  }
}

bool startCameraServices() {
  if (!MDNS.begin(cameraId.c_str())) {
    Serial.println("mDNS startup failed; camera still works at its printed IP address.");
    return false;
  }
  MDNS.setInstanceName("Paparazzi Camera");
  MDNS.addService(kSetupServiceName, kSetupServiceProtocol, 80);
  MDNS.addServiceTxt(kSetupServiceName, kSetupServiceProtocol, "camera_id", cameraId);
  return true;
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
  Serial.setDebugOutput(true);
  loadDeviceNames();

  if (!connectToSavedWifi()) startWifiSetupPortal();
  if (!startCamera()) return;

  startCameraServices();
  startCameraServer();
  Serial.printf("Paparazzi camera ready: %s.local\n", cameraId.c_str());
  Serial.printf("Camera IP: %s\n", WiFi.localIP().toString().c_str());
}

void loop() {
  delay(10000);
}
