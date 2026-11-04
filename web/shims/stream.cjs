// Browser stand-in for Node's `stream`: sax (via onml, used by netlistsvg) subclasses Stream for its
// streaming API, which svkit never uses. Parsing strings needs nothing from it.
function Stream() {}
Stream.prototype.pipe = function () {
  throw new Error("streams are not available in the browser");
};
module.exports = { Stream };
