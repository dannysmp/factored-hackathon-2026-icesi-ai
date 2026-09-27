"use strict";

// Executed evidence for the glob translation: every fixture below is asserted against the actual
// function the review gate requires, and the process exits non-zero on the first failure, so a
// broken translation fails loudly instead of merging quietly.
// Run with: node .github/scripts/glob-match.test.js

const assert = require("node:assert/strict");
const { globToRegex } = require("./glob-match.js");

function check(pattern, path, expected, label) {
  const matched = globToRegex(pattern).test(path);
  assert.equal(
    matched,
    expected,
    `${label}: /${pattern}/ against "${path}" expected ${expected}, got ${matched}`
  );
}

// Root file matched by a doubled-star prefix: a leading `**/` must also match a path with nothing
// before it, not only a path that has at least one directory.
check("**/Dockerfile*", "Dockerfile", true, "root file, ** prefix");
check("**/Dockerfile*", "infra/Dockerfile.web", true, "nested file, ** prefix");

// A plain literal pattern matches only its own path, not a same-named file elsewhere.
check("Makefile", "Makefile", true, "exact root file");
check("Makefile", "app/Makefile", false, "exact root file does not match a nested one");

// A single-segment star does not cross a directory boundary; a double star does.
check("app/security/*", "app/security/errors.py", true, "single-segment star, direct child");
check(
  "app/security/*",
  "app/security/nested/deep.py",
  false,
  "single-segment star does not cross a directory"
);
check("app/security/**", "app/security/nested/deep.py", true, "double star, any depth");
check("app/security/**", "app/domain/policy/models.py", false, "non-match: different directory");

// A rename: the gate's own logic unions a file's old and new name and tests each independently,
// so moving a file out of a governed path is still caught; this fixture proves the matcher judges
// each name correctly on its own, which that logic depends on.
check("app/tools/**", "app/tools/create.py", true, "rename source, still governed");
check("app/tools/**", "app/other/create.py", false, "rename target, no longer governed");

// A literal `?` is not a regex quantifier: it matches only itself, never zero or one of the
// preceding character.
check("weird?name.txt", "weird?name.txt", true, "literal question mark matches itself");
check("weird?name.txt", "weirdname.txt", false, "literal question mark is not zero-or-one");

console.log("glob-match: all fixtures passed");
