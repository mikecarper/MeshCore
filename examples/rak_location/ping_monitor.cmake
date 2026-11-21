target_sources(app PRIVATE "${CMAKE_CURRENT_LIST_DIR}/ping_monitor.c")
zephyr_link_libraries(-Wl,--wrap=bt_le_ext_adv_set_data -Wl,--wrap=bt_le_ext_adv_start -Wl,--wrap=bt_le_ext_adv_create)
