#include <errno.h>
#include <string.h>
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

static void check_android_status(const char *state)
{
    command("locator ids status B", 0);
    size_t size;
    const char *output = shell_backend_dummy_get_output(shell_backend_dummy_get_ptr(), &size);
    if (!strstr(output, "RAK-B-Android") || !strstr(output, state) ||
        strstr(output, "RAK-A-Apple") || strstr(output, "RAK-C-Sidewalk")) {
        posix_print_trace("Unexpected B status: %.*s\n", (int)size, output);
        posix_exit(1);
    }
}

static void observe(const char *enabled)
{
    posix_print_trace("scan %s\n", enabled);
    k_sleep(K_MSEC(750));
    posix_print_trace("scan end\n");
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
    command("locator ids start c", 0);
    command("locator ids status C", 0);
    command("locator ids start C", 0);
    // A starts, then the controller rejects B. C must keep transmitting.
    command("locator ids start", -EACCES);
    command("locator capacity", -EBUSY);
    observe("C");
    command("locator ids start B", 0);
    check_android_status("local beacon on");
    observe("BC");
    command("locator ids start", 0);
    command("locator ids start", 0);
    observe("ABC");
    command("locator ids stop b", 0);
    command("locator ids stop B", 0);
    command("locator ids stop D", -EINVAL);
    command("locator ids stop AA", -EINVAL);
    check_android_status("local beacon off");
    observe("AC");
    command("locator ids stop C", 0);
    observe("A");
    command("locator ids start B", 0);
    observe("AB");
    command("locator ids stop A", 0);
    observe("B");
    command("locator ids stop", 0);
    command("locator ids stop", 0);
    command("locator capacity", 0);
    observe("none");
    posix_print_trace("BLE command checks passed\n");
    posix_exit(0);
    return 0;
}
