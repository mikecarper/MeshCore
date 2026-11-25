// Included at the end of the pinned littlefs v1 implementation. Use its
// normal erase/copy/CRC/sync sequence and its fixed superblock pair. Never
// overwrite a flash sector apart from the filesystem's own commit protocol.
int mesh_lfs_set_usb_lock(lfs_t *lfs, bool locked) {
    static const uint8_t entry[] = {0, 0, 0, 8, 'M', 'C', 'U', 'L', 'O', 'C', 'K', '1'};
    const lfs_off_t offset = sizeof(struct lfs_disk_dir) + sizeof(struct lfs_disk_superblock);
    if (offset != 48 || lfs->cfg->block_size != 128 || lfs->cfg->block_count != 224)
        return LFS_ERR_INVAL;
    const lfs_block_t pair[] = {0, 1};
    lfs_dir_t dir;
    int rc = lfs_dir_fetch(lfs, &dir, pair);
    if (rc) return rc;
    bool copies[2];
    for (unsigned i = 0; i < 2; ++i) {
        uint8_t bytes[sizeof(entry)];
        rc = lfs_bd_read(lfs, i, offset, bytes, sizeof(bytes));
        if (rc) return rc;
        copies[i] = !memcmp(bytes, entry, sizeof(bytes));
    }
    const bool current = copies[dir.pair[0]];
    // Refuse unfamiliar extensions, including an unexpected split superblock.
    if (dir.d.size != offset + 4 + (current ? sizeof(entry) : 0))
        return LFS_ERR_CORRUPT;
    if (copies[0] == locked && copies[1] == locked) return 0;
    if (current != locked) {
        const struct lfs_region change = {offset, current ? sizeof(entry) : 0,
                                         entry, locked ? sizeof(entry) : 0};
        rc = lfs_dir_commit(lfs, &dir, &change, 1);
        if (rc) return rc;
    }
    // Mirror into the other copy, including after a previously interrupted
    // change. An ordinary filesystem commit preserves the optional entry.
    return lfs_dir_commit(lfs, &dir, NULL, 0);
}
