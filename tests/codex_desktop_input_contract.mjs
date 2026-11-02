// Offline regression against the pinned desktop's actual plain-text decoder.
// Never evaluates the application bundle, opens IPC, or invokes desktop UI.
import fs from 'node:fs';
import vm from 'node:vm';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';

const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const fd = fs.openSync('/Applications/ChatGPT.app/Contents/Resources/app.asar', 'r');
let source;
try {
  const header = Buffer.alloc(16);
  fs.readSync(fd, header, 0, 16, 0);
  const headerSize = header.readUInt32LE(12);
  assert(headerSize > 0 && headerSize < 16 * 1024 * 1024);
  const bytes = Buffer.alloc(headerSize);
  fs.readSync(fd, bytes, 0, bytes.length, 16);
  let entry = JSON.parse(bytes.toString());
  for (const part of 'webview/assets/app-initial-61c1d0e6137e.js'.split('/')) entry = entry.files[part];
  assert(entry.size < 64 * 1024 * 1024);
  const data = Buffer.alloc(entry.size);
  fs.readSync(fd, data, 0, data.length, 8 + header.readUInt32LE(4) + Number(entry.offset));
  assert.equal(crypto.createHash('sha256').update(data).digest('hex'),
    '7a8d8b711bf2eb1adbfab04818a63bf6517935d11bb4d0d4c9d6602d1e3fe6f2');
  source = data.toString();
} finally {
  fs.closeSync(fd);
}
// 26.1027: the plain-text decoder is `nDe` (was `mrn`); it still reads
// text_elements.length without a default and returns null for plain text.
const start = source.indexOf('function nDe(');
const end = source.indexOf('function ', start + 10);
assert(start >= 0 && end > start && end - start < 1500);
const decoder = source.slice(start, end);
assert(decoder.includes('e.text_elements.length'));
function decode(value) {
  return vm.runInNewContext(`(${decoder})(value)`, {value}, {timeout: 1000});
}
assert.throws(() => decode({type: 'text', text: 'fixture'}), /Cannot read properties of undefined.*length/);
const items = input.turnStart.request.input;
assert.equal(items.length, 1);
for (const item of items) {
  assert.equal(item.type, 'text');
  assert.equal(typeof item.text, 'string');
  assert.deepEqual(item.text_elements, []);
  assert.equal(decode(item), null);
}
console.log(JSON.stringify({original_bug_reproduced: true, corrected_input_passed: true}));
