import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const [inputPath, outputDir] = process.argv.slice(2);
if (!inputPath || !outputDir) throw new Error("缺少工作簿或预览目录");

const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
await fs.mkdir(outputDir, { recursive: true });
const ranges = {
  "今日概览": "A1:L18",
  "今日推荐": "A1:K16",
  "重点候选": "A1:V16",
  "价格与权重": "A1:X16",
  "复核依据": "A1:S12",
  "量化前100": "A1:L18",
  "当前问题": "A1:E10",
};
for (const [sheetName, range] of Object.entries(ranges)) {
  const preview = await workbook.render({ sheetName, range, scale: 1.1, format: "png" });
  await fs.writeFile(path.join(outputDir, `${sheetName}.png`), new Uint8Array(await preview.arrayBuffer()));
}
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "最终公式错误检查",
  maxChars: 3000,
});
await fs.writeFile(path.join(outputDir, "公式检查.ndjson"), errors.ndjson, "utf8");
