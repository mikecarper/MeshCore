#include <zephyr/kernel.h>
#include <zephyr/drivers/pwm.h>
#include <zephyr/drivers/gpio.h>
#include "native_pings.h"

extern void tag_motion_start(void);
extern int tag_motion_sample(void);
extern void tag_motion_stop(void);

static K_MUTEX_DEFINE(lock);
static unsigned monitoring, observations, ringing, indicating;
enum { COLLAR_MOTION = BIT(2) };
static bool configured;
static const struct gpio_dt_spec button = GPIO_DT_SPEC_GET(DT_NODELABEL(tag_button), gpios);
static const struct gpio_dt_spec green = GPIO_DT_SPEC_GET(DT_NODELABEL(green_led), gpios);
static const struct pwm_dt_spec buzzer = PWM_DT_SPEC_GET(DT_NODELABEL(tag_buzzer));

int rak_board_prepare(void)
{
    k_mutex_lock(&lock, K_FOREVER);
    int err = 0;
    if (!configured) {
        err = gpio_pin_configure_dt(&green, GPIO_OUTPUT_INACTIVE);
        if (!err) err = gpio_pin_configure_dt(&button, GPIO_INPUT);
        if (!err) configured = true;
    }
    k_mutex_unlock(&lock);
    return err;
}

static int indicate(unsigned client, bool active)
{
    k_mutex_lock(&lock, K_FOREVER);
    unsigned next = active ? indicating | client : indicating & ~client;
    int err = gpio_pin_set_dt(&green, next != 0);
    if (!err) indicating = next;
    k_mutex_unlock(&lock);
    return err;
}

static void start(unsigned client)
{
    k_mutex_lock(&lock, K_FOREVER);
    if (!monitoring) tag_motion_start();
    monitoring |= client;
    k_mutex_unlock(&lock);
}

static bool detected(unsigned client)
{
    k_mutex_lock(&lock, K_FOREVER);
    if (monitoring) {
        int sample = tag_motion_sample();
        if (sample > 0) observations |= monitoring;
        // Finder SDKs treat a sensor error as possible movement. A GPS wake
        // for the collar requires an actual motion observation.
        if (sample < 0) observations |= monitoring & ~COLLAR_MOTION;
    }
    bool moved = !(monitoring & client) || (observations & client);
    observations &= ~client;
    k_mutex_unlock(&lock);
    return moved;
}

static void stop(unsigned client)
{
    k_mutex_lock(&lock, K_FOREVER);
    if (monitoring == client) tag_motion_stop();
    monitoring &= ~client;
    observations &= ~client;
    k_mutex_unlock(&lock);
}

static unsigned pet_hz;

static int write_sound(unsigned clients, unsigned hz)
{
    uint32_t period = clients || !hz ? buzzer.period : 1000000000U / hz;
    return pwm_set_dt(&buzzer, period, (clients || hz) ? period / 2 : 0);
}

int rak_pet_tone(unsigned hz)
{
    k_mutex_lock(&lock, K_FOREVER);
    int err = write_sound(ringing, hz);
    if (!err) pet_hz = hz;
    k_mutex_unlock(&lock);
    return err;
}

static int sound(unsigned client, bool active)
{
    k_mutex_lock(&lock, K_FOREVER);
    unsigned next = active ? ringing | client : ringing & ~client;
    int err = write_sound(next, pet_hz);
    if (!err) ringing = next;
    k_mutex_unlock(&lock);
    return err;
}

void rak_apple_motion_start(void) { start(BIT(0)); }
void rak_collar_motion_start(void) { start(COLLAR_MOTION); }
bool rak_collar_motion_detected(void) { return detected(COLLAR_MOTION); }
void rak_collar_motion_stop(void) { stop(COLLAR_MOTION); }
bool rak_apple_motion_detected(void) { return detected(BIT(0)); }
void rak_apple_motion_stop(void) { stop(BIT(0)); }
int rak_apple_buzzer(bool active) { return sound(BIT(0), active); }
void rak_android_motion_start(void) { start(BIT(1)); }
bool rak_android_motion_detected(void) { return detected(BIT(1)); }
void rak_android_motion_stop(void) { stop(BIT(1)); }
int rak_android_buzzer(bool active) { return sound(BIT(1), active); }
int rak_apple_indicator(bool active) { return indicate(BIT(0), active); }
int rak_android_indicator(bool active) { return indicate(BIT(1), active); }
