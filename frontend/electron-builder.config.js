export default {
  appId: "com.local.ai-trader-assistant",
  productName: "AI Trader Assistant",
  directories: {
    output: "../build/electron"
  },
  files: [
    "dist/**/*",
    "dist-electron/electron/*.js",
    "!dist-electron/electron/*.spec.js",
    "package.json"
  ],
  extraResources: [
    {
      from: "../build/backend/ai-trader-backend",
      to: "backend"
    },
    {
      from: "../config",
      to: "config",
      filter: ["**/*.yaml", "**/*.yml"]
    }
  ],
  win: {
    target: [
      {
        target: "nsis",
        arch: ["x64"]
      },
      {
        target: "dir",
        arch: ["x64"]
      }
    ],
    artifactName: "AI-Trader-Assistant-Setup-${version}-${arch}.${ext}",
    signAndEditExecutable: true,
    verifyUpdateCodeSignature: false
  },
  nsis: {
    oneClick: false,
    allowToChangeInstallationDirectory: true,
    createDesktopShortcut: true,
    createStartMenuShortcut: true,
    shortcutName: "AI Trader Assistant",
    uninstallDisplayName: "AI Trader Assistant"
  },
  asar: true,
  npmRebuild: false
};
