#pragma once

// Dispatcher includes identity declarations, but the packet-pool/share
// methods exercised here do not invoke identity or crypto operations.
namespace mesh { class Identity {}; class LocalIdentity {}; }
