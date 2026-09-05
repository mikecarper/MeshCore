/* S140 v6 application layout with a reset-retained 64 KiB mOTA arena. */

SEARCH_DIR(.)
GROUP(-lgcc -lc -lnosys)

MEMORY
{
  FLASH (rx)     : ORIGIN = 0x26000, LENGTH = 0xED000 - 0x26000

  /* 8-byte retained clock state followed by the 72-byte MOTAHYB1 record. */
  PERSISTENT_RAM (rwx) : ORIGIN = 0x20006000, LENGTH = 80

  /* Normal application data, heap, and stack end below the mOTA arena. */
  RAM (rwx) : ORIGIN = 0x20006000 + 80, LENGTH = 0x20030000 - 0x20006000 - 80

  /* Fixed staging arena retained across the software-reset bootloader handoff. */
  MOTA_RAM (rwx) : ORIGIN = 0x20030000, LENGTH = 0x10000
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

  /* Export the direct-access handoff ABI even though no input section owns it. */
  __mota_hybrid_handoff_start__ = ADDR(.persistent) + SIZEOF(.persistent);
  __mota_hybrid_handoff_end__ = ORIGIN(PERSISTENT_RAM) + LENGTH(PERSISTENT_RAM);

  ASSERT(ADDR(.persistent) == 0x20006000,
         "mOTA retained clock prefix start must remain fixed")
  ASSERT(SIZEOF(.persistent) == 8,
         "mOTA hybrid retained clock prefix must remain exactly 8 bytes")
  ASSERT(__mota_hybrid_handoff_start__ == 0x20006008,
         "mOTA hybrid handoff start must remain fixed")
  ASSERT(__mota_hybrid_handoff_end__ == 0x20006050,
         "mOTA hybrid handoff end must remain fixed")
  ASSERT(__mota_hybrid_handoff_end__ - __mota_hybrid_handoff_start__ == 72,
         "mOTA hybrid handoff must remain exactly 72 bytes")
  ASSERT(ORIGIN(RAM) == __mota_hybrid_handoff_end__,
         "normal RAM must start immediately after the mOTA handoff")

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

  /* No input section owns this memory. These symbols are the direct-access ABI. */
  .mota_ram (NOLOAD) :
  {
    PROVIDE(__mota_ram_start__ = .);
    . += LENGTH(MOTA_RAM);
    PROVIDE(__mota_ram_end__ = .);
  } > MOTA_RAM

  ASSERT(ORIGIN(MOTA_RAM) == 0x20030000,
         "mOTA RAM arena start must remain fixed")
  ASSERT(LENGTH(MOTA_RAM) == 0x10000,
         "mOTA RAM arena must remain exactly 64 KiB")
  ASSERT(ORIGIN(RAM) + LENGTH(RAM) == ORIGIN(MOTA_RAM),
         "normal RAM must end at the mOTA arena")
  ASSERT(ORIGIN(MOTA_RAM) + LENGTH(MOTA_RAM) == 0x20040000,
         "mOTA RAM arena must end at physical SRAM top")
}

INCLUDE "nrf52_common.ld"
