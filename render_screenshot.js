// render_screenshot.js
const puppeteer = require('puppeteer');
const fs = require('fs');
const path = require('path');

(async () => {
  try {
    const htmlPath = `file://${process.cwd()}/docs/index.html`;
    const outPath = path.join(process.cwd(), 'docs', 'sunburst_screenshot.png');
    const browser = await puppeteer.launch({
      args: ['--no-sandbox', '--disable-setuid-sandbox']
    });
    const page = await browser.newPage();
    await page.setViewport({ width: 1400, height: 900 });
    await page.goto(htmlPath, { waitUntil: 'networkidle0' });

    // Wait for the chart's svg element to exist
    await page.waitForSelector('#chart svg', { timeout: 10000 });

    // select the svg element and get its bounding box
    const svgHandle = await page.$('#chart svg');
    if (!svgHandle) {
      console.error('SVG element not found');
    } else {
      const bbox = await svgHandle.boundingBox();
      console.log('SVG bounding box:', bbox);
    }


    // screenshot the bounding box of the svg
    const boundingBox = await svgHandle.boundingBox();
    if (!boundingBox) {
      // as a fallback, screenshot full page
      await page.screenshot({ path: outPath, fullPage: true });
    } else {
      await page.screenshot({
        path: outPath,
        clip: {
          x: Math.floor(boundingBox.x),
          y: Math.floor(boundingBox.y),
          width: Math.ceil(boundingBox.width),
          height: Math.ceil(boundingBox.height)
        }
      });
    }
    console.log('Wrote screenshot:', outPath);
    await browser.close();
    process.exit(0);
  } catch (err) {
    console.error('Screenshot error:', err);
    process.exit(1);
  }
})();
await page.goto(htmlPath, { waitUntil: 'networkidle0' });
// wait extra time for D3 to render
await page.waitForTimeout(2000);  // <-- add 2 seconds
