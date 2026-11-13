'use strict';

// Exact SHA-checked Tencent CEF CAPI. Bind to the caller's current document URL.
// Background operations only; no browser creation or desktop activation.
// The Python owner must use beginclose/status/finish before unload/detach.
const EXPECTED_SHA256 = '305967f575bc139b13975d080f620103993b65a6bbef9672c07d3f036743c59f';
const MAX_RESULT_BYTES = 2 * 1024 * 1024;
const SIZE = {browser: 208, frame: 240, host: 584, registration: 40, task: 48, observer: 80};
const state = {
  phase: 'new', pinned: false, browser: null, host: null, observer: null,
  registration: null, cleanupQueued: false, cleanupComplete: false,
  callbacks: 0, retiring: false, unsafe: false, errors: [],
  startedAt: null, checkpointSequence: 0, lastCheckpoint: null, checkpoints: [],
  activeOperations: new Map(),
  records: new Map(), pending: new Map(), retainedBrowsers: new Map(),
  nextId: 0x40000000 + Math.floor(Math.random() * 0x10000000),
};
let mod = null;
let postTaskNative = null;
let currentlyOn = null;
// One SHA/base-bound script lifetime. Capture mappings before posting UI work;
// every live object's complete callback pointer table is still compared.
let executableRanges = [];

function emit(type, values = {}) { send({type, ...values}); }
function checkpoint(stage, details = {}) {
  const value = {sequence: ++state.checkpointSequence, stage,
    elapsed_ms: state.startedAt === null ? 0 : Date.now() - state.startedAt, ...details};
  const parts = stage.split('.');
  const transition = parts.pop();
  const key = parts.join('.') + (details.task_kind === undefined ? '' : ':' + details.task_kind);
  if (transition === 'begin' || transition === 'enter' && parts.join('.') === 'task.execute') {
    state.activeOperations.set(key, value);
  } else if (['end', 'leave', 'accepted', 'rejected', 'complete'].includes(transition)) {
    state.activeOperations.delete(key);
  }
  const message = {...value, active_operations: Array.from(state.activeOperations.values())};
  state.lastCheckpoint = message;
  state.checkpoints.push(message);
  if (state.checkpoints.length > 16) state.checkpoints.shift();
  emit('checkpoint', message);
}
function describe(error) { return String(error && error.stack ? error.stack : error); }
function ensure(condition, message) { if (!condition) throw new Error(message); }
function inModule(address) {
  if (address.compare(mod.base) < 0 || address.compare(mod.base.add(mod.size)) >= 0) return false;
  return executableRanges.some(range => address.compare(range.base) >= 0 && address.compare(range.end) < 0);
}
function fn(object, offset, result, args) {
  const address = object.add(offset).readPointer();
  ensure(inModule(address), 'CEF callback is outside the checked executable module');
  return new NativeFunction(address, result, args);
}
function sizeCheck(object, size, label) {
  ensure(!object.isNull() && object.readU64().toNumber() === size, label + ' CAPI size differs');
}
function addRef(object) { fn(object, 8, 'void', ['pointer'])(object); }
function release(object) { return fn(object, 16, 'int', ['pointer'])(object); }
function assertUI() { ensure(currentlyOn(0) === 1, 'CEF callback is not on TID_UI'); }

function settle(id, success, value) {
  if (!state.pending.has(id)) return;
  state.pending.delete(id);
  emit('result', {id, success, value});
}
function failPending(reason) {
  for (const id of Array.from(state.pending.keys())) settle(id, false, {local_error: reason});
}
function fatal(error, uncertainNativeState = false) {
  const text = describe(error);
  state.errors.push(text);
  if (uncertainNativeState) state.unsafe = true;
  emit('failure', {error: text, uncertain_native_state: uncertainNativeState,
    code: error.code || null, candidates: error.candidates || []});
  failPending(text);
  if (state.phase !== 'finished') state.phase = 'closing';
  setImmediate(() => {
    try { beginClose(); } catch (cleanupError) {
      state.errors.push(describe(cleanupError));
      emit('failure', {error: describe(cleanupError), cleanup: true});
    }
  });
}

// Every CEF-owned object and all NativeCallbacks remain strongly reachable.
// Retire on a later JS turn, after the final release callback has returned.
function deferRetire() {
  if (state.retiring) return;
  state.retiring = true;
  setImmediate(() => {
    state.retiring = false;
    for (const [key, record] of state.records) {
      if (record.refs === 0 && record.active === 0) state.records.delete(key);
    }
    emit('lifecycle', snapshot());
  });
}
function callback(body, result, args, failureValue) {
  return new NativeCallback((...values) => {
    state.callbacks++;
    try { return body(...values); }
    catch (error) { fatal(error); return failureValue; }
    finally { state.callbacks--; deferRetire(); }
  }, result, args);
}
function recordFor(self) {
  const record = state.records.get(self.toString());
  ensure(record !== undefined && record.refs > 0, 'Callback on an unowned CEF adapter object');
  return record;
}
function drop(record) {
  ensure(record.refs > 0, 'CEF reference count underflow');
  record.refs--;
  if (record.refs === 0) deferRetire();
  return record.refs === 0 ? 1 : 0;
}
const baseCallbacks = {
  add: callback(self => { recordFor(self).refs++; }, 'void', ['pointer']),
  release: callback(self => drop(recordFor(self)), 'int', ['pointer'], 0),
  one: callback(self => recordFor(self).refs === 1 ? 1 : 0, 'int', ['pointer'], 0),
  any: callback(self => recordFor(self).refs > 0 ? 1 : 0, 'int', ['pointer'], 0),
};
function owned(kind, size) {
  const memory = Memory.alloc(size);
  memory.writeByteArray(new Uint8Array(size));
  memory.writeU64(size);
  memory.add(8).writePointer(baseCallbacks.add);
  memory.add(16).writePointer(baseCallbacks.release);
  memory.add(24).writePointer(baseCallbacks.one);
  memory.add(32).writePointer(baseCallbacks.any);
  const record = {kind, memory, refs: 1, active: 0, job: null, ran: false};
  state.records.set(memory.toString(), record);
  return record;
}
const executeTask = callback(self => {
  const record = recordFor(self);
  record.active++;
  try {
    ensure(record.kind === 'task' && !record.ran, 'Unexpected repeated CEF task execution');
    record.ran = true;
    checkpoint('task.execute.enter', {task_kind: record.taskKind});
    assertUI();
    record.job();
  } finally {
    checkpoint('task.execute.leave', {task_kind: record.taskKind});
    record.active--;
  }
}, 'void', ['pointer']);

function postUI(job, kind) {
  const task = owned('task', SIZE.task);
  task.taskKind = kind;
  task.job = () => {
    if (state.phase !== 'closing' || kind === 'cleanup') job();
    else checkpoint('task.skipped-closing', {task_kind: kind});
  };
  task.memory.add(40).writePointer(executeTask);
  let accepted;
  // CEF's CAPI consumes one input reference. Retain our local reference too.
  task.refs++;
  checkpoint('task.post.begin', {task_kind: kind});
  try {
    accepted = postTaskNative(0, task.memory) === 1;
    checkpoint(accepted ? 'task.post.accepted' : 'task.post.rejected', {task_kind: kind});
  }
  catch (error) {
    // A native exception does not prove whether CEF took ownership. Stay pinned.
    fatal(error, true);
    throw error;
  } finally { drop(task); } // CEF owns its separate reference on successful post.
  if (!accepted) throw new Error('cef_post_task(TID_UI) rejected ' + kind);
}

// CToCpp passes CefBrowserCppToC::Wrap(browser): each callback owns one input
// browser reference. This is separate from self, which remains CEF-owned.
// See OWNERSHIP.md for the exact-version source and native call sites.
function observerCallback(body, result, args, failureValue) {
  return callback((self, browser, ...values) => {
    let record = null;
    try {
      record = recordFor(self);
      record.active++;
      return body(browser, ...values);
    } finally {
      try { release(browser); }
      finally { if (record !== null) record.active--; }
    }
  }, result, args, failureValue);
}

const observerCallbacks = {
  // Let CEF parse its own envelope and deliver the typed result callback.
  message: observerCallback(() => 0, 'int', ['pointer', 'pointer', 'pointer', 'uint64'], 0),
  result: observerCallback((browser, id, success, bytes, byteCount) => {
    assertUI();
    if (!state.pending.has(id)) return;
    ensure(browser.equals(state.browser) ||
      fn(browser, 120, 'int', ['pointer'])(browser) === state.browserId,
    'CDP result belongs to another browser');
    const length = byteCount.toNumber();
    ensure(Number.isSafeInteger(length) && length <= MAX_RESULT_BYTES, 'CDP result exceeds byte limit');
    ensure(length === 0 || !bytes.isNull(), 'Null CDP result buffer');
    const value = length === 0 ? null : JSON.parse(bytes.readUtf8String(length));
    settle(id, success !== 0, value);
  }, 'void', ['pointer', 'pointer', 'int', 'int', 'pointer', 'uint64']),
  event: observerCallback(() => {}, 'void', ['pointer', 'pointer', 'pointer', 'pointer', 'uint64']),
  attached: observerCallback(() => {}, 'void', ['pointer', 'pointer']),
  detached: observerCallback(() => { fatal(new Error('CEF DevTools agent detached; pending results will not arrive')); },
    'void', ['pointer', 'pointer']),
};
function makeObserver() {
  const record = owned('observer', SIZE.observer);
  const names = ['message', 'result', 'event', 'attached', 'detached'];
  names.forEach((name, index) => record.memory.add(40 + index * 8).writePointer(observerCallbacks[name]));
  return record;
}

function checkBrowser(browser, layout) {
  sizeCheck(browser, SIZE.browser, 'browser');
  ensure(layout.methods.length === 25, 'Incomplete checked browser method table');
  layout.methods.forEach((method, index) => {
    const offset = Number(method.capi_offset);
    ensure(offset === 8 + index * 8, 'Unexpected browser method layout');
    const expected = mod.base.add(Number(method.function_rva));
    ensure(inModule(expected) && browser.add(offset).readPointer().equals(expected), 'Browser method table changed');
  });
  ensure(browser.sub(8).readPointer().equals(browser.sub(32)), 'Browser wrapper self-pointer changed');
  ensure(browser.sub(32).readPointer().equals(mod.base.add(Number(layout.wrapper_cpp_vtable_rva))),
    'Browser wrapper C++ vtable changed');
  ensure(browser.add(208).readS32() > 0, 'Browser wrapper has no live reference');
}

function frameUrl(browser, details = {}) {
  checkpoint('frame.get-main.begin', details);
  const frame = fn(browser, 152, 'pointer', ['pointer'])(browser);
  checkpoint('frame.get-main.end', {...details, has_frame: !frame.isNull()});
  if (frame.isNull()) return null;
  try {
    sizeCheck(frame, SIZE.frame, 'frame');
    checkpoint('frame.get-url.begin', details);
    const value = fn(frame, 192, 'pointer', ['pointer'])(frame);
    checkpoint('frame.get-url.end', {...details, has_url: !value.isNull()});
    if (value.isNull()) return null;
    try {
      const length = value.add(8).readU64().toNumber();
      ensure(Number.isSafeInteger(length) && length <= 8192, 'Unexpected frame URL length');
      if (length === 0) return '';
      return value.readPointer().readUtf16String(length).split(/[?#]/, 1)[0];
    } finally {
      checkpoint('frame.url-free.begin', details);
      new NativeFunction(mod.getExportByName('cef_string_userfree_utf16_free'), 'void', ['pointer'])(value);
      checkpoint('frame.url-free.end', details);
    }
  } finally {
    checkpoint('frame.release.begin', details);
    release(frame);
    checkpoint('frame.release.end', details);
  }
}
function releaseBrowser(browser, details = {}) {
  checkpoint('browser.release.begin', details);
  release(browser);
  checkpoint('browser.release.end', details);
  state.retainedBrowsers.delete(browser.toString());
}
function selectionError(code, message, matches) {
  const error = new Error(message);
  error.code = code;
  error.candidates = matches.map(item => ({browser_id: item.id, url: item.url}));
  return error;
}
function assertDocumentBinding() {
  checkpoint('binding.is-valid.begin', {browser_id: state.browserId});
  ensure(fn(state.browser, 40, 'int', ['pointer'])(state.browser) === 1, 'Bound browser has closed');
  checkpoint('binding.is-valid.end', {browser_id: state.browserId});
  ensure(frameUrl(state.browser, {browser_id: state.browserId, purpose: 'binding'}) === state.documentUrl,
    'Browser navigated away from the bound document');
}
function initialize(layout, capture, scope) {
  ensure(state.phase === 'new', 'Adapter already initialized');
  state.startedAt = Date.now();
  checkpoint('initialize.enter', {candidate_count: capture.candidates.length,
    explicit_browser_id: scope.browser_id});
  ensure(Process.arch === 'x64' && Process.pointerSize === 8, 'Requires 64-bit CEF');
  ensure(layout.sha256 === EXPECTED_SHA256 && capture.sha256 === EXPECTED_SHA256, 'Unexpected CEF layout SHA256');
  ensure(typeof scope.docid === 'string' && scope.docid.length > 0, 'An explicit docid is required');
  const encodedId = encodeURIComponent(scope.docid).replace(/[!'()*]/g,
    value => '%' + value.charCodeAt(0).toString(16).toUpperCase());
  ensure(scope.document_url === 'https://doc.weixin.qq.com/doc/' + encodedId, 'Document URL and docid differ');
  ensure(scope.browser_id === null || Number.isInteger(scope.browser_id) && scope.browser_id > 0,
    'Invalid explicit browser selection');
  mod = Process.getModuleByName('libcef.dll');
  ensure(mod.base.equals(ptr(capture.libcef_base)) && Process.id === capture.pid, 'CEF process/base changed');
  checkpoint('module.executable-ranges.begin');
  const rangeStartedAt = Date.now();
  executableRanges = mod.enumerateRanges('--x').map(range => ({base: range.base, end: range.base.add(range.size)}));
  ensure(executableRanges.length > 0, 'Checked CEF module has no executable mappings');
  checkpoint('module.executable-ranges.end', {range_count: executableRanges.length,
    enumeration_ms: Date.now() - rangeStartedAt});
  postTaskNative = new NativeFunction(mod.getExportByName('cef_post_task'), 'int', ['int', 'pointer']);
  currentlyOn = new NativeFunction(mod.getExportByName('cef_currently_on'), 'int', ['int']);
  Script.pin();
  state.pinned = true;
  state.phase = 'opening';
  state.docid = scope.docid;
  state.documentUrl = scope.document_url;
  try {
    postUI(() => {
      checkpoint('discovery.begin', {candidate_count: capture.candidates.length});
      const seen = new Set();
      const matches = [];
      let invalidated = 0;
      for (let index = 0; index < capture.candidates.length; index++) {
        const candidate = capture.candidates[index];
        const details = {candidate_index: index};
        const browser = ptr(candidate.capi);
        // A wrapper may have retired since the external scan. Do not call it.
        checkpoint('candidate.validate', details);
        try { checkBrowser(browser, layout); }
        catch (_) { invalidated++; checkpoint('candidate.invalidated', details); continue; }
        checkpoint('browser.retain.begin', details);
        addRef(browser);
        checkpoint('browser.retain.end', details);
        state.retainedBrowsers.set(browser.toString(), browser);
        checkpoint('browser.is-valid.begin', details);
        const valid = fn(browser, 40, 'int', ['pointer'])(browser) === 1;
        checkpoint('browser.is-valid.end', {...details, valid});
        if (!valid) { releaseBrowser(browser, details); continue; }
        checkpoint('browser.identifier.begin', details);
        const id = fn(browser, 120, 'int', ['pointer'])(browser);
        const identified = {...details, browser_id: id};
        checkpoint('browser.identifier.end', identified);
        if (seen.has(id)) { releaseBrowser(browser, identified); continue; }
        seen.add(id);
        // Explicit selection needs only this instance's URL. Other browsers do
        // not participate in binding and must not enter their frame getters.
        if (scope.browser_id !== null && id !== scope.browser_id) {
          checkpoint('candidate.skip-other-id', identified);
          releaseBrowser(browser, identified);
          continue;
        }
        const url = frameUrl(browser, {...identified, purpose: 'discovery'});
        checkpoint('candidate.url-checked', {...identified, matched_document: url === state.documentUrl});
        if (url === state.documentUrl) matches.push({browser, id, url});
        else releaseBrowser(browser, identified);
      }
      checkpoint('discovery.complete', {match_count: matches.length, invalidated_candidates: invalidated});
      matches.sort((left, right) => left.id - right.id);
      if (matches.length === 0) {
        throw selectionError('document_not_open',
          'No current checked browser matches the requested document; invalidated candidates: ' + invalidated, matches);
      }
      if (scope.browser_id === null && matches.length > 1) {
        throw selectionError('ambiguous_document', 'Several browser instances match the requested document', matches);
      }
      const selected = scope.browser_id === null ? matches[0] : matches.find(item => item.id === scope.browser_id);
      if (selected === undefined) {
        throw selectionError('document_not_open', 'The explicit browser ID does not match the requested document', matches);
      }
      for (const item of matches) {
        if (item !== selected) releaseBrowser(item.browser);
      }
      state.browser = selected.browser;
      state.browserId = selected.id;
      assertDocumentBinding();
      checkpoint('host.get.begin', {browser_id: state.browserId});
      const host = fn(state.browser, 48, 'pointer', ['pointer'])(state.browser);
      checkpoint('host.get.end', {browser_id: state.browserId, has_host: !host.isNull()});
      ensure(!host.isNull(), 'Browser host is null');
      state.host = host;
      sizeCheck(host, SIZE.host, 'host');
      ensure(layout.host.sha256 === EXPECTED_SHA256 && Number(layout.host.capi_base_size) === SIZE.host,
        'Missing exact Tencent host layout');
      ensure(layout.host.methods.length === 72, 'Incomplete host method table');
      layout.host.methods.forEach((method, index) => {
        const offset = Number(method.capi_offset);
        const address = mod.base.add(Number(method.function_rva));
        ensure(offset === 8 + index * 8 && inModule(address) && host.add(offset).readPointer().equals(address),
          'Host method table changed');
      });
      checkpoint('host.verified', {browser_id: state.browserId, method_count: 72});
      const observer = makeObserver();
      state.observer = observer;
      // Registration consumes its input reference; keep one local until close.
      observer.refs++;
      checkpoint('observer.register.begin', {browser_id: state.browserId});
      const registration = fn(host, 248, 'pointer', ['pointer', 'pointer'])(host, observer.memory);
      checkpoint('observer.register.end', {browser_id: state.browserId, registered: !registration.isNull()});
      ensure(!registration.isNull(), 'CEF did not register the CDP observer');
      state.registration = registration;
      sizeCheck(registration, SIZE.registration, 'registration');
      state.phase = 'ready';
      checkpoint('initialize.ready', {browser_id: state.browserId});
      emit('ready', {browser_id: state.browserId, document_url: state.documentUrl});
    }, 'initialize');
  } catch (error) { fatal(error); throw error; }
  return snapshot();
}

function request(method, input) {
  ensure(state.phase === 'ready', 'CDP adapter is not ready');
  ensure(state.pending.size < 16, 'Too many pending CDP requests');
  let params;
  if (method === 'Page.getFrameTree') {
    ensure(input === null || Object.keys(input).length === 0, 'Frame tree takes no parameters');
    params = {};
  } else if (method === 'Page.reload') {
    ensure(input !== null && Object.keys(input).length === 1 && input.docid === state.docid,
      'Refresh requires the exact bound docid explicitly');
    params = {ignoreCache: false};
  } else if (method === 'Runtime.evaluate') {
    ensure(input !== null && typeof input.expression === 'string' &&
      input.expression.length > 0 && input.expression.length <= 16384, 'Invalid document expression');
    ensure(Object.keys(input).every(key => key === 'expression' || key === 'document_script'), 'Evaluation options are fixed');
    ensure(input.document_script === undefined || typeof input.document_script === 'boolean', 'Invalid document script mode');
    const documentScript = input.document_script === true;
    // Recheck in the renderer too, so a navigation cannot redirect an editor call.
    const expression = '(() => {if(location.origin+location.pathname!==' + JSON.stringify(state.documentUrl) +
      ')throw new Error("Document scope changed");return (' + input.expression + ');})()';
    params = {
      expression, returnByValue: true, generatePreview: false,
      throwOnSideEffect: !documentScript, disableBreaks: true, awaitPromise: documentScript,
      userGesture: false, includeCommandLineAPI: false, silent: true,
      timeout: 3000, allowUnsafeEvalBlockedByCSP: false,
    };
  } else { throw new Error('CDP method is outside the document allowlist'); }
  const id = state.nextId++;
  ensure(id < 0x7fffffff, 'CDP request ID exhausted');
  const text = JSON.stringify({id, method, params});
  const memory = Memory.allocUtf8String(text);
  const length = unescape(encodeURIComponent(text)).length;
  state.pending.set(id, {memory, method});
  try {
    postUI(() => {
      if (!state.pending.has(id)) return;
      assertDocumentBinding();
      const sent = fn(state.host, 232, 'int', ['pointer', 'pointer', 'uint64'])(state.host, memory, length);
      if (sent !== 1) settle(id, false, {local_error: 'CEF rejected the CDP message'});
    }, 'request');
  } catch (error) { settle(id, false, {local_error: describe(error)}); fatal(error); }
  return id;
}

function closeOnUI() {
  assertUI();
  checkpoint('cleanup.begin');
  try {
    if (state.registration !== null) {
      checkpoint('cleanup.registration-release.begin');
      release(state.registration);
      checkpoint('cleanup.registration-release.end');
      state.registration = null;
    }
    if (state.observer !== null) { drop(state.observer); state.observer = null; }
    if (state.host !== null) {
      checkpoint('cleanup.host-release.begin');
      release(state.host);
      checkpoint('cleanup.host-release.end');
      state.host = null;
    }
    for (const browser of Array.from(state.retainedBrowsers.values())) releaseBrowser(browser);
    state.browser = null;
    state.cleanupComplete = true;
    checkpoint('cleanup.complete');
  } catch (error) {
    state.unsafe = true;
    fatal(error, true);
  } finally { state.cleanupQueued = false; deferRetire(); }
}
function beginClose() {
  if (state.phase === 'finished') return snapshot();
  if (state.phase === 'new') { state.phase = 'closing'; state.cleanupComplete = true; return snapshot(); }
  state.phase = 'closing';
  failPending('CDP adapter is closing');
  if (!state.cleanupComplete && !state.cleanupQueued && !state.unsafe) {
    state.cleanupQueued = true;
    try { postUI(closeOnUI, 'cleanup'); }
    catch (error) { state.cleanupQueued = false; state.errors.push(describe(error)); }
  }
  return snapshot();
}
function snapshot() {
  return {
    phase: state.phase, browser_id: state.browserId || null, pinned: state.pinned,
    document_url: state.documentUrl || null, retained_browser_count: state.retainedBrowsers.size,
    checkpoint: state.lastCheckpoint,
    cleanup_complete: state.cleanupComplete, callbacks: state.callbacks,
    objects: Array.from(state.records.values(), item => ({kind: item.kind, refs: item.refs, active: item.active, ran: item.ran})),
    pending_count: state.pending.size, unsafe_native_state: state.unsafe,
    safe_to_unload: (state.phase === 'closing' || state.phase === 'finished') && state.cleanupComplete &&
      !state.cleanupQueued && !state.retiring && state.callbacks === 0 &&
      state.records.size === 0 && state.pending.size === 0 && state.retainedBrowsers.size === 0 && !state.unsafe,
    errors: state.errors.slice(-8),
  };
}
function finish() {
  if (state.phase === 'finished') return {finished: true};
  ensure(snapshot().safe_to_unload, 'CEF callbacks/registrations/tasks have not drained; keep the script loaded');
  state.phase = 'finished';
  if (state.pinned) { Script.unpin(); state.pinned = false; }
  return {finished: true};
}
rpc.exports = {initialize, request, beginclose: beginClose, status: snapshot, finish};
