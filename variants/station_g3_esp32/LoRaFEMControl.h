#pragma once

class LoRaFEMControl {
public:
  void init();
  void setSleepModeEnable();
  void setTxModeEnable();
  void setRxModeEnable();
  void setLNAEnable(bool enabled);
  void setPAGainEnable(bool enabled);

  bool canControlLNA() const;
  bool canControlPAGain() const;
  bool isLNAEnabled() const { return lna_enabled; }
  bool isPAGainEnabled() const { return pa_gain_enabled; }

private:
  enum class Mode : unsigned char { Sleep, Receive, Transmit };
  void applyPAGain();

  Mode _mode = Mode::Sleep;
  bool lna_enabled = true;
  bool pa_gain_enabled = false;
};
