// Inspect only advertising commands issued by this tag's native finder SDK.
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/shell/shell.h>
#include <zephyr/sys/atomic.h>
#include <zephyr/sys/byteorder.h>

static atomic_t native_payload[CONFIG_BT_EXT_ADV_MAX_ADV_SET];
enum payload_kind { OTHER, APPLE, ANDROID, SIDEWALK };
#ifdef RAK_NATIVE_PINGS
#include "native_pings.h"
static atomic_t accepted_updates[3], accepted_starts[3];
static atomic_ptr_t advertisers[CONFIG_BT_EXT_ADV_MAX_ADV_SET];
#else
static atomic_t accepted_updates[1], accepted_starts[1];
#endif

static size_t counter_index(enum payload_kind kind)
{
    return ARRAY_SIZE(accepted_updates) == 1 ? 0 : (size_t)kind - 1;
}

static enum payload_kind classify(const struct bt_data *ad, size_t count)
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
            return APPLE; // Find My separated-state frame.
#if defined(CONFIG_BT_FAST_PAIR_FHN)
        // UUID, frame type, configured ephemeral identifier and hashed flags.
        if (ad[i].type == BT_DATA_SVC_DATA16 && id == 0xfeaa &&
            len == 4 + CONFIG_BT_FAST_PAIR_FHN_ECC_LEN &&
            (data[2] == 0x40 || data[2] == 0x41))
            return ANDROID;
#endif
        if (IS_ENABLED(CONFIG_SIDEWALK)) {
            if (ad[i].type == BT_DATA_UUID16_ALL && len == 2 && id == 0xfe03)
                sidewalk_service = true;
            if (ad[i].type == BT_DATA_MANUFACTURER_DATA && len > 2 && id == 0x0171)
                amazon_data = true;
        }
    }
    return sidewalk_service && amazon_data ? SIDEWALK : OTHER;
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
        enum payload_kind kind = classify(ad, count);
        atomic_set(&native_payload[bt_le_ext_adv_get_index(adv)], kind);
        if (kind != OTHER) atomic_inc(&accepted_updates[counter_index(kind)]);
    }
    return err;
}

int __wrap_bt_le_ext_adv_start(struct bt_le_ext_adv *adv,
                               const struct bt_le_ext_adv_start_param *param)
{
    int err = __real_bt_le_ext_adv_start(adv, param);
    if (!err) {
        enum payload_kind kind = atomic_get(&native_payload[bt_le_ext_adv_get_index(adv)]);
        if (kind != OTHER) atomic_inc(&accepted_starts[counter_index(kind)]);
    }
    return err;
}

int __wrap_bt_le_ext_adv_create(const struct bt_le_adv_param *param, const struct bt_le_ext_adv_cb *cb,
                                struct bt_le_ext_adv **adv)
{
    int err = __real_bt_le_ext_adv_create(param, cb, adv);
    if (!err) {
        uint8_t index = bt_le_ext_adv_get_index(*adv);
        atomic_clear(&native_payload[index]);
#ifdef RAK_NATIVE_PINGS
        atomic_ptr_set(&advertisers[index], *adv);
#endif
    }
    return err;
}

#ifdef RAK_NATIVE_PINGS
static bool currently_advertising(enum payload_kind kind)
{
    static const uint8_t ids[] = {0, RAK_APPLE_ID, RAK_ANDROID_ID, RAK_SIDEWALK_ID};
    for (size_t i = 0; i < ARRAY_SIZE(advertisers); ++i) {
        if (atomic_get(&native_payload[i]) != kind) continue;
        // Zephyr allocates handles in a static pool. It checks the slot is
        // allocated when queried, including after a connection or timeout.
        struct bt_le_ext_adv *adv = atomic_ptr_get(&advertisers[i]);
        struct bt_le_ext_adv_info info;
        if (adv && !bt_le_ext_adv_get_info(adv, &info) && info.id == ids[kind] &&
            info.ext_adv_state == BT_LE_EXT_ADV_STATE_ENABLED && atomic_get(&native_payload[i]) == kind)
            return true;
    }
    return false;
}
#endif

static int ping_status(const struct shell *sh, size_t argc, char **argv)
{
    static const char *const labels[] = {"A Apple separated", "B Android Find Hub", "C Sidewalk BLE"};
    for (enum payload_kind kind = APPLE; kind <= SIDEWALK; ++kind) {
        if ((kind == APPLE && !IS_ENABLED(CONFIG_FMNA)) ||
            (kind == ANDROID && !IS_ENABLED(CONFIG_BT_FAST_PAIR_FHN)) ||
            (kind == SIDEWALK && !IS_ENABLED(CONFIG_SIDEWALK))) continue;
        size_t index = counter_index(kind);
#ifdef RAK_NATIVE_PINGS
        shell_print(sh, "%s advertising=%s payload_updates=%ld advertising_starts=%ld since boot",
                    labels[kind - 1], currently_advertising(kind) ? "on" : "off",
#else
        shell_print(sh, "%s payload_updates=%ld advertising_starts=%ld since boot", labels[kind - 1],
#endif
                    (long)atomic_get(&accepted_updates[index]), (long)atomic_get(&accepted_starts[index]));
    }
    return 0;
}

SHELL_STATIC_SUBCMD_SET_CREATE(ping_commands,
    SHELL_CMD_ARG(ping, NULL, "Show native Bluetooth advertising commands accepted since boot", ping_status, 1, 0),
    SHELL_SUBCMD_SET_END);
SHELL_CMD_REGISTER(finder, &ping_commands, "Native finder diagnostics", NULL);
