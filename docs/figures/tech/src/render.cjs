// Renders the technical-design figures to PNG (2x):  node docs/figures/tech/src/render.cjs [architecture writepath dag]
const { chromium } = require("playwright");
const path = require("path");
(async () => {
  const names = process.argv.slice(2).length ? process.argv.slice(2) : ["architecture", "writepath", "dag"];
  const browser = await chromium.launch();
  for (const name of names) {
    const page = await browser.newPage({ viewport: { width: 1700, height: 1200 }, deviceScaleFactor: 2 });
    page.on("pageerror", (e) => console.error(name, "page error:", e.message));
    await page.goto("file://" + path.join(__dirname, name + ".html"));
    await page.evaluate(() => document.fonts.ready);
    await page.waitForTimeout(300);
    await (await page.$("#fig")).screenshot({ path: path.join(__dirname, "..", name + ".png") });
    console.log("rendered", name);
  }
  await browser.close();
})();
