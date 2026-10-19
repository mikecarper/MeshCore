/* S140 v6 application layout for the MeshTower SD OTA target. */

SEARCH_DIR(.)
GROUP(-lgcc -lc -lnosys)

MEMORY
{
  FLASH (rx)     : ORIGIN = 0x26000, LENGTH = 0xED000 - 0x26000

  /* 8-byte retained clock state followed by the 72-byte MOTASDA2 authorization record. */
  PERSISTENT_RAM (rwx) : ORIGIN = 0x20006000, LENGTH = 80

  /* Separate reset-retained state; clock and OTA handoff ABIs stay fixed. */
  REGULATOR_RECOVERY_RAM (rwx) : ORIGIN = 0x20006050, LENGTH = 64

  /* SRAM required by SoftDevice depends on configured BLE resources. */
  RAM (rwx) : ORIGIN = 0x20006090, LENGTH = 0x20040000 - 0x20006090
}

SECTIONS
{
  . = ALIGN(4);
  .persistent (NOLOAD) :
  {
    KEEP(*(.persistent_magic))
    KEEP(*(.persistent_data))
    . = ALIGN(4);
  } > PERSISTENT_RAM

  .regulator_recovery (NOLOAD) :
  {
    __regulator_recovery_start__ = .;
    KEEP(*(.regulator_recovery))
    . = ORIGIN(REGULATOR_RECOVERY_RAM) + LENGTH(REGULATOR_RECOVERY_RAM);
    __regulator_recovery_end__ = .;
  } > REGULATOR_RECOVERY_RAM

  ASSERT(__regulator_recovery_start__ == 0x20006050,
         "regulator recovery start must remain fixed")
  ASSERT(__regulator_recovery_end__ == 0x20006090,
         "regulator recovery reservation must remain exactly 64 bytes")
  ASSERT(ORIGIN(RAM) == __regulator_recovery_end__,
         "application RAM must start after regulator recovery")

  __mota_sd_auth_start__ = ADDR(.persistent) + SIZEOF(.persistent);
  __mota_sd_auth_end__ = ORIGIN(PERSISTENT_RAM) + LENGTH(PERSISTENT_RAM);

  ASSERT(ADDR(.persistent) == 0x20006000,
         "SD OTA retained clock prefix start must remain fixed")
  ASSERT(SIZEOF(.persistent) == 8,
         "MeshTower SD OTA retained clock prefix must remain exactly 8 bytes")
  ASSERT(__mota_sd_auth_start__ == 0x20006008,
         "SD OTA authorization start must remain fixed")
  ASSERT(__mota_sd_auth_end__ == 0x20006050,
         "SD OTA authorization end must remain fixed")
  ASSERT(ORIGIN(RAM) >= __mota_sd_auth_end__,
         "application RAM must not overlap SD OTA authorization")

  . = ALIGN(4);
  .svc_data :
  {
    PROVIDE(__start_svc_data = .);
    KEEP(*(.svc_data))
    PROVIDE(__stop_svc_data = .);
  } > RAM

  .fs_data :
  {
    PROVIDE(__start_fs_data = .);
    KEEP(*(.fs_data))
    PROVIDE(__stop_fs_data = .);
  } > RAM
}

INCLUDE "nrf52_common.ld"
