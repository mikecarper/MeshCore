/* Shared Bluetooth host for the three provisioned RAK tracker services. */
#include <errno.h>
#include <string.h>
#include <bt_app_callbacks.h>
#include <fmna.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/kernel.h>
#include <zephyr/settings/settings.h>
#include <zephyr/shell/shell.h>
#include "native_pings.h"

BUILD_ASSERT((int)BT_ID_SIDEWALK == (int)RAK_SIDEWALK_ID);
BUILD_ASSERT(CONFIG_BT_ID_MAX > RAK_APPLE_ID && CONFIG_BT_EXT_ADV_MAX_ADV_SET >= 4);

extern int apple_entry(void), findhub_entry(void), sidewalk_entry(void);
extern bool rak_sidewalk_running(void);
extern void tag_mesh_start(void);

static K_MUTEX_DEFINE(settings_lock);
static bool loaded;
static atomic_t host_err = ATOMIC_INIT(-EINPROGRESS);
static atomic_t next_mask = ATOMIC_INIT(7);
static atomic_t boot_mask;
enum { DEFAULT_INTERVAL_S = 10, MAX_INTERVAL_S = 300 };
static atomic_t next_interval_s = ATOMIC_INIT(DEFAULT_INTERVAL_S);
static atomic_t boot_interval_s = ATOMIC_INIT(DEFAULT_INTERVAL_S);
static K_MUTEX_DEFINE(selection_lock);
static const struct bt_gatt_authorization_cb *android_gatt;
static K_MUTEX_DEFINE(android_lock);

int __real_settings_load(void);
int __wrap_settings_load(void)
{
    k_mutex_lock(&settings_lock, K_FOREVER);
    int err = loaded ? 0 : __real_settings_load();
    if (!err) loaded = true;
    k_mutex_unlock(&settings_lock);
    return err;
}

int rak_host_ready(void) { return atomic_get(&host_err); }

int rak_register_android_gatt(const struct bt_gatt_authorization_cb *cb)
{
    k_mutex_lock(&android_lock, K_FOREVER);
    android_gatt = cb;
    k_mutex_unlock(&android_lock);
    return 0;
}

static uint8_t record_service(const struct bt_gatt_attr *attr, uint16_t handle, void *context)
{
    const struct bt_uuid **service = context;
    *service = attr->user_data;
    return BT_GATT_ITER_CONTINUE;
}

static bool allow_attr(struct bt_conn *conn, const struct bt_gatt_attr *attr, bool write)
{
    struct bt_conn_info info;
    if (bt_conn_get_info(conn, &info)) return false;
    const struct bt_uuid *service = NULL;
    uint16_t handle = bt_gatt_attr_get_handle(attr);
    if (!handle) return false;
    bt_gatt_foreach_attr_type(1, handle, BT_UUID_GATT_PRIMARY, NULL, 0, record_service, &service);
    if (!service) return false;
    bool common = !bt_uuid_cmp(service, BT_UUID_GAP) || !bt_uuid_cmp(service, BT_UUID_GATT);
    switch (info.id) {
    case RAK_APPLE_ID:
        return fmna_is_ready() && (common || !bt_uuid_cmp(service, BT_UUID_DECLARE_16(0xfd44)) ||
            !bt_uuid_cmp(service, BT_UUID_DECLARE_128(BT_UUID_128_ENCODE(
                0x87290102, 0x3c51, 0x43b1, 0xa1a9, 0x11b9dc38478b))));
    case RAK_ANDROID_ID: {
        if (!common && bt_uuid_cmp(service, BT_UUID_DIS) &&
            bt_uuid_cmp(service, BT_UUID_DECLARE_16(0xfe2c)) &&
            bt_uuid_cmp(service, BT_UUID_DECLARE_128(BT_UUID_128_ENCODE(
                0x15190001, 0x12f4, 0xc226, 0x88ed, 0x2ac5579f2a85)))) return false;
        k_mutex_lock(&android_lock, K_FOREVER);
        const struct bt_gatt_authorization_cb *cb = android_gatt;
        k_mutex_unlock(&android_lock);
        return cb && (write ? cb->write_authorize(conn, attr) : cb->read_authorize(conn, attr));
    }
    case RAK_SIDEWALK_ID:
        return rak_sidewalk_running() && (common || !bt_uuid_cmp(service, BT_UUID_DECLARE_16(0xfe03)));
    default:
        return false;
    }
}

static bool allow_read(struct bt_conn *conn, const struct bt_gatt_attr *attr)
{
    return allow_attr(conn, attr, false);
}

static bool allow_write(struct bt_conn *conn, const struct bt_gatt_attr *attr)
{
    return allow_attr(conn, attr, true);
}

static const struct bt_gatt_authorization_cb auth = {
    .read_authorize = allow_read, .write_authorize = allow_write
};

uint32_t rak_ping_interval_units(void)
{
    // The LE extended advertising command encodes intervals in 625 us units.
    return (uint32_t)atomic_get(&boot_interval_s) * 1600U;
}

static int load_ping_setting(const char *key, size_t len, settings_read_cb read, void *ctx)
{
    if (!strcmp(key, "interval")) {
        uint16_t seconds;
        if (len != sizeof(seconds) || read(ctx, &seconds, len) != sizeof(seconds) ||
            !seconds || seconds > MAX_INTERVAL_S) return -EINVAL;
        atomic_set(&next_interval_s, seconds);
        return 0;
    }
    if (strcmp(key, "enabled")) return -ENOENT;
    uint8_t mask;
    if (len != sizeof(mask) || read(ctx, &mask, sizeof(mask)) != sizeof(mask) || mask > 7)
        return -EINVAL;
    atomic_set(&next_mask, mask);
    return 0;
}
SETTINGS_STATIC_HANDLER_DEFINE(rak_pings, "rak_pings", NULL, load_ping_setting, NULL, NULL);

static void show_interval(const struct shell *sh)
{
    shell_print(sh, "Ping interval: %u s on this boot, %u s on next boot",
                (unsigned)atomic_get(&boot_interval_s), (unsigned)atomic_get(&next_interval_s));
}

static unsigned parse_interval(const char *text)
{
    unsigned seconds = 0;
    for (; *text; ++text) {
        if (*text < '0' || *text > '9') return 0;
        seconds = seconds * 10U + (*text - '0');
        if (seconds > MAX_INTERVAL_S) return 0;
    }
    return seconds;
}

static int interval_cmd(const struct shell *sh, size_t argc, char **argv)
{
    uint16_t seconds = 0;
    if (argc == 2) {
        int err = rak_host_ready();
        if (err) return err;
        seconds = parse_interval(argv[1]);
        if (!seconds) {
            shell_error(sh, "Enter a whole number of seconds from 1 to %d", MAX_INTERVAL_S);
            return -EINVAL;
        }
    }
    k_mutex_lock(&selection_lock, K_FOREVER);
    int err = 0;
    if (argc == 2 && atomic_get(&next_interval_s) != seconds) {
        err = settings_save_one("rak_pings/interval", &seconds, sizeof(seconds));
        if (!err) atomic_set(&next_interval_s, seconds);
    }
    if (!err) show_interval(sh);
    k_mutex_unlock(&selection_lock);
    if (!err && argc == 2 && seconds != atomic_get(&boot_interval_s)) shell_print(sh, "Reboot to apply.");
    return err;
}

static int configure(const struct shell *sh, size_t argc, char **argv)
{
    if (argc == 2 && atomic_get(&host_err)) return atomic_get(&host_err);
    if (argc == 2) {
        int bit;
        if (!strcmp(argv[1], "all")) bit = 7;
        else if (strlen(argv[1]) == 1 && argv[1][0] >= 'A' && argv[1][0] <= 'C')
            bit = BIT(argv[1][0] - 'A');
        else return -EINVAL;
        k_mutex_lock(&selection_lock, K_FOREVER);
        uint8_t old = atomic_get(&next_mask);
        uint8_t mask = !strcmp(argv[0], "on") ? old | bit : old & ~bit;
        int err = mask == old ? 0 : settings_save_one("rak_pings/enabled", &mask, sizeof(mask));
        if (!err) atomic_set(&next_mask, mask);
        k_mutex_unlock(&selection_lock);
        if (err) return err;
        shell_print(sh, "Selection saved. Reboot to apply.");
    }
    show_interval(sh);
    shell_print(sh, "Bluetooth error=%ld. Selected on this boot / next boot:", (long)atomic_get(&host_err));
    for (int i = 0; i < 3; ++i) {
        shell_print(sh, "%c %s / %s", 'A' + i, (atomic_get(&boot_mask) & BIT(i)) ? "on" : "off",
                    (atomic_get(&next_mask) & BIT(i)) ? "on" : "off");
    }
    return 0;
}
SHELL_STATIC_SUBCMD_SET_CREATE(pings_commands,
    SHELL_CMD_ARG(interval, NULL, "Show or set heartbeat interval in seconds (1-300)", interval_cmd, 1, 1),
    SHELL_CMD_ARG(status, NULL, "Show startup selection", configure, 1, 0),
    SHELL_CMD_ARG(on, NULL, "Enable A, B, C or all on next boot", configure, 2, 0),
    SHELL_CMD_ARG(off, NULL, "Disable A, B, C or all on next boot", configure, 2, 0),
    SHELL_SUBCMD_SET_END);
SHELL_CMD_REGISTER(pings, &pings_commands, "Native heartbeat selection", NULL);

static int prepare_host(void)
{
    int err = bt_gatt_authorization_cb_register(&auth);
    // Hold one reference for the whole boot so Sidewalk can cycle its own BLE link.
    if (!err) err = sid_ble_bt_enable(NULL);
    if (!err) err = settings_load();
    if (err) return err;
    bt_addr_le_t addresses[CONFIG_BT_ID_MAX];
    size_t count = ARRAY_SIZE(addresses);
    bt_id_get(addresses, &count);
    if (!count) return -EIO;
    for (int id = 1; id <= RAK_APPLE_ID; ++id) {
        if (id < count && !bt_addr_le_eq(&addresses[id], BT_ADDR_LE_ANY)) continue;
        int allocated = id < count ? bt_id_reset(id, NULL, NULL) : bt_id_create(NULL, NULL);
        if (allocated != id) return allocated < 0 ? allocated : -EIO;
    }
    return 0;
}

static void apple_thread(void *a, void *b, void *c) { (void)apple_entry(); }
static void sidewalk_thread(void *a, void *b, void *c) { (void)sidewalk_entry(); }
K_THREAD_DEFINE(apple_worker, 4096, apple_thread, NULL, NULL, NULL, 6, 0, SYS_FOREVER_MS);
K_THREAD_DEFINE(sidewalk_worker, 8192, sidewalk_thread, NULL, NULL, NULL, 6, 0, SYS_FOREVER_MS);

int main(void)
{
    int err = prepare_host();
    atomic_set(&boot_interval_s, atomic_get(&next_interval_s));
    atomic_set(&host_err, err);
    tag_mesh_start();
    if (err) return err;
    int mask = atomic_get(&next_mask);
    atomic_set(&boot_mask, mask);
    if (mask & BIT(0)) k_thread_start(apple_worker);
    if (mask & BIT(2)) k_thread_start(sidewalk_worker);
    if (mask & BIT(1)) (void)findhub_entry();
    return 0;
}
