#pragma once

namespace mesh {

static const char CLIENT_ACL_PRIMARY_PATH[] = "/s_contacts";
static const char CLIENT_ACL_TEMP_PATH[] = "/s_contacts.tmp";
static const char CLIENT_ACL_BACKUP_PATH[] = "/s_contacts.bak";

template <typename Filesystem>
bool removeClientACLArtifact(Filesystem* fs, const char* path) {
  if (!fs->exists(path)) return true;
  fs->remove(path);
  return !fs->exists(path);
}

// Recover the last published image after any power-loss boundary in the
// temp -> backup -> primary transaction. A present primary is authoritative;
// if publication had not completed, the backup is restored instead.
template <typename Filesystem>
bool recoverClientACLFiles(Filesystem* fs) {
  if (fs->exists(CLIENT_ACL_PRIMARY_PATH)) {
    // The primary is already authoritative.  Cleanup is best-effort: a
    // filesystem which cannot remove a stale artifact must not make the
    // committed image appear unavailable.
    removeClientACLArtifact(fs, CLIENT_ACL_TEMP_PATH);
    removeClientACLArtifact(fs, CLIENT_ACL_BACKUP_PATH);
    return true;
  }
  if (fs->exists(CLIENT_ACL_BACKUP_PATH)) {
    if (!fs->rename(CLIENT_ACL_BACKUP_PATH, CLIENT_ACL_PRIMARY_PATH)) {
      return false;
    }
    removeClientACLArtifact(fs, CLIENT_ACL_TEMP_PATH);
    return true;
  }
  return removeClientACLArtifact(fs, CLIENT_ACL_TEMP_PATH);
}

template <typename Filesystem, typename Validator>
bool recoverClientACLFilesVerified(Filesystem* fs, Validator is_valid) {
  if (fs->exists(CLIENT_ACL_PRIMARY_PATH)
      && is_valid(fs, CLIENT_ACL_PRIMARY_PATH)) {
    // A validated primary remains authoritative even when stale-artifact
    // cleanup is temporarily unavailable.
    removeClientACLArtifact(fs, CLIENT_ACL_TEMP_PATH);
    removeClientACLArtifact(fs, CLIENT_ACL_BACKUP_PATH);
    return true;
  }
  if (fs->exists(CLIENT_ACL_BACKUP_PATH)
      && is_valid(fs, CLIENT_ACL_BACKUP_PATH)) {
    if (!removeClientACLArtifact(fs, CLIENT_ACL_PRIMARY_PATH)
        || !fs->rename(CLIENT_ACL_BACKUP_PATH, CLIENT_ACL_PRIMARY_PATH)) {
      return false;
    }
    removeClientACLArtifact(fs, CLIENT_ACL_TEMP_PATH);
    return true;
  }
  return !fs->exists(CLIENT_ACL_PRIMARY_PATH)
      && !fs->exists(CLIENT_ACL_BACKUP_PATH)
      && removeClientACLArtifact(fs, CLIENT_ACL_TEMP_PATH);
}

template <typename Filesystem>
bool publishVerifiedClientACLTemp(Filesystem* fs, bool temp_verified) {
  if (!temp_verified) {
    removeClientACLArtifact(fs, CLIENT_ACL_TEMP_PATH);
    return false;
  }
  if (fs->exists(CLIENT_ACL_BACKUP_PATH)) return false;

  const bool had_primary = fs->exists(CLIENT_ACL_PRIMARY_PATH);
  if (had_primary
      && !fs->rename(CLIENT_ACL_PRIMARY_PATH, CLIENT_ACL_BACKUP_PATH)) {
    return false;
  }
  if (!fs->rename(CLIENT_ACL_TEMP_PATH, CLIENT_ACL_PRIMARY_PATH)) {
    // Restore the last committed image immediately when possible. If this
    // rename also fails, leave both artifacts for recoverClientACLFiles().
    if (had_primary && !fs->exists(CLIENT_ACL_PRIMARY_PATH)) {
      fs->rename(CLIENT_ACL_BACKUP_PATH, CLIENT_ACL_PRIMARY_PATH);
    }
    return false;
  }

  // A stale backup does not make the newly published primary unsuccessful;
  // recovery removes it before the next load/save.
  removeClientACLArtifact(fs, CLIENT_ACL_BACKUP_PATH);
  return true;
}

template <typename Filesystem, typename Validator>
bool publishVerifiedClientACLTemp(Filesystem* fs, bool temp_verified,
                                  Validator is_valid) {
  if (!temp_verified) {
    removeClientACLArtifact(fs, CLIENT_ACL_TEMP_PATH);
    return false;
  }
  if (fs->exists(CLIENT_ACL_BACKUP_PATH)) return false;
  const bool had_primary = fs->exists(CLIENT_ACL_PRIMARY_PATH);
  if (had_primary
      && !fs->rename(CLIENT_ACL_PRIMARY_PATH, CLIENT_ACL_BACKUP_PATH)) {
    return false;
  }
  if (!fs->rename(CLIENT_ACL_TEMP_PATH, CLIENT_ACL_PRIMARY_PATH)
      || !is_valid(fs, CLIENT_ACL_PRIMARY_PATH)) {
    removeClientACLArtifact(fs, CLIENT_ACL_PRIMARY_PATH);
    if (had_primary) {
      fs->rename(CLIENT_ACL_BACKUP_PATH, CLIENT_ACL_PRIMARY_PATH);
    }
    return false;
  }
  // Publication committed at the successful temp -> primary rename and
  // validation.  A stale backup is recoverable housekeeping, not a failed
  // save (which could otherwise make RAM roll back while disk holds new data).
  removeClientACLArtifact(fs, CLIENT_ACL_BACKUP_PATH);
  return true;
}

} // namespace mesh
