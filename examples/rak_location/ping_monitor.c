// Inspect only advertising commands issued by this tag's native finder SDK.
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/shell/shell.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/byteorder.h>

static atomic_t native_payload[CONFIG_BT_EXT_ADV_MAX_ADV_SET];
static atomic_t accepted_updates;
static atomic_t accepted_starts;

static bool matches_native_payload(const struct bt_data *ad, size_t count)
{
    bool sidewalk_service = false;
    bool amazon_data = false;
    for (size_t i = 0; i < count; ++i) {
        const uint8_t *data = ad[i].data;
        size_t len = ad[i].data_len;
        if (len < 2) continue;
        uint16_t id = sys_get_le16(data);
        if (IS_ENABLED(CONFIG_FMNA) && ad[i].type == BT_DATA_MANUFACTURER_DATA &&
            id == 0x004c && len == 29 && data[2] == 0x12 && data[3] == 25)
            return true; // Find My separated-state frame.
#if defined(CONFIG_BT_FAST_PAIR_FHN)
        // UUID, frame type, configured ephemeral identifier and hashed flags.
        if (ad[i].type == BT_DATA_SVC_DATA16 && id == 0xfeaa &&
            len == 4 + CONFIG_BT_FAST_PAIR_FHN_ECC_LEN &&
            (data[2] == 0x40 || data[2] == 0x41))
            return true;
#endif
        if (IS_ENABLED(CONFIG_SIDEWALK)) {
            if (ad[i].type == BT_DATA_UUID16_ALL && len == 2 && id == 0xfe03)
                sidewalk_service = true;
            if (ad[i].type == BT_DATA_MANUFACTURER_DATA && len > 2 && id == 0x0171)
                amazon_data = true;
        }
    }
    return sidewalk_service && amazon_data;
}

int __real_bt_le_ext_adv_set_data(struct bt_le_ext_adv *adv, const struct bt_data *ad,
                                  size_t count, const struct bt_data *sd, size_t sd_count);
int __real_bt_le_ext_adv_start(struct bt_le_ext_adv *adv,
                               const struct bt_le_ext_adv_start_param *param);
int __real_bt_le_ext_adv_create(const struct bt_le_adv_param *param, const struct bt_le_ext_adv_cb *cb,
                                struct bt_le_ext_adv **adv);

int __wrap_bt_le_ext_adv_set_data(struct bt_le_ext_adv *adv, const struct bt_data *ad,
                                  size_t count, const struct bt_data *sd, size_t sd_count)
{
    int err = __real_bt_le_ext_adv_set_data(adv, ad, count, sd, sd_count);
    if (!err) {
        bool matched = matches_native_payload(ad, count);
        atomic_set(&native_payload[bt_le_ext_adv_get_index(adv)], matched);
        if (matched) atomic_inc(&accepted_updates);
    }
    return err;
}

int __wrap_bt_le_ext_adv_start(struct bt_le_ext_adv *adv,
                               const struct bt_le_ext_adv_start_param *param)
{
    int err = __real_bt_le_ext_adv_start(adv, param);
    if (!err && atomic_get(&native_payload[bt_le_ext_adv_get_index(adv)]))
        atomic_inc(&accepted_starts);
    return err;
}

int __wrap_bt_le_ext_adv_create(const struct bt_le_adv_param *param, const struct bt_le_ext_adv_cb *cb,
                                struct bt_le_ext_adv **adv)
{
    int err = __real_bt_le_ext_adv_create(param, cb, adv);
    if (!err) atomic_clear(&native_payload[bt_le_ext_adv_get_index(*adv)]);
    return err;
}

static int ping_status(const struct shell *sh, size_t argc, char **argv)
{
    const char *profile = IS_ENABLED(CONFIG_FMNA) ? "Apple separated" :
                          IS_ENABLED(CONFIG_BT_FAST_PAIR_FHN) ? "Android Find Hub" : "Sidewalk BLE";
    shell_print(sh, "%s payload_updates=%ld advertising_starts=%ld since boot", profile,
                (long)atomic_get(&accepted_updates), (long)atomic_get(&accepted_starts));
    return 0;
}

SHELL_STATIC_SUBCMD_SET_CREATE(ping_commands,
    SHELL_CMD_ARG(ping, NULL, "Show native Bluetooth advertising commands accepted since boot", ping_status, 1, 0),
    SHELL_SUBCMD_SET_END);
SHELL_CMD_REGISTER(finder, &ping_commands, "Native finder diagnostics", NULL);
