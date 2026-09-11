#pragma once

#include <Arduino.h>
#include <stdint.h>
#include "CustomSX1276Wrapper.h"

// TX power control for boards whose SX1276 does not drive the antenna
// directly, but feeds an external power amplifier whose gain is set by an
// analog control voltage rather than by the radio.
//
// Such a PA exposes a gain-control input - variously labelled APC, APC1/APC2,
// VAPC or VGA depending on the module - which is driven here from one of the
// MCU's DAC outputs. The radio is parked at a single fixed drive level and
// every power step is made by moving that control voltage, so the only thing
// this class overrides is applyCachedTxPower().
//
// A board supplies a table mapping the power it wants, in dBm at the antenna,
// to the control code that produces it. That table is the board's calibration:
// it is the only thing that knows what the amplifier actually does, so it is
// also what defines the legal range. Requests outside it are refused rather
// than saturated at the nearest end.
//
//   static const DacPaLevel LEVELS[] = { {10, 30}, {17, 50}, {30, 130} };
//   DacPaSX1276Wrapper radio_driver(radio, board, PIN_APC, LEVELS, 3);
//
// Entries must be ordered by ascending dBm. Nothing is assumed about the
// control codes themselves, so a board whose gain input runs backwards (a
// falling code for rising power) needs no special handling.
//
// The default drive level of +2 dBm is the SX1276's floor on PA_BOOST
// (RegPaConfig OutputPower = 0), which suits an amplifier expecting a small
// constant input; pass drive_dbm to override it for a PA that wants more.
//
// Not every board holds the radio still. Where the amplifier is driven
// somewhere other than its floor, the radio's own output moves with each step
// as well, and the board supplies a second table of radio output levels
// alongside the first. Pass it as radio_dbm and the fixed drive level is not
// used at all:
//
//   static const DacPaLevel LEVELS[]    = { {20, 165}, {24, 155}, {27, 142} };
//   static const int8_t     RADIO_DBM[] = {   2,         6,         9       };
//   DacPaSX1276Wrapper radio_driver(radio, board, PIN_APC, LEVELS, 3,
//                                   DAC_PA_TABLE_MAX, RADIO_DBM);
//
// radio_dbm must have one entry per level.
//
// Which output pin those values reach depends on how the board is wired. Set
// force_rfo for a board whose radio feeds the amplifier from RFO_HF rather
// than PA_BOOST (ExpressLRS layouts flag this as "radio_rfo_hf"). It matters
// because the two paths accept different ranges and RadioLib will not guess:
//
//   RFO       -4 .. 15 dBm
//   PA_BOOST   2 .. 17 dBm, plus a special case at 20
//
// RadioLib selects RFO on its own for anything below 2 dBm, so a board whose
// levels are all negative works either way. One sitting in the overlap - the
// Radiomaster Bandit's [2, 6, 9, 10], for instance - would silently come out
// of the wrong pin without this flag.
//
// drive_dbm's +2 default is the PA_BOOST floor; an RFO board should pass its
// own, since -4 is where that path bottoms out instead.
//
// The gain control is written through writeGainControl(), which uses the
// ESP32's DAC by default. A board driving its PA from a PWM pin or an external
// I2C DAC can subclass and override that one method.
//
// ---------------------------------------------------------------------------
// Deriving a table from an ExpressLRS hardware layout
//
// ExpressLRS transmitter modules use this arrangement widely (they call it
// POWER_OUTPUT_DACWRITE), so their published layouts are a convenient source
// of vendor-calibrated tables. The layout's "power_values" array is indexed by
// (PowerLevels_e - power_min), so with power_min = 0 the entries run:
//
//     PWR_10mW  PWR_25mW  PWR_50mW  PWR_100mW  PWR_250mW  PWR_500mW  ...
//       10 dBm    14 dBm    17 dBm     20 dBm     24 dBm     27 dBm
//
// Those are the vendor's nominal labels, not measurements. Treat an entry as
// an index into the amplifier's behaviour until it has been on a power meter.
// ---------------------------------------------------------------------------

struct DacPaLevel {
  int8_t  dbm;   // power at the antenna, after the amplifier
  uint8_t dac;   // gain-control code that produces it
};

// SX1276 PA_BOOST output floor: Pout = 2 + OutputPower, so +2 dBm is
// OutputPower = 0.
#define DAC_PA_DEFAULT_DRIVE_DBM  2

// Sentinel for the max_dbm constructor argument: use the table's top entry.
#define DAC_PA_TABLE_MAX  INT8_MAX

class DacPaSX1276Wrapper : public CustomSX1276Wrapper {
public:
  // max_dbm optionally caps the amplifier below its top table entry, for a
  // deployment that should not use everything the hardware can reach (thermal
  // headroom, or a regulatory limit lower than the PA's capability). It is
  // only ever a reduction; it cannot raise the ceiling above the table.
  DacPaSX1276Wrapper(CustomSX1276& radio, mesh::MainBoard& board,
                     uint8_t ctrl_pin,
                     const DacPaLevel* levels, uint8_t num_levels,
                     int8_t max_dbm = DAC_PA_TABLE_MAX,
                     const int8_t* radio_dbm = NULL,
                     bool force_rfo = false,
                     int8_t drive_dbm = DAC_PA_DEFAULT_DRIVE_DBM)
      : CustomSX1276Wrapper(radio, board),
        _ctrl_pin(ctrl_pin), _levels(levels), _num_levels(num_levels),
        _radio_dbm(radio_dbm), _force_rfo(force_rfo), _drive_dbm(drive_dbm) {
    if (!levels || !num_levels) {
      _min_dbm = 1;
      _max_dbm = 0;  // Empty range: every power request fails.
      return;
    }
    _min_dbm = levels[0].dbm;
    _max_dbm = levels[num_levels - 1].dbm;
    if (max_dbm < _max_dbm) _max_dbm = max_dbm;
  }

  // The supported range, taken from the table itself. The amplifier decides
  // what this board can do, so nothing else has to be told separately.
  int8_t minTxPowerDbm() const { return _min_dbm; }
  int8_t maxTxPowerDbm() const { return _max_dbm; }

  // Park the radio at its drive level and set the amplifier to dbm. Call once,
  // after the radio has started. A startup level outside the supported range
  // is brought into it rather than left unset.
  //
  // Boards carrying a radio_dbm table have no fixed drive level to park at;
  // applyCachedTxPower() sets the radio for each step instead.
  //
  // This is also where an RFO board is corrected: std_init()'s begin() always
  // configures PA_BOOST, so the first write from here moves it. Nothing
  // transmits in between.
  bool beginPowerControl(int8_t dbm) {
    if (dbm < _min_dbm) dbm = _min_dbm;
    if (dbm > _max_dbm) dbm = _max_dbm;
    return setTxPower(dbm);
  }

protected:
  // Emit a gain-control code. Override for a PA driven by PWM or an external
  // DAC instead of the MCU's own.
  virtual void writeGainControl(uint8_t code) {
    dacWrite(_ctrl_pin, code);
  }

  // MeshCore asks for power in dBm at the antenna. Restore both the radio
  // drive and amplifier setting, including after a watchdog hard reset.
  //
  // Out-of-range requests are refused rather than silently saturated. Without
  // this the top table entry becomes the response to any large number, which
  // on a high-power module is not a failure anyone wants to discover on air.
  int16_t applyCachedTxPower(int8_t dbm) override {
    if (dbm < _min_dbm || dbm > _max_dbm) {
      return RADIOLIB_ERR_INVALID_OUTPUT_POWER;
    }
    const uint8_t idx = indexForDbm(dbm);
    // A watchdog hard reset restores std_init()'s radio output, too. Reapply
    // the fixed drive level/RFO selection as well as per-step radio levels;
    // otherwise recovery can drive the PA at LORA_TX_POWER instead of +2 dBm.
    const int16_t status = ((CustomSX1276 *)_radio)->setOutputPower(
        _radio_dbm ? _radio_dbm[idx] : _drive_dbm, _force_rfo);
    if (status != RADIOLIB_ERR_NONE) return status;
    writeGainControl(_levels[idx].dac);
    return RADIOLIB_ERR_NONE;
  }

private:
  // Highest level that does not exceed dbm. Callers have already range-checked.
  uint8_t indexForDbm(int8_t dbm) const {
    uint8_t idx = 0;
    for (uint8_t i = 0; i < _num_levels; i++) {
      if (_levels[i].dbm <= dbm) idx = i;
    }
    return idx;
  }

  uint8_t _ctrl_pin;
  const DacPaLevel* _levels;
  uint8_t _num_levels;
  const int8_t* _radio_dbm;
  bool _force_rfo;
  int8_t _drive_dbm;
  int8_t _min_dbm;
  int8_t _max_dbm;
};
