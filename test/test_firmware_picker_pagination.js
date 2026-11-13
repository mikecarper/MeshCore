"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const picker = require("../docs/_javascript/firmware_picker.js");

function response(items) {
  return { ok: true, json: async () => items };
}

test("loads all pages before selecting a firmware release family", async () => {
  const family = "v1.18.0.1-halo-keymind-cascade-dev-03214a16";
  const previews = Array.from({ length: 100 }, (_, i) => ({
    tag_name: `preview-${i}`, prerelease: true,
  }));
  const main = { tag_name: family, name: "Stable", published_at: "2026-10-24" };
  const repeater = { tag_name: "lora-ota-" + family, assets: [{
    name: "Heltec_t1_repeater_lora_ota_no_external_sensors-full-ota-" + family + ".uf2",
    browser_download_url: "https://github.com/mikecarper/MeshCore/releases/download/x/image.uf2",
  }] };
  const urls = [];
  const releases = await picker.fetchAllReleases("https://api.example/releases", async (url, options) => {
    urls.push(url);
    assert.equal(options.headers.Accept, "application/vnd.github+json");
    return response(url.endsWith("page=1") ? previews : [main, repeater]);
  });
  assert.deepEqual(urls, ["https://api.example/releases?per_page=100&page=1",
    "https://api.example/releases?per_page=100&page=2"]);
  const set = picker.selectReleaseSet(releases);
  assert.equal(set.familyTag, family);
  assert(set.releases.includes(repeater));
  assert(picker.flattenReleaseAssets(set).some(item => item.name.startsWith("Heltec_t1_repeater")));
});

test("surfaces failures on later pages rather than using an incomplete catalog", async () => {
  await assert.rejects(picker.fetchAllReleases("/releases", async url =>
    url.endsWith("page=1") ? response(Array(100).fill({})) : { ok: false, status: 403 }), /HTTP 403/);
  await assert.rejects(picker.fetchAllReleases("/releases", async () => response({})), /Invalid GitHub release response/);
});

test("stops on an empty page after a full final page and rejects runaway pagination", async () => {
  let requests = 0;
  const values = await picker.fetchAllReleases("/releases", async () => response(++requests === 1 ? Array(100).fill({}) : []));
  assert.equal(values.length, 100);
  assert.equal(requests, 2);
  requests = 0;
  await assert.rejects(picker.fetchAllReleases("/releases", async () => {
    requests++;
    return response(Array(100).fill({}));
  }), /pagination limit/);
  assert.equal(requests, 100);
});
