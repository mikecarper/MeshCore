#include <errno.h>
#include <zephyr/kernel.h>
#include <zephyr/shell/shell_dummy.h>
#include "posix_board_if.h"
#include "controller.h"

static void command(const char *text, int expected)
{
    const struct shell *sh = shell_backend_dummy_get_ptr();
    shell_backend_dummy_clear_output(sh);
    int result = shell_execute_cmd(sh, text);
    if (result != expected) {
        size_t size;
        const char *output = shell_backend_dummy_get_output(sh, &size);
        posix_print_trace("%.*s\n", (int)size, output);
        posix_print_trace("%s returned %d, expected %d\n", text, result, expected);
        posix_exit(1);
    }
}

int main(void)
{
    int err = rak_locator_controller_init();
    if (err) {
        posix_print_trace("Bluetooth startup failed (%d)\n", err);
        posix_exit(1);
    }
    command("locator ids", 0);
    command("locator capacity", 0);
    // The virtual controller rejects the second enable on the first attempt.
    command("locator ids start", -EACCES);
    command("locator capacity", 0);
    posix_print_trace("scan rollback\n");
    k_sleep(K_MSEC(500));
    for (int cycle = 0; cycle < 2; ++cycle) {
        command("locator ids start", 0);
        command("locator ids start", 0);
        command("locator ids status", 0);
        command("locator capacity", -EBUSY);
        posix_print_trace("scan active\n");
        k_sleep(K_MSEC(900));
        command("locator ids stop", 0);
        command("locator ids stop", 0);
        command("locator capacity", 0);
        posix_print_trace("scan idle\n");
        k_sleep(K_MSEC(500));
        posix_print_trace("scan cycle done\n");
    }
    posix_print_trace("BLE command checks passed\n");
    posix_exit(0);
    return 0;
}
