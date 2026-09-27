"use strict";

// Translates one glob pattern from `.claude/ownership.json` into a `RegExp` that matches a full
// repository-relative path. The review gate's own script requires this module, and
// `glob-match.test.js` exercises it directly, so the gate and its test never drift apart.
//
// Grammar: `**/` matches zero or more whole path segments, so it also matches a root-level file
// with nothing before the match; `**` matches any path, directory separators included; `*`
// matches within one path segment only; every other character, including `?`, is literal.

function escapeLiteral(text) {
  return text.replace(/[.+^${}()|[\]\\?]/g, "\\$&");
}

function globToRegex(glob) {
  let out = "";
  for (let i = 0; i < glob.length; ) {
    if (glob.startsWith("**/", i)) {
      out += "(?:.*/)?";
      i += 3;
    } else if (glob.startsWith("**", i)) {
      out += ".*";
      i += 2;
    } else if (glob[i] === "*") {
      out += "[^/]*";
      i += 1;
    } else {
      out += escapeLiteral(glob[i]);
      i += 1;
    }
  }
  return new RegExp("^" + out + "$");
}

module.exports = { globToRegex, escapeLiteral };
