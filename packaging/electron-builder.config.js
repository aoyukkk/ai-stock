module.exports = {
  appId: "com.local.ai-trader-assistant",
  productName: "AI Trader Assistant",
  directories: {
    output: "frontend/release"
  },
  files: [
    "frontend/dist/**/*",
    "frontend/dist-electron/**/*",
    "frontend/package.json"
  ],
  win: {
    target: ["dir"],
    artifactName: "AI-Trader-Assistant-${version}-${arch}.${ext}"
  },
  asar: true
};
