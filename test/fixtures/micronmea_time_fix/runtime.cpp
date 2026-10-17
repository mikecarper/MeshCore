#include <Arduino.h>
#include <MicroNMEA.h>
#include <cstdlib>
#include <cstring>
#include <iostream>
#include <memory>
#include <string>
#include <utility>
#include <vector>

namespace {

void require(bool condition, const char* diagnostic) {
  if (!condition) {
    std::cerr << "SEMANTIC: " << diagnostic << "\n";
    std::exit(42);
  }
}

std::string sentence(const std::string& body) {
  const std::string line = "$" + body;
  char checksum[3] = {};
  MicroNMEA::generateChecksum(line.c_str(), checksum);
  const std::string result = line + "*" + checksum;
  require(MicroNMEA::testChecksum(result.c_str()), "fixture checksum");
  return result;
}

std::string rmc(const std::string& time, const std::string& date = "171026",
                bool terminal_date = false) {
  return sentence("GPRMC," + time +
      ",A,4807.038,N,01131.000,E,0.0,0.0," + date +
      (terminal_date ? "" : ",,,A"));
}

std::string gga(const std::string& time) {
  return sentence("GPGGA," + time +
      ",4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,");
}

void feed(MicroNMEA& parser, const std::string& raw) {
  for (char c : raw) parser.process(c);
  parser.process('\r');
}

void seed(MicroNMEA& parser) {
  feed(parser, gga("120000.00"));
  feed(parser, rmc("120000.00"));
  require(parser.isValid() && parser.getNumSatellites() == 8,
          "valid fixture seed");
  require(parser.getYear() == 2026 && parser.getMonth() == 10 &&
          parser.getDay() == 17, "valid fixture seed date");
}

void checkTime(const MicroNMEA& parser, uint8_t hundredths) {
  require(parser.getHour() == 12 && parser.getMinute() == 34 &&
          parser.getSecond() == 56, "parsed UTC time changed");
  require(parser.getHundredths() == hundredths, "hundredths pad/truncate");
}

void formats() {
  const std::vector<std::pair<std::string, uint8_t>> inputs = {
      {"123456", 0}, {"123456.1", 10}, {"123456.12", 12},
      {"123456.123456", 12}, {"123456.0", 0}, {"123456.09", 9}};
  for (const auto& input : inputs) {
    char buffer[100] = {};
    MicroNMEA parser(buffer, sizeof(buffer));
    seed(parser);
    const std::string raw_rmc = rmc(input.first);
    feed(parser, raw_rmc);
    require(parser.isValid(), "RMC short time format lost validity");
    checkTime(parser, input.second);
    require(parser.getLatitude() == 48117300 &&
            parser.getLongitude() == 11516666, "RMC location changed");
    require(std::string(parser.getSentence()) == raw_rmc, "RMC raw changed");
    const std::string raw_gga = gga(input.first);
    feed(parser, raw_gga);
    require(parser.isValid() && parser.getNumSatellites() == 8,
            "GGA short time format lost validity or satellites");
    checkTime(parser, input.second);
    require(std::string(parser.getSentence()) == raw_gga, "GGA raw changed");
  }
}

void invalidFields() {
  const std::vector<std::string> dates = {
      "", "1", "12", "17102", "17102x", "171026x"};
  for (const auto& date : dates) {
    char buffer[100] = {};
    MicroNMEA parser(buffer, sizeof(buffer));
    seed(parser);
    const std::string raw = rmc("123456.12", date);
    feed(parser, raw);
    require(parser.getYear() == 0 && parser.getMonth() == 0 &&
            parser.getDay() == 0, "incomplete date retained old UTC date");
    checkTime(parser, 12);
    require(parser.isValid() && parser.getNumSatellites() == 8 &&
            parser.getLatitude() == 48117300,
            "date rejection destroyed position/satellite state");
    require(std::string(parser.getSentence()) == raw, "bad-date raw changed");
    feed(parser, rmc("123456.12"));
    require(parser.getYear() == 2026 && parser.getMonth() == 10 &&
            parser.getDay() == 17, "date recovery");
  }
  const std::vector<std::string> times = {
      "", "1", "12", "12345", "12345x", "123456.",
      "123456.x", "123456.1x"};
  for (const auto& time : times) {
    for (bool use_gga : {false, true}) {
      char buffer[100] = {};
      MicroNMEA parser(buffer, sizeof(buffer));
      seed(parser);
      const std::string raw = use_gga ? gga(time) : rmc(time);
      feed(parser, raw);
      require(parser.getHour() == 99 && parser.getMinute() == 99 &&
              parser.getSecond() == 99 && parser.getHundredths() == 0,
              "incomplete time retained old UTC time");
      require(parser.isValid() && parser.getNumSatellites() == 8 &&
              parser.getLatitude() == 48117300,
              "time rejection destroyed position/satellite state");
      require(std::string(parser.getSentence()) == raw, "bad-time raw changed");
      feed(parser, rmc("123456.12"));
      checkTime(parser, 12);
    }
  }
}

void terminalDate() {
  char buffer[100] = {};
  MicroNMEA parser(buffer, sizeof(buffer));
  seed(parser);
  const std::string raw = rmc("123456.12", "181026", true);
  feed(parser, raw);
  require(parser.isValid() && parser.getYear() == 2026 &&
          parser.getMonth() == 10 && parser.getDay() == 18,
          "valid terminal RMC date rejected");
  require(std::string(parser.getSentence()) == raw, "terminal-date raw changed");
}

unsigned bad_count = 0, unknown_count = 0;
void badChecksum(MicroNMEA&) { ++bad_count; }
void unknownSentence(MicroNMEA&) { ++unknown_count; }

class Output : public Stream {
public:
  std::string bytes;
  size_t write(uint8_t c) override {
    bytes += static_cast<char>(c);
    return 1;
  }
};

void callbacks() {
  char buffer[100] = {};
  MicroNMEA parser(buffer, sizeof(buffer));
  parser.setBadChecksumHandler(badChecksum);
  parser.setUnknownSentenceHandler(unknownSentence);
  seed(parser);
  std::string bad = rmc("235959.00", "181026");
  const size_t checksum = bad.find('*') + 1;
  bad[checksum] = bad[checksum] == '0' ? '1' : '0';
  require(!MicroNMEA::testChecksum(bad.c_str()), "bad fixture checksum");
  feed(parser, bad);
  require(bad_count == 1 && unknown_count == 0, "checksum callback changed");
  require(parser.getHour() == 12 && parser.getDay() == 17,
          "bad checksum changed valid state");
  require(std::string(parser.getSentence()) == bad, "bad-checksum raw changed");
  parser.process('\n');
  require(bad_count == 1 && parser.getSentence()[0] == '\0',
          "CRLF duplicated checksum callback");
  const std::string other = sentence("GPTXT,01,01,02,unchanged");
  feed(parser, other);
  require(unknown_count == 1 && bad_count == 1, "unknown callback changed");
  require(std::string(parser.getSentence()) == other, "unknown raw changed");
  require(std::string(parser.getMessageID()) == "TXT" &&
          parser.getTalkerID() == 'P', "message identifiers changed");
  parser.process('\n');
  parser.process('\r');
  parser.process('\n');
  require(unknown_count == 1 && bad_count == 1, "empty CRLF callback");
  Output output;
  MicroNMEA::sendSentence(output, "$PCAS04,7");
  require(output.bytes == sentence("PCAS04,7") + "\r\n",
          "sendSentence framing changed");
}

void boundedBuffer() {
  std::unique_ptr<char[]> buffer(new char[16]());
  MicroNMEA parser(buffer.get(), 16);
  const std::string long_input = "$GPTXT," + std::string(200, 'x') + "*00";
  feed(parser, long_input);
  require(std::strlen(parser.getSentence()) == 15,
          "bounded parser buffer length changed");
  require(parser.getSentence()[15] == '\0', "buffer terminator missing");
  parser.process('\n');
  feed(parser, sentence("GPTXT,x"));
  require(std::strlen(parser.getSentence()) < 16, "buffer did not recover");
}

void nearBufferDate() {
  // Date starts at index 95; "1*HH" ends at 98, NUL at 99. Original 2.0.6
  // unconditionally reads six date digits and crosses the exact heap buffer.
  std::unique_ptr<char[]> buffer(new char[100]());
  MicroNMEA parser(buffer.get(), 100);
  seed(parser);
  std::string prefix = "$GPRMC,123456.12,A,4807.038,N,01131.000,E,0.0,";
  require(prefix.size() < 94, "near-buffer fixture prefix");
  prefix += std::string(94 - prefix.size(), '0');
  prefix += ',';
  require(prefix.size() == 95, "near-buffer date offset");
  const std::string raw = sentence(prefix.substr(1) + "1");
  require(raw.size() == 99 && raw[95] == '1' && raw[96] == '*',
          "near-buffer sentence geometry");
  feed(parser, raw);
  require(parser.getYear() == 0 && parser.getMonth() == 0 &&
          parser.getDay() == 0, "near-buffer short date retained UTC");
  require(parser.isValid() && parser.getLatitude() == 48117300,
          "near-buffer date rejection destroyed position");
  require(std::string(parser.getSentence()) == raw, "near-buffer raw changed");
}

}  // namespace

int main(int argc, char** argv) {
  require(argc == 2, "select one runtime scenario");
  const std::string mode = argv[1];
  if (mode == "formats") formats();
  else if (mode == "invalid") invalidFields();
  else if (mode == "terminal-date") terminalDate();
  else if (mode == "callbacks") callbacks();
  else if (mode == "bounds") boundedBuffer();
  else if (mode == "near-buffer-date") nearBufferDate();
  else require(false, "unknown runtime scenario");
  return 0;
}
