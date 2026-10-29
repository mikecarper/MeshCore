#include "../../CardputerMesh.h"
#include "../CardputerUITask.h"
#include "../icons.h"

class SettingsScreen : public CardputerScreen {
  enum SettingsItem {
    HdrPublicInfo,
    PublicInfoName,
    HdrRadio,
    RadioFreq,
    RadioBw,
    RadioSf,
    RadioCr,
    RadioPwr,
    HdrDevice,
    DeviceTextSize,
    DeviceBeep,
    DeviceBluetooth,
    DeviceGps,
    DevicePowersave,
    DeviceBatteryCorrection,
    HdrMesh,
    MeshPathSize,
    MeshDefaultScope,
    Count
  };

  CardputerUITask *_task;
  mesh::RTCClock *_rtc;
  CompanionNodePrefs *_node_prefs;
  CustomNodePrefs *_custom_prefs;

  int menu_index = 0;
  bool is_editing = false;
  char edit_error[80] = "Invalid value";
  String edit_buffer;
  uint8_t edit_u8;
  int32_t edit_tx_power = 0;
  static constexpr size_t MAX_NUMERIC_EDIT_BYTES = 31;

  void renderItem(CardputerDisplay &display, SettingsItem item, int x, int y);
  bool enterItemEdit(SettingsItem item);
  void cancelItemEdit(SettingsItem item);
  bool commitItemEdit(SettingsItem item);
  void handleEditInput(SettingsItem item, Keyboard::Event &e);
  bool inputParsePositiveFloat(float &val);
  bool validRadioParams(float freq, float bw, uint8_t sf, uint8_t cr) const;
  bool commitCommand(const char *command);
  bool commitRadio(float freq, float bw, uint8_t sf, uint8_t cr);

public:
  SettingsScreen(CardputerUITask *task, mesh::RTCClock *rtc, CompanionNodePrefs *node_prefs,
                 CustomNodePrefs *custom_prefs, CardputerAdvBoard *board)
      : _task(task), _rtc(rtc), _node_prefs(node_prefs), _custom_prefs(custom_prefs) {}
  int render(CardputerDisplay &lcd) override;
  bool handleInput(Keyboard::Event &e) override;
};
