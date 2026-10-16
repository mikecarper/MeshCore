static void seed(MyMesh& node) {
#if defined(STM32_PLATFORM) || defined(NRF52_PLATFORM)
  node.store.fs.rename_replaces = true;
#endif
  strcpy(node.channels[0].name, "Public");
  strcpy(node.channels[1].name, "Other");
  strcpy(node.contacts[1].name, "Healthy contact");
  node.channels[0].channel.tx_radio = mesh::RADIO_TX_PRIMARY;
  memset(node.channels[0].channel.secret, 0x11, 16);
  assert(node.store.saveChannels(&node));
  assert(last_channel_outcome == 2); // Actual writer: Committed.
}

static void failPublicObservationAfterPublish(MyMesh& node) {
#if defined(STM32_PLATFORM) || defined(NRF52_PLATFORM)
  node.store.fs.arm_public_read_failure = true;
#else
  node.store.fs.arm_namespace_failure = true;
#endif
}

static void publication_uncertainty_cannot_acknowledge_a_rolled_back_value() {
  MyMesh node;
  seed(node);
  const auto old_image = node.store.fs.files.at("/channels2");
  failPublicObservationAfterPublish(node);
  char reply[160];
  assert(node.handleTxRoutingCommand("set tx.channel 0 both", reply, sizeof(reply)));
  assert(strstr(reply, "not saved") && last_channel_outcome == 3);
  assert(node.channels[0].channel.tx_radio == mesh::RADIO_TX_PRIMARY);
  const auto published = node.store.fs.files.at("/channels2");
  assert(published != old_image);
  assert(published[0] == mesh::encodeRadioTxPolicy(mesh::RADIO_TX_BOTH));
  assert(!node.store.hasIncompleteContactLoad());
  const unsigned writes = node.writes;
  assert(node.handleTxRoutingCommand("set tx.channel 0 radio", reply, sizeof(reply)));
  // Exact regression invariant: without the publication latch this same-value
  // branch returns OK without saving, although a reboot restores BOTH.
  assert(strstr(reply, "storage unavailable"));
  assert(node.writes == writes && node.store.hasIncompleteChannelLoad());
  assert(node.store.fs.files.at("/channels2") == published);
  assert(!node.store.saveChannels(&node));
  assert(node.handleTxRoutingCommand("set tx.user \"Healthy contact\" both", reply, sizeof(reply)));
  assert(!strncmp(reply, "OK", 2)); // Contact authority is independent.
  MyMesh reboot;
  reboot.store.fs.files = node.store.fs.files;
  reboot.store.loadChannels(&reboot);
  assert(reboot.channels[0].channel.tx_radio == mesh::RADIO_TX_BOTH);
  assert(!reboot.store.hasIncompleteChannelLoad());
}

static void prepublication_failures_remain_retryable() {
  MyMesh node;
  seed(node);
  const auto old_image = node.store.fs.files.at("/channels2");
  node.store.fs.fail_write = true;
  char reply[160];
  assert(node.handleTxRoutingCommand("set tx.channel 0 both", reply, sizeof(reply)));
  assert(strstr(reply, "not saved") && last_channel_outcome == 1);
  assert(node.store.fs.files.at("/channels2") == old_image);
  assert(!node.store.hasIncompleteChannelLoad());
  node.store.fs.fail_write = false;
  const unsigned writes = node.writes;
  assert(node.handleTxRoutingCommand("set tx.channel 0 radio", reply, sizeof(reply)));
  assert(!strncmp(reply, "OK", 2) && node.writes == writes);
  assert(node.store.fs.files.at("/channels2")[0] == mesh::encodeRadioTxPolicy(mesh::RADIO_TX_PRIMARY));
  assert(node.handleTxRoutingCommand("set tx.channel 0 both", reply, sizeof(reply)));
  assert(!strncmp(reply, "OK", 2) && last_channel_outcome == 2);
}

static void binary_psk_rollback_preserves_live_communications_but_blocks_writes() {
  MyMesh node;
  seed(node);
  failPublicObservationAfterPublish(node);
  uint8_t frame[2 + 32 + 16] = {};
  frame[0] = CMD_SET_CHANNEL;
  memcpy(frame + 2, "Public", 6);
  memset(frame + 2 + 32, 0xAA, 16);
  node.applyChannelFrame(frame, sizeof(frame));
  assert(node.last_error == ERR_CODE_FILE_IO_ERROR && !node.binary_ack);
  assert(last_channel_outcome == 3 && node.store.hasIncompleteChannelLoad());
  assert(node.channels[0].channel.secret[0] == 0x11);
  const auto published = node.store.fs.files.at("/channels2");
  assert(published[36] == 0xAA);
  ChannelDetails active;
  assert(node.getChannel(0, active) && active.channel.secret[0] == 0x11);
  memset(frame + 2 + 32, 0xBB, 16);
  node.last_error = 0;
  node.applyChannelFrame(frame, sizeof(frame));
  assert(node.last_error == ERR_CODE_FILE_IO_ERROR && !node.binary_ack);
  assert(node.channels[0].channel.secret[0] == 0x11);
  assert(node.store.fs.files.at("/channels2") == published);
  MyMesh reboot;
  reboot.store.fs.files = node.store.fs.files;
  reboot.store.loadChannels(&reboot);
  assert(reboot.channels[0].channel.secret[0] == 0xAA);
}

#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
static void known_rollback_is_retryable_but_failed_rollback_is_quarantined() {
  for (bool rollback_fails : {false, true}) {
    MyMesh node;
    seed(node);
    const auto old_image = node.store.fs.files.at("/channels2");
    node.store.fs.fail_rename_from = {"/channels2.tmp"};
    if (rollback_fails) node.store.fs.fail_rename_from.push_back("/channels2.bak");
    char reply[160];
    assert(node.handleTxRoutingCommand("set tx.channel 0 both", reply, sizeof(reply)));
    assert(strstr(reply, "not saved"));
    assert(last_channel_outcome == (rollback_fails ? 3 : 1));
    assert(node.store.hasIncompleteChannelLoad() == rollback_fails);
    assert(!node.store.hasIncompleteContactLoad());
    node.store.fs.fail_rename_from.clear();
    if (rollback_fails) {
      assert(!node.store.fs.exists("/channels2"));
      assert(node.store.fs.files.at("/channels2.bak") == old_image);
      assert(node.handleTxRoutingCommand("set tx.channel 0 radio", reply, sizeof(reply)));
      assert(strstr(reply, "storage unavailable"));
      MyMesh reboot;
      reboot.store.fs.files = node.store.fs.files;
      reboot.store.loadChannels(&reboot);
      assert(reboot.channels[0].channel.tx_radio == mesh::RADIO_TX_PRIMARY);
    } else {
      assert(node.store.fs.files.at("/channels2") == old_image);
      assert(node.handleTxRoutingCommand("set tx.channel 0 radio", reply, sizeof(reply)));
      assert(!strncmp(reply, "OK", 2));
      assert(node.handleTxRoutingCommand("set tx.channel 0 both", reply, sizeof(reply)));
      assert(!strncmp(reply, "OK", 2));
    }
  }
}

static void constructor_recovery_uncertainty_is_quarantined_without_a_commit() {
  MyMesh node;
  seed(node);
  const auto old_image = node.store.fs.files.at("/channels2");
  node.store.fs.files["/channels2.bak"] = old_image;
  node.store.fs.files.erase("/channels2");
  node.store.fs.arm_namespace_failure = true;
  last_channel_outcome = -1;
  assert(!node.store.saveChannels(&node));
  assert(last_channel_outcome == 3 && node.store.hasIncompleteChannelLoad());
  assert(!node.store.hasIncompleteContactLoad());
  assert(node.store.fs.files.at("/channels2") == old_image);
  assert(!node.store.fs.exists("/channels2.bak"));
  assert(!node.store.fs.exists("/channels2.tmp"));
}
#endif

int main() {
  prepublication_failures_remain_retryable();
  publication_uncertainty_cannot_acknowledge_a_rolled_back_value();
  binary_psk_rollback_preserves_live_communications_but_blocks_writes();
#if defined(ESP32_PLATFORM) || defined(RP2040_PLATFORM)
  known_rollback_is_retryable_but_failed_rollback_is_quarantined();
  constructor_recovery_uncertainty_is_quarantined_without_a_commit();
#endif
  puts("PASS: channel publication authority");
}
