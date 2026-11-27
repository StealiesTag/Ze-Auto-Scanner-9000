(() => {
  const root = document.querySelector('#device-setup');
  if (!root) return;
  const find = (id) => document.querySelector('#' + id);
  const form = find('camera-settings');
  const port = find('usb-port');
  const password = find('wifi-password');
  const message = find('device-message');
  const server = find('server-url');
  let device;
  let network;
  let busy = false;

  async function api(path, data) {
    const response = await fetch('/api/devices' + path, {
      method: data === undefined ? 'GET' : 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': root.dataset.csrf },
      body: data === undefined ? undefined : JSON.stringify(data),
      cache: 'no-store',
    });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || 'Paparazzi could not complete this request.');
    return result;
  }

  async function run(task) {
    if (busy) return;
    busy = true;
    root.querySelectorAll('button').forEach((button) => { button.disabled = true; });
    message.textContent = 'Working…';
    message.classList.remove('text-danger');
    try {
      message.textContent = await task();
    } catch (error) {
      message.textContent = error.message;
      message.classList.add('text-danger');
    } finally {
      busy = false;
      root.querySelectorAll('button').forEach((button) => { button.disabled = false; });
    }
  }

  async function refreshPorts() {
    const selected = port.value;
    const result = await api('/ports');
    const ordered = result.ports.sort((a, b) => Number(b.likely_camera) - Number(a.likely_camera));
    port.replaceChildren(...ordered.map((item) => new Option(item.port + ' · ' + item.description, item.port)));
    if (ordered.some((item) => item.port === selected)) port.value = selected;
    return ordered.length ? 'Select the camera port and connect.' : 'No serial ports found. Check the USB cable and refresh.';
  }

  function showStatus(info) {
    const items = ['USB connected', info.wifi_connected ? 'Wi-Fi connected · ' + info.ip : 'Wi-Fi disconnected',
      info.camera_ready ? 'Camera sensor ready' : 'Camera sensor unavailable',
      info.server_connected ? 'Server verified' : 'Server not yet verified'];
    find('camera-checks').replaceChildren(...items.map((text) => {
      const item = document.createElement('li');
      item.textContent = text;
      return item;
    }));
  }

  function showServer() {
    const recommended = network?.addresses[0]?.url;
    let text = server.value ? 'Server · ' + server.value : 'Enter a LAN address in Advanced settings.';
    if (recommended && recommended !== server.value) text += ' Detected address differs. Select it in Advanced settings to update.';
    if (network?.loopback_only) text += ' The configured host is localhost. Enable LAN access and check the firewall before testing.';
    find('server-address').textContent = text;
  }

  function fields() {
    const data = Object.fromEntries(new FormData(form));
    data.capture_interval_ms = Number(data.capture_interval_ms);
    data.jpeg_quality = Number(data.jpeg_quality);
    if (!password.value && !find('replace-wifi-password').checked) delete data.password;
    return data;
  }

  function path(action) {
    return '/' + encodeURIComponent(device.device_id) + '/' + action;
  }

  find('refresh-ports').addEventListener('click', () => run(refreshPorts));
  find('connect-camera').addEventListener('click', () => run(async () => {
    if (!port.value) throw new Error('Select a USB port.');
    device = await api('/connect', { port: port.value });
    network = await api('/network?peer=' + encodeURIComponent(device.ip || ''));
    const interfaces = find('lan-address');
    interfaces.replaceChildren(new Option('Choose a detected address', ''), ...network.addresses.map(
      (item) => new Option(item.address + ' · ' + item.interface, item.url)));
    for (const key of ['device_name', 'ssid', 'capture_interval_ms', 'jpeg_quality', 'resolution']) {
      form.elements.namedItem(key).value = device[key];
    }
    server.value = device.server_url || network.addresses[0]?.url || '';
    password.value = '';
    find('replace-wifi-password').checked = false;
    find('device-info').textContent = device.device_id + ' · Firmware ' + device.firmware_version;
    showStatus(device);
    showServer();
    form.hidden = false;
    return 'Camera connected through USB.';
  }));
  find('lan-address').addEventListener('change', (event) => {
    if (event.target.value) server.value = event.target.value;
    showServer();
  });
  server.addEventListener('input', showServer);
  find('show-wifi-password').addEventListener('change', (event) => {
    password.type = event.target.checked ? 'text' : 'password';
  });
  form.addEventListener('submit', (event) => {
    event.preventDefault();
    run(async () => {
      await api(path('command'), { command: 'SET_CONFIG', data: fields() });
      password.value = '';
      find('replace-wifi-password').checked = false;
      const status = await api(path('command'), { command: 'GET_STATUS' });
      showStatus(status);
      return status.camera_ready
        ? 'Setup saved. Choose Use for scanning, then you can disconnect USB.'
        : 'Wi-Fi and server settings saved. The camera sensor still needs attention.';
    });
  });
  form.querySelectorAll('[data-action]').forEach((button) => button.addEventListener('click', () => run(async () => {
    const command = button.dataset.action;
    if (command === 'USE_CAMERA') {
      await api(path('use-camera'), {});
      return 'Camera selected for automatic scanning. You can disconnect USB.';
    }
    if (command === 'DISCONNECT') {
      await api(path('disconnect'), {});
      form.hidden = true;
      password.value = '';
      return 'USB port released. Wireless status is shown below.';
    }
    let confirmation;
    if (command === 'FACTORY_RESET') {
      confirmation = window.prompt('Type ' + device.device_id + ' to erase camera settings.');
      if (confirmation !== device.device_id) return 'Factory reset canceled.';
    }
    const testing = command === 'TEST_WIFI' || command === 'TEST_SERVER';
    if (testing && !form.reportValidity()) return 'Complete the camera settings first.';
    const result = await api(path('command'), { command, confirmation, data: testing ? fields() : undefined });
    if (command === 'GET_STATUS') showStatus(result);
    if (command === 'RESTART' || command === 'FACTORY_RESET') {
      form.hidden = true;
      password.value = '';
      return 'Camera is restarting. Reconnect when the USB port returns.';
    }
    if (testing) return command === 'TEST_WIFI' ? 'Camera joined Wi-Fi during the test.' : 'Paparazzi received the connection test.';
    return 'Camera status refreshed.';
  })));

  async function wirelessStatus() {
    try {
      const { wireless, usb } = await api('');
      if (device && !busy && !form.hidden && !usb.some((item) => item.device_id === device.device_id)) {
        form.hidden = true;
        password.value = '';
        message.textContent = 'USB disconnected. Wireless status is shown below.';
      }
      find('wireless-devices').replaceChildren(...(wireless.length ? wireless : [null]).map((item) => {
        const row = document.createElement('li');
        row.textContent = item ? [item.device_name || item.device_id, item.online ? 'Online' : 'Offline', item.ip, item.ssid,
          item.camera_ready ? 'Sensor ready' : 'Sensor error', 'Last seen ' + new Date(item.last_seen).toLocaleString(),
          item.last_capture ? 'Last capture ' + new Date(item.last_capture).toLocaleString() : 'No queued captures'].filter(Boolean).join(' · ')
          : 'No wireless camera has checked in.';
        return row;
      }));
    } catch {
      find('wireless-devices').textContent = 'Wireless status is unavailable.';
    } finally {
      window.setTimeout(wirelessStatus, 15000);
    }
  }
  run(refreshPorts);
  wirelessStatus();
})();
