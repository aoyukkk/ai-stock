import fs from "node:fs/promises";
import path from "node:path";
import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const [inputPath, outputDir] = process.argv.slice(2);
if (!inputPath || !outputDir) throw new Error("usage: node verify_trader_demo_excel.mjs INPUT_XLSX OUTPUT_DIR");
const workbook = await SpreadsheetFile.importXlsx(await FileBlob.load(inputPath));
await fs.mkdir(outputDir, {recursive: true});
const specs = [
  ["01_全A量化排名", "A1:T12"], ["02_Top100_LLM评分", "A1:AG8"],
  ["03_挂单与仓位", "A1:AP10"], ["04_重点基本面", "A1:BD8"],
  ["05_说明与异常", "A1:G32"],
];
for (const [sheetName, range] of specs) {
  const image = await workbook.render({sheetName, range, scale: 0.9, format: "png"});
  await fs.writeFile(path.join(outputDir, `${sheetName}.png`), new Uint8Array(await image.arrayBuffer()));
}
const inspect = await workbook.inspect({
  kind: "table", range: "01_全A量化排名!A1:C5", include: "values,formulas",
  tableMaxRows: 5, tableMaxCols: 3, maxChars: 3000,
});
const errors = await workbook.inspect({
  kind: "match", searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: {useRegex: true, maxResults: 300}, summary: "final formula error scan", maxChars: 4000,
});
await fs.writeFile(path.join(outputDir, "inspect.ndjson"), `${inspect.ndjson}\n${errors.ndjson}\n`, "utf8");
