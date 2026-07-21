import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const [inputPath, outputDir, ...sheetNames] = process.argv.slice(2);
if (!inputPath || !outputDir || !sheetNames.length) {
  throw new Error("usage: node render_workbook_sheets.mjs INPUT_XLSX OUTPUT_DIR SHEET...");
}

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
await fs.mkdir(outputDir, { recursive: true });
for (const sheetName of sheetNames) {
  const renderRange = process.env.ARTIFACT_RENDER_RANGE;
  const preview = await workbook.render({
    sheetName,
    ...(renderRange ? { range: renderRange } : { autoCrop: "all" }),
    scale: 1,
    format: "png",
  });
  await fs.writeFile(
    path.join(outputDir, `${sheetName}.png`),
    new Uint8Array(await preview.arrayBuffer()),
  );
}

const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 200 },
  summary: "公式错误检查",
  maxChars: 4000,
});
await fs.writeFile(path.join(outputDir, "公式检查.ndjson"), errors.ndjson, "utf8");
