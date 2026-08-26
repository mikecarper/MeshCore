#include <Arduino.h>
#include <SD.h>
#include <SPI.h>

namespace {

constexpr uint32_t FONT_UART_BAUD = 1000000;
constexpr uint32_t SD_CLOCK_HZ = 1000000;
constexpr uint32_t RECEIVE_IDLE_TIMEOUT_MS = 10000;
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

bool sdReady = false;
size_t fontSize = 0;
uint32_t fontCrc = 0;
uint32_t espInfoRequests = 0;
uint32_t espGetAttempts = 0;
uint32_t espGetCompleted = 0;
size_t espLastBytes = 0;

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

void removeIfPresent(const char* path) {
  if (SD.exists(path)) SD.remove(path);
}

void cleanTransactionFiles() {
  removeIfPresent(TEMP_FONT_PATH);
  removeIfPresent(TEMP_META_PATH);
  removeIfPresent(BACKUP_FONT_PATH);
  removeIfPresent(BACKUP_META_PATH);
}

bool promotePair(const char* sourceFont, const char* sourceMetadata) {
  removeIfPresent(FONT_PATH);
  removeIfPresent(FONT_META_PATH);
  if (!SD.rename(sourceFont, FONT_PATH)) return false;
  if (SD.rename(sourceMetadata, FONT_META_PATH)) return true;
  removeIfPresent(FONT_PATH);
  return false;
}

void recoverFontTransaction() {
  size_t size;
  uint32_t crc;
  if (validatePair(FONT_PATH, FONT_META_PATH, size, crc)) {
    cleanTransactionFiles();
    return;
  }

  // A reset between the two final renames can leave the new font next to its
  // temporary metadata. Complete that transaction when the sizes still agree.
  if (validatePair(FONT_PATH, TEMP_META_PATH, size, crc)) {
    removeIfPresent(FONT_META_PATH);
    if (SD.rename(TEMP_META_PATH, FONT_META_PATH)) {
      cleanTransactionFiles();
      return;
    }
  }

  if (validatePair(TEMP_FONT_PATH, TEMP_META_PATH, size, crc)
      && promotePair(TEMP_FONT_PATH, TEMP_META_PATH)) {
    cleanTransactionFiles();
    return;
  }

  if (validatePair(BACKUP_FONT_PATH, BACKUP_META_PATH, size, crc)
      && promotePair(BACKUP_FONT_PATH, BACKUP_META_PATH)) {
    cleanTransactionFiles();
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
  if (!moveCurrentToBackup()) return false;
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

void receiveFont(Stream& input, Print& output, size_t expectedSize,
                 uint32_t expectedCrc) {
  if (!sdReady) {
    output.print("ERROR SD\n");
    return;
  }

  removeIfPresent(TEMP_FONT_PATH);
  removeIfPresent(TEMP_META_PATH);
  File font = SD.open(TEMP_FONT_PATH, "w");
  if (!font) {
    output.print("ERROR OPEN\n");
    return;
  }

  output.print("READY\n");
  uint8_t buffer[512];
  size_t received = 0;
  uint32_t crc = 0xFFFFFFFFUL;
  uint32_t lastProgress = millis();
  bool writeFailed = false;
  while (received < expectedSize
         && millis() - lastProgress < RECEIVE_IDLE_TIMEOUT_MS) {
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

  if (writeFailed || received != expectedSize || ~crc != expectedCrc) {
    removeIfPresent(TEMP_FONT_PATH);
    output.print(writeFailed ? "ERROR WRITE\n" : "ERROR CHECKSUM\n");
    return;
  }
  if (!writeMetadata(TEMP_META_PATH, expectedSize, expectedCrc)
      || !installTemporaryPair(expectedSize, expectedCrc)) {
    recoverFontTransaction();
    output.print("ERROR INSTALL\n");
    return;
  }
  output.print("OK\n");
}

struct CommandReader {
  Stream& input;
  Print& output;
  bool allowUpload;
  char line[96] = {};
  size_t length = 0;
};

void processCommand(CommandReader& reader) {
  if (strcmp(reader.line, "MCFONT INFO") == 0) {
    sendInfo(reader.output, !reader.allowUpload);
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
  if (strcmp(reader.line, "MCFONT GET") == 0) {
    sendFont(reader.output, !reader.allowUpload);
    return;
  }

  unsigned long size = 0;
  unsigned long crc = 0;
  if (sscanf(reader.line, "MCFONT PUT %lu %lx", &size, &crc) == 2) {
    if (!reader.allowUpload) {
      reader.output.print("ERROR READONLY\n");
    } else if (size < 64 || size > MAX_FONT_BYTES) {
      reader.output.print("ERROR SIZE\n");
    } else {
      receiveFont(reader.input, reader.output, (size_t)size, (uint32_t)crc);
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

CommandReader usbCommands = {Serial, Serial, true};
CommandReader espCommands = {Serial1, Serial1, false};

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
  sdReady = SD.begin(SD_CS, SD_CLOCK_HZ, SPI1);
  if (sdReady) {
    if (!SD.exists(FONT_DIRECTORY)) SD.mkdir(FONT_DIRECTORY);
    recoverFontTransaction();
    refreshFontInfo();
  }
}

void loop() {
  pollCommands(espCommands);
  pollCommands(usbCommands);
  delay(1);
}
