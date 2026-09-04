#pragma once

// The production helper only needs a valid owner for a closed LittleFS file.
// The harness defines this object without any filesystem or hardware access.
struct FakeFS;
extern FakeFS InternalFS;
