const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const script = fs.readFileSync(path.join(__dirname, '../static/js/devices.js'), 'utf8');

function mount(fetch) {
  const elements = new Map();
  function element(id) {
    if (!elements.has(id)) {
      elements.set(id, {
        value: '', textContent: '', children: [], handlers: {}, dataset: { csrf: 'test-token' },
        querySelectorAll: () => [],
        classList: { add() {}, remove() {} },
        addEventListener(event, handler) { this.handlers[event] = handler; },
        replaceChildren(...children) { this.children = children; },
        elements: { namedItem: element },
      });
    }
    return elements.get(id);
  }
  element('#camera-settings').hidden = true;
  vm.runInNewContext(script, {
    document: { querySelector: element, createElement: () => ({}) },
    window: { setTimeout() {} },
    Option: function Option(text, value) { Object.assign(this, { text, value }); },
    fetch,
  });
  return element;
}

function response(result, ok = true) {
  return { ok, json: async () => result };
}

test('port discovery shows loading and prefers a likely ESP32', async () => {
  let finish;
  const ports = new Promise((resolve) => { finish = resolve; });
  const element = mount((url) => url.endsWith('/ports') ? ports : response({ wireless: [], usb: [] }));
  assert.equal(element('#device-message').textContent, 'Working…');
  finish(response({ ports: [
    { port: 'COM1', description: 'Other port', likely_camera: false },
    { port: 'COM11', description: 'ESP32', likely_camera: true },
  ] }));
  await new Promise(setImmediate);
  assert.equal(element('#usb-port').children[0].value, 'COM11');
  assert.match(element('#device-message').textContent, /Select the camera/);
});

test('failed discovery renders an error while the settings form stays hidden', async () => {
  const element = mount((url) => url.endsWith('/ports')
    ? response({ error: 'USB is unavailable.' }, false)
    : response({ wireless: [], usb: [] }));
  await new Promise(setImmediate);
  assert.equal(element('#device-message').textContent, 'USB is unavailable.');
  assert.equal(element('#camera-settings').hidden, true);
});
