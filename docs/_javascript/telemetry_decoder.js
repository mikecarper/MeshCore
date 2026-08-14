(function (global) {
  "use strict";

  const TYPE_TEMPERATURE = 0x11;
  const TYPE_VOLTAGE = 0x12;
  const TYPE_GPS = 0x13;
  const BINARY_TEMPERATURE_MAGIC = "TTB1";
  const BINARY_VOLTAGE_MAGIC = "TVB1";
  const BINARY_HEADER_SIZE = 19;
  const BINARY_MAX_SAMPLES = 165;
  const PAYLOAD_TYPE_RAW_CUSTOM = 0x0f;
  const METERS_PER_DEGREE = 111320;
  const DEGREES_TO_RADIANS = Math.PI / 180;

  const EXAMPLES = Object.freeze({
    packetTemperature: Object.freeze({
      label: "Analyzer temperature packet",
      command: "send telemetry.tx now",
      reply: "3E00545442311122334455667788800092651E0008000102354A4E5082",
    }),
    packetVoltage: Object.freeze({
      label: "Analyzer voltage packet",
      command: "send telemetry.tx now",
      reply: "3E00545642311122334455667788800092651E000800010264C8FEFFDC",
    }),
    temperature: Object.freeze({
      label: "Temperature example",
      command: "get telemetry.temp",
      reply:
        "> EUDUcWoeMAVZVVVVUVVVVVXVVQACTJlSwEyZMmTJkuXKkyZEeNFARIcOFChQoUOHDiRY0aPI/ypUuXMmTA==",
    }),
    voltage: Object.freeze({
      label: "Voltage example",
      command: "get telemetry.volt",
      reply:
        "> EkDUcWoeMAAB5+bl5eTj4uLh4ODf3t7d3Nvb2tnZ2NfX1tXU1NPS0tHQ0M/Ozc3My8vKycnI/w==",
    }),
    gps: Object.freeze({
      label: "GPS example",
      command: "get telemetry.gps",
      reply:
        "> EwB9cmoeGIChAxwAR0i3AgAAAAAAAAAAAAAAAAKAAAAAAIAAAAD/9ABAAX/+AAgAYAAAB//wAD/+gAAAA/+wAP/8ABwAEAEABgAAAA//gAX/7AAv/3/8AAf/oAF//QAYACAAgAc=",
    }),
  });

  class TelemetryDecodeError extends Error {
    constructor(message) {
      super(message);
      this.name = "TelemetryDecodeError";
    }
  }

  function extractBase64(input) {
    if (typeof input !== "string" || input.trim() === "") {
      throw new TelemetryDecodeError("Paste a telemetry reply or Base64 payload first.");
    }

    let text = input.trim();
    if (text.length > 4096) {
      throw new TelemetryDecodeError("The pasted value is too large to be a telemetry page.");
    }

    const replyLine = text
      .split(/\r?\n/)
      .map((line) => line.trim())
      .find((line) => /^>\s*[A-Za-z0-9+/]+={0,2}$/.test(line));

    if (replyLine) {
      text = replyLine.replace(/^>\s*/, "");
    } else {
      text = text
        .replace(/^```(?:text)?\s*/i, "")
        .replace(/```\s*$/, "")
        .trim()
        .replace(/^>\s*/, "")
        .replace(/\s+/g, "");
    }

    if (text === "") {
      throw new TelemetryDecodeError("The telemetry reply does not contain Base64 data.");
    }
    if (text.length % 4 !== 0) {
      throw new TelemetryDecodeError("Base64 length is invalid; copy the complete padded reply.");
    }
    const validBase64 = /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/;
    if (!validBase64.test(text)) {
      throw new TelemetryDecodeError("The reply is not valid standard padded Base64.");
    }
    return text;
  }

  function base64ToBytes(encoded) {
    let binary;
    try {
      if (typeof global.atob === "function") {
        binary = global.atob(encoded);
      } else if (typeof Buffer !== "undefined") {
        return Uint8Array.from(Buffer.from(encoded, "base64"));
      } else {
        throw new Error("No Base64 decoder is available");
      }
    } catch (_error) {
      throw new TelemetryDecodeError("The Base64 payload could not be decoded.");
    }

    const bytes = new Uint8Array(binary.length);
    for (let index = 0; index < binary.length; index += 1) {
      bytes[index] = binary.charCodeAt(index);
    }
    return bytes;
  }

  function bytesToHex(bytes) {
    return Array.from(bytes, (value) => value.toString(16).padStart(2, "0"))
      .join("")
      .toUpperCase();
  }

  function asciiAt(bytes, offset, length) {
    let value = "";
    for (let index = 0; index < length; index += 1) {
      value += String.fromCharCode(bytes[offset + index]);
    }
    return value;
  }

  function extractHexBytes(input) {
    if (typeof input !== "string" || input.trim() === "") {
      throw new TelemetryDecodeError("Paste raw packet hex from the analyzer first.");
    }
    if (input.length > 32768) {
      throw new TelemetryDecodeError("The pasted value is too large to be a MeshCore packet.");
    }

    const text = input
      .trim()
      .replace(/^```(?:text|json)?\s*/i, "")
      .replace(/```\s*$/, "");
    const candidates = [text];
    text.split(/\r?\n/).forEach((line) => {
      candidates.push(line);
      const separator = line.search(/[:=]/);
      if (separator >= 0) candidates.push(line.slice(separator + 1));
    });
    const streams = text.match(
      /(?:0x)?[0-9a-f]{2}(?:(?:[\s:,_-]*)(?:0x)?[0-9a-f]{2}){18,}/gi
    );
    if (streams) candidates.push(...streams);

    let firstValid = null;
    for (const candidate of candidates) {
      const normalized = candidate
        .trim()
        .replace(/^['"]|['",;]$/g, "")
        .replace(/0x/gi, "")
        .replace(/[\s:,_-]/g, "");
      if (normalized.length < BINARY_HEADER_SIZE * 2
          || normalized.length % 2 !== 0
          || !/^[0-9a-f]+$/i.test(normalized)) {
        continue;
      }
      const bytes = new Uint8Array(normalized.length / 2);
      for (let index = 0; index < bytes.length; index += 1) {
        bytes[index] = Number.parseInt(normalized.slice(index * 2, index * 2 + 2), 16);
      }
      if (firstValid === null) firstValid = bytes;
      for (let offset = 0; offset <= bytes.length - 4; offset += 1) {
        const magic = asciiAt(bytes, offset, 4);
        if (magic === BINARY_TEMPERATURE_MAGIC || magic === BINARY_VOLTAGE_MAGIC) {
          return bytes;
        }
      }
    }

    if (firstValid) {
      throw new TelemetryDecodeError(
        "Hex was found, but it does not contain TTB1 temperature or TVB1 voltage telemetry."
      );
    }
    throw new TelemetryDecodeError(
      "Raw packet data must contain complete two-character hexadecimal bytes."
    );
  }

  function uint32LE(bytes, offset) {
    return (
      bytes[offset] |
      (bytes[offset + 1] << 8) |
      (bytes[offset + 2] << 16) |
      (bytes[offset + 3] << 24)
    ) >>> 0;
  }

  function int32LE(bytes, offset) {
    const value = uint32LE(bytes, offset);
    return value > 0x7fffffff ? value - 0x100000000 : value;
  }

  function readPackedBits(bytes, byteOffset, bitOffset, width) {
    let value = 0;
    const firstBit = byteOffset * 8 + bitOffset;
    for (let bit = 0; bit < width; bit += 1) {
      const absoluteBit = firstBit + bit;
      value =
        (value << 1) |
        ((bytes[Math.floor(absoluteBit / 8)] >> (7 - (absoluteBit % 8))) & 1);
    }
    return value;
  }

  function signed14(value) {
    return value & 0x2000 ? value - 0x4000 : value;
  }

  function decodeHeader(bytes) {
    if (bytes.length < 7) {
      throw new TelemetryDecodeError("Telemetry payload is shorter than its seven-byte header.");
    }
    const header = {
      typeCode: bytes[0],
      firstEpoch: uint32LE(bytes, 1),
      intervalMinutes: bytes[5],
      count: bytes[6],
      byteLength: bytes.length,
    };
    if (header.intervalMinutes === 0) {
      throw new TelemetryDecodeError("Telemetry sample interval cannot be zero.");
    }
    if (header.count === 0) {
      throw new TelemetryDecodeError("Telemetry sample count cannot be zero.");
    }
    return header;
  }

  function assertLength(bytes, expected, label) {
    if (bytes.length !== expected) {
      throw new TelemetryDecodeError(
        `${label} payload should be ${expected} bytes for this sample count; received ${bytes.length}.`
      );
    }
  }

  function sampleEpoch(header, index) {
    return header.firstEpoch + index * header.intervalMinutes * 60;
  }

  function decodePacketEnvelope(bytes, payloadOffset) {
    if (bytes.length < 2) return null;
    const headerByte = bytes[0];
    const routeCode = headerByte & 0x03;
    const payloadType = (headerByte >> 2) & 0x0f;
    const payloadVersion = (headerByte >> 6) + 1;
    let pathLengthOffset = 1;
    if (routeCode === 0 || routeCode === 3) pathLengthOffset += 4;
    if (pathLengthOffset >= bytes.length) return null;
    const pathMetadata = bytes[pathLengthOffset];
    const hashSizeCode = pathMetadata >> 6;
    if (hashSizeCode === 3) return null;
    const hopCount = pathMetadata & 0x3f;
    const pathHashBytes = hashSizeCode + 1;
    const expectedPayloadOffset = pathLengthOffset + 1 + hopCount * pathHashBytes;
    if (expectedPayloadOffset !== payloadOffset || expectedPayloadOffset > bytes.length) return null;

    const routeNames = ["transport flood", "flood", "direct", "transport direct"];
    return {
      headerByte,
      routeCode,
      routeName: routeNames[routeCode],
      payloadType,
      payloadVersion,
      hopCount,
      pathHashBytes,
      payloadOffset,
      isRawCustom: payloadType === PAYLOAD_TYPE_RAW_CUSTOM,
    };
  }

  function decodeBinarySnapshot(bytes, offset) {
    if (bytes.length - offset < BINARY_HEADER_SIZE) {
      throw new TelemetryDecodeError("The binary telemetry header is incomplete.");
    }
    const magic = asciiAt(bytes, offset, 4);
    const count = bytes[offset + 18];
    const intervalMinutes = bytes[offset + 16] | (bytes[offset + 17] << 8);
    if (count < 1 || count > BINARY_MAX_SAMPLES) {
      throw new TelemetryDecodeError(
        `Binary telemetry sample count ${count} is outside the supported 1-${BINARY_MAX_SAMPLES} range.`
      );
    }
    if (intervalMinutes === 0) {
      throw new TelemetryDecodeError("Binary telemetry sample interval cannot be zero.");
    }
    const payloadLength = BINARY_HEADER_SIZE + count;
    if (bytes.length - offset < payloadLength) {
      throw new TelemetryDecodeError(
        `Binary telemetry declares ${count} samples but ${payloadLength - (bytes.length - offset)} payload bytes are missing.`
      );
    }

    const header = {
      typeCode: magic,
      firstEpoch: uint32LE(bytes, offset + 12),
      intervalMinutes,
      count,
      byteLength: payloadLength,
      sourceId: bytesToHex(bytes.slice(offset + 4, offset + 12)),
      format: "binary",
      payloadOffset: offset,
      inputByteLength: bytes.length,
      trailingBytes: bytes.length - offset - payloadLength,
      packet: decodePacketEnvelope(bytes, offset),
    };
    const rows = [];
    for (let index = 0; index < count; index += 1) {
      const rawCode = bytes[offset + BINARY_HEADER_SIZE + index];
      if (magic === BINARY_TEMPERATURE_MAGIC) {
        let status = "Value";
        let valueC = null;
        if (rawCode === 0) status = "Missing";
        else if (rawCode === 1) status = "Below range";
        else if (rawCode === 2) status = "Above range";
        else if (rawCode <= 130) valueC = rawCode - 53;
        else status = "Reserved code";
        rows.push({
          index,
          epoch: sampleEpoch(header, index),
          status,
          statusCode: null,
          rawCode,
          valueC,
        });
      } else {
        let status = "Value";
        let millivolts = null;
        if (rawCode === 0) status = "Missing";
        else if (rawCode === 1) status = "Below range";
        else if (rawCode === 255) status = "Above range";
        else millivolts = 1880 + (rawCode - 2) * 10;
        rows.push({
          index,
          epoch: sampleEpoch(header, index),
          status,
          rawCode,
          millivolts,
        });
      }
    }
    const warnings = [];
    if (header.packet && !header.packet.isRawCustom) {
      warnings.push(
        `The enclosing packet type is 0x${header.packet.payloadType.toString(16)}, not RAW_CUSTOM (0x0f).`
      );
    }
    if (header.trailingBytes > 0) {
      warnings.push(`${header.trailingBytes} byte(s) after the declared telemetry payload were ignored.`);
    }
    if (magic === BINARY_TEMPERATURE_MAGIC
        && rows.some((row) => row.status === "Reserved code")) {
      warnings.push("One or more temperature samples use a reserved code and may be corrupt.");
    }
    return {
      ...header,
      kind: magic === BINARY_TEMPERATURE_MAGIC ? "temperature" : "voltage",
      label: magic === BINARY_TEMPERATURE_MAGIC ? "Temperature" : "Battery voltage",
      rows,
      warnings,
    };
  }

  function decodeRawTelemetryHex(input) {
    const bytes = extractHexBytes(input);
    const decodedCandidates = [];
    let lastError = null;
    for (let offset = 0; offset <= bytes.length - 4; offset += 1) {
      const magic = asciiAt(bytes, offset, 4);
      if (magic !== BINARY_TEMPERATURE_MAGIC && magic !== BINARY_VOLTAGE_MAGIC) continue;
      try {
        decodedCandidates.push(decodeBinarySnapshot(bytes, offset));
      } catch (error) {
        lastError = error;
      }
    }
    if (decodedCandidates.length === 0) {
      if (lastError) throw lastError;
      throw new TelemetryDecodeError("No supported telemetry snapshot was found in the hex data.");
    }
    if (decodedCandidates.length > 1) {
      throw new TelemetryDecodeError(
        "More than one telemetry snapshot was found; paste one analyzer packet at a time."
      );
    }
    const decoded = decodedCandidates[0];
    decoded.encoded = bytesToHex(bytes);
    return decoded;
  }

  function decodeTemperature(bytes, header) {
    const statusBytes = Math.ceil((header.count * 2) / 8);
    const valueBytes = Math.ceil((header.count * 7) / 8);
    assertLength(bytes, 7 + statusBytes + valueBytes, "Temperature");

    const rows = [];
    for (let index = 0; index < header.count; index += 1) {
      const statusCode = readPackedBits(bytes, 7, index * 2, 2);
      const rawCode = readPackedBits(bytes, 7 + statusBytes, index * 7, 7);
      let status;
      let valueC = null;
      if (statusCode === 0) {
        status = "Missing";
      } else if (statusCode === 1) {
        status = "Value";
        valueC = rawCode - 50;
      } else if (statusCode === 2) {
        status = "Below range";
      } else {
        status = "Above range";
      }
      rows.push({
        index,
        epoch: sampleEpoch(header, index),
        status,
        statusCode,
        rawCode,
        valueC,
      });
    }

    return {
      ...header,
      kind: "temperature",
      label: "Temperature",
      rows,
      warnings: [],
    };
  }

  function decodeVoltage(bytes, header) {
    assertLength(bytes, 7 + header.count, "Voltage");

    const rows = [];
    for (let index = 0; index < header.count; index += 1) {
      const rawCode = bytes[7 + index];
      let status;
      let millivolts = null;
      if (rawCode === 0) {
        status = "Missing";
      } else if (rawCode === 1) {
        status = "Below range";
      } else if (rawCode === 255) {
        status = "Above range";
      } else {
        status = "Value";
        millivolts = 1880 + (rawCode - 2) * 10;
      }
      rows.push({
        index,
        epoch: sampleEpoch(header, index),
        status,
        rawCode,
        millivolts,
      });
    }

    return {
      ...header,
      kind: "voltage",
      label: "Battery voltage",
      rows,
      warnings: [],
    };
  }

  function decodeGps(bytes, header) {
    if (bytes.length < 17) {
      throw new TelemetryDecodeError("GPS payload is shorter than its 17-byte header.");
    }
    const dataBytes = Math.ceil((header.count * 28) / 8);
    assertLength(bytes, 17 + dataBytes, "GPS");

    const originLatE7 = int32LE(bytes, 7);
    const originLonE7 = int32LE(bytes, 11);
    const originIndex = bytes[15];
    const flags = bytes[16];
    if (originIndex !== 0xff && originIndex >= header.count) {
      throw new TelemetryDecodeError(
        `GPS origin index ${originIndex} is outside the ${header.count}-sample page.`
      );
    }

    const warnings = [];
    if (flags & 0x01) {
      warnings.push(
        "At least one GPS differential was clipped to the signed 14-bit range; positions after it may be less accurate."
      );
    }
    if (flags & 0xfe) {
      warnings.push(`GPS flags contain unknown bits: 0x${flags.toString(16).padStart(2, "0")}.`);
    }
    if (originIndex !== 0xff) {
      warnings.push(
        "A 0 m / 0 m GPS differential after the origin is ambiguous: it can mean an unchanged fix, movement under the 10 m resolution, or no fix."
      );
    }
    if (
      originIndex !== 0xff &&
      (originLatE7 < -900000000 ||
        originLatE7 > 900000000 ||
        originLonE7 < -1800000000 ||
        originLonE7 > 1800000000)
    ) {
      warnings.push("The GPS origin is outside the valid latitude/longitude range.");
    }

    let referenceLat = originLatE7 / 10000000;
    let referenceLon = originLonE7 / 10000000;
    let unexpectedPreOriginDelta = false;
    const rows = [];

    for (let index = 0; index < header.count; index += 1) {
      const bitOffset = index * 28;
      const northUnits = signed14(readPackedBits(bytes, 17, bitOffset, 14));
      const eastUnits = signed14(readPackedBits(bytes, 17, bitOffset + 14, 14));
      const northMeters = northUnits * 10;
      const eastMeters = eastUnits * 10;
      let latitude = null;
      let longitude = null;
      let status;
      let ambiguous = false;
      let possiblyClipped = false;

      if (originIndex === 0xff) {
        status = "No fix";
        if (northUnits !== 0 || eastUnits !== 0) unexpectedPreOriginDelta = true;
      } else if (index < originIndex) {
        status = "No fix";
        if (northUnits !== 0 || eastUnits !== 0) unexpectedPreOriginDelta = true;
      } else if (index === originIndex) {
        status = "Origin fix";
        latitude = referenceLat;
        longitude = referenceLon;
        if (northUnits !== 0 || eastUnits !== 0) {
          warnings.push("The GPS origin row contains a non-zero differential.");
        }
      } else if (northUnits === 0 && eastUnits === 0) {
        status = "Unchanged / no fix";
        ambiguous = true;
      } else {
        referenceLat += northMeters / METERS_PER_DEGREE;
        const longitudeScale =
          METERS_PER_DEGREE * Math.cos(referenceLat * DEGREES_TO_RADIANS);
        if (Math.abs(longitudeScale) > 0.001) {
          referenceLon += eastMeters / longitudeScale;
        }
        latitude = referenceLat;
        longitude = referenceLon;
        possiblyClipped =
          Boolean(flags & 0x01) &&
          [northUnits, eastUnits].some((value) => value === -8192 || value === 8191);
        status = possiblyClipped ? "Fix (clipped delta)" : "Fix";
      }

      rows.push({
        index,
        epoch: sampleEpoch(header, index),
        status,
        latitude,
        longitude,
        northMeters,
        eastMeters,
        ambiguous,
        possiblyClipped,
      });
    }

    if (unexpectedPreOriginDelta) {
      warnings.push("A no-fix row contains a non-zero GPS differential.");
    }

    return {
      ...header,
      kind: "gps",
      label: "GPS",
      originLatE7,
      originLonE7,
      originIndex,
      flags,
      rows,
      warnings,
    };
  }

  function decodeTelemetry(input) {
    const compactHex = typeof input === "string"
      ? input.replace(/^```(?:text|json)?\s*/i, "")
        .replace(/```\s*$/, "")
        .replace(/0x/gi, "")
        .replace(/[\s:,_-]/g, "")
        .replace(/^['"]|['",;]$/g, "")
      : "";
    if ((/^[0-9a-f]+$/i.test(compactHex) && compactHex.length % 2 === 0)
        || /54544231|54564231/i.test(String(input))) {
      return decodeRawTelemetryHex(input);
    }
    const encoded = extractBase64(input);
    const bytes = base64ToBytes(encoded);
    if (bytes.length === 0) {
      throw new TelemetryDecodeError("The Base64 payload decoded to zero bytes.");
    }

    const header = decodeHeader(bytes);
    let decoded;
    if (header.typeCode === TYPE_TEMPERATURE) {
      decoded = decodeTemperature(bytes, header);
    } else if (header.typeCode === TYPE_VOLTAGE) {
      decoded = decodeVoltage(bytes, header);
    } else if (header.typeCode === TYPE_GPS) {
      decoded = decodeGps(bytes, header);
    } else {
      throw new TelemetryDecodeError(
        `Unsupported telemetry type 0x${header.typeCode.toString(16).padStart(2, "0")}; expected 0x11, 0x12, or 0x13.`
      );
    }
    decoded.encoded = encoded;
    return decoded;
  }

  function timestampText(epoch, localTime) {
    const date = new Date(epoch * 1000);
    if (!Number.isFinite(date.getTime())) return "Invalid timestamp";
    if (!localTime) return date.toISOString().replace(".000Z", "Z");
    return new Intl.DateTimeFormat(undefined, {
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
      timeZoneName: "short",
    }).format(date);
  }

  function tableModel(decoded, localTime) {
    const localZone =
      (Intl.DateTimeFormat().resolvedOptions() || {}).timeZone || "browser local";
    const timestampHeader = localTime ? `Timestamp (${localZone})` : "Timestamp (UTC)";

    if (decoded.kind === "temperature") {
      return {
        headers: [
          "#",
          timestampHeader,
          "Status",
          "Temperature",
          decoded.format === "binary" ? "Raw code" : "Raw status/code",
        ],
        rows: decoded.rows.map((row) => ({
          muted: row.status === "Missing",
          values: [
            row.index + 1,
            timestampText(row.epoch, localTime),
            row.status,
            row.status === "Below range"
              ? "< -50 °C"
              : row.status === "Above range"
                ? "> 77 °C"
                : row.valueC === null
                  ? "—"
                  : `${row.valueC} °C`,
            row.statusCode === null ? row.rawCode : `${row.statusCode} / ${row.rawCode}`,
          ],
        })),
      };
    }

    if (decoded.kind === "voltage") {
      return {
        headers: ["#", timestampHeader, "Status", "Battery voltage", "Raw code"],
        rows: decoded.rows.map((row) => ({
          muted: row.status === "Missing",
          values: [
            row.index + 1,
            timestampText(row.epoch, localTime),
            row.status,
            row.status === "Below range"
              ? "< 1.88 V"
              : row.status === "Above range"
                ? "> 4.40 V"
                : row.millivolts === null
                  ? "—"
                  : `${(row.millivolts / 1000).toFixed(2)} V`,
            row.rawCode,
          ],
        })),
      };
    }

    return {
      headers: [
        "#",
        timestampHeader,
        "Status",
        "Latitude",
        "Longitude",
        "North Δ",
        "East Δ",
      ],
      rows: decoded.rows.map((row) => ({
        muted: row.status === "No fix" || row.ambiguous,
        values: [
          row.index + 1,
          timestampText(row.epoch, localTime),
          row.status,
          row.latitude === null ? "—" : row.latitude.toFixed(7),
          row.longitude === null ? "—" : row.longitude.toFixed(7),
          `${row.northMeters} m`,
          `${row.eastMeters} m`,
        ],
      })),
    };
  }

  function summaryItems(decoded, localTime) {
    const lastEpoch = sampleEpoch(decoded, decoded.count - 1);
    const items = [
      [
        "Payload",
        decoded.format === "binary"
          ? `${decoded.label} (${decoded.typeCode})`
          : `${decoded.label} (0x${decoded.typeCode.toString(16)})`,
      ],
      ["Samples", String(decoded.count)],
      ["Interval", `${decoded.intervalMinutes} minutes`],
      ["First sample", timestampText(decoded.firstEpoch, localTime)],
      ["Last sample", timestampText(lastEpoch, localTime)],
      ["Decoded size", `${decoded.byteLength} bytes`],
    ];
    if (decoded.format === "binary") {
      items.splice(1, 0, ["Repeater ID", decoded.sourceId]);
      items.push([
        "Input",
        decoded.packet
          ? `MeshCore ${decoded.packet.routeName}, ${decoded.packet.hopCount} path hops`
          : decoded.payloadOffset === 0
            ? "Telemetry payload hex"
            : `Embedded payload at byte ${decoded.payloadOffset}`,
      ]);
      if (decoded.packet) {
        items.push([
          "Packet header",
          `0x${decoded.packet.headerByte.toString(16).padStart(2, "0")} / payload v${decoded.packet.payloadVersion}`,
        ]);
      }
    }
    if (decoded.kind === "gps") {
      items.push([
        "Origin",
        decoded.originIndex === 0xff
          ? "No fix on page"
          : `row ${decoded.originIndex + 1}: ${(decoded.originLatE7 / 1e7).toFixed(7)}, ${(decoded.originLonE7 / 1e7).toFixed(7)}`,
      ]);
      items.push(["GPS flags", `0x${decoded.flags.toString(16).padStart(2, "0")}`]);
    }
    return items;
  }

  function csvCell(value) {
    const text = String(value);
    return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
  }

  function modelToCsv(model) {
    return [model.headers, ...model.rows.map((row) => row.values)]
      .map((row) => row.map(csvCell).join(","))
      .join("\r\n");
  }

  function appendCell(row, tagName, value) {
    const cell = document.createElement(tagName);
    cell.textContent = String(value);
    row.appendChild(cell);
  }

  function initializeDecoder() {
    const root = document.querySelector("[data-telemetry-decoder]");
    if (!root) return;

    const input = root.querySelector("[data-role='input']");
    const decodeButton = root.querySelector("[data-role='decode']");
    const clearButton = root.querySelector("[data-role='clear']");
    const downloadButton = root.querySelector("[data-role='download']");
    const localTime = root.querySelector("[data-role='local-time']");
    const errorBox = root.querySelector("[data-role='error']");
    const results = root.querySelector("[data-role='results']");
    const resultTitle = root.querySelector("[data-role='result-title']");
    const summary = root.querySelector("[data-role='summary']");
    const warningBox = root.querySelector("[data-role='warnings']");
    const warningList = root.querySelector("[data-role='warning-list']");
    const table = root.querySelector("[data-role='table']");
    let current = null;

    root.querySelectorAll("[data-example-reply]").forEach((element) => {
      const example = EXAMPLES[element.getAttribute("data-example-reply")];
      if (example) element.textContent = example.reply;
    });

    function hideError() {
      errorBox.hidden = true;
      errorBox.textContent = "";
    }

    function showError(error) {
      current = null;
      results.hidden = true;
      errorBox.textContent = error instanceof Error ? error.message : String(error);
      errorBox.hidden = false;
    }

    function render() {
      if (!current) return;
      const useLocalTime = localTime.checked;
      const model = tableModel(current, useLocalTime);
      resultTitle.textContent = `${current.label} telemetry`;

      summary.replaceChildren();
      summaryItems(current, useLocalTime).forEach(([term, description]) => {
        const item = document.createElement("div");
        const dt = document.createElement("dt");
        const dd = document.createElement("dd");
        dt.textContent = term;
        dd.textContent = description;
        item.append(dt, dd);
        summary.appendChild(item);
      });

      warningList.replaceChildren();
      current.warnings.forEach((warning) => {
        const item = document.createElement("li");
        item.textContent = warning;
        warningList.appendChild(item);
      });
      warningBox.hidden = current.warnings.length === 0;

      table.replaceChildren();
      const head = document.createElement("thead");
      const headRow = document.createElement("tr");
      model.headers.forEach((header) => appendCell(headRow, "th", header));
      head.appendChild(headRow);
      table.appendChild(head);

      const body = document.createElement("tbody");
      model.rows.forEach((modelRow) => {
        const row = document.createElement("tr");
        if (modelRow.muted) row.classList.add("telemetry-row-muted");
        modelRow.values.forEach((value) => appendCell(row, "td", value));
        body.appendChild(row);
      });
      table.appendChild(body);
      results.hidden = false;
    }

    function decodeInput() {
      hideError();
      try {
        current = decodeTelemetry(input.value);
        render();
      } catch (error) {
        showError(error);
      }
    }

    decodeButton.addEventListener("click", decodeInput);
    clearButton.addEventListener("click", () => {
      input.value = "";
      current = null;
      results.hidden = true;
      hideError();
      input.focus();
    });
    localTime.addEventListener("change", render);
    input.addEventListener("keydown", (event) => {
      if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
        event.preventDefault();
        decodeInput();
      }
    });

    root.querySelectorAll("[data-telemetry-example]").forEach((button) => {
      button.addEventListener("click", () => {
        const example = EXAMPLES[button.getAttribute("data-telemetry-example")];
        if (!example) return;
        input.value = example.reply;
        decodeInput();
      });
    });

    downloadButton.addEventListener("click", () => {
      if (!current) return;
      const csv = modelToCsv(tableModel(current, localTime.checked));
      const blob = new Blob([csv], { type: "text/csv;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      const sourcePart = current.sourceId ? `${current.sourceId.toLowerCase()}-` : "";
      link.download = `meshcore-telemetry-${sourcePart}${current.kind}.csv`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      URL.revokeObjectURL(url);
    });

    const requestedExample = new URLSearchParams(global.location.search).get("example");
    if (requestedExample && EXAMPLES[requestedExample]) {
      input.value = EXAMPLES[requestedExample].reply;
      decodeInput();
    }
  }

  const api = Object.freeze({
    EXAMPLES,
    TelemetryDecodeError,
    decodeTelemetry,
    decodeRawTelemetryHex,
    extractHexBytes,
    extractBase64,
    tableModel,
    modelToCsv,
  });

  global.MeshCoreTelemetryDecoder = api;
  if (typeof module === "object" && module.exports) module.exports = api;

  if (typeof document !== "undefined") {
    if (document.readyState === "loading") {
      document.addEventListener("DOMContentLoaded", initializeDecoder, { once: true });
    } else {
      initializeDecoder();
    }
  }
})(typeof globalThis !== "undefined" ? globalThis : this);
