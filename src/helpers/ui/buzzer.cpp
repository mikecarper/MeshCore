#include "Arduino.h"
#include <helpers/CustomPeripheralConfig.h>
#if defined(PIN_BUZZER) || MESH_CUSTOM_PERIPHERALS
#include "buzzer.h"
#if MESH_GPS_VOICE
#if defined(MESH_GPS_VOICE_G726_BITRATE)
// Board source filters already compile this player entry point. Keep the
// experimental decoder out of ordinary images and avoid changing every
// board's filter just for the hardware comparison.
#include "g726/g726_decoder.c"
#include "G726VoiceData.h"
#else
#include "ButtonVoiceData.h"
#include "GpsVoiceData.h"
#endif
#include "nrf52/GpsVoicePlayer.h"
static mesh::audio::GpsVoicePlayer gps_voice;
extern "C" void PWM3_IRQHandler() { gps_voice.interrupt(); }

static mesh::audio::VoiceClip buttonVoiceClip(ButtonVoicePrompt prompt) {
    using namespace mesh::audio;
    switch (prompt) {
    case ButtonVoicePrompt::Ready: return readyClip;
    case ButtonVoicePrompt::NotificationCleared: return notificationClearedClip;
    case ButtonVoicePrompt::AdvertQueued: return advertQueuedClip;
    case ButtonVoicePrompt::AdvertFailed: return advertFailedClip;
    case ButtonVoicePrompt::SoundOn: return soundOnClip;
    case ButtonVoicePrompt::SoundOff: return soundOffClip;
    case ButtonVoicePrompt::GpsOn: return gpsOnClip;
    case ButtonVoicePrompt::GpsOff: return gpsOffClip;
    case ButtonVoicePrompt::ActionFailed: return actionFailedClip;
    case ButtonVoicePrompt::UsbSetup: return usbSetupClip;
    case ButtonVoicePrompt::ShuttingDown: return shuttingDownClip;
    case ButtonVoicePrompt::Restarting: return restartingClip;
#ifdef HAS_DRV2605
    case ButtonVoicePrompt::AlertsSoundVibration: return alertsSoundVibrationClip;
    case ButtonVoicePrompt::AlertsSoundOnly: return alertsSoundOnlyClip;
    case ButtonVoicePrompt::AlertsVibrationOnly: return alertsVibrationOnlyClip;
    case ButtonVoicePrompt::AlertsSilent: return alertsSilentClip;
#else
    case ButtonVoicePrompt::AlertsSoundVibration:
    case ButtonVoicePrompt::AlertsSoundOnly:
    case ButtonVoicePrompt::AlertsVibrationOnly:
    case ButtonVoicePrompt::AlertsSilent: return {};
#endif
    }
    return {};
}
#endif

int8_t genericBuzzer::_override_pin = -1;
uint32_t genericBuzzer::_generation = 0;

bool genericBuzzer::selected() {
    if (_seen_generation != _generation) {
        _seen_generation = _generation;
#if MESH_GPS_VOICE
        _voice_pending = false;
        _voice_allow_quiet = false;
        _voice_gap_started = false;
#endif
    }
    return _pin >= 0 && (_override_pin < 0 || _pin == _override_pin);
}

void genericBuzzer::setOverridePin(int8_t pin) {
    if (_override_pin == pin) return;
    rtttl::stop();
#if MESH_GPS_VOICE
    gps_voice.stop();
#endif
#ifdef PIN_BUZZER
    if (pin >= 0) digitalWrite(PIN_BUZZER, LOW);
#endif
#ifdef PIN_BUZZER_EN
    if (pin >= 0) digitalWrite(PIN_BUZZER_EN, LOW);
#endif
    _override_pin = pin;
    ++_generation;
}

void genericBuzzer::setPin(int8_t pin) {
    if (pin == _pin) return;
    stop();
    if (_pin >= 0) {
        digitalWrite(_pin, LOW);
        pinMode(_pin, INPUT);
    }
    _pin = pin;
    begin();
}

void genericBuzzer::begin() {
    if (!selected()) return;
    digitalWrite(_pin, LOW);
    pinMode(_pin, OUTPUT);
#ifdef PIN_BUZZER_EN
    if (usesBuiltInPin()) digitalWrite(PIN_BUZZER_EN, LOW);
    if (usesBuiltInPin()) pinMode(PIN_BUZZER_EN, OUTPUT);
#endif
    // Start muted until the UI applies the saved preference.
    quiet(_is_quiet);
}

void genericBuzzer::play(const char *melody) {
    if (!selected() || !melody) return;
#if MESH_GPS_VOICE
    // A muted ordinary alert must not cancel the one courtesy confirmation
    // that may still be queued or playing while the preference is quiet.
    if (_is_quiet) return;
#endif
    if (isPlaying())   // interrupt existing
    {
        stop();
    }

    if (_is_quiet) return;

#ifdef PIN_BUZZER_EN
    if (usesBuiltInPin()) digitalWrite(PIN_BUZZER_EN, HIGH);
#endif
    rtttl::begin(_pin,melody);
//    Serial.print("DBG: Playing melody - isQuiet: ");
//    Serial.println(isQuiet());
}

void genericBuzzer::playNotification(const char* melody) {
    if (!selected() || !melody) return;
    // Muted background alerts must not interrupt an explicit button prompt.
    if (_is_quiet) return;
    stop();
#ifdef PIN_BUZZER_EN
    if (usesBuiltInPin()) digitalWrite(PIN_BUZZER_EN, HIGH);
#endif
    rtttl::begin(_pin, melody);
}

void genericBuzzer::stopNotification() {
    if (!selected()) return;
#if MESH_GPS_VOICE
    // Notification timers own their tones, not user-requested confirmations.
    // Preserve a pending direction tone, speech gap, or active voice clip.
    if (_voice_pending || gps_voice.active()) return;
#endif
    stop();
}

bool genericBuzzer::speakGps(bool enabled) {
    return speakButton(enabled ? ButtonVoicePrompt::GpsOn : ButtonVoicePrompt::GpsOff);
}

bool genericBuzzer::speakButton(ButtonVoicePrompt prompt, bool allowQuiet) {
    if (!selected()) return false;
    const char* melody = nullptr;
    switch (prompt) {
    case ButtonVoicePrompt::GpsOn:
        melody = startup_song;
        break;
    case ButtonVoicePrompt::GpsOff:
    case ButtonVoicePrompt::ShuttingDown:
    case ButtonVoicePrompt::Restarting:
        melody = shutdown_song;
        break;
    case ButtonVoicePrompt::Ready:
    case ButtonVoicePrompt::NotificationCleared:
    case ButtonVoicePrompt::AdvertQueued:
    case ButtonVoicePrompt::AdvertFailed:
    case ButtonVoicePrompt::SoundOn:
    case ButtonVoicePrompt::SoundOff:
    case ButtonVoicePrompt::ActionFailed:
    case ButtonVoicePrompt::UsbSetup:
    case ButtonVoicePrompt::AlertsSoundVibration:
    case ButtonVoicePrompt::AlertsSoundOnly:
    case ButtonVoicePrompt::AlertsVibrationOnly:
    case ButtonVoicePrompt::AlertsSilent:
#if MESH_GPS_VOICE && !defined(HAS_DRV2605)
        // Reject unavailable modes before cancelling any valid audio.
        if (prompt >= ButtonVoicePrompt::AlertsSoundVibration) return false;
#endif
        break;
    default:
        return false;
    }
    stop();
    if (_is_quiet && !allowQuiet) return true;
#if !MESH_GPS_VOICE
    // The UI retains its legacy acknowledgements on untested boards. New
    // voice-only prompts must not introduce tones there (including Ready).
    if (!melody) return true;
#endif
#ifdef PIN_BUZZER_EN
    if (usesBuiltInPin()) digitalWrite(PIN_BUZZER_EN, HIGH);
#endif
#if MESH_GPS_VOICE
    _voice_prompt = prompt;
    _voice_pending = usesBuiltInPin();
    _voice_allow_quiet = allowQuiet;
    _voice_gap_started = false;
#endif
    // Preserve the recognisable rising/falling control tone even where voice
    // playback is disabled. Optional tones release their PWM before speech.
    if (melody) rtttl::begin(_pin, melody);
    return true;
}

#ifdef MESH_BUTTON_AUDIO_HIL
void genericBuzzer::voiceStatus(char* reply, size_t size) {
#if MESH_GPS_VOICE
    snprintf(reply, size, "voice playing=%u started=%lu completed=%lu cancelled=%lu samples=%lu quiet=%u gain=%u pending=%u tone=%u",
        gps_voice.playing(), (unsigned long)gps_voice.started(),
        (unsigned long)gps_voice.completed(), (unsigned long)gps_voice.cancelled(),
        (unsigned long)gps_voice.played(), _is_quiet, gps_voice.gain(),
        _voice_pending, rtttl::isPlaying());
#else
    snprintf(reply, size, "voice unsupported");
#endif
}
bool genericBuzzer::voiceGain(uint8_t gain) {
#if MESH_GPS_VOICE
    return !_voice_pending && gps_voice.setGain(gain);
#else
    (void)gain;
    return false;
#endif
}
#endif

void genericBuzzer::stop() {
    if (!selected()) return;
    rtttl::stop();
#if MESH_GPS_VOICE
    _voice_pending = false;
    _voice_allow_quiet = false;
    _voice_gap_started = false;
    gps_voice.stop();
#endif
#ifdef PIN_BUZZER_EN
    if (_is_quiet && usesBuiltInPin()) digitalWrite(PIN_BUZZER_EN, LOW);
#endif
}

bool genericBuzzer::isPlaying() {
    if (!selected()) return false;
#if MESH_GPS_VOICE
    if (_voice_pending || gps_voice.active()) return true;
#endif
    return rtttl::isPlaying();
}

void genericBuzzer::loop() {
    if (!selected()) return;
#if MESH_GPS_VOICE
    gps_voice.service();
    if (gps_voice.playing()) return;
    if (_voice_pending) {
      if (!rtttl::done()) {
        rtttl::play();
        return;
      }
      if (!_voice_gap_started) {
        rtttl::stop();
        _voice_gap_ms = millis();
        _voice_gap_started = true;
      }
      if (static_cast<uint32_t>(millis() - _voice_gap_ms) < 100) return;
      _voice_pending = false;
      _voice_gap_started = false;
      if (!_is_quiet || _voice_allow_quiet)
        gps_voice.start(buttonVoiceClip(_voice_prompt));
      _voice_allow_quiet = false;
      if (gps_voice.playing()) return;
    }
#endif
    if (!rtttl::done()) {
      rtttl::play();
      return;
    }
    // Courtesy mute/unmute speech can temporarily power the driver while the
    // preference stays quiet. Return both pins to their low-power state.
    if (_is_quiet && _pin >= 0) {
      digitalWrite(_pin, LOW);
#ifdef PIN_BUZZER_EN
      if (usesBuiltInPin()) digitalWrite(PIN_BUZZER_EN, LOW);
#endif
    }
}

void genericBuzzer::startup() {
    play(startup_song);
}

void genericBuzzer::shutdown() {
    play(shutdown_song);
}

void genericBuzzer::quiet(bool buzzer_state) {
    if (buzzer_state && selected()) stop();
    _is_quiet = buzzer_state;
    if (!selected()) return;
#ifdef PIN_BUZZER_EN
    if (_is_quiet) {
      if (usesBuiltInPin()) digitalWrite(PIN_BUZZER_EN, LOW);
    } else {
      if (usesBuiltInPin()) digitalWrite(PIN_BUZZER_EN, HIGH);
    }
#endif
}

bool genericBuzzer::isQuiet() {
    return _is_quiet;
}

#endif // builtin buzzer or configurable external outputs
