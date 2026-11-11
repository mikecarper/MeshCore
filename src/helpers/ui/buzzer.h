#pragma once

#include <Arduino.h>
#include <NonBlockingRtttl.h>
#include "ButtonVoice.h"

// Screenless Full Companions share spoken button confirmations. Keep an
// explicit board list: a configured buzzer on a screened board is not an
// opt-in, and the Wio joystick/display profiles retain their existing tones.
// ThinkNode M4 is excluded until its buzzer wiring is verified: its current
// PIN_BUZZER/PIN_BUZZER_EN overlap I2C SDA and a battery-status LED.
// R1 Neo has no flash fitted; its dummy QSPI SCK aliases the buzzer, so a
// profile which opts into QSPIFLASH must not also default to spoken audio.
// Retain the existing prerecorded clips; intelligibility still needs physical
// qualification on each board. MESH_GPS_VOICE=0 is an explicit opt-out.
#ifndef MESH_GPS_VOICE
#if defined(NRF52_PLATFORM) && defined(PIN_BUZZER) \
    && defined(COMPANION_RADIO_FULL) && COMPANION_RADIO_FULL \
    && (defined(T1000_E) || defined(RAK_WISMESH_TAG) \
        || defined(MESH_TRACKER_X1) \
        || (defined(R1Neo) && !defined(QSPIFLASH)) \
        || defined(THINKNODE_M3))
  #define MESH_GPS_VOICE 1
#else
  #define MESH_GPS_VOICE 0
#endif
#endif

/* class abstracts underlying RTTTL library 

    Just a simple implementation to start.  At the moment use same
    melody for message and discovery
    Suggest enum type for different sounds
    - on message
    - on discovery

    TODO
    - make message ring tone configurable

*/

class genericBuzzer
{
    public:
#ifdef PIN_BUZZER
        explicit genericBuzzer(int8_t pin = PIN_BUZZER) : _pin(pin) {}
#else
        explicit genericBuzzer(int8_t pin = -1) : _pin(pin) {}
#endif
        void setPin(int8_t pin); // stop playback before releasing the previous pin
        int8_t pin() const { return _pin; }
        // NonBlockingRTTTL is global. A selected custom output owns it until
        // cleared; built-in UI instances retain their mute preference only.
        static void setOverridePin(int8_t pin);
        void begin();  // set up buzzer port
        void play(const char *melody); // Generic play function
        void playNotification(const char* melody);
        void stopNotification(); // background alert cancellation must not cut off control speech
        bool speakGps(bool enabled); // direction tones, then optional spoken confirmation
        bool speakButton(ButtonVoicePrompt prompt, bool allowQuiet = false);
#ifdef MESH_BUTTON_AUDIO_HIL
        void voiceStatus(char* reply, size_t size);
        bool voiceGain(uint8_t gain);
#endif
        void stop();
        void loop();  // loop driven-nonblocking
        void startup();  // play startup sound
        void shutdown();  // play shutdown sound
        bool isPlaying();  // returns true if a sound is still playing else false
        void quiet(bool buzzer_state);  // enables or disables the buzzer
        bool isQuiet();  // get buzzer state on/off

    private:
        int8_t _pin;
        static int8_t _override_pin;
        static uint32_t _generation;
        uint32_t _seen_generation = 0;
        bool selected();
        bool usesBuiltInPin() const {
#ifdef PIN_BUZZER
            return _pin == PIN_BUZZER;
#else
            return false;
#endif
        }
        // gemini's picks:
        const char *startup_song = "Startup:d=4,o=5,b=160:16c6,16e6,8g6";
        const char *shutdown_song = "Shutdown:d=4,o=5,b=100:8g5,16e5,16c5";

        bool _is_quiet = true;
#if MESH_GPS_VOICE
        bool _voice_pending = false;
        ButtonVoicePrompt _voice_prompt = ButtonVoicePrompt::Ready;
        bool _voice_allow_quiet = false;
        bool _voice_gap_started = false;
        uint32_t _voice_gap_ms = 0;
#endif
};
