import path from "node:path";

import base from "./electron-builder.config.js";

const reviewRoot = path.resolve(
  process.env.AI_TRADER_REVIEW_ROOT || "../outputs/security_remediation_20260730"
);
const expectedRoot = path.resolve("..", "outputs", "security_remediation_20260730");
if (reviewRoot !== expectedRoot) {
  throw new Error("REVIEW_BUILD_OUTPUT_MUST_USE_ISOLATED_SECURITY_REMEDIATION_DIRECTORY");
}

export default {
  ...base,
  electronDist: path.resolve("node_modules", "electron", "dist"),
  directories: {
    ...base.directories,
    output: path.join(reviewRoot, "desktop_build")
  },
  extraResources: [
    {
      from: path.join(reviewRoot, "backend_dist", "backend"),
      to: "backend"
    },
    {
      from: "../config",
      to: "config",
      filter: ["**/*.yaml", "**/*.yml"]
    }
  ]
};
