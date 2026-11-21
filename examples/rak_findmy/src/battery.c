#include <zephyr/drivers/adc.h>

static const struct adc_dt_spec battery = ADC_DT_SPEC_GET(DT_PATH(zephyr_user));

int tag_battery_mv(void)
{
    int16_t raw;
    struct adc_sequence seq = { .buffer = &raw, .buffer_size = sizeof(raw) };
    int err = adc_sequence_init_dt(&battery, &seq);
    if (!err) err = adc_read(battery.dev, &seq);
    if (err) return err;
    int32_t mv = raw;
    err = adc_raw_to_millivolts_dt(&battery, &mv);
    // Same resistor-divider calibration as RAKWismeshTagBoard.
    return err ? err : (mv * 2054 + 500) / 1000;
}
