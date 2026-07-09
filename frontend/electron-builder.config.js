export default {
  appId: "com.local.ai-trader-assistant",
  productName: "AI Trader Assistant",
  directories: {
    output: "release"
  },
  files: [
    "dist/**/*",
    "dist-electron/**/*",
    "package.json"
  ],
  win: {
    target: ["dir"],
    artifactName: "AI-Trader-Assistant-${version}-${arch}.${ext}"
  },
  asar: true
};
