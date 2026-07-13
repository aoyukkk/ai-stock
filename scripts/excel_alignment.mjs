/** Apply the workbook-wide table alignment policy without changing cell values or number formats. */
export function applyCenteredAlignment(sheet, range) {
  sheet.getRange(range).format = {
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
  };
}
