#include <Arduino.h>
#include <SD.h>
#include <SPI.h>

#include "../../../src/helpers/IndicatorFontStageV2Protocol.h"

namespace {

constexpr uint32_t FONT_UART_BAUD = 1000000;
constexpr uint32_t SD_CLOCK_HZ = 1000000;
constexpr uint32_t SD_MOUNT_RETRY_INTERVAL_MS = 2000;
constexpr uint8_t SD_MOUNT_MAX_ATTEMPTS = 30;
constexpr uint32_t RECEIVE_IDLE_TIMEOUT_MS = 10000;
constexpr uint32_t RECEIVE_TOTAL_TIMEOUT_MS = 180000;
constexpr size_t MAX_FONT_BYTES = 1536 * 1024;

constexpr int FONT_UART_TX = 16;
constexpr int FONT_UART_RX = 17;
constexpr int SD_SCK = 10;
constexpr int SD_MOSI = 11;
constexpr int SD_MISO = 12;
constexpr int SD_CS = 13;

constexpr const char* FONT_DIRECTORY = "/meshcore";
constexpr const char* FONT_PATH = "/meshcore/ui-font.vlw";
constexpr const char* FONT_META_PATH = "/meshcore/ui-font.meta";
constexpr const char* TEMP_FONT_PATH = "/meshcore/ui-font.tmp";
constexpr const char* TEMP_META_PATH = "/meshcore/ui-font-meta.tmp";
constexpr const char* BACKUP_FONT_PATH = "/meshcore/ui-font.bak";
constexpr const char* BACKUP_META_PATH = "/meshcore/ui-font-meta.bak";
// Wi-Fi recovery stages into separate paths.  Unlike TEMP_*, these are never
// promoted during boot recovery: the ESP32 must first verify the downloaded
// SHA-256 and explicitly issue MCFONT COMMIT.
constexpr const char* STAGED_FONT_PATH = "/meshcore/ui-font.stage";
constexpr const char* STAGED_META_PATH = "/meshcore/ui-font-meta.stage";

bool sdReady = false;
uint8_t sdMountAttempts = 0;
uint32_t sdMountNextAttempt = 0;
size_t fontSize = 0;
uint32_t fontCrc = 0;
uint32_t espInfoRequests = 0;
uint32_t espGetAttempts = 0;
uint32_t espGetCompleted = 0;
size_t espLastBytes = 0;
uint32_t espStage2Attempts = 0;
uint32_t espStage2Completed = 0;
size_t espStage2LastBytes = 0;
uint32_t espStage2LastElapsedMs = 0;

enum class Stage2Result : uint8_t {
  Idle,
  Receiving,
  Staged,
  SdUnavailable,
  OpenFailed,
  Timeout,
  WriteFailed,
  ChecksumFailed,
  MetadataFailed,
  Aborted,
};

Stage2Result espStage2LastResult = Stage2Result::Idle;

const char* stage2ResultName(Stage2Result result) {
  switch (result) {
    case Stage2Result::Idle: return "IDLE";
    case Stage2Result::Receiving: return "RECEIVING";
    case Stage2Result::Staged: return "STAGED";
    case Stage2Result::SdUnavailable: return "SD";
    case Stage2Result::OpenFailed: return "OPEN";
    case Stage2Result::Timeout: return "TIMEOUT";
    case Stage2Result::WriteFailed: return "WRITE";
    case Stage2Result::ChecksumFailed: return "CHECKSUM";
    case Stage2Result::MetadataFailed: return "METADATA";
    case Stage2Result::Aborted: return "ABORTED";
  }
  return "UNKNOWN";
}

uint32_t updateCrc32(uint32_t crc, const uint8_t* data, size_t size) {
  for (size_t i = 0; i < size; ++i) {
    crc ^= data[i];
    for (uint8_t bit = 0; bit < 8; ++bit) {
      crc = (crc >> 1) ^ (0xEDB88320UL & (0U - (crc & 1U)));
    }
  }
  return crc;
}

bool readLine(File& file, char* line, size_t capacity) {
  size_t length = 0;
  while (file.available()) {
    int value = file.read();
    if (value < 0) break;
    if (value == '\n') {
      line[length] = 0;
      return true;
    }
    if (value != '\r' && length + 1 < capacity) {
      line[length++] = (char)value;
    }
  }
  line[length] = 0;
  return length != 0;
}

bool parseMetadata(const char* line, size_t& size, uint32_t& crc) {
  unsigned long parsedSize = 0;
  unsigned long parsedCrc = 0;
  if (sscanf(line, "MCFONT 1 %lu %lx", &parsedSize, &parsedCrc) != 2
      || parsedSize < 64
      || parsedSize > MAX_FONT_BYTES) {
    return false;
  }
  size = (size_t)parsedSize;
  crc = (uint32_t)parsedCrc;
  return true;
}

bool validatePair(const char* fontPath, const char* metadataPath,
                  size_t& size, uint32_t& crc) {
  File metadata = SD.open(metadataPath, FILE_READ);
  if (!metadata) return false;

  char line[64];
  bool valid = readLine(metadata, line, sizeof(line))
      && parseMetadata(line, size, crc);
  metadata.close();
  if (!valid) return false;

  File font = SD.open(fontPath, FILE_READ);
  if (!font) return false;
  valid = font.size() == size;
  font.close();
  return valid;
}

bool validatePairCrc(const char* fontPath, const char* metadataPath,
                     size_t expectedSize, uint32_t expectedCrc) {
  size_t size = 0;
  uint32_t crc = 0;
  if (!validatePair(fontPath, metadataPath, size, crc)
      || size != expectedSize || crc != expectedCrc) {
    return false;
  }

  File font = SD.open(fontPath, FILE_READ);
  if (!font) return false;
  uint8_t buffer[512];
  size_t readTotal = 0;
  uint32_t calculated = 0xFFFFFFFFUL;
  while (readTotal < expectedSize) {
    size_t wanted = expectedSize - readTotal;
    if (wanted > sizeof(buffer)) wanted = sizeof(buffer);
    size_t count = font.read(buffer, wanted);
    if (count == 0) break;
    calculated = updateCrc32(calculated, buffer, count);
    readTotal += count;
  }
  font.close();
  return readTotal == expectedSize && ~calculated == expectedCrc;
}

// Transaction recovery may need to match a font with metadata left under a
// different path by an interrupted rename.  Font releases commonly have the
// same byte length, so size alone is not enough to prove that cross-path pair.
bool validateStoredPairCrc(const char* fontPath, const char* metadataPath,
                           size_t& size, uint32_t& crc) {
  return validatePair(fontPath, metadataPath, size, crc)
      && validatePairCrc(fontPath, metadataPath, size, crc);
}

bool hasInstallTransactionFiles() {
  return SD.exists(TEMP_FONT_PATH) || SD.exists(TEMP_META_PATH)
      || SD.exists(BACKUP_FONT_PATH) || SD.exists(BACKUP_META_PATH);
}

void removeIfPresent(const char* path) {
  if (SD.exists(path)) SD.remove(path);
}

void cleanTransactionFiles() {
  removeIfPresent(TEMP_FONT_PATH);
  removeIfPresent(TEMP_META_PATH);
  removeIfPresent(BACKUP_FONT_PATH);
  removeIfPresent(BACKUP_META_PATH);
}

void cleanStagedFiles() {
  removeIfPresent(STAGED_FONT_PATH);
  removeIfPresent(STAGED_META_PATH);
}

bool promotePair(const char* sourceFont, const char* sourceMetadata) {
  removeIfPresent(FONT_PATH);
  removeIfPresent(FONT_META_PATH);
  if (!SD.rename(sourceFont, FONT_PATH)) return false;
  if (SD.rename(sourceMetadata, FONT_META_PATH)) return true;
  // Keep the already-moved font next to its source metadata. A retry or reset
  // can recognize that CRC-valid split pair and finish the metadata rename;
  // deleting FONT_PATH here would destroy the only copy of the candidate.
  return false;
}

void recoverFontTransaction() {
  size_t size;
  uint32_t crc;
  if (validatePair(FONT_PATH, FONT_META_PATH, size, crc)) {
    // Metadata and length are enough on an ordinary boot because the ESP32
    // verifies the streamed CRC. If an interrupted install left candidates,
    // however, prove the live bytes before deleting the only possible backup.
    if (!hasInstallTransactionFiles()
        || validatePairCrc(FONT_PATH, FONT_META_PATH, size, crc)) {
      cleanTransactionFiles();
      return;
    }
  }

  // A reset between the two final renames can leave the new font next to its
  // temporary metadata. Complete only a byte-for-byte CRC-valid transaction;
  // different font releases can have identical lengths.
  if (validateStoredPairCrc(FONT_PATH, TEMP_META_PATH, size, crc)) {
    removeIfPresent(FONT_META_PATH);
    if (SD.rename(TEMP_META_PATH, FONT_META_PATH)) {
      cleanTransactionFiles();
    }
    // A failed metadata rename still leaves a CRC-valid split pair for the
    // next boot. Do not fall through to destructive cleanup.
    return;
  }

  // Restoring a backup has the same two-rename reset window. In that case the
  // backup font is already live while its metadata retains the backup name.
  if (validateStoredPairCrc(FONT_PATH, BACKUP_META_PATH, size, crc)) {
    removeIfPresent(FONT_META_PATH);
    if (SD.rename(BACKUP_META_PATH, FONT_META_PATH)) {
      cleanTransactionFiles();
    }
    return;
  }

  // Moving the old live pair to backup can reset after the font rename but
  // before its metadata rename. Finish forming the CRC-valid backup before
  // trying the pending replacement, so a failed replacement can roll back.
  if (validateStoredPairCrc(BACKUP_FONT_PATH, FONT_META_PATH, size, crc)) {
    removeIfPresent(BACKUP_META_PATH);
    if (!SD.rename(FONT_META_PATH, BACKUP_META_PATH)) return;
  }

  if (validateStoredPairCrc(TEMP_FONT_PATH, TEMP_META_PATH, size, crc)) {
    if (promotePair(TEMP_FONT_PATH, TEMP_META_PATH)) {
      cleanTransactionFiles();
    }
    // A failed rename leaves either the intact temp pair or a live-font/temp-
    // metadata split pair. Preserve it for a later retry instead of replacing
    // or deleting a candidate whose full CRC has already passed.
    return;
  }

  if (validateStoredPairCrc(BACKUP_FONT_PATH, BACKUP_META_PATH, size, crc)) {
    if (promotePair(BACKUP_FONT_PATH, BACKUP_META_PATH)) {
      cleanTransactionFiles();
    }
    return;
  }

  removeIfPresent(FONT_PATH);
  removeIfPresent(FONT_META_PATH);
  cleanTransactionFiles();
}

bool refreshFontInfo() {
  fontSize = 0;
  fontCrc = 0;
  return sdReady && validatePair(FONT_PATH, FONT_META_PATH, fontSize, fontCrc);
}

bool mountSdCard() {
  if (sdReady || sdMountAttempts >= SD_MOUNT_MAX_ATTEMPTS) return sdReady;

  // A failed SDFS begin can leave partial mount state behind. Clear only the
  // filesystem wrapper before another attempt; SPI1 remains configured below.
  if (sdMountAttempts != 0) SD.end(false);
  ++sdMountAttempts;
  sdReady = SD.begin(SD_CS, SD_CLOCK_HZ, SPI1);
  if (!sdReady) {
    sdMountNextAttempt = millis() + SD_MOUNT_RETRY_INTERVAL_MS;
    if (sdMountNextAttempt == 0) sdMountNextAttempt = 1;
    return false;
  }

  sdMountNextAttempt = 0;
  if (!SD.exists(FONT_DIRECTORY)) SD.mkdir(FONT_DIRECTORY);
  recoverFontTransaction();
  // A staged Wi-Fi download has not passed the ESP32's SHA-256 gate after a
  // reset, so it is discarded rather than boot-promoted.
  cleanStagedFiles();
  refreshFontInfo();
  return true;
}

void serviceSdMount(uint32_t now) {
  if (sdReady || sdMountAttempts >= SD_MOUNT_MAX_ATTEMPTS) return;
  if (sdMountNextAttempt != 0
      && static_cast<int32_t>(now - sdMountNextAttempt) < 0) {
    return;
  }
  mountSdCard();
}

void sendInfo(Print& output, bool trackEspRequest) {
  if (trackEspRequest) ++espInfoRequests;
  if (!refreshFontInfo()) {
    output.print("MCFONT 0 0 00000000\n");
    return;
  }
  output.printf("MCFONT 1 %lu %08lx\n", (unsigned long)fontSize,
                (unsigned long)fontCrc);
}

void sendFont(Print& output, bool trackEspRequest) {
  if (trackEspRequest) {
    ++espGetAttempts;
    espLastBytes = 0;
  }
  if (!refreshFontInfo()) {
    output.print("MCFONT 0 0 00000000\n");
    return;
  }

  File font = SD.open(FONT_PATH, FILE_READ);
  if (!font) {
    output.print("MCFONT 0 0 00000000\n");
    return;
  }

  output.printf("MCFONT 1 %lu %08lx\n", (unsigned long)fontSize,
                (unsigned long)fontCrc);
  uint8_t buffer[512];
  size_t sent = 0;
  while (font.available()) {
    size_t count = font.read(buffer, sizeof(buffer));
    if (count == 0) break;
    size_t written = output.write(buffer, count);
    sent += written;
    if (written != count) break;
  }
  font.close();
  if (trackEspRequest) {
    espLastBytes = sent;
    if (sent == fontSize) ++espGetCompleted;
  }
}

bool writeMetadata(const char* path, size_t size, uint32_t crc) {
  removeIfPresent(path);
  File metadata = SD.open(path, "w");
  if (!metadata) return false;
  metadata.printf("MCFONT 1 %lu %08lx\n", (unsigned long)size,
                  (unsigned long)crc);
  metadata.flush();
  metadata.close();
  return true;
}

bool moveCurrentToBackup() {
  removeIfPresent(BACKUP_FONT_PATH);
  removeIfPresent(BACKUP_META_PATH);

  bool movedFont = false;
  if (SD.exists(FONT_PATH)) {
    if (!SD.rename(FONT_PATH, BACKUP_FONT_PATH)) return false;
    movedFont = true;
  }
  if (SD.exists(FONT_META_PATH)
      && !SD.rename(FONT_META_PATH, BACKUP_META_PATH)) {
    if (movedFont) SD.rename(BACKUP_FONT_PATH, FONT_PATH);
    return false;
  }
  return true;
}

bool installTemporaryPair(size_t expectedSize, uint32_t expectedCrc) {
  if (!moveCurrentToBackup()) {
    // The first rename pair can itself be interrupted. Repair or promote the
    // CRC-valid candidate immediately rather than leaving split paths until a
    // reboot, then report success only if the requested asset became live.
    recoverFontTransaction();
    return refreshFontInfo() && fontSize == expectedSize && fontCrc == expectedCrc;
  }
  if (!SD.rename(TEMP_FONT_PATH, FONT_PATH)) {
    recoverFontTransaction();
    return refreshFontInfo() && fontSize == expectedSize && fontCrc == expectedCrc;
  }
  if (!SD.rename(TEMP_META_PATH, FONT_META_PATH)) {
    recoverFontTransaction();
    return refreshFontInfo() && fontSize == expectedSize && fontCrc == expectedCrc;
  }
  cleanTransactionFiles();
  return refreshFontInfo() && fontSize == expectedSize && fontCrc == expectedCrc;
}

bool installStagedPair(size_t expectedSize, uint32_t expectedCrc) {
  // Re-read the staged file before touching the live pair.  The ESP32 has
  // already checked SHA-256; this verifies the bytes still on the SD card.
  if (!validatePairCrc(STAGED_FONT_PATH, STAGED_META_PATH,
                       expectedSize, expectedCrc)) {
    cleanStagedFiles();
    return false;
  }

  removeIfPresent(TEMP_FONT_PATH);
  removeIfPresent(TEMP_META_PATH);
  if (!SD.rename(STAGED_FONT_PATH, TEMP_FONT_PATH)) {
    cleanStagedFiles();
    return false;
  }
  if (!SD.rename(STAGED_META_PATH, TEMP_META_PATH)) {
    removeIfPresent(TEMP_FONT_PATH);
    cleanStagedFiles();
    return false;
  }
  return installTemporaryPair(expectedSize, expectedCrc);
}

enum class ReceiveMode : uint8_t {
  InstallNow,
  StageOnly,
};

void receiveFont(Stream& input, Print& output, size_t expectedSize,
                 uint32_t expectedCrc, ReceiveMode mode) {
  if (!sdReady) {
    output.print("ERROR SD\n");
    return;
  }

  const char* destinationFont = mode == ReceiveMode::StageOnly
      ? STAGED_FONT_PATH : TEMP_FONT_PATH;
  const char* destinationMetadata = mode == ReceiveMode::StageOnly
      ? STAGED_META_PATH : TEMP_META_PATH;
  removeIfPresent(destinationFont);
  removeIfPresent(destinationMetadata);
  File font = SD.open(destinationFont, "w");
  if (!font) {
    output.print("ERROR OPEN\n");
    return;
  }

  output.print("READY\n");
  uint8_t buffer[512];
  size_t received = 0;
  uint32_t crc = 0xFFFFFFFFUL;
  const uint32_t started = millis();
  uint32_t lastProgress = started;
  bool writeFailed = false;
  bool timedOut = false;
  while (received < expectedSize) {
    const uint32_t now = millis();
    if (now - started >= RECEIVE_TOTAL_TIMEOUT_MS
        || now - lastProgress >= RECEIVE_IDLE_TIMEOUT_MS) {
      timedOut = true;
      break;
    }
    int available = input.available();
    if (available <= 0) {
      delay(1);
      continue;
    }
    size_t count = (size_t)available;
    if (count > sizeof(buffer)) count = sizeof(buffer);
    if (count > expectedSize - received) count = expectedSize - received;
    size_t actual = input.readBytes(buffer, count);
    if (actual == 0) continue;
    if (font.write(buffer, actual) != actual) {
      writeFailed = true;
      break;
    }
    crc = updateCrc32(crc, buffer, actual);
    received += actual;
    lastProgress = millis();
  }
  font.flush();
  font.close();
  // Also reject a final read/write/flush which crossed the whole-transfer
  // deadline after receiving the last byte; success must fit in the same
  // absolute budget as every intermediate iteration.
  if (millis() - started >= RECEIVE_TOTAL_TIMEOUT_MS) timedOut = true;

  if (writeFailed || timedOut || received != expectedSize
      || ~crc != expectedCrc) {
    removeIfPresent(destinationFont);
    removeIfPresent(destinationMetadata);
    output.print(writeFailed ? "ERROR WRITE\n"
                 : timedOut ? "ERROR TIMEOUT\n" : "ERROR CHECKSUM\n");
    return;
  }
  if (!writeMetadata(destinationMetadata, expectedSize, expectedCrc)) {
    removeIfPresent(destinationFont);
    removeIfPresent(destinationMetadata);
    output.print("ERROR INSTALL\n");
    return;
  }
  if (mode == ReceiveMode::StageOnly) {
    output.print("STAGED\n");
    return;
  }
  if (!installTemporaryPair(expectedSize, expectedCrc)) {
    recoverFontTransaction();
    output.print("ERROR INSTALL\n");
    return;
  }
  output.print("OK\n");
}

// STAGEV2 is receiver paced: the sender transmits exactly the negotiated chunk
// (or the shorter final chunk) and waits for the cumulative ACK before sending
// more. This prevents SD latency from overflowing the RP2040 UART FIFO.
void receiveFontStage2(Stream& input, Print& output, size_t expectedSize,
                       uint32_t expectedCrc) {
  ++espStage2Attempts;
  espStage2LastBytes = 0;
  espStage2LastElapsedMs = 0;
  espStage2LastResult = Stage2Result::Receiving;
  const uint32_t started = millis();

  if (!sdReady) {
    espStage2LastResult = Stage2Result::SdUnavailable;
    espStage2LastElapsedMs = millis() - started;
    output.print("ERROR SD\n");
    return;
  }

  cleanStagedFiles();
  File font = SD.open(STAGED_FONT_PATH, "w");
  if (!font) {
    espStage2LastResult = Stage2Result::OpenFailed;
    espStage2LastElapsedMs = millis() - started;
    output.print("ERROR OPEN\n");
    return;
  }

  output.print(mesh::indicator_font::kStageV2ReadyReply);
  output.print('\n');
  uint8_t buffer[mesh::indicator_font::kStageV2ChunkBytes];
  size_t received = 0;
  uint32_t crc = 0xFFFFFFFFUL;
  uint32_t lastProgress = started;
  Stage2Result failure = Stage2Result::Idle;

  while (received < expectedSize) {
    const size_t chunkSize =
        mesh::indicator_font::stageV2ChunkSize(expectedSize, received);
    size_t chunkReceived = 0;
    while (chunkReceived < chunkSize) {
      const uint32_t now = millis();
      if (now - started >= RECEIVE_TOTAL_TIMEOUT_MS
          || now - lastProgress >= RECEIVE_IDLE_TIMEOUT_MS) {
        failure = Stage2Result::Timeout;
        break;
      }

      int available = input.available();
      if (available <= 0) {
        delay(1);
        continue;
      }
      size_t count = (size_t)available;
      if (count > chunkSize - chunkReceived) {
        count = chunkSize - chunkReceived;
      }
      size_t actual = input.readBytes(buffer + chunkReceived, count);
      if (actual == 0) continue;
      chunkReceived += actual;
      lastProgress = millis();
    }
    if (failure != Stage2Result::Idle) break;

    // ACK only after the entire negotiated chunk has reached the SD layer.
    if (font.write(buffer, chunkSize) != chunkSize) {
      failure = Stage2Result::WriteFailed;
      break;
    }
    crc = updateCrc32(crc, buffer, chunkSize);
    size_t nextReceived = 0;
    if (!mesh::indicator_font::advanceStageV2Offset(
            expectedSize, received, chunkSize, nextReceived)) {
      failure = Stage2Result::WriteFailed;
      break;
    }
    received = nextReceived;
    espStage2LastBytes = received;
    output.printf("ACK %lu\n", (unsigned long)received);
  }

  font.flush();
  font.close();
  espStage2LastElapsedMs = millis() - started;
  // A final SD write/flush can cross the same absolute transfer deadline even
  // though the last byte arrived just before it. Do not publish STAGED after
  // the receiver's whole-transfer budget has expired.
  if (failure == Stage2Result::Idle
      && espStage2LastElapsedMs >= RECEIVE_TOTAL_TIMEOUT_MS) {
    failure = Stage2Result::Timeout;
  }

  if (failure != Stage2Result::Idle) {
    cleanStagedFiles();
    espStage2LastResult = failure;
    output.print(failure == Stage2Result::Timeout
                     ? "ERROR TIMEOUT\n" : "ERROR WRITE\n");
    return;
  }
  if (received != expectedSize || ~crc != expectedCrc) {
    cleanStagedFiles();
    espStage2LastResult = Stage2Result::ChecksumFailed;
    output.print("ERROR CHECKSUM\n");
    return;
  }
  if (!writeMetadata(STAGED_META_PATH, expectedSize, expectedCrc)) {
    cleanStagedFiles();
    espStage2LastResult = Stage2Result::MetadataFailed;
    output.print("ERROR INSTALL\n");
    return;
  }

  ++espStage2Completed;
  espStage2LastResult = Stage2Result::Staged;
  output.print("STAGED\n");
}

struct CommandReader {
  Stream& input;
  Print& output;
  bool allowUpload;
  bool allowStagedUpload;
  bool trackEspRequest;
  char line[96] = {};
  size_t length = 0;
};

void processCommand(CommandReader& reader) {
  if (strcmp(reader.line, "MCFONT INFO") == 0) {
    sendInfo(reader.output, reader.trackEspRequest);
    return;
  }
  if (strcmp(reader.line, "MCFONT STATUS") == 0) {
    reader.output.printf("MCFONT STATUS %lu %lu %lu %lu\n",
                         (unsigned long)espInfoRequests,
                         (unsigned long)espGetAttempts,
                         (unsigned long)espGetCompleted,
                         (unsigned long)espLastBytes);
    return;
  }
  if (strcmp(reader.line, "MCFONT STAGESTATUS") == 0) {
    reader.output.printf("MCFONT STAGESTATUS 2 %lu %lu %lu %lu %s\n",
                         (unsigned long)espStage2Attempts,
                         (unsigned long)espStage2Completed,
                         (unsigned long)espStage2LastBytes,
                         (unsigned long)espStage2LastElapsedMs,
                         stage2ResultName(espStage2LastResult));
    return;
  }
  if (strcmp(reader.line, "MCFONT GET") == 0) {
    sendFont(reader.output, reader.trackEspRequest);
    return;
  }

  unsigned long size = 0;
  unsigned long crc = 0;
  unsigned long chunkSize = 0;
  int consumed = 0;
  if (sscanf(reader.line, "MCFONT STAGEV2 %lu %lx %lu %n",
             &size, &crc, &chunkSize, &consumed) == 3
      && reader.line[consumed] == 0) {
    if (!reader.allowStagedUpload) {
      reader.output.print("ERROR READONLY\n");
    } else if (size < 64 || size > MAX_FONT_BYTES) {
      reader.output.print("ERROR SIZE\n");
    } else if (chunkSize != mesh::indicator_font::kStageV2ChunkBytes) {
      reader.output.print("ERROR CHUNK\n");
    } else {
      receiveFontStage2(reader.input, reader.output,
                        (size_t)size, (uint32_t)crc);
    }
    return;
  }
  if (sscanf(reader.line, "MCFONT STAGE %lu %lx", &size, &crc) == 2) {
    if (!reader.allowStagedUpload) {
      reader.output.print("ERROR READONLY\n");
    } else if (size < 64 || size > MAX_FONT_BYTES) {
      reader.output.print("ERROR SIZE\n");
    } else {
      receiveFont(reader.input, reader.output, (size_t)size, (uint32_t)crc,
                  ReceiveMode::StageOnly);
    }
    return;
  }
  if (sscanf(reader.line, "MCFONT COMMIT %lu %lx", &size, &crc) == 2) {
    if (!reader.allowStagedUpload) {
      reader.output.print("ERROR READONLY\n");
    } else if (installStagedPair((size_t)size, (uint32_t)crc)) {
      reader.output.print("OK\n");
    } else {
      reader.output.print("ERROR INSTALL\n");
    }
    return;
  }
  if (strcmp(reader.line, "MCFONT ABORT") == 0) {
    if (!reader.allowStagedUpload) {
      reader.output.print("ERROR READONLY\n");
    } else {
      cleanStagedFiles();
      espStage2LastResult = Stage2Result::Aborted;
      reader.output.print("OK\n");
    }
    return;
  }

  if (sscanf(reader.line, "MCFONT PUT %lu %lx", &size, &crc) == 2) {
    if (!reader.allowUpload) {
      reader.output.print("ERROR READONLY\n");
    } else if (size < 64 || size > MAX_FONT_BYTES) {
      reader.output.print("ERROR SIZE\n");
    } else {
      receiveFont(reader.input, reader.output, (size_t)size, (uint32_t)crc,
                  ReceiveMode::InstallNow);
    }
    return;
  }
  reader.output.print("ERROR COMMAND\n");
}

void pollCommands(CommandReader& reader) {
  while (reader.input.available()) {
    int value = reader.input.read();
    if (value < 0) break;
    if (value == '\n') {
      reader.line[reader.length] = 0;
      processCommand(reader);
      reader.length = 0;
    } else if (value != '\r') {
      if (reader.length + 1 < sizeof(reader.line)) {
        reader.line[reader.length++] = (char)value;
      } else {
        reader.length = 0;
      }
    }
  }
}

// USB keeps the explicit host-side PUT command.  The internal UART exposes a
// two-phase STAGE/COMMIT path so ESP32 Wi-Fi recovery cannot replace a valid
// font until its immutable-source SHA-256 has passed.
CommandReader usbCommands = {Serial, Serial, true, false, false};
CommandReader espCommands = {Serial1, Serial1, false, true, true};

}  // namespace

void setup() {
  Serial.begin(115200);

  Serial1.setRX(FONT_UART_RX);
  Serial1.setTX(FONT_UART_TX);
  Serial1.setFIFOSize(1024);
  Serial1.begin(FONT_UART_BAUD);

  SPI1.setSCK(SD_SCK);
  SPI1.setTX(SD_MOSI);
  SPI1.setRX(SD_MISO);
  serviceSdMount(millis());
}

void loop() {
  serviceSdMount(millis());
  pollCommands(espCommands);
  pollCommands(usbCommands);
  delay(1);
}
