'use strict';

const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const test = require('node:test');
const vm = require('node:vm');

const source = fs.readFileSync(path.join(__dirname, '../runtime/documents/cef/agent.js'), 'utf8');
const sha256 = '305967f575bc139b13975d080f620103993b65a6bbef9672c07d3f036743c59f';

function fixture({base = 0x100000n, ranges = [[0x100n, 0x200], [0x400n, 0x100]]} = {}) {
  const memory = new Map();
  class Pointer {
    constructor(value) { this.value = value instanceof Pointer ? value.value : BigInt(value); }
    add(value) { return new Pointer(this.value + BigInt(value)); }
    sub(value) { return new Pointer(this.value - BigInt(value)); }
    compare(other) { return this.value < other.value ? -1 : this.value > other.value ? 1 : 0; }
    equals(other) { return this.value === other.value; }
    isNull() { return this.value === 0n; }
    toString() { return '0x' + this.value.toString(16); }
    readPointer() { return new Pointer(memory.get(this.value)); }
    readU64() { return {toNumber: () => Number(memory.get(this.value))}; }
    readS32() { return Number(memory.get(this.value)); }
  }
  const events = [];
  let enumerations = 0;
  let pins = 0;
  const module = {
    base: new Pointer(base), size: 0x1000,
    enumerateRanges(protection) {
      assert.equal(protection, '--x');
      enumerations++;
      events.push({type: 'enumerate'});
      return ranges.map(([offset, size]) => ({base: new Pointer(base + offset), size}));
    },
    getExportByName: name => name,
  };
  const context = vm.createContext({
    Process: {
      arch: 'x64', pointerSize: 8, id: 123,
      getModuleByName(name) { assert.equal(name, 'libcef.dll'); return module; },
      findRangeByAddress() { throw new Error('Per-address memory queries must not run'); },
    },
    ptr: value => new Pointer(value),
    NativeCallback: function (body) { return body; },
    NativeFunction: function () { return () => 1; },
    Script: {pin() { pins++; }},
    send: value => events.push(value),
    rpc: {},
    setImmediate() { throw new Error('Unexpected asynchronous work in range initialization'); },
  });
  // Substitute only the native UI scheduler; keep initialization and validation intact.
  vm.runInContext(source + '\npostUI = (job, kind) => send({type: "post", kind});\n' +
    'globalThis.checked = {initialize, inModule, checkBrowser};', context);
  const layout = {
    sha256,
    methods: Array.from({length: 25}, (_, index) => ({capi_offset: 8 + index * 8, function_rva: 0x100 + index * 8})),
    wrapper_cpp_vtable_rva: 0x800,
  };
  const capture = {sha256, pid: 123, libcef_base: module.base.toString(), candidates: []};
  const scope = {docid: 'test-document', document_url: 'https://doc.weixin.qq.com/doc/test-document', browser_id: null};
  const browser = new Pointer(0x200000n);
  memory.set(browser.value, 208);
  layout.methods.forEach(method => memory.set(browser.value + BigInt(method.capi_offset), base + BigInt(method.function_rva)));
  memory.set(browser.value - 8n, browser.value - 32n);
  memory.set(browser.value - 32n, base + 0x800n);
  memory.set(browser.value + 208n, 1);
  return {
    initialize: () => context.checked.initialize(layout, capture, scope),
    contains: offset => context.checked.inModule(new Pointer(base + offset)),
    checkBrowser: () => context.checked.checkBrowser(browser, layout),
    enumerations: () => enumerations, pins: () => pins,
    events, capture, memory, browser,
  };
}

test('enumerates executable mappings once before posting UI work, including repeated validations', () => {
  const f = fixture();
  f.initialize();
  for (let i = 0; i < 100; i++) {
    assert.equal(f.contains(0x100n), true);
    f.checkBrowser();
  }
  assert.equal(f.enumerations(), 1);
  assert.ok(f.events.findIndex(event => event.type === 'enumerate') < f.events.findIndex(event => event.type === 'post'));
  const completed = f.events.find(event => event.stage === 'module.executable-ranges.end');
  assert.equal(completed.range_count, 2);
  assert.ok(completed.enumeration_ms >= 0);
});

test('accepts only executable intervals inside the module, with exclusive upper bounds', () => {
  const f = fixture({ranges: [[-0x100n, 0x200], [0x400n, 0x100], [0xf00n, 0x200]]});
  f.initialize();
  for (const offset of [0n, 0xffn, 0x400n, 0x4ffn, 0xf00n, 0xfffn]) {
    assert.equal(f.contains(offset), true, offset.toString(16));
  }
  for (const offset of [-1n, 0x100n, 0x300n, 0x500n, 0x1000n]) {
    assert.equal(f.contains(offset), false, offset.toString(16));
  }
});

test('still rejects a changed callback pointer even when its replacement is executable', () => {
  const f = fixture();
  f.initialize();
  f.checkBrowser();
  f.memory.set(f.browser.value + 8n, 0x100400n);
  assert.throws(f.checkBrowser, /Browser method table changed/);
  assert.equal(f.enumerations(), 1);
});

test('rejects missing executable mappings before pinning or posting UI callbacks', () => {
  const f = fixture({ranges: []});
  assert.throws(f.initialize, /no executable mappings/);
  assert.equal(f.pins(), 0);
  assert.equal(f.events.some(event => event.type === 'post'), false);
});

test('validates process identity before enumerating and gives each connection fresh mappings', () => {
  const wrong = fixture();
  wrong.capture.pid++;
  assert.throws(wrong.initialize, /CEF process\/base changed/);
  assert.equal(wrong.enumerations(), 0);
  const first = fixture();
  const second = fixture({base: 0x300000n, ranges: [[0x600n, 0x100]]});
  first.initialize();
  second.initialize();
  assert.equal(first.contains(0x100n), true);
  assert.equal(second.contains(0x100n), false);
  assert.equal(second.contains(0x600n), true);
  assert.equal(first.enumerations(), 1);
  assert.equal(second.enumerations(), 1);
});
