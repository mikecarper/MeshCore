#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/conn.h>
#include <zephyr/bluetooth/gatt.h>
#include <zephyr/random/random.h>
#include <zephyr/settings/settings.h>
#include "native_pings.h"
#include "pet.h"

#define HIKE_UUID(n) BT_UUID_128_ENCODE(0xcaa50000U + (n), 0xa471, 0x47ba, 0x898a, 0xf85af2612101)
static const struct bt_uuid_128 service = BT_UUID_INIT_128(HIKE_UUID(1));
static const struct bt_uuid_128 claim = BT_UUID_INIT_128(HIKE_UUID(2));
struct config { bt_addr_le_t phones[2]; uint16_t timeout; uint8_t enabled; };
static const char key[] = "tagmesh/hike1";
static struct config cfg = {.timeout = 180};
static K_MUTEX_DEFINE(lock);
static unsigned slot, code, attempts, present;
static int64_t expires, away_since = -1, poll_at;
static bool lost;
static int adv_error;
static struct bt_le_ext_adv *adv;
static atomic_t advertising;

bool rak_hike_service(const struct bt_uuid *uuid) { return !bt_uuid_cmp(uuid, &service.uuid); }
static bool filled(unsigned i) { return !bt_addr_le_eq(&cfg.phones[i], BT_ADDR_LE_ANY); }
static int owner(const bt_addr_le_t *addr)
{
    for (unsigned i = 0; i < 2; ++i)
        if (filled(i) && bt_addr_le_eq(addr, &cfg.phones[i])) return i;
    return -1;
}
static bool enrolling(void) { return expires > k_uptime_get(); }
static int save(const struct config *next)
{
    int rc = settings_save_one(key, next, sizeof(*next));
    if (!rc) cfg = *next;
    return rc;
}

int pet_hike_init(pet_load_fn load)
{
    struct config stored;
    int rc = load(key, &stored, sizeof(stored));
    if (rc == -ENOENT) return 0;
    if (rc || !stored.timeout || stored.timeout > 3600 || stored.enabled > 1)
        return rc ? rc : -EBADMSG;
    cfg = stored;
    return 0;
}

static ssize_t enroll(struct bt_conn *conn, const struct bt_gatt_attr *attr, const void *buf,
                      uint16_t len, uint16_t offset, uint8_t flags)
{
    struct bt_conn_info info;
    if (bt_conn_get_info(conn, &info) || info.id != BT_ID_DEFAULT || info.security.level < BT_SECURITY_L2 || offset || len != 6 ||
        !bt_le_bond_exists(info.id, info.le.dst)) return BT_GATT_ERR(BT_ATT_ERR_AUTHORIZATION);
    k_mutex_lock(&lock, K_FOREVER);
    int rc = BT_GATT_ERR(BT_ATT_ERR_AUTHORIZATION);
    if (enrolling() && attempts && !filled(slot) && owner(info.le.dst) < 0) {
        char expected[7];
        snprintk(expected, sizeof(expected), "%06u", code);
        if (!memcmp(buf, expected, 6)) {
            struct config next = cfg;
            next.phones[slot] = *info.le.dst;
            if (save(&next)) rc = BT_GATT_ERR(BT_ATT_ERR_UNLIKELY);
            else { rc = len; expires = -1; }
        } else if (!--attempts) expires = -1;
    }
    k_mutex_unlock(&lock);
    return rc;
}

BT_GATT_SERVICE_DEFINE(pet_svc, BT_GATT_PRIMARY_SERVICE(&service),
    BT_GATT_CHARACTERISTIC(&claim.uuid, BT_GATT_CHRC_WRITE, BT_GATT_PERM_WRITE_ENCRYPT,
                           NULL, enroll, NULL));

static enum bt_security_err hike_pairing_accept(struct bt_conn *conn, const struct bt_conn_pairing_feat *const feat)
{
    k_mutex_lock(&lock, K_FOREVER);
    bool allow = enrolling() && owner(bt_conn_get_dst(conn)) < 0;
    k_mutex_unlock(&lock);
    return allow ? BT_SECURITY_ERR_SUCCESS : BT_SECURITY_ERR_PAIR_NOT_ALLOWED;
}
static const struct bt_conn_auth_cb auth = {.pairing_accept = hike_pairing_accept};

static void hike_connected(struct bt_conn *conn, uint8_t err)
{
    struct bt_conn_info info;
    if (err || bt_conn_get_info(conn, &info) || info.id != BT_ID_DEFAULT) return;
    k_mutex_lock(&lock, K_FOREVER);
    bool allow = enrolling() || (cfg.enabled && owner(info.le.dst) >= 0);
    k_mutex_unlock(&lock);
    if (!allow || bt_conn_auth_cb_overlay(conn, &auth) ||
        bt_conn_set_security(conn, BT_SECURITY_L2))
        bt_conn_disconnect(conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
}
BT_CONN_CB_DEFINE(hike_conn) = {.connected = hike_connected};

static void adv_connected(struct bt_le_ext_adv *a, struct bt_le_ext_adv_connected_info *info)
{
    atomic_clear(&advertising);
}
static const struct bt_le_ext_adv_cb adv_cb = {.connected = adv_connected};
static int advertise(bool wanted)
{
    if (!wanted) {
        if (!adv || !atomic_get(&advertising)) return 0;
        int rc = bt_le_ext_adv_stop(adv);
        if (!rc) atomic_clear(&advertising);
        return rc;
    }
    if (atomic_get(&advertising)) return 0;
    static const struct bt_le_adv_param params = {
        .id = BT_ID_DEFAULT, .options = BT_LE_ADV_OPT_CONN, .interval_min = 320, .interval_max = 400,
    };
    const struct bt_data data[] = {
        BT_DATA_BYTES(BT_DATA_FLAGS, BT_LE_AD_GENERAL | BT_LE_AD_NO_BREDR),
        BT_DATA_BYTES(BT_DATA_UUID128_ALL, HIKE_UUID(1)),
    };
    static const struct bt_data name[] = {BT_DATA(BT_DATA_NAME_COMPLETE, "Pet Collar", 10)};
    if (!adv) {
        int rc = bt_le_ext_adv_create(&params, &adv_cb, &adv);
        if (rc) return rc;
        rc = bt_le_ext_adv_set_data(adv, data, ARRAY_SIZE(data), name, ARRAY_SIZE(name));
        if (rc) { bt_le_ext_adv_delete(adv); adv = NULL; return rc; }
    }
    atomic_set(&advertising, 1);
    int rc = bt_le_ext_adv_start(adv, BT_LE_EXT_ADV_START_DEFAULT);
    if (rc && rc != -EALREADY) atomic_clear(&advertising);
    return rc == -EALREADY ? 0 : rc;
}

static void check_conn(struct bt_conn *conn, void *context)
{
    struct bt_conn_info info;
    if (bt_conn_get_info(conn, &info) || info.id != BT_ID_DEFAULT ||
        info.state != BT_CONN_STATE_CONNECTED) return;
    k_mutex_lock(&lock, K_FOREVER);
    int i = owner(info.le.dst);
    bool keep = enrolling() || (cfg.enabled && i >= 0);
    k_mutex_unlock(&lock);
    if (!keep) bt_conn_disconnect(conn, BT_HCI_ERR_REMOTE_USER_TERM_CONN);
    else if (i >= 0 && info.security.level >= BT_SECURITY_L2 && bt_le_bond_exists(info.id, info.le.dst))
        *(unsigned *)context |= BIT(i);
}

struct unused_bonds { unsigned count; bt_addr_le_t addr[CONFIG_BT_MAX_PAIRED]; };
static void find_unused(const struct bt_bond_info *bond, void *context)
{
    struct unused_bonds *unused = context;
    if (owner(&bond->addr) < 0 && unused->count < ARRAY_SIZE(unused->addr))
        unused->addr[unused->count++] = bond->addr;
}

bool pet_hike_tick(int64_t now)
{
    // Only the collar worker changes advertising and polls live encrypted links.
    if (now < poll_at) {
        lost = away_since >= 0 && now - away_since >= 1000LL * cfg.timeout;
        return lost;
    }
    poll_at = now + 500;
    k_mutex_lock(&lock, K_FOREVER);
    bool cleanup = expires && !enrolling();
    struct unused_bonds unused = {0};
    if (cleanup) {
        expires = 0;
        bt_foreach_bond(BT_ID_DEFAULT, find_unused, &unused);
    }
    bool active = cfg.enabled || enrolling();
    k_mutex_unlock(&lock);
    for (unsigned i = 0; i < unused.count; ++i) bt_unpair(BT_ID_DEFAULT, &unused.addr[i]);
    unsigned mask = 0;
    if (active || adv) bt_conn_foreach(BT_CONN_TYPE_LE, check_conn, &mask);
    k_mutex_lock(&lock, K_FOREVER);
    present = mask;
    bool wanted = enrolling() || (cfg.enabled && (filled(0) + filled(1) > ((mask & 1) + ((mask >> 1) & 1))));
    if (!cfg.enabled || mask) away_since = -1;
    else if (away_since < 0) away_since = now;
    lost = away_since >= 0 && now - away_since >= 1000LL * cfg.timeout;
    k_mutex_unlock(&lock);
    adv_error = wanted && rak_host_ready() ? rak_host_ready() : advertise(wanted);
    return lost;
}

int pet_hike_command(const struct shell *sh, size_t argc, char **argv)
{
    k_mutex_lock(&lock, K_FOREVER);
    struct config next = cfg;
    int rc = 0;
    if (!strcmp(argv[1], "hike")) {
        if (argc == 2)
            shell_print(sh, "hike=%s timeout_s=%u state=%s bluetooth_error=%d", cfg.enabled ? "on" : "off",
                        cfg.timeout, !cfg.enabled ? "off" : present ? "nearby" : lost ? "lost" : "waiting", adv_error);
        else if (argc == 3 && (!strcmp(argv[2], "on") || !strcmp(argv[2], "off"))) {
            next.enabled = !strcmp(argv[2], "on");
            if (next.enabled && !filled(0) && !filled(1)) rc = -ENOENT;
            else if (!(rc = save(&next))) {
                if (!cfg.enabled) { lost = false; away_since = -1; }
                else if (away_since < 0 && !present) away_since = k_uptime_get();
            }
        } else if (argc == 4 && !strcmp(argv[2], "timeout")) {
            char *end;
            unsigned long s = strtoul(argv[3], &end, 10);
            if (argv[3][0] < '0' || argv[3][0] > '9' || *end || !s || s > 3600) rc = -EINVAL;
            else { next.timeout = s; rc = save(&next); }
        } else rc = -EINVAL;
    } else if (argc == 2 && !strcmp(argv[1], "phones")) {
        for (unsigned i = 0; i < 2; ++i)
            shell_print(sh, "phone%u=%s", i + 1, !filled(i) ? "empty" : (present & BIT(i)) ? "connected" : "away");
    } else if (!strcmp(argv[1], "phone") && argc == 4 &&
               (!strcmp(argv[2], "1") || !strcmp(argv[2], "2"))) {
        unsigned i = argv[2][0] - '1';
        if (!strcmp(argv[3], "enroll")) {
            uint32_t random;
            if (filled(i)) rc = -EEXIST;
            else if ((rc = rak_host_ready()) == 0 && (rc = sys_csrand_get(&random, sizeof(random))) == 0) {
                slot = i; code = random % 1000000; attempts = 5; expires = k_uptime_get() + 120000;
                shell_print(sh, "phone=%u code=%06u expires_s=120", i + 1, code);
            }
        } else if (!strcmp(argv[3], "forget")) {
            memset(&next.phones[i], 0, sizeof(next.phones[i]));
            if (bt_addr_le_eq(&next.phones[1-i], BT_ADDR_LE_ANY)) next.enabled = 0;
            if (!(rc = save(&next))) {
                present &= ~BIT(i);
                expires = -1; // Close enrollment and prune retired bonds on the worker.
            }
        } else rc = -EINVAL;
    } else rc = -EINVAL;
    poll_at = 0;
    k_mutex_unlock(&lock);
    return rc;
}
