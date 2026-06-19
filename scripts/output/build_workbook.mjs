// Workbook builder scaffold.
// Implement with @oai/artifact-tool when producing final .xlsx files.

import fs from "node:fs/promises";

const inputPath = process.env.WORKPAPER_INPUT_JSON;
const outputPath = process.env.WORKPAPER_OUTPUT_XLSX;

if (!inputPath || !outputPath) {
  throw new Error("WORKPAPER_INPUT_JSON and WORKPAPER_OUTPUT_XLSX are required");
}

await fs.access(inputPath);
throw new Error("TODO: implement workbook generation from references/output_workbook_spec.md");
