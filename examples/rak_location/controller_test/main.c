/* Local Bluetooth controller bring-up for the RAK tracker roles. */
#include <errno.h>
#include <zephyr/kernel.h>
#include <zephyr/drivers/adc.h>
#include <zephyr/bluetooth/bluetooth.h>
#include <zephyr/bluetooth/hci.h>
#include <zephyr/settings/settings.h>
#include <zephyr/shell/shell.h>

extern int apple_entry(void), findhub_entry(void), sidewalk_entry(void);
extern void tag_mesh_start(void);

// Retain the full applications in the link, including their SDK and physical alert handlers.
static int (*const volatile entries[])(void) = {apple_entry, findhub_entry, sidewalk_entry};
static atomic_t bt_error = ATOMIC_INIT(-EINPROGRESS);
static struct bt_le_ext_adv *sets[3];
static K_MUTEX_DEFINE(check_lock);

static int release_sets(void)
{
    int result = 0;
    for (size_t i = 0; i < ARRAY_SIZE(sets); ++i) {
        if (!sets[i]) continue;
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

static int check_controller(const struct shell *sh, size_t argc, char **argv)
{
    int err = atomic_get(&bt_error);
    if (err) {
        shell_error(sh, "Bluetooth startup status %d", err);
        return err;
    }
    if (k_mutex_lock(&check_lock, K_NO_WAIT)) return -EBUSY;

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
    SHELL_CMD_ARG(capacity, NULL, "Query Bluetooth and allocate three idle sets", check_controller, 1, 0),
    SHELL_SUBCMD_SET_END);
SHELL_CMD_REGISTER(locator, &locator_commands, "RAK controller bring-up", NULL);

int main(void)
{
    if (!entries[0] || !entries[1] || !entries[2]) return -EINVAL;
    static const struct adc_dt_spec battery = ADC_DT_SPEC_GET(DT_PATH(zephyr_user));
    if (adc_is_ready_dt(&battery)) (void)adc_channel_setup_dt(&battery);
    int err = bt_enable(NULL);
    if (!err) err = settings_load();
    atomic_set(&bt_error, err);
    tag_mesh_start();
    return 0;
}
