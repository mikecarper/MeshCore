#include <errno.h>
#include <string.h>
#include <sid_api.h>
#include <sid_location.h>
#include <sid_pal_common_ifc.h>
#include <app_ble_config.h>
#include <app_mfg_config.h>
#ifdef RAK_NATIVE_PINGS
#include "native_pings.h"
#endif
#include <zephyr/drivers/adc.h>
#include <zephyr/kernel.h>
#include <zephyr/shell/shell.h>
#include <zephyr/storage/flash_map.h>
#include <zephyr/sys/reboot.h>

extern void tag_mesh_start(void);

static K_SEM_DEFINE(wake, 0, 1);
static atomic_t running, registered, cloud, locate_request, connect_request, reboot_request;
static atomic_t sent, pending, retry_location, location_active, last_error;
static atomic_t gateway_location = ATOMIC_INIT(-1);
static const char *stage = "boot";
static K_MUTEX_DEFINE(stage_lock);
static struct sid_handle *handle;

static void set_stage(const char *value)
{
    k_mutex_lock(&stage_lock, K_FOREVER);
    stage = value;
    k_mutex_unlock(&stage_lock);
}

static void on_event(bool in_isr, void *context)
{
    k_sem_give(&wake);
}

static void on_status(const struct sid_status *status, void *context)
{
    atomic_set(&registered, status->detail.registration_status == SID_STATUS_REGISTERED);
    atomic_set(&cloud, status->state == SID_STATE_READY &&
               status->detail.time_sync_status == SID_STATUS_TIME_SYNCED &&
               (status->detail.link_status_mask & SID_LINK_TYPE_1) &&
               (status->detail.supported_link_modes[SID_LINK_TYPE_1_IDX] & SID_LINK_MODE_CLOUD));
    if (!atomic_get(&cloud))
        atomic_set(&gateway_location, -1);
    k_sem_give(&wake);
}

static void on_reset(void *context)
{
    atomic_set(&reboot_request, 1);
    k_sem_give(&wake);
}

static void on_rx(const struct sid_msg_desc *desc, const struct sid_msg *msg, void *context) {}
static void on_tx(const struct sid_msg_desc *desc, void *context) {}
static void on_tx_error(sid_error_t err, const struct sid_msg_desc *desc, void *context)
{
    atomic_set(&last_error, err);
}

static void on_location(const struct sid_location_result *const result, void *context)
{
    if (result->status == SID_LOCATION_LVL1_READY || result->status == SID_LOCATION_LVL1_UNAVAILABLE) {
        atomic_set(&gateway_location, result->status == SID_LOCATION_LVL1_READY);
        k_sem_give(&wake);
    } else if (result->status == SID_LOCATION_SEND_DONE) {
        atomic_set(&last_error, result->err);
        if (!result->err)
            atomic_inc(&sent);
        else
            atomic_set(&retry_location, 1);
        atomic_clear(&pending);
        k_sem_give(&wake);
    }
}

static struct sid_location_config location = {
    .sid_location_type_mask = SID_LOCATION_METHOD_BLE_GATEWAY,
    .callbacks = { .on_update = on_location },
    .max_effort = SID_LOCATION_EFFORT_L1,
    .manage_effort = true,
};

static int start(void)
{
    const struct flash_area *area;
    uint32_t header;
    int err = flash_area_open(FIXED_PARTITION_ID(mfg_storage), &area);
    if (err)
        return err;
    err = flash_area_read(area, 0, &header, sizeof(header));
    flash_area_close(area);
    if (err)
        return err;
    if (header == UINT32_MAX) {
        set_stage("needs AWS manufacturing data");
        return -ENODATA;
    }

    if (memcmp(&header, "SID0", sizeof(header))) {
        set_stage("invalid AWS manufacturing data");
        return -EBADMSG;
    }

    set_stage("platform init");
    static platform_parameters_t platform = {
        .mfg_store_region = { .addr_start = APP_MFG_CFG_FLASH_START, .addr_end = APP_MFG_CFG_FLASH_END }
    };
    err = sid_platform_init(&platform);
    if (err)
        return err;
    if (app_mfg_cfg_is_empty()) {
        set_stage("invalid AWS manufacturing data");
        return -ENODATA;
    }

    static struct sid_event_callbacks callbacks = {
        .on_event = on_event, .on_status_changed = on_status, .on_msg_received = on_rx,
        .on_msg_sent = on_tx, .on_send_error = on_tx_error, .on_factory_reset = on_reset
    };
    static struct sid_config config = {
        .link_mask = SID_LINK_TYPE_1,
        .dev_ch = { .type = SID_END_DEVICE_TYPE_MOBILE, .power_type = SID_END_DEVICE_POWERED_BY_BATTERY_ONLY },
        .callbacks = &callbacks
    };
    config.link_config = app_get_ble_config();
#ifdef RAK_NATIVE_PINGS
    // A single presence cadence has no fast-to-slow transition at 30 s.
    static sid_ble_link_config_t link;
    static sid_ble_config_t ble;
    link = *config.link_config;
    ble = *link.config;
    ble.adv_param.fast_enabled = false;
    ble.adv_param.slow_interval = rak_ping_interval_units();
    link.config = &ble;
    config.link_config = &link;
#endif
    set_stage("stack init");
    err = sid_init(&config, &handle);
    if (err)
        return err;
    if (!IS_ENABLED(CONFIG_RAK_SIDEWALK_PROXIMITY)) {
        set_stage("location init");
        err = sid_location_init(handle, &location);
        if (err) {
            sid_deinit(handle);
            return err;
        }
    }
    set_stage("BLE start");
    err = sid_start(handle, SID_LINK_TYPE_1);
    if (err) {
        if (!IS_ENABLED(CONFIG_RAK_SIDEWALK_PROXIMITY))
            sid_location_deinit(handle);
        sid_deinit(handle);
        return err;
    }
    set_stage("running");
    atomic_set(&location_active, !IS_ENABLED(CONFIG_RAK_SIDEWALK_PROXIMITY));
    atomic_set(&running, 1);
    return 0;
}

#ifdef RAK_NATIVE_PINGS
bool rak_sidewalk_running(void) { return atomic_get(&running) != 0; }
#endif

static int status_cmd(const struct shell *sh, size_t argc, char **argv)
{
    k_mutex_lock(&stage_lock, K_FOREVER);
    if (IS_ENABLED(CONFIG_RAK_SIDEWALK_PROXIMITY)) {
        shell_print(sh, "mode=proximity %s registered=%ld cloud_ble=%ld last_error=%ld",
                    stage, (long)atomic_get(&registered), (long)atomic_get(&cloud),
                    (long)atomic_get(&last_error));
    } else {
        shell_print(sh, "%s; registered=%ld cloud_ble=%ld gateway_location=%ld pending=%ld sent=%ld last_error=%ld",
                    stage, (long)atomic_get(&registered), (long)atomic_get(&cloud),
                    (long)atomic_get(&gateway_location), (long)atomic_get(&pending), (long)atomic_get(&sent), (long)atomic_get(&last_error));
    }
    k_mutex_unlock(&stage_lock);
    return 0;
}

static int request_cmd(const struct shell *sh, size_t argc, char **argv)
{
    if (IS_ENABLED(CONFIG_RAK_SIDEWALK_PROXIMITY)) {
        shell_error(sh, "This proximity image sends presence beacons. Use the location image for gateway requests.");
        return -ENOTSUP;
    }
    bool locate = !strcmp(argv[0], "locate");
    if (!atomic_get(&running) || (locate && (!atomic_get(&registered) || !atomic_get(&cloud)))) {
        shell_error(sh, "Check sidewalk status. Location needs a registered tag and an active cloud BLE link.");
        return -ENOTCONN;
    }
    if (locate && !atomic_get(&location_active))
        return -EAGAIN;
    if (locate && atomic_get(&gateway_location) == 0) {
        shell_error(sh, "This gateway reported location unavailable");
        return -EHOSTUNREACH;
    }
    if (locate && atomic_get(&pending))
        return -EBUSY;
    atomic_set(locate ? &locate_request : &connect_request, 1);
    k_sem_give(&wake);
    shell_print(sh, "Queued");
    return 0;
}

SHELL_STATIC_SUBCMD_SET_CREATE(commands,
    SHELL_CMD_ARG(status, NULL, "Show Sidewalk startup, link, and location state", status_cmd, 1, 0),
    SHELL_CMD_ARG(connect, NULL, "Request a Sidewalk BLE gateway connection", request_cmd, 1, 0),
    SHELL_CMD_ARG(locate, NULL, "Request a gateway-based location uplink", request_cmd, 1, 0),
    SHELL_SUBCMD_SET_END);
SHELL_CMD_REGISTER(sidewalk, &commands, "Sidewalk BLE location", NULL);

int main(void)
{
    static const struct adc_dt_spec battery = ADC_DT_SPEC_GET(DT_PATH(zephyr_user));
    if (adc_is_ready_dt(&battery))
        adc_channel_setup_dt(&battery);

    int err = start();
    atomic_set(&last_error, err);
#ifndef RAK_NATIVE_PINGS
    tag_mesh_start();
#endif
    if (err)
        return 0;

    int64_t next_connect = 0, next_location = 0, pending_deadline = 0;
    for (;;) {
        err = sid_process(handle);
        if (err)
            atomic_set(&last_error, err);
        if (atomic_get(&reboot_request))
            sys_reboot(SYS_REBOOT_COLD);

        if (IS_ENABLED(CONFIG_RAK_SIDEWALK_PROXIMITY)) {
            // The SDK owns BLE advertising, registration and clock maintenance.
            k_sem_take(&wake, K_FOREVER);
            continue;
        }

        int64_t now = k_uptime_get();
        bool force_connect = atomic_clear(&connect_request);
        if (force_connect || (!atomic_get(&cloud) && now >= next_connect)) {
            err = sid_ble_bcn_connection_request(handle, true);
            if (err)
                atomic_set(&last_error, err);
            next_connect = now + 300000;
        }
        if (atomic_clear(&retry_location))
            next_location = now + 60000;
        if (atomic_get(&pending) && now >= pending_deadline) {
            // End the old session before issuing a new request. The location
            // callback carries no request ID to distinguish a late completion.
            err = sid_location_deinit(handle);
            if (!err) {
                atomic_clear(&pending);
                atomic_clear(&location_active);
                set_stage("restarting location service");
                atomic_set(&last_error, -ETIMEDOUT);
                next_location = now + 60000;
            } else {
                atomic_set(&last_error, err);
            }
            pending_deadline = now + 90000;
        }
        if (!atomic_get(&location_active) && now >= next_location) {
            err = sid_location_init(handle, &location);
            if (err) {
                atomic_set(&last_error, err);
                next_location = now + 60000;
            } else {
                atomic_set(&location_active, 1);
                set_stage("running");
            }
        }
        bool force_location = atomic_clear(&locate_request);
        if (atomic_get(&location_active) && atomic_get(&registered) && atomic_get(&cloud) &&
            atomic_get(&gateway_location) != 0 && !atomic_get(&pending) &&
            (force_location || now >= next_location)) {
            struct sid_location_run_config request = {
                .type = SID_LOCATION_SCAN_AND_SEND, .mode = SID_LOCATION_EFFORT_DEFAULT
            };
            pending_deadline = now + 90000;
            atomic_set(&pending, 1);
            err = sid_location_run(handle, &request, 0);
            if (err) {
                atomic_clear(&pending);
                atomic_set(&last_error, err);
            }
            next_location = now + (err ? 60000 : 900000);
        }
        k_sem_take(&wake, K_SECONDS(5));
    }
}
