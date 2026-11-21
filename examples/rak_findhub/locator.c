// Reuse the Nordic v3.3.0 locator state machine and its registered callbacks.
// Run its cooperative initialization while allowing the USB/LoRa application
// to continue after a board or provisioning failure.
#define main nordic_locator_sample_main
#include "locator_tag/src/main.c"
#undef main

int rak_findhub_start(void)
{
    struct k_work_sync done;
    int err = k_work_submit(&init_work);
    if (err < 0)
        return err;
    k_work_flush(&init_work, &done);
    if (k_sem_take(&init_work_sem, K_NO_WAIT))
        return -EIO; // Nordic logs the failing initialization step.
    return app_ui_state_change_indicate(APP_UI_STATE_APP_RUNNING, true);
}
