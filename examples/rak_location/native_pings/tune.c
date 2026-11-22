#include <errno.h>
#include <stdlib.h>
#include <string.h>
#include <zephyr/settings/settings.h>
#include "pet.h"

struct note { uint16_t hz, ms; };
struct tune { uint16_t interval, count; struct note notes[8]; };
static const char key[] = "tagmesh/tune1";
static struct tune tune = {30, 3, {{1568, 175}, {0, 100}, {1047, 350}}};
// The workqueue times individual notes even during a blocking LoRa transmission.
static K_MUTEX_DEFINE(tune_lock);
static void advance(struct k_work *work);
static K_WORK_DELAYABLE_DEFINE(note_work, advance);
static unsigned step;
static bool playing, preview, was_outside;
static int64_t note_end, repeat_at;

static bool valid_note(struct note n)
{
    return (!n.hz || (n.hz >= 200 && n.hz <= 5000)) && n.ms >= 50 && n.ms <= 1000;
}

static bool valid(const struct tune *t)
{
    if (!t->count || t->count > ARRAY_SIZE(t->notes) || t->interval > 3600) return false;
    for (unsigned i = 0; i < t->count; ++i)
        if (!valid_note(t->notes[i])) return false;
    return true;
}

int pet_tune_init(pet_load_fn load)
{
    struct tune stored;
    int rc = load(key, &stored, sizeof(stored));
    if (rc == -ENOENT) return 0;
    if (rc || !valid(&stored)) return rc ? rc : -EBADMSG;
    tune = stored;
    return 0;
}

static void stop(void)
{
    k_work_cancel_delayable(&note_work);
    if (playing) rak_pet_tone(0);
    playing = preview = false;
}

static bool integer(const char **cursor, unsigned max, unsigned *value)
{
    const char *s = *cursor;
    if (*s < '0' || *s > '9') return false;
    unsigned n = 0;
    do {
        n = n * 10 + (*s++ - '0');
        if (n > max) return false;
    } while (*s >= '0' && *s <= '9');
    *cursor = s;
    *value = n;
    return true;
}

static int command(const struct shell *sh, size_t argc, char **argv)
{
    if (argc == 2) {
        shell_print(sh, "repeat_s=%u notes=%u", tune.interval, tune.count);
        for (unsigned i = 0; i < tune.count; ++i)
            shell_print(sh, "%u:%u", tune.notes[i].hz, tune.notes[i].ms);
        return 0;
    }
    if (argc == 3 && !strcmp(argv[2], "play")) {
        stop();
        preview = true;
        return 0;
    }
    struct tune next = tune;
    if (argc == 3 && !strcmp(argv[2], "off")) next.interval = 0;
    else {
        if (argc != 3 && argc != 4) return -EINVAL;
        const char *s = argv[2];
        unsigned interval;
        if (!integer(&s, 3600, &interval) || *s || !interval) return -EINVAL;
        next.interval = interval;
        if (argc == 4) {
            s = argv[3];
            next.count = 0;
            while (true) {
                unsigned hz, ms;
                if (next.count == ARRAY_SIZE(next.notes) || !integer(&s, 5000, &hz) ||
                    *s++ != ':' || !integer(&s, 1000, &ms)) return -EINVAL;
                next.notes[next.count++] = (struct note){hz, ms};
                if (!*s) break;
                if (*s++ != ',') return -EINVAL;
            }
        }
    }
    if (!valid(&next)) return -EINVAL;
    int rc = settings_save_one(key, &next, sizeof(next));
    if (!rc) { stop(); tune = next; repeat_at = 0; }
    return rc;
}

int pet_tune_command(const struct shell *sh, size_t argc, char **argv)
{
    k_mutex_lock(&tune_lock, K_FOREVER);
    int rc = command(sh, argc, argv);
    k_mutex_unlock(&tune_lock);
    return rc;
}

static void play_note(int64_t now)
{
    rak_pet_tone(tune.notes[step].hz);
    note_end = now + tune.notes[step].ms;
    k_work_reschedule(&note_work, K_MSEC(tune.notes[step].ms));
}

static void advance(struct k_work *work)
{
    k_mutex_lock(&tune_lock, K_FOREVER);
    int64_t now = k_uptime_get();
    if (playing && now < note_end) k_work_reschedule(&note_work, K_MSEC(note_end - now));
    else if (playing) {
        if (++step == tune.count) stop();
        else play_note(now);
    }
    k_mutex_unlock(&tune_lock);
}

void pet_tune_tick(bool outside, int64_t now)
{
    k_mutex_lock(&tune_lock, K_FOREVER);
    if (!outside && was_outside && !preview) stop();
    if (!outside) repeat_at = 0;
    was_outside = outside;
    if (!playing && (preview || (outside && tune.interval && now >= repeat_at))) {
        playing = true;
        step = 0;
        repeat_at = now + 1000LL * tune.interval;
        play_note(now);
    }
    k_mutex_unlock(&tune_lock);
}
