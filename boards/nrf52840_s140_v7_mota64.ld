/* S140 v7 application layout with a reset-retained 64 KiB mOTA arena. */

SEARCH_DIR(.)
GROUP(-lgcc -lc -lnosys)

MEMORY
{
  FLASH (rx)     : ORIGIN = 0x27000, LENGTH = 0xED000 - 0x27000

  /* 8-byte retained clock state followed by the 72-byte MOTAHYB1 record. */
  PERSISTENT_RAM (rwx) : ORIGIN = 0x20006000, LENGTH = 80

  /* Separate reset-retained state; clock and OTA handoff ABIs stay fixed. */
  REGULATOR_RECOVERY_RAM (rwx) : ORIGIN = 0x20006050, LENGTH = 64

  /* Normal application data, heap, and stack end below the mOTA arena. */
  RAM (rwx) : ORIGIN = 0x20006090, LENGTH = 0x20030000 - 0x20006090

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

  /* Export the direct-access handoff ABI even though no input section owns it. */
  __mota_hybrid_handoff_start__ = ADDR(.persistent) + SIZEOF(.persistent);
  __mota_hybrid_handoff_end__ = ORIGIN(PERSISTENT_RAM) + LENGTH(PERSISTENT_RAM);
  __mota_sd_auth_start__ = __mota_hybrid_handoff_start__;
  __mota_sd_auth_end__ = __mota_hybrid_handoff_end__;

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
  ASSERT(ORIGIN(REGULATOR_RECOVERY_RAM) == __mota_hybrid_handoff_end__,
         "regulator recovery must follow the mOTA handoff")
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
