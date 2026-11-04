// Browser stand-in for Node's `assert`, which netlistsvg's drawModule requires (only assert(cond, msg)).
function assert(cond, msg) {
  if (!cond) throw new Error(msg || "assertion failed");
}
assert.ok = assert;
assert.equal = (a, b, msg) => assert(a == b, msg); // eslint-disable-line eqeqeq
assert.strictEqual = (a, b, msg) => assert(a === b, msg);
module.exports = assert;
