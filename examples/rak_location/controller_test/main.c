/* Local Bluetooth controller bring-up for the RAK tracker roles. */
#include <errno.h>
#include <zephyr/kernel.h>
#include <zephyr/drivers/adc.h>
#include "controller.h"

extern int apple_entry(void), findhub_entry(void), sidewalk_entry(void);
extern void tag_mesh_start(void);

// Retain the full applications in the link, including their SDK and physical alert handlers.
static int (*const volatile entries[])(void) = {apple_entry, findhub_entry, sidewalk_entry};
int main(void)
{
    if (!entries[0] || !entries[1] || !entries[2]) return -EINVAL;
    static const struct adc_dt_spec battery = ADC_DT_SPEC_GET(DT_PATH(zephyr_user));
    if (adc_is_ready_dt(&battery)) (void)adc_channel_setup_dt(&battery);
    (void)rak_locator_controller_init();
    tag_mesh_start();
    return 0;
}
