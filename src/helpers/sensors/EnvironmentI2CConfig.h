#pragma once

// ENV_PIN_SDA / ENV_PIN_SCL select a dedicated environmental-sensor bus.
// GPIO 0 is valid on supported targets, while negative values are the common
// disconnected-pin sentinel. Keep every consumer on the same validity rule.
#if defined(ENV_PIN_SDA) && defined(ENV_PIN_SCL) \
    && (ENV_PIN_SDA >= 0) && (ENV_PIN_SCL >= 0)
#define ENV_HAS_SECONDARY_I2C 1
#else
#define ENV_HAS_SECONDARY_I2C 0
#endif
