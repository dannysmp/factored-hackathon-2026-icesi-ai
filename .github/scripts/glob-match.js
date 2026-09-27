"use strict";

/**
 * Glob Translation
 * ================
 *
 * Overview
 * --------
 * Translates one glob pattern from `.claude/ownership.json` into a `RegExp` that matches a full
 * repository-relative path. `.github/scripts/glob-match.test.js` exercises this module directly
 * with an executed fixture test.
 *
 * Scope
 * -----
 * In: the pattern-to-regex translation, kept correct against fixtures.
 * Out: deciding which patterns are governed (`.claude/ownership.json`, maintainer-edited) and
 * acting on a match (`.github/workflows/review-gate.yml`, which currently carries its own
 * separate inline copy of this same logic rather than requiring this module — a change to one
 * does not yet reach the other; see the module's own docstring in that workflow for its copy).
 *
 * Design Principles
 * -----------------
 * - `**/` matches zero or more whole path segments, so it also matches a root-level file with
 *   nothing before the match; `**` matches any path, directory separators included; `*` matches
 *   within one path segment only; every other character, including `?`, is literal.
 *
 * Runtime Contract
 * -----------------
 * `globToRegex(glob) -> RegExp`; `escapeLiteral(text) -> string`.
 */

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
