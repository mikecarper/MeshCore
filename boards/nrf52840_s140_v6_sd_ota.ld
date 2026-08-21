/* S140 v6 application layout for the MeshTower SD OTA target. */

SEARCH_DIR(.)
GROUP(-lgcc -lc -lnosys)

MEMORY
{
  FLASH (rx)     : ORIGIN = 0x26000, LENGTH = 0xED000 - 0x26000

  /* 8-byte retained clock state followed by the 72-byte MOTASDA2 authorization record. */
  PERSISTENT_RAM (rwx) : ORIGIN = 0x20006000, LENGTH = 80

  /* SRAM required by SoftDevice depends on configured BLE resources. */
  RAM (rwx) : ORIGIN = 0x20006000 + 80, LENGTH = 0x20040000 - 0x20006000 - 80
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

  ASSERT(SIZEOF(.persistent) == 8,
         "MeshTower SD OTA retained clock prefix must remain exactly 8 bytes")

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
