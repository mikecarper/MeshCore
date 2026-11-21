#include <errno.h>
#include <string.h>
#include <zephyr/kernel.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/settings/settings.h>
#include <zephyr/shell/shell.h>
#include "controller.h"

static atomic_t bt_error = ATOMIC_INIT(-EINPROGRESS);
enum { beacon_count = 3 };
BUILD_ASSERT(CONFIG_BT_ID_MAX > beacon_count);
BUILD_ASSERT(CONFIG_BT_EXT_ADV_MAX_ADV_SET >= beacon_count);
static const char names[beacon_count][sizeof("RAK-TX-A")] = {"RAK-TX-A", "RAK-TX-B", "RAK-TX-C"};
static struct bt_le_ext_adv *sets[beacon_count];
static bool transmitting[beacon_count];
static K_MUTEX_DEFINE(check_lock);

static int release_sets(void)
{
    int result = 0;
    for (size_t i = 0; i < ARRAY_SIZE(sets); ++i) {
        if (!sets[i]) continue;
        if (transmitting[i]) {
            int err = bt_le_ext_adv_stop(sets[i]);
            if (err) {
                result = err;
                continue;
            }
            transmitting[i] = false;
        }
        int err = bt_le_ext_adv_delete(sets[i]);
        if (err) result = err;
        else sets[i] = NULL;
    }
    return result;
}

static int controller_limit(uint8_t *maximum)
{
    struct net_buf *rsp = NULL;
    int err = bt_hci_cmd_send_sync(BT_HCI_OP_LE_READ_NUM_ADV_SETS, NULL, &rsp);
    if (err) return err;
    if (!rsp) return -EIO;

    const struct bt_hci_rp_le_read_num_adv_sets *capacity = (const void *)rsp->data;
    if (rsp->len < sizeof(*capacity) || capacity->status) err = -EIO;
    else *maximum = capacity->num_sets;
    net_buf_unref(rsp);
    return err;
}

static size_t active_count(void)
{
    size_t count = 0;
    for (size_t i = 0; i < ARRAY_SIZE(sets); ++i) count += transmitting[i];
    return count;
}

static int ensure_identities(void)
{
    bt_addr_le_t addrs[beacon_count + 1];
    size_t count = ARRAY_SIZE(addrs);
    bt_id_get(addrs, &count);
    if (!count) return -EAGAIN;

    // The bench has its own settings partition. Slot zero belongs to the host.
    for (size_t id = 1; id <= beacon_count; ++id) {
        if (id < count && !bt_addr_le_eq(&addrs[id], BT_ADDR_LE_ANY)) continue;
        int result = id < count ? bt_id_reset(id, NULL, NULL) : bt_id_create(NULL, NULL);
        if (result < 0) return result;
        if ((size_t)result != id) return -EIO;
    }
    return 0;
}

static int start_beacon(size_t slot)
{
    struct bt_le_adv_param param = *BT_LE_ADV_NCONN_IDENTITY;
    param.id = slot + 1;
    param.interval_min = 160; // 100 to 150 ms, legacy packets visible to ordinary scanners.
    param.interval_max = 240;
    const struct bt_data data[] = {
        BT_DATA_BYTES(BT_DATA_FLAGS, BT_LE_AD_NO_BREDR),
        BT_DATA(BT_DATA_NAME_COMPLETE, names[slot], sizeof(names[slot]) - 1),
    };

    int err = bt_le_ext_adv_create(&param, NULL, &sets[slot]);
    if (!err) err = bt_le_ext_adv_set_data(sets[slot], data, ARRAY_SIZE(data), NULL, 0);
    if (!err) err = bt_le_ext_adv_start(sets[slot], BT_LE_EXT_ADV_START_DEFAULT);
    if (!err) transmitting[slot] = true;
    return err;
}

static int start_beacons(const struct shell *sh)
{
    if (active_count() == beacon_count) return 0;
    int err = release_sets();
    if (err) return err;
    err = ensure_identities();
    if (err) return err;
    for (size_t i = 0; i < ARRAY_SIZE(sets); ++i) {
        err = start_beacon(i);
        if (err) {
            int cleanup = release_sets();
            if (cleanup) shell_error(sh, "Cleanup failed (%d). Retry locator ids stop", cleanup);
            return err;
        }
    }
    return 0;
}

static void show_beacons(const struct shell *sh)
{
    bt_addr_le_t addrs[beacon_count + 1];
    size_t count = ARRAY_SIZE(addrs);
    bt_id_get(addrs, &count);
    for (size_t i = 0; i < ARRAY_SIZE(sets); ++i) {
        char address[BT_ADDR_LE_STR_LEN] = "unassigned";
        if (i + 1 < count && !bt_addr_le_eq(&addrs[i + 1], BT_ADDR_LE_ANY))
            bt_addr_le_to_str(&addrs[i + 1], address, sizeof(address));
        shell_print(sh, "%s  %s  %s", names[i], address, transmitting[i] ? "on" : "off");
    }
}

static int ids_command(const struct shell *sh, size_t argc, char **argv)
{
    int err = atomic_get(&bt_error);
    if (err) {
        shell_error(sh, "Bluetooth startup status %d", err);
        return err;
    }
    if (k_mutex_lock(&check_lock, K_NO_WAIT)) return -EBUSY;
    const char *action = argc == 1 ? "status" : argv[1];
    if (!strcmp(action, "start")) {
        err = start_beacons(sh);
    } else if (!strcmp(action, "stop")) {
        err = release_sets();
    } else if (strcmp(action, "status")) {
        shell_error(sh, "Use locator ids start, stop or status");
        err = -EINVAL;
    }
    show_beacons(sh);
    k_mutex_unlock(&check_lock);
    if (err) shell_error(sh, "BLE identity command failed (%d)", err);
    return err;
}

static int check_controller(const struct shell *sh, size_t argc, char **argv)
{
    int err = atomic_get(&bt_error);
    if (err) {
        shell_error(sh, "Bluetooth startup status %d", err);
        return err;
    }
    if (k_mutex_lock(&check_lock, K_NO_WAIT)) return -EBUSY;

    if (active_count()) {
        k_mutex_unlock(&check_lock);
        shell_error(sh, "Stop the beacons with locator ids stop before checking capacity");
        return -EBUSY;
    }
    uint8_t maximum = 0;
    err = release_sets(); // Retry any failed cleanup before allocating again.
    if (!err) err = controller_limit(&maximum);
    if (!err) {
        size_t count = 0;
        for (; count < ARRAY_SIZE(sets); ++count) {
            err = bt_le_ext_adv_create(BT_LE_EXT_ADV_NCONN, NULL, &sets[count]);
            if (err) break;
        }
        shell_print(sh, "Controller limit %u; allocated %zu of 3 idle advertising sets", maximum, count);
        int cleanup = release_sets();
        if (!err) err = cleanup;
    }

    k_mutex_unlock(&check_lock);
    if (err) shell_error(sh, "Controller check failed (%d)", err);
    return err;
}

SHELL_STATIC_SUBCMD_SET_CREATE(locator_commands,
    SHELL_CMD_ARG(ids, NULL, "Show, start or stop the three named BLE identities", ids_command, 1, 1),
    SHELL_CMD_ARG(capacity, NULL, "Query Bluetooth and allocate three idle sets", check_controller, 1, 0),
    SHELL_SUBCMD_SET_END);
SHELL_CMD_REGISTER(locator, &locator_commands, "RAK controller bring-up", NULL);

int rak_locator_controller_init(void)
{
    int err = bt_enable(NULL);
    if (!err) err = settings_load();
    atomic_set(&bt_error, err);
    return err;
}
