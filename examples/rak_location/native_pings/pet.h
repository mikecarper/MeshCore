#pragma once
#include <zephyr/shell/shell.h>
#ifdef __cplusplus
extern "C" {
#endif
typedef int (*pet_load_fn)(const char *, void *, size_t);
int pet_tune_init(pet_load_fn load);
int pet_hike_init(pet_load_fn load);
int pet_tune_command(const struct shell *sh, size_t argc, char **argv);
int pet_hike_command(const struct shell *sh, size_t argc, char **argv);
void pet_tune_tick(bool outside, int64_t now);
bool pet_hike_tick(int64_t now);
int rak_pet_tone(unsigned hz);
#ifdef __cplusplus
}
#endif
