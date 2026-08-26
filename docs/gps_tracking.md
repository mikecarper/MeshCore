# GPS Tracking

This document describes how GPS telemetry works for companion/client nodes and
sensor nodes.

## Scope

GPS tracking uses the existing CayenneLPP GPS telemetry field. It does not add a
new phone app protocol field.

The GPS telemetry value contains latitude, longitude, and altitude. Speed,
heading, and freshness are not sent as separate telemetry fields.

If the firmware does not have a valid fresh GPS cache, it omits the GPS field
from telemetry. This is intentional: stale or missing fixes are not reported as
zero coordinates.

## Freshness

GPS telemetry is cached separately from advert location.

The cache behavior is:

- Refresh the cached GPS value every 2 hours only when at least one contact or
  ACL client can receive location telemetry.
- Try for up to 15 minutes during a refresh.
- If the position stays within about 100 meters for 30 seconds, cache a weighted
  average of the stable fixes, with newer fixes weighted more heavily.
- If the position moves outside that 100 meter circle during acquisition, cache
  the latest valid fix.
- Omit GPS from telemetry if there is no fix or if the cached fix is more than
  12 hours old.

When a telemetry request asks for location, GPS is kept on for 2 hours after the
latest location request. During that hold window, later location telemetry
requests can use fresh GPS data as soon as valid fixes are available.

If GPS is manually enabled, it stays on and valid fixes continue to update the
cache.

If no contact or ACL client can receive location telemetry, the scheduled
2-hour refresh does not run. A real location telemetry request still turns GPS
on for the 2-hour hold window, and manual GPS-on still keeps the cache updated.

## Companion/Client Nodes

Companion/client telemetry uses the existing companion telemetry permission
system:

- base telemetry
- location telemetry
- environment telemetry

Location telemetry is sent only when the requester's effective telemetry
permissions include location. Those permissions are derived from the companion
telemetry mode settings and contact flags.

The scheduled GPS cache refresh runs only when at least one stored contact has
effective location telemetry access:

- `location: allow all` with at least one stored contact
- `location: allow flags` with at least one stored contact whose flags include
  location

No new phone app behavior is required. Existing clients see the existing GPS
telemetry field when it is present.

## Sensor Nodes

Sensor telemetry access is controlled by:

```text
get telemetry.access
set telemetry.access all
set telemetry.access acl
```

`all` is the default and matches the previous sensor telemetry behavior. A
request that reaches the normal sensor telemetry request path can receive the
requested telemetry fields, including GPS, subject to the request mask and GPS
freshness rules.

`acl` gates telemetry through the sensor ACL. A requester with read-only or
higher permissions receives the existing telemetry set, including GPS. Guest or
unknown requesters receive no telemetry fields.

The scheduled GPS cache refresh follows the same access setting:

- `all`: runs only when at least one ACL client exists
- `acl`: runs only when at least one ACL client is read-only or higher

Use the existing ACL command to grant access:

```text
setperm <pubkey> 1
```

Permission values:

- `1`: read-only, suitable for telemetry access
- `2`: read-write
- `3`: admin

## Advert Location

Advert location is separate from telemetry GPS. The advert policy is controlled
with:

```text
gps advert
gps advert none
gps advert share
gps advert prefs
```

Policies:

- `none`: do not include location in adverts
- `share`: use the live/shared sensor manager location
- `prefs`: use the stored node latitude and longitude preferences

`prefs` is the first-boot default for every repeater, room-server, and sensor
build. Firmware updates retain an explicitly saved policy from the existing
preferences filesystem.

Telemetry GPS can be fresh while advert location is fixed or disabled, depending
on this policy.

## Recommended Setup

For private sensor tracking:

```text
set telemetry.access acl
setperm <owner_pubkey> 3
setperm <trusted_pubkey> 1
```

For compatibility with the older sensor behavior:

```text
set telemetry.access all
```

For GPS telemetry behavior, leave the phone app unchanged. The firmware omits
GPS when it is stale or missing and sends the existing GPS telemetry field when
fresh data is available.
