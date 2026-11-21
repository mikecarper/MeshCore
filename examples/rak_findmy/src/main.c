#include <errno.h>
#include <string.h>
#include <fmna.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/drivers/gpio.h>
#include <zephyr/drivers/pwm.h>
#include <zephyr/kernel.h>
#include <zephyr/settings/settings.h>
#include <zephyr/shell/shell.h>
#ifdef CONFIG_USB_DEVICE_STACK
#include <zephyr/usb/usb_device.h>
#endif

extern bool tag_has_product_plan(void);
extern void tag_mesh_start(void);
extern bool tag_motion_present(void);
extern void tag_motion_start(void);
extern bool tag_motion_detected(void);
extern void tag_motion_stop(void);

static const struct gpio_dt_spec button = GPIO_DT_SPEC_GET(DT_NODELABEL(tag_button), gpios);
static const struct gpio_dt_spec green = GPIO_DT_SPEC_GET(DT_NODELABEL(green_led), gpios);
static const struct pwm_dt_spec buzzer = PWM_DT_SPEC_GET(DT_NODELABEL(tag_buzzer));
static const struct adc_dt_spec battery = ADC_DT_SPEC_GET(DT_PATH(zephyr_user));

static atomic_t hardware_ready, paired, pairing, uuid_present, token_present;
static atomic_t last_error;
static K_MUTEX_DEFINE(buzzer_lock);
static bool notify_sound_end;
static int64_t sound_deadline;
static void finish_sound(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(sound_timer, finish_sound);

extern int tag_battery_mv(void);

static uint8_t battery_percent(int mv)
{
    return CLAMP((mv - 3200) / 10, 0, 100);
}

static void update_battery(void)
{
    int mv = tag_battery_mv();
    if (mv >= 0) fmna_battery_level_set(battery_percent(mv));
}

static void finish_sound(struct k_work *work)
{
    ARG_UNUSED(work);
    k_mutex_lock(&buzzer_lock, K_FOREVER);
    if (sound_deadline) {
        int64_t left = sound_deadline - k_uptime_get();
        if (left > 0) {
            k_work_reschedule(&sound_timer, K_MSEC(left));
        } else {
            pwm_set_pulse_dt(&buzzer, 0);
            sound_deadline = 0;
            bool report = notify_sound_end;
            notify_sound_end = false;
            k_mutex_unlock(&buzzer_lock);
            if (report) fmna_sound_completed_indicate();
            return;
        }
    }
    k_mutex_unlock(&buzzer_lock);
}

static int play_sound(bool network)
{
    k_mutex_lock(&buzzer_lock, K_FOREVER);
    int err = 0;
    if (!network && notify_sound_end) err = -EBUSY;
    else if ((err = pwm_set_pulse_dt(&buzzer, buzzer.period / 2)) == 0) {
        notify_sound_end = network;
        int duration_ms = network ? 5000 : 500;
        sound_deadline = k_uptime_get() + duration_ms;
        k_work_reschedule(&sound_timer, K_MSEC(duration_ms));
    }
    k_mutex_unlock(&buzzer_lock);
    return err;
}

static void sound_start(enum fmna_sound_trigger trigger)
{
    ARG_UNUSED(trigger);
    int err = play_sound(true);
    if (err) {
        atomic_set(&last_error, err);
        fmna_sound_completed_indicate();
    }
}

static void sound_stop(void)
{
    k_mutex_lock(&buzzer_lock, K_FOREVER);
    sound_deadline = 0;
    notify_sound_end = false;
    k_work_cancel_delayable(&sound_timer);
    pwm_set_pulse_dt(&buzzer, 0);
    k_mutex_unlock(&buzzer_lock);
}

static void state_changed(bool is_paired)
{
    atomic_set(&paired, is_paired);
    if (is_paired) atomic_clear(&pairing);
    gpio_pin_set_dt(&green, is_paired);
}

static void pairing_exited(void) { atomic_clear(&pairing); }
static void pairing_failed(void) { atomic_set(&last_error, -EACCES); }

static const struct fmna_enable_cb events = {
    .paired_state_changed = state_changed, .battery_level_request = update_battery,
    .pairing_failed = pairing_failed, .pairing_mode_exited = pairing_exited,
};
static const struct fmna_sound_cb sounds = { .sound_start = sound_start, .sound_stop = sound_stop };
static const struct fmna_motion_detection_cb motion = {
    .motion_detection_start = tag_motion_start,
    .motion_detection_period_expired = tag_motion_detected,
    .motion_detection_stop = tag_motion_stop,
};

// Inspect only the presence and expected size of the SDK's provisioning
// records. The SDK alone reads and authenticates their contents.
static int provisioned(const char *key, size_t len, settings_read_cb read,
                       void *arg, void *unused)
{
    ARG_UNUSED(read); ARG_UNUSED(arg); ARG_UNUSED(unused);
    if (key && strcmp(key, "998") == 0 && len == 16) atomic_set(&uuid_present, 1);
    if (key && strcmp(key, "999") == 0 && len == 1024) atomic_set(&token_present, 1);
    return 0;
}

static int start_accessory(int mv)
{
    int err = bt_enable(NULL);
    if (!err) err = settings_load();
    if (!err) err = settings_load_subtree_direct("fmna/provisioning", provisioned, NULL);
    if (err) return err;
    if (!tag_has_product_plan() || !atomic_get(&uuid_present)
        || !atomic_get(&token_present)) return -ENODATA;
    size_t count = 0;
    bt_id_get(NULL, &count);
    if (count < 2) {
        int id = bt_id_create(NULL, NULL);
        if (id != 1) return id < 0 ? id : -EINVAL;
    }
    err = fmna_sound_cb_register(&sounds);
    if (!err) err = fmna_motion_detection_cb_register(&motion);
    const struct fmna_enable_param params = {
        .bt_id = 1, .init_battery_level = battery_percent(mv),
        .use_default_factory_settings = false
    };
    return err ? err : fmna_enable(&params, &events);
}

static int enter_pairing(void)
{
    if (!fmna_is_ready()) return atomic_get(&last_error) ?: -EAGAIN;
    if (atomic_get(&paired)) return -EALREADY;
    int err = fmna_resume();
    if (!err) atomic_set(&pairing, 1);
    return err;
}

static int cmd_status(const struct shell *sh, size_t argc, char **argv)
{
    ARG_UNUSED(argc); ARG_UNUSED(argv);
    shell_print(sh, "plan=%s uuid=%s token=%s ready=%d paired=%ld pairing=%ld error=%ld",
                tag_has_product_plan() ? "configured" : "absent",
                atomic_get(&uuid_present) ? "present" : "absent",
                atomic_get(&token_present) ? "present" : "absent", fmna_is_ready(),
                (long)atomic_get(&paired), (long)atomic_get(&pairing), (long)atomic_get(&last_error));
    if (atomic_get(&hardware_ready))
        shell_print(sh, "battery_mV=%d accelerometer=%s", tag_battery_mv(),
                    tag_motion_present() ? "present" : "unavailable");
    return 0;
}

static int cmd_sound(const struct shell *sh, size_t argc, char **argv)
{
    ARG_UNUSED(argc); ARG_UNUSED(argv);
    int err = atomic_get(&hardware_ready) ? play_sound(false) : -EAGAIN;
    if (err) shell_error(sh, "Buzzer unavailable (%d)", err);
    return err;
}

static int cmd_pair(const struct shell *sh, size_t argc, char **argv)
{
    ARG_UNUSED(argc); ARG_UNUSED(argv);
    int err = enter_pairing();
    if (err == -ENODATA) shell_error(sh, "Find My requires a product plan and device provisioning");
    else if (err) shell_error(sh, "Pairing unavailable (%d)", err);
    else shell_print(sh, "Open Find My > Items > Add Other Item");
    return err;
}

SHELL_STATIC_SUBCMD_SET_CREATE(tag_commands,
    SHELL_CMD(status, NULL, "Board and Find My state", cmd_status),
    SHELL_CMD(sound, NULL, "Test the local buzzer", cmd_sound),
    SHELL_CMD(pair, NULL, "Open the Find My pairing window", cmd_pair),
    SHELL_SUBCMD_SET_END);
SHELL_CMD_REGISTER(tag, &tag_commands, "RAK WisMesh Tag", NULL);

int main(void)
{
    // The tracker also boots on battery with no USB host.
#ifdef CONFIG_USB_DEVICE_STACK
    (void)usb_enable(NULL);
#endif
    int err = 0;
    if (!gpio_is_ready_dt(&green) || !gpio_is_ready_dt(&button)
        || !device_is_ready(buzzer.dev) || !device_is_ready(battery.dev)) err = -ENODEV;
    if (!err) err = gpio_pin_configure_dt(&green, GPIO_OUTPUT_INACTIVE);
    if (!err) err = gpio_pin_configure_dt(&button, GPIO_INPUT);
    if (!err) err = adc_channel_setup_dt(&battery);
    int mv = err ? err : tag_battery_mv();
    if (mv < 0) { atomic_set(&last_error, mv); return mv; }
    atomic_set(&hardware_ready, 1);
    if (!tag_motion_present()) err = -ENODEV;
    if (!err) err = start_accessory(mv);
    atomic_set(&last_error, err);
    tag_mesh_start();
    // Only a deliberate press after boot opens the pairing window.
    bool released = false;
    unsigned down_ticks = 0;
    while (true) {
        k_sleep(K_MSEC(25));
        int down = gpio_pin_get_dt(&button);
        if (down == 0) { down_ticks = 0; released = true; }
        else if (down > 0 && released && ++down_ticks == 3) {
            released = false;
            if (fmna_is_ready()) {
                if (atomic_get(&paired)) fmna_serial_number_lookup_enable();
                else enter_pairing();
            } else play_sound(false);
        }
    }
}
