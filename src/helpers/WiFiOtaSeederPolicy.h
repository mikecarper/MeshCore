#pragma once

namespace WiFiOtaSeederPolicy {

enum class ListenerAction {
  Keep,
  Start,
  Stop,
};

inline ListenerAction listenerAction(bool network_ready, bool listening) {
  if (network_ready) return listening ? ListenerAction::Keep : ListenerAction::Start;
  return listening ? ListenerAction::Stop : ListenerAction::Keep;
}

inline bool canAttachTcpFolder(bool folder_active, bool tcp_folder_attached) {
  return !folder_active || tcp_folder_attached;
}

inline bool tcpFolderWasDetached(bool tcp_folder_attached, bool folder_active) {
  return tcp_folder_attached && !folder_active;
}

}  // namespace WiFiOtaSeederPolicy
