#!/usr/bin/env node
"use strict";

// Self-registering installer for the Bubble Buddy support skill.
//
// Run it with:  npx @bubble-buddy/skills
//
// npm/npx downloads this whole package first — SKILL.md *and* every
// references/ file — then this script points the Copilot CLI at the bundled
// skill directory so nothing has to be fetched at runtime.

const { spawnSync } = require("node:child_process");
const path = require("node:path");
const fs = require("node:fs");

const skillDir = path.join(__dirname, "..", "bubble-buddy");
const skillFile = path.join(skillDir, "SKILL.md");

if (!fs.existsSync(skillFile)) {
  console.error(
    `Could not find the bundled skill at ${skillDir}. ` +
      "The package looks incomplete — try reinstalling."
  );
  process.exit(1);
}

console.log(`Registering the Bubble Buddy skill from:\n  ${skillDir}\n`);

// On Windows the Copilot CLI is a `.bat`/`.cmd` shim, which Node can only spawn
// through a shell (spawning it directly throws EINVAL on modern Node). Quote the
// path ourselves so directories containing spaces still work. On POSIX we spawn
// with an argv array (no shell), which is both safe and space-proof.
const result =
  process.platform === "win32"
    ? spawnSync(`copilot skill add "${skillDir}"`, {
        stdio: "inherit",
        shell: true,
      })
    : spawnSync("copilot", ["skill", "add", skillDir], { stdio: "inherit" });

const notFound =
  (result.error && result.error.code === "ENOENT") ||
  (result.error && result.error.code === "EINVAL");
if (notFound) {
  console.error(
    "\nThe GitHub Copilot CLI ('copilot') was not found on your PATH.\n" +
      "Install it first (https://docs.github.com/copilot/how-tos/copilot-cli),\n" +
      "then register the skill manually with:\n" +
      `  copilot skill add "${skillDir}"`
  );
  process.exit(1);
}

if (typeof result.status === "number" && result.status !== 0) {
  process.exit(result.status);
}

console.log(
  "\nDone. Start the Copilot CLI and ask it to help, e.g.:\n" +
    '  copilot -p "Help me install and configure Bubble Buddy"'
);
