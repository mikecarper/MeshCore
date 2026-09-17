(function (global) {
  "use strict";

  // This decoder intentionally uses only the browser Web Crypto API.  In
  // particular, the management password is never sent to a server or put in a
  // URL.  AES-SIV needs CMAC and ECB-style single-block encryption, neither of
  // which Web Crypto exposes directly.  AES-CBC with a zero IV supplies the
  // required AES block primitive; only its first ciphertext block is used.
  const HEADER = 83;
  const TAG = 16;
  const ENTRY = 13;
  const PER_PAGE = 6;
  const MAX_KEYS = 36;
  const ZERO_BLOCK = new Uint8Array(16);
  const UTF8 = new TextEncoder();

  const EXAMPLE_PASSWORD = "management test password";
  const EXAMPLE_PAGE =
    "4D475231000102030405060708090A0B0C0D0E0F2A00000000F153650501110106040200CDAB3412112233445566778840E2010000900100070700004800700E2850840E2A4EA815011F051F4F00000101000138BD699262E721CD2E9018D9E52A0AF74669C9D56685DEFFD8ABEA3C58";

  class ManagementDecodeError extends Error {
    constructor(message) {
      super(message);
      this.name = "ManagementDecodeError";
    }
  }

  function bytesToHex(bytes) {
    return Array.from(bytes, (value) => value.toString(16).padStart(2, "0"))
      .join("")
      .toUpperCase();
  }

  function hexToBytes(text) {
    if (typeof text !== "string") {
      throw new ManagementDecodeError("Packet data must be hexadecimal text.");
    }
    const normalized = text
      .trim()
      .replace(/^['"]|['",;]$/g, "")
      .replace(/0x/gi, "")
      .replace(/[\s:,_-]/g, "");
    if (!normalized || normalized.length % 2 || !/^[0-9a-f]+$/i.test(normalized)) {
      throw new ManagementDecodeError("Packet data must contain complete hexadecimal bytes.");
    }
    const bytes = new Uint8Array(normalized.length / 2);
    for (let index = 0; index < bytes.length; index += 1) {
      bytes[index] = Number.parseInt(normalized.slice(index * 2, index * 2 + 2), 16);
    }
    return bytes;
  }

  function concatBytes(...parts) {
    const length = parts.reduce((total, part) => total + part.length, 0);
    const result = new Uint8Array(length);
    let offset = 0;
    parts.forEach((part) => {
      result.set(part, offset);
      offset += part.length;
    });
    return result;
  }

  function equalBytes(left, right) {
    if (left.length !== right.length) return false;
    let difference = 0;
    for (let index = 0; index < left.length; index += 1) difference |= left[index] ^ right[index];
    return difference === 0;
  }

  function uint16LE(bytes, offset) {
    return bytes[offset] | (bytes[offset + 1] << 8);
  }

  function uint32LE(bytes, offset) {
    return (
      bytes[offset] |
      (bytes[offset + 1] << 8) |
      (bytes[offset + 2] << 16) |
      (bytes[offset + 3] << 24)
    ) >>> 0;
  }

  function managementError(message) {
    throw new ManagementDecodeError(message);
  }

  function cryptoApi() {
    if (!global.crypto || !global.crypto.subtle) {
      managementError("This browser does not provide Web Crypto. Open the decoder over HTTPS in a current browser.");
    }
    return global.crypto.subtle;
  }

  function doubleBlock(block) {
    const result = new Uint8Array(16);
    const carry = block[0] >> 7;
    for (let index = 0; index < 15; index += 1) {
      result[index] = ((block[index] << 1) | (block[index + 1] >> 7)) & 0xff;
    }
    result[15] = ((block[15] << 1) & 0xff) ^ (carry ? 0x87 : 0);
    return result;
  }

  async function aesBlockEncryptor(keyBytes) {
    const subtle = cryptoApi();
    const key = await subtle.importKey("raw", keyBytes, { name: "AES-CBC" }, false, ["encrypt"]);
    return async function encryptBlock(block) {
      if (block.length !== 16) managementError("Internal AES block length is invalid.");
      const encrypted = new Uint8Array(await subtle.encrypt({ name: "AES-CBC", iv: ZERO_BLOCK }, key, block));
      // Web Crypto's AES-CBC applies PKCS#7 padding. Its first block is exactly
      // AES-ECB(key, block), which is the CMAC primitive required by RFC 5297.
      return encrypted.slice(0, 16);
    };
  }

  async function cmac(encryptBlock, data) {
    const l = await encryptBlock(ZERO_BLOCK);
    const k1 = doubleBlock(l);
    let chain = new Uint8Array(16);
    let offset = 0;
    while (data.length - offset > 16) {
      const block = new Uint8Array(16);
      for (let index = 0; index < 16; index += 1) block[index] = chain[index] ^ data[offset + index];
      chain = await encryptBlock(block);
      offset += 16;
    }
    const remaining = data.length - offset;
    const final = new Uint8Array(16);
    if (remaining === 16) {
      for (let index = 0; index < 16; index += 1) final[index] = data[offset + index] ^ k1[index];
    } else {
      const k2 = doubleBlock(k1);
      for (let index = 0; index < remaining; index += 1) final[index] = data[offset + index];
      final[remaining] = 0x80;
      for (let index = 0; index < 16; index += 1) final[index] ^= k2[index];
    }
    for (let index = 0; index < 16; index += 1) final[index] ^= chain[index];
    return encryptBlock(final);
  }

  async function s2v(macKey, aad, plaintext) {
    const encryptBlock = await aesBlockEncryptor(macKey);
    let d = await cmac(encryptBlock, ZERO_BLOCK);
    const aadMac = await cmac(encryptBlock, aad);
    d = doubleBlock(d);
    for (let index = 0; index < 16; index += 1) d[index] ^= aadMac[index];

    if (plaintext.length >= 16) {
      const adjusted = plaintext.slice();
      const last = adjusted.length - 16;
      for (let index = 0; index < 16; index += 1) adjusted[last + index] ^= d[index];
      return cmac(encryptBlock, adjusted);
    }

    d = doubleBlock(d);
    const padded = new Uint8Array(16);
    padded.set(plaintext);
    padded[plaintext.length] = 0x80;
    for (let index = 0; index < 16; index += 1) padded[index] ^= d[index];
    return cmac(encryptBlock, padded);
  }

  async function ctrCrypt(keyBytes, tag, input) {
    const subtle = cryptoApi();
    const counter = tag.slice();
    counter[8] &= 0x7f;
    counter[12] &= 0x7f;
    const key = await subtle.importKey("raw", keyBytes, { name: "AES-CTR" }, false, ["decrypt"]);
    return new Uint8Array(await subtle.decrypt(
      { name: "AES-CTR", counter, length: 128 }, key, input
    ));
  }

  async function openSiv(key, aad, ciphertext, tag) {
    if (key.length !== 32 || tag.length !== TAG) managementError("Management encryption data has an invalid length.");
    const plaintext = await ctrCrypt(key.slice(16), tag, ciphertext);
    const expected = await s2v(key.slice(0, 16), aad, plaintext);
    if (!equalBytes(expected, tag)) {
      plaintext.fill(0);
      managementError("Password is wrong or this management page was modified.");
    }
    return plaintext;
  }

  async function sha256(data) {
    return new Uint8Array(await cryptoApi().digest("SHA-256", data));
  }

  async function hmacSha256(keyBytes, data) {
    const subtle = cryptoApi();
    const key = await subtle.importKey("raw", keyBytes, { name: "HMAC", hash: "SHA-256" }, false, ["sign"]);
    return new Uint8Array(await subtle.sign("HMAC", key, data));
  }

  async function passwordKey(password) {
    if (typeof password !== "string") managementError("Enter the management password as text.");
    const encoded = UTF8.encode(password);
    if (encoded.length < 12 || encoded.length > 96) {
      managementError("Management passwords must contain 12 through 96 UTF-8 bytes.");
    }
    return sha256(concatBytes(Uint8Array.of(0x23), encoded));
  }

  async function deriveKey(root, domain, radio) {
    return hmacSha256(root, concatBytes(UTF8.encode(domain), radio));
  }

  async function aclFingerprint(password, radio, administrator) {
    const normalized = typeof administrator === "string" ? hexToBytes(administrator) : administrator;
    if (normalized.length !== 32) managementError("A candidate administrator key must be a complete 32-byte public key.");
    const root = await passwordKey(password);
    const key = await deriveKey(root, "MeshCore-MGR1-ACL", radio);
    return (await hmacSha256(key, concatBytes(radio, normalized))).slice(0, 12);
  }

  function canonicalLength(payload) {
    if (payload.length < HEADER + TAG || bytesToAscii(payload, 0, 4) !== "MGR1") {
      managementError("This data does not begin with an MGR1 management page.");
    }
    const page = payload[78];
    const pages = payload[79];
    const total = payload[80];
    const first = payload[81];
    const count = payload[82];
    const expectedPages = total ? Math.ceil(total / PER_PAGE) : 1;
    if (total > MAX_KEYS || pages !== expectedPages || page >= pages ||
        first !== page * PER_PAGE || first > total ||
        count !== Math.min(PER_PAGE, total - first)) {
      managementError("The MGR1 page index or ACL bounds are invalid.");
    }
    return HEADER + count * ENTRY + TAG;
  }

  function paddedFloodLength(canonical) {
    return 3 + Math.ceil((canonical - 3) / 16) * 16;
  }

  function bytesToAscii(bytes, offset, length) {
    let text = "";
    for (let index = 0; index < length; index += 1) text += String.fromCharCode(bytes[offset + index]);
    return text;
  }

  function routeDescription(route) {
    return ["transport flood", "flood", "direct", "transport direct"][route] || "unknown";
  }

  function parsePacket(bytes) {
    if (bytes.length < 2 || bytes[0] >> 6 !== 0 || ((bytes[0] >> 2) & 0x0f) !== 0x06) return null;
    const route = bytes[0] & 0x03;
    const pathOffset = route === 0 || route === 3 ? 5 : 1;
    if (pathOffset >= bytes.length) return null;
    const pathInfo = bytes[pathOffset];
    const width = (pathInfo >> 6) + 1;
    const hops = pathInfo & 0x3f;
    if (width > 3 || hops * width > 64) return null;
    const payloadOffset = pathOffset + 1 + width * hops;
    if (payloadOffset >= bytes.length || bytesToAscii(bytes, payloadOffset, 4) !== "MGR1") return null;
    const payload = bytes.slice(payloadOffset);
    const canonical = canonicalLength(payload);
    const padded = paddedFloodLength(canonical);
    if (payload.length !== canonical &&
        (payload.length !== padded || !payload.slice(canonical).every((value) => value === 0))) {
      managementError("MGR1 packet padding or length is invalid.");
    }
    return {
      payload: payload.slice(0, canonical),
      envelope: {
        header: bytes[0],
        route: routeDescription(route),
        routeCode: route,
        pathHops: hops,
        pathHashBytes: width,
      },
    };
  }

  function parseCanonical(bytes) {
    if (bytesToAscii(bytes, 0, 4) !== "MGR1") return null;
    const canonical = canonicalLength(bytes);
    const padded = paddedFloodLength(canonical);
    if (bytes.length !== canonical &&
        (bytes.length !== padded || !bytes.slice(canonical).every((value) => value === 0))) {
      managementError("MGR1 payload padding or length is invalid.");
    }
    return { payload: bytes.slice(0, canonical), envelope: null };
  }

  function inputByteStreams(input) {
    if (typeof input !== "string" || input.trim() === "") {
      managementError("Paste one or more MGR1 payloads or GroupData packet hex values first.");
    }
    if (input.length > 32768) managementError("The pasted value is too large to be management-report data.");
    const candidates = [input, ...input.split(/\r?\n/)];
    const quotedRaw = /["'](?:raw|data)["']\s*:\s*["']([^"']+)["']/gi;
    let match;
    while ((match = quotedRaw.exec(input)) !== null) candidates.push(match[1]);
    const streams = input.match(/(?:0x)?[0-9a-f]{2}(?:(?:[\s:,_-]*)(?:0x)?[0-9a-f]{2}){15,}/gi);
    if (streams) candidates.push(...streams);

    const unique = new Map();
    candidates.forEach((candidate) => {
      try {
        const bytes = hexToBytes(candidate);
        unique.set(bytesToHex(bytes), bytes);
      } catch (_error) {
        // Explanatory prose and JSON wrappers are expected around analyzer data.
      }
    });
    return [...unique.values()];
  }

  function parseInput(input) {
    const found = [];
    const seen = new Set();
    for (const bytes of inputByteStreams(input)) {
      let parsed = null;
      try {
        parsed = parseCanonical(bytes) || parsePacket(bytes);
      } catch (error) {
        if (error instanceof ManagementDecodeError && bytesToAscii(bytes, 0, 4) !== "MGR1") {
          // It may be an unrelated hex stream alongside a valid packet.
          continue;
        }
        throw error;
      }
      if (!parsed) continue;
      const key = bytesToHex(parsed.payload);
      if (!seen.has(key)) {
        seen.add(key);
        found.push(parsed);
      }
    }
    if (!found.length) {
      managementError("No complete MGR1 management page was found. Paste canonical MGR1 payload hex or an entire GroupData packet.");
    }
    return found;
  }

  function temperature(value) {
    if (value === 0) return "unavailable";
    if (value === 252) return "below -50 C";
    if (value === 253) return "above 200 C";
    if (value > 253) return "reserved/invalid";
    return `${value - 51} C`;
  }

  function extrema(bytes, offset) {
    const voltage = uint16LE(bytes, offset);
    return `${voltage ? `${voltage} mV` : "unavailable"}; ${temperature(bytes[offset + 2])} to ${temperature(bytes[offset + 3])}`;
  }

  function version(bytes, offset) {
    const value = uint32LE(bytes, offset);
    return `${(value >>> 24) & 0xff}.${(value >>> 16) & 0xff}.${(value >>> 8) & 0xff}.${value & 0xff}`;
  }

  function featureNames(bits) {
    const definitions = [[1, "Wi-Fi"], [2, "GPS"], [4, "NTP time"], [8, "USB data"], [16, "LoRa OTA"]];
    const names = definitions.filter(([bit]) => bits & bit).map(([, name]) => name);
    return names.length ? names.join(", ") : "none";
  }

  function roleName(value) {
    return ({ 1: "repeater", 2: "room server", 3: "sensor" })[value] || `unknown (${value})`;
  }

  function publicFields(payload, envelope) {
    const valid = uint16LE(payload, 76);
    const capabilities = payload[73];
    const active = payload[74];
    const known = payload[75];
    const knownActive = featureNames(active & known);
    const unknownActive = featureNames(capabilities & ~known);
    return {
      radioId: bytesToHex(payload.slice(4, 20)),
      sequence: uint32LE(payload, 20),
      timestamp: uint32LE(payload, 24),
      firmware: valid & 1 ? version(payload, 28) : "unavailable",
      bootloader: valid & 2 ? version(payload, 32) : "unavailable",
      target: valid & 4 ? uint32LE(payload, 36).toString(16).padStart(8, "0").toUpperCase() : "unavailable",
      baseHash: valid & 4 ? bytesToHex(payload.slice(40, 48)) : "unavailable",
      imageLength: valid & 4 ? `${uint32LE(payload, 48)} bytes` : "unavailable",
      staging: valid & 8 ? `${uint32LE(payload, 52)} bytes` : "unavailable",
      otaCapabilities: `0x${uint32LE(payload, 56).toString(16).padStart(8, "0").toUpperCase()}`,
      uptime: `${uint16LE(payload, 60)} hours`,
      weekly: extrema(payload, 62),
      sinceReport: extrema(payload, 66),
      history: `${payload[70]} hours${valid & 16 ? " (partial)" : ""}`,
      interval: `${payload[71]} days`,
      role: roleName(payload[72]),
      compiled: featureNames(capabilities),
      active: knownActive + (unknownActive !== "none" ? `; unknown: ${unknownActive}` : ""),
      page: `${payload[78] + 1} of ${payload[79]}`,
      acl: `${payload[80]} total; ${payload[82]} on this page`,
      partialSince: Boolean(valid & 32),
      mcuTemperature: Boolean(valid & 64),
      envelope,
    };
  }

  function reportGroup(pages) {
    const first = pages[0];
    const radio = bytesToHex(first.payload.slice(4, 20));
    const sequence = uint32LE(first.payload, 20);
    const byPage = new Map();
    pages.forEach((page) => {
      if (bytesToHex(page.payload.slice(4, 20)) !== radio || uint32LE(page.payload, 20) !== sequence) {
        managementError("Input contains more than one report. Decode one radio and sequence at a time.");
      }
      const index = page.payload[78];
      if (byPage.has(index) && !equalBytes(byPage.get(index).payload, page.payload)) {
        managementError("Conflicting copies were provided for the same management page.");
      }
      byPage.set(index, page);
    });
    const sorted = [...byPage.values()].sort((left, right) => left.payload[78] - right.payload[78]);
    const reference = sorted[0].payload;
    if (sorted.some((page) => !equalBytes(page.payload.slice(0, 78), reference.slice(0, 78)) ||
        page.payload[79] !== reference[79] || page.payload[80] !== reference[80])) {
      managementError("MGR1 pages do not belong to the same snapshot.");
    }
    return sorted;
  }

  async function decryptPage(payload, password, candidate) {
    const root = await passwordKey(password);
    const key = await deriveKey(root, "MeshCore-MGR1-SIV", payload.slice(4, 20));
    const privateLength = payload[82] * ENTRY;
    const plaintext = await openSiv(key, payload.slice(0, HEADER),
      payload.slice(HEADER, HEADER + privateLength), payload.slice(HEADER + privateLength));
    try {
      let wanted = null;
      if (candidate) wanted = await aclFingerprint(password, payload.slice(4, 20), candidate);
      const entries = [];
      for (let offset = 0; offset < plaintext.length; offset += ENTRY) {
        const flags = plaintext[offset + 12];
        if (!flags || flags & ~3) managementError("Authenticated ACL data contains invalid role flags.");
        const fingerprint = plaintext.slice(offset, offset + 12);
        entries.push({
          index: payload[81] + offset / ENTRY,
          fingerprint: bytesToHex(fingerprint),
          administrator: Boolean(flags & 1),
          otaSigner: Boolean(flags & 2),
          candidateMatch: wanted ? equalBytes(fingerprint, wanted) : null,
        });
      }
      return entries;
    } finally {
      plaintext.fill(0);
    }
  }

  async function decodeManagement(input, password, candidate) {
    const pages = reportGroup(parseInput(input));
    const first = pages[0];
    const model = {
      public: publicFields(first.payload, first.envelope),
      pageCount: first.payload[79],
      suppliedPages: pages.length,
      complete: pages.length === first.payload[79],
      authenticated: false,
      acl: [],
      warnings: [],
    };
    if (!password) {
      model.warnings.push("Public fields are plaintext but unauthenticated until the management password is supplied.");
      model.warnings.push("ACL entries remain encrypted. Their 12-byte fingerprints cannot be reversed into public keys.");
      return model;
    }
    for (const page of pages) model.acl.push(...await decryptPage(page.payload, password, candidate));
    model.authenticated = true;
    if (!model.complete) {
      model.warnings.push(`Authenticated ${pages.length} of ${first.payload[79]} pages. Paste the remaining pages to view the complete ACL.`);
    }
    if (!model.acl.length) model.warnings.push("This report contains no administrator or OTA-signer ACL entries.");
    if (candidate && !model.acl.some((entry) => entry.candidateMatch)) {
      model.warnings.push("The supplied candidate administrator key does not appear in the decoded ACL entries.");
    }
    return model;
  }

  function timestampText(epoch) {
    if (!epoch) return "unavailable";
    const date = new Date(epoch * 1000);
    return Number.isNaN(date.getTime()) ? "invalid" : `${date.toISOString().replace("T", " ").replace(".000Z", " UTC")}`;
  }

  function appendTextCell(row, tag, text) {
    const cell = document.createElement(tag);
    cell.textContent = String(text);
    row.appendChild(cell);
  }

  function initializeDecoder() {
    const root = document.querySelector("[data-management-decoder]");
    if (!root) return;
    const input = root.querySelector("[data-role='input']");
    const password = root.querySelector("[data-role='password']");
    const candidate = root.querySelector("[data-role='candidate']");
    const decode = root.querySelector("[data-role='decode']");
    const clear = root.querySelector("[data-role='clear']");
    const error = root.querySelector("[data-role='error']");
    const results = root.querySelector("[data-role='results']");
    const summary = root.querySelector("[data-role='summary']");
    const status = root.querySelector("[data-role='status']");
    const warnings = root.querySelector("[data-role='warnings']");
    const warningList = root.querySelector("[data-role='warning-list']");
    const aclTable = root.querySelector("[data-role='acl-table']");

    function showError(value) {
      results.hidden = true;
      error.textContent = value instanceof Error ? value.message : String(value);
      error.hidden = false;
    }

    function render(model) {
      const values = [
        ["Reporter", model.public.radioId], ["Sequence", model.public.sequence],
        ["Report time", timestampText(model.public.timestamp)], ["Role", model.public.role],
        ["Firmware", model.public.firmware], ["Bootloader", model.public.bootloader],
        ["EndF target", model.public.target], ["Delta base hash", model.public.baseHash],
        ["Image length", model.public.imageLength], ["Staging", model.public.staging],
        ["OTA capabilities", model.public.otaCapabilities], ["Uptime", model.public.uptime],
        ["Weekly extrema", model.public.weekly], ["Since-report extrema", model.public.sinceReport],
        ["History", model.public.history], ["Report interval", model.public.interval],
        ["Compiled capabilities", model.public.compiled], ["Known active capabilities", model.public.active],
        ["Page", model.public.page], ["ACL", model.public.acl],
      ];
      summary.replaceChildren();
      values.forEach(([term, description]) => {
        const item = document.createElement("div");
        const dt = document.createElement("dt");
        const dd = document.createElement("dd");
        dt.textContent = term;
        dd.textContent = description;
        item.append(dt, dd);
        summary.appendChild(item);
      });
      status.textContent = model.authenticated
        ? `Password-authenticated ${model.suppliedPages}/${model.pageCount} page${model.pageCount === 1 ? "" : "s"}.`
        : "Public-only decode — no authenticity claim without the management password.";
      status.classList.toggle("management-status-authenticated", model.authenticated);

      warningList.replaceChildren();
      model.warnings.forEach((note) => {
        const item = document.createElement("li");
        item.textContent = note;
        warningList.appendChild(item);
      });
      warnings.hidden = !model.warnings.length;

      aclTable.replaceChildren();
      const head = document.createElement("thead");
      const heading = document.createElement("tr");
      ["ACL index", "Fingerprint", "Permissions", "Candidate key"].forEach((text) => appendTextCell(heading, "th", text));
      head.appendChild(heading);
      aclTable.appendChild(head);
      const body = document.createElement("tbody");
      model.acl.forEach((entry) => {
        const row = document.createElement("tr");
        appendTextCell(row, "td", entry.index + 1);
        appendTextCell(row, "td", entry.fingerprint);
        appendTextCell(row, "td", [entry.administrator && "administrator", entry.otaSigner && "OTA signer"].filter(Boolean).join(", "));
        appendTextCell(row, "td", entry.candidateMatch === null ? "not checked" : entry.candidateMatch ? "matches" : "does not match");
        body.appendChild(row);
      });
      if (!model.acl.length) {
        const row = document.createElement("tr");
        const cell = document.createElement("td");
        cell.colSpan = 4;
        cell.textContent = model.authenticated ? "No ACL entries in the supplied page(s)." : "Enter the password to decrypt ACL entries.";
        row.appendChild(cell);
        body.appendChild(row);
      }
      aclTable.appendChild(body);
      results.hidden = false;
    }

    async function decodeInput() {
      error.hidden = true;
      decode.disabled = true;
      try {
        render(await decodeManagement(input.value, password.value, candidate.value.trim()));
      } catch (failure) {
        showError(failure);
      } finally {
        decode.disabled = false;
      }
    }

    decode.addEventListener("click", decodeInput);
    clear.addEventListener("click", () => {
      input.value = "";
      password.value = "";
      candidate.value = "";
      results.hidden = true;
      error.hidden = true;
      input.focus();
    });
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        decodeInput();
      }
    });
    root.querySelector("[data-role='example']").addEventListener("click", () => {
      input.value = EXAMPLE_PAGE;
      password.value = EXAMPLE_PASSWORD;
      candidate.value = "";
      decodeInput();
    });
  }

  const api = Object.freeze({
    EXAMPLE_PAGE,
    EXAMPLE_PASSWORD,
    ManagementDecodeError,
    aesBlockEncryptor,
    s2v,
    openSiv,
    passwordKey,
    deriveKey,
    aclFingerprint,
    parseInput,
    decodeManagement,
  });
  global.MeshCoreManagementDecoder = api;
  if (typeof module === "object" && module.exports) module.exports = api;

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", initializeDecoder, { once: true });
    } else {
      initializeDecoder();
    }
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
