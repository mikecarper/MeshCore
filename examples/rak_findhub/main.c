#include <errno.h>
#include <string.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/pwm.h>
#include <zephyr/kernel.h>
#include <zephyr/storage/flash_map.h>
#include <zephyr/sys/poweroff.h>
#include <zephyr/shell/shell.h>
#include <bluetooth/fast_pair/fhn/fhn.h>

#include "app_factory_reset.h"
#include "app_ui.h"
#include "app_ui_priv.h"
#ifdef RAK_NATIVE_PINGS
#include "native_pings.h"
#endif

extern int rak_findhub_start(void);
extern int tag_battery_mv(void);
extern void tag_mesh_start(void);
extern bool tag_motion_present(void);
#ifdef RAK_NATIVE_PINGS
#define tag_motion_start rak_android_motion_start
#define tag_motion_detected rak_android_motion_detected
#define tag_motion_stop rak_android_motion_stop
#define set_buzzer rak_android_buzzer
#else
extern void tag_motion_start(void);
extern bool tag_motion_detected(void);
extern void tag_motion_stop(void);
#endif

static const struct adc_dt_spec battery = ADC_DT_SPEC_GET(DT_PATH(zephyr_user));
static const struct gpio_dt_spec button = GPIO_DT_SPEC_GET(DT_NODELABEL(tag_button), gpios);
static const struct gpio_dt_spec led = GPIO_DT_SPEC_GET(DT_NODELABEL(blue_led), gpios);
static const struct gpio_dt_spec mode_led = GPIO_DT_SPEC_GET(DT_NODELABEL(green_led), gpios);
static const struct pwm_dt_spec buzzer = PWM_DT_SPEC_GET(DT_NODELABEL(tag_buzzer));
#ifndef RAK_NATIVE_PINGS
static int set_buzzer(bool active)
{
    return pwm_set_pulse_dt(&buzzer, active ? buzzer.period / 2 : 0);
}
#endif

static struct gpio_callback button_callback;
static atomic_t states;
static atomic_t startup_err = ATOMIC_INIT(-EINPROGRESS);

int app_ui_state_change_indicate(enum app_ui_state state, bool active)
{
    int err = 0;
    if (state == APP_UI_STATE_RINGING)
        err = set_buzzer(active);
    else if (state == APP_UI_STATE_FP_ADV)
        err = gpio_pin_set_dt(&led, active);
    if (!err) {
        atomic_set_bit_to(&states, state, active);
        if (state == APP_UI_STATE_RECOVERY_MODE || state == APP_UI_STATE_ID_MODE) {
            bool indicating = atomic_test_bit(&states, APP_UI_STATE_RECOVERY_MODE) ||
                              atomic_test_bit(&states, APP_UI_STATE_ID_MODE);
#ifdef RAK_NATIVE_PINGS
            err = rak_android_indicator(indicating);
#else
            err = gpio_pin_set_dt(&mode_led, indicating);
#endif
        }
    }
    return err;
}

static int64_t pressed_at = -1;

static void handle_button(struct k_work *work)
{
    int value = gpio_pin_get_dt(&button);
    if (value < 0)
        return;
    if (value) {
        if (atomic_test_bit(&states, APP_UI_STATE_RINGING)) {
            app_ui_request_broadcast(APP_UI_REQUEST_RINGING_STOP);
            pressed_at = -1;
        } else if (pressed_at < 0) {
            pressed_at = k_uptime_get();
        }
        return;
    }
    if (pressed_at < 0)
        return;

    int64_t held = k_uptime_get() - pressed_at;
    pressed_at = -1;
    if (held >= 12000) {
        app_factory_reset_schedule(K_NO_WAIT);
    } else if (held >= 8000) {
        // Arm button wake only after release, as on the stock RAK Tag.
        gpio_pin_interrupt_configure_dt(&button, GPIO_INT_LEVEL_ACTIVE);
        sys_poweroff();
    } else if (held >= 5000) {
        app_ui_request_broadcast(APP_UI_REQUEST_RECOVERY_MODE_ENTER);
    } else if (held >= 2000) {
        app_ui_request_broadcast(APP_UI_REQUEST_ID_MODE_ENTER);
    } else {
        app_ui_request_broadcast(APP_UI_REQUEST_FP_ADV_PAIRING_MODE_CHANGE);
    }
}
static K_WORK_DELAYABLE_DEFINE(button_work, handle_button);

static void button_edge(const struct device *port, struct gpio_callback *cb, uint32_t pins)
{
    if (atomic_test_bit(&states, APP_UI_STATE_APP_RUNNING))
        k_work_reschedule(&button_work, K_MSEC(30));
}

int app_ui_init(void)
{
    if (!gpio_is_ready_dt(&button) || !gpio_is_ready_dt(&led) ||
        !gpio_is_ready_dt(&mode_led) || !pwm_is_ready_dt(&buzzer))
        return -ENODEV;
    int err = gpio_pin_configure_dt(&led, GPIO_OUTPUT_INACTIVE);
#ifdef RAK_NATIVE_PINGS
    if (!err)
        err = rak_board_prepare();
#else
    if (!err)
        err = gpio_pin_configure_dt(&mode_led, GPIO_OUTPUT_INACTIVE);
    if (!err)
        err = gpio_pin_configure_dt(&button, GPIO_INPUT);
#endif
    if (!err)
        err = set_buzzer(false);
    if (!err) {
        gpio_init_callback(&button_callback, button_edge, BIT(button.pin));
        err = gpio_add_callback(button.port, &button_callback);
    }
    if (!err)
        err = gpio_pin_interrupt_configure_dt(&button, GPIO_INT_EDGE_BOTH);
    return err;
}

int app_motion_detector_init(void)
{
    static const struct bt_fast_pair_fhn_motion_detector_cb callbacks = {
        .start = tag_motion_start, .period_expired = tag_motion_detected, .stop = tag_motion_stop,
    };
    return tag_motion_present() ? bt_fast_pair_fhn_motion_detector_cb_register(&callbacks) : -ENODEV;
}

static int update_battery(void)
{
    int mv = tag_battery_mv();
    uint8_t percent = mv > 0 ? CLAMP((mv - 3200) / 10, 0, 100) : BT_FAST_PAIR_FHN_BATTERY_LEVEL_NONE;
    return bt_fast_pair_fhn_battery_level_set(percent);
}

static void poll_battery(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(battery_work, poll_battery);
static void poll_battery(struct k_work *work)
{
    if (atomic_test_bit(&states, APP_UI_STATE_APP_RUNNING)) {
        update_battery();
        k_work_reschedule(&battery_work, K_MINUTES(1));
    }
}
int app_battery_init(void)
{
    int err = update_battery();
    if (!err)
        k_work_schedule(&battery_work, K_MINUTES(1));
    return err;
}

static int registration_present(void)
{
    const struct flash_area *area;
    uint8_t header[4];
    static const uint8_t magic[] = {0xfa, 0x57, 0xfa, 0x57};
    int err = flash_area_open(FIXED_PARTITION_ID(bt_fast_pair_partition), &area);
    if (err)
        return err;
    err = flash_area_read(area, 0, header, sizeof(header));
    flash_area_close(area);
    if (err)
        return err;
    // The SDK validates the complete registration record before enabling Fast Pair.
    return memcmp(header, magic, sizeof(header)) ? -ENODATA : 0;
}

static int status_cmd(const struct shell *sh, size_t argc, char **argv)
{
    int err = atomic_get(&startup_err);
    if (err == -ENODATA)
        shell_print(sh, "Needs Google model registration data");
    else if (err)
        shell_print(sh, "Locator startup status %d; see USB log", err);
    else
        shell_print(sh, "Ready; provisioned=%d pairing_advertising=%d ringing=%d",
                    atomic_test_bit(&states, APP_UI_STATE_PROVISIONED),
                    atomic_test_bit(&states, APP_UI_STATE_FP_ADV),
                    atomic_test_bit(&states, APP_UI_STATE_RINGING));
    if (err != -EINPROGRESS)
        shell_print(sh, "battery_mV=%d accelerometer=%s", tag_battery_mv(),
                    tag_motion_present() ? "present" : "unavailable");
    return 0;
}
SHELL_STATIC_SUBCMD_SET_CREATE(commands,
    SHELL_CMD_ARG(status, NULL, "Show Google Find Hub state", status_cmd, 1, 0),
    SHELL_SUBCMD_SET_END);
SHELL_CMD_REGISTER(findhub, &commands, "Google Find Hub locator", NULL);

int main(void)
{
    int err = adc_is_ready_dt(&battery) ? adc_channel_setup_dt(&battery) : -ENODEV;
    if (!err)
        err = registration_present();
    if (!err)
        err = rak_findhub_start();
    atomic_set(&startup_err, err);
#ifndef RAK_NATIVE_PINGS
    tag_mesh_start();
#endif
    return 0;
}
